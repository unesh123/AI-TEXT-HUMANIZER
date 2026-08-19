"""Style profiles for the naturalizer.

Each profile tunes how the rewrite behaves for a given register. Profiles
currently control:

* ``allowlist`` — phrases that are *natural* in this register and should not
  be flagged or rewritten. This includes register-appropriate clichés and
  hedges: Business keeps its meeting-room idiom ("circle back", "touch
  base"), Casual keeps spoken idiom ("silver lining", "to be honest"),
  while Academic strips all of them as AI tells. Entries must match the
  exact ``PHRASE_SWAPS`` / ``WORD_SWAPS`` keys where a swap exists, plus the
  detector pattern text for detector-only phrases.
* ``keep_structure`` — when true, the detector does not flag the
  structured-answer shape (list/heading lines). Business keeps bulleted
  summaries; the other registers flag them as an AI tell.
* ``min_words`` / ``max_words`` — sentence-length targets for rhythm work.
* ``description`` — shown in the UI so users pick the right profile.
"""

from __future__ import annotations

from typing import Dict


def _profile(
    name: str,
    label: str,
    description: str,
    allowlist: tuple = (),
    min_words: int = 14,
    max_words: int = 45,
    keep_structure: bool = False,
    contractions: bool = False,
) -> dict:
    return {
        "name": name,
        "label": label,
        "description": description,
        "allowlist": set(allowlist),
        "min_words": min_words,
        "max_words": max_words,
        "keep_structure": keep_structure,
        "contractions": contractions,
    }


STYLES: Dict[str, dict] = {
    "academic": _profile(
        "academic",
        "Academic",
        "Formal, measured tone for essays, papers, and reports. Keeps technical "
        "vocabulary while cutting hedging and formulaic transitions.",
        allowlist=(
            "robust",        # common in peer-reviewed writing
            "crucial",       # accepted academic emphasis
            "significantly", # standard statistical/causal phrasing
            "notably",
            "ultimately",
            "overall",
        ),
        min_words=16,
        max_words=45,
    ),
    "business": _profile(
        "business",
        "Business",
        "Direct, confident tone for emails, memos, and proposals. Keeps "
        "everyday corporate idiom (circle back, touch base, move the needle) "
        "and bulleted summaries, but still strips inflated AI verbs (leverage, "
        "synergy, utilize).",
        keep_structure=True,
        allowlist=(
            # Meeting-room idiom — the vocabulary business writers actually use.
            "circle back", "circle back on", "circle back to",
            "circled back", "circling back", "circles back",
            "touch base", "touch base with", "touched base with",
            "touching base with",
            "drill down", "drill down into", "drilled down", "drilling down",
            "move the needle",
            "actionable",
            "deep dive into",
            "low-hanging fruit",
            "hit the ground running", "hits the ground running",
            "hitting the ground running",
            "raise the bar", "raises the bar", "raised the bar",
            "raising the bar",
            "steep learning curve",
            "pave the way for", "paves the way for", "paved the way for",
            "paving the way for",
            "at the heart of",
            "on the same page",
            "key takeaway", "key takeaways",
            "game plan",
            "in the pipeline",
            "bandwidth to", "bandwidth for",
            "touchpoint", "touchpoints",
        ),
        min_words=12,
        max_words=38,
        contractions=True,
    ),
    "creative": _profile(
        "creative",
        "Creative",
        "Flowing, expressive tone for essays, blog posts, and narrative writing. "
        "Focuses on rhythm and sentence variety.",
        min_words=8,
        max_words=42,
        contractions=True,
    ),
    "casual": _profile(
        "casual",
        "Casual",
        "Plain, conversational tone for personal writing. Short sentences, "
        "contractions welcome, and everyday idioms (silver lining, to be "
        "honest) left alone.",
        allowlist=(
            "basically",
            "overall",
            # Spoken idiom and conversational hedges people actually say.
            "silver lining", "a silver lining", "the silver lining",
            "in a nutshell",
            "double-edged sword", "is a double-edged sword",
            "to be honest",
            "for what it's worth,", "for what it's worth",
            "let's be honest:", "let's be honest,", "let's be honest.",
            "more often than not",
            "without a doubt",
            "at first glance",
            "at the end of the day",
        ),
        min_words=6,
        max_words=32,
        contractions=True,
    ),
}

#: Order used by the UI dropdown.
STYLE_NAMES = ["academic", "business", "creative", "casual"]

#: Default style when none is supplied.
DEFAULT_STYLE = "academic"


def get_style(name: str) -> dict:
    """Return the profile for *name*, falling back to the default."""
    style = STYLES.get(name or DEFAULT_STYLE)
    if style is None:
        style = STYLES[DEFAULT_STYLE]
    # Never hand out the live set object (allowlist may be mutated).
    return {**style, "allowlist": set(style["allowlist"])}
