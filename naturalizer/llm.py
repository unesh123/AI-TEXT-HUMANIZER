"""Optional LLM backend for higher-quality rewrites.

The deterministic transforms handle the common cases, but a capable language
model produces better prose. This module is a thin, dependency-free client
for two providers:

* **Claude** — configured with ``HINAA_CLAUDE_*`` env vars
  (``HINAA_CLAUDE_API_KEY``, ``HINAA_CLAUDE_BASE_URL``,
  ``HINAA_CLAUDE_MODEL``). Speaks either the native Anthropic Messages API
  (``HINAA_CLAUDE_PROTOCOL=anthropic``) or an OpenAI-compatible proxy that
  fronts Claude models (any other/absent protocol) — it auto-falls back
  between the two on auth/path errors, so a proxy like ``api.mwapi.dev``
  works out of the box.
* **CX GPT gateway** — any OpenAI-compatible ``/chat/completions`` endpoint,
  configured with ``CX_GATEWAY_*`` env vars (``CX_GATEWAY_API_KEY``,
  ``CX_GATEWAY_BASE_URL``, ``CX_GATEWAY_MODEL``, e.g. ``cx/gpt-5.6-sol``).

``rewrite_with_llm`` tries Claude first, then the CX gateway; when neither
is configured (or both fail) it returns ``None`` and the engine falls back
to the deterministic pipeline. ``chat`` is the generic protocol-aware
single-round primitive (used by both the single-pass rewrite and the
``chain`` module). Credentials are read from ``.env.local`` / ``.env`` by
``naturalizer.envfile.load_envfile`` at the entry points.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from .styles import STYLES

_SYSTEM_PROMPT = (
    "You are a skilled human author and developmental editor. Rewrite the "
    "entire user's draft from scratch so it reads like thoughtful, original "
    "human writing. Keep the meaning and factual claims exactly, but do not "
    "preserve the source's sentence structure, clause order, or phrasing. "
    "This is a full re-authoring pass, not proofreading or synonym swapping. "
    "Break up mechanical sentence patterns, "
    "vary sentence length and openers, cut clichés and empty filler, and let "
    "the writing have a natural, slightly irregular rhythm. Match the "
    "requested style. Do not add information that is not in the original. "
    "Return only the rewritten text, with no commentary. Rebuild every "
    "sentence: merge or split ideas where natural, change sentence openings, "
    "reorder clauses when the logic remains clear, and use genuinely new "
    "wording. Avoid copying any sequence of five or more consecutive words "
    "from the draft unless it is a necessary name, quotation, or technical "
    "term. Do not add facts, examples, evidence, or conclusions that are not "
    "supported by the draft. "
    "Punctuation and wording: avoid em dashes (use commas, parentheses, or "
    "restructure instead), and avoid balanced 'not X but Y' / 'not X; they "
    "are Y' antithesis scaffolds — state the point directly in one clause. "
    "When the draft piles up abstract nouns (values, impacts, aspects), "
    "name the concrete things they come down to; replace stiff connectives "
    "like 'as against' or 'in the broad sense of the term' with plain "
    "English. Never use these exact tells: 'in the broad sense of the "
    "term', 'at the intersection of', 'critically analyse(d)', 'discerning "
    "users', 'in today's fast-paced world', 'our analysis focused on', "
    "'must also consider', 'intended and unintended', 'we first sought to', "
    "'we were also asked to', 'this assignment required us to', "
    "'successfully implemented'. Vary the flow like a person: occasional "
    "short sentences, a natural aside now and then (in practice, as it "
    "happens, admittedly — at most once or twice), and plain verbs instead "
    "of abstract noun phrases. Do not narrate a rigid 'we did X, then we "
    "did Y, finally we did Z' sequence — fold the steps into the prose so "
    "the order comes across naturally rather than as a checklist. Vary the "
    "grammatical architecture too: occasionally move a subordinate clause to "
    "the front (\"Because Y, X\" instead of \"X because Y\") and vary subject "
    "position, while keeping the register exactly what the draft uses — "
    "formal text stays formal, casual stays casual. Never dumb a draft down "
    "to escape detection; restructure it like a skilled editor would. "
    "Plain-language rules (non-negotiable): prefer short everyday verbs and "
    "nouns — use, help, start, make, get, try, need, show, give, find — over "
    "their Latinate doubles (utilize, facilitate, commence, fabricate, "
    "obtain, endeavor, demonstrate, provide, ensure). When the draft says "
    "'utilize' write 'use'; 'commence' -> 'start'; 'ascertain' -> 'find "
    "out'; 'numerous' -> 'many'; 'subsequently' -> 'then' or 'later'; "
    "'additionally' / 'furthermore' / 'moreover' -> drop them or say 'also' "
    "or 'and'; 'in order to' -> 'to'; 'with regard to' -> 'about'; 'a "
    "number of' -> 'some'; 'in the event that' -> 'if'; 'at this point in "
    "time' -> 'now'. Write verbs as verbs, not noun phrases ('make a "
    "decision' -> 'decide', 'conduct an analysis' -> 'analyze'). Never "
    "replace one stiff word with a different stiff word — the point is to "
    "read like a person speaking, so when in doubt choose the word that "
    "would come first in spoken conversation. Keep the meaning and facts "
    "exactly as the draft has them."
)

# Voice directives rotated by seed so consecutive runs of the same draft
# genuinely read differently (a single uniform rewrite style is itself a
# fingerprint detectors can learn). Each voice only nudges register — the
# shared rules above still apply, so meaning and facts never change.
_VOICES = (
    "",
    "Write in a plain-spoken, direct register: short sentences, concrete "
    "words, and first-person observations where the draft already speaks "
    "for people. Avoid any trace of report-card tone.",
    "Write like a careful student explaining the work to a friend: keep the "
    "detail, but make the sentences breathe and let the reasoning show "
    "through instead of being summarized.",
    "Write like a working professional summarizing what was done: calm, "
    "specific, and unpretentious, with the occasional short emphatic "
    "sentence breaking up longer ones.",
    "Write like a journalist covering the topic: varied sentence lengths, "
    "concrete examples instead of abstractions, and a slightly informal "
    "rhythm that never slides into slang.",
)

_MAX_TOKENS = 4000
_TIMEOUT = 120
#: Per-provider timeout for *failover* attempts (providers after the first
#: in a chain). The first provider gets the full ``_TIMEOUT``; a dead
#: gateway behind it must not be able to burn 120s per attempt — "auto"
#: with several configured-but-dead gateways used to take 30-67s before
#: reaching a working one, which made the UI look frozen.
_FAILOVER_TIMEOUT = 20.0

#: Provider name that last produced a rewrite, so "auto" can try it first
#: instead of re-walking the whole chain every call. Cleared when that
#: provider fails, so a dead gateway can't pin the order forever.
_LAST_GOOD_PROVIDER: Optional[str] = None
# Some gateway proxies (e.g. api.mwapi.dev) 403 urllib's default UA, so send
# a browser-like one on every provider request.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _user_message(text: str, style: str) -> str:
    profile = STYLES.get(style)
    style_name = profile["label"] if profile else style
    return f"Style: {style_name}\n\nDraft:\n{text}"


# ---------------------------------------------------------------- providers
def _claude_config() -> Optional[Dict[str, str]]:
    key = os.environ.get("HINAA_CLAUDE_API_KEY")
    if not key:
        return None
    return {
        "api_key": key,
        "base": os.environ.get("HINAA_CLAUDE_BASE_URL", "https://api.anthropic.com").rstrip("/"),
        "model": os.environ.get("HINAA_CLAUDE_MODEL", "claude-sonnet-4-6"),
        # "anthropic" -> native Messages API; anything else -> OpenAI-compatible.
        "protocol": os.environ.get("HINAA_CLAUDE_PROTOCOL", "anthropic").lower(),
    }


def _cx_config() -> Optional[Dict[str, str]]:
    key = os.environ.get("CX_GATEWAY_API_KEY")
    if not key:
        return None
    return {
        "api_key": key,
        "base": os.environ.get("CX_GATEWAY_BASE_URL", "").rstrip("/"),
        "model": os.environ.get("CX_GATEWAY_MODEL", "cx/gpt-5.6-sol"),
    }


def _openai_config() -> Optional[Dict[str, str]]:
    """Direct OpenAI (OpenAI-compatible ``/chat/completions``)."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    return {
        "api_key": key,
        "base": os.environ.get(
            "OPENAI_API_BASE",
            os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        ).rstrip("/"),
        "model": os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
    }


