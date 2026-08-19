#!/usr/bin/env node
/**
 * Live humanizer-site probe — the honest version of "check the other sites".
 *
 * Visits free-tier AI humanizer sites (no signup needed), pastes one sample
 * through their real editor, waits for the humanized output, and saves it to
 * disk so tools/detector_bench.py can score it with the same floor gate used
 * everywhere else in this repo.
 *
 * Usage:
 *   node tools/humanizer_site_probe.mjs <input.txt> <out-dir> [site-url ...]
 *
 * One sample, one run, real browser, the site's own advertised free tier —
 * this is what a normal user does, not mass scraping. Sites that are
 * login-walled or Cloudflare-blocked are reported honestly and skipped.
 *
 * No dependencies: drives Chrome over CDP with Node's built-in WebSocket,
 * same pattern as tools/ui_check.mjs.
 */
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const CHROME =
  process.env.CHROME_PATH ||
  "C:/Program Files/Google/Chrome/Application/chrome.exe";
const PORT = 9444;

const [inputFile, outDir, ...siteArgs] = process.argv.slice(2);
const SITES =
  siteArgs.length > 0
    ? siteArgs
    : [
        "https://texttohuman.com",
        "https://humanizeai.pro",
        "https://quillbot.com",
      ];

if (!inputFile || !outDir) {
  console.error(
    "usage: node tools/humanizer_site_probe.mjs <input.txt> <out-dir> [site-url ...]"
  );
  process.exit(2);
}
if (!existsSync(CHROME)) {
  console.error(`Chrome not found at ${CHROME}`);
  process.exit(2);
}

