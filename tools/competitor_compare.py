#!/usr/bin/env python3
"""Live multi-provider humanizer comparison — find the best humanized
version of one input by running it through every available engine.

The same input goes through Naturalizer's own engines plus, when keys are
configured, the official StealthGPT / Undetectable AI APIs; with --scrape it
also drives a real headless-Chrome browser against the free-tier sites'
own editors (tools/humanizer_site_probe.mjs) and captures their output.
Every candidate is scored with the shared floor gate (naturalness score +
plain register + fact preservation vs. the original), ranked, and the single
best version is written to disk with full provenance.

Usage:

    python tools/competitor_compare.py --input state/probe_input.txt
    python tools/competitor_compare.py --input state/probe_input.txt --scrape
    python tools/competitor_compare.py --input in.txt --llm --style academic

Outputs:

    state/compare_report.json   full ranked report
    state/best_humanized.txt    the winning version (with provider note)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from naturalizer.compare import run_comparison  # noqa: E402
from naturalizer.envfile import load_envfile  # noqa: E402

PROBE_SITES = ("https://texttohuman.com",)


def _run_probe(input_file: str, probe_dir: str) -> int:
    """Drive the headless-Chrome probe against the free-tier sites and
    return its exit code. Best-effort: blocked sites are reported by the
    probe itself, not here."""
    cmd = ["node", "tools/humanizer_site_probe.mjs", input_file, probe_dir, *PROBE_SITES]
    try:
        return subprocess.run(cmd, timeout=360).returncode  # noqa: PLW1510
    except (OSError, subprocess.TimeoutExpired) as err:
        print(f"  scrape unavailable: {err}")
        return 1


def _md_table(report: Dict) -> str:
    lines = [
        "| # | Provider | Method | Score | Plain | Facts lost | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in report["candidates"]:
        verdict = "**BEST**" if c["best"] else ("pass" if c["score"] >= report["floor"] else "below floor")
        lost = c["fact_lost"]
        lost_txt = "—" if lost == 0 else str(lost)
        label = c.get("label") or c["provider"]
        lines.append(
            f"| {c['rank']} | {label} | {c.get('method', '—')} | {c['score']} | "
            f"{c['plain']} | {lost_txt} | {verdict} |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="input text file (plain UTF-8)")
    ap.add_argument("--style", default="academic", help="style profile (default: academic)")
    ap.add_argument("--floor", type=float, default=75.0, help="human-like floor (default: 75)")
    ap.add_argument("--llm", action="store_true", help="allow the perfect loop to use LLM providers")
    ap.add_argument("--scrape", action="store_true", help="also scrape free-tier sites via headless Chrome")
    ap.add_argument("--json", default="state/compare_report.json", help="JSON report output")
    ap.add_argument("--out", default="state/best_humanized.txt", help="best-version text output")
    args = ap.parse_args()

    load_envfile()  # entry point — keys for API providers come from .env.local

    input_file = Path(args.input)
    if not input_file.is_file():
        print(f"input not found: {input_file}")
        return 2
    text = input_file.read_text(encoding="utf-8").strip()
    if not text:
        print("input file is empty")
        return 2

    probe_dir = None
    if args.scrape:
        probe_dir = tempfile.mkdtemp(prefix="compare-probe-")
        print(f"→ scraping {', '.join(PROBE_SITES)} with headless Chrome…")
        _run_probe(str(input_file), probe_dir)

    report = run_comparison(
        text,
        style=args.style,
        floor=args.floor,
        use_llm=args.llm,
        probe_dir=probe_dir,
    )

    print("\n" + _md_table(report))
    if report["blocked"]:
        print("\n**Not available this run (honest):**")
        for b in report["blocked"]:
            print(f"- {b.get('label', b['provider'])}: {b.get('note', b['blocked'])}")

    best = report["best"]
    if best:
        print(
            f"\n🏆 Best: {best.get('label', best['provider'])} "
            f"(score {best['score']}, {best['reason']})"
        )
    else:
        print("\nNo candidate produced output.")

    out_json = Path(args.json)
    out_txt = Path(args.out)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if best:
        header = (
            f"# Best humanized version — {best.get('label', best['provider'])}\n"
            f"# {best['reason']}\n\n"
        )
        out_txt.write_text(header + best["text"] + "\n", encoding="utf-8")
    print(f"\nReport: {out_json}\nBest text: {out_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
