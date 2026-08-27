"""Free / Pro plan structure with daily word-cap accounting.

Naturalizer uses a local-only plan model:

* **Free** — deterministic humanizer + local AI detector, reference-overlap
  check, document upload, and reviewable before/after output.
* **Pro** — the same local capabilities with a higher local word allowance,
  batch mode, full intensity range, and sentence-level re-humanize.

Cloud LLMs, external detector scoring, feedback loops aimed at detector
outcomes, and multi-language translation chains are deliberately out of scope.
Plan resolution: ``NATURALIZER_PLAN`` env var wins; otherwise the plan stays
``free``. To demo the free experience::

    NATURALIZER_PLAN=free python server.py

Daily word usage is persisted to a tiny JSON file under
``NATURALIZER_STATE_DIR`` (default ``state/``) and resets at midnight.
Pure stdlib.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Tuple

FREE_WORDS_PER_DAY = 1000

PLANS: Dict[str, Dict] = {
    "free": {
        "label": "Free",
        "features": {
            "llm": False,
            "deep": True,
            "batch": False,
            "perfect": False,
            "sentence_rehumanize": True,
            "plagiarism": True,
            "upload": True,
            "max_intensity": 0.5,
            "words_per_day": FREE_WORDS_PER_DAY,
        },
    },
    "pro": {
        "label": "Pro",
        "features": {
            "llm": False,
            "deep": True,
            "batch": True,
            "perfect": False,
            "sentence_rehumanize": True,
            "plagiarism": True,
            "upload": True,
            "max_intensity": 1.0,
            "words_per_day": None,  # unlimited
        },
    },
}

PLAN_NAMES = list(PLANS)


def _state_dir() -> Path:
    return Path(os.environ.get("NATURALIZER_STATE_DIR", "state")).resolve()


def _state_file() -> Path:
    return _state_dir() / "plan_state.json"


def _read_state() -> Dict:
    path = _state_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _write_state(state: Dict) -> None:
    path = _state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:  # pragma: no cover - read-only FS; degrade gracefully
        pass


def _today() -> str:
    return date.today().isoformat()


def current_plan() -> str:
    """Resolve the active local plan from the explicit environment override."""
    value = (os.environ.get("NATURALIZER_PLAN") or "").strip().lower()
    return value if value in PLANS else "free"


def plan_features(plan: Optional[str] = None) -> Dict:
    plan = plan or current_plan()
    return PLANS.get(plan, PLANS["free"])["features"]


def words_used_today() -> int:
    state = _read_state()
    entry = state.get(_today(), {})
    return int(entry.get("words", 0))


def usage_remaining() -> Optional[int]:
    """Words left today for the active plan (``None`` when unlimited)."""
    cap = plan_features().get("words_per_day")
    if cap is None:
        return None
    return max(0, int(cap) - words_used_today())


def record_usage(words: int) -> None:
    """Add *words* to today's counter (no-op for unlimited plans)."""
    cap = plan_features().get("words_per_day")
    if cap is None or words <= 0:
        return
    state = _read_state()
    today = _today()
    entry = state.get(today, {})
    entry["words"] = int(entry.get("words", 0)) + int(words)
    state[today] = entry
    _write_state(state)


def check_word_quota(words: int) -> Tuple[bool, Optional[str]]:
    """Return ``(allowed, error)`` for a request using *words* words.

    Only enforced on the free plan; ``(True, None)`` otherwise. The error
    message tells the user how to unlock Pro.
    """
    features = plan_features()
    cap = features.get("words_per_day")
    if cap is None or words <= 0:
        return True, None
    used = words_used_today()
    if used + int(words) > int(cap):
        remaining = max(0, int(cap) - used)
        return False, (
            f"Free plan daily limit reached — {remaining} of "
            f"{FREE_WORDS_PER_DAY} words left today. Set NATURALIZER_PLAN=pro "
            "to unlock the local Pro allowance."
        )
    return True, None


def status() -> Dict:
    """Plan summary for ``/api/status``."""
    plan = current_plan()
    features = plan_features(plan)
    return {
        "name": plan,
        "label": PLANS[plan]["label"],
        "features": features,
        "words_used_today": words_used_today(),
        "words_remaining_today": usage_remaining(),
        "upgrade_hint": "Set NATURALIZER_PLAN=pro to unlock the local Pro allowance and batch tools.",
    }
