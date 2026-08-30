"""Tests for StealthGPT API integration."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer import stealthgpt
from naturalizer.engine import Naturalizer


class StealthGPTTest(unittest.TestCase):
    def test_key_lookup_finds_various_env_vars(self):
        with patch.dict(os.environ, {"STEALTHGPT_API_KEY": "test-key-123"}, clear=True):
            self.assertEqual(stealthgpt.get_api_key(), "test-key-123")
            self.assertTrue(stealthgpt.is_configured())

        with patch.dict(os.environ, {"stealthgpt_ai_api_key": "alias-key-456"}, clear=True):
            self.assertEqual(stealthgpt.get_api_key(), "alias-key-456")
            self.assertTrue(stealthgpt.is_configured())

        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(stealthgpt.get_api_key())
            self.assertFalse(stealthgpt.is_configured())

    def test_base_url_normalization(self):
        with patch.dict(os.environ, {"STEALTHGPT_BASE_URL": "https://stealthgpt.ai/api/stealthify"}, clear=True):
            self.assertEqual(stealthgpt.get_base_url(), "https://www.stealthgpt.ai/api/stealthify")

        with patch.dict(os.environ, {"STEALTHGPT_BASE_URL": "https://www.stealthgpt.ai/api/stealthify"}, clear=True):
            self.assertEqual(stealthgpt.get_base_url(), "https://www.stealthgpt.ai/api/stealthify")

    def test_stealthify_empty_text(self):
        with patch.dict(os.environ, {"STEALTHGPT_API_KEY": "dummy"}, clear=True):
            res, meta, err = stealthgpt.stealthify("")
            self.assertEqual(res, "")
            self.assertIsNone(err)

    @patch("urllib.request.urlopen")
    def test_stealthify_successful_mock(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"result": "Humanized prose here.", "howLikelyToBeDetected": 92, "wordsSpent": 15}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with patch.dict(os.environ, {"STEALTHGPT_API_KEY": "dummy"}, clear=True):
            res, meta, err = stealthgpt.stealthify("AI text to rewrite.", style="academic")
            self.assertEqual(res, "Humanized prose here.")
            self.assertIsNone(err)
            self.assertEqual(meta["provider"], "stealthgpt")
            self.assertEqual(meta["howLikelyToBeDetected"], 92)

    @patch("urllib.request.urlopen")
    def test_engine_uses_stealthgpt_when_configured(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"result": "Polished text output.", "howLikelyToBeDetected": 95, "wordsSpent": 10}'
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with patch.dict(os.environ, {"STEALTHGPT_API_KEY": "dummy"}, clear=True):
            engine = Naturalizer()
            res = engine.naturalize("Original input draft.", provider="stealthgpt")
            self.assertTrue(res.llm_used)
            self.assertEqual(res.llm_provider, "stealthgpt")
            self.assertEqual(res.llm_rewritten, "Polished text output.")


if __name__ == "__main__":
    unittest.main()
