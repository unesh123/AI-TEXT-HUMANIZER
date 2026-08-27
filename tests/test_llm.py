"""Tests for the LLM backend (Claude + CX GPT gateway providers).

All HTTP is mocked — these tests never touch the network or real keys.
Provider env vars are cleared/set per test so the suite stays hermetic even
though the project directory contains a real .env.local.
"""

import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer import llm

CLAUDE_KEYS = (
    "HINAA_CLAUDE_API_KEY",
    "HINAA_CLAUDE_BASE_URL",
    "HINAA_CLAUDE_MODEL",
)
CX_KEYS = (
    "CX_GATEWAY_API_KEY",
    "CX_GATEWAY_BASE_URL",
    "CX_GATEWAY_MODEL",
)
ALL_KEYS = CLAUDE_KEYS + CX_KEYS

OPENAI_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
GEMINI_KEYS = ("GEMINI_API_KEY", "GEMINI_BASE_URL", "GEMINI_MODEL")
QWEN_KEYS = ("HINAA_QWEN_API_KEY", "HINAA_QWEN_BASE_URL", "HINAA_QWEN_MODEL")
ROUTER_KEYS = ("AGENT_ROUTER_API_KEY", "AGENT_ROUTER_BASE_URL", "AGENT_ROUTER_MODEL")
CODEX_KEYS = ("OPENAI_CODEX_API_KEY", "OPENAI_CODEX_BASE_URL", "OPENAI_CODEX_MODEL")
HCNS_KEYS = ("HCNSEC_API_KEY", "HCNSEC_BASE_URL", "HCNSEC_MODEL")


def _env(**overrides):
    """Clear all provider env vars, then apply *overrides*."""
    return mock.patch.dict(os.environ, overrides, clear=True)


def _header(request, name: str) -> str:
    """Case-insensitive header lookup (http.client sends headers as-is)."""
    lower = name.lower()
    return next((v for k, v in request.headers.items() if k.lower() == lower), None)


def _fake_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return body

    return FakeResponse()


def _claude_payload(text: str = "Hello world.") -> dict:
    return {
        "model": "claude-sonnet-4-6",
        "max_tokens": llm._MAX_TOKENS,
        "system": llm._SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": f"Style: Academic\n\nDraft:\n{text}"}],
    }


def _cx_payload(text: str = "Hello world.") -> dict:
    return {
        "model": "cx/gpt-5.6-sol",
        "messages": [
            {"role": "system", "content": llm._SYSTEM_PROMPT},
            {"role": "user", "content": f"Style: Academic\n\nDraft:\n{text}"},
        ],
        "temperature": 0.8,
    }


