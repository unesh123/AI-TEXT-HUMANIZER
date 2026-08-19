#!/usr/bin/env node
/**
 * Focused UI check: drives the humanize + detector flow and asserts the
 * new Plain register metric (verified human-writing memory) actually
 * renders in the results panel with real before → after values.
 *
 * Usage: node tools/ui_check_plain.mjs <app-url>
 * Prints a JSON report; exits nonzero on failure.
 */
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const appUrl = process.argv[2];
const outDir = "ui-check-plain";
const PORT = 9334;
if (!appUrl) {
  console.error("usage: node tools/ui_check_plain.mjs <app-url>");
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
      } else cdp.events.push(msg);
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
  const r = await cdp.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (r.exceptionDetails)
    throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}
async function waitFor(cdp, expr, what, timeoutMs = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await evalJs(cdp, expr)) return;
    await sleep(150);
  }
  throw new Error(`timed out waiting for: ${what}`);
}
async function shot(cdp, name) {
  const r = await cdp.send("Page.captureScreenshot", { format: "png" });
  writeFileSync(path.join(outDir, name), Buffer.from(r.data, "base64"));
}

// Pull the Plain register row out of the metrics grid: label, delta, bars.
const GRAB = `(() => {
  const cells = [...document.querySelectorAll('#metrics-grid .metric')];
  const row = cells.find((c) => c.querySelector('.metric-head strong')?.textContent === 'Plain register');
  if (!row) return null;
  const bars = [...row.querySelectorAll('.metric-bar')].map((b) => ({
    label: b.querySelector('span').textContent,
    value: parseInt(b.querySelector('b').textContent, 10),
    fill: parseFloat(b.querySelector('.fill').style.width) || 0,
  }));
  const delta = row.querySelector('.metric-delta')?.textContent || '';
  return {
    delta,
    before: bars.find((b) => b.label === 'before') || null,
    after: bars.find((b) => b.label === 'after') || null,
  };
})()`;

// Grab the prominent Plain register meter state from the result panel.
const METER = `(() => {
  const el = document.getElementById('plain-meter');
  if (!el) return { present: false };
  return {
    present: true,
    hidden: el.hidden,
    before: parseInt(document.getElementById('plain-meter-before').textContent, 10),
    after: parseInt(document.getElementById('plain-meter-after').textContent, 10),
    delta: document.getElementById('plain-meter-delta').textContent,
    beforeFill: parseFloat(document.getElementById('plain-meter-before-fill').style.width) || 0,
    afterFill: parseFloat(document.getElementById('plain-meter-after-fill').style.width) || 0,
  };
})()`;

// Grab the second gauge (plain register) next to the naturalness gauge.
const GAUGE = `(() => {
  const wrap = document.getElementById('plain-score-wrap');
  if (!wrap) return { present: false };
  const box = wrap.querySelector('.score-box');
  return {
    present: true,
    hidden: wrap.hidden,
    value: parseInt(document.getElementById('plain-score-num').textContent, 10),
    fill: parseFloat(box.style.getPropertyValue('--plain-score')) || 0,
  };
})()`;

const STIFF = "The utilization of advanced methodologies facilitates the attainment of optimal outcomes. Furthermore, it is imperative to ascertain the parameters of this approach prior to implementation.";

const report = { url: appUrl, consoleErrors: [], steps: [] };

