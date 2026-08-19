"""Real-time streaming humanization — "typewriter" mode.

The UI asks for ``POST /api/naturalize/stream`` instead of the plain
``/api/naturalize`` and receives a server-sent-events (SSE) stream while the
rewrite happens. This module produces that stream as a generator of event
dicts:

    {"type": "status", "step": "analyzing"}   # step: analyzing|rewriting|verifying
    {"type": "delta",  "text": "..."}          # one text chunk as it arrives
    {"type": "done",   "result": {...}}        # full NaturalizeResult.to_dict()
    {"type": "error",  "message": "..."}       # fatal; stream ends

Flow per request: score the draft, then rewrite. When an LLM provider is
configured the rewrite is *actually* streamed from the provider (delta by
delta, as the model generates). When it isn't (or the LLM fails before any
content), the deterministic engine produces the rewrite instantly and the
deltas are emitted word-by-word on a small timer, so the animation works on
the free tier too. Every path ends with the same post-rewrite verification
re-scan and a ``done`` event carrying the identical payload a non-streaming
call would return.
"""

from __future__ import annotations

import queue
import random
import threading
import time
from typing import Dict, Iterator, List, Optional

from .detectors import analyze
from .engine import NaturalizeResult
from .styles import DEFAULT_STYLE, get_style
from .transforms import rewrite as deterministic_rewrite

try:
    from .llm import llm_available, llm_provider_label, stream_rewrite_with_llm
    from .chain import run_chain
except ImportError:  # pragma: no cover - defensive
    llm_available = lambda provider="auto": False
    llm_provider_label = lambda provider="auto": None
    stream_rewrite_with_llm = None
    run_chain = lambda text, style="academic", provider="auto", **kw: None

#: Delay between word-deltas when the rewrite is instant (deterministic /
#: chain fallback), in seconds. Fast enough to feel live, slow enough to
#: actually watch.
_WORD_DELAY = 0.012


def _emit_words(text: str) -> Iterator[Dict]:
    """Split finished *text* into word deltas (deterministic fallback)."""
    words = text.split(" ")
    for i, word in enumerate(words):
        chunk = word + (" " if i < len(words) - 1 else "")
        yield {"type": "delta", "text": chunk}
        time.sleep(_WORD_DELAY)


