"""Tests for the Naturalizer engine."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tests.testutil as _testutil  # noqa: F401  (scrub LLM env — hermetic suite)

from naturalizer.engine import Naturalizer


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = Naturalizer(seed=0)

    def test_naturalize_returns_full_result(self):
        result = self.engine.naturalize(
            "In today's fast-paced world, it is important to note that "
            "technology plays a crucial role.",
            style="academic",
        )
        self.assertIsInstance(result.score, int)
        self.assertTrue(result.rewritten)
        self.assertTrue(result.original)
        self.assertEqual(result.style, "academic")
        # Deterministic path must not use the LLM unless configured.
        self.assertFalse(result.llm_used)

    def test_empty_input_returns_clean_result(self):
        result = self.engine.naturalize("   ")
        self.assertEqual(result.score, 100)
        self.assertEqual(result.rewritten, "")

    def test_batch_returns_one_result_per_input(self):
        texts = [
            "Furthermore, the data was noisy.",
            "However, the trend was clear.",
            "",
        ]
        results = self.engine.batch(texts, style="business")
        self.assertEqual(len(results), 3)
        self.assertEqual(results[2].rewritten, "")

    def test_unknown_style_falls_back_to_default(self):
        result = self.engine.naturalize("Some text here.", style="totally-bogus")
        self.assertEqual(result.style, "academic")

    def test_to_dict_serializes(self):
        result = self.engine.naturalize("The wiring came loose behind the counter.")
        d = result.to_dict()
        self.assertEqual(set(d), {
            "original", "rewritten", "llm_rewritten", "llm_used", "llm_method",
            "llm_provider", "llm_warning", "score", "issues", "style",
            "sentence_count", "metrics", "diff", "intensity",
        })
        # Diff is between the original and the shown rewrite.
        self.assertEqual(
            "".join(op["text"] for op in d["diff"] if op["type"] != "add"),
            result.original,
        )

    # -- provider warnings ----------------------------------------------




    def test_auto_provider_stays_quiet_on_failure(self):
        # Auto mode is designed to fall back — no warning noise.
        result = self.engine.naturalize(
            "In today's fast-paced world, it is important to note that "
            "technology plays a crucial role.",
            style="academic",
            provider="auto",
        )
        self.assertIsNone(result.llm_warning)

    # -- detector view ---------------------------------------------------

    def test_detect_human_text(self):
        d = self.engine.detect(
            "The wiring came loose behind the counter, so we shut the power off "
            "before touching anything. Once the new breaker was in, the lights "
            "came back on without a flicker."
        )
        self.assertEqual(d["verdict"], "human")
        self.assertGreaterEqual(d["confidence"], 70)
        self.assertEqual(d["distribution"]["human"], 100)
        self.assertTrue(all(s["label"] == "human" for s in d["sentences"]))
        self.assertIn("word_count", d)
        self.assertIn("metrics", d)

    def test_detect_ai_text(self):
        d = self.engine.detect(
            "In today's fast-paced world, it is important to note that technology "
            "plays a crucial role. Furthermore, we must leverage cutting-edge "
            "solutions to remain competitive."
        )
        self.assertEqual(d["verdict"], "ai")
        self.assertGreater(d["distribution"]["ai"], 0)
        self.assertTrue(any(s["label"] == "ai" for s in d["sentences"]))

    def test_detect_empty_text(self):
        d = self.engine.detect("")
        self.assertEqual(d["word_count"], 0)
        self.assertEqual(d["verdict"], "uncertain")
        self.assertLessEqual(d["confidence"], 45)
        self.assertIn("evidence_coverage", d)

    # -- verification re-scan (metrics before/after) ---------------------

    def test_metrics_before_after_present(self):
        text = (
            "In today's fast-paced world, it is important to note that "
            "technology plays a crucial role in our daily lives. Furthermore, "
            "the ever-evolving landscape of digital tools continues to "
            "transform the way we work and communicate. Moreover, organizations "
            "must leverage cutting-edge solutions to remain competitive, and "
            "this is essential to highlight in any discussion of modern business."
        )
        result = self.engine.naturalize(text, style="academic")
        m = result.metrics
        self.assertEqual(
            set(m),
            {"before", "after", "after_score", "detector_comparison", "plain_register", "semantic_preservation", "source_overlap", "rewrite_mode", "rewrite_quality"},
        )
        self.assertEqual(
            set(m["before"]),
            {"perplexity", "burstiness", "syntactic", "coherence", "word_choice"},
        )
        self.assertEqual(
            set(m["after"]),
            {"perplexity", "burstiness", "syntactic", "coherence", "word_choice"},
        )
        # The rewrite must not score worse than the input on formulaic patterns.
        self.assertGreaterEqual(m["after"]["syntactic"], m["before"]["syntactic"])
        # Verification score is a real 0-100 number.
        self.assertIsInstance(m["after_score"], int)
        self.assertTrue(0 <= m["after_score"] <= 100)

    def test_full_rewrite_mode_is_reported_without_overriding_intensity(self):
        result = self.engine.naturalize(
            "The team reviewed 12 accounts and found two issues.",
            use_llm=False,
            intensity=0.2,
            rewrite_mode="full",
        )
        self.assertEqual(result.metrics["rewrite_mode"], "full")
        self.assertEqual(result.intensity, 0.2)

    def test_source_overlap_is_reported(self):
        result = self.engine.naturalize(
            "Technology helps students find information online, but too much reliance can create problems.",
            use_llm=False,
        )
        overlap = result.metrics["source_overlap"]
        self.assertIn("reuse_percent", overlap)
        self.assertGreaterEqual(overlap["reuse_percent"], 0)
        self.assertLessEqual(overlap["reuse_percent"], 100)


    def test_metrics_plain_register_shifts_toward_human_memory(self):
        # Stiff, Latinate register -> the deterministic rewrite should move
        # toward the everyday vocabulary humans actually use, and the
        # before/after metrics must reflect the shift.
        text = (
            "The utilization of advanced methodologies facilitates the "
            "attainment of optimal outcomes. Furthermore, it is imperative "
            "to ascertain the parameters of this approach."
        )
        result = self.engine.naturalize(text, style="academic")
        pr = result.metrics["plain_register"]
        self.assertIn("before", pr)
        self.assertIn("after", pr)
        self.assertTrue(0.0 <= pr["before"] <= 1.0)
        self.assertTrue(0.0 <= pr["after"] <= 1.0)
        # The rewrite must not drift away from plain, everyday English.
        self.assertGreaterEqual(pr["after"], pr["before"])

    def test_metrics_plain_register_high_on_human_style_text(self):
        # Text already written the way humans actually write should score
        # comfortably above stiff, Latinate prose against the verified
        # human-writing memory (the corpus itself scores 1.0).
        from naturalizer.human_memory import plain_register_score

        casual = (
            "I got up early and made coffee. Then I sat down and checked "
            "my email before the kids woke up. It was a quiet morning, "
            "the kind you don't get very often."
        )
        stiff = (
            "The utilization of advanced methodologies facilitates the "
            "attainment of optimal outcomes. Furthermore, it is imperative "
            "to ascertain the parameters of this approach."
        )
        casual_score = plain_register_score(casual)
        stiff_score = plain_register_score(stiff)
        # Everyday prose must read clearly more human than stiff prose, and
        # the plain-language rewrite of stiff prose must land in between.
        self.assertGreater(casual_score, 0.8)
        self.assertLess(stiff_score, 0.7)
        self.assertGreater(casual_score - stiff_score, 0.15)

    def test_metrics_empty_text(self):
        result = self.engine.naturalize("")
        self.assertEqual(result.metrics, {})

    # -- deep mode (translation chain) -----------------------------------