def _gemini_config() -> Optional[Dict[str, str]]:
    """Google Gemini via its OpenAI-compatible endpoint."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    return {
        "api_key": key,
        "base": os.environ.get(
            "GEMINI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai",
        ).rstrip("/"),
        "model": os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
    }


def _qwen_config() -> Optional[Dict[str, str]]:
    """Alibaba Qwen (DashScope compatible mode)."""
    key = os.environ.get("HINAA_QWEN_API_KEY")
    if not key:
        return None
    return {
        "api_key": key,
        "base": os.environ.get(
            "HINAA_QWEN_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).rstrip("/"),
        "model": os.environ.get("HINAA_QWEN_MODEL", "qwen-plus"),
    }


def _router_config() -> Optional[Dict[str, str]]:
    """Agent router (any OpenAI-compatible endpoint). Needs key + base URL."""
    key = os.environ.get("AGENT_ROUTER_API_KEY")
    base = os.environ.get("AGENT_ROUTER_BASE_URL")
    if not key or not base:
        return None
    return {
        "api_key": key,
        "base": base.rstrip("/"),
        "model": os.environ.get("AGENT_ROUTER_MODEL", ""),
    }


def _codex_config() -> Optional[Dict[str, str]]:
    """The hcnsec OpenAI-compatible gateway (``OPENAI_CODEX_*``, DeepSeek/Kimi)."""
    key = os.environ.get("OPENAI_CODEX_API_KEY")
    if not key:
        return None
    return {
        "api_key": key,
        "base": os.environ.get(
            "OPENAI_CODEX_BASE_URL", "https://api.openai.com/v1"
        ).rstrip("/"),
        "model": os.environ.get("OPENAI_CODEX_MODEL", "deepseek-chat"),
    }


def _hcns_config() -> Optional[Dict[str, str]]:
    """HCN security gateway key (``HCNSEC_API_KEY``), OpenAI-compatible."""
    key = os.environ.get("HCNSEC_API_KEY")
    if not key:
        return None
    return {
        "api_key": key,
        "base": os.environ.get("HCNSEC_BASE_URL", "https://api.hcnsec.cn/v1").rstrip("/"),
        "model": os.environ.get("HCNSEC_MODEL", "deepseek-chat"),
    }


def _anthropic_chat(
    cfg: Dict[str, str],
    messages: List[Dict[str, str]],
    temperature: float,
    timeout: Optional[float] = None,
) -> Optional[str]:
    """Native Anthropic Messages API (``/v1/messages``, ``x-api-key``)."""
    payload = {
        "model": cfg["model"],
        "max_tokens": _MAX_TOKENS,
        "system": next(
            (m["content"] for m in messages if m.get("role") == "system"), ""
        ),
        "messages": [m for m in messages if m.get("role") != "system"],
    }
    request = urllib.request.Request(
        f"{cfg['base']}/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
            "x-api-key": cfg["api_key"],
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout or _TIMEOUT) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = body["content"][0]["text"]
    content = content.strip()
    return content if content else None


def _openai_chat(
    cfg: Dict[str, str],
    messages: List[Dict[str, str]],
    temperature: float,
    timeout: Optional[float] = None,
) -> Optional[str]:
    """OpenAI-compatible ``/chat/completions`` with a Bearer token.

    Tries ``/v1/chat/completions`` first (the common layout), then falls
    back to ``/chat/completions`` (some gateways mount at the root).
    """
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
    }
    last_error: Optional[Exception] = None
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        request = urllib.request.Request(
            f"{cfg['base']}{suffix}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
                "Authorization": f"Bearer {cfg['api_key']}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or _TIMEOUT) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                last_error = exc
                continue
            raise
        content = body["choices"][0]["message"]["content"]
        content = content.strip()
        if content:
            return content
        return None
    raise last_error  # type: ignore[misc]


def chat(
    cfg: Dict[str, str],
    messages: List[Dict[str, str]],
    temperature: float = 0.8,
    timeout: Optional[float] = None,
) -> Optional[str]:
    """One chat round through a provider config, protocol-aware.

    Claude-style configs (those with a ``protocol`` key) try the configured
    protocol first — native Anthropic Messages API vs OpenAI-compatible —
    and auto-fall back between them on auth/path errors (400/401/403/404/
    405), which makes OpenAI-compatible proxies that front Claude models
    (e.g. api.mwapi.dev) work out of the box. Plain OpenAI-compatible
    configs (e.g. the CX gateway) return ``None`` on 4xx instead of raising.
    """
    if "protocol" in cfg:
        protocols = (
            ("openai", "anthropic")
            if cfg["protocol"] != "anthropic"
            else ("anthropic", "openai")
        )
        last_error: Optional[Exception] = None
        for proto in protocols:
            try:
                if proto == "anthropic":
                    out = _anthropic_chat(cfg, messages, temperature, timeout=timeout)
                else:
                    out = _openai_chat(cfg, messages, temperature, timeout=timeout)
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (400, 401, 403, 404, 405):
                    last_error = exc
                    continue
                raise
            if out:
                return out
            return None
        raise last_error  # type: ignore[misc]
    try:
        return _openai_chat(cfg, messages, temperature, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403, 404, 405):
            return None
        raise


# ------------------------------------------------------- streaming variants
#
# The same two protocols, but with ``stream: true``: the response body is an
# SSE stream and each yield is one text delta, so callers can push words to
# the UI as the model generates them. Both are generator functions — the
# HTTP request happens lazily on the first ``next()``.


def _stream_anthropic_chat(
    cfg: Dict[str, str], messages: List[Dict[str, str]], temperature: float,
    timeout: Optional[float] = None,
):
    """Stream deltas from the native Anthropic Messages API."""
    payload = {
        "model": cfg["model"],
        "max_tokens": _MAX_TOKENS,
        "stream": True,
        "system": next(
            (m["content"] for m in messages if m.get("role") == "system"), ""
        ),
        "messages": [m for m in messages if m.get("role") != "system"],
    }
    request = urllib.request.Request(
        f"{cfg['base']}/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
            "x-api-key": cfg["api_key"],
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout or _TIMEOUT) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):].strip()
            if not data:
                continue
            try:
                body = json.loads(data)
            except ValueError:
                continue
            if body.get("type") == "content_block_delta":
                text = body.get("delta", {}).get("text")
                if text:
                    yield text


def _stream_openai_chat(
    cfg: Dict[str, str], messages: List[Dict[str, str]], temperature: float,
    timeout: Optional[float] = None,
):
    """Stream deltas from an OpenAI-compatible ``/chat/completions``.

    Tries ``/v1/chat/completions`` first, then ``/chat/completions`` on 404
    (matching the non-streaming path).
    """
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    last_error: Optional[Exception] = None
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        request = urllib.request.Request(
            f"{cfg['base']}{suffix}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
                "Authorization": f"Bearer {cfg['api_key']}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or _TIMEOUT) as response:
                for raw in response:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[len("data: "):].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        body = json.loads(data)
                    except ValueError:
                        continue
                    delta = body.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content")
                    if text:
                        yield text
            return
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                last_error = exc
                continue
            raise
    raise last_error  # type: ignore[misc]


def stream_chat(
    cfg: Dict[str, str],
    messages: List[Dict[str, str]],
    temperature: float = 0.8,
    timeout: Optional[float] = None,
):
    """Stream one chat round, protocol-aware (same fallbacks as ``chat``).

    Returns a generator yielding text deltas, or ``None`` when the provider
    rejects the request outright (4xx on every protocol). A mid-stream
    failure raises — callers decide whether to keep partial output.

    Note: both underlying stream generators perform their HTTP request
    lazily on first ``next()``, so the protocol fallback happens *inside*
    the returned generator — it pulls the first delta from each protocol
    and only moves on when that request is rejected before any content.
    """
    if "protocol" in cfg:
        protocols = (
            ("openai", "anthropic")
            if cfg["protocol"] != "anthropic"
            else ("anthropic", "openai")
        )

        def _fallback():
            last_error: Optional[Exception] = None
            for proto in protocols:
                try:
                    gen = (
                        _stream_anthropic_chat(cfg, messages, temperature, timeout=timeout)
                        if proto == "anthropic"
                        else _stream_openai_chat(cfg, messages, temperature, timeout=timeout)
                    )
                    first = next(gen)
                except StopIteration:
                    # Protocol produced zero deltas before any content — not
                    # a rewrite; try the next protocol (e.g. a proxy fronting
                    # Claude may answer OpenAI-format on both paths).
                    last_error = None
                    continue
                except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
                    if isinstance(exc, urllib.error.HTTPError) and exc.code in (400, 401, 403, 404, 405):
                        last_error = exc
                        continue
                    raise
                yield first
                yield from gen
                return
            if last_error is not None:
                raise last_error

        return _fallback()

    def _plain():
        gen = _stream_openai_chat(cfg, messages, temperature, timeout=timeout)
        try:
            first = next(gen)
        except StopIteration:
            return
        yield first
        yield from gen

    try:
        return _plain()
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403, 404, 405):
            return None
        raise


# ------------------------------------------------------------------- public
PROVIDER_NAMES = (
    "auto", "claude", "cx", "openai", "gemini", "qwen", "router", "codex", "hcns",
)

#: name -> config loader, in fallback order for "auto".
_PROVIDER_CONFIGS = (
    ("claude", _claude_config),
    ("cx", _cx_config),
    ("openai", _openai_config),
    ("gemini", _gemini_config),
    ("qwen", _qwen_config),
    ("router", _router_config),
    ("codex", _codex_config),
    ("hcns", _hcns_config),
)


def llm_providers(provider: str = "auto") -> List[Dict[str, str]]:
    """Ordered, configured providers.

    *provider*: ``"auto"`` -> every configured provider, Claude first;
    any specific name (``"claude"`` / ``"cx"`` / ``"openai"`` / …) ->
    that provider only (empty list when its env vars are absent).
    """
    provider = (provider or "auto").lower()
    if provider != "auto":
        for name, loader in _PROVIDER_CONFIGS:
            if name != provider:
                continue
            cfg = loader()
            return [{"name": name, "model": cfg["model"], "config": cfg}] if cfg else []
        return []  # unknown provider name
    providers: List[Dict[str, str]] = []
    for name, loader in _PROVIDER_CONFIGS:
        cfg = loader()
        if cfg:
            providers.append({"name": name, "model": cfg["model"], "config": cfg})
    return providers


def llm_provider_chain(provider: str = "auto") -> List[Dict[str, str]]:
    """Ordered providers for a *call*: the requested selection first, then
    every other configured provider as failover.

    ``llm_providers`` answers "what did the user ask for" (an explicit
    ``"cx"`` yields only CX — correct for the UI picker). But a *call*
    should never die because the requested gateway is rate-limited: when
    CX 429s, the rewrite must fall through to Claude rather than dropping
    to the deterministic path. This returns the requested provider(s) in
    front, followed by each remaining configured provider, de-duplicated.

    In ``"auto"`` mode the provider that last produced a rewrite is moved
    to the front (when still configured): re-walking every gateway on
    every call cost tens of seconds whenever earlier gateways were dead.
    """
    providers = list(llm_providers(provider))
    if provider == "auto" and _LAST_GOOD_PROVIDER:
        good = [p for p in providers if p["name"] == _LAST_GOOD_PROVIDER]
        if good:
            providers = good + [p for p in providers if p["name"] != _LAST_GOOD_PROVIDER]
    # Fail over only when the requested selection is *configured but
    # failing* (e.g. CX rate-limited). An unconfigured requested provider
    # stays empty — the engine explains that case distinctly ("add its
    # env vars") and never wants a silent cross-provider call there.
    if provider != "auto" and providers:
        seen = {p["name"] for p in providers}
        for p in llm_providers("auto"):
            if p["name"] not in seen:
                providers.append(p)
    return providers


def _remember_good(provider_name: Optional[str]) -> None:
    """Record which provider actually served a rewrite (``None`` clears it)."""
    global _LAST_GOOD_PROVIDER
    _LAST_GOOD_PROVIDER = provider_name


def llm_available(provider: str = "auto") -> bool:
    """True when at least one LLM provider (of the requested selection) is configured."""
    return bool(llm_providers(provider))


def llm_provider_label(provider: str = "auto") -> Optional[str]:
    """Human-readable label like ``claude (claude-sonnet-4-6)``."""
    providers = llm_providers(provider)
    if not providers:
        return None
    return f"{providers[0]['name']} ({providers[0]['model']})"


def llm_provider_choices() -> List[Dict[str, Optional[str]]]:
    """Per-provider status for the UI's model picker."""
    choices = [{"name": "auto", "label": "Auto (first available)", "configured": True}]
    for name, loader in _PROVIDER_CONFIGS:
        cfg = loader()
        if cfg:
            choices.append(
                {"name": name, "label": f"{name} ({cfg['model']})", "configured": True}
            )
        else:
            choices.append({"name": name, "label": name, "configured": False})
    return choices


