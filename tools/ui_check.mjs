#!/usr/bin/env node
/**
 * UI check driver: drives a headless Chrome session against the running
 * Naturalizer app via the Chrome DevTools Protocol (no dependencies — uses
 * Node's built-in WebSocket).
 *
 * Usage:  node tools/ui_check.mjs <app-url> [out-dir]
 *
 * It loads the page, clicks the real Naturalize / batch buttons, collects
 * console errors and exceptions, measures layout geometry (overflow,
 * zero-height elements, score-gauge placement), and saves screenshots to
 * <out-dir> (default ./ui-check). Prints a JSON report at the end.
 */
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const appUrl = process.argv[2];
const outDir = process.argv[3] || "ui-check";
const PORT = 9333;

if (!appUrl) {
  console.error("usage: node tools/ui_check.mjs <app-url> [out-dir]");
  process.exit(2);
}
mkdirSync(outDir, { recursive: true });

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
          msg.error
            ? p.reject(new Error(JSON.stringify(msg.error)))
            : p.resolve(msg.result);
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

// -------------------------------------------------------------------- helpers
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
  const r = await cdp.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (r.exceptionDetails) {
    throw new Error(
      "eval failed: " +
        (r.exceptionDetails.exception?.description || r.exceptionDetails.text)
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

// ---------------------------------------------------------------- diagnostics
const DIAG = `(() => {
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  const issues = [];
  if (document.documentElement.scrollWidth > vw + 1) {
    issues.push({ type: 'h-overflow', scrollWidth: document.documentElement.scrollWidth, vw });
  }
  const hiddenAncestor = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      if (p.hidden) return true;
    }
    return false;
  };
  for (const el of document.querySelectorAll('body *')) {
    if (hiddenAncestor(el)) continue;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (r.width > 0 && r.width > vw + 1 && cs.position !== 'fixed') {
      issues.push({ type: 'too-wide', tag: el.tagName, cls: String(el.className).slice(0, 40), w: Math.round(r.width), vw });
    }
    if (r.height === 0 && el.children.length > 0 && cs.display !== 'none' && cs.visibility !== 'hidden') {
      issues.push({ type: 'zero-height', tag: el.tagName, cls: String(el.className).slice(0, 40) });
    }
  }
  const box = document.querySelector('.score-box');
  if (box) {
    const br = box.getBoundingClientRect();
    issues.push({ type: 'score-box-size', w: Math.round(br.width), h: Math.round(br.height) });
    const cap = document.querySelector('.score-cap');
    if (cap) {
      const cr = cap.getBoundingClientRect();
      issues.push({
        type: 'score-cap',
        top: Math.round(cr.top), bottom: Math.round(cr.bottom), boxBottom: Math.round(br.bottom),
        overlapsBox: !(cr.bottom <= br.top || cr.top >= br.bottom),
        belowBox: cr.top >= br.bottom,
      });
    }
    const num = document.querySelector('.score-num');
    if (num) {
      const nr = num.getBoundingClientRect();
      issues.push({ type: 'score-num', w: Math.round(nr.width), h: Math.round(nr.height) });
    }
  }
  const counts = {
    score: (document.getElementById('score-num') || {}).textContent,
    issueLis: document.querySelectorAll('#issues li').length,
    del: document.querySelectorAll('.diff-del').length,
    add: document.querySelectorAll('.diff-add').length,
    beforeLen: (document.getElementById('before') || {}).textContent?.length || 0,
    afterLen: (document.getElementById('after') || {}).textContent?.length || 0,
    batchCards: document.querySelectorAll('.batch-card').length,
  };
  return { vw, vh, issues, counts };
})()`;

// ---------------------------------------------------------------- diff diag
// Inspects every rendered diff span: does its background extend past the last
// glyph (trailing whitespace inside the span)? Is it whitespace-only (a bare
// colored bar)? Also reports the text/background contrast of the highlight.
const DIFF_DIAG = `(() => {
  const hex = (s) => {
    const m = /^rgb\\((\\d+), (\\d+), (\\d+)/.exec(s);
    return m ? '#' + m.slice(1).map((v) => (+v).toString(16).padStart(2, '0')).join('') : s;
  };
  const lum = (c) => {
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})/.exec(c);
    if (!m) return 0;
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(parseInt(m[1], 16)) + 0.7152 * f(parseInt(m[2], 16)) + 0.0722 * f(parseInt(m[3], 16));
  };
  const spans = [...document.querySelectorAll('.diff-del, .diff-add')];
  const info = spans.map((el) => {
    const t = el.textContent;
    // trimEnd is escape-proof (unlike a regex inside this template literal).
    const trailingWs = t.length - t.trimEnd().length;
    const rect = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {
      cls: el.className,
      text: JSON.stringify(t.length > 40 ? t.slice(0, 40) + '…' : t),
      trailingWs,
      wsOnly: t.trim().length === 0,
      color: hex(cs.color),
      bg: hex(cs.backgroundColor),
      decoration: cs.textDecorationLine,
      w: Math.round(rect.width),
    };
  });
  const contrast = (fg, bg) => {
    const l1 = lum(fg), l2 = lum(bg);
    const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1];
    return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
  };
  const pairs = {};
  if (spans.length) {
    const a = spans.find((el) => el.className === 'diff-add');
    const d = spans.find((el) => el.className === 'diff-del');
    if (a) { const cs = getComputedStyle(a); pairs.add = { fg: hex(cs.color), bg: hex(cs.backgroundColor), ratio: contrast(hex(cs.color), hex(cs.backgroundColor)) }; }
    if (d) { const cs = getComputedStyle(d); pairs.del = { fg: hex(cs.color), bg: hex(cs.backgroundColor), ratio: contrast(hex(cs.color), hex(cs.backgroundColor)) }; }
  }
  return { spanCount: spans.length, trailing: info.filter((i) => i.trailingWs > 0 && !i.wsOnly), wsOnly: info.filter((i) => i.wsOnly), info: info.slice(0, 6), pairs };
})()`;

// ------------------------------------------------------------------------ main
const report = { url: appUrl, consoleErrors: [], exceptions: [], steps: [] };

async function main() {
  const profile = os.tmpdir() + "/nat-chrome-" + Date.now();
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
          report.consoleErrors.push(
            msg.params.args.map((a) => a.value ?? a.description ?? "").join(" ")
          );
        }
        if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") {
          report.consoleErrors.push(msg.params.entry.text);
        }
      }
    };

    // Desktop viewport, then a clean reload so metrics apply.
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1280,
      height: 900,
      deviceScaleFactor: 1,
      mobile: false,
    });
    await cdp.send("Page.reload", { ignoreCache: true });
    await waitFor(cdp, "document.readyState === 'complete'", "load", 15000);
    await waitFor(
      cdp,
      "document.querySelectorAll('#style option').length >= 4",
      "style dropdown populated (init fetch)",
      10000
    );

    console.log("== landing ==");
    await shot(cdp, "01-landing.png");
    report.steps.push({ step: "landing", diag: await evalJs(cdp, DIAG) });

    // Plan modal must open AND close (regression: .modal-backdrop display:flex
    // used to override the [hidden] attribute and stuck the popup open).
    const modalVisible = `(() => {
      const el = document.getElementById('plan-modal');
      if (!el) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    })()`;
    await evalJs(cdp, "document.getElementById('upgrade-btn').click()");
    await waitFor(cdp, modalVisible, "plan modal opens");
    await evalJs(cdp, "document.getElementById('plan-close').click()");
    await waitFor(cdp, `!(${modalVisible})`, "plan modal closes via Close button");
    await evalJs(cdp, "document.getElementById('upgrade-btn').click()");
    await waitFor(cdp, modalVisible, "plan modal re-opens");
    await evalJs(cdp, "document.getElementById('plan-modal').click()");
    await waitFor(cdp, `!(${modalVisible})`, "plan modal closes via backdrop click");
    report.steps.push({ step: "plan-modal", ok: true });

    // The app boots in Detector mode; switch to Humanizer for the rewrite
    // flows (diff, issues, export, batch, plagiarism). This check is about
    // UI health (rendering, layout, console), not LLM latency — the live
    // LLM streaming path is covered by unit tests — so uncheck the LLM
    // toggle and run the deterministic engine (instant, exercises the same
    // renderResult/diff/steps code paths).
    await evalJs(cdp, "document.getElementById('tab-humanizer').click()");
    await waitFor(
      cdp,
      "!document.getElementById('humanizer-controls').hidden",
      "humanizer mode active"
    );
    await evalJs(cdp, "document.getElementById('use-llm').click()");

    // Naturalize the pre-filled sample. With an LLM configured the stream
    // shows the deterministic preview instantly but the score lands only
    // when the LLM upgrade completes (can take 30-90s on slow gateways) —
    // the wait below is a generous ceiling, not a speed claim.
    await evalJs(cdp, "document.getElementById('run-humanize').click()");
    await waitFor(
      cdp,
      "!document.getElementById('analysis').hidden && /^\\d+$/.test(document.getElementById('score-num').textContent.trim())",
      "result rendered",
      180000
    );
    await sleep(200);
    console.log("== result ==");
    await shot(cdp, "02-result.png");
    report.steps.push({ step: "result", diag: await evalJs(cdp, DIAG) });
    report.steps.push({ step: "diff-diag", diff: await evalJs(cdp, DIFF_DIAG) });

    // Upload flow: put a real PDF on the file input and let the handler run.
    await cdp.send("DOM.enable");
    const { root } = await cdp.send("DOM.getDocument", { depth: 1 });
    const { nodeId } = await cdp.send("DOM.querySelector", {
      nodeId: root.nodeId,
      selector: "#file",
    });
    await cdp.send("DOM.setFileInputFiles", {
      nodeId,
      files: [path.resolve(".smoke/draft.pdf")],
    });
    await evalJs(
      cdp,
      "document.getElementById('file').dispatchEvent(new Event('change', { bubbles: true }))"
    );
    await waitFor(
      cdp,
      "!document.getElementById('export-bar').hidden && " +
        "/^\\d+$/.test(document.getElementById('score-num').textContent.trim())",
      "upload result + export bar rendered"
    );
    await waitFor(
      cdp,
      "document.getElementById('warnings').textContent.length > 0",
      "pdf extraction warning shown"
    );
    await sleep(200);
    console.log("== upload ==");
    await shot(cdp, "06-upload.png");
    report.steps.push({ step: "upload", diag: await evalJs(cdp, DIAG) });

    // Batch mode: two docs.
    await evalJs(cdp, `
      const t = document.getElementById('batch-input');
      t.value = 'Doc one: Furthermore, the data was noisy.\\n\\nDoc two: Moreover, we must leverage cutting-edge tools.';
      document.getElementById('run-batch').click();
    `);
    await waitFor(cdp, "document.querySelectorAll('.batch-card').length === 2", "batch cards");
    await sleep(200);
    console.log("== batch ==");
    await shot(cdp, "03-batch.png");
    report.steps.push({ step: "batch", diag: await evalJs(cdp, DIAG) });

    // Plagiarism check: verbatim copy against a provided source.
    await evalJs(cdp, `
      document.getElementById('plag-text').value =
        'Technology has quietly permeated every aspect of our daily lives. ' +
        'Digital tools have reshaped how organizations operate, and ' +
        'businesses that fail to adapt risk falling behind.';
      document.getElementById('plag-refs').value =
        'Technology has quietly permeated every aspect of our daily lives. ' +
        'Digital tools have reshaped how organizations operate, and ' +
        'businesses that fail to adapt risk falling behind.';
      document.getElementById('run-plag').click();
    `);
    await waitFor(
      cdp,
      "document.getElementById('plag-out').textContent.includes('similarity')",
      "plagiarism result rendered"
    );
    await sleep(200);
    console.log("== plagiarism ==");
    await shot(cdp, "07-plagiarism.png");
    report.steps.push({ step: "plagiarism", diag: await evalJs(cdp, DIAG) });

    // Mobile viewport.
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 390,
      height: 800,
      deviceScaleFactor: 1,
      mobile: true,
    });
    await cdp.send("Page.reload", { ignoreCache: true });
    await waitFor(cdp, "document.readyState === 'complete'", "mobile load", 15000);
    await waitFor(cdp, "document.querySelectorAll('#style option').length >= 4", "mobile dropdown", 10000);
    await sleep(300);
    console.log("== mobile ==");
    await shot(cdp, "04-mobile.png");
    report.steps.push({ step: "mobile", diag: await evalJs(cdp, DIAG) });
    await evalJs(cdp, "document.getElementById('tab-humanizer').click()");
    await sleep(150);
    await evalJs(cdp, "document.getElementById('run-humanize').click()");
    await waitFor(
      cdp,
      "!document.getElementById('analysis').hidden && /^\\d+$/.test(document.getElementById('score-num').textContent.trim())",
      "mobile result rendered",
      180000
    );
    await sleep(200);
    await shot(cdp, "05-mobile-result.png");
    report.steps.push({ step: "mobile-result", diag: await evalJs(cdp, DIAG) });
  } catch (err) {
    report.fatal = String(err && err.stack || err);
    console.error("FATAL:", err && err.message);
  } finally {
    if (cdp) cdp.close();
    chrome.kill();
  }

  writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log("\nConsole errors:", report.consoleErrors.length);
  report.consoleErrors.forEach((e) => console.log("  -", e));
  console.log("Exceptions:", report.exceptions.length);
  report.exceptions.forEach((e) => console.log("  -", e.slice(0, 200)));
  console.log(`Report: ${path.join(outDir, "report.json")}`);
}
main();
