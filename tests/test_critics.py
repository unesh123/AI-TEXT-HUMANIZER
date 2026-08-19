"""Tests for the factual preservation critics and best-of-N ranking.

The gates are the hard-check layer the report-style architecture demands:
a rewrite that drops a number must be rejected no matter how fluent it
reads; negation and proper-noun drift are flagged as strong warnings.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.testutil as _testutil  # noqa: F401  (hermetic suite)

from naturalizer.critics import (
    extract_entities,
    extract_negations,
    extract_numbers,
    preservation_issues,
    rank_candidates,
)


class NumberExtractionTest(unittest.TestCase):
    def test_extracts_integers_decimals_percents(self):
        text = "The study ran 12 weeks, covered 800 participants (3.5 per week), a 90% rate, and 1,000 total."
        self.assertEqual(
            extract_numbers(text),
            {"12", "800", "3.5", "90%", "1000"},
        )

    def test_no_numbers(self):
        self.assertEqual(extract_numbers("No figures here at all."), set())


class NegationTest(unittest.TestCase):
    def test_counts_negations(self):
        self.assertEqual(extract_negations("We do not recommend it, and it has never worked."), 2)
        self.assertEqual(extract_negations("It works fine."), 0)

    def test_contractions_count(self):
        self.assertEqual(extract_negations("It isn't ready and hasn't shipped."), 2)


class EntityTest(unittest.TestCase):
    def test_mid_sentence_capitalized_words_are_entities(self):
        text = "The report was written by Dr. Smith at the University of Oxford."
        self.assertIn("Smith", extract_entities(text))
        self.assertIn("Oxford", extract_entities(text))

    def test_sentence_initial_words_are_not_entities(self):
        # "The" at the start of a sentence is a function word, not a name.
        text = "The wiring came loose behind the counter."
        self.assertNotIn("The", extract_entities(text))

    def test_common_non_entity_caps_ignored(self):
        text = "However, the results were clear. This confirms the trend."
        self.assertEqual(extract_entities(text), set())


class PreservationIssuesTest(unittest.TestCase):
    def test_faithful_rewrite_has_no_issues(self):
        orig = "The study ran for 12 weeks and covered 800 participants, with a 90% response rate."
        cand = "The study ran for 12 weeks, covering 800 participants with a 90% response rate."
        self.assertEqual(preservation_issues(orig, cand), [])

    def test_dropped_number_is_high_severity(self):
        orig = "The study ran for 12 weeks and covered 800 participants."
        cand = "The study ran for several months, covering many participants."
        issues = preservation_issues(orig, cand)
        self.assertTrue(issues)
        for issue in issues:
            self.assertEqual(issue["severity"], "high")
            self.assertEqual(issue["kind"], "number")
        snippets = {i["snippet"] for i in issues}
        self.assertEqual(snippets, {"12", "800"})

    def test_number_format_change_is_not_drift(self):
        # "1,000" -> "1000" is the same number; not a fact change.
        self.assertEqual(
            preservation_issues("A total of 1,000 units.", "A total of 1000 units."),
            [],
        )

    def test_negation_added_is_flagged(self):
        issues = preservation_issues(
            "We recommend turning it off at night.",
            "We do not recommend turning it off at night.",
        )
        self.assertTrue(any(i["kind"] == "negation" for i in issues))

    def test_negation_removed_is_flagged(self):
        issues = preservation_issues(
            "We do not recommend it.",
            "We recommend it.",
        )
        self.assertTrue(any(i["kind"] == "negation" for i in issues))

    def test_proper_noun_dropped_is_flagged(self):
        issues = preservation_issues(
            "The study at Stanford found the effect.",
            "The study at the university found the effect.",
        )
        self.assertTrue(any(i["kind"] == "entity" and i["snippet"] == "Stanford" for i in issues))


class RankCandidatesTest(unittest.TestCase):
    def test_faithful_candidate_beats_fluent_fact_drifter(self):
        orig = "The study ran for 12 weeks and covered 800 participants, with a 90% response rate."
        faithful = "The study ran for 12 weeks, covering 800 participants with a 90% response rate."
        fluent_but_drifting = (
            "The study unfolded over several months, sweeping in hundreds of "
            "participants and drawing a strong response from nearly everyone."
        )
        best, warnings = rank_candidates(orig, [fluent_but_drifting, faithful])
        self.assertEqual(best, faithful)
        self.assertTrue(any(w["kind"] == "fact_drift" for w in warnings))

    def test_empty_pool_returns_none(self):
        best, warnings = rank_candidates("Some text.", [])
        self.assertIsNone(best)
        self.assertEqual(warnings, [])

    def test_highest_scoring_faithful_wins(self):
        orig = "The device weighs 2 kg and costs 500 dollars."
        a = "The device weighs 2 kg and costs 500 dollars, all in all."
        b = "The device weighs 2 kg and costs 500 dollars in total, so there we are."
        best, _ = rank_candidates(orig, [a, b])
        # Both are faithful; the higher naturalness score wins.
        self.assertIn(best, (a, b))

    def test_all_candidates_drift_warns_but_returns_best(self):
        orig = "The device weighs 2 kg and costs 500 dollars."
        c1 = "The device is light and affordable."
        c2 = "The device weighs almost nothing and is cheap."
        best, warnings = rank_candidates(orig, [c1, c2])
        self.assertIsNotNone(best)
        self.assertTrue(any(w["kind"] == "fact_drift" for w in warnings))


if __name__ == "__main__":
    unittest.main()