def _build_messages(
    text: str, style: str, instruction: Optional[str], voice: int
) -> List[Dict[str, str]]:
    """Assemble the system + user messages for one rewrite round."""
    system = _SYSTEM_PROMPT
    if voice:
        system = f"{_SYSTEM_PROMPT}\n\n{_VOICES[voice % len(_VOICES)]}"
    if instruction:
        system = (
            f"{system}\n\nFollow this extra direction from the last "
            f"review pass:\n{instruction}"
        )
    # Plain-register memory: name the specific stiff words in THIS draft
    # and the everyday words humans use instead. Only appended when the
    # draft actually reaches for formal vocabulary, so plain drafts get no
    # extra noise.
    from .human_memory import plain_register_guidance

    memory = plain_register_guidance(text)
    if memory:
        system = f"{system}\n\n{memory}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": _user_message(text, style)},
    ]


def rewrite_with_llm(
    text: str,
    style: str = "academic",
    provider: str = "auto",
    instruction: Optional[str] = None,
    voice: int = 0,
    best_of: int = 1,
) -> Optional[str]:
    """Rewrite *text* via the configured providers.

    *instruction* (optional) is a short, concrete directive appended to the
    system prompt — used by the live feedback loop to tell the model what
    the last re-scan flagged (e.g. "the word 'leveraging' reads like
    corporate filler — vary the wording").

    *voice* (optional) indexes ``_VOICES`` so consecutive runs of the same
    draft read differently instead of sharing one uniform rewrite style.

    *best_of* (optional, default 1) is the number of candidate rewrites to
    generate before returning one. When > 1 the candidates are produced
    with rotated voices (so they genuinely differ), then ranked by the
    factual critics: a candidate that preserves every number beats one
    that dropped a number no matter how fluent it reads, and ties break on
    the local naturalness score (best-of-N selection — the same
    "generate several, keep the best surviving" strategy the advanced
    humanizers use). Best-of-1 is exactly the old single-shot behavior.

    Returns the rewritten text, or ``None`` when no provider is configured
    or every provider fails (callers then fall back to the deterministic
    path). A provider is only skipped when it raises, returns empty, or
    returns a non-dict body — never on a bad rewrite.
    """
    details = rewrite_with_llm_details(
        text, style=style, provider=provider, instruction=instruction,
        voice=voice, best_of=best_of,
    )
    return details[0] if details else None