def naturalize_stream(
    text: str,
    style: str = DEFAULT_STYLE,
    use_llm: Optional[bool] = None,
    seed: int = 0,
    deep: bool = False,
    provider: str = "auto",
    intensity: float = 0.5,
    prefer_llm: bool = True,
) -> Iterator[Dict]:
    """Stream one naturalize run as ``(status | delta | done | error)`` events."""
    text = (text or "").strip()
    if not text:
        yield {"type": "error", "message": "missing or empty text"}
        return
    intensity = max(0.0, min(1.0, intensity))

    profile = get_style(style)
    style = profile["name"]

    yield {"type": "status", "step": "analyzing"}
    report = analyze(
        text,
        allowlist=profile["allowlist"],
        keep_structure=profile.get("keep_structure", False),
    )

    rng = random.Random(seed)
    rewritten, _, _ = deterministic_rewrite(
        text,
        rng=rng,
        allowlist=profile["allowlist"],
        min_words=profile["min_words"],
        max_words=profile["max_words"],
        intensity=intensity,
        contractions=profile.get("contractions", False),
    )

    llm_used = False
    llm_rewritten: Optional[str] = None
    llm_method: Optional[str] = None
    llm_provider_name: Optional[str] = None
    llm_warning: Optional[str] = None

    want_llm = prefer_llm if use_llm is None else use_llm
    if want_llm and llm_available(provider):
        yield {"type": "status", "step": "rewriting"}
        if deep and run_chain is not None:
            # Chain hops can't be streamed (4 sequential calls, the last two
            # on a second engine that can take 30-60s each). Run them on a
            # worker thread and yield hop-progress status events as each hop
            # completes, so the UI never looks frozen, then emit the finished
            # text word-by-word so the animation holds.
            hop_q: "queue.Queue" = queue.Queue()

            def _run_chain() -> None:
                try:
                    out = run_chain(
                        text,
                        style=style,
                        provider=provider,
                        on_hop=lambda n, total, label: hop_q.put(
                            ("hop", n, total, label)
                        ),
                    )
                except Exception:  # pragma: no cover - network/timeout edge
                    out = None
                hop_q.put(("done", out))

            threading.Thread(target=_run_chain, daemon=True).start()
            candidate = None
            while True:
                item = hop_q.get()
                if item[0] == "done":
                    candidate = item[1]
                    break
                _, n, total, label = item
                yield {
                    "type": "status",
                    "step": "rewriting",
                    "detail": f"deep chain — hop {n}/{total} ({label})",
                }
            if candidate:
                llm_method = "chain"
                llm_provider_name = provider
                polished, _, _ = deterministic_rewrite(
                    candidate,
                    rng=rng,
                    allowlist=profile["allowlist"],
                    min_words=profile["min_words"],
                    max_words=profile["max_words"],
                    intensity=intensity,
                    contractions=profile.get("contractions", False),
                )
                llm_rewritten = (polished.strip() or candidate).strip()
                llm_used = True
                for event in _emit_words(llm_rewritten):
                    yield event
        if not llm_used and stream_rewrite_with_llm is not None:
            # Instant feedback: the deterministic rewrite is already in
            # hand, and the LLM can take 10-60s to answer on a slow
            # gateway. Stream the deterministic version first so the pane
            # never sits empty, then a clear event, then the LLM's own
            # words as they arrive. If the LLM dies mid-stream the caller
            # keeps the deterministic text (already on screen).
            preview_shown = False
            if rewritten:
                for event in _emit_words(rewritten):
                    yield event
                yield {"type": "clear"}
                preview_shown = True
            try:
                used: List[str] = []
                gen = stream_rewrite_with_llm(
                    text,
                    style=style,
                    provider=provider,
                    voice=rng.randrange(1, 5),
                    provider_out=used,
                )
                if gen is not None:
                    parts: List[str] = []
                    for delta in gen:
                        parts.append(delta)
                        yield {"type": "delta", "text": delta}
                    candidate = "".join(parts).strip()
                    if candidate:
                        llm_rewritten = candidate
                        llm_used = True
                        llm_method = "single"
                        llm_provider_name = used[0] if used else provider
            except Exception:  # pragma: no cover - mid-stream failure
                if not llm_used:
                    if preview_shown:
                        # The deterministic preview is still on screen —
                        # nothing to restore, just say the upgrade failed.
                        llm_warning = (
                            "The LLM stream failed before finishing — the "
                            "version below is the deterministic rewrite."
                        )
                    else:
                        llm_warning = (
                            "The LLM stream failed before producing a rewrite — "
                            "using the deterministic rewrite instead."
                        )

        if llm_used:
            # *llm_provider_name* is already the provider that actually
            # served (failover-aware) — never relabel with the requested one.
            pass
        elif want_llm and provider != "auto" and not llm_used:
            # Explicit provider that produced nothing: explain the fallback.
            if not llm_available(provider):
                llm_warning = (
                    f"{provider} isn't configured — add its env vars to "
                    ".env.local (see README). Using the deterministic "
                    "rewrite instead."
                )
            else:
                llm_warning = (
                    f"{provider} call failed (check the gateway URL and "
                    f"API key in .env.local). Using the deterministic "
                    "rewrite instead."
                )

    if not llm_used:
        # The deterministic rewrite is already computed — reveal it live so
        # the animation holds even without an LLM (or after one failed).
        yield {"type": "status", "step": "rewriting"}
        for event in _emit_words(rewritten):
            yield event

    chosen = llm_rewritten if llm_used else rewritten
    yield {"type": "status", "step": "verifying"}
    after_report = analyze(
        chosen or text,
        allowlist=profile["allowlist"],
        keep_structure=profile.get("keep_structure", False),
    )
    from .human_memory import plain_register_score

    metrics = {
        "before": report.metrics,
        "after": after_report.metrics,
        "after_score": after_report.score,
        # Verified human-writing memory: how plain the register is (fraction
        # of words from the everyday vocabulary humans actually use). Kept
        # in lockstep with the engine so the streamed result renders the
        # same Plain register row as a non-streaming call.
        "plain_register": {
            "before": round(plain_register_score(text), 3),
            "after": round(plain_register_score(chosen or text), 3),
        },
    }

    result = NaturalizeResult(
        original=text,
        rewritten=rewritten if rewritten.strip() else text,
        llm_rewritten=llm_rewritten,
        llm_used=llm_used,
        llm_method=llm_method,
        llm_provider=llm_provider_name,
        score=report.score,
        issues=[i.to_dict() for i in report.issues],
        style=style,
        sentence_count=report.sentence_count,
        intensity=intensity,
        metrics=metrics,
        llm_warning=llm_warning,
    )
    yield {"type": "done", "result": result.to_dict()}
