"""User history: every input + rewrite + scores, persisted to disk.

Entries are appended whenever a humanize / perfect run completes, so the
user can revisit what they rewrote, reload an input, or copy an output.
Storage is a tiny JSON list under ``NATURALIZER_STATE_DIR`` (default
``state/``) — the same directory plan usage uses — and is capped so the
file cannot grow without bound. Pure stdlib.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

MAX_ENTRIES = 100
MAX_TEXT_CHARS = 20_000  # per entry, guards against multi-MB blobs


def _state_dir() -> Path:
    return Path(os.environ.get("NATURALIZER_STATE_DIR", "state")).resolve()


def _history_file() -> Path:
    return _state_dir() / "history.json"


def _read() -> List[Dict]:
    path = _history_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict)]
    except (OSError, ValueError):
        pass
    return []


def _write(entries: List[Dict]) -> None:
    path = _history_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except OSError:  # pragma: no cover - read-only FS; degrade gracefully
        pass


def _next_id() -> str:
    return uuid.uuid4().hex


def save(
    text: str,
    output: str,
    score: float,
    *,
    style: str = "academic",
    mode: str = "naturalize",
    provider: str = "auto",
    llm_used: bool = False,
    intensity: float = 0.5,
    plan: str = "free",
    extra: Optional[Dict] = None,
) -> Optional[str]:
    """Append one history entry; returns its id (``None`` if empty).

    *mode* distinguishes how the rewrite was produced — ``naturalize``,
    ``perfect`` (feedback loop), ``batch`` — and *extra* carries anything
    mode-specific (e.g. ``{"passes": 3}`` for perfect runs).
    """
    text = (text or "").strip()
    output = (output or "").strip()
    if not text or not output:
        return None
    entry = {
        "id": _next_id(),
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "style": style,
        "mode": mode,
        "provider": provider,
        "llm_used": bool(llm_used),
        "intensity": float(intensity),
        "plan": plan,
        "input": text[:MAX_TEXT_CHARS],
        "output": output[:MAX_TEXT_CHARS],
        "score": round(float(score), 1),
    }
    if extra:
        entry.update(extra)
    entries = _read()
    entries.insert(0, entry)
    _write(entries[:MAX_ENTRIES])
    return entry["id"]


def list_entries(limit: int = 50) -> List[Dict]:
    """Recent entries, newest first (max *limit*)."""
    return _read()[: max(0, int(limit))]


def get(entry_id: str) -> Optional[Dict]:
    for entry in _read():
        if entry.get("id") == entry_id:
            return entry
    return None


def remove(entry_id: str) -> bool:
    """Delete one entry; returns ``True`` when it existed."""
    entries = _read()
    kept = [e for e in entries if e.get("id") != entry_id]
    if len(kept) == len(entries):
        return False
    _write(kept)
    return True


def clear() -> int:
    """Wipe history; returns how many entries were removed."""
    count = len(_read())
    _write([])
    return count


def public(entry: Dict) -> Dict:
    """The fields the UI is allowed to see (no surprises in the JSON)."""
    keys = (
        "id", "iso", "ts", "style", "mode", "provider", "llm_used",
        "intensity", "plan", "input", "output", "score", "passes",
    )
    return {k: entry.get(k) for k in keys if k in entry}
