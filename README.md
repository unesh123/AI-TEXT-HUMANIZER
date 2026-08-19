# Naturalizer

A zero-dependency toolkit that turns stiff, machine-generated drafts into
writing that reads like it came from a human. It **scores** prose for the
linguistic fingerprints common in AI output (filler buzzwords, formulaic
transitions, flat sentence rhythm, repetitive openers) and **rewrites** it
with semantics-preserving transformations.

Nothing here evades detection systems, strips watermarks, or hides
authorship — it simply makes machine prose read more naturally, which is
useful on its own (better emails, reports, and blog drafts).

## Features

- **Naturalness score (0–100)** with a per-issue breakdown: every detected
  AI tell comes with the offending snippet and a concrete fix suggestion,
  tagged by category (`filler`, `cliche`, `hedge`, `transition`,
  `formulaic`, `openers`, `rhythm`, `structure`, `lexical`, …). Beyond the
  phrase patterns it also scores structural signals adapted from the
  MIT-licensed [lynote-ai/ai-text-detector](https://github.com/lynote-ai/ai-text-detector):
  structured-answer shape (list/heading lines), lexical variety
  (unique-token ratio), and compressibility (zlib ratio) — the latter two
  length-gated so they only fire on long text — plus a soft low-confidence
  note on samples under 30 words and the classic ChatGPT self-reference
  tells (`as an AI`, `I'm sorry, but I can't…`). The structure flag is
  style-aware: **Business** keeps bulleted summaries as a legitimate
  register choice, while Academic/Creative/Casual flag them.
- **Detection signals panel** — the four statistical tells real detectors
  look at, shown live with a before → after comparison after every rewrite:
  **randomness** (perplexity, approximated via zlib — AI text is more
  predictable), **sentence variety** (burstiness — the coefficient of
  variation of sentence lengths), **formulaic patterns** (freedom from
  filler/cliché/hedge shapes), and **flow & cohesion** (lexical overlap
  between neighbouring sentences). Higher is always more human; the
  length-gated signals read "—" on short samples because no statistical
  signal exists there (burstiness needs 5+ sentences — variance on 3-4
  sentence lengths is noise, so those texts are honestly abstained on
  rather than given a false-precision rhythm score).
- **Windowed passage layer (Turnitin-style)** — commercial detectors do not
  label isolated sentences; they score overlapping windows and aggregate, so
  a sentence's verdict is influenced by its neighbours. The detector now
  does the same: per-sentence labels are neighbour-smoothed with a
  `[0.6 own, 0.2 left, 0.2 right]` prior (a clean sentence inside an
  AI-heavy run is pulled up; an isolated flagged sentence in clean prose is
  pulled down), and the result is surfaced as **contiguous AI regions** —
  passage-level evidence ("sentences 1–3 read as one machine-written
  block") alongside the per-sentence list. The ⚡ Perfect loop converges
  against this passage view, not just the overall score: it keeps rewriting
  until no 3+-sentence AI block remains.
- **Honest confidence & abstention** — short samples (< 30 words) and
  list/heading-dominated text report a low-confidence verdict with an
  explicit abstention reason instead of a false-precision number, and the
  confidence figure blends the score with how many statistical signals
  actually measured something (a 20-word sample has almost no  statistical evidence behind it).
- **Invisible-Unicode watermark hygiene** — zero-width spaces/joiners, bidi
  controls, and filler codepoints are *invisible* machine fingerprints
  (the exact carriers stealth humanizers inject). The detector flags them
  (bidi controls `high` — they can reorder visible text and flip meaning;
  everything else `medium`) and the rewrite chain strips them on every
  path, so an invisible fingerprint can never survive into the output.
  Borrowed from the watermarks-remover project's Layer-A hygiene.
- **Deterministic rewrite engine** — no API key, no network:
  - cuts **257 AI tell patterns** — buzzwords (`leverage`, `synergy`,
    `comprehensive`), institutional/academic scaffolding that survives LLM
    rewrites (`our analysis focused on`, `must also consider`, `intended
    and unintended`, `we first sought to`, `we were also asked to`, `this
    assignment required us to`, `successfully implemented`),
    `comprehensive`),
    dead metaphors (`double-edged sword`, `hit the ground running`,
    `in a nutshell`), hedging (`It's worth mentioning that…`, `as we
    all know`, `due to the fact that`), formulaic structures
    (`not only … but also`, `plays a key role in`), corporate jargon
    (`circle back`, `deep dive into`), and the modern LLM-era tells
    (`unpack`, `reimagine`, `digital transformation`, `bridge the gap`,
    `a wealth of`, `first and foremost`, `I hope this email finds you
    well`, template closers like `do not hesitate to`), plus the
    Wikipedia "Signs of AI writing" set (negative parallelism
    `It's not just X, it's Y`, signposting `Let's dive in`,
    sycophantic praise, promotional `nestled within`, formulaic
    `Despite challenges, … thrives`, fake-candid `Honestly?`,
    aphorism formulas `Patience is the key to success`) — all
    case-preserving, plus three low-severity structural flags: the
    manufactured **rule of three** (`innovation, inspiration, and
    insights` — never concrete triads like `flour, sugar, and eggs`),
    **staccato drama** (`No prior. No nostalgia.` — a run of very
    short sentences), and the balanced **antithesis scaffold**
    (`not treated as aspirations but as qualities` / `not acknowledged
    …; they are treated as …`), plus the em-dash **density** tell
    (≥10 per 1000 words — never a count-based flag) — never plain speech like
    `She is not tall but she is quick`), plus a **density-based em-dash
    check** (3+ dashes at 10+ per 1000 words — 4 dashes in a 223-word
    paragraph is a tell; 4 across a 2,000-word report is not)
  - varies formulaic transition openers (`Furthermore,` → `Beyond that,`)
  - removes hedges safely at any position and re-capitalizes the sentence
    (`It is clear that the plan worked` → `The plan worked`)
  - softens heavy em-dash use (density-aware: only texts with 3+ dashes
    at 10+ per 1000 words get the pass — sparse use is left alone)
  - varies sentence rhythm by splitting long sentences and merging short
    ones (merges never graft onto a discourse marker or leave a ". ," seam),
    restoring burstiness in uniformly mid-length prose the way real
    detectors weigh it (103 word-level swaps and **193 phrase swaps** total)
  - swaps `in turn`/`as such`/`moving forward` only where they read as
    connectors or noun subjects, so literal and natural uses ("we each spoke
    in turn", "the plan, as such,", "Moving forward, we should …") survive
  - drops ChatGPT self-reference openers cleanly (`As an AI language model,
    I cannot …` → `I can't …`) without mangling human idioms (`I cannot
    wait`, `I cannot stress`)
  - keeps article grammar intact ("a silver lining" → "an upside") and
    leaves tense-ambiguous or human-idiom phrases (bare "hit the ground
    running", "on the same page", "necessary evil", "bandwidth for")
    as detector-only, so natural prose is never mangled
- **Style profiles** — Academic, Business, Creative, Casual — each with its
  own vocabulary allowlist and sentence-length targets. The allowlists are
  register-aware for the cliché/hedge swaps: **Business** keeps meeting-room
  idiom (`circle back`, `touch base`, `move the needle`, `bandwidth for`)
  and bulleted summaries while still stripping inflated AI verbs
  (`leverage`, `synergy`, `utilize`);
  **Casual** keeps spoken idiom (`silver lining`, `to be honest`, `for what
  it's worth`); **Academic** flags and rewrites all of them as AI tells.
- **Optional LLM backend** (Claude + CX GPT gateway) for higher-quality
  rewrites. Reads `HINAA_CLAUDE_*` / `CX_GATEWAY_*` from `.env.local` (or
  `.env`) at startup; tries Claude first, falls back to the CX gateway,
  then to the deterministic engine when unset or unreachable.
- **Web UI** with before/after comparison (word-level diff highlighting —
  removals struck through in red, additions in green), score gauge, issue
  list, batch mode, and a drag-and-drop document upload.
- **Document upload & export** — upload a **TXT, DOCX, or PDF**, extract its
  text, naturalize it, and download the result back as TXT, DOCX, or PDF.
  Extraction is pure stdlib (PDF support covers text streams compressed with
  Flate/ASCIIHex/ASCII85; scanned/image-only PDFs have no extractable text).
- **Test corpus** — `tests/corpus/` holds AI-tell-heavy and human-written
  samples as a regression guard for the detector, plus
  `live_llm_samples.txt` — real commercial-LLM output captured through
  the configured provider (provenance documented in the file header),
  kept separate from the tuned AI set so the benchmark's accuracy number
  stays honest. The human samples
  deliberately include phrases the rewrite knows about (literal `in turn` /
  `as such`, `on the same page`, `bandwidth for`, present-tense `hit the
  ground running`, `Moving forward,`) — the corpus test asserts the rewrite
  leaves every one of them untouched. The AI samples carry a floor guarantee
  across **all three** rewrite paths: deterministic ≥ 75, single-pass LLM
  ≥ 80, and deep translation-chain ≥ 75 (post-rewrite naturalness scores).
  The LLM-path fixtures are recorded outputs from the real providers
  (`tests/corpus/llm_*_fixtures.json`) so the suite stays hermetic;
  regenerate them with `python tools/gen_llm_fixtures.py` after prompt or
  corpus changes.
- **Plagiarism / similarity check** — a local, pure-stdlib n-gram
  containment checker that scores a document against reference sources you
  provide and reports per-source overlap plus the exact matching sentences.
  It is deliberately local: it can only see the texts you paste (no
  internet, no commercial database), and it catches *verbatim and
  near-verbatim copying* — the shape of a real Turnitin-style flag — not
  heavy paraphrase. The verdict is a heuristic, like every detector, not
  proof of authorship.
- **Rewrite intensity & run-to-run variety** — an intensity control (Light →
  Strong) scales how aggressively the deterministic engine restructures
  prose. At higher intensity it also runs **synonym-variety** and
  **contraction** passes (contractions are style-gated: Business / Creative /
  Casual contract, Academic stays formal), all probabilistic — the same
  input with a different seed produces different word choices, so the
  rewrite is never one fixed algorithm. The UI's **↻ Vary** button and the
  API's `seed` parameter surface this.
- **Perfect humanize (live feedback loop)** — the **⚡ Perfect humanize**
  button (or `POST /api/perfect`) runs the untell-style closed loop:
  rewrite → re-scan with the detector → feed the remaining AI tells back
  into the next pass as a concrete instruction → repeat until the scan
  clears the human floor (80/100), capped at 4 passes so it can't burn
  unbounded credits. The result shows the per-pass naturalness trail
  (`23 → 61 → 88`), which engine ran, and the detector-coverage panel.
  Works on the deterministic engine too, but converges far better with an
  LLM provider configured.
- **Multi-detector panel with live third-party scores** — the **🔌
  Detectors** button (or `GET /api/detectors`) reports which detectors
  are live: the built-in detector is always on, and **GPTZero and ZeroGPT
  make real API calls** (`POST /api/detectors/scan`) when their keys are
  in `.env.local` — the panel shows each one's live **%AI score and
  verdict for the current text**, and the perfect-humanize result carries
  them too. Originality.ai and Turnitin are status-only (paid API / no
  developer API respectively) — their rows stay honest about that.
- **Humanize again / sentence re-humanize** — one click re-runs the
  humanizer on its own output (bumping intensity each pass so it
  restructures more), or humanize a single flagged sentence and splice it
  back into the text instead of reworking the whole document.
- **Saved history** — every Naturalize / ⚡ Perfect run is persisted
  (`input` + `rewrite` + `score`, plus style, mode, provider, LLM flag and
  pass count) to `state/history.json`. The **🕘 History** button in the UI
  lists recent runs — expand one to see input and rewrite side by side,
  **Use input** to reload it for another pass, copy the rewrite, or delete
  individual entries / clear all.
- **Detector & humanizer accuracy benchmark** — the **📊 Accuracy** button
  (or `GET /api/benchmark`) runs the local detector against the labeled
  corpus and reports precision / recall / F1, plus how much the humanizer
  lifts the naturalness score — with a **before → after card per writing
  style** (academic, business, creative, casual). Measured on the bundled
  corpus: detector **97.8% accuracy / F1 0.96**, and per style the
  deterministic humanizer lifts mean naturalness **23.6 → 94.0**
  (academic, +70.4), and with **100%** of AI samples clearing the 75
  floor in every style. This is the honest, local "how accurate am I"
  report — real third-party comparison (GPTZero, Turnitin, …) needs
  their paid APIs.

  **What the accuracy number means (and doesn't):** the labeled corpus
  (`ai_samples.txt` / `human_samples.txt`) is the tuned, best case. The
  report now also carries a **live_llm** bucket — real commercial-LLM
  output captured through the configured provider (see
  `tests/corpus/live_llm_samples.txt` for provenance). Those samples are
  *external*: not crafted, never tuned on, and reported separately from
  the accuracy number, because modern LLM prose reads human-grade to a
  heuristic detector. Honest baseline at the time of writing: the local
  detector flags **0 of 6** live-LLM samples as AI. That is the
  real-world gap this project's perfect-humanize loop and live GPTZero
  verification exist to close — the tuned 97.8% is a ceiling, not a
  floor. The human corpus also now includes **6 authored student essays**
  (personal-narrative register) that must pass the same invariants as
  the rest: score ≥ 75, no high-severity issues, and byte-identical
  under rewrite.
- **Live realistic-prose benchmark** — `tools/detector_bench.py` measures
  the rewrite paths against *fluent modern AI writing* (blog, academic,
  email, product, commentary — low-perplexity, uniform-rhythm prose with
  almost no classic buzzwords, the hard cases pattern matching barely
  touches). Pass means the naturalness floor **and** every statistical
  signal real detectors weigh — perplexity, burstiness, syntactic
  variety, and word-choice predictability (the surprisal/rarity signal,
  built on embedded real English word frequencies, no API key needed) —
  all sit in the human band. Measured with real provider calls: the
  **deterministic** engine passes **16/16 samples (100%)** — 5 realistic
  prose samples plus the full AI corpus, up from 6.2% on the original
  inputs — and **⚡ Perfect humanize** passes **16/16 (100%)**, the loop
  converging on the full human band (it re-passes when rhythm or
  vocabulary still reads machine-written, not just when the score
  clears the floor). The burstiness pass (merging/splitting uniformly
  mid-length sentences, now including comma-conjunction splits like
  "evil, but…" that previously never fired) is what closes the gap; it
  only fires when the source text still carries AI tells, so naturally
  rhythmic prose is never touched.
- **Humanizing animation** — staged progress (Analyzing → Rewriting →
  Verifying), a typewriter reveal of the rewrite, and an animated score
  gauge while the run happens.
- **Real-time streaming (SSE)** — check **Stream** and the rewrite
  appears *word by word* as the LLM generates it (live provider deltas,
  or a paced reveal of the deterministic rewrite when no LLM is
  configured), ending with the same verification re-scan and result
  payload as the plain endpoint. While a run is quiet the server sends
  `ping` keep-alives every ~8s, and Deep humanize reports its progress
  live (`hop 3/4 — suomi`), so the stream never looks frozen; if the
  connection stalls for 25s the UI surfaces an error instead of hanging
  on "Streaming…".
- **Free / Pro plan structure** — see [Plans](#plans) below.
- **JSON API** for scripted use.

## Quick start

Requires Python 3.9+. No packages to install.

**Windows: just double-click `start.bat`.** It finds Python (or the `py`
launcher), picks a free port (8000, or the next one up if it's busy), starts
the server, and opens the site in your browser. Ctrl+C in the window stops
it.

```bash
# Run the server (web UI + API)
python server.py
# -> http://127.0.0.1:8000

# Custom port
PORT=9000 python server.py
```

### Command line

Same extract → naturalize → export pipeline, without a server:

```bash
# Naturalize a DOCX in place
python -m naturalizer.cli draft.docx                 # -> draft-naturalized.docx

# Convert format at the same time
python -m naturalizer.cli draft.pdf -f txt           # -> draft-naturalized.txt
python -m naturalizer.cli draft.docx -o clean.pdf    # extension picks the format

# Multiple files, one output each
python -m naturalizer.cli a.txt b.pdf

# Full result as JSON (score, issues, diff, rewritten text)
python -m naturalizer.cli draft.docx --json

# Other flags: --style business, --no-llm, --overwrite
```

Exit codes: `0` success, `1` a file failed (unreadable, no extractable
text, output exists without `--overwrite`), `2` bad usage.

Use it from code:

```python
from naturalizer import Naturalizer

n = Naturalizer()
result = n.naturalize(
    "In today's fast-paced world, it is important to note that technology "
    "plays a crucial role. Furthermore, we must leverage cutting-edge tools.",
    style="business",
)
print(result.score)      # 0-100 naturalness
print(result.rewritten)  # deterministic rewrite
print(result.llm_rewritten)  # set when an LLM backend is configured
```

## API

| Endpoint | Body | Returns |
|---|---|---|
| `GET /api/status` | — | version, styles, whether an LLM is configured, upload limits, plan (name, features, words left today) |
| `GET /api/benchmark` | — | detector accuracy (precision/recall/F1 on the labeled corpus) + humanizer lift |
| `GET /api/detectors` | — | configured/live status of every detector (local + GPTZero, ZeroGPT, Originality.ai, Turnitin) |
| `POST /api/detectors/scan` | `{"text": "..."}` | live **%AI scores** from every configured third-party detector (`results`: `{name, label, score, verdict, error}` per detector) |
| `POST /api/perfect` | `{"text": "...", "style": "academic", "intensity": 0.75, "seed": 0, "provider": "auto"}` | feedback-loop humanize: `text`, per-pass `scores`, `passes`, `method`, `llm_used`, `detectors` panel (Pro / LLM required) |
| `POST /api/compare` | `{"text": "...", "style": "academic"}` | live multi-provider comparison: `{best, candidates (ranked, each with score/plain/fact loss/reason), blocked (providers that need a key or failed)}` |
| `POST /api/naturalize` | `{"text": "...", "style": "academic", "use_llm": true, "deep": false, "provider": "auto", "intensity": 0.6, "seed": 3}` | score, issues, rewritten, llm_rewritten, diff, metrics (before/after signals + post-rewrite score), intensity; plus `plan_note` when the active plan downgrades a requested feature |
| `POST /api/naturalize/stream` | same body as `/api/naturalize` | **Server-sent events** — `event: status` (analyzing/rewriting/verifying), `event: delta` (each word as it's generated, live from the LLM provider or word-by-word for the deterministic fallback), then `event: done` with the identical payload a non-streaming call returns. Free-plan LLM requests downgrade with a `plan_note` on the done event; deep is 402-gated like the plain endpoint. |
| `POST /api/batch` | `{"texts": ["...", "..."], "style": "business"}` | `{"results": [...]}` |
| `POST /api/upload` | `multipart/form-data` with `file` (+ optional `style`, `use_llm`) | result JSON plus `format` and `warnings` — or, with `?format=txt\|docx\|pdf`, the rewritten file itself |
| `POST /api/export` | `{"text": "...", "format": "docx"}` | the rendered file (TXT / DOCX / PDF download) |
| `POST /api/plagiarism` | `{"text": "...", "refs": ["source one", "..."]}` | similarity report: `score` (0–100), `verdict` (`low`/`medium`/`high`), `word_count`, `per_ref` breakdown, matching sentence spans, note |
| `GET /api/history` | `?limit=50` | saved runs, newest first — each `{id, iso, style, mode, provider, llm_used, plan, input, output, score, passes}` |
| `POST /api/history/delete` | `{"id": "..."}` | `{"deleted": true}` (404 when the entry is gone) |
| `POST /api/history/clear` | — | `{"cleared": N}` |

`diff` is a word-level change list between the original and the shown
rewrite: `[{"type": "same"|"del"|"add", "text": "..."}, ...]` in
document order. Concatenating `same`+`del` reproduces the original;
`same`+`add` reproduces the rewrite.

```bash
curl -s -X POST http://127.0.0.1:8000/api/naturalize \
  -H "Content-Type: application/json" \
  -d '{"text": "Furthermore, the data was noisy.", "style": "business"}'

# Check a draft for verbatim overlap against source material
curl -s -X POST http://127.0.0.1:8000/api/plagiarism \
  -H "Content-Type: application/json" \
  -d '{"text": "Your draft goes here.", "refs": ["The original source text."]}'
```

### Document upload

Upload a document, naturalize its text, and get the result back as a file
(TXT, **Markdown**, DOCX, or PDF — Markdown extracts as plain text):

```bash
# Naturalize a PDF and download the rewrite as a DOCX
curl -s -o rewritten.docx \
  -X POST -F "file=@draft.pdf" "http://127.0.0.1:8000/api/upload?format=docx"

# Same for a Markdown file
curl -s -X POST -F "file=@notes.md" http://127.0.0.1:8000/api/upload

# Same upload, but get the JSON result (score, issues, diff) instead
curl -s -X POST -F "file=@draft.docx" http://127.0.0.1:8000/api/upload

# Export already-naturalized text as a PDF
curl -s -o out.pdf -X POST http://127.0.0.1:8000/api/export \
  -H "Content-Type: application/json" \
  -d '{"text": "The naturalized result goes here.", "format": "pdf"}'
```

Notes:

- Uploads are capped at 10 MB by default (override with
  `MAX_UPLOAD_BYTES`).
- TXT and DOCX extraction is faithful (paragraphs, tabs, runs). PDF
  extraction is **best-effort**: text is pulled from content streams in file
  order, so layout and reading order may not be perfect, and scanned PDFs
  yield no text. The API reports a `warnings` list when this applies.
- The rewritten file is a plain text reflow (DOCX/PDF export keeps your
  *text*, not the original layout).

## LLM backend (optional)

Create a `.env.local` (or `.env`) in the project root — the server and CLI
load it at startup. Values already set in the shell environment always win.

```dotenv
# Claude (native Anthropic API, or an OpenAI-compatible proxy that fronts
# Claude models — the provider auto-falls back between the two protocols)
HINAA_CLAUDE_API_KEY=sk-...
HINAA_CLAUDE_BASE_URL=https://api.anthropic.com
HINAA_CLAUDE_MODEL=claude-sonnet-4-6
# HINAA_CLAUDE_PROTOCOL=anthropic   # default; use "openai" for chat/completions proxies

# CX GPT gateway (any OpenAI-compatible chat/completions endpoint)
CX_GATEWAY_API_KEY=...
CX_GATEWAY_BASE_URL=https://your-gateway.example
CX_GATEWAY_MODEL=cx/gpt-5.6-sol

# Direct OpenAI
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

# Google Gemini (OpenAI-compatible endpoint)
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.0-flash

# Alibaba Qwen (DashScope compatible mode)
HINAA_QWEN_API_KEY=...
HINAA_QWEN_MODEL=qwen-plus

# Agent router (needs both key and base URL)
AGENT_ROUTER_API_KEY=...
AGENT_ROUTER_BASE_URL=https://router.example
AGENT_ROUTER_MODEL=...

# hcnsec gateway (OpenAI-compatible, DeepSeek/Kimi models)
OPENAI_CODEX_API_KEY=...
OPENAI_CODEX_BASE_URL=https://api.hcnsec.cn/v1
OPENAI_CODEX_MODEL=DeepSeek-V4-Pro

# Standalone HCNSEC key (OpenAI-compatible)
HCNSEC_API_KEY=...
HCNSEC_BASE_URL=https://api.hcnsec.cn/v1
HCNSEC_MODEL=deepseek-chat
```

```bash
python server.py
```

`rewrite_with_llm` tries every configured provider in order — **Claude
first**, then CX gateway, OpenAI, Gemini, Qwen, agent router, and the
hcnsec gateway — so the more engines you configure, the less any single
model's fingerprint shapes the rewrite. Consecutive runs of the same
draft also rotate through **five seeded rewrite voices** (plain-spoken,
student-explaining, professional, journalistic) so no single uniform
"AI rewrite style" ever emerges to be fingerprinted — the same input
reads differently on each run while facts and meaning stay identical.
When nothing is configured (or every provider fails), the engine falls
back to the deterministic path, so the tool never breaks. Picking a
specific provider
(`provider=claude|cx|openai|gemini|qwen|router|codex|hcns`) that is
missing or failing is never silent: the API returns `llm_warning`
explaining the fallback, and the UI shows it under the result. Requests
send a browser-like User-Agent because some gateways 403 urllib's
default.

**Provider failover:** an explicitly picked provider that is *configured
but down* (e.g. the CX gateway 429-rate-limited) never drops to the
deterministic path — the call falls through to the next working
provider in the chain, and the result's `llm_provider` names the one
that actually served (never a lie about which model wrote the text). The
`llm_warning` only appears when *every* configured gateway failed. The
same failover applies to the deep translation chain (hops 1-2 retry on
the secondary engine) and to live streaming.

**Verified human-writing memory + plain register:** the engine keeps a
memory of *how regular humans actually write* — the verified corpus
(`tests/corpus/human_samples.txt`) plus an everyday-vocabulary model and
a 1256-entry plain-register swap table (`utilize -> use`, `commence ->
start`, `ascertain -> find out`), where every source word is asserted to
have **zero hits across the human corpus**, so a demotion can never touch
natural prose (the corpus scores 1.0 against the memory; stiff Latinate
prose ~0.45). Both engines write from the same memory: the deterministic
path runs the plain-language pass, and the LLM path receives
`plain_register_guidance` naming the draft's own complex words to demote.
The result's `metrics.plain_register.before/after` shows the shift.

> Note: `trycloudflare.com` tunnel URLs are temporary — if the CX gateway
> stops answering (`000`/connection errors), refresh the tunnel and update
> `CX_GATEWAY_BASE_URL` in `.env.local`.

## Live GPTZero / ZeroGPT comparison

The **🔌 Detectors** panel compares your text against the real GPTZero and
ZeroGPT APIs — not just status, but each one's **%AI score and verdict**
for whatever text is in the input box (and the perfect-humanize result
shows them for the final rewrite). Both clients live in
`naturalizer/detectors_live.py`; nothing is called until a key exists.

### Get a GPTZero API key

1. Go to <https://gptzero.me/developers> and create an account.
2. Copy your API key from the dashboard (looks like a long hex string).
3. Add it to `.env.local`:

   ```dotenv
   GPTZERO_API_KEY=your_key_here
   ```

4. Restart `python server.py`, click **🔌 Detectors** — the GPTZero row
   now shows `live`, and with text in the input box it displays
   **GPTZero's %AI score** (e.g. `AI 96%`).

   GPTZero's free tier gives a limited number of scans; the client
   surfaces quota errors honestly ("no AI probability (check quota?)")
   when you run out.

### ZeroGPT

ZeroGPT's public detection endpoint (`/api/detect/detectText`) used to be
keyless, but it now **403s without a paid account** ("Please make a
purchase"). The client still *attempts* the call keyless (it can't hurt),
but you'll need a real account key for a score:

```dotenv
ZEROGPT_API_KEY=your_key_here
```

If the endpoint rejects the request, the row shows the provider's own
message and suggests GPTZero instead — the panel never fakes a score.

### Originality.ai / Turnitin

- **Originality.ai** has a real paid API (`ORIGINALITY_API_KEY`) but no
  free tier — the row stays `not configured` until you add a key.
- **Turnitin** has no individual/developer API (detection is baked into
  its LMS submission pipeline) — its row is honest about that and will
  never show a score.

### Try it end-to-end

```bash
# 1. add your key to .env.local, then start the server
python server.py
# 2. open http://127.0.0.1:8000, paste AI-looking text
# 3. click 🔌 Detectors -> GPTZero (and ZeroGPT) show live %AI scores
# 4. click ⚡ Perfect humanize -> the result panel includes the same
#    live scores for the humanized text, so you can see the drop
```

### Deep humanize (translation chain)

For the strongest structural disruption, tick **Deep humanize** in the UI,
pass `deep: true` in the API, or add `--deep` on the CLI. The rewrite then
routes through a 4-hop translation chain — adapted from the MIT-licensed
[lynote-ai/humanize-text](https://github.com/lynote-ai/humanize-text)
Standard Pipeline — so no single model's fingerprint survives:

```
EN -> 中文 -> 日本語 -> suomi -> EN
```

- Hops 1-2 are LLM *humanization rewrites* on the primary provider
  (Claude / CX); hop 2 carries hop 1 as conversation history.
- Hops 3-4 are *translation* hops on a second engine when configured
  (`OPENAI_CODEX_*`, e.g. the hcnsec gateway with DeepSeek/Kimi models, or
  the CX gateway) — mirroring the upstream cross-engine design; otherwise
  they reuse the primary.

Deep mode is noticeably slower (4 sequential LLM calls) and costs more;
when the chain fails it falls back to the single-pass rewrite, then the
`llm_method` field in the result reports which path ran (`"chain"` /
`"single"`). On a test sample the chain took a 28 → 100 naturalness
sample and returned prose like *"Technology has quietly permeated every
aspect of our lives…"*.

## Plans

The roadmap starts at a free tier and gates the expensive paths behind Pro:

| Feature | Free | Pro |
|---|---|---|
| AI detector + naturalness score | ✓ | ✓ |
| Deterministic humanizer | ✓ 1,000 words/day | ✓ unlimited |
| LLM rewrite (Claude / CX / OpenAI / Gemini / Qwen / router / hcnsec) | — | ✓ |
| Perfect humanize — live feedback loop | — | ✓ |
| Deep humanize (4-hop translation chain) | — | ✓ |
| Batch mode | — | ✓ |
| Full rewrite intensity + run-to-run variety | capped at 50% | ✓ full |
| Sentence-level re-humanize | ✓ | ✓ |
| Plagiarism / similarity check | ✓ | ✓ |

Plan resolution: the `NATURALIZER_PLAN` env var wins; otherwise the plan is
**Pro** when any LLM provider is configured (the tool is yours to run) and
**Free** otherwise. To demo the free experience:

```bash
NATURALIZER_PLAN=free python server.py
```Free-tier daily word usage is tracked in a tiny JSON file under
`NATURALIZER_STATE_DIR` (default `state/`) and resets at midnight; gated
requests return clear 402/429 messages. In this local build the "Upgrade to
Pro" button explains the tiers and how to unlock Pro — a real deployment
would wire payments there.

## Production hardening

The server ships with defensive defaults so it is safe to expose beyond
`127.0.0.1` (or put behind a reverse proxy):

- **Security headers on every response** — `X-Content-Type-Options:
nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
`Permissions-Policy` (geo/mic/camera/payment disabled),
`Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy:
same-origin`. The HTML shell gets a **per-request CSP nonce** (the inline
script is nonce-gated; `frame-ancestors 'none'`, `object-src 'none'`,
`base-uri 'self'`).
- **CORS is not `*`** — `Access-Control-Allow-Origin` is echoed only for
the loopback family (`127.0.0.1` / `localhost` / `[::1]` — any port),
same-host requests, or origins in `ALLOWED_ORIGINS`. CORS preflight
(`OPTIONS`) is answered; cross-origin POSTs from disallowed origins are
rejected with `403` (CSRF guard for state-changing endpoints).
- **Request-body caps** — JSON bodies are capped at `MAX_JSON_BYTES`
(default 2 MiB, `413` when exceeded); uploads at `MAX_UPLOAD_BYTES`
(default 10 MiB).
- **Per-IP rate limiting** on the expensive endpoints (`naturalize`,
`stream`, `perfect`, `upload`, `batch`, `plagiarism`, `detectors/scan`,
`detect`) — sliding 60-second window, `RATE_LIMIT_PER_MIN` (default 120,
`0` disables), `429` with `retry_after` when exhausted.
- **Never a bare connection drop** — any unhandled exception is logged
with a traceback and answered with a JSON `500`.
- **Caching** — HTML is `no-cache`, API responses are `no-store`, the
stylesheet is cached for a day.
- **`robots.txt`** disallows `/api/`; `/favicon.ico` answers `204`
(no favicon file needed).

Tune deployment knobs with env vars: `HOST` (default `127.0.0.1`),
`PORT`, `ALLOWED_ORIGINS` (comma-separated), `MAX_JSON_BYTES`,
`MAX_UPLOAD_BYTES`, `RATE_LIMIT_PER_MIN`, `NATURALIZER_STATE_DIR`.

## Roadmap

- **Basic (Free)** — shipped: detector, deterministic humanizer, 1,000
  words/day, plagiarism check, upload/export.
- **Pro** — shipped (unlock with `NATURALIZER_PLAN=pro` or a configured
  LLM): LLM rewrite across every configured provider, perfect humanize
  (feedback loop), deep translation chain, batch mode, full intensity +
  variety, unlimited words.
- **Next** — per-style benchmark reports (shipped: the Accuracy panel
  shows before→after cards for academic / business / creative / casual),
  user history persistence (shipped: 🕘 History panel + `state/history.json`),
  and the third-party detector comparison endpoints (GPTZero / ZeroGPT /
  Originality.ai APIs) whose keys are already wired into the detectors
  panel, so adding a key to `.env.local` lights that detector up in the
  perfect loop.

## Platform research (how we compare to StealthWriter, WriteHuman, etc.)

`tools/platform_research.py` is the honest version of "scrape 20+ humanizer
sites": those platforms are login-walled commercial services behind
Cloudflare, so mass-scraping them is ToS-violating, fragile, and useless
for improving a rewrite engine — their *techniques* are what matter, and
those are publicly documented. The tool:

```bash
python tools/platform_research.py            # catalog + coverage matrix
python tools/platform_research.py --bench    # + corpus comparison run
python tools/platform_research.py --json out.json
```

- **22-platform catalog** — name, URL, free-tier limit, core technique,
  register behavior, and a source URL for every claim (GPTinf,
  HumanizeAI.pro, Undetectable AI, StealthWriter, WriteHuman, TextToHuman,
  BypassGPT, HIX Bypass, Humbot, Netus AI, Smodin, QuillBot, StealthGPT,
  GPTHuman, Litero AI, Walter Writes, SurferSEO, uPass, Ryne, Humalingo,
  WriteHybrid, Humanize AI Pro).
- **Technique coverage matrix** — maps each platform approach (synonym
  substitution, burstiness, register-preserving clause movement, texture
  asides, verify-until-clean loops, style profiles, multi-engine rotation)
  to the exact pass in this engine that implements it.
- **Standardized comparison** — identical corpus inputs through our
  deterministic + perfect-loop paths, scored against the same floor gate
  as `detector_bench.py` (currently 11/11 AI samples pass, human corpus
  untouched).

Research findings synthesized into the engine this round: a new
**register-preserving clause-fronting pass** (`X because Y` → `Because Y,
X` at high intensity, gated so formal text stays formal and natural prose
is never restructured) and LLM-prompt guidance to vary grammatical
architecture — the structural school GPTinf / HumanizeAI.pro / Undetectable
AI share.

## Live comparison vs other humanizers (the honest "best of all sites")

`tools/competitor_compare.py` runs the *same input* through every available
humanizer, scores each output with the shared floor gate (naturalness
score + plain register + fact preservation vs. the original), ranks them,
and writes the single best version with full provenance:

```bash
python tools/competitor_compare.py --input state/probe_input.txt
python tools/competitor_compare.py --input state/probe_input.txt --scrape  # + live sites
python tools/competitor_compare.py --input in.txt --llm --style academic    # + LLM perfect loop
```

- **Always-on providers** — `local-deterministic` (dependency-free engine)
  and `local-perfect-loop` (feedback loop). The comparison never depends
  on third parties.
- **Key-gated API providers** — StealthGPT and Undetectable AI are wired
  through their official APIs and light up the moment
  `STEALTHGPT_API_KEY` / `UNDETECTABLE_API_KEY` lands in `.env.local`;
  until then they are reported as `blocked: not_configured` — never
  silently skipped, exactly like the GPTZero row in the detector panel.
- **Live site scraping (`--scrape`)** — drives a real headless-Chrome
  browser against the free-tier sites' own editors via
  `tools/humanizer_site_probe.mjs` (TextToHuman et al.). The probe is
  honest about bot-gates: every major site sits behind Cloudflare /
  Turnstile / login walls, so captures are best-effort and each blocked
  site is reported with its reason (see `state/site_probe/`).
- **Explainable selection** — the winner is the candidate that clears the
  floor with the highest naturalness, then plainest register, then fewest
  lost facts; each row carries its `reason`. Outputs:
  `state/compare_report.json` (full ranked report) and
  `state/best_humanized.txt` (the winning version).
- **The site can do it too** — `POST /api/compare` runs the comparison
  server-side (own engines + key-gated APIs; scraping stays CLI-only)
  and returns `{best, candidates, blocked}`. The web UI's **⚖ Compare**
  button (next to ⚡ Perfect humanize) renders the ranked table and lets
  you click **Use** to load any candidate into the After pane.

### Fact critics + best-of-N selection (agency-level quality filtering)

The advanced-humanizer playbook — generate several candidates, keep the
best *surviving* one — is implemented in `naturalizer/critics.py`:

- **Factual preservation gates** — `preservation_issues(original,
candidate)` flags every dropped number (hard gate: a rewrite that loses
"42%" or "800 words" is rejected no matter how fluent it reads), plus
negation flips and lost proper nouns (strong warnings).
- **Best-of-N ranking** — `rank_candidates` picks the highest-naturalness
candidate that survives the gates. `rewrite_with_llm(best_of=N)` draws N
candidates with rotated voices, then keeps the best faithful one.
- **Perfect-loop fact feedback** — when a pass drops a number, the next
pass gets a "restore the number(s) …" instruction, and the final result
reports `fact_issues` so the UI warns "double-check the figures".

### Transformed-human robustness (no false positives on edited human text)

A detector that flags *professionally edited* human writing is unusable —
that is exactly what real people submit. The H1-H4 false-positive battery
is in `tests/test_robustness.py` and `tools/robustness_check.py`:

- H1 human professionally edited (comma/run-on fixes, editor synonym swaps)
- H2 human grammar-corrected (spelling normalization, article fixes)
- H3 human style-transferred (formalized register, un-contracted)
- H4 human run through our own perfect loop (must not flip to AI)

Current result: **75/75 checks clean, 0 false positives** — edited human
prose stays human.

## Tests

```bash
python -m unittest discover -s tests -v
```

401 tests covering detection (including the corpus — 11 AI-heavy samples
that must score low, 35 human-written samples that must score high and
survive the rewrite untouched — 6 of them authored student essays — plus
6 real live-LLM samples held separately as the external honesty check,
and a guarantee that naturalizing any AI-heavy sample brings its score
to 75+), the windowed passage layer
(neighbour-smoothed labels, contiguous AI-region detection, abstention on
short/list-dominated samples), the factual critics (number/negation/entity
preservation, best-of-N ranking, faithful-beats-fluent selection), the
transformed-human robustness battery (H1-H4: professionally edited,
grammar-corrected, style-transferred, and humanizer-round-tripped human
text must never false-positive as AI), the verified human-writing memory
(the everyday-vocabulary model scores the human corpus 1.0 and stiff
Latinate prose ~0.45; every one of the 1256 plain-register swaps is
asserted to have zero hits on the human corpus; the plain-language pass
demotes ``utilize -> use`` / ``ascertain -> find out`` while leaving all
35 verified human paragraphs byte-identical — the corpus now spans
emails, how-to instructions, personal notes, and student essays; the
same memory is
handed to the LLM path as plain-register guidance naming the draft's own
complex words), transforms (filler removal, opener variation, rhythm work, em-dash
softening, register-preserving clause fronting), the word-level diff,
engine orchestration, HTTP endpoints,
text extraction (handcrafted TXT / DOCX / PDF fixtures, including Flate-
and ASCIIHex-compressed streams), export round-trips (every rendered file
extracts back to the original text), the upload/export endpoints
(multipart parsing, size caps, download content types), the command-
line pipeline (format conversion, JSON output, overwrite guard, failures),
the LLM providers (Claude native vs OpenAI-compatible protocols with
auto-fallback, CX gateway paths, all eight providers' config + ordering,
the feedback-loop instruction hook, auth headers), the translation chain
(hop order, cross-engine secondary, conversation history, failure
fallbacks — all with mocked HTTP, so the suite never touches the network
or real keys), the live feedback loop (per-pass scores, pass cap,
convergence, remaining-issue instructions, detector status panel), and
the plagiarism checker (verbatim-high vs paraphrase-low
scoring, short-query shingle shrinking, per-source breakdown, empty-input
and missing-reference handling), the detection-signals metrics (key
presence, length gating on every signal, the syntactic-signal improvement
after a rewrite), the post-rewrite verification re-scan in the engine
(before/after metrics + verification score in every result), Markdown
upload extraction, the accuracy benchmark (report structure + corpus
floors), and the free/pro plan gating (LLM/deep/batch blocked on Free,
word-cap accounting, intensity cap, explicit error codes).

## Project layout

```
naturalizer/
  detectors.py   # AI-tell detection + naturalness scoring + signals metrics
  critics.py     # factual preservation gates + best-of-N candidate ranking
  transforms.py  # deterministic, semantics-preserving rewrites (incl. intensity
                 #   + synonym/contraction variety passes)
  diff.py        # word-level LCS diff for before/after highlighting
  extract.py     # TXT / DOCX / PDF text extraction (pure stdlib)
  export.py      # TXT / DOCX / PDF writers for the rewritten file
  cli.py         # python -m naturalizer.cli — headless file pipeline
  styles.py      # academic / business / creative / casual profiles
  llm.py         # optional LLM backend: Claude / CX / OpenAI / Gemini / Qwen /
                 #   agent router / hcnsec gateway (all OpenAI-compatible)
  feedback.py    # perfect humanize: live rewrite→re-scan→fix feedback loop
  chain.py       # deep humanize: 4-hop translation chain (EN->中文->日本語->suomi->EN)
  plagiarism.py  # local n-gram similarity check against user-provided refs
  benchmark.py   # detector accuracy + humanizer lift over the labeled corpus
  plans.py       # free/pro plan structure + daily word-cap accounting
  envfile.py     # .env.local / .env loader (pure stdlib)
  engine.py      # orchestrates scoring + rewriting, batch support
server.py        # stdlib HTTP server (UI + JSON API)
start.bat        # Windows double-click launcher (find Python, free port,
                 #   start server, open browser)
templates/       # web UI
static/          # stylesheet
tests/           # unittest suite + corpus
tools/           # ui_check.mjs — headless-browser UI verification driver
                 # detector_bench.py — live realistic-prose benchmark
                 # platform_research.py — 22-platform technique catalog
                 # competitor_compare.py — live multi-provider comparison
                 # humanizer_site_probe.mjs — free-tier site scrape driver
                 # robustness_check.py — transformed-human false-positive battery
```

## Scope notes

- Rewrites preserve facts and meaning; the deterministic engine never
  invents content. Use it as a polish pass, not a content generator.
- The detector is a heuristic, not a detector of any specific platform.
  Scores reflect writing style only.