const inputText = readFileSync(inputFile, "utf-8").trim();
mkdirSync(outDir, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- CDP client
class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
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

async function launchChrome() {
  const profile = path.join(os.tmpdir(), `hz-probe-${Date.now()}`);
  const chrome = spawn(
    CHROME,
    [
      "--headless=new",
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profile}`,
      "--no-first-run",
      "--disable-gpu",
      "--window-size=1400,1000",
      "--lang=en-US",
      "about:blank",
    ],
    { stdio: "ignore" }
  );
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const list = await res.json();
      if (list.some((t) => t.type === "page")) return chrome;
    } catch {}
    await sleep(250);
  }
  throw new Error("Chrome debugging port not up");
}

async function newTab() {
  const res = await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" });
  const target = await res.json();
  return Cdp.connect(target.webSocketDebuggerUrl);
}

async function evalJs(cdp, expression) {
  const r = await cdp.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (r.exceptionDetails) {
    throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  }
  return r.result.value;
}

async function shot(cdp, name) {
  try {
    const r = await cdp.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(path.join(outDir, name), Buffer.from(r.data, "base64"));
  } catch {}
}

// ------------------------------------------------------- page interaction
async function setInput(cdp) {
  // Find the largest textarea / contenteditable on the page and put the
  // sample in it, dispatching input events so framework bindings fire.
  return evalJs(
    cdp,
    `(() => {
      const text = ${JSON.stringify(inputText)};
      const tas = [...document.querySelectorAll('textarea')];
      const editable = [...document.querySelectorAll('[contenteditable="true"]')];
      const candidates = [...tas, ...editable].filter((el) => el.offsetParent !== null);
      if (candidates.length === 0) return { ok: false, why: 'no textarea/contenteditable' };
      candidates.sort((a, b) => (b.value?.length || b.textContent?.length || 0) - (a.value?.length || a.textContent?.length || 0));
      const el = candidates[0];
      const set = (e) => {
        if (e.tagName === 'TEXTAREA' || e.tagName === 'INPUT') {
          const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
          setter.call(e, text);
        } else {
          e.textContent = text;
        }
        e.dispatchEvent(new Event('input', { bubbles: true }));
        e.dispatchEvent(new Event('change', { bubbles: true }));
      };
      set(el);
      el.focus();
      return { ok: true, tag: el.tagName, count: candidates.length };
    })()`
  );
}

async function findAndClickButton(cdp) {
  return evalJs(
    cdp,
    `(() => {
      const re = /humaniz|rewrit|transform|convert|bypass|paraphrase/i;
      const buttons = [...document.querySelectorAll('button, [role="button"], input[type="submit"], a.btn, a.button')]
        .filter((b) => b.offsetParent !== null);
      const labelled = buttons.filter((b) => re.test((b.textContent || b.value || '').trim()));
      const pick = labelled[0] || buttons.find((b) => /submit|go|start/i.test(b.textContent || '')) || buttons[buttons.length - 1];
      if (!pick) return { ok: false, why: 'no button found' };
      const label = (pick.textContent || pick.value || pick.getAttribute('aria-label') || pick.className || '').trim().slice(0, 40);
      pick.click();
      return { ok: true, label };
    })()`
  );
}

async function grabOutput(cdp, pageHtml) {
  // After the run, collect every textarea value + long text blocks and pick
  // the one that is long, differs from the input, and looks like prose.
  return evalJs(
    cdp,
    `(() => {
      const input = ${JSON.stringify(inputText)};
      const out = [];
      for (const t of document.querySelectorAll('textarea')) {
        if (t.value && t.value.trim().length > 60 && t.value.trim() !== input) out.push(t.value.trim());
      }
      const el = [...document.querySelectorAll('pre, code, .result, .output, [class*="result"], [class*="output"], [id*="result"], [id*="output"]')]
        .filter((e) => e.offsetParent !== null);
      for (const e of el) {
        const t = (e.innerText || e.textContent || '').trim();
        if (t.length > 60 && t !== input) out.push(t);
      }
      return { texts: [...new Set(out)], html: (${JSON.stringify(pageHtml)} && document.title) || '' };
    })()`
  );
}

// ---------------------------------------------------------------- main
async function probeSite(url) {
  const cdp = await newTab();
  const result = { url, ok: false };
  try {
    await cdp.send("Page.enable");
    await cdp.send("Page.navigate", { url });
    await sleep(5000);

    const title = await evalJs(cdp, "document.title");
    const bodyStart = await evalJs(cdp, "document.body ? document.body.innerText.slice(0, 200) : ''");
    result.title = title;
    result.bodyStart = bodyStart;

    // Cloudflare / login-wall sniff — real challenge markers only. The word
    // "captcha" alone is a false positive (it appears in page bundles).
    const blocked = await evalJs(
      cdp,
      `(() => {
        const html = document.documentElement.outerHTML.slice(0, 300000);
        const title = (document.title || '').toLowerCase();
        const body = (document.body ? document.body.innerText : '').slice(0, 5000);
        const realCF = /challenge-platform|cf-chl-/i.test(html) ||
          title.includes('attention required') ||
          /sorry, you have been blocked|you are unable to access/i.test(body);
        return realCF;
      })()`
    );
    if (blocked) {
      result.why = "bot-challenge (Cloudflare/captcha)";
      await shot(cdp, url.replace(/^https?:\/\//, "").replace(/[^\w.-]/g, "_") + ".png");
      return result;
    }
    const loginWall = await evalJs(
      cdp,
      `/log in|sign in|create account/i.test(document.body ? document.body.innerText.slice(0, 5000) : '') && document.querySelector('input[type="password"]') !== null`
    );
    if (loginWall) {
      result.why = "login-wall";
      return result;
    }

    const set = await setInput(cdp);
    if (!set.ok) {
      result.why = "no-input: " + set.why;
      await shot(cdp, url.replace(/^https?:\/\//, "").replace(/[^\w.-]/g, "_") + ".png");
      return result;
    }
    await sleep(800);

    const click = await findAndClickButton(cdp);
    if (!click.ok) {
      result.why = "no-button: " + click.why;
      await shot(cdp, url.replace(/^https?:\/\//, "").replace(/[^\w.-]/g, "_") + ".png");
      return result;
    }
    result.clicked = click.label;

    // Poll up to 60s for output that differs from the input.
    let got = null;
    for (let i = 0; i < 120; i++) {
      await sleep(500);
      const g = await grabOutput(cdp, false);
      if (g.texts.length > 0) {
        // pick the longest candidate that is not the input
        const candidates = g.texts.filter((t) => t !== inputText);
        if (candidates.length) {
          got = candidates.sort((a, b) => b.length - a.length)[0];
          break;
        }
      }
    }
    if (!got) {
      result.why = "no-output-after-60s";
      await shot(cdp, url.replace(/^https?:\/\//, "").replace(/[^\w.-]/g, "_") + ".png");
      return result;
    }
    result.ok = true;
    const safeName = url.replace(/^https?:\/\//, "").replace(/[^\w.-]/g, "_");
    writeFileSync(path.join(outDir, safeName + ".txt"), got, "utf-8");
    result.outputChars = got.length;
    result.outputStart = got.slice(0, 150);
    await shot(cdp, safeName + ".png");
    return result;
  } catch (err) {
    result.why = "error: " + err.message;
    return result;
  } finally {
    cdp.close();
  }
}

async function main() {
  console.log(`Probing ${SITES.length} sites with ${inputText.split(/\s+/).length} words…\n`);
  const chrome = await launchChrome();
  const report = [];
  for (const url of SITES) {
    console.log(`→ ${url}`);
    const r = await probeSite(url);
    report.push(r);
    if (r.ok) {
      console.log(`  OK  saved ${r.outputChars} chars (${r.outputStart.slice(0, 60)}…)`);
    } else {
      console.log(`  SKIP ${r.why}`);
    }
    await sleep(1500);
  }
  writeFileSync(path.join(outDir, "probe_report.json"), JSON.stringify(report, null, 2), "utf-8");
  console.log(`\nReport: ${path.join(outDir, "probe_report.json")}`);
  chrome.kill();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
