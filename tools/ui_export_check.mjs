#!/usr/bin/env node
/**
 * Export-flow walkthrough: drives a real Chrome session against the running
 * Naturalizer app and verifies the full upload → humanize → export loop.
 *
 * Usage:  node tools/ui_export_check.mjs <app-url> [out-dir]
 *
 * Steps:
 *   1. Load the app, switch to Humanizer mode, humanize the sample.
 *   2. Upload a real .docx (and .pdf) via the file input.
 *   3. Verify the result + export bar render with a real score.
 *   4. Click every export button (.txt, .docx, .pdf) and confirm each
 *      actually downloads a non-empty file (CDP download events).
 *   5. Report console errors / exceptions and save screenshots.
 */
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const appUrl = process.argv[2];
const outDir = process.argv[3] || "ui-export-check";
const PORT = 9334;
const DOWNLOAD_DIR = path.resolve(outDir, "downloads");

if (!appUrl) {
  console.error("usage: node tools/ui_export_check.mjs <app-url> [out-dir]");
  process.exit(2);
}
mkdirSync(DOWNLOAD_DIR, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.events = [];
    this.downloads = [];
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
        if (msg.method === "Page.downloadWillBegin") {
          cdp.downloads.push({ url: msg.params.url, suggested: msg.params.suggestedFilename });
        }
        if (msg.method === "Page.downloadProgress" && msg.params.state === "completed") {
          cdp.lastDownloadComplete = msg.params;
        }
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
    try { this.ws.close(); } catch {}
  }
}

async function waitForTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await res.json();
      const page = list.find((t) => t.type === "page");
      if (page) return page;
    } catch {}
    await sleep(250);
  }
  throw new Error("Chrome debugging target not available");
}

async function evalJs(cdp, expression) {
  const r = await cdp.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) {
    throw new Error("eval failed: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  }
  return r.result?.value;
}

async function waitFor(cdp, expr, label, timeoutMs = 15000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      if (await evalJs(cdp, expr)) return true;
    } catch {}
    await sleep(200);
  }
  throw new Error(`timeout waiting for: ${label}`);
}

async function shot(cdp, name) {
  try {
    const { data } = await cdp.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(path.join(outDir, name), Buffer.from(data, "base64"));
  } catch {}
}

function latestDownload() {
  const files = readdirSync(DOWNLOAD_DIR).filter((f) => !f.endsWith(".crdownload"));
  if (!files.length) return null;
  files.sort((a, b) => statSync(path.join(DOWNLOAD_DIR, b)).mtimeMs - statSync(path.join(DOWNLOAD_DIR, a)).mtimeMs);
  return files[0];
}

function waitForDownload(suggested, timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const tick = () => {
      const f = latestDownload();
      if (f && (!suggested || f === suggested)) return resolve(path.join(DOWNLOAD_DIR, f));
      if (Date.now() - t0 > timeoutMs) return reject(new Error(`download timeout for ${suggested}`));
      setTimeout(tick, 200);
    };
    tick();
  });
}

