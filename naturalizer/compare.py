"""Live multi-provider humanization comparison — the honest "best of all
sites" engine.

Runs the *same input* through every available humanizer, scores each output
with the same floor gate used everywhere else in this repo (naturalness
score + plain-register + fact preservation vs. the original), ranks them,
and returns the single best version with provenance and reasons. The system
always runs its own engines; third-party providers activate the moment their
key is in the environment, exactly like the GPTZero row in the detector
panel.

Providers:

  * ``local-deterministic`` — the dependency-free rewrite engine. Always on.
  * ``local-perfect-loop`` — the feedback loop (rewrite → re-scan → rewrite
    until the floor clears). Always on; uses the LLM path only when a
    provider is configured and ``use_llm`` is requested.
  * ``stealthgpt`` / ``undetectable`` — official HTTP APIs. Wired and
    best-effort: they report ``blocked=not_configured`` until
    ``STEALTHGPT_API_KEY`` / ``UNDETECTABLE_API_KEY`` is set, and any API
    drift is reported honestly instead of guessed around.
  * ``scraped:<site>`` — outputs captured from live humanizer sites by
    ``tools/humanizer_site_probe.mjs`` (CLI-only: it drives a real browser
    against the site's own free tier). Loaded from a probe report dir.

The selection is deliberately transparent: highest naturalness score that
clears the floor, then plainest register, then fewest lost facts — no
black-box weighting, so the "best" pick is explainable to the user.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional

from .critics import preservation_issues
from .detectors import analyze
from .engine import Naturalizer
from .feedback import feedback_humanize
from .human_memory import PLAIN_SWAPS, plain_register_score
from .styles import DEFAULT_STYLE, get_style
from .transforms import rewrite as deterministic_rewrite

#: Same floor as tools/detector_bench.py — "human-like" means at-or-above
#: this on the shared 0-100 scale.
DEFAULT_FLOOR = 75.0

#: External API providers keyed by the env var that activates them.
_API_PROVIDERS: List[Dict[str, object]] = [
    {
        "name": "stealthgpt",
        "label": "StealthGPT",
        "env": "STEALTHGPT_API_KEY",
        "endpoint": "https://api.stealthgpt.ai/api/text/humanize",
    },
    {
        "name": "undetectable",
        "label": "Undetectable AI",
        "env": "UNDETECTABLE_API_KEY",
        "endpoint": "https://api.undetectable.ai/rewrite",
    },
]


def _safe_name(url: str) -> str:
    """Same slug the probe uses for output filenames."""
    return re.sub(r"[^\w.-]", "_", re.sub(r"^https?://", "", url))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score_candidate(
    original: str,
    text: str,
    allowlist: Optional[set] = None,
    keep_structure: bool = False,
) -> Dict[str, object]:
    """Score one candidate on the same scale used everywhere: naturalness
    score, plain register, and high-severity fact drift vs. the original
    (dropped numbers / negation flips)."""
    report = analyze(text, allowlist=allowlist, keep_structure=keep_structure)
    facts = [
        i for i in preservation_issues(original, text) if i["severity"] == "high"
    ]
    return {
        "score": report.score,
        "plain": round(plain_register_score(text), 3),
        "fact_issues": sorted({i["snippet"] for i in facts}),
        "fact_lost": len(facts),
    }


def _rank_key(c: Dict, floor: float) -> tuple:
    # (clears floor, naturalness, plainness, fewest lost facts) — descending.
    return (
        1 if c["score"] >= floor else 0,
        c["score"],
        c["plain"],
        -c["fact_lost"],
    )


def _reason(c: Dict, floor: float) -> str:
    if c["score"] >= floor:
        base = f"clears floor {floor} (score {c['score']})"
    else:
        base = f"below floor {floor} (score {c['score']})"
    if c["fact_lost"]:
        base += f" — lost {c['fact_lost']} fact(s)"
    return f"{base}; plain-register {c['plain']}"


def select_best(
    original: str, candidates: List[Dict], floor: float = DEFAULT_FLOOR
) -> List[Dict]:
    """Rank *candidates* (already scored) best-first, tagging each with
    ``rank``, ``best`` and an explainable ``reason``. Deterministic: ties
    fall back to provider order (local engines first)."""
    for c in candidates:
        if c.get("text", "").strip() == original.strip():
            c["no_change"] = True
    ranked = sorted(
        candidates,
        key=lambda c: _rank_key(c, floor),
        reverse=True,
    )
    for i, c in enumerate(ranked):
        c["rank"] = i + 1
        c["best"] = i == 0
        c["reason"] = _reason(c, floor)
    return ranked


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
def _http_json(url: str, payload: Dict, headers: Dict, timeout: int = 45) -> Dict:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - HTTPS only
        return json.loads(resp.read().decode("utf-8"))


def _extract_text(data: Dict) -> Optional[str]:
    """Defensive parse across vendor response shapes that drift over time."""
    for path in (
        lambda d: (d.get("data") or {}).get("text"),
        lambda d: d.get("text"),
        lambda d: d.get("output"),
        lambda d: d.get("result"),
        lambda d: (d.get("data") or {}).get("result"),
    ):
        out = path(data)
        if isinstance(out, str) and out.strip():
            return out.strip()
    return None


def provider_stealthgpt(text: str) -> Dict:
    key = os.environ.get("STEALTHGPT_API_KEY")
    if not key:
        return {
            "provider": "stealthgpt",
            "label": "StealthGPT",
            "text": None,
            "blocked": "not_configured",
            "note": "add STEALTHGPT_API_KEY to .env.local to enable live comparison",
        }
    try:
        data = _http_json(
            "https://api.stealthgpt.ai/api/text/humanize",
            {"text": text, "target": "natural, human-written"},
            {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        out = _extract_text(data)
        if not out:
            return {"provider": "stealthgpt", "label": "StealthGPT", "text": None,
                    "blocked": "error", "note": f"unexpected response: {str(data)[:200]}"}
        return {"provider": "stealthgpt", "label": "StealthGPT", "text": out,
                "method": "StealthGPT official API"}
    except Exception as err:  # noqa: BLE001 - reported honestly, never fatal
        return {"provider": "stealthgpt", "label": "StealthGPT", "text": None,
                "blocked": "error", "note": str(err)}


def provider_undetectable(text: str) -> Dict:
    key = os.environ.get("UNDETECTABLE_API_KEY")
    if not key:
        return {
            "provider": "undetectable",
            "label": "Undetectable AI",
            "text": None,
            "blocked": "not_configured",
            "note": "add UNDETECTABLE_API_KEY to .env.local to enable live comparison",
        }
    try:
        data = _http_json(
            "https://api.undetectable.ai/rewrite",
            {
                "content": text,
                "readability": "University",
                "purpose": "General Writing",
                "strength": "More Human",
            },
            {"Content-Type": "application/json", "api-key": key},
        )
        out = _extract_text(data)
        # Newer API returns a job id that is polled afterwards.
        if not out and isinstance(data.get("id"), str):
            for _ in range(2):
                time.sleep(3)
                import urllib.request

                req = urllib.request.Request(
                    f"https://api.undetectable.ai/detect/{data['id']}",
                    headers={"api-key": key},
                )
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                    data = json.loads(resp.read().decode("utf-8"))
                out = _extract_text(data)
                if out:
                    break
        if not out:
            return {"provider": "undetectable", "label": "Undetectable AI",
                    "text": None, "blocked": "error",
                    "note": f"unexpected response: {str(data)[:200]}"}
        return {"provider": "undetectable", "label": "Undetectable AI", "text": out,
                "method": "Undetectable AI official API"}
    except Exception as err:  # noqa: BLE001
        return {"provider": "undetectable", "label": "Undetectable AI",
                "text": None, "blocked": "error", "note": str(err)}


def provider_scraped(probe_dir: Optional[str]) -> List[Dict]:
    """Load candidates captured by tools/humanizer_site_probe.mjs from a
    probe report directory (CLI-only; the web app never scrapes)."""
    if not probe_dir:
        return []
    report_path = os.path.join(probe_dir, "probe_report.json")
    if not os.path.isfile(report_path):
        return []
    try:
        with open(report_path, encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError):
        return []
    out: List[Dict] = []
    for entry in report:
        if not entry.get("ok"):
            continue
        txt_path = os.path.join(probe_dir, _safe_name(entry["url"]) + ".txt")
        if os.path.isfile(txt_path):
            with open(txt_path, encoding="utf-8") as fh:
                text = fh.read().strip()
            if text:
                out.append({
                    "provider": f"scraped:{_safe_name(entry['url'])}",
                    "label": entry["url"],
                    "text": text,
                    "method": "live scrape via headless Chrome (site's own free tier)",
                })
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_comparison(
    text: str,
    style: str = DEFAULT_STYLE,
    floor: float = DEFAULT_FLOOR,
    use_llm: bool = False,
    external: bool = True,
    probe_dir: Optional[str] = None,
) -> Dict:
    """Run the same *text* through every available humanizer, rank the
    outputs, and return ``{original, floor, style, candidates, blocked,
    best}``. External API providers are attempted only when their key is
    configured; anything unreachable is reported in ``blocked`` with an
    honest reason — never silently skipped."""
    text = (text or "").strip()
    profile = get_style(style)
    allowlist = profile.get("allowlist")
    keep_structure = profile.get("keep_structure", False)

    original_report = analyze(
        text, allowlist=allowlist, keep_structure=keep_structure
    )

    candidates: List[Dict] = []
    blocked: List[Dict] = []

    # 1. Local deterministic engine — always on, dependency-free.
    out, _, _ = deterministic_rewrite(text, intensity=0.5, allowlist=allowlist)
    candidates.append({
        "provider": "local-deterministic",
        "label": "Naturalizer (deterministic)",
        "text": out,
        "method": f"deterministic {len(PLAIN_SWAPS)}-slot + rhythm pass",
    })

    # 2. Local perfect loop — always on; LLM path only when configured.
    try:
        res = feedback_humanize(
            Naturalizer(), text, style=style, use_llm=use_llm,
            max_passes=3, floor=int(floor),
        )
        candidates.append({
            "provider": "local-perfect-loop",
            "label": "Naturalizer (perfect loop)",
            "text": res["text"],
            "method": res["method"],
            "passes": res["passes"],
            "scores": res["scores"],
        })
    except Exception as err:  # noqa: BLE001
        blocked.append({
            "provider": "local-perfect-loop", "label": "Naturalizer (perfect loop)",
            "blocked": "error", "note": str(err),
        })

    # 3. External API providers (best-effort, key-gated).
    if external:
        for fn in (provider_stealthgpt, provider_undetectable):
            r = fn(text)
            if r.get("text"):
                candidates.append(r)
            else:
                blocked.append({k: v for k, v in r.items() if k != "text"})

    # 4. Live scraped outputs (CLI-only).
    candidates.extend(provider_scraped(probe_dir))

    # Score every candidate with the shared gate, then rank.
    for c in candidates:
        c.update(
            score_candidate(
                text, c["text"], allowlist=allowlist, keep_structure=keep_structure
            )
        )
    ranked = select_best(text, candidates, floor)

    return {
        "original": {
            "score": original_report.score,
            "plain": round(plain_register_score(text), 3),
        },
        "floor": floor,
        "style": style,
        "candidates": ranked,
        "blocked": blocked,
        "best": ranked[0] if ranked else None,
    }


def provider_status() -> List[Dict]:
    """Configured status of every comparison provider (the panel row)."""
    rows = [
        {"provider": "local-deterministic", "label": "Naturalizer (deterministic)",
         "configured": True, "env": None},
        {"provider": "local-perfect-loop", "label": "Naturalizer (perfect loop)",
         "configured": True, "env": None},
    ]
    for p in _API_PROVIDERS:
        configured = bool(os.environ.get(p["env"]))
        rows.append({
            "provider": p["name"], "label": p["label"], "configured": configured,
            "env": p["env"],
            "note": None if configured else f"add {p['env']} to .env.local to enable",
        })
    rows.append({
        "provider": "scraped", "label": "Live site scrape (headless Chrome)",
        "configured": True, "env": None,
        "note": "CLI-only: python tools/competitor_compare.py --scrape",
    })
    return rows