class LlmConfigTest(unittest.TestCase):
    def test_nothing_configured(self):
        with _env():
            self.assertFalse(llm.llm_available())
            self.assertIsNone(llm.llm_provider_label())
            self.assertIsNone(llm.rewrite_with_llm("Hello."))

    def test_claude_only(self):
        with _env(HINAA_CLAUDE_API_KEY="sk-test", HINAA_CLAUDE_MODEL="claude-sonnet-4-6"):
            self.assertTrue(llm.llm_available())
            self.assertEqual(llm.llm_provider_label(), "claude (claude-sonnet-4-6)")
            self.assertEqual([p["name"] for p in llm.llm_providers()], ["claude"])

    def test_cx_only(self):
        with _env(CX_GATEWAY_API_KEY="gk-test", CX_GATEWAY_MODEL="cx/gpt-5.6-sol"):
            self.assertTrue(llm.llm_available())
            self.assertEqual(llm.llm_provider_label(), "cx (cx/gpt-5.6-sol)")
            self.assertEqual([p["name"] for p in llm.llm_providers()], ["cx"])

    def test_claude_first_when_both(self):
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            CX_GATEWAY_API_KEY="gk-test",
        ):
            self.assertEqual([p["name"] for p in llm.llm_providers()], ["claude", "cx"])

    def test_provider_selection(self):
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            CX_GATEWAY_API_KEY="gk-test",
        ):
            self.assertEqual([p["name"] for p in llm.llm_providers("claude")], ["claude"])
            self.assertEqual([p["name"] for p in llm.llm_providers("cx")], ["cx"])
            self.assertEqual(llm.llm_providers("bogus"), [])
            self.assertEqual([p["name"] for p in llm.llm_providers("CLAUDE")], ["claude"])
            self.assertTrue(llm.llm_available("cx"))
            self.assertEqual(llm.llm_provider_label("cx"), "cx (cx/gpt-5.6-sol)")

    def test_provider_selection_unconfigured(self):
        with _env(CX_GATEWAY_API_KEY="gk-test"):
            self.assertEqual(llm.llm_providers("claude"), [])
            self.assertFalse(llm.llm_available("claude"))
            self.assertIsNone(llm.llm_provider_label("claude"))

    def test_provider_choices_list(self):
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            CX_GATEWAY_API_KEY="gk-test",
        ):
            choices = llm.llm_provider_choices()
            by_name = {c["name"]: c for c in choices}
            self.assertEqual(by_name["auto"]["configured"], True)
            self.assertEqual(by_name["claude"]["configured"], True)
            self.assertEqual(by_name["cx"]["configured"], True)
            self.assertIn("claude", by_name["claude"]["label"])
        with _env():
            choices = llm.llm_provider_choices()
            by_name = {c["name"]: c for c in choices}
            self.assertEqual(by_name["claude"]["configured"], False)
            self.assertEqual(by_name["cx"]["configured"], False)

    def test_openai_gemini_qwen_providers(self):
        with _env(
            OPENAI_API_KEY="oa-test",
            GEMINI_API_KEY="gm-test",
            HINAA_QWEN_API_KEY="qw-test",
        ):
            names = [p["name"] for p in llm.llm_providers()]
            self.assertEqual(names, ["openai", "gemini", "qwen"])
            self.assertEqual(llm.llm_provider_label("gemini"), "gemini (gemini-2.0-flash)")
            self.assertEqual(llm.llm_providers("openai")[0]["config"]["base"],
                             "https://api.openai.com/v1")
            self.assertEqual(llm.llm_providers("qwen")[0]["config"]["base"],
                             "https://dashscope.aliyuncs.com/compatible-mode/v1")

    def test_router_requires_base_url(self):
        with _env(AGENT_ROUTER_API_KEY="ar-test"):
            self.assertEqual(llm.llm_providers("router"), [])
        with _env(AGENT_ROUTER_API_KEY="ar-test", AGENT_ROUTER_BASE_URL="https://router.example"):
            cfg = llm.llm_providers("router")[0]["config"]
            self.assertEqual(cfg["base"], "https://router.example")

    def test_codex_and_hcns_providers(self):
        with _env(
            OPENAI_CODEX_API_KEY="cdx-test",
            OPENAI_CODEX_MODEL="DeepSeek-V4-Pro",
            HCNSEC_API_KEY="hcn-test",
        ):
            names = [p["name"] for p in llm.llm_providers()]
            self.assertEqual(names, ["codex", "hcns"])
            self.assertEqual(llm.llm_providers("codex")[0]["model"], "DeepSeek-V4-Pro")
            self.assertEqual(llm.llm_providers("hcns")[0]["config"]["base"],
                             "https://api.hcnsec.cn/v1")

    def test_auto_order_claude_first(self):
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            OPENAI_API_KEY="oa-test",
            GEMINI_API_KEY="gm-test",
            CX_GATEWAY_API_KEY="gk-test",
        ):
            names = [p["name"] for p in llm.llm_providers()]
            self.assertEqual(names[0], "claude")
            self.assertIn("openai", names)
            self.assertIn("gemini", names)
            self.assertIn("cx", names)

    def test_last_good_provider_goes_first_on_auto(self):
        # After a provider actually serves a rewrite, "auto" should try it
        # first instead of re-walking the whole chain (which cost tens of
        # seconds whenever earlier gateways were dead).
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://claude.example",
            OPENAI_API_KEY="oa-test",
            OPENAI_BASE_URL="https://openai.example",
        ):
            llm._remember_good("openai")
            names = [p["name"] for p in llm.llm_provider_chain("auto")]
            self.assertEqual(names[0], "openai")
            self.assertEqual(names[1], "claude")
            llm._remember_good(None)

    def test_last_good_unconfigured_ignored(self):
        # A remembered provider that is no longer configured must not
        # reorder the chain (or crash).
        with _env(HINAA_CLAUDE_API_KEY="sk-test", HINAA_CLAUDE_BASE_URL="https://claude.example"):
            llm._remember_good("qwen")  # not configured here
            names = [p["name"] for p in llm.llm_provider_chain("auto")]
            self.assertEqual(names, ["claude"])
            llm._remember_good(None)

    def test_explicit_provider_ignores_cache(self):
        # The cache only reorders "auto" — an explicit provider choice must
        # stay first (failover still appends the others).
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://claude.example",
            OPENAI_API_KEY="oa-test",
            OPENAI_BASE_URL="https://openai.example",
        ):
            llm._remember_good("openai")
            names = [p["name"] for p in llm.llm_provider_chain("claude")]
            self.assertEqual(names[0], "claude")
            llm._remember_good(None)

    def test_instruction_is_attached_to_system_prompt(self):
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            HINAA_CLAUDE_MODEL="claude-sonnet-4-6",
        ):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _fake_response({"content": [{"text": "Fixed."}]})
                out = llm.rewrite_with_llm(
                    "Hello world.",
                    style="academic",
                    instruction="The word 'leveraging' reads like corporate filler.",
                )
        self.assertEqual(out, "Fixed.")
        request = urlopen.call_args[0][0]
        sent = json.loads(request.data.decode("utf-8"))
        self.assertIn("extra direction from the last review pass", sent["system"])
        self.assertIn("corporate filler", sent["system"])

    def test_plain_register_guidance_appended_for_stiff_draft(self):
        # The verified human-writing memory is injected into the system
        # prompt when the draft reaches for formal words, naming the exact
        # words and their plain equivalents.
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            HINAA_CLAUDE_MODEL="claude-sonnet-4-6",
        ):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _fake_response({"content": [{"text": "Fixed."}]})
                out = llm.rewrite_with_llm(
                    "The utilization of this approach will subsequently "
                    "facilitate the commencement of our work.",
                    style="academic",
                )
        self.assertEqual(out, "Fixed.")
        request = urlopen.call_args[0][0]
        sent = json.loads(request.data.decode("utf-8"))
        self.assertIn("utilization", sent["system"])
        self.assertIn("-> use", sent["system"])
        self.assertIn("plain everyday words", sent["system"])

    def test_plain_draft_gets_no_guidance_noise(self):
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            HINAA_CLAUDE_MODEL="claude-sonnet-4-6",
        ):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _fake_response({"content": [{"text": "Fixed."}]})
                llm.rewrite_with_llm(
                    "I sorted the garage on Saturday and found three boxes of "
                    "old cables.",
                    style="academic",
                )
        request = urlopen.call_args[0][0]
        sent = json.loads(request.data.decode("utf-8"))
        self.assertNotIn("plain everyday words", sent["system"])