async function main() {
  const profile = os.tmpdir() + "/nat-plain-" + Date.now();
  const chrome = spawn(CHROME, [
    "--headless=new",
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--window-size=1280,900", appUrl,
  ], { stdio: "ignore" });

  let cdp;
  try {
    const target = await waitForTarget();
    cdp = await Cdp.connect(target.webSocketDebuggerUrl);
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Log.enable");
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
          report.consoleErrors.push(msg.params.exceptionDetails.exception?.description || "exception");
        }
        if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") {
          report.consoleErrors.push(msg.params.args.map((a) => a.value ?? a.description ?? "").join(" "));
        }
        if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") {
          report.consoleErrors.push(msg.params.entry.text);
        }
      }
    };

    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.reload", { ignoreCache: true });
    await waitFor(cdp, "document.readyState === 'complete'", "load");
    await waitFor(cdp, "document.querySelectorAll('#style option').length >= 4", "style dropdown", 10000);

    // ---- Humanizer flow with stiff Latinate text -----------------------
    await evalJs(cdp, "document.getElementById('tab-humanizer').click()");
    await waitFor(cdp, "!document.getElementById('humanizer-controls').hidden", "humanizer mode");
    await evalJs(cdp, `document.getElementById('input').value = ${JSON.stringify(STIFF)}; document.getElementById('run-humanize').click();`);
    await waitFor(cdp, "!document.getElementById('analysis').hidden && /^\\d+$/.test(document.getElementById('score-num').textContent.trim())", "result rendered");
    await waitFor(cdp, `document.querySelectorAll('#metrics-grid .metric').length > 0`, "metrics grid populated");
    await sleep(300);

    const plainRow = await evalJs(cdp, GRAB);
    report.steps.push({ step: "humanize-plain-register", row: plainRow });
    const meter = await evalJs(cdp, METER);
    report.steps.push({ step: "humanize-plain-meter", meter });
    // The gauge number animates (700ms); wait for it to settle on the fill.
    await waitFor(
      cdp,
      `(() => {
        const wrap = document.getElementById('plain-score-wrap');
        if (!wrap || wrap.hidden) return false;
        const fill = parseFloat(wrap.querySelector('.score-box').style.getPropertyValue('--plain-score')) || 0;
        return parseInt(document.getElementById('plain-score-num').textContent, 10) === Math.round(fill);
      })()`,
      "plain gauge animation",
      5000,
    );
    const gauge = await evalJs(cdp, GAUGE);
    report.steps.push({ step: "humanize-plain-gauge", gauge });
    await shot(cdp, "01-humanize-plain.png");
    if (!plainRow) throw new Error("Plain register metric not rendered in humanize result");
    if (!plainRow.before || !plainRow.after) throw new Error("Plain register missing before/after bars");
    if (plainRow.before.value === undefined || plainRow.after.value === undefined) throw new Error("Plain register values not numbers");
    if (!meter.present) throw new Error("Plain register meter missing from result panel");
    if (meter.hidden) throw new Error("Plain register meter hidden after humanize");
    if (!(meter.before >= 0 && meter.after >= 0) || meter.before > 100 || meter.after > 100) {
      throw new Error(`Plain register meter out of range: ${meter.before} → ${meter.after}`);
    }
    if (!gauge.present) throw new Error("Plain register gauge missing from result panel");
    if (gauge.hidden) throw new Error("Plain register gauge hidden after humanize");
    if (gauge.value < 0 || gauge.value > 100 || gauge.fill !== gauge.value) {
      throw new Error(`Plain register gauge inconsistent: value ${gauge.value}, fill ${gauge.fill}`);
    }
    console.log(`  humanize: plain register ${plainRow.before.value} → ${plainRow.after.value} (${plainRow.delta}) | meter ${meter.before} → ${meter.after} (${meter.delta}) | gauge ${gauge.value} (${gauge.fill}%)`);

    // ---- Perfect humanize (feedback loop) shows the meter too ----------
    await evalJs(cdp, "document.getElementById('tab-humanizer').click()");
    await waitFor(cdp, "!document.getElementById('humanizer-controls').hidden", "humanizer mode again");
    await evalJs(cdp, `document.getElementById('input').value = ${JSON.stringify(STIFF)}; document.getElementById('perfect-btn').click();`);
    await waitFor(cdp, "!document.getElementById('perfect-out').hidden", "perfect loop output", 60000);
    await waitFor(
      cdp,
      `(() => {
        const wrap = document.getElementById('plain-score-wrap');
        if (!wrap || wrap.hidden) return false;
        const fill = parseFloat(wrap.querySelector('.score-box').style.getPropertyValue('--plain-score')) || 0;
        return parseInt(document.getElementById('plain-score-num').textContent, 10) === Math.round(fill);
      })()`,
      "perfect plain gauge animation",
      10000,
    );
    const perfectMeter = await evalJs(cdp, METER);
    report.steps.push({ step: "perfect-plain-meter", meter: perfectMeter });
    const perfectGauge = await evalJs(cdp, GAUGE);
    report.steps.push({ step: "perfect-plain-gauge", gauge: perfectGauge });
    await shot(cdp, "03-perfect-plain.png");
    if (!perfectMeter.present) throw new Error("Plain register meter missing after perfect humanize");
    if (perfectMeter.hidden) throw new Error("Plain register meter hidden after perfect humanize");
    if (!(perfectMeter.before >= 0 && perfectMeter.after >= 0) || perfectMeter.before > 100 || perfectMeter.after > 100) {
      throw new Error(`Perfect-loop plain meter out of range: ${perfectMeter.before} → ${perfectMeter.after}`);
    }
    if (!perfectGauge.present) throw new Error("Plain register gauge missing after perfect humanize");
    if (perfectGauge.hidden) throw new Error("Plain register gauge hidden after perfect humanize");
    console.log(`  perfect: plain register meter ${perfectMeter.before} → ${perfectMeter.after} (${perfectMeter.delta}) | gauge ${perfectGauge.value} (${perfectGauge.fill}%)`);

    // ---- Detector flow on the same text (shares #input + #run) ---------
    await evalJs(cdp, "document.getElementById('tab-detector').click()");
    await waitFor(cdp, "document.getElementById('tab-detector').classList.contains('active')", "detector mode");
    await evalJs(cdp, `document.getElementById('input').value = ${JSON.stringify(STIFF)}; document.getElementById('run').click();`);
    await waitFor(cdp, "!document.getElementById('detector-result').hidden", "detector result rendered");
    await sleep(300);
    const detectInfo = await evalJs(cdp, `(() => ({
      gauge: document.getElementById('gauge-num')?.textContent,
      cap: document.getElementById('gauge-cap')?.textContent,
      aiPct: document.getElementById('dist-ai-num')?.textContent,
      humanPct: document.getElementById('dist-human-num')?.textContent,
      note: document.getElementById('detector-note')?.textContent.slice(0, 140),
    }))()`);
    report.steps.push({ step: "detector", info: detectInfo });
    await shot(cdp, "02-detector.png");
    console.log("  detector:", JSON.stringify(detectInfo));

    console.log("  PASS");
  } catch (err) {
    report.fatal = String(err && err.stack || err);
    console.error("FATAL:", err && err.message);
  } finally {
    if (cdp) cdp.close();
    chrome.kill();
  }
  writeFileSync(path.join(outDir, "report.json"), JSON.stringify(report, null, 2));
  console.log("Console errors:", report.consoleErrors.length);
  report.consoleErrors.forEach((e) => console.log("  -", e.slice(0, 160)));
  console.log(`Report: ${path.join(outDir, "report.json")}`);
  if (report.fatal || report.consoleErrors.length) process.exit(1);
}
main();
