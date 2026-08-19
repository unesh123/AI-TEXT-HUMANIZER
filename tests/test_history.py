"""Tests for the user-history persistence module (hermetic, temp state dir)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer import history


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"NATURALIZER_STATE_DIR": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        history.clear()

    def test_save_and_list_roundtrip(self):
        eid = history.save(
            "Input text.", "Output text.", 87,
            style="business", mode="naturalize", provider="claude",
            llm_used=True, plan="pro",
        )
        self.assertIsNotNone(eid)
        entries = history.list_entries()
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["input"], "Input text.")
        self.assertEqual(e["output"], "Output text.")
        self.assertEqual(e["score"], 87.0)
        self.assertEqual(e["style"], "business")
        self.assertEqual(e["mode"], "naturalize")
        self.assertEqual(e["provider"], "claude")
        self.assertTrue(e["llm_used"])
        self.assertEqual(e["plan"], "pro")
        self.assertIn("iso", e)

    def test_empty_entries_not_saved(self):
        self.assertIsNone(history.save("", "output", 50))
        self.assertIsNone(history.save("input", "", 50))
        self.assertEqual(history.list_entries(), [])

    def test_newest_first_and_limit(self):
        for i in range(5):
            history.save(f"input {i}", f"output {i}", float(i))
        entries = history.list_entries()
        self.assertEqual(len(entries), 5)
        self.assertEqual(entries[0]["input"], "input 4")  # newest first
        self.assertEqual(history.list_entries(limit=2), entries[:2])

    def test_get_remove_clear(self):
        eid = history.save("a", "b", 60)
        self.assertEqual(history.get(eid)["id"], eid)
        self.assertTrue(history.remove(eid))
        self.assertFalse(history.remove(eid))  # already gone
        self.assertIsNone(history.get(eid))
        history.save("c", "d", 70)
        self.assertEqual(history.clear(), 1)
        self.assertEqual(history.list_entries(), [])

    def test_cap_at_max_entries(self):
        for i in range(history.MAX_ENTRIES + 10):
            history.save(f"input {i}", f"output {i}", 50)
        self.assertEqual(
            len(history.list_entries(limit=10_000)), history.MAX_ENTRIES
        )

    def test_extra_fields_and_perfect_mode(self):
        history.save("in", "out", 90, mode="perfect", extra={"passes": 3})
        e = history.list_entries()[0]
        self.assertEqual(e["mode"], "perfect")
        self.assertEqual(e["passes"], 3)

    def test_public_whitelist(self):
        history.save("in", "out", 90, mode="perfect", extra={"passes": 2, "secret": "x"})
        pub = history.public(history.list_entries()[0])
        self.assertEqual(pub["passes"], 2)
        self.assertNotIn("secret", pub)
        for key in ("input", "output", "score", "style", "mode", "iso"):
            self.assertIn(key, pub)


if __name__ == "__main__":
    unittest.main()