async function main() {
  const report = { steps: [], consoleErrors: [], exceptions: [], downloads: [] };
  let chrome = null, cdp = null;
  try {
    chrome = spawn(CHROME, [
      "--headless=new",
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      `--remote-debugging-port=${PORT}`,
      "--user-data-dir=" + path.resolve(outDir, "profile"),
      `--download-default-directory=${DOWNLOAD_DIR}`,
      "about:blank",
    ], { stdio: "ignore" });

    const target = await waitForTarget();
    cdp = await Cdp.connect(target.webSocketDebuggerUrl);
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Browser.setDownloadBehavior", {
      behavior: "allow",
      downloadPath: DOWNLOAD_DIR,
      eventsEnabled: true,
    });

    const consoleErrors = [];
    cdp.events = [];
    cdp.send("Runtime.enable").catch(() => {});
    // wire console capture via a fresh evaluate wrapper
    const origSend = cdp.send.bind(cdp);
    // Track console errors through Runtime.consoleAPICalled / exceptionThrown.
    // (We re-read cdp.events after each action below.)

    await cdp.send("Page.navigate", { url: appUrl });
    await waitFor(cdp, "document.readyState === 'complete'", "page load", 20000);
    await waitFor(cdp, "document.querySelectorAll('#style option').length >= 4", "style dropdown", 10000);
    report.steps.push({ step: "load", ok: true, url: appUrl });

    // --- Humanizer mode + run on the prefilled sample -----------------------
    await evalJs(cdp, "document.getElementById('tab-humanizer').click()");
    await waitFor(cdp, "!document.getElementById('humanizer-controls').hidden", "humanizer mode active");
    await evalJs(cdp, "document.getElementById('run-humanize').click()");
    await waitFor(
      cdp,
      "!document.getElementById('analysis').hidden && /^\\d+$/.test(document.getElementById('score-num').textContent.trim())",
      "sample humanized (score rendered)",
      30000
    );
    const sampleScore = await evalJs(cdp, "document.getElementById('score-num').textContent.trim()");
    await sleep(300);
    await shot(cdp, "01-humanized-sample.png");
    report.steps.push({ step: "humanize-sample", ok: true, score: sampleScore });

    // --- Upload a real .docx -------------------------------------------------
    await cdp.send("DOM.enable");
    const { root } = await cdp.send("DOM.getDocument", { depth: 1 });
    const { nodeId } = await cdp.send("DOM.querySelector", { nodeId: root.nodeId, selector: "#file" });
    await cdp.send("DOM.setFileInputFiles", {
      nodeId,
      files: [path.resolve(".smoke/input.docx")],
    });
    await evalJs(cdp, "document.getElementById('file').dispatchEvent(new Event('change', { bubbles: true }))");
    await waitFor(
      cdp,
      "!document.getElementById('export-bar').hidden && /^\\d+$/.test(document.getElementById('score-num').textContent.trim())",
      "upload processed + export bar rendered",
      30000
    );
    const uploadScore = await evalJs(cdp, "document.getElementById('score-num').textContent.trim()");
    const exportVisible = await evalJs(cdp, "!document.getElementById('export-bar').hidden");
    await sleep(300);
    await shot(cdp, "02-uploaded-docx.png");
    report.steps.push({ step: "upload-docx", ok: true, score: uploadScore, exportBar: exportVisible });

    // --- Upload a real .pdf ---------------------------------------------------
    await cdp.send("DOM.setFileInputFiles", {
      nodeId,
      files: [path.resolve(".smoke/input.pdf")],
    });
    await evalJs(cdp, "document.getElementById('file').dispatchEvent(new Event('change', { bubbles: true }))");
    await waitFor(
      cdp,
      "!document.getElementById('export-bar').hidden && /^\\d+$/.test(document.getElementById('score-num').textContent.trim())",
      "pdf upload processed",
      30000
    );
    const pdfScore = await evalJs(cdp, "document.getElementById('score-num').textContent.trim()");
    await sleep(300);
    await shot(cdp, "03-uploaded-pdf.png");
    report.steps.push({ step: "upload-pdf", ok: true, score: pdfScore });

    // --- Export every format ---------------------------------------------------
    const exports = [];
    for (const fmt of ["txt", "docx", "pdf"]) {
      const before = latestDownload();
      await evalJs(cdp, `document.querySelector('.export-bar button[data-fmt="${fmt}"]').click()`);
      const file = await waitForDownload(null, 20000);
      const size = statSync(file).size;
      const ok = size > 0 && file !== before;
      exports.push({ fmt, file: path.basename(file), size, ok });
      report.steps.push({ step: `export-${fmt}`, ok, file: path.basename(file), size });
      console.log(`export .${fmt}: ${ok ? "OK" : "FAIL"} -> ${path.basename(file)} (${size} B)`);
      await sleep(400);
    }

    // --- Settled final state (humanize completed + animations done) -----------
    await sleep(1500);
    const finalState = await evalJs(cdp, `(() => ({
      score: document.getElementById('score-num').textContent.trim(),
      scoreLabel: (document.getElementById('score-label') || {}).textContent?.trim() || '',
      beforeSnippet: (document.getElementById('before')?.textContent || '').slice(0, 120),
      afterSnippet: (document.getElementById('after')?.textContent || '').slice(0, 160),
      issuesCount: document.querySelectorAll('#issues li').length,
      stepsText: (document.getElementById('steps')?.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 70),
      perfectBtn: document.getElementById('perfect-btn') ? 'present' : 'missing',
    }))()`);
    await shot(cdp, "04-final.png");
    report.steps.push({ step: "final-state", ok: true, ...finalState });
    console.log("final state:", JSON.stringify(finalState));

    // --- Collect console errors / exceptions -----------------------------------
    const r = await cdp.send("Runtime.evaluate", {
      expression: "1+1",
      returnByValue: true,
    });
    void r;
    report.consoleErrors = cdp.events
      .filter((e) => e.method === "Runtime.consoleAPICalled" && e.params.type === "error")
      .map((e) => JSON.stringify(e.params.args || []).slice(0, 300));
    report.exceptions = cdp.events
      .filter((e) => e.method === "Runtime.exceptionThrown")
      .map((e) => (e.params.exceptionDetails?.exception?.description || e.params.exceptionDetails?.text || "").slice(0, 300));
    report.downloads = exports;

    console.log("\nConsole errors:", report.consoleErrors.length);
    report.consoleErrors.forEach((e) => console.log("  -", e));
    console.log("Exceptions:", report.exceptions.length);
    report.exceptions.forEach((e) => console.log("  -", e));
    console.log("Screenshots:", readdirSync(outDir).filter((f) => f.endsWith(".png")).join(", "));
  } catch (err) {
    report.fatal = String(err && err.stack || err);
    console.error("FATAL:", err && err.message);
  } finally {
    if (cdp) cdp.close();
    if (chrome) chrome.kill();
  }
  writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(`Report: ${path.join(outDir, "report.json")}`);
}
main();
