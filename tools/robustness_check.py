#!/usr/bin/env python
"""Transformed-human robustness battery (H1-H4 false-positive classes).

A detector is only as good as its false-positive rate on *edited* human
writing. This tool applies the transformation classes the detection
literature demands — human professionally edited, human grammar-corrected,
human style-transferred, and human run through our own humanizer — to real
human-corpus samples, then reports whether the detector wrongly flips any
of them to "AI".

Usage:
    python tools/robustness_check.py            # run the full battery
    python tools/robustness_check.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.detectors import analyze, sentence_distribution
from naturalizer.engine import Naturalizer
from naturalizer.feedback import feedback_humanize

CORPUS = Path(__file__).resolve().parent.parent / "tests" / "corpus" / "human_samples.txt"

#: Soft floor: edited human text must keep scoring here or above. Real
#: detectors abstain / flag low-confidence on short text, so a small dip is
#: tolerated — what must never happen is a flip to the AI verdict.
SCORE_FLOOR = 70


def load_human_samples() -> list[str]:
    text = CORPUS.read_text(encoding="utf-8")
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def check(sample: str, edited: str) -> dict:
    """Score an edited human sample; report any false-positive signal."""
    report = analyze(edited)
    dist = sentence_distribution(edited)
    return {
        "score": report.score,
        "high_issues": [i.to_dict() for i in report.issues if i.severity == "high"],
        "ai_regions": dist["regions"],
        "flipped": (
            report.score < SCORE_FLOOR
            or any(i.severity == "high" for i in report.issues)
            or bool(dist["regions"])
        ),
    }


def edit_variants(sample: str) -> dict[str, str]:
    """The H-class edits for one sample ('' = same as input)."""
    out: dict[str, str] = {}
    import re as _re

    parts = _re.split(r"(?<=[.!?])\s+(?=[A-Z])", sample)
    if len(parts) >= 3:
        out["merge_sentences"] = (
            parts[0] + ", " + parts[1][0].lower() + parts[1][1:] + ". " + " ".join(parts[2:])
        )
    else:
        out["merge_sentences"] = sample

    formal = sample.replace(" shut ", " turned off ").replace(" went home", " headed home")
    out["formalized"] = formal if formal != sample else sample

    uncontracted = sample.replace("it's", "it is").replace("didn't", "did not")
    out["uncontracted"] = uncontracted if uncontracted != sample else sample

    out["original"] = sample
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None, help="write full JSON report")
    args = ap.parse_args()

    samples = load_human_samples()
    engine = Naturalizer(seed=0, prefer_llm=False)
    rows: list[dict] = []
    flipped_total = 0
    checks_total = 0

    print(f"Transformed-human robustness battery — {len(samples)} human samples\n")
    for i, sample in enumerate(samples, 1):
        variants = edit_variants(sample)
        # H4: the humanizer's own perfect loop must not flip human text.
        perfect = feedback_humanize(engine, sample, style="academic", max_passes=2)
        variants["perfect_loop"] = perfect["text"]

        row: dict = {"sample": i, "variants": {}}
        for name, edited in variants.items():
            result = check(sample, edited)
            checks_total += 1
            if result["flipped"]:
                flipped_total += 1
            row["variants"][name] = {
                "score": result["score"],
                "flipped": result["flipped"],
                "high_issues": len(result["high_issues"]),
                "regions": len(result["ai_regions"]),
            }
        rows.append(row)

        status = "OK " if not any(v["flipped"] for v in row["variants"].values()) else "FAIL"
        print(f"  [{i}/{len(samples)}] {status} sample {i}")
        for name, v in row["variants"].items():
            mark = "!" if v["flipped"] else " "
            print(f"      {mark} {name:<16} score={v['score']:>3}  "
                  f"high={v['high_issues']} regions={v['regions']}")

    rate = (1 - flipped_total / checks_total) * 100 if checks_total else 100.0
    print(f"\n=== {checks_total} checks, {flipped_total} false positives "
          f"({rate:.1f}% clean) ===")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"rows": rows, "checks": checks_total,
                        "false_positives": flipped_total, "clean_rate": rate},
                       indent=2, default=str),
            encoding="utf-8",
        )
        print(f"JSON report: {args.json}")

    # Exit non-zero when the battery fails: edited human text flips to AI.
    return 0 if flipped_total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