def rewrite_with_llm_details(
    text: str,
    style: str = "academic",
    provider: str = "auto",
    instruction: Optional[str] = None,
    voice: int = 0,
    best_of: int = 1,
) -> Optional[Tuple[str, str]]:
    """Like ``rewrite_with_llm`` but returns ``(text, provider_name)``.

    The provider name is the one that *actually* produced the rewrite —
    with failover this differs from the requested provider when the first
    choice is down (``"cx"`` requested, Claude served). Callers that
    display the model label use this so the UI never claims CX wrote text
    Claude produced. Returns ``None`` when every provider fails.
    """
    count = max(1, int(best_of or 1))
    if count == 1:
        messages = _build_messages(text, style, instruction, voice)
        providers = llm_provider_chain(provider)
        for i, provider_cfg in enumerate(providers):
            timeout = None if i == 0 else _FAILOVER_TIMEOUT
            try:
                result = chat(provider_cfg["config"], messages, temperature=0.8, timeout=timeout)
            except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError):
                if provider_cfg["name"] == _LAST_GOOD_PROVIDER:
                    _remember_good(None)  # the remembered gateway is dead — re-walk next call
                continue
            if result:
                _remember_good(provider_cfg["name"])
                return result, provider_cfg["name"]
        return None

    candidates: List[str] = []
    used_name: Optional[str] = None
    for i in range(count):
        messages = _build_messages(text, style, instruction, voice + i)
        providers = llm_provider_chain(provider)
        for j, provider_cfg in enumerate(providers):
            timeout = None if j == 0 else _FAILOVER_TIMEOUT
            try:
                result = chat(provider_cfg["config"], messages, temperature=0.8, timeout=timeout)
            except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError):
                if provider_cfg["name"] == _LAST_GOOD_PROVIDER:
                    _remember_good(None)
                continue
            if result:
                candidates.append(result)
                used_name = provider_cfg["name"]
                _remember_good(provider_cfg["name"])
                break
    if not candidates:
        return None
    from .critics import rank_candidates

    best, _ = rank_candidates(text, candidates, style=style)
    return best, used_name


