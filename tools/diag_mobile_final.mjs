#!/usr/bin/env node
import { spawn } from "node:child_process";
import os from "node:os";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const appUrl = "http://127.0.0.1:8000/";
const PORT = 9341;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); }
  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error("ws error")); });
    const cdp = new Cdp(ws);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id != null) {
        const p = cdp.pending.get(msg.id);
        if (p) { cdp.pending.delete(msg.id); msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result); }
      }
    };
    return cdp;
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => { this.pending.set(id, { resolve, reject }); this.ws.send(JSON.stringify({ id, method, params })); });
  }
  close() { try { this.ws.close(); } catch {} }
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
  throw new Error("no target");
}
async function evalJs(cdp, expression) {
  const r = await cdp.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}

async function main() {
  const profile = os.tmpdir() + "/nat-mobok-" + Date.now();
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
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 800, deviceScaleFactor: 1, mobile: true });
    await cdp.send("Page.reload", { ignoreCache: true });
    await sleep(2500);
    const diag = await evalJs(cdp, `(() => {
      const docW = document.documentElement.scrollWidth;
      const winW = window.innerWidth;
      const wide = [];
      for (const el of document.querySelectorAll('body *')) {
        const r = el.getBoundingClientRect();
        if (r.right > winW + 1 || r.left < -1) {
          wide.push({
            tag: el.tagName, id: el.id || '', cls: String(el.className).slice(0, 36),
            left: Math.round(r.left), right: Math.round(r.right), w: Math.round(r.width),
          });
        }
      }
      return {
        viewport: winW,
        docScrollW: docW,
        horizontalOverflow: docW > winW + 1,
        wideElements: wide.slice(0, 12),
      };
    })()`);
    console.log(JSON.stringify(diag, null, 2));
  } catch (err) {
    console.error("FATAL:", err && err.message);
  } finally {
    if (cdp) cdp.close();
    chrome.kill();
  }
}
main();
