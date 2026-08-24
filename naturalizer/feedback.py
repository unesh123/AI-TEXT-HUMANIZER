"""Local-only compatibility helpers.

Naturalizer intentionally does not provide external detector scoring, detector
outcome optimization, cloud rewrite routing, or feedback loops designed to
produce undetectable output. The main app uses the local detector directly.
These small helpers remain only so older imports fail safely and clearly.
"""

from __future__ import annotations

from typing import Dict, List

DETECTORS: List[Dict[str, object]] = [
    {"name": "local", "label": "Naturalizer local detector", "key": None, "live": True},
]

HUMAN_FLOOR = 80
PLAIN_FLOOR = 0.78
MAX_PASSES = 1


def detector_status() -> List[Dict[str, object]]:
    """Return the one detector that Naturalizer actually runs locally."""
    return [
        {
            "name": "local",
            "label": "Naturalizer local detector",
            "configured": True,
            "live": True,
            "env": None,
            "note": "Local writing-signal analysis only; not proof of authorship.",
        }
    ]


def scan_live(text: str) -> List[Dict[str, object]]:
    """External detector scoring is intentionally unavailable."""
    return []


def feedback_humanize(*args, **kwargs):
    """Reject the retired detector-outcome feedback loop explicitly."""
    raise RuntimeError(
        "Feedback-loop optimization for external detector outcomes is outside Naturalizer's scope."
    )
