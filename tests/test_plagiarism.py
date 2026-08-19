"""Tests for the local similarity (plagiarism) checker.

All hermetic — pure string math, no network, no keys.
"""

import unittest

from naturalizer.plagiarism import MatchSpan, PlagiarismReport, check

VERBATIM = (
    "Technology has quietly permeated every aspect of our daily lives. "
    "From the way we communicate to the way we work, digital tools have "
    "reshaped how organizations operate. Businesses that fail to adapt "
    "risk falling behind in an increasingly competitive landscape."
)

REWRITE = (
    "Tech now sits inside almost every part of everyday life. How we talk, "
    "how we work, how companies run their operations — all of it has been "
    "reshaped by digital tools. Firms that cannot keep up risk losing "
    "ground in a market that keeps getting tougher."
)

ORIGINAL_TEXT = "This is a completely unrelated paragraph about baking bread."


class PlagiarismCheckTest(unittest.TestCase):
    def test_verbatim_copy_scores_high(self):
        r = check(VERBATIM, [VERBATIM])
        self.assertEqual(r.verdict, "high")
        self.assertGreaterEqual(r.score, 80)
        self.assertTrue(r.matching)  # matching spans reported

    def test_heavy_paraphrase_scores_lower(self):
        r = check(REWRITE, [VERBATIM])
        # Paraphrase shares few 8-grams, so it must NOT read as verbatim copying.
        self.assertLess(r.score, 50)
        self.assertNotEqual(r.verdict, "high")

    def test_unrelated_text_scores_low(self):
        r = check(ORIGINAL_TEXT, [VERBATIM])
        self.assertEqual(r.verdict, "low")
        self.assertLess(r.score, 20)

    def test_empty_text(self):
        r = check("   ", [VERBATIM])
        self.assertEqual(r.score, 0)
        self.assertEqual(r.verdict, "low")
        self.assertEqual(r.word_count, 0)

    def test_no_refs(self):
        r = check(VERBATIM, [])
        self.assertEqual(r.verdict, "low")
        self.assertIn("No reference texts provided", r.note)

    def test_short_query_uses_smaller_shingles(self):
        # Under the short threshold a smaller shingle size is used so short
        # passages still produce enough shingles to compare.
        short = "The quick brown fox jumps over the lazy dog."
        r = check(short, [short])
        self.assertEqual(r.verdict, "high")

    def test_matching_spans_carry_ref_indices(self):
        ref_a = "Unrelated first source about gardening."
        r = check(VERBATIM, [ref_a, VERBATIM])
        self.assertEqual(r.verdict, "high")
        self.assertTrue(any(1 in m.refs for m in r.matching))

    def test_per_ref_breakdown(self):
        r = check(VERBATIM, [ORIGINAL_TEXT, VERBATIM])
        self.assertEqual(len(r.per_ref), 2)
        self.assertGreater(r.per_ref[1]["score"], r.per_ref[0]["score"])

    def test_to_dict_roundtrip(self):
        r = check(VERBATIM, [VERBATIM])
        d = r.to_dict()
        self.assertEqual(set(d), {"score", "verdict", "word_count", "per_ref", "matching", "note"})
        self.assertEqual(d["verdict"], "high")
        self.assertIsInstance(d["matching"], list)
        self.assertIn("sentence", d["matching"][0])
        self.assertIn("snippet", d["matching"][0])
        self.assertIn("refs", d["matching"][0])


class PlagiarismEdgeTest(unittest.TestCase):
    def test_medium_threshold(self):
        # A query sharing only a third of its shingles lands in "medium",
        # not "high". 9 of the base's words open the query; the rest differ.
        base = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda "
        base += "mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega"
        head = base.split()[:9]
        query = " ".join(head) + " then a string of completely different words "
        query += "that share nothing with the source at all beyond the opener"
        r = check(query, [base])
        self.assertEqual(r.verdict, "medium")
        self.assertGreaterEqual(r.score, 20)
        self.assertLess(r.score, 50)

    def test_dataclass_defaults(self):
        m = MatchSpan(sentence="s", snippet="x", refs=[0])
        self.assertEqual(m.to_dict()["refs"], [0])
        rep = PlagiarismReport(score=5, verdict="low", word_count=3, per_ref=[])
        self.assertEqual(rep.matching, [])
        self.assertEqual(rep.note, "")


if __name__ == "__main__":
    unittest.main()
