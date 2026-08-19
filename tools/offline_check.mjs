#!/usr/bin/env node
/**
 * Offline-server UI check: proves the app degrades gracefully when the
 * server dies mid-session (the exact "Request failed: Failed to fetch"
 * alert users hit when a start.bat window is closed or the machine rebooted).
 *
 * Flow: boot the real server on a test port -> load the page in headless
 * Chrome (banner must be hidden) -> kill the server -> click Naturalize ->
 * the page must show the in-page "Can't reach the server" banner with the
 * restart hint, and must not throw uncaught JS exceptions.
 *
 * Usage:  node tools/offline_check.mjs
 */
import { spawn } from "node:child_process";
import os from "node:os";
import path from "node:path";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const SERVER_PORT = 8141;
const CDP_PORT = 9334;
const APP_URL = `http://127.0.0.1:${SERVER_PORT}/`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- CDP client
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

async function evalJs(cdp, expression) {
  const r = await cdp.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (r.exceptionDetails) {
    throw new Error("eval failed: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  }
  return r.result.value;
}

async function waitFor(cdp, expr, what, timeoutMs = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await evalJs(cdp, expr)) return;
    await sleep(150);
  }
  throw new Error(`timed out waiting for: ${what}`);
}

async function waitForTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
      const list = await res.json();
      const page = list.find((t) => t.type === "page");
      if (page) return page;
    } catch {}
    await sleep(250);
  }
  throw new Error("Chrome debugging target not available");
}

async function waitForServer(url, timeoutMs = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {}
    await sleep(300);
  }
  throw new Error("test server did not come up");
}

const report = { exceptions: [], steps: [] };

async function main() {
  // 1. boot the real server on a test port
  const server = spawn("python", ["server.py"], {
    env: { ...process.env, PORT: String(SERVER_PORT) },
    stdio: "ignore",
  });
  let serverUp = false;
  try {
    await waitForServer(`${APP_URL}api/status`);
    serverUp = true;
    report.steps.push({ step: "server up", ok: true });

    // 2. load the page in headless Chrome (server alive -> no banner)
    const profile = os.tmpdir() + "/nat-offline-" + Date.now();
    const chrome = spawn(CHROME, [
      "--headless=new",
      `--remote-debugging-port=${CDP_PORT}`,
      `--user-data-dir=${profile}`,
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      "--window-size=1280,900",
      APP_URL,
    ], { stdio: "ignore" });

    let cdp;
    try {
      const target = await waitForTarget();
      cdp = await Cdp.connect(target.webSocketDebuggerUrl);
      await cdp.send("Page.enable");
      await cdp.send("Runtime.enable");
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
        }
      };

      // page must be fully initialized (style selector populated, sample set)
      await waitFor(
        cdp,
        `document.getElementById("style").options.length > 0 && document.getElementById("input").value.length > 0`,
        "page init"
      );
      report.steps.push({ step: "page loaded (server up)", ok: true });

      const bannerBefore = await evalJs(
        cdp,
        `(() => { const b = document.getElementById("err-banner"); return b ? b.hidden : null; })()`
      );
      if (bannerBefore !== true) {
        throw new Error(`expected banner hidden before kill, got: ${bannerBefore}`);
      }
      report.steps.push({ step: "no banner while server up", ok: true });

      // 3. kill the server mid-session
      server.kill();
      await sleep(1200);

      // 4. click Naturalize -> fetch must fail -> banner must explain
      await evalJs(cdp, `document.getElementById("run").click()`);
      await waitFor(
        cdp,
        `(() => { const b = document.getElementById("err-banner"); return !b.hidden && b.textContent.length > 0; })()`,
        "error banner after server death"
      );
      const bannerText = await evalJs(cdp, `document.getElementById("err-banner").textContent`);
      if (!/can't reach the server/i.test(bannerText) || !/start\.bat/i.test(bannerText)) {
        throw new Error(`banner text not actionable: ${JSON.stringify(bannerText)}`);
      }
      report.steps.push({ step: "banner explains server is down + restart hint", ok: true, text: bannerText });

      const buttonReset = await evalJs(
        cdp,
        `(() => { const b = document.getElementById("run"); return b.disabled === false && b.textContent.includes("Naturalize"); })()`
      );
      if (!buttonReset) throw new Error("Naturalize button not reset after failed request");
      report.steps.push({ step: "button reset after failure", ok: true });

      if (report.exceptions.length) {
        throw new Error("uncaught JS exceptions: " + report.exceptions.join(" | "));
      }
      report.steps.push({ step: "no uncaught JS exceptions", ok: true });
    } finally {
      if (cdp) cdp.close();
      chrome.kill();
    }
  } finally {
    if (serverUp) server.kill();
  }

  console.log("offline check PASSED");
  for (const s of report.steps) console.log(`  ✓ ${s.step}`);
  process.exit(0);
}

main().catch((err) => {
  console.error("offline check FAILED: " + err.message);
  for (const s of report.steps) console.log(`  ${s.ok ? "✓" : "✗"} ${s.step}`);
  process.exit(1);
});
