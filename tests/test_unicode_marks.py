"""Tests for invisible-Unicode watermark hygiene (watermarks-remover Layer A
borrowing)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.detectors import analyze
from naturalizer.transforms import rewrite as det_rewrite
from naturalizer.unicode_marks import check_unicode_marks, find_marks, strip_marks

HUMAN_LIKE = (
    "I got the new coffee machine set up this morning, and it turns out the "
    "old one wasn't broken after all. The wiring had come loose behind the "
    "counter. A quick screwdriver job fixed it in ten minutes, and now the "
    "office smells like a cafe. Colleagues keep wandering over, cup in hand, "
    "hoping I'll brew another pot. I suppose I should have checked the "
    "obvious before ordering a replacement, but the new one is nice anyway."
)


class FindMarksTest(unittest.TestCase):
    def test_clean_text_has_no_marks(self):
        self.assertEqual(find_marks(HUMAN_LIKE), {})

    def test_counts_zero_width_space(self):
        self.assertEqual(find_marks("a\u200bb"), {"\u200b": 1})

    def test_counts_multiple_classes(self):
        marks = find_marks("a\u200bb c\u200cc d\u200dd")
        self.assertEqual(marks, {"\u200b": 1, "\u200c": 1, "\u200d": 1})


class StripMarksTest(unittest.TestCase):
    def test_removes_invisible_only(self):
        clean, removed = strip_marks("hello\u200b world\u200e!")
        self.assertEqual(clean, "hello world!")
        self.assertEqual(removed, ["ZERO WIDTH SPACE \u00d71", "LEFT-TO-RIGHT MARK \u00d71"])

    def test_visible_text_untouched(self):
        clean, removed = strip_marks(HUMAN_LIKE)
        self.assertEqual(clean, HUMAN_LIKE)
        self.assertEqual(removed, [])

    def test_punctuation_and_accents_survive(self):
        clean, _ = strip_marks("Caf\u00e9 — it's great!")
        self.assertEqual(clean, "Caf\u00e9 — it's great!")


class CheckUnicodeMarksTest(unittest.TestCase):
    def test_no_issue_on_clean_text(self):
        self.assertEqual(check_unicode_marks(HUMAN_LIKE), [])

    def test_zero_width_is_medium(self):
        issues = check_unicode_marks("a\u200bb")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, "unicode")
        self.assertEqual(issues[0].severity, "medium")

    def test_bidi_override_is_high(self):
        issues = check_unicode_marks("text\u202eoverridden")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "high")


class AnalyzeIntegrationTest(unittest.TestCase):
    def test_analyze_flags_invisible_marks(self):
        report = analyze("This is fine prose but it hides a mark\u200b inside.")
        kinds = [i.kind for i in report.issues]
        self.assertIn("unicode", kinds)

    def test_analyze_clean_text_has_no_unicode_issue(self):
        report = analyze(HUMAN_LIKE)
        self.assertNotIn("unicode", [i.kind for i in report.issues])


class RewriteIntegrationTest(unittest.TestCase):
    def test_rewrite_strips_marks(self):
        out, _, changed = det_rewrite("The plan was simple\u200b and it worked well.", intensity=0.5)
        self.assertTrue(changed)
        self.assertNotIn("\u200b", out)
        self.assertIn("The plan was simple and it worked well", out)

    def test_rewrite_leaves_clean_text_alone(self):
        out, _, changed = det_rewrite(HUMAN_LIKE, intensity=0.5)
        self.assertNotIn("unicode", [i.kind for i in analyze(out).issues])


if __name__ == "__main__":
    unittest.main()