class LlmFailoverTest(unittest.TestCase):
    def test_failover_uses_short_timeout_after_first(self):
        # Dead gateways after the first in a chain must not be able to burn
        # the full 120s timeout each — failover attempts get a short cap so
        # "auto" reaches a working provider in seconds, not minutes.
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append((request.full_url, timeout))
            if "claude.example" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 503, "down", {}, None)
            return _fake_response({"choices": [{"message": {"content": "OK."}}]})

        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://claude.example",
            OPENAI_API_KEY="oa-test",
            OPENAI_BASE_URL="https://openai.example",
        ):
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen) as uo:
                out = llm.rewrite_with_llm("Hello.", style="academic")
        self.assertEqual(out, "OK.")
        self.assertEqual(calls[0][0].split("/")[2], "claude.example")
        # First provider gets the full default timeout, failover gets the cap.
        self.assertEqual(calls[0][1], llm._TIMEOUT)
        self.assertEqual(calls[1][1], llm._FAILOVER_TIMEOUT)

    def test_remembered_provider_cleared_on_failure(self):
        # When the remembered gateway fails, "auto" must not pin it forever.
        def fake_urlopen(request, timeout=None):
            if "openai.example" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 503, "down", {}, None)
            return _fake_response({"content": [{"text": "OK."}]})

        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://claude.example",
            OPENAI_API_KEY="oa-test",
            OPENAI_BASE_URL="https://openai.example",
        ):
            llm._remember_good("openai")
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                out = llm.rewrite_with_llm("Hello.", style="academic")
        self.assertEqual(out, "OK.")  # recovered on claude
        # The cache is updated to the provider that actually served — it
        # must not stay pinned to the dead remembered one.
        self.assertEqual(llm._LAST_GOOD_PROVIDER, "claude")


