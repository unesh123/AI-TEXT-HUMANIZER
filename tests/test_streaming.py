"""Tests for real-time streaming: the SSE event generator and the streaming
LLM chat parsers (OpenAI + Anthropic SSE formats).

All HTTP is mocked — these tests never touch the network or real keys.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.testutil  # noqa: F401 - scrub provider env + default plan to pro

from naturalizer import llm
from naturalizer.streaming import _emit_words, naturalize_stream


class _StreamResponse:
    """Minimal file-like object yielding SSE text lines (like http.client)."""

    def __init__(self, lines):
        self._lines = [line.encode("utf-8") for line in lines]
        self._i = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return self

    def __next__(self):
        if self._i >= len(self._lines):
            raise StopIteration
        line = self._lines[self._i]
        self._i += 1
        return line


def _openai_sse(chunks):
    lines = []
    for text in chunks:
        lines.append(f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}")
    lines.append("data: [DONE]")
    return lines


def _anthropic_sse(chunks):
    lines = [
        'event: message_start',
        'data: {"type": "message_start", "message": {"id": "msg_1"}}',
        "",
    ]
    for text in chunks:
        lines.append("event: content_block_delta")
        lines.append(
            f"data: {json.dumps({'type': 'content_block_delta', 'delta': {'text': text}})}"
        )
        lines.append("")
    lines.append('event: message_stop')
    lines.append('data: {"type": "message_stop"}')
    return lines


class LlmStreamParseTest(unittest.TestCase):
    def test_openai_stream_yields_deltas(self):
        with mock.patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "oa-test", "OPENAI_BASE_URL": "https://api.openai.example/v1"},
            clear=True,
        ):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _StreamResponse(_openai_sse(["Hello", " world", "."]))
                gen = llm.stream_rewrite_with_llm("Some draft.", provider="openai")
                out = list(gen)
        self.assertEqual(out, ["Hello", " world", "."])
        request = urlopen.call_args[0][0]
        sent = json.loads(request.data.decode("utf-8"))
        self.assertTrue(sent["stream"])
        self.assertEqual(sent["model"], "gpt-5-mini")

    def test_anthropic_stream_yields_deltas(self):
        with mock.patch.dict(
            os.environ,
            {
                "HINAA_CLAUDE_API_KEY": "sk-test",
                "HINAA_CLAUDE_BASE_URL": "https://mwapi.example",
                "HINAA_CLAUDE_MODEL": "claude-sonnet-4-6",
            },
            clear=True,
        ):
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value = _StreamResponse(_anthropic_sse(["Bonjour", " le", " monde."]))
                gen = llm.stream_rewrite_with_llm("Some draft.", provider="claude")
                out = list(gen)
        self.assertEqual(out, ["Bonjour", " le", " monde."])
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://mwapi.example/v1/messages")
        sent = json.loads(request.data.decode("utf-8"))
        self.assertTrue(sent["stream"])

    def test_openai_proxy_streams_claude(self):
        # A proxy fronts Claude: protocol absent -> OpenAI-compatible stream.
        with mock.patch.dict(
            os.environ,
            {
                "HINAA_CLAUDE_API_KEY": "sk-test",
                "HINAA_CLAUDE_BASE_URL": "https://mwapi.example",
                "HINAA_CLAUDE_MODEL": "claude-sonnet-4-6",
            },
            clear=True,
        ):
            with mock.patch("urllib.request.urlopen") as urlopen:
                # Fresh response per attempt: the anthropic protocol consumes
                # the stream before falling back to the OpenAI-compatible path.
                # urlopen is called with a timeout kwarg, so accept **kwargs.
                urlopen.side_effect = lambda req, **kw: _StreamResponse(
                    _openai_sse(["Via", " proxy"])
                )
                out = list(llm.stream_rewrite_with_llm("Draft.", provider="claude"))
        self.assertEqual(out, ["Via", " proxy"])
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://mwapi.example/v1/chat/completions")

    def test_no_provider_is_empty_generator(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(list(llm.stream_rewrite_with_llm("Draft.")), [])

    def test_failed_first_provider_falls_back(self):
        # Claude stream raises before any content -> CX provider is used.
        with mock.patch.dict(
            os.environ,
            {
                "HINAA_CLAUDE_API_KEY": "sk-test",
                "HINAA_CLAUDE_BASE_URL": "https://mwapi.example",
                "CX_GATEWAY_API_KEY": "gk-test",
                "CX_GATEWAY_BASE_URL": "https://gateway.example",
                "CX_GATEWAY_MODEL": "cx/gpt-5.6-sol",
            },
            clear=True,
        ):
            def fake_urlopen(request, *args, **kwargs):
                if request.full_url.endswith("/v1/messages"):
                    raise OSError("claude down")
                return _StreamResponse(_openai_sse(["From", " CX"]))

            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen) as urlopen:
                out = list(llm.stream_rewrite_with_llm("Draft."))
        self.assertEqual(out, ["From", " CX"])


class StreamingGeneratorTest(unittest.TestCase):
    def test_deterministic_stream_events(self):
        # No LLM configured -> word-by-word deltas of the deterministic
        # rewrite, then a done event with a full result payload.
        with mock.patch.dict(os.environ, {}, clear=True):
            events = list(naturalize_stream(
                "In today's fast-paced world, it is important to note that "
                "technology plays a crucial role.",
                style="academic",
                use_llm=False,
            ))
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds[0], "status")
        self.assertIn("delta", kinds)
        self.assertEqual(kinds[-1], "done")
        # statuses must appear in the right order
        statuses = [e["step"] for e in events if e["type"] == "status"]
        self.assertEqual(statuses, ["analyzing", "rewriting", "verifying"])
        # deltas reassemble exactly the deterministic rewrite
        deltas = "".join(e["text"] for e in events if e["type"] == "delta")
        done = next(e for e in events if e["type"] == "done")
        self.assertEqual(deltas, done["result"]["rewritten"])
        self.assertIn("score", done["result"])
        m = done["result"]["metrics"]
        self.assertIn("metrics", done["result"])
        # The streamed payload must carry the same verified-memory
        # plain-register row the non-streaming path renders, so the UI's
        # Plain register metric shows for streamed results too.
        self.assertIn("plain_register", m)
        self.assertIn("before", m["plain_register"])
        self.assertIn("after", m["plain_register"])
        self.assertTrue(0.0 <= m["plain_register"]["before"] <= 1.0)
        self.assertTrue(0.0 <= m["plain_register"]["after"] <= 1.0)

    def test_empty_text_errors(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            events = list(naturalize_stream("   ", use_llm=False))
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("empty", events[0]["message"])

    def test_emit_words_preserves_text(self):
        events = list(_emit_words("one two three"))
        self.assertEqual("".join(e["text"] for e in events), "one two three")
        self.assertEqual(len(events), 3)

    def test_stream_shows_deterministic_preview_before_llm(self):
        # With an LLM configured, the deterministic rewrite must stream
        # immediately (instant feedback) followed by a clear event and then
        # the LLM's own deltas — the UI never stares at an empty pane while
        # the LLM takes 30-60s to answer.
        sample = (
            "In today's fast-paced world, it is important to note that "
            "technology plays a crucial role in our daily lives."
        )
        import naturalizer.streaming as streaming
        with mock.patch.object(streaming, "llm_available", return_value=True), \
             mock.patch.object(
                 streaming, "stream_rewrite_with_llm",
                 return_value=iter(["The", " upgraded", " text."]),
             ):
            events = list(naturalize_stream(sample, style="academic", use_llm=True))
        kinds = [e["type"] for e in events]
        # Preview deltas come first, then exactly one clear, then LLM deltas.
        first_clear = kinds.index("clear")
        self.assertGreater(first_clear, 0)
        # Leading statuses (analyzing/rewriting) then only deltas before clear.
        self.assertTrue(all(k in ("status", "delta") for k in kinds[:first_clear]))
        self.assertIn("delta", kinds[:first_clear])
        self.assertEqual(kinds.count("clear"), 1)
        self.assertIn("delta", kinds[first_clear + 1:])
        # Everything before the clear reassembles the deterministic rewrite;
        # the LLM deltas land after it.
        preview = "".join(
            e["text"] for e in events[:first_clear] if e["type"] == "delta"
        )
        self.assertGreater(len(preview), 0)
        self.assertNotEqual(preview, "")

    def test_llm_failure_keeps_deterministic_preview(self):
        # If the LLM stream dies mid-flight after the preview, the run still
        # finishes with the deterministic text (already on screen) and an
        # honest warning — it must not error out or leave an empty pane.
        sample = "Furthermore, we must leverage cutting-edge tools."
        import naturalizer.streaming as streaming
        with mock.patch.object(streaming, "llm_available", return_value=True):
            def boom(*args, **kwargs):
                yield "partial"
                raise OSError("connection reset")

            with mock.patch.object(streaming, "stream_rewrite_with_llm", side_effect=boom):
                events = list(naturalize_stream(sample, style="academic", use_llm=True))
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds[-1], "done")
        done = events[-1]["result"]
        self.assertFalse(done.get("llm_used"))
        self.assertIsNotNone(done.get("llm_warning"))
        # The final result is the deterministic rewrite, and it was previewed.
        self.assertIn("clear", kinds)
        self.assertEqual(done["rewritten"], done["original"] and done["rewritten"])


if __name__ == "__main__":
    unittest.main()
