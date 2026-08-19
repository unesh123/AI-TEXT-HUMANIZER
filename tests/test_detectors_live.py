"""Tests for the live third-party detector clients (GPTZero, ZeroGPT) and
the server's /api/detectors/scan endpoint.

All HTTP is mocked via urllib.request.urlopen — no real keys, no network.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.testutil  # noqa: F401 - scrub env + default plan to pro

from naturalizer import detectors_live
from naturalizer.feedback import scan_live


class _JsonResponse:
    """Minimal urllib response: read() returns encoded JSON."""

    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


class GptzeroClientTest(unittest.TestCase):
    def test_ai_document(self):
        with mock.patch.dict(os.environ, {"GPTZERO_API_KEY": "gz-test"}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _JsonResponse(
                    {
                        "documents": [
                            {
                                "completely_generated_prob": 0.96,
                                "document_classification": "AI_ONLY",
                            }
                        ]
                    }
                )
                result = detectors_live.check_gptzero("some text")
        self.assertEqual(result["score"], 96)
        self.assertEqual(result["verdict"], "AI")
        self.assertIsNone(result["error"])
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://api.gptzero.me/v2/predict/text")
        self.assertEqual(request.get_header("X-api-key"), "gz-test")
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent["document"], "some text")

    def test_human_document(self):
        with mock.patch.dict(os.environ, {"GPTZERO_API_KEY": "gz-test"}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _JsonResponse(
                    {"documents": [{"completely_generated_prob": 0.05}]}
                )
                result = detectors_live.check_gptzero("some text")
        self.assertEqual(result["score"], 5)
        self.assertEqual(result["verdict"], "human")

    def test_missing_key_reports_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            result = detectors_live.check_gptzero("some text")
        self.assertIsNone(result["score"])
        self.assertIn("GPTZERO_API_KEY", result["error"])

    def test_network_error_is_honest(self):
        with mock.patch.dict(os.environ, {"GPTZERO_API_KEY": "gz-test"}, clear=True):
            with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")):
                result = detectors_live.check_gptzero("some text")
        self.assertIsNone(result["score"])
        self.assertIn("request failed", result["error"])

    def test_malformed_response_is_honest(self):
        with mock.patch.dict(os.environ, {"GPTZERO_API_KEY": "gz-test"}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _JsonResponse({"unexpected": True})
                result = detectors_live.check_gptzero("some text")
        self.assertIsNone(result["score"])
        self.assertIn("no AI probability", result["error"])


class ZerogptClientTest(unittest.TestCase):
    def test_ai_document(self):
        with mock.patch.dict(os.environ, {"ZEROGPT_API_KEY": "zg-test"}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _JsonResponse(
                    {"data": {"fakePercentage": "87.5%", "text": "..."}}
                )
                result = detectors_live.check_zerogpt("some text")
        self.assertEqual(result["score"], 88)
        self.assertEqual(result["verdict"], "AI")
        self.assertIsNone(result["error"])
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://api.zerogpt.com/api/detect/detectText")
        self.assertEqual(request.get_header("X-api-key"), "zg-test")
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent["input_text"], "some text")

    def test_human_document(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _JsonResponse(
                    {"data": {"fakePercentage": 12}}
                )
                result = detectors_live.check_zerogpt("some text")
        self.assertEqual(result["score"], 12)
        self.assertEqual(result["verdict"], "human")

    def test_keyless_still_works(self):
        # ZeroGPT's public endpoint works without a key (the web tool uses
        # it) — the client must not require ZEROGPT_API_KEY to attempt it.
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _JsonResponse({"data": {"fakePercentage": 40}})
                result = detectors_live.check_zerogpt("some text")
        self.assertEqual(result["score"], 40)
        self.assertIsNone(result["error"])
        request = urlopen.call_args[0][0]
        self.assertIsNone(request.get_header("X-api-key"))

    def test_malformed_response_is_honest(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _JsonResponse({"nope": True})
                result = detectors_live.check_zerogpt("some text")
        self.assertIsNone(result["score"])
        self.assertIn("fakePercentage", result["error"])


class ScanLiveTest(unittest.TestCase):
    def test_skips_unconfigured_detectors(self):
        # No keys: scan_live must not hit the network at all.
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                results = scan_live("some text")
        self.assertEqual(results, [])
        urlopen.assert_not_called()

    def test_runs_only_configured_detectors(self):
        with mock.patch.dict(os.environ, {"GPTZERO_API_KEY": "gz-test"}, clear=True):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _JsonResponse(
                    {"documents": [{"completely_generated_prob": 0.8}]}
                )
                results = scan_live("some text")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "gptzero")
        self.assertEqual(results[0]["score"], 80)


if __name__ == "__main__":
    unittest.main()
