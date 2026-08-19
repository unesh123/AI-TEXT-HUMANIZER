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

    def test_explicit_provider_unconfigured_warns(self):
        # No LLM env vars in the test environment: picking Claude explicitly
        # must explain the deterministic fallback instead of staying silent.
        result = self.engine.naturalize(
            "In today's fast-paced world, it is important to note that "
            "technology plays a crucial role.",
            style="academic",
            provider="claude",
        )
        self.assertFalse(result.llm_used)
        self.assertTrue(result.rewritten)  # deterministic path still works
        self.assertIn("claude isn't configured", result.llm_warning or "")
        self.assertIn("deterministic rewrite", result.llm_warning or "")

    def test_explicit_provider_failure_warns(self):
        # Provider is configured, but the gateway call fails (the real-world
        # 401 case) — the fallback must be explained, not silent.
        with mock.patch("naturalizer.engine.llm_available", return_value=True), \
             mock.patch("naturalizer.engine.rewrite_with_llm_details", return_value=None):
            result = self.engine.naturalize(
                "In today's fast-paced world, it is important to note that "
                "technology plays a crucial role.",
                style="academic",
                provider="cx",
            )
        self.assertFalse(result.llm_used)
        self.assertTrue(result.rewritten)
        self.assertIn("cx call failed", result.llm_warning or "")
        self.assertIn("API key", result.llm_warning or "")

    def test_explicit_provider_recovers_on_failover_stays_quiet(self):
        # CX is requested but down; the failover chain serves from Claude.
        # The text is real and served, so no "call failed" scare message —
        # and the provider label names the one that actually wrote it.
        with mock.patch("naturalizer.engine.llm_available", return_value=True), \
             mock.patch(
                 "naturalizer.engine.rewrite_with_llm_details",
                 return_value=("Failover output text.", "claude"),
             ):
            result = self.engine.naturalize(
                "In today's fast-paced world, it is important to note that "
                "technology plays a crucial role.",
                style="academic",
                provider="cx",
            )
        self.assertTrue(result.llm_used)
        self.assertIsNone(result.llm_warning)
        self.assertEqual(result.llm_provider, "claude")

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
        self.assertIn(d["verdict"], ("human", "ai", "mixed"))

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
            {"before", "after", "after_score", "plain_register", "semantic_preservation"},
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

    def test_number_drift_falls_back_to_safe_rewrite(self):
        import naturalizer.engine as eng

        original = "The pilot reduced costs by 42% across 800 accounts."
        with mock.patch.object(eng, "llm_available", return_value=True), mock.patch.object(
            eng,
            "rewrite_with_llm_details",
            return_value=("The pilot reduced costs across many accounts.", "claude"),
        ):
            result = self.engine.naturalize(original, provider="claude")

        shown = result.llm_rewritten if result.llm_used else result.rewritten
        self.assertIn("42%", shown)
        self.assertIn("800", shown)
        self.assertIn("fact-preserving", result.llm_warning or "")
        self.assertFalse(result.metrics["semantic_preservation"]["hard_drift"])

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

    def test_deep_uses_chain(self):
        import naturalizer.engine as eng

        with mock.patch.object(eng, "llm_available", return_value=True), mock.patch.object(
            eng, "run_chain", return_value="Chain output text."
        ) as chain, mock.patch.object(eng, "rewrite_with_llm_details") as single:
            result = self.engine.naturalize("Draft text here.", deep=True)

        chain.assert_called_once()
        single.assert_not_called()
        self.assertTrue(result.llm_used)
        self.assertEqual(result.llm_method, "chain")
        self.assertEqual(result.llm_rewritten, "Chain output text.")

    def test_deep_falls_back_to_single_when_chain_fails(self):
        import naturalizer.engine as eng

        with mock.patch.object(eng, "llm_available", return_value=True), mock.patch.object(
            eng, "run_chain", return_value=None
        ), mock.patch.object(
            eng, "rewrite_with_llm_details", return_value=("Single output text.", "claude")
        ):
            result = self.engine.naturalize("Draft text here.", deep=True)

        self.assertTrue(result.llm_used)
        self.assertEqual(result.llm_method, "single")
        self.assertEqual(result.llm_rewritten, "Single output text.")

    def test_deep_without_llm_stays_deterministic(self):
        import naturalizer.engine as eng

        with mock.patch.object(eng, "llm_available", return_value=False), mock.patch.object(
            eng, "run_chain"
        ) as chain, mock.patch.object(eng, "rewrite_with_llm_details") as single:
            result = self.engine.naturalize("Draft text here.", deep=True)

        chain.assert_not_called()
        single.assert_not_called()
        self.assertFalse(result.llm_used)
        self.assertIsNone(result.llm_method)

    # -- LLM output is polished through the deterministic engine ---------

    def test_single_pass_output_is_polished(self):
        import naturalizer.engine as eng

        with mock.patch.object(eng, "llm_available", return_value=True), mock.patch.object(
            eng, "rewrite_with_llm_details",
            return_value=(
                "This is the tip of the iceberg, and think outside the box "
                "is the motto. Ultimately, it matters.",
                "claude",
            ),
        ):
            result = self.engine.naturalize("Draft text here.")

        self.assertTrue(result.llm_used)
        self.assertEqual(result.llm_method, "single")
        self.assertNotIn("tip of the iceberg", result.llm_rewritten)
        self.assertNotIn("think outside the box", result.llm_rewritten)
        self.assertNotIn("Ultimately", result.llm_rewritten)

    def test_deep_output_is_polished(self):
        import naturalizer.engine as eng

        with mock.patch.object(eng, "llm_available", return_value=True), mock.patch.object(
            eng, "run_chain",
            return_value=(
                "Teams fixate on the tip of the iceberg and presume they "
                "understand everything."
            ),
        ):
            result = self.engine.naturalize("Draft text here.", deep=True)

        self.assertTrue(result.llm_used)
        self.assertEqual(result.llm_method, "chain")
        self.assertNotIn("tip of the iceberg", result.llm_rewritten)


if __name__ == "__main__":
    unittest.main()
