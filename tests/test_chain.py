"""Tests for the translation-chain humanizer (naturalizer/chain.py).

All LLM calls are mocked — the suite never touches the network or real
keys. Provider env vars are cleared/set per test.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer import chain


def _env(**overrides):
    return mock.patch.dict(os.environ, overrides, clear=True)


class ChainTest(unittest.TestCase):
    def test_four_hops_with_history_and_secondary_engine(self):
        """EN->中文->日本語 on primary, ->芬兰语->EN on the hcnsec secondary,
        with hop 2 carrying hop 1 as conversation history."""
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            OPENAI_CODEX_API_KEY="cdx-test",
            OPENAI_CODEX_BASE_URL="https://api.hcnsec.cn/v1",
            OPENAI_CODEX_MODEL="DeepSeek-V4-Pro",
        ):
            calls = []
            chat_cfg = {}

            def fake_chat(cfg, messages, temperature=0.8, timeout=None):
                calls.append((cfg, list(messages), temperature))
                chat_cfg["last"] = cfg
                instruction = messages[-1]["content"].split("\n")[0]
                # Echo which hop this is so we can verify the chain order.
                if instruction.startswith("翻译为中文"):
                    return "第一步：中文改写输出。"
                if instruction.startswith("翻译为日语"):
                    return "第二步：日本語改写の出力。"
                if instruction.startswith("翻译为芬兰语"):
                    return "Kolmas vaihe: suomennos."
                return "Final English rewrite from the chain."

            with mock.patch("naturalizer.chain.llm.chat", side_effect=fake_chat) as chat:
                out = chain.run_chain("Original draft here.", style="academic")

        self.assertEqual(out, "Final English rewrite from the chain.")
        self.assertEqual(len(calls), 4)

        # Hop 1: primary provider (claude config), humanize prompt, temp 1.3
        cfg1, msgs1, temp1 = calls[0]
        self.assertEqual(cfg1["base"], "https://mwapi.example")
        self.assertIn("翻译为中文", msgs1[-1]["content"])
        self.assertEqual(temp1, chain._TEMPERATURE)

        # Hop 2: carries hop 1 as history (user + assistant turns)
        cfg2, msgs2, _ = calls[1]
        self.assertEqual(cfg2["base"], "https://mwapi.example")
        self.assertIn("翻译为日语", msgs2[-1]["content"])
        self.assertEqual(msgs2[-2]["role"], "assistant")
        self.assertIn("第一步", msgs2[-2]["content"])

        # Hops 3-4: the hcnsec secondary engine, plain translation prompts
        cfg3, msgs3, _ = calls[2]
        self.assertEqual(cfg3["base"], "https://api.hcnsec.cn/v1")
        self.assertEqual(cfg3["model"], "DeepSeek-V4-Pro")
        self.assertIn("翻译为芬兰语", msgs3[-1]["content"])
        self.assertNotIn("去掉 AI 味道", msgs3[-1]["content"])
        cfg4, msgs4, _ = calls[3]
        self.assertEqual(cfg4["base"], "https://api.hcnsec.cn/v1")
        self.assertIn("英语", msgs4[-1]["content"])

    def test_primary_down_hop1_retries_on_secondary(self):
        """The reported bug: the primary gateway is rate-limited, so hops
        1-2 must retry on the secondary engine instead of killing the
        whole chain and dropping to deterministic."""
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            OPENAI_CODEX_API_KEY="ck-test",
            OPENAI_CODEX_BASE_URL="https://api.hcnsec.cn/v1",
            OPENAI_CODEX_MODEL="DeepSeek-V4-Pro",
        ):
            calls = []

            def fake_chat(cfg, messages, temperature=0.8, timeout=None):
                calls.append(cfg["base"])
                instruction = messages[-1]["content"].split("\n")[0]
                if cfg["base"] == "https://mwapi.example":
                    # Primary is down on the humanize hops.
                    return None
                if instruction.startswith("翻译为中文"):
                    return "中文改写输出"
                if instruction.startswith("翻译为日语"):
                    return "日本語の出力"
                if instruction.startswith("翻译为芬兰语"):
                    return "Suomennos."
                return "English out."

            with mock.patch("naturalizer.chain.llm.chat", side_effect=fake_chat) as chat:
                out = chain.run_chain("Draft.")

        self.assertEqual(out, "English out.")
        # Hop 1 tried on the primary (dead), then retried on the
        # secondary — and the chain still completed all four hops.
        self.assertIn("https://mwapi.example", calls)
        self.assertIn("https://api.hcnsec.cn/v1", calls)
        self.assertGreaterEqual(len(calls), 5)

    def test_secondary_falls_back_to_primary(self):
        """No secondary configured -> all four hops use the primary."""
        with _env(HINAA_CLAUDE_API_KEY="sk-test", HINAA_CLAUDE_BASE_URL="https://mwapi.example"):
            def fake_chat(cfg, messages, temperature=0.8, timeout=None):
                instruction = messages[-1]["content"].split("\n")[0]
                if instruction.startswith("翻译为中文"):
                    return "中文改写"
                if instruction.startswith("翻译为日语"):
                    return "日本語の出力"
                if instruction.startswith("翻译为芬兰语"):
                    return "Suomennos."
                return "English out."

            with mock.patch("naturalizer.chain.llm.chat", side_effect=fake_chat) as chat:
                out = chain.run_chain("Draft.")

        self.assertEqual(out, "English out.")
        bases = {c.args[0]["base"] for c in chat.call_args_list}
        self.assertEqual(bases, {"https://mwapi.example"})

    def test_cx_gateway_can_be_secondary(self):
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            CX_GATEWAY_API_KEY="gk-test",
            CX_GATEWAY_BASE_URL="https://gateway.example",
            CX_GATEWAY_MODEL="cx/gpt-5.6-sol",
        ):
            def fake_chat(cfg, messages, temperature=0.8, timeout=None):
                instruction = messages[-1]["content"].split("\n")[0]
                if instruction.startswith("翻译为中文"):
                    return "中文"
                if instruction.startswith("翻译为日语"):
                    return "日本語"
                if instruction.startswith("翻译为芬兰语"):
                    return "Suomi."
                return "English."

            with mock.patch("naturalizer.chain.llm.chat", side_effect=fake_chat):
                out = chain.run_chain("Draft.")

        self.assertEqual(out, "English.")

    def test_no_providers_returns_none(self):
        with _env():
            self.assertIsNone(chain.run_chain("Draft."))

    def test_hop_failure_returns_none(self):
        with _env(HINAA_CLAUDE_API_KEY="sk-test", HINAA_CLAUDE_BASE_URL="https://mwapi.example"):
            def fake_chat(cfg, messages, temperature=0.8, timeout=None):
                instruction = messages[-1]["content"].split("\n")[0]
                if instruction.startswith("翻译为中文"):
                    return "中文改写"
                return None  # hop 2 fails

            with mock.patch("naturalizer.chain.llm.chat", side_effect=fake_chat):
                self.assertIsNone(chain.run_chain("Draft."))

    def test_style_label_reaches_final_hop(self):
        with _env(HINAA_CLAUDE_API_KEY="sk-test", HINAA_CLAUDE_BASE_URL="https://mwapi.example"):
            seen = {}

            def fake_chat(cfg, messages, temperature=0.8, timeout=None):
                seen["last"] = messages[-1]["content"]
                instruction = messages[-1]["content"].split("\n")[0]
                if instruction.startswith("翻译为中文"):
                    return "中文"
                if instruction.startswith("翻译为日语"):
                    return "日本語"
                if instruction.startswith("翻译为芬兰语"):
                    return "Suomi."
                return "Business English."

            with mock.patch("naturalizer.chain.llm.chat", side_effect=fake_chat):
                chain.run_chain("Draft.", style="business")

        self.assertIn("商务风格", seen["last"])


if __name__ == "__main__":
    unittest.main()
