"""Local rewrite comparison helpers.

This module compares approved local rewrite approaches only. It does not call
external humanizers, scrape competitor products, reverse-engineer private
systems, or optimize text against third-party detector outcomes. The HTTP
comparison route is retired; these helpers remain for local experiments and
backward-compatible imports.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .critics import preservation_issues
from .detectors import analyze
from .human_memory import plain_register_score
from .styles import DEFAULT_STYLE, get_style
from .transforms import rewrite as deterministic_rewrite

DEFAULT_FLOOR = 75.0


def score_candidate(original: str, text: str, allowlist: Optional[set] = None, keep_structure: bool = False) -> Dict[str, object]:
    """Score one local candidate for quality and factual preservation."""
    report = analyze(text, allowlist=allowlist, keep_structure=keep_structure)
    facts = [i for i in preservation_issues(original, text) if i["severity"] == "high"]
    return {
        "score": report.score,
        "plain": round(plain_register_score(text), 3),
        "fact_issues": sorted({i["snippet"] for i in facts}),
        "fact_lost": len(facts),
    }


def _rank_key(candidate: Dict, floor: float) -> tuple:
    return (
        1 if candidate["score"] >= floor else 0,
        candidate["score"],
        candidate["plain"],
        -candidate["fact_lost"],
    )


def _reason(candidate: Dict, floor: float) -> str:
    status = "clears" if candidate["score"] >= floor else "below"
    return f"{status} local quality floor {floor} (score {candidate['score']}); plain-register {candidate['plain']}"


def select_best(original: str, candidates: List[Dict], floor: float = DEFAULT_FLOOR) -> List[Dict]:
    """Rank local candidates with an explainable quality-first ordering."""
    for candidate in candidates:
        if candidate.get("text", "").strip() == original.strip():
            candidate["no_change"] = True
    ranked = sorted(candidates, key=lambda candidate: _rank_key(candidate, floor), reverse=True)
    for index, candidate in enumerate(ranked):
        candidate["rank"] = index + 1
        candidate["best"] = index == 0
        candidate["reason"] = _reason(candidate, floor)
    return ranked


def provider_scraped(probe_dir: Optional[str]) -> List[Dict]:
    """Return no results; competitor-site scraping is intentionally retired."""
    return []


def provider_status() -> List[Dict[str, object]]:
    """Return the approved local comparison capability only."""
    return [{"provider": "local", "label": "Naturalizer local engine", "configured": True}]


def run_comparison(text: str, style: str = DEFAULT_STYLE, floor: float = DEFAULT_FLOOR, **_: object) -> Dict:
    """Compare a few local rewrite intensities with transparent provenance."""
    text = (text or "").strip()
    profile = get_style(style)
    allowlist = profile.get("allowlist")
    keep_structure = profile.get("keep_structure", False)
    candidates: List[Dict] = []
    for label, intensity in (("Light local edit", 0.35), ("Balanced local rewrite", 0.6), ("Thorough local rewrite", 0.9)):
        rewritten, _, _ = deterministic_rewrite(
            text,
            allowlist=allowlist,
            min_words=profile["min_words"],
            max_words=profile["max_words"],
            intensity=intensity,
        )
        candidate = {
            "provider": f"local-{intensity}",
            "label": label,
            "text": rewritten,
            "method": "Naturalizer local deterministic engine",
        }
        candidate.update(score_candidate(text, rewritten, allowlist=allowlist, keep_structure=keep_structure))
        candidates.append(candidate)
    ranked = select_best(text, candidates, floor)
    return {
        "original": {"score": analyze(text, allowlist=allowlist, keep_structure=keep_structure).score, "plain": round(plain_register_score(text), 3)},
        "floor": floor,
        "style": style,
        "candidates": ranked,
        "blocked": [],
        "best": ranked[0] if ranked else None,
        "scope_note": "Local rewrite approaches only; no external provider or detector-outcome comparison is used.",
    }
