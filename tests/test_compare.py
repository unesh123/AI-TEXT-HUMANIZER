"""Tests for the live multi-provider comparison and best-candidate
selection (naturalizer/compare.py)."""

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.compare import (
    provider_scraped,
    provider_status,
    run_comparison,
    score_candidate,
    select_best,
)

SAMPLE = (
    "The proposed initiative would introduce a new reporting framework "
    "across the entire organization. Furthermore, it aims to leverage "
    "existing infrastructure to optimize operational efficiency. "
    "Additionally, the initiative will utilize a phased rollout strategy "
    "to mitigate potential risks. In conclusion, the framework is "
    "designed to enhance overall productivity."
)


def _cand(provider, score, plain=0.8, fact_lost=0, text="x"):
    return {
        "provider": provider,
        "text": text,
        "score": score,
        "plain": plain,
        "fact_issues": [],
        "fact_lost": fact_lost,
    }


class SelectBestTest(unittest.TestCase):
    def test_clearing_floor_beats_below_floor(self):
        ranked = select_best("orig", [_cand("a", 60), _cand("b", 80)], floor=75)
        self.assertEqual(ranked[0]["provider"], "b")
        self.assertTrue(ranked[0]["best"])
        self.assertEqual(ranked[1]["rank"], 2)
        self.assertFalse(ranked[1]["best"])

    def test_higher_score_wins_among_passing(self):
        ranked = select_best("orig", [_cand("a", 82), _cand("b", 90)], floor=75)
        self.assertEqual(ranked[0]["provider"], "b")

    def test_plain_register_is_tiebreak(self):
        ranked = select_best(
            "orig",
            [_cand("a", 80, plain=0.7), _cand("b", 80, plain=0.9)],
            floor=75,
        )
        self.assertEqual(ranked[0]["provider"], "b")

    def test_fact_loss_is_last_tiebreak(self):
        ranked = select_best(
            "orig",
            [_cand("a", 85, plain=0.8, fact_lost=1), _cand("b", 85, plain=0.8, fact_lost=0)],
            floor=75,
        )
        self.assertEqual(ranked[0]["provider"], "b")

    def test_below_floor_still_ranked_best_available(self):
        ranked = select_best("orig", [_cand("a", 40), _cand("b", 50)], floor=75)
        self.assertEqual(ranked[0]["provider"], "b")
        # rank 1 is the best *available* even when nothing clears the floor
        self.assertTrue(ranked[0]["best"])
        self.assertEqual(ranked[0]["reason"].split(" (")[0], "below floor 75")

    def test_identical_candidate_marked_no_change(self):
        ranked = select_best(
            "hello",
            [{"provider": "p", "text": "hello", "score": 90, "plain": 0.8,
              "fact_issues": [], "fact_lost": 0}],
            floor=75,
        )
        self.assertTrue(ranked[0]["no_change"])

    def test_reason_is_explainable(self):
        ranked = select_best("orig", [_cand("b", 80)], floor=75)
        self.assertIn("clears floor 75", ranked[0]["reason"])
        self.assertIn("plain-register", ranked[0]["reason"])


class ScoreCandidateTest(unittest.TestCase):
    def test_dropped_number_counts_as_fact_loss(self):
        s = score_candidate(
            "There were 12 participants and 3 winners.",
            "There were participants and winners.",
        )
        self.assertGreaterEqual(s["fact_lost"], 1)
        self.assertTrue(s["fact_issues"])

    def test_preserved_numbers_no_loss(self):
        s = score_candidate(
            "There were 12 participants and 3 winners.",
            "A total of 12 participants competed for 3 prizes.",
        )
        self.assertEqual(s["fact_lost"], 0)


class RunComparisonTest(unittest.TestCase):
    def test_local_candidates_always_present(self):
        report = run_comparison(SAMPLE, style="academic", floor=75)
        providers = [c["provider"] for c in report["candidates"]]
        self.assertIn("local-deterministic", providers)
        self.assertIn("local-perfect-loop", providers)

    def test_missing_external_keys_reported_not_skipped(self):
        for k in ("STEALTHGPT_API_KEY", "UNDETECTABLE_API_KEY"):
            os.environ.pop(k, None)
        report = run_comparison(SAMPLE, style="academic", floor=75)
        blocked = {b["provider"] for b in report["blocked"]}
        self.assertIn("stealthgpt", blocked)
        self.assertIn("undetectable", blocked)
        notes = " ".join(b.get("note", "") or "" for b in report["blocked"])
        self.assertIn("STEALTHGPT_API_KEY", notes)
        self.assertIn("UNDETECTABLE_API_KEY", notes)

    def test_best_is_local_when_no_external_keys(self):
        for k in ("STEALTHGPT_API_KEY", "UNDETECTABLE_API_KEY"):
            os.environ.pop(k, None)
        report = run_comparison(SAMPLE, style="academic", floor=75)
        self.assertTrue(report["best"]["provider"].startswith("local-"))

    def test_report_has_original_and_floor(self):
        report = run_comparison(SAMPLE, style="academic", floor=75)
        self.assertEqual(report["floor"], 75)
        self.assertIn("score", report["original"])


class ProviderScrapedTest(unittest.TestCase):
    def test_loads_probe_report(self):
        with tempfile.TemporaryDirectory() as d:
            url = "https://texttohuman.com"
            safe = re.sub(r"[^\w.-]", "_", re.sub(r"^https?://", "", url))
            Path(d, "probe_report.json").write_text(
                json.dumps([{"url": url, "ok": True}]), encoding="utf-8"
            )
            Path(d, safe + ".txt").write_text("humanized output here", encoding="utf-8")
            cands = provider_scraped(d)
            self.assertEqual(len(cands), 1)
            self.assertIn("texttohuman", cands[0]["provider"])
            self.assertEqual(cands[0]["text"], "humanized output here")

    def test_missing_dir_returns_empty(self):
        self.assertEqual(provider_scraped(None), [])
        self.assertEqual(provider_scraped("does-not-exist"), [])


class ProviderStatusTest(unittest.TestCase):
    def test_status_reports_key_gated_providers(self):
        os.environ.pop("STEALTHGPT_API_KEY", None)
        rows = {r["provider"]: r for r in provider_status()}
        self.assertTrue(rows["local-deterministic"]["configured"])
        self.assertFalse(rows["stealthgpt"]["configured"])
        self.assertIn("STEALTHGPT_API_KEY", rows["stealthgpt"]["note"])


if __name__ == "__main__":
    unittest.main()
