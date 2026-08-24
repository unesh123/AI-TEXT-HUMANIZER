"""Text Naturalizer.

A dependency-free toolkit that turns stiff, machine-generated prose into
natural, human-sounding writing. It scores drafts for "AI tells"
(repetitive openers, filler phrases, uniform sentence rhythm) and rewrites
them with deterministic, semantics-preserving transformations. Naturalizer
runs fully locally; no cloud model backend is included.
"""

from .engine import Naturalizer, NaturalizeResult
from .detectors import analyze, NaturalnessReport, Issue
from .styles import STYLES, STYLE_NAMES

__all__ = [
    "Naturalizer",
    "NaturalizeResult",
    "analyze",
    "NaturalnessReport",
    "Issue",
    "STYLES",
    "STYLE_NAMES",
]

__version__ = "0.1.0"
