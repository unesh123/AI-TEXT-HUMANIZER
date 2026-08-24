"""Tests for the free/pro plan structure and its server-side gating."""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.plans import (
    PLANS,
    check_word_quota,
    current_plan,
    plan_features,
    record_usage,
    status,
    words_used_today,
)
from server import Handler, ThreadingHTTPServer

ENV_FREE = {"NATURALIZER_PLAN": "free"}


class PlansModuleTest(unittest.TestCase):

    def test_word_quota_accounting(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {**ENV_FREE, "NATURALIZER_STATE_DIR": tmp}
        ):
            cap = PLANS["free"]["features"]["words_per_day"]
            # Free: quota enforced, usage recorded.
            allowed, err = check_word_quota(10)
            self.assertTrue(allowed)
            record_usage(10)
            self.assertEqual(words_used_today(), 10)
            allowed, err = check_word_quota(cap)  # exactly fills the day
            self.assertFalse(allowed)
            self.assertIn("Free plan daily limit", err)
            self.assertEqual(status()["words_remaining_today"], cap - 10)

    def test_pro_plan_is_unlimited(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"NATURALIZER_PLAN": "pro", "NATURALIZER_STATE_DIR": tmp}
        ):
            allowed, err = check_word_quota(10_000_000)
            self.assertTrue(allowed)
            self.assertIsNone(err)
            record_usage(10_000_000)
            self.assertIsNone(status()["words_remaining_today"])

    def test_status_shape(self):
        with mock.patch.dict(os.environ, ENV_FREE):
            s = status()
            self.assertEqual(s["name"], "free")
            self.assertIn("features", s)
            self.assertIn("words_used_today", s)
            self.assertIn("upgrade_hint", s)


class ServerPlanGatingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _free_env(self, tmp):
        return {**ENV_FREE, "NATURALIZER_STATE_DIR": tmp}



    def test_free_plan_blocks_batch(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, self._free_env(tmp)
        ):
            status_code, body = self._post(
                "/api/batch", {"texts": ["One doc.", "Two docs."]}
            )
            self.assertEqual(status_code, 402)
            self.assertIn("Batch mode is a Pro feature", body["error"])

    def test_free_plan_caps_intensity(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, self._free_env(tmp)
        ):
            status_code, body = self._post(
                "/api/naturalize",
                {"text": "Furthermore, the data was noisy.", "intensity": 1.0},
            )
            self.assertEqual(status_code, 200)
            self.assertEqual(body["intensity"], 0.5)

    def test_free_plan_enforces_word_cap(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, self._free_env(tmp)
        ):
            from naturalizer.plans import FREE_WORDS_PER_DAY

            big = " ".join(["word"] * (FREE_WORDS_PER_DAY + 50))
            status_code, body = self._post(
                "/api/naturalize", {"text": big, "use_llm": False}
            )
            self.assertEqual(status_code, 429)
            self.assertIn("Free plan daily limit", body["error"])

    def test_pro_plan_allows_everything(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"NATURALIZER_PLAN": "pro", "NATURALIZER_STATE_DIR": tmp}
        ):
            status_code, body = self._post(
                "/api/naturalize",
                {"text": "Furthermore, the data was noisy.", "intensity": 1.0},
            )
            self.assertEqual(status_code, 200)
            self.assertEqual(body["intensity"], 1.0)

    def test_status_reports_plan(self):
        with mock.patch.dict(os.environ, ENV_FREE):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/api/status", timeout=10
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data["plan"]["name"], "free")
        self.assertIn("features", data["plan"])


if __name__ == "__main__":
    unittest.main()
