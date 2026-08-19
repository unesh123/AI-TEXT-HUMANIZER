"""Tests for the detector / humanizer accuracy benchmark."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.benchmark import load_corpus, run_benchmark


class BenchmarkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_benchmark(seed=0)

    def test_corpus_loads_labeled_samples(self):
        corpus = load_corpus()
        self.assertGreater(len(corpus["ai"]), 0)
        self.assertGreater(len(corpus["human"]), 0)

    def test_report_structure(self):
        d = self.report.to_dict()
        self.assertEqual(
            set(d["detector"]),
            {
                "ai_samples", "human_samples", "confusion", "accuracy",
                "precision", "recall", "f1", "human_detection_accuracy",
                "live_llm_samples", "live_llm_detected", "live_llm_detection_rate",
            },
        )
        self.assertEqual(
            set(d["humanizer"]),
            {"mean_before", "mean_after", "mean_lift", "floor_75_reached", "by_style"},
        )
        self.assertIn("note", d)

    def test_by_style_covers_every_writing_style(self):
        styles = {s["style"] for s in self.report.by_style}
        self.assertEqual(
            styles, {"academic", "business", "creative", "casual"}
        )
        for entry in self.report.by_style:
            self.assertGreater(entry["after"], entry["before"])
            self.assertLessEqual(entry["lift"], 100)
            self.assertTrue(0 <= entry["floor_75_reached"] <= 1)
            self.assertEqual(
                round(entry["after"] - entry["before"], 1), entry["lift"]
            )

    def test_by_style_matches_overall_academic(self):
        # The overall humanizer numbers are the academic run, so the
        # academic by-style row must agree with them.
        hum = self.report.to_dict()["humanizer"]
        acad = next(s for s in hum["by_style"] if s["style"] == "academic")
        self.assertEqual(acad["before"], hum["mean_before"])
        self.assertEqual(acad["after"], hum["mean_after"])

    def test_detector_beats_chance_on_corpus(self):
        det = self.report.to_dict()["detector"]
        self.assertGreaterEqual(det["accuracy"], 0.5)
        self.assertGreaterEqual(det["f1"], 0.5)
        self.assertTrue(0 <= det["precision"] <= 1)
        self.assertTrue(0 <= det["recall"] <= 1)

    def test_humanizer_lifts_ai_samples(self):
        hum = self.report.to_dict()["humanizer"]
        # The corpus guarantee (test_corpus.py) is every AI sample reaching
        # >= 75 after a deterministic rewrite, so the mean lift is large.
        self.assertGreater(hum["mean_lift"], 10)
        self.assertGreater(hum["floor_75_reached"], 0.9)
        self.assertLess(hum["mean_before"], 60)

    def test_per_sample_rows_present(self):
        samples = self.report.samples
        self.assertEqual(
            len(samples),
            self.report.ai_samples + self.report.human_samples + self.report.live_llm_samples,
        )
        for s in samples:
            self.assertIn("label", s)
            self.assertIn("verdict", s)

    def test_live_llm_reported_separately(self):
        """Real LLM output must not pollute the tuned accuracy."""
        det = self.report.to_dict()["detector"]
        self.assertGreater(det["live_llm_samples"], 0)
        # Accuracy is computed only over the tuned ai/human sets.
        self.assertEqual(
            self.report.total,
            self.report.ai_samples + self.report.human_samples,
        )
        self.assertIn("live_llm_detection_rate", det)


if __name__ == "__main__":
    unittest.main()
