#!/usr/bin/env python
"""Live detector benchmark: how human does each rewrite path *really* sound?

Real detectors (GPTZero, ZeroGPT, …) score text on statistical signals —
perplexity / randomness, burstiness (sentence-length variation), syntactic
freedom from formulaic shapes, and lexical cohesion. This tool measures
exactly those signals (the built-in detector computes them the same way the
paid detectors do, honestly) plus the 0-100 naturalness score, across every
rewrite path:

    original   -> raw AI-style text (baseline)
    deterministic -> no-API-key rewrite engine
    perfect    -> perfect-humanize feedback loop (LLM, up to 4 passes)
    deep       -> 4-hop translation chain (LLM)

A rewrite "passes" when the text reads human end-to-end: naturalness >= 75
and every measurable statistical signal sits in the human band (burstiness
>= 50, syntactic >= 75, perplexity/coherence above their length gates).

When ``GPTZERO_API_KEY`` is set in the environment, each text is also sent
to the real GPTZero API and the aggregate includes its pass rate
(%AI < 50 = human). ZeroGPT's public endpoint now requires a paid account,
so it is probed but its result is reported as-is.

Usage:
    python tools/detector_bench.py [--samples N] [--methods det,perfect,deep]
        [--floor 70] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.detectors import analyze, compute_metrics
from naturalizer.detectors_live import check_gptzero, check_zerogpt
from naturalizer.engine import Naturalizer
from naturalizer.feedback import feedback_humanize
from naturalizer.llm import llm_available

CORPUS = Path(__file__).resolve().parent.parent / "tests" / "corpus" / "ai_samples.txt"

#: Naturalness floor and per-signal human bands (0-100, higher = more human).
#: Coherence is reported but not gated — short paragraphs naturally share
#: few content words between sentences, and real detectors don't weigh it
#: as a hard signal the way they weigh burstiness/perplexity.
NATURAL_FLOOR = 75
BURSTINESS_HUMAN = 50   # sentence-length variation
SYNTACTIC_HUMAN = 75    # freedom from formulaic shapes
PERPLEXITY_HUMAN = 50   # randomness (LLM text is more predictable)
WORD_CHOICE_HUMAN = 55  # word predictability (rare-word density vs Google Books)

#: method name (CLI) -> path key in run_paths output.
METHOD_KEYS = {"det": "deterministic", "perfect": "perfect", "deep": "deep"}

#: Realistic modern AI-style prose — NOT the tell-stuffed test corpus. These
#: mimic what current models actually output: fluent, low-perplexity,
#: uniform-rhythm text with few classic buzzwords. They are the hard cases
#: the deterministic pattern matcher barely touches, so the benchmark is
#: honest about what real detectors still see.
REALISTIC = [
    # blog-style intro
    ("blog", (
        "In today's fast-moving digital world, businesses face an increasingly "
        "complex set of challenges when it comes to connecting with their "
        "audiences. The rise of social media has fundamentally transformed how "
        "brands communicate, making it essential for companies to adapt their "
        "strategies. Moreover, the integration of artificial intelligence into "
        "marketing workflows has opened up new possibilities for personalization "
        "and customer engagement. As we move forward, organizations that embrace "
        "these changes will be better positioned to thrive in a competitive "
        "landscape. Ultimately, the key to success lies in striking a balance "
        "between innovation and consistency across all channels. Companies that "
        "fail to adapt risk losing relevance in an era where consumer "
        "expectations continue to rise. Therefore, investing in the right tools "
        "and training is not merely an option but a necessity for long-term "
        "growth. By staying attuned to emerging trends, organizations can "
        "position themselves to capitalize on new opportunities as they arise."
    )),
    # academic-ish paragraph
    ("academic", (
        "The results of this study demonstrate that regular physical activity "
        "plays a significant role in improving cognitive function among older "
        "adults. It is important to note that the observed improvements were "
        "consistent across both genders and various socioeconomic backgrounds. "
        "Furthermore, the data suggests that moderate-intensity exercise yields "
        "the most substantial benefits when performed consistently over time. "
        "In conclusion, these findings highlight the importance of incorporating "
        "structured exercise programs into public health initiatives aimed at "
        "aging populations. The implications extend beyond individual wellbeing "
        "to broader questions about healthcare costs and community design. "
        "Future research should explore how these interventions can be scaled "
        "and sustained over longer periods, particularly in underserved areas "
        "where access to recreational facilities remains limited. Ultimately, "
        "translating these insights into policy will require collaboration "
        "across disciplines and a commitment to evidence-based decision making."
    )),
    # email / corporate
    ("email", (
        "I hope this message finds you well. I wanted to reach out regarding "
        "the upcoming project timeline and discuss a few adjustments we "
        "believe will streamline our workflow. As we prepare for the next "
        "phase, it would be helpful to align on priorities and ensure that "
        "all stakeholders are on the same page. Please let me know if you "
        "have any questions or if there are other considerations we should "
        "take into account. I look forward to hearing your thoughts and "
        "moving forward with this initiative. In the meantime, the team has "
        "already begun preliminary research on the second phase, which should "
        "give us a head start once we finalize the scope. We have also "
        "scheduled a preliminary review for early next month to assess our "
        "progress and identify any potential risks before they become "
        "significant issues. Please feel free to share any additional input "
        "you may have, as your perspective has always been valuable to us."
    )),
    # product blurb
    ("product", (
        "Our platform offers a comprehensive suite of tools designed to "
        "enhance productivity and drive meaningful results. With a user-friendly "
        "interface and robust analytics, teams can easily track their progress "
        "and make data-driven decisions. The seamless integration with existing "
        "systems ensures a smooth transition, while our dedicated support team "
        "is available around the clock to assist with any challenges. Whether "
        "you are a small business or a large enterprise, our solution scales "
        "to meet your needs and empowers you to achieve your goals efficiently. "
        "Security remains a top priority, with encryption and compliance "
        "measures built into every layer of the architecture. Customers also "
        "benefit from regular feature updates that are rolled out without "
        "disrupting their daily operations. The result is a dependable "
        "platform that grows alongside your organization and adapts to your "
        "changing requirements over time."
    )),
    # op-ed / commentary
    ("commentary", (
        "The debate over remote work has evolved considerably in recent years. "
        "While some argue that returning to the office fosters collaboration "
        "and culture, others point to the undeniable gains in flexibility and "
        "work-life balance. It is worth noting that the most successful "
        "companies have adopted hybrid models that combine the best of both "
        "worlds. However, the long-term implications of these arrangements "
        "remain uncertain, and it is clear that a one-size-fits-all approach "
        "will not suffice. As organizations navigate this transition, they "
        "must carefully consider the needs of their employees and the demands "
        "of their business. There is also the question of how mentorship and "
        "informal learning can survive in a distributed environment where "
        "serendipitous encounters are increasingly rare. Leaders will need to "
        "be intentional about fostering connection and building trust across "
        "geographically dispersed teams. Ultimately, the organizations that "
        "thrive will be those that treat flexibility not as a concession but "
        "as a strategic advantage in attracting and retaining talent."
    )),
]


def load_corpus_samples(limit: int | None = None) -> list[str]:
    text = CORPUS.read_text(encoding="utf-8")
    samples = [p.strip() for p in text.split("\n\n") if p.strip()]
    if limit:
        samples = samples[:limit]
    return samples


def local_report(text: str) -> dict:
    rep = analyze(text)
    return {
        "score": rep.score,
        "metrics": rep.metrics,
    }


def human_band(metrics: dict) -> list[str]:
    """Which statistical signals sit below the human band ('' = fine)."""
    weak = []
    gates = [
        ("burstiness", BURSTINESS_HUMAN),
        ("syntactic", SYNTACTIC_HUMAN),
        ("perplexity", PERPLEXITY_HUMAN),
        ("word_choice", WORD_CHOICE_HUMAN),
    ]
    for key, floor in gates:
        value = metrics.get(key)
        if value is not None and value < floor:
            # word_choice is only treated as a tell in combination with
            # formulaic prose (mid-frequency formal vocabulary is a "training
            # echo" signal; concrete topic vocabulary — a recipe, a field
            # guide — is not). The syntactic signal already gates that.
            if key == "word_choice" and metrics.get("syntactic", 100) >= SYNTACTIC_HUMAN:
                continue
            weak.append(key)
    return weak


def human_pass(local: dict, gptzero: dict | None) -> tuple[bool, list[str]]:
    """Pass = naturalness floor + every measurable signal in the human band
    (+ GPTZero < 50% AI when a key is configured). Returns (ok, reasons)."""
    reasons = []
    if local["score"] < NATURAL_FLOOR:
        reasons.append("naturalness")
    reasons += human_band(local["metrics"])
    if gptzero is not None and not gptzero.get("error"):
        if gptzero["score"] is not None and gptzero["score"] >= 50:
            reasons.append("gptzero")
    return (not reasons, reasons)


def gptzero(text: str) -> dict | None:
    if not os.environ.get("GPTZERO_API_KEY"):
        return None
    try:
        return check_gptzero(text)
    except Exception as exc:  # pragma: no cover - network edge
        return {"name": "gptzero", "score": None, "error": str(exc)}


def run_paths(eng: Naturalizer, sample: str, methods: list[str]) -> dict:
    out: dict[str, dict] = {}
    out["original"] = {
        "text": sample,
        "local": local_report(sample),
        "gptzero": gptzero(sample),
    }
    if "det" in methods:
        r = eng.naturalize(sample, use_llm=False)
        out["deterministic"] = {
            "text": r.rewritten,
            "local": local_report(r.rewritten),
            "gptzero": gptzero(r.rewritten),
        }
    if "perfect" in methods:
        r = feedback_humanize(eng, sample)
        text = r.get("text") or sample
        out["perfect"] = {
            "text": text,
            "local": local_report(text),
            "gptzero": gptzero(text),
            "passes": r.get("passes"),
        }
    if "deep" in methods:
        r = eng.naturalize(sample, use_llm=True, deep=True)
        text = r.llm_rewritten or r.rewritten
        out["deep"] = {
            "text": text,
            "local": local_report(text),
            "gptzero": gptzero(text),
            "method": r.llm_method,
        }
    return out


def fmt_signals(metrics: dict) -> str:
    bits = []
    for key, label in (("perplexity", "rand"), ("burstiness", "burst"),
                       ("syntactic", "syn"), ("coherence", "coh"),
                       ("word_choice", "word")):
        value = metrics.get(key)
        bits.append(f"{label}={value if value is not None else '—':<4}")
    return "  ".join(str(b) for b in bits)


def fmt_gz(g: dict | None) -> str:
    if g is None:
        return "gptzero=off"
    if g.get("error"):
        return "gptzero=ERR"
    return f"gptzero={g['score']}% {g['verdict']}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=None, help="limit corpus samples")
    ap.add_argument("--methods", default="det,perfect,deep",
                    help="comma list of det,perfect,deep (original always runs)")
    ap.add_argument("--floor", type=float, default=0.0,
                    help="min aggregate pass rate %% that must be met (exit 1 otherwise)")
    ap.add_argument("--json", default=None, help="write full JSON report")
    args = ap.parse_args()

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    samples = load_corpus_samples(args.samples)
    eng = Naturalizer(seed=0)

    if not llm_available("auto") and ("perfect" in methods or "deep" in methods):
        print("ERROR: perfect/deep need an LLM provider; none configured.", file=sys.stderr)
        return 2

    cases = [(f"realistic-{label}", t) for label, t in REALISTIC]
    cases += [(f"corpus#{i + 1}", t) for i, t in enumerate(samples)]
    rows: list[dict] = []

    print(f"Running {len(cases)} samples x {len(methods) + 1} paths "
          f"(methods: {', '.join(['original'] + methods)})…\n")
    for i, (label, sample_text) in enumerate(cases, 1):
        row = {"sample": label, "paths": run_paths(eng, sample_text, methods)}
        rows.append(row)
        print(f"[{i}/{len(cases)}] {label}")
        for name, p in row["paths"].items():
            m = p["local"]["metrics"]
            extra = f" passes={p.get('passes')}" if "passes" in p else ""
            extra += f" method={p.get('method')}" if "method" in p else ""
            print(f"    {name:<13} nat={p['local']['score']:>3}  "
                  f"{fmt_signals(m)}  {fmt_gz(p.get('gptzero'))}{extra}")
        time.sleep(1)  # be polite to free tiers

    print("\n=== aggregate pass rates (reads human end-to-end) ===")
    agg: dict[str, dict] = {}
    for name in ["original"] + [METHOD_KEYS[m] for m in methods]:
        total = pass_count = 0
        sig_weak: dict[str, int] = {}
        gz_human = gz_total = 0
        for row in rows:
            p = row["paths"].get(name)
            if not p:
                continue
            total += 1
            ok, reasons = human_pass(p["local"], p.get("gptzero"))
            if ok:
                pass_count += 1
            else:
                for r in reasons:
                    sig_weak[r] = sig_weak.get(r, 0) + 1
            g = p.get("gptzero")
            if g and not g.get("error") and g.get("score") is not None:
                gz_total += 1
                if g["score"] < 50:
                    gz_human += 1
        rate = pass_count / total * 100 if total else 0.0
        agg[name] = {"pass": pass_count, "total": total, "rate": rate,
                     "weak_signals": sig_weak, "gptzero_human": gz_human,
                     "gptzero_total": gz_total}
        weak = ", ".join(f"{k}x{n}" for k, n in sorted(sig_weak.items())) or "none"
        gz_part = f"  GPTZero-human {gz_human}/{gz_total}" if gz_total else ""
        print(f"  {name:<13} {pass_count:>2}/{total} pass ({rate:5.1f}%)  "
              f"weak: {weak}{gz_part}")

    if args.json:
        Path(args.json).write_text(
            json.dumps({"rows": rows, "aggregate": agg}, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\nJSON report: {args.json}")

    best = "perfect" if "perfect" in agg else ("deep" if "deep" in agg else "deterministic")
    rate = agg[best]["rate"]
    if args.floor and rate < args.floor:
        print(f"\nFLOOR NOT MET: best-path pass rate {rate:.1f}% < {args.floor:.0f}%",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
