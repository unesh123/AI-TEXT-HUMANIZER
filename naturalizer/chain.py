"""Translation-chain humanization — "deep humanize".

Adapted from the Standard Pipeline of
`lynote-ai/humanize-text <https://github.com/lynote-ai/humanize-text>`_
(MIT License). The technique: route text through a chain of distant-language
hops so that no single model's structural fingerprint survives:

    EN -> 中文 -> 日本語 -> suomi -> EN

* Hops 1-2 are LLM *humanization rewrites* on the primary provider
  (Claude / CX gateway); hop 2 carries hop 1 as conversation history so the
  rewrite stays coherent.
* Hops 3-4 are plain *translation* hops. They run on a second engine when
  one is configured — the hcnsec OpenAI-compatible gateway
  (``OPENAI_CODEX_*``, DeepSeek/Kimi models) or the CX gateway — mirroring
  the original repo's cross-engine design; otherwise they reuse the primary.

Everything runs on the stdlib via ``naturalizer.llm.chat`` — no new
dependencies. When any hop fails (or no provider is configured),
``run_chain`` returns ``None`` and the engine falls back to the single-pass
LLM rewrite, then the deterministic pipeline.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from . import llm

_SYSTEM_PROMPT = "你是一个专业的文案改写专家，精通多语言本地化。"

_HUMANIZE = "翻译为{target}，去掉 AI 味道，拟人化改写，只输出结果：\n{text}"
_TRANSLATE = "翻译为{target}，只输出译文：\n{text}"

_TEMPERATURE = 1.3  # the upstream repo's recommended sampling for humanization

#: Final-hop label per style, so the returned English matches the profile.
_STYLE_LABELS = {
    "academic": "学术风格的自然英语",
    "business": "商务风格的自然英语",
    "creative": "有文采的自然英语",
    "casual": "口语化的自然英语",
}


def _secondary_config() -> Optional[Dict[str, str]]:
    """A second engine for the NMT-style hops, when configured."""
    key = os.environ.get("OPENAI_CODEX_API_KEY")
    if key:
        return {
            "api_key": key,
            "base": os.environ.get(
                "OPENAI_CODEX_BASE_URL", "https://api.openai.com/v1"
            ).rstrip("/"),
            "model": os.environ.get("OPENAI_CODEX_MODEL", "deepseek-chat"),
        }
    key = os.environ.get("CX_GATEWAY_API_KEY")
    if key:
        return {
            "api_key": key,
            "base": os.environ.get("CX_GATEWAY_BASE_URL", "").rstrip("/"),
            "model": os.environ.get("CX_GATEWAY_MODEL", "cx/gpt-5.6-sol"),
        }
    return None


def _hop(
    text: str,
    target: str,
    cfg: Dict[str, str],
    mode: str,
    history: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> Optional[str]:
    template = _HUMANIZE if mode == "humanize" else _TRANSLATE
    messages: List[Dict[str, str]] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if history:
        messages.append(
            {"role": "user", "content": template.format(target=target, text=history["input"])}
        )
        messages.append({"role": "assistant", "content": history["output"]})
    messages.append({"role": "user", "content": template.format(target=target, text=text)})
    return llm.chat(cfg, messages, temperature=_TEMPERATURE, timeout=timeout)


def run_chain(
    text: str,
    style: str = "academic",
    provider: str = "auto",
    on_hop: Optional[callable] = None,
    hop_timeout: Optional[float] = 90.0,
) -> Optional[str]:
    """Run the 4-hop translation chain, returning the final English text.

    *provider* selects the engine for hops 1-2 (``"auto"`` picks the first
    configured one — Claude when available). Hops 3-4 always try a second,
    different engine when one is configured (the cross-engine design); the
    fallback is the other main provider, then the primary itself.

    *on_hop* (optional) is called with ``(hop_number, total, target_label)``
    *before* each hop starts — streaming callers use it to show which hop
    is running while the chain is slow (hops on a second engine can take
    30-60s each). *hop_timeout* bounds each hop's HTTP call (the module
    default is 120s; the chain lowers it so a dead engine falls back fast).

    Returns ``None`` when no provider is configured or any hop fails, so
    callers can fall back to the single-pass rewrite.
    """
    providers = llm.llm_providers(provider)
    if not providers:
        return None
    primary = providers[0]["config"]
    secondary = _secondary_config()
    if secondary is None or secondary == primary:
        # Cross-engine hops need a *different* engine: try the other main
        # provider, then fall back to the primary itself.
        others = [p["config"] for p in llm.llm_providers("auto") if p["config"] != primary]
        secondary = others[0] if others else primary

    if on_hop is not None:
        on_hop(1, 4, "中文")
    hop1 = _hop(text, "中文", primary, "humanize", timeout=hop_timeout)
    if not hop1 and secondary != primary:
        # Primary is down (e.g. rate-limited) — retry the hop on the
        # failover engine before giving up on the chain.
        hop1 = _hop(text, "中文", secondary, "humanize", timeout=hop_timeout)
    if not hop1:
        return None
    if on_hop is not None:
        on_hop(2, 4, "日语")
    hop2 = _hop(hop1, "日语", primary, "humanize", history={"input": text, "output": hop1}, timeout=hop_timeout)
    if not hop2 and secondary != primary:
        hop2 = _hop(hop1, "日语", secondary, "humanize", history={"input": text, "output": hop1}, timeout=hop_timeout)
    if not hop2:
        return None
    if on_hop is not None:
        on_hop(3, 4, "suomi")
    hop3 = _hop(hop2, "芬兰语", secondary, "translate", timeout=hop_timeout)
    if not hop3:
        return None

    target = _STYLE_LABELS.get(style, "自然、地道的英语")
    if on_hop is not None:
        on_hop(4, 4, "English")
    hop4 = _hop(hop3, target, secondary, "translate", timeout=hop_timeout)
    if not hop4:
        return None
    result = hop4.strip()
    return result if result else None