class LlmCallTest(unittest.TestCase):
    def test_anthropic_request_shape(self):
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            HINAA_CLAUDE_MODEL="claude-sonnet-4-6",
        ):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _fake_response({"content": [{"text": " Rewritten. "}]})

                out = llm.rewrite_with_llm("Hello world.", style="academic")

        self.assertEqual(out, "Rewritten.")
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://mwapi.example/v1/messages")
        self.assertEqual(_header(request, "x-api-key"), "sk-test")
        self.assertEqual(_header(request, "anthropic-version"), "2023-06-01")
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent, _claude_payload())

    def test_cx_request_shape(self):
        with _env(
            CX_GATEWAY_API_KEY="gk-test",
            CX_GATEWAY_BASE_URL="https://gateway.example",
            CX_GATEWAY_MODEL="cx/gpt-5.6-sol",
        ):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _fake_response(
                    {"choices": [{"message": {"content": " Rewritten. "}}]}
                )

                out = llm.rewrite_with_llm("Hello world.", style="academic")

    def test_rewrite_with_forced_provider(self):
        # Forcing "cx" must only call the CX gateway, never Claude.
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            CX_GATEWAY_API_KEY="gk-test",
            CX_GATEWAY_BASE_URL="https://gateway.example",
            CX_GATEWAY_MODEL="cx/gpt-5.6-sol",
        ):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _fake_response(
                    {"choices": [{"message": {"content": " Rewritten. "}}]}
                )

                out = llm.rewrite_with_llm("Hello world.", style="academic", provider="cx")

        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://gateway.example/v1/chat/completions")
        self.assertEqual(_header(request, "authorization"), "Bearer gk-test")
        self.assertEqual(out, "Rewritten.")

    def test_rewrite_unconfigured_provider_returns_none(self):
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            HINAA_CLAUDE_MODEL="claude-sonnet-4-6",
        ):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _fake_response(
                    {"choices": [{"message": {"content": "Rewritten."}}]}
                )
                # No CX key configured -> forcing "cx" finds nothing to call.
                out = llm.rewrite_with_llm("Hello world.", provider="cx")
        self.assertIsNone(out)
        urlopen.assert_not_called()

    def test_claude_failure_falls_back_to_cx(self):
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            CX_GATEWAY_API_KEY="gk-test",
            CX_GATEWAY_BASE_URL="https://gateway.example",
            CX_GATEWAY_MODEL="cx/gpt-5.6-sol",
        ):
            def fake_urlopen(request, *args, **kwargs):
                if request.full_url.endswith("/v1/messages"):
                    raise OSError("claude down")
                return _fake_response({"choices": [{"message": {"content": "From CX."}}]})

            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen) as urlopen:
                out = llm.rewrite_with_llm("Hello world.")

        self.assertEqual(out, "From CX.")
        urls = [c.args[0].full_url for c in urlopen.call_args_list]
        self.assertEqual(
            urls,
            [
                "https://mwapi.example/v1/messages",
                "https://gateway.example/v1/chat/completions",
            ],
        )

    def test_cx_failure_falls_back_to_claude(self):
        # The exact reported bug: the user picks cx, the gateway is
        # rate-limited (429), and the rewrite must fall through to Claude
        # instead of dying to the deterministic path. The returned text
        # is Claude's, and the details variant reports "claude" as the
        # actual serving provider.
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            CX_GATEWAY_API_KEY="gk-test",
            CX_GATEWAY_BASE_URL="https://gateway.example",
            CX_GATEWAY_MODEL="cx/gpt-5.6-sol",
        ):
            def fake_urlopen(request, *args, **kwargs):
                if request.full_url.endswith("/v1/chat/completions"):
                    raise OSError("cx gateway down")
                return _fake_response({"content": [{"text": "From Claude."}]})

            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen) as urlopen:
                out = llm.rewrite_with_llm("Hello world.", provider="cx")

        self.assertEqual(out, "From Claude.")
        urls = [c.args[0].full_url for c in urlopen.call_args_list]
        # CX (OpenAI-compatible) tried first, then the Claude fallback
        # (Anthropic Messages API) — and nothing else.
        self.assertEqual(
            urls,
            [
                "https://gateway.example/v1/chat/completions",
                "https://mwapi.example/v1/messages",
            ],
        )

    def test_claude_success_never_calls_cx(self):
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            CX_GATEWAY_API_KEY="gk-test",
            CX_GATEWAY_BASE_URL="https://gateway.example",
        ):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _fake_response({"content": [{"text": "From Claude."}]})

                out = llm.rewrite_with_llm("Hello world.")

        self.assertEqual(out, "From Claude.")
        self.assertEqual(urlopen.call_count, 1)

    def test_claude_openai_protocol_uses_chat_completions(self):
        # A proxy fronting Claude models: HINAA_CLAUDE_PROTOCOL=openai
        # (or absent) -> OpenAI-compatible path, Bearer auth.
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            HINAA_CLAUDE_PROTOCOL="openai",
            HINAA_CLAUDE_MODEL="claude-sonnet-4-6",
        ):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _fake_response(
                    {"choices": [{"message": {"content": " Via proxy. "}}]}
                )

                out = llm.rewrite_with_llm("Hello world.")

        self.assertEqual(out, "Via proxy.")
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://mwapi.example/v1/chat/completions")
        self.assertEqual(_header(request, "authorization"), "Bearer sk-test")

    def test_claude_anthropic_403_falls_back_to_openai_path(self):
        # The env says protocol=anthropic, but the base is an OpenAI-compatible
        # proxy that 403s /v1/messages: the provider must retry via
        # /v1/chat/completions and still return a rewrite.
        def fake_urlopen(request, *args, **kwargs):
            if request.full_url.endswith("/v1/messages"):
                raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", None, None)
            return _fake_response({"choices": [{"message": {"content": "From proxy."}}]})

        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            HINAA_CLAUDE_PROTOCOL="anthropic",
        ):
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen) as urlopen:
                out = llm.rewrite_with_llm("Hello world.")

        self.assertEqual(out, "From proxy.")
        urls = [c.args[0].full_url for c in urlopen.call_args_list]
        self.assertEqual(
            urls,
            [
                "https://mwapi.example/v1/messages",
                "https://mwapi.example/v1/chat/completions",
            ],
        )

    def test_cx_404_falls_back_to_bare_path(self):
        # Some gateways mount chat/completions at the root instead of /v1.
        def fake_urlopen(request, *args, **kwargs):
            if request.full_url.endswith("/v1/chat/completions"):
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", None, None)
            return _fake_response({"choices": [{"message": {"content": "Root mount."}}]})

        with _env(
            CX_GATEWAY_API_KEY="gk-test",
            CX_GATEWAY_BASE_URL="https://gateway.example",
            CX_GATEWAY_MODEL="cx/gpt-5.6-sol",
        ):
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen) as urlopen:
                out = llm.rewrite_with_llm("Hello world.")

        self.assertEqual(out, "Root mount.")
        urls = [c.args[0].full_url for c in urlopen.call_args_list]
        self.assertEqual(
            urls,
            [
                "https://gateway.example/v1/chat/completions",
                "https://gateway.example/chat/completions",
            ],
        )

    def test_all_providers_fail_returns_none(self):
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
            CX_GATEWAY_API_KEY="gk-test",
            CX_GATEWAY_BASE_URL="https://gateway.example",
        ):
            with mock.patch("urllib.request.urlopen", side_effect=OSError("network down")):
                self.assertIsNone(llm.rewrite_with_llm("Hello world."))

    def test_best_of_generates_multiple_candidates_and_keeps_faithful(self):
        # Best-of-N: several candidates are drawn (voice rotated), and the
        # one that preserves every number wins over a more fluent drifter.
        def fake_urlopen(request, *args, **kwargs):
            # First call (voice 1): fluent but drops the number "800".
            # Second call (voice 2): faithful rewrite.
            call = len(urlopen.call_args_list)
            if call == 0:
                text = (
                    "The study ran for several months, covering many "
                    "participants with a strong response rate overall."
                )
            else:
                text = (
                    "The study ran for 12 weeks, covering 800 participants "
                    "with a 90% response rate."
                )
            return _fake_response({"content": [{"text": text}]})

        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
        ):
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen) as urlopen:
                out = llm.rewrite_with_llm(
                    "The study ran for 12 weeks and covered 800 participants, "
                    "with a 90% response rate.",
                    best_of=2,
                )

        self.assertEqual(urlopen.call_count, 2)
        self.assertIn("800", out)
        self.assertIn("12 weeks", out)

    def test_best_of_one_is_single_shot(self):
        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
        ):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _fake_response({"content": [{"text": "Rewritten."}]})
                out = llm.rewrite_with_llm("Hello world.", best_of=1)

        self.assertEqual(out, "Rewritten.")
        self.assertEqual(urlopen.call_count, 1)

    def test_best_of_rotates_voices(self):
        # Each draw must use a different voice directive so the pool differs.
        seen_system_prompts = []

        def fake_urlopen(request, *args, **kwargs):
            sent = json.loads(request.data.decode("utf-8"))
            seen_system_prompts.append(sent["system"])
            return _fake_response(
                {"content": [{"text": f"Rewrite {len(seen_system_prompts)}."}]}
            )

        with _env(
            HINAA_CLAUDE_API_KEY="sk-test",
            HINAA_CLAUDE_BASE_URL="https://mwapi.example",
        ):
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                llm.rewrite_with_llm("Hello world.", best_of=2)

        self.assertEqual(len(seen_system_prompts), 2)
        self.assertNotEqual(seen_system_prompts[0], seen_system_prompts[1])


if __name__ == "__main__":
    unittest.main()
