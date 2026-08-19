#!/usr/bin/env python3
"""Platform research — the honest replacement for mass-scraping.

Every serious AI-humanizer site (StealthWriter, WriteHuman, Undetectable AI,
GPTinf, HumanizeAI.pro, ...) is a login-walled commercial service behind
Cloudflare. Mass-scraping them with rotating proxies is ToS-violating,
fragile (they change UI weekly), and — crucially — useless for improving a
rewrite engine: their *techniques* are what matter, and those are publicly
documented. This module does the honest version of that research:

  1. A grounded catalog of 20+ platforms (name, URL, free tier, core
     technique, detectors claimed, register behavior, source).
  2. A **technique coverage matrix** — for each platform technique, which
     pass in *this* engine already implements it (or why we deliberately
     don't).
  3. A **standardized comparison harness** — identical corpus inputs scored
     by our engine (deterministic + perfect-loop paths), so "how do we
     compare" is measured, not vibes.

Run it with:

    python tools/platform_research.py              # catalog + coverage report
    python tools/platform_research.py --bench      # + run the corpus comparison
    python tools/platform_research.py --json out.json

No network requests are made: every claim below carries its source URL so
the catalog can be audited and re-verified. If you want to compare against
a *specific* platform's live output, paste their output into the site's
editor yourself and run `python tools/detector_bench.py --methods det,perfect`
— that scores any text with the same floor gate used here.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

# Windows consoles default to cp1252; force UTF-8 so emoji in reports print.
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover - non-console
    pass

# Make `naturalizer` importable whether run from repo root or tools/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Catalog. Fields:
#   url, free_tier, technique (what the platform actually does), detectors
#   (which detectors it claims to pass), register (preserves formal register
#   or pushes casual), source (where the claim comes from), notes.
# ---------------------------------------------------------------------------
PLATFORMS: List[Dict[str, str]] = [
    {
        "name": "GPTinf",
        "url": "https://gptinf.com",
        "free_tier": "120 words/run (free tier)",
        "technique": "Non-LLM clause-level transformation engine: changes grammatical "
                     "structure at the clause level (clause order, verb patterns, subject "
                     "position) instead of synonym swapping; trained against GPTZero / "
                     "Originality.ai signals; 8 rewrite modes; freeze-keywords + selective "
                     "rephrase.",
        "detectors": "GPTZero, Originality.ai, Turnitin",
        "register": "preserves formal register",
        "source": "https://www.310creative.com/blog/best-ai-humanizer-tools",
        "notes": "The clearest articulation of the 'structural, not lexical' school — "
                 "the same core engine reportedly shared with HumanizeAI.pro and "
                 "Undetectable AI.",
    },
    {
        "name": "HumanizeAI.pro",
        "url": "https://humanizeai.pro",
        "free_tier": "400 words/run, no daily cap, no account",
        "technique": "Clause-level sentence re-architecture that keeps the original "
                     "register (formal stays formal); 'Ultra-run' adds a second structural "
                     "pass; auto-runs its detector after every humanization.",
        "detectors": "GPTZero, Turnitin, ZeroGPT, Originality.ai",
        "register": "preserves formal register",
        "source": "https://www.310creative.com/blog/best-ai-humanizer-tools",
        "notes": "Register preservation is the differentiator — most cheap tools push "
                 "text toward casual to escape detection, which wrecks quality.",
    },
    {
        "name": "Undetectable AI",
        "url": "https://undetectable.ai",
        "free_tier": "250 words (one-time trial)",
        "technique": "Grammatical-level restructuring: clause order, verb patterns, "
                     "subject position; readability level selector (high school → "
                     "doctorate) and purpose selector; built-in before/after detector.",
        "detectors": "GPTZero, Turnitin, Originality.ai, Copyleaks",
        "register": "register adjustable via readability selector",
        "source": "https://www.310creative.com/blog/best-ai-humanizer-tools",
        "notes": "Readability selector is effectively the same knob as our style "
                 "profiles (Academic vs Casual vs Business).",
    },
    {
        "name": "StealthWriter",
        "url": "https://stealthwriter.ai",
        "free_tier": "10 humanizations/day, 1,000 words/input, 10 AI scans/day",
        "technique": "Sentence-level rewrite with 'Ghost' models (Mini free / Pro paid), "
                     "10 intensity levels, 8 style presets (academic, formal, simple, "
                     "casual, university professor/student, high school student). "
                     "Never adds/removes sentences — only rewrites in place, and at "
                     "higher levels deliberately introduces human imperfections "
                     "(dropped apostrophes, odd phrasing) to fool weak detectors. "
                     "'Humanize More' re-rewrites only the sentences its own detector "
                     "still flags; 'Rehumanize' re-runs the whole pass; selective "
                     "sentence alternatives offered.",
        "detectors": "own detector (V2, modes easy/normal/strict, sentence-level "
                     "color-coded), GPTZero, Originality.ai, Copyleaks, QuillBot",
        "register": "pushes casual / imperfect at high levels (readability drops)",
        "source": "https://stealthwriter.ai/ and https://gptzero.me/news/stealthwriter-ai-review/",
        "notes": "Independent GPTZero review (gptzero.me): StealthWriter bypassed "
                 "Originality.ai, Copyleaks, and QuillBot, but GPTZero still scored "
                 "its output 100% AI — the imperfection trick fools weak detectors, "
                 "not strong ones, at the cost of real readability. We deliberately "
                 "do NOT inject grammar errors (fact critics + no-mangle invariant); "
                 "our ⚡ Perfect loop is their 'Humanize More' generalized: iterate "
                 "on flagged sentences until the floor gate passes, without "
                 "degrading prose. Their level knob ≈ our intensity (0-1); their "
                 "sentence alternatives ≈ our per-sentence diff view.",
    },
    {
        "name": "WriteHuman",
        "url": "https://writehuman.ai",
        "free_tier": "200 words/check",
        "technique": "LLM-based rewrite tuned against Copyleaks / ZeroGPT / GPTZero "
                     "signals; claims human-quality output while keeping meaning.",
        "detectors": "Copyleaks, ZeroGPT, GPTZero",
        "register": "mixed",
        "source": "https://writehuman.ai/",
        "notes": "Standard LLM-rewrite school — matches our LLM path (single-pass + "
                 "perfect loop).",
    },
    {
        "name": "TextToHuman",
        "url": "https://texttohuman.com",
        "free_tier": "Unlimited, no signup",
        "technique": "'Autopilot' multi-pass refinement that reworks phrasing until "
                     "detectability scores land; 'Smart Alternatives' per-sentence rewrites "
                     "scored for detectability; 25+ languages.",
        "detectors": "GPTZero, Turnitin, ZeroGPT",
        "register": "mixed",
        "source": "https://www.310creative.com/blog/best-ai-humanizer-tools",
        "notes": "Autopilot = our perfect loop (feedback-until-clean). We already do this "
                 "in the ⚡ Perfect path.",
    },
    {
        "name": "Humanize AI Pro",
        "url": "https://thehumanizeai.pro",
        "free_tier": "Unlimited, no signup (claimed)",
        "technique": "Marketing-leading claims (99.8% bypass, 30+ languages); the "
                     "comparison page is itself the source, so treat numbers as "
                     "advertising, not independent data.",
        "detectors": "Turnitin, GPTZero (claimed)",
        "register": "n/a (marketing)",
        "source": "https://thehumanizeai.pro/articles/best-ai-humanizer-comparison-table-2026",
        "notes": "Self-reported numbers on an affiliate page — useful for the comparison "
                 "table only, not as ground truth.",
    },
    {
        "name": "BypassGPT",
        "url": "https://bypassgpt.ai",
        "free_tier": "300 words/check",
        "technique": "LLM rewrite school; claims to remove 'AI fingerprints' while "
                     "keeping facts; several output modes.",
        "detectors": "GPTZero, Turnitin, Copyleaks",
        "register": "mixed",
        "source": "https://thehumanizeai.pro/articles/best-ai-humanizer-comparison-table-2026",
        "notes": "Same school as WriteHuman.",
    },
    {
        "name": "HIX Bypass",
        "url": "https://hix.ai",
        "free_tier": "None (paid)",
        "technique": "Part of the HIX.AI suite; multi-pass humanization with "
                     "tone/format options.",
        "detectors": "GPTZero, Turnitin, Originality.ai",
        "register": "adjustable",
        "source": "https://thehumanizeai.pro/articles/best-ai-humanizer-comparison-table-2026",
        "notes": "Suite bundler — humanizer is one tab among writer/chat tools.",
    },
    {
        "name": "Humbot",
        "url": "https://humbot.ai",
        "free_tier": "500 words total",
        "technique": "Sentence-level rewrite with 'human tone' target; detector "
                     "compatibility matrix on site.",
        "detectors": "GPTZero, Turnitin, Copyleaks",
        "register": "mixed",
        "source": "https://thehumanizeai.pro/articles/best-ai-humanizer-comparison-table-2026",
        "notes": "",
    },
    {
        "name": "Netus AI",
        "url": "https://netus.ai",
        "free_tier": "None (paid)",
        "technique": "Paraphrase + humanize with anti-detection focus; API-first; "
                     "claims to rewrite at the semantic level.",
        "detectors": "GPTZero, Turnitin, Originality.ai",
        "register": "preserves",
        "source": "https://thehumanizeai.pro/articles/best-ai-humanizer-comparison-table-2026",
        "notes": "API-first — closest to a programmatic comparison target, but paid.",
    },
    {
        "name": "Smodin Humanizer",
        "url": "https://smodin.io",
        "free_tier": "500 words/day",
        "technique": "LLM rewrite inside the Smodin writing suite; multi-language.",
        "detectors": "GPTZero, Turnitin",
        "register": "mixed",
        "source": "https://thehumanizeai.pro/articles/best-ai-humanizer-comparison-table-2026",
        "notes": "",
    },
    {
        "name": "QuillBot (paraphraser)",
        "url": "https://quillbot.com",
        "free_tier": "125 words/paste",
        "technique": "Not a dedicated humanizer — general paraphraser with modes "
                     "(Standard/Fluent/Formal/...). Frequently searched as a humanizer; "
                     "weakest bypass of the list.",
        "detectors": "n/a",
        "register": "mode-dependent",
        "source": "https://thehumanizeai.pro/articles/best-ai-humanizer-comparison-table-2026",
        "notes": "Included because users search it as a humanizer; it isn't one.",
    },
    {
        "name": "StealthGPT",
        "url": "https://www.stealthgpt.ai",
        "free_tier": "Trial",
        "technique": "LLM rewrite tuned for bypass; claims stealth across detectors.",
        "detectors": "GPTZero, Turnitin",
        "register": "mixed",
        "source": "https://www.stealthgpt.ai/",
        "notes": "Often confused with StealthWriter; separate product.",
    },
    {
        "name": "GPTHuman.ai",
        "url": "https://gpthuman.ai",
        "free_tier": "Claims 120,000 words",
        "technique": "LLM rewrite claiming the highest free volume; positions on "
                     "bypassing 'all premium detectors'.",
        "detectors": "GPTZero, Turnitin, Originality.ai",
        "register": "mixed",
        "source": "https://medium.com/freelancers-hub/i-tried-7-ai-humanizers-heres-the-best-tool-to-bypass-ai-detectors-628590da5ccf",
        "notes": "High-volume free tier is its differentiator.",
    },
    {
        "name": "Literal AI",
        "url": "https://literal.ai",
        "free_tier": "Monthly word cap",
        "technique": "Student-focused; context-aware rewrite that preserves technical "
                     "terminology; 3 strength levels (light/balanced/aggressive).",
        "detectors": "GPTZero, Turnitin, Originality.ai",
        "register": "preserves academic",
        "source": "https://www.310creative.com/blog/best-ai-humanizer-tools",
        "notes": "Strength levels ≈ our intensity knob.",
    },
    {
        "name": "HumanizerPro",
        "url": "https://humanizerpro.ai",
        "free_tier": "Trial",
        "technique": "LLM rewrite school; review blog of others (their StealthWriter "
                     "review is a useful second source on that product).",
        "detectors": "GPTZero, Turnitin",
        "register": "mixed",
        "source": "https://humanizerpro.ai/blog/stealth-writer",
        "notes": "",
    },
    {
        "name": "Walter Writes AI",
        "url": "https://walterwritesai.com",
        "free_tier": "Trial",
        "technique": "LLM rewrite positioned on consistency across content types; "
                     "popular in reddit testing threads.",
        "detectors": "GPTZero, Originality.ai",
        "register": "mixed",
        "source": "https://www.reddit.com/r/BypassAiDetect/comments/1m8y0sk/best_ai_humanizer_tools_of_2025_tested_against/",
        "notes": "Reddit-tested — community data, not vendor claims.",
    },
    {
        "name": "SurferSEO AI Humanizer",
        "url": "https://surferseo.com",
        "free_tier": "None (paid)",
        "technique": "SEO-focused humanizer inside Surfer's content editor; tuned for "
                     "readability + detection.",
        "detectors": "GPTZero, Originality.ai",
        "register": "mixed",
        "source": "https://www.reddit.com/r/BypassAiDetect/comments/1m8y0sk/best_ai_humanizer_tools_of_2025_tested_against/",
        "notes": "For SEO writers; couples humanization with SERP optimization.",
    },
    {
        "name": "uPass AI",
        "url": "https://upass.ai",
        "free_tier": "Trial",
        "technique": "LLM rewrite; community-tested in the same reddit roundup.",
        "detectors": "GPTZero, Turnitin",
        "register": "mixed",
        "source": "https://www.reddit.com/r/BypassAiDetect/comments/1m8y0sk/best_ai_humanizer_tools_of_2025_tested_against/",
        "notes": "",
    },
    {
        "name": "Ryne",
        "url": "https://ryne.ai",
        "free_tier": "Trial",
        "technique": "Humanizer inside a full writing suite; tested in the 7-tool "
                     "roundup.",
        "detectors": "GPTZero, Turnitin",
        "register": "mixed",
        "source": "https://ryne.ai/blog/what-is-the-best-ai-humanizer-7-tools-put-to-the-test",
        "notes": "",
    },
    {
        "name": "Humalingo",
        "url": "https://humalingo.com",
        "free_tier": "Free tier",
        "technique": "LLM rewrite with built-in detector; reviewed as a top pick in "
                     "long-form testing.",
        "detectors": "GPTZero, Copyleaks",
        "register": "mixed",
        "source": "https://anangsha.substack.com/p/i-tried-30-ai-humanizers-here-are",
        "notes": "Built-in detector after every run = same as our post-rewrite "
                 "verification.",
    },
    {
        "name": "WriteHybrid",
        "url": "https://www.writehybrid.com",
        "free_tier": "Recurring free tier",
        "technique": "Free-tier-focused humanizer; positioned in the free-tier "
                     "comparison.",
        "detectors": "GPTZero, Turnitin",
        "register": "mixed",
        "source": "https://www.writehybrid.com/best/free-ai-humanizers",
        "notes": "",
    },
]

# How each platform *approach* maps onto this engine's passes. Left column =
# technique family; right = the naturalizer passes that implement it.
_TECHNIQUE_MAP: List[Tuple[str, str, str]] = [
    (
        "Synonym / word-level substitution",
        "vary_synonyms, cut_filler (257 tell patterns)",
        "Implemented — word-choice diversity + filler removal.",
    ),
    (
        "Sentence-length burstiness (merge/split)",
        "diversify_rhythm, vary_length, _force_rhythm_variety",
        "Implemented — the flat-rhythm tell is handled probabilistically and "
        "deterministically.",
    ),
    (
        "Register-preserving clause movement",
        "vary_openers + new clause-fronting pass",
        "Implemented this round — subordinate-clause fronting that keeps formal "
        "formal (the GPTinf / HumanizeAI.pro core).",
    ),
    (
        "Texture / discourse asides",
        "add_human_texture",
        "Implemented — capped natural asides at high intensity.",
    ),
    (
        "Em-dash and punctuation normalization",
        "soften_emdash + density-based detector",
        "Implemented — em-dashes converted to commas/parens; density is a detector "
        "signal.",
    ),
    (
        "Antithesis / balanced-scaffold removal",
        "_check_antithesis + LLM prompt ban",
        "Implemented — 'not X but Y' scaffolds flagged and banned in LLM output.",
    ),
    (
        "Multi-pass verify-until-clean (TextToHuman Autopilot)",
        "feedback.py perfect loop",
        "Implemented — the ⚡ Perfect path re-scans and re-rewrites until the floor "
        "is cleared.",
    ),
    (
        "Style / readability profile selection",
        "styles.py (Academic / Casual / Business / Creative)",
        "Implemented — the Undetectable-AI readability selector equivalent.",
    ),
    (
        "Multi-engine rotation (no single fingerprint)",
        "5 seeded voices + Claude/CX/OpenAI/Gemini fallback chain",
        "Implemented — this round added voice rotation.",
    ),
    (
        "Detector-specific training",
        "N/A (deliberate)",
        "Not implemented on purpose: we do not train against any closed detector's "
        "weights (ToS + they change weekly). We score with our own transparent "
        "signals instead.",
    ),
]


def _coverage_matrix() -> List[Dict[str, str]]:
    rows = []
    for approach, engine_side, verdict in _TECHNIQUE_MAP:
        rows.append(
            {"approach": approach, "engine": engine_side, "verdict": verdict}
        )
    return rows


def _catalog_md() -> str:
    lines = [
        "# AI humanizer platform catalog",
        "",
        f"**{len(PLATFORMS)} platforms cataloged.** Every claim carries its source URL;",
        "none of the marketing numbers here are treated as ground truth. The point of",
        "the catalog is the *technique* column — what each platform actually does —",
        "because that is what a rewrite engine can learn from.",
        "",
        "| Platform | Free tier | Technique (what it does) | Register | Source |",
        "|---|---|---|---|---|",
    ]
    for p in PLATFORMS:
        tech = p["technique"].split(";")[0]
        lines.append(
            f"| [{p['name']}]({p['url']}) | {p['free_tier']} | {tech} | "
            f"{p['register']} | [src]({p['source']}) |"
        )
    lines.append("")
    return "\n".join(lines)


def _coverage_md() -> str:
    lines = [
        "## Technique coverage — how this engine implements each platform approach",
        "",
        "| Platform approach | Our engine | Verdict |",
        "|---|---|---|",
    ]
    for approach, engine_side, verdict in _TECHNIQUE_MAP:
        lines.append(f"| {approach} | `{engine_side}` | {verdict} |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Standardized comparison harness: identical corpus inputs through our engine.
# ---------------------------------------------------------------------------
def _load_corpus() -> Tuple[List[str], List[str]]:
    ai = open(
        os.path.join("tests", "corpus", "ai_samples.txt"), encoding="utf-8"
    ).read()
    hum = open(
        os.path.join("tests", "corpus", "human_samples.txt"), encoding="utf-8"
    ).read()
    ai_paras = [p.strip() for p in re.split(r"\n\s*\n", ai) if len(p.strip()) > 80]
    hum_paras = [p.strip() for p in re.split(r"\n\s*\n", hum) if len(p.strip()) > 80]
    return ai_paras, hum_paras


def _bench(floor: float = 75.0) -> Dict:
    """Score the corpus through both engine paths, same floor gate as
    tools/detector_bench.py."""
    from naturalizer.detectors import analyze
    from naturalizer.transforms import rewrite as det_rewrite

    ai_paras, hum_paras = _load_corpus()

    det_pass, det_fail = 0, 0
    perfect_attempts, perfect_pass, perfect_skip = 0, 0, 0
    human_touched = 0
    examples: List[Dict] = []

    for p in ai_paras:
        out, _, _ = det_rewrite(p, intensity=0.5)
        score = analyze(out).score if out else 0
        det_pass += 1 if score >= floor else 0
        det_fail += 0 if score >= floor else 1
        examples.append(
            {"sample": p[:60], "path": "deterministic", "score": score}
        )
        # Perfect loop (feedback) — runs on the deterministic engine too; the
        # LLM path only when a provider is configured.
        try:
            from naturalizer.engine import Naturalizer
            from naturalizer.feedback import feedback_humanize

            result = feedback_humanize(
                Naturalizer(), p, style="academic", max_passes=3, use_llm=False
            )
            final_score = result["scores"][-1] if result.get("scores") else None
            if final_score is not None:
                perfect_attempts += 1
                ok = final_score >= floor
                perfect_pass += 1 if ok else 0
                examples.append(
                    {"sample": p[:60], "path": "perfect", "score": final_score}
                )
        except Exception:
            perfect_skip += 1

    for p in hum_paras:
        before = analyze(p).score
        out, _, _ = det_rewrite(p, intensity=0.5)
        after = analyze(out).score if out else 0
        if before < floor or after < floor:
            human_touched += 1

    return {
        "ai_samples": len(ai_paras),
        "human_samples": len(hum_paras),
        "deterministic_pass": det_pass,
        "deterministic_fail": det_fail,
        "perfect_attempts": perfect_attempts,
        "perfect_pass": perfect_pass,
        "perfect_skipped": perfect_skip,
        "human_touched_below_floor": human_touched,
        "floor": floor,
        "examples": examples,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bench", action="store_true", help="run the corpus comparison")
    ap.add_argument("--json", default=None, help="write full JSON report to FILE")
    args = ap.parse_args()

    report = {
        "catalog": PLATFORMS,
        "coverage": _coverage_matrix(),
    }
    if args.bench:
        report["bench"] = _bench()

    out = _catalog_md() + _coverage_md()
    print(out)
    if args.bench:
        b = report["bench"]
        print(
            "## Standardized comparison (our engine, corpus-identical inputs)\n"
        )
        print(
            f"- AI samples: {b['ai_samples']} | deterministic pass "
            f"{b['deterministic_pass']}/{b['ai_samples']} (floor {b['floor']})"
        )
        print(
            f"- Perfect loop: {b['perfect_pass']}/{b['perfect_attempts']} "
            f"attempts, {b['perfect_skipped']} skipped (deterministic engine)"
        )
        print(
            f"- Human corpus untouched: {b['human_touched_below_floor']} "
            f"paragraphs below floor"
        )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
