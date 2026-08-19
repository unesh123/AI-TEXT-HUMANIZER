"""Detector / humanizer accuracy benchmark over the test corpus.

Runs the local detector against ``tests/corpus/ai_samples.txt`` and
``tests/corpus/human_samples.txt`` and reports how often its verdict
matches the known label, plus how much the deterministic rewrite lifts the
naturalness score of the AI-heavy samples.

This is the honest "how accurate am I" report: it measures the detector
against the labeled corpus it was tuned on (a best case, not a real-world
distribution — same caveat as every detector), and it measures the
humanizer by its own score floor. Real third-party comparison (GPTZero,
Turnitin, …) needs their paid APIs; this gives you the ground truth about
the local pipeline instead.

Pure stdlib, deterministic, fast.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .engine import Naturalizer
from .styles import STYLE_NAMES

CORPUS_DIR = Path(__file__).resolve().parent.parent / "tests" / "corpus"


def load_corpus() -> Dict[str, List[str]]:
    """Load labeled samples from the corpus files (blank-line separated).

    ``ai`` and ``human`` are the curated, labeled sets the detector was
    tuned on. ``live_llm`` is real commercial-LLM output (see
    ``live_llm_samples.txt`` for provenance) — the external, un-tuned case
    that keeps the accuracy number honest.
    """
    out: Dict[str, List[str]] = {}
    for label, name in (
        ("ai", "ai_samples.txt"),
        ("human", "human_samples.txt"),
        ("live_llm", "live_llm_samples.txt"),
    ):
        path = CORPUS_DIR / name
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        samples = [p.strip() for p in text.split("\n\n") if p.strip()]
        # Skip the provenance header lines in live_llm_samples.txt.
        samples = [s for s in samples if not s.startswith("#")]
        out[label] = samples
    return out


def _f1(precision: float, recall: float) -> float:
    return (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )


@dataclass
class BenchmarkReport:
    """Accuracy of the detector plus the humanizer's score lift."""

    # Detector: AI-vs-human classification on the labeled corpus.
    ai_samples: int = 0
    human_samples: int = 0
    tp: int = 0   # AI sample -> "ai" verdict
    fp: int = 0   # human sample -> "ai" verdict
    fn: int = 0   # AI sample -> not "ai" verdict
    tn: int = 0   # human sample -> not "ai" verdict

    # External, un-tuned case: real commercial-LLM output. Reported
    # separately — how many of these the local detector catches is the
    # honest real-world number, kept out of the tuned accuracy above.
    live_llm_samples: int = 0
    live_llm_detected: int = 0  # real LLM output flagged "ai"

    # Humanizer: naturalness before -> after the deterministic rewrite.
    lift_before: List[int] = field(default_factory=list)
    lift_after: List[int] = field(default_factory=list)

    # Per-style humanizer lift (academic, business, creative, casual): each
    # entry carries the mean before/after score and lift for that style.
    by_style: List[Dict] = field(default_factory=list)

    # Per-sample detail for the UI (label, verdict, before/after scores).
    samples: List[Dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.fn + self.tn

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / self.total if self.total else 0.0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        return _f1(self.precision, self.recall)

    @property
    def human_detection_accuracy(self) -> float:
        """Share of human samples correctly judged human (not AI/mixed)."""
        if not self.human_samples:
            return 0.0
        tn = self.tn
        # tn counts human samples *not* flagged AI (i.e. human or mixed).
        return tn / self.human_samples

    @property
    def mean_lift(self) -> float:
        if not self.lift_after:
            return 0.0
        before = sum(self.lift_before) / len(self.lift_before)
        after = sum(self.lift_after) / len(self.lift_after)
        return after - before

    @property
    def mean_after(self) -> float:
        return (
            sum(self.lift_after) / len(self.lift_after) if self.lift_after else 0.0
        )

    @property
    def floor_reached(self) -> float:
        """Share of AI samples that clear the 75 naturalness floor."""
        if not self.lift_after:
            return 0.0
        return sum(1 for s in self.lift_after if s >= 75) / len(self.lift_after)

    def to_dict(self) -> Dict:
        return {
            "detector": {
                "ai_samples": self.ai_samples,
                "human_samples": self.human_samples,
                "confusion": {"tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn},
                "accuracy": round(self.accuracy, 3),
                "precision": round(self.precision, 3),
                "recall": round(self.recall, 3),
                "f1": round(self.f1, 3),
                "human_detection_accuracy": round(self.human_detection_accuracy, 3),
                "live_llm_samples": self.live_llm_samples,
                "live_llm_detected": self.live_llm_detected,
                "live_llm_detection_rate": round(
                    self.live_llm_detected / self.live_llm_samples
                    if self.live_llm_samples else 0.0, 3
                ),
            },
            "humanizer": {
                "mean_before": round(
                    sum(self.lift_before) / len(self.lift_before) if self.lift_before else 0, 1
                ),
                "mean_after": round(self.mean_after, 1),
                "mean_lift": round(self.mean_lift, 1),
                "floor_75_reached": round(self.floor_reached, 3),
                "by_style": self.by_style,
            },
            "samples": self.samples,
            "note": (
                "Local heuristic benchmark on the bundled labeled corpus (a best "
                "case, not a real-world distribution) — the same honesty caveat "
                "applies to every AI detector. The live_llm rows are real "
                "commercial-LLM output (tests/corpus/live_llm_samples.txt) and "
                "are reported separately: they are not part of the tuned "
                "accuracy, and most read human-grade to a heuristic detector."
            ),
        }


def run_benchmark(seed: int = 0) -> BenchmarkReport:
    """Score the detector and humanizer over the labeled corpus."""
    corpus = load_corpus()
    engine = Naturalizer(seed=seed)
    report = BenchmarkReport(
        ai_samples=len(corpus["ai"]),
        human_samples=len(corpus["human"]),
        live_llm_samples=len(corpus["live_llm"]),
    )

    for sample in corpus["ai"]:
        d = engine.detect(sample, style="academic")
        before = d["score"]
        result = engine.naturalize(sample, style="academic", use_llm=False)
        after = result.metrics.get("after_score") if result.metrics else None
        if after is None:
            after = result.score
        verdict = d["verdict"]
        report.lift_before.append(before)
        report.lift_after.append(after)
        if verdict == "ai":
            report.tp += 1
        else:
            report.fn += 1
        report.samples.append(
            {
                "label": "ai",
                "verdict": verdict,
                "score_before": before,
                "score_after": after,
            }
        )

    # Humanizer lift per writing style: rewrite the AI corpus in each style
    # and report the mean naturalness before -> after. The detector verdicts
    # above are style-agnostic (they classify the raw text); the per-style
    # numbers show how well each register's rewrite escapes detection.
    for style in STYLE_NAMES:
        befores: List[int] = []
        afters: List[int] = []
        for sample in corpus["ai"]:
            det = engine.detect(sample, style=style)
            res = engine.naturalize(sample, style=style, use_llm=False)
            aft = res.metrics.get("after_score") if res.metrics else None
            if aft is None:
                aft = res.score
            befores.append(det["score"])
            afters.append(aft)
        mean_before = round(sum(befores) / len(befores), 1) if befores else 0.0
        mean_after = round(sum(afters) / len(afters), 1) if afters else 0.0
        cleared = (
            sum(1 for s in afters if s >= 75) / len(afters) if afters else 0.0
        )
        report.by_style.append(
            {
                "style": style,
                "before": mean_before,
                "after": mean_after,
                "lift": round(mean_after - mean_before, 1),
                "floor_75_reached": round(cleared, 3),
            }
        )

    for sample in corpus["human"]:
        d = engine.detect(sample, style="academic")
        verdict = d["verdict"]
        if verdict == "ai":
            report.fp += 1
        else:
            report.tn += 1
        report.samples.append(
            {
                "label": "human",
                "verdict": verdict,
                "score_before": d["score"],
            }
        )

    # External, un-tuned case: real commercial-LLM output. Detected = the
    # local detector calls it "ai". This is the honest real-world number,
    # separate from the tuned accuracy above.
    for sample in corpus["live_llm"]:
        d = engine.detect(sample, style="academic")
        if d["verdict"] == "ai":
            report.live_llm_detected += 1
        report.samples.append(
            {
                "label": "live_llm",
                "verdict": d["verdict"],
                "score_before": d["score"],
            }
        )

    return report
