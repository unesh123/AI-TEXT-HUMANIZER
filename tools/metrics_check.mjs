#!/usr/bin/env node
/**
 * Focused probe: confirms the metrics (detection signals) panel renders with
 * all four metrics and before/after values after a Naturalize click.
 * Usage: node tools/metrics_check.mjs <app-url> [out-dir]
 */
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const appUrl = process.argv[2];
const outDir = process.argv[3] || "ui-check";
const PORT = 9351;

if (!appUrl) { console.error("usage: node tools/metrics_check.mjs <app-url>"); process.exit(2); }
mkdirSync(outDir, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.events = []; }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => {
      const t = setTimeout(() => { ws.close(); rej(new Error("ws open timeout")); }, 8000);
      ws.onopen = () => { clearTimeout(t); res(); };
      ws.onerror = () => { clearTimeout(t); rej(new Error("ws error")); };
    });
    const cdp = new Cdp(ws);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id != null) {
        const p = cdp.pending.get(msg.id);
        if (p) { cdp.pending.delete(msg.id); msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result); }
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
  close() { try { this.ws.close(); } catch {} }
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
  throw new Error("no target");
}

async function evalJs(cdp, expression) {
  const r = await cdp.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}

async function waitFor(cdp, expr, what, timeoutMs = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await evalJs(cdp, expr)) return;
    await sleep(150);
  }
  throw new Error("timed out: " + what);
}

const report = { consoleErrors: [], exceptions: [], ok: false };

async function main() {
  const profile = os.tmpdir() + "/nat-metrics-" + Date.now();
  const chrome = spawn(CHROME, [
    "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--window-size=1280,900", appUrl,
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
          report.exceptions.push(msg.params.exceptionDetails.exception?.description || "exception");
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
    await waitFor(cdp, "document.querySelectorAll('#style option').length >= 4", "dropdown");

    await evalJs(cdp, "document.getElementById('tab-humanizer').click()");
    await sleep(150);
    await evalJs(cdp, "document.getElementById('run-humanize').click()");
    await waitFor(cdp,
      "!document.getElementById('analysis').hidden && !document.getElementById('metrics').hidden",
      "metrics panel visible");
    await sleep(200);

    const probe = await evalJs(cdp, `(() => {
      const cards = [...document.querySelectorAll('#metrics-grid .metric')].map((el) => {
        const label = el.querySelector('.metric-head strong').textContent;
        const delta = el.querySelector('.metric-delta')?.textContent || '';
        const vals = [...el.querySelectorAll('.metric-bar')].map((b) => b.querySelector('b').textContent);
        const widths = [...el.querySelectorAll('.fill')].map((f) => f.style.width);
        return { label, delta, vals, widths };
      });
      const note = document.getElementById('metrics-note').textContent;
      const hidden = document.getElementById('metrics').hidden;
      return { cards, note, hidden };
    })()`);

    report.probe = probe;
    report.ok = probe.cards.length === 4 &&
      probe.cards.every((c) => c.vals.length === 2) &&
      !probe.hidden;

    const r = await cdp.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(path.join(outDir, "metrics-panel.png"), Buffer.from(r.data, "base64"));
    console.log("screenshot: metrics-panel.png");
  } catch (err) {
    report.fatal = String(err && err.stack || err);
    console.error("FATAL:", err && err.message);
  } finally {
    if (cdp) cdp.close();
    chrome.kill();
  }

  writeFileSync(path.join(outDir, "metrics-report.json"), JSON.stringify(report, null, 2));
  console.log("Console errors:", report.consoleErrors.length);
  console.log("Exceptions:", report.exceptions.length);
  console.log("Metrics probe:", report.ok ? "PASS (4 cards, before/after values)" : "FAIL");
  if (report.probe) {
    report.probe.cards.forEach((c) =>
      console.log(`  ${c.label.padEnd(18)} ${c.vals.join(" -> ")}  ${c.delta}`));
  }
}
main();
