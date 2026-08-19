"""Tests for the word-level diff module."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.diff import reconstruct, word_diff


def _assert_reconstructs(testcase, before, after, ops):
    """The diff must be lossless on both sides."""
    testcase.assertEqual(reconstruct(ops, ("same", "del")), before)
    testcase.assertEqual(reconstruct(ops, ("same", "add")), after)


class WordDiffTest(unittest.TestCase):
    def test_identical_texts_are_all_same(self):
        text = "The wiring came loose behind the counter."
        ops = word_diff(text, text)
        self.assertTrue(ops)
        self.assertTrue(all(op["type"] == "same" for op in ops))
        _assert_reconstructs(self, text, text, ops)

    def test_deletion(self):
        before = "The red fox jumps over the lazy dog."
        after = "The fox jumps over the dog."
        ops = word_diff(before, after)
        dels = [op["text"] for op in ops if op["type"] == "del"]
        self.assertIn("red ", dels)
        self.assertIn("lazy ", dels)
        self.assertFalse([op for op in ops if op["type"] == "add"])
        _assert_reconstructs(self, before, after, ops)

    def test_addition(self):
        before = "The fox jumps."
        after = "The red fox jumps high."
        ops = word_diff(before, after)
        adds = [op["text"] for op in ops if op["type"] == "add"]
        self.assertIn("red ", adds)
        self.assertTrue(any(t.startswith("high") for t in adds))
        _assert_reconstructs(self, before, after, ops)

    def test_replacement(self):
        before = "Furthermore, the data was noisy."
        after = "Beyond that, the data was messy."
        ops = word_diff(before, after)
        types = {op["type"] for op in ops}
        self.assertIn("del", types)
        self.assertIn("add", types)
        _assert_reconstructs(self, before, after, ops)

    def test_multiple_changes_preserve_order(self):
        before = "one two three four five"
        after = "one three five six"
        ops = word_diff(before, after)
        # Reconstructed order must match the originals.
        self.assertEqual(
            "".join(op["text"] for op in ops if op["type"] != "add").rstrip(),
            before,
        )
        _assert_reconstructs(self, before, after, ops)

    def test_empty_sides(self):
        ops = word_diff("", "")
        self.assertEqual(ops, [])
        ops = word_diff("hello world", "")
        self.assertTrue(all(op["type"] == "del" for op in ops))
        ops = word_diff("", "hello world")
        self.assertTrue(all(op["type"] == "add" for op in ops))

    def test_whitespace_only_difference_has_no_word_changes(self):
        before = "a  b"   # double space
        after = "a b"     # single space
        ops = word_diff(before, after)
        # Only a whitespace del/add may appear — never a word change.
        for op in ops:
            if op["type"] in ("del", "add"):
                self.assertTrue(op["text"].strip() == "")
        _assert_reconstructs(self, before, after, ops)

    def test_large_text_uses_fallback_without_crashing(self):
        # 2,100 tokens each side blows past the word-level cell budget, so the
        # sentence-level fallback (or degenerate path) kicks in.
        before = " ".join(f"word{i} is a filler phrase here." for i in range(700))
        after = " ".join(f"word{i} reads much better now." for i in range(700))
        ops = word_diff(before, after)
        self.assertIsInstance(ops, list)
        _assert_reconstructs(self, before, after, ops)


if __name__ == "__main__":
    unittest.main()
