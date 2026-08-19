#!/usr/bin/env node
/**
 * Visual check for the style/structure detector signals in the live UI.
 *
 * Usage:  node tools/signal_check.mjs <app-url> [out-dir]
 *
 * Drives a headless Chrome session (no dependencies, Node WebSocket):
 *   1. pastes a bulleted AI answer  -> expects a `structure` tag in the
 *      issue list (plus the usual filler/hedge tells), screenshot saved
 *   2. pastes a very short sample   -> expects the `short` low-confidence
 *      note, screenshot saved
 * Collects console errors / exceptions / layout diagnostics and prints a
 * short report. Deterministic rewrite only (use_llm unchecked), so it never
 * touches the network.
 */
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const appUrl = process.argv[2];
const outDir = process.argv[3] || "ui-check";
const PORT = 9334;

if (!appUrl) {
  console.error("usage: node tools/signal_check.mjs <app-url> [out-dir]");
  process.exit(2);
}
mkdirSync(outDir, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.events = [];
  }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => {
      ws.onopen = res;
      ws.onerror = () => rej(new Error("websocket error"));
    });
    const cdp = new Cdp(ws);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id != null) {
        const p = cdp.pending.get(msg.id);
        if (p) {
          cdp.pending.delete(msg.id);
          msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result);
        }
      } else {
        cdp.events.push(msg);
      }
    };
    return cdp;
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  close() {
    try {
      this.ws.close();
    } catch {}
  }
}

async function waitForTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) return page;
    } catch {}
    await sleep(250);
  }
  throw new Error("Chrome debugging target not available");
}

async function evalJs(cdp, expression) {
  const r = await cdp.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (r.exceptionDetails) {
    throw new Error(
      "eval failed: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text)
    );
  }
  return r.result.value;
}

async function shot(cdp, name) {
  const r = await cdp.send("Page.captureScreenshot", { format: "png" });
  writeFileSync(path.join(outDir, name), Buffer.from(r.data, "base64"));
  console.log(`  screenshot: ${name}`);
}

async function waitFor(cdp, expr, what, timeoutMs = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await evalJs(cdp, expr)) return;
    await sleep(150);
  }
  throw new Error(`timed out waiting for: ${what}`);
}

const BULLETED_AI = [
  "Here are the key reasons this approach wins:",
  "- First, it is important to note that the platform leverages cutting-edge technology.",
  "- Second, the robust design streamlines the entire workflow.",
  "- Third, it empowers teams to utilize data more effectively.",
  "- Finally, the seamless integration fosters a holistic experience.",
].join("\n");

const SHORT_SAMPLE = "We won the contract and celebrated late into the night.";

const report = { url: appUrl, consoleErrors: [], exceptions: [], steps: [] };