def stream_rewrite_with_llm(
    text: str,
    style: str = "academic",
    provider: str = "auto",
    instruction: Optional[str] = None,
    voice: int = 0,
    provider_out: Optional[List[str]] = None,
):
    """Rewrite *text* via the configured providers, streaming deltas.

    Generator yielding the rewrite as text deltas (as they arrive from the
    provider). When no provider is configured the generator is simply
    empty — iterate it to find out (a generator function can't return
    ``None``). Providers are tried in order: a provider that fails *before*
    any content is skipped; once the first delta has flowed the caller is
    committed to it (the caller keeps partial output on a mid-stream
    failure, mirroring how ``rewrite_with_llm`` keeps the last good
    rewrite).

    *instruction* behaves exactly as in ``rewrite_with_llm``; *voice* too.
    """
    system = _SYSTEM_PROMPT
    if voice:
        system = f"{_SYSTEM_PROMPT}\n\n{_VOICES[voice % len(_VOICES)]}"
    if instruction:
        system = (
            f"{system}\n\nFollow this extra direction from the last "
            f"review pass:\n{instruction}"
        )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _user_message(text, style)},
    ]
    for i, provider_cfg in enumerate(llm_provider_chain(provider)):
        timeout = None if i == 0 else _FAILOVER_TIMEOUT
        gen = stream_chat(provider_cfg["config"], messages, temperature=0.8, timeout=timeout)
        if gen is None:
            continue
        try:
            first = next(gen)
        except StopIteration:
            continue
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, TypeError):
            if provider_cfg["name"] == _LAST_GOOD_PROVIDER:
                _remember_good(None)
            continue
        if provider_out is not None:
            provider_out[:] = [provider_cfg["name"]]
        _remember_good(provider_cfg["name"])
        yield first
        yield from gen
        return
