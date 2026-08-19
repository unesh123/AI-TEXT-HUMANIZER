#!/usr/bin/env node
/**
 * Focused probe for the Detector tab: pastes an AI-heavy sample, clicks
 * Detect AI, verifies the distribution bars, circular gauge, verdict
 * banner, and sentence highlights all render with sane values.
 * Usage: node tools/detector_check.mjs <app-url> [out-dir]
 */
import { spawn } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const appUrl = process.argv[2];
const outDir = process.argv[3] || "ui-check";
const PORT = 9361;

if (!appUrl) { console.error("usage: node tools/detector_check.mjs <app-url>"); process.exit(2); }
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

const AI_SAMPLE =
  "In today's fast-paced world, it is important to note that technology plays a crucial role in our daily lives. " +
  "Furthermore, the ever-evolving landscape of digital tools continues to transform the way we work and communicate. " +
  "Moreover, it is essential to highlight that organizations must leverage cutting-edge solutions to remain competitive.";

async function main() {
  const profile = os.tmpdir() + "/nat-det-" + Date.now();
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
        if (p) { cdp.pending.delete(msg.id); msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result); }
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

    // Detector mode is the default; paste the AI sample and run.
    await evalJs(cdp, `document.getElementById('input').value = ${JSON.stringify(AI_SAMPLE)};`);
    await evalJs(cdp, "document.getElementById('run').click()");
    await waitFor(cdp, "!document.getElementById('detector-result').hidden", "detector result");
    await sleep(400);

    const probe = await evalJs(cdp, `(() => {
      const num = (id) => document.getElementById(id).textContent.trim();
      const width = (id) => document.getElementById(id).style.width;
      const sents = [...document.querySelectorAll('#highlighted .sent')].map((s) => s.className.replace('sent ', ''));
      const det = [...document.querySelectorAll('#det-sentences .det-sent')].map((s) => s.className);
      const gauge = document.getElementById('gauge').className;
      return {
        ai: num('dist-ai-num'), mix: num('dist-mix-num'), human: num('dist-human-num'),
        aiW: width('dist-ai'), humanW: width('dist-human'),
        gauge, gaugeNum: num('gauge-num'),
        banner: document.getElementById('verdict-banner').textContent.slice(0, 80),
        bannerHidden: document.getElementById('verdict-banner').hidden,
        sentCount: sents.length, aiSents: sents.filter((c) => c === 'sent-ai').length,
        detList: det,
        highlightsShown: document.getElementById('editor-wrap').classList.contains('show-highlight'),
        note: document.getElementById('detector-note').textContent.slice(0, 80),
      };
    })()`);

    report.probe = probe;
    report.ok =
      probe.aiW !== "0%" &&
      probe.gaugeNum !== "–" &&
      !probe.bannerHidden &&
      probe.sentCount > 0 &&
      probe.aiSents > 0 &&
      probe.highlightsShown;

    const r = await cdp.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(path.join(outDir, "detector-tab.png"), Buffer.from(r.data, "base64"));
    console.log("screenshot: detector-tab.png");
  } catch (err) {
    report.fatal = String(err && err.stack || err);
    console.error("FATAL:", err && err.message);
  } finally {
    if (cdp) cdp.close();
    chrome.kill();
  }

  writeFileSync(path.join(outDir, "detector-report.json"), JSON.stringify(report, null, 2));
  console.log("Console errors:", report.consoleErrors.length);
  console.log("Exceptions:", report.exceptions.length);
  console.log("Detector probe:", report.ok ? "PASS" : "FAIL");
  if (report.probe) {
    const p = report.probe;
    console.log(`  distribution: AI ${p.ai} / Mix ${p.mix} / Human ${p.human}`);
    console.log(`  gauge: ${p.gaugeNum} (${p.gauge})`);
    console.log(`  highlights: ${p.sentCount} sentences, ${p.aiSents} flagged AI (shown: ${p.highlightsShown})`);
    console.log(`  banner: ${p.banner}${p.bannerHidden ? " [HIDDEN!]" : ""}`);
  }
}
main();