async function main() {
  const profile = os.tmpdir() + "/nat-signal-" + Date.now();
  const chrome = spawn(
    CHROME,
    [
      "--headless=new",
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profile}`,
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      "--window-size=1280,900",
      appUrl,
    ],
    { stdio: "ignore" }
  );

  let cdp;
  try {
    const target = await waitForTarget();
    cdp = await Cdp.connect(target.webSocketDebuggerUrl);
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Log.enable");
    cdp.events = [];
    cdp.ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id != null) {
        const p = cdp.pending.get(msg.id);
        if (p) {
          cdp.pending.delete(msg.id);
          msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result);
        }
      } else {
        cdp.events.push(msg);
        if (msg.method === "Runtime.exceptionThrown") {
          const d = msg.params.exceptionDetails;
          report.exceptions.push(d.exception?.description || d.text || "exception");
        }
        if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
          report.consoleErrors.push(msg.params.args?.map((a) => a.value || a.description).join(" "));
        }
      }
    };

    await cdp.send("Page.navigate", { url: appUrl });
    await waitFor(cdp, "document.getElementById('input') !== null", "input ready");
    await sleep(400);

    // --- Step 1: bulleted AI answer ----------------------------------------
    // The app boots in Detector mode; switch to the Humanizer tab first so
    // the naturalize flow (issues list, score) is the active one.
    await evalJs(cdp, "document.getElementById('tab-humanizer').click()");
    await sleep(150);
    await evalJs(
      cdp,
      `document.getElementById('input').value = ${JSON.stringify(BULLETED_AI)};`
    );
    await evalJs(cdp, "document.getElementById('use-llm').checked = false;");
    await evalJs(cdp, "document.getElementById('run-humanize').click()");
    await waitFor(
      cdp,
      "!document.getElementById('analysis').hidden && /^\\d+$/.test(document.getElementById('score-num').textContent.trim())",
      "bulleted result rendered"
    );
    await sleep(300);
    const kinds1 = await evalJs(
      cdp,
      "Array.from(document.querySelectorAll('#issues li .tag')).map((el) => el.textContent)"
    );
    const score1 = await evalJs(cdp, "document.getElementById('score-num').textContent.trim()");
    const issues1 = await evalJs(
      cdp,
      "Array.from(document.querySelectorAll('#issues li')).map((li) => li.textContent.slice(0, 90))"
    );
    report.steps.push({
      step: "bulleted-ai",
      score: score1,
      kinds: kinds1,
      structureShown: kinds1.includes("structure"),
      issueText: issues1,
    });
    console.log(
      `== bulleted AI == score ${score1} | structure tag: ${kinds1.includes("structure")}`
    );
    await shot(cdp, "signal-bulleted.png");

    // --- Step 2: very short sample -----------------------------------------
    await evalJs(
      cdp,
      `document.getElementById('input').value = ${JSON.stringify(SHORT_SAMPLE)};`
    );
    await evalJs(cdp, "document.getElementById('run-humanize').click()");
    await waitFor(
      cdp,
      "!document.getElementById('analysis').hidden && /^\\d+$/.test(document.getElementById('score-num').textContent.trim())",
      "short result rendered"
    );
    await sleep(300);
    const kinds2 = await evalJs(
      cdp,
      "Array.from(document.querySelectorAll('#issues li .tag')).map((el) => el.textContent)"
    );
    const score2 = await evalJs(cdp, "document.getElementById('score-num').textContent.trim()");
    const issues2 = await evalJs(
      cdp,
      "Array.from(document.querySelectorAll('#issues li')).map((li) => li.textContent.slice(0, 90))"
    );
    report.steps.push({
      step: "short-sample",
      score: score2,
      kinds: kinds2,
      shortShown: kinds2.includes("short"),
      issueText: issues2,
    });
    console.log(
      `== short sample == score ${score2} | short note: ${kinds2.includes("short")}`
    );
    await shot(cdp, "signal-short.png");

    report.steps.push({
      step: "layout",
      diag: await evalJs(cdp, `(() => {
        const vw = document.documentElement.clientWidth;
        const issues = [];
        if (document.documentElement.scrollWidth > vw + 1) {
          issues.push('h-overflow ' + document.documentElement.scrollWidth + '>' + vw);
        }
        return issues;
      })()`),
    });
  } finally {
    if (cdp) cdp.close();
    chrome.kill();
  }

  writeFileSync(
    path.join(outDir, "signal-report.json"),
    JSON.stringify(report, null, 2),
    "utf-8"
  );
  const pass =
    report.consoleErrors.length === 0 &&
    report.exceptions.length === 0 &&
    report.steps[0]?.structureShown &&
    report.steps[1]?.shortShown;
  console.log("\nConsole errors:", report.consoleErrors.length);
  console.log("Exceptions:", report.exceptions.length);
  console.log(pass ? "SIGNAL CHECK PASS" : "SIGNAL CHECK FAIL");
  console.log("Report: " + path.join(outDir, "signal-report.json"));
  process.exit(pass ? 0 : 1);
}

main().catch((err) => {
  console.error("FATAL:", err.message);
  writeFileSync(
    path.join(outDir, "signal-report.json"),
    JSON.stringify(report, null, 2),
    "utf-8"
  );
  process.exit(1);
});
