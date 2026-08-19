"""Invisible-Unicode watermark hygiene — the Layer-A borrowing from
guillaumemeyer/watermarks-remover.

AI provenance can hide in *invisible* characters, not just in word choice:
zero-width spaces / joiners, bidi control characters, and exotic filler
codepoints are injected by stealth humanizers and by some generators as a
machine-readable fingerprint the eye never sees. Real detectors and content
managers sniff for them; this module gives the engine the same hygiene:

  * :func:`find_marks` — inventory every invisible codepoint in a string.
  * :func:`strip_marks` — remove them, returning the clean text plus what
    was removed (so the UI can say \"stripped 3 invisible chars\").
  * :func:`check_unicode_marks` — the detector-side Issue, so the naturalness
    score honestly drops when a text carries an invisible fingerprint and the
    rewrite pass is told to remove it.

Severity mirrors watermarks-remover's caution: bidi controls (overrides /
embeddings / isolates, which can *reorder visible text* and flip meaning) are
``high``; every other invisible carrier (zero-width, joiners, soft hyphen,
filler) is ``medium`` — a fingerprint but not a meaning change.

Nothing here touches visible characters: normal prose, punctuation, and
legitimate typographic marks (regular hyphen, apostrophe, accented letters)
pass through untouched, so the human corpus is never altered.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

#: Invisible carriers that carry no visible glyph. ``name`` is the official
#: Unicode name used in reports; ``danger`` marks bidi controls that can
#: reorder the *visible* text and therefore change meaning.
INVISIBLE: Dict[str, Dict[str, bool]] = {
    "\u200b": {"name": "ZERO WIDTH SPACE", "danger": False},
    "\u200c": {"name": "ZERO WIDTH NON-JOINER", "danger": False},
    "\u200d": {"name": "ZERO WIDTH JOINER", "danger": False},
    "\u2060": {"name": "WORD JOINER", "danger": False},
    "\ufeff": {"name": "ZERO WIDTH NO-BREAK SPACE", "danger": False},
    "\u00ad": {"name": "SOFT HYPHEN", "danger": False},
    "\u200e": {"name": "LEFT-TO-RIGHT MARK", "danger": False},
    "\u200f": {"name": "RIGHT-TO-LEFT MARK", "danger": False},
    "\u061c": {"name": "ARABIC LETTER MARK", "danger": True},
    "\u202a": {"name": "LEFT-TO-RIGHT EMBEDDING", "danger": True},
    "\u202b": {"name": "RIGHT-TO-LEFT EMBEDDING", "danger": True},
    "\u202c": {"name": "POP DIRECTIONAL FORMATTING", "danger": True},
    "\u202d": {"name": "LEFT-TO-RIGHT OVERRIDE", "danger": True},
    "\u202e": {"name": "RIGHT-TO-LEFT OVERRIDE", "danger": True},
    "\u2066": {"name": "LEFT-TO-RIGHT ISOLATE", "danger": True},
    "\u2067": {"name": "RIGHT-TO-LEFT ISOLATE", "danger": True},
    "\u2068": {"name": "FIRST STRONG ISOLATE", "danger": True},
    "\u2069": {"name": "POP DIRECTIONAL ISOLATE", "danger": True},
    "\u2061": {"name": "FUNCTION APPLICATION", "danger": False},
    "\u2062": {"name": "INVISIBLE TIMES", "danger": False},
    "\u2063": {"name": "INVISIBLE SEPARATOR", "danger": False},
    "\u034f": {"name": "COMBINING GRAPHEME JOINER", "danger": False},
    "\u180e": {"name": "MONGOLIAN VOWEL SEPARATOR", "danger": False},
    "\u3164": {"name": "HANGUL FILLER", "danger": False},
    "\u2800": {"name": "BRAILLE PATTERN BLANK", "danger": False},
}

#: Compiled table used by :func:`find_marks` / :func:`strip_marks`.
_TRANSLATE: Dict[int, None] = {ord(c): None for c in INVISIBLE}


def find_marks(text: str) -> Dict[str, int]:
    """Count each invisible codepoint present in *text*.

    Returns ``{char: count}`` keyed by the raw character (so callers can
    re-derive the Unicode name from :data:`INVISIBLE`). An empty dict means
    the text is clean.
    """
    counts: Dict[str, int] = {}
    for ch in text:
        if ch in INVISIBLE:
            counts[ch] = counts.get(ch, 0) + 1
    return counts


def strip_marks(text: str) -> Tuple[str, List[str]]:
    """Remove every invisible codepoint from *text*.

    Returns ``(clean_text, removed)`` where *removed* is one entry per
    distinct character class, e.g. ``[\"ZERO WIDTH SPACE ×3\",
    \"RIGHT-TO-LEFT OVERRIDE ×1\"]``. Visible characters are never touched.
    """
    removed: List[str] = []
    for ch, count in find_marks(text).items():
        name = INVISIBLE[ch]["name"]
        removed.append(f"{name} ×{count}")
    if not removed:
        return text, []
    return text.translate(_TRANSLATE), removed


def _describe(counts: Dict[str, int]) -> str:
    parts = [f"{INVISIBLE[ch]['name']} ×{n}" for ch, n in counts.items()]
    return ", ".join(parts)


def check_unicode_marks(text: str) -> List["Issue"]:
    """Detector-side check: return an :class:`Issue` when invisible marks are
    present, else ``[]``. Imported lazily to avoid a cycle with
    ``detectors.py``."""
    from .detectors import Issue

    counts = find_marks(text)
    if not counts:
        return []
    total = sum(counts.values())
    has_danger = any(INVISIBLE[ch]["danger"] for ch in counts)
    names = _describe(counts)
    return [
        Issue(
            kind="unicode",
            severity="high" if has_danger else "medium",
            message=(
                f"{total} invisible Unicode character(s) ({names}) — an "
                "invisible machine/stealth fingerprint, not visible prose"
            ),
            snippet=names[:80],
            suggestion=(
                "strip invisible Unicode characters (zero-width, bidi "
                "controls, joiners) — they are watermark/stealth artifacts"
            ),
        )
    ]
