"""Deterministic, semantics-preserving rewriting transforms.

These transforms are the no-API-key core of the naturalizer. Every function
takes text (or sentences) and returns a modified version, reporting whether
anything actually changed. The goal is to make prose *read* like a person
wrote it — varying rhythm and openers, cutting filler — without altering the
meaning or inventing facts.

All transforms are pure functions of their input plus a seeded RNG, so the
output is reproducible for a given input.
"""

from __future__ import annotations

import random
import re
from functools import lru_cache
from typing import Callable, List, Optional, Tuple

from .detectors import _overlaps_allowlist, split_sentences
from .unicode_marks import strip_marks


# ---------------------------------------------------------------------------
# Synonym tables (conservative — only swaps that keep the meaning intact)
# ---------------------------------------------------------------------------

#: Swap-in alternatives for formulaic transitions.
TRANSITION_SWAPS = {
    "furthermore": ["beyond that", "what is more", "also", "and"],
    "moreover": ["besides", "on top of that", "also", "and"],
    "additionally": ["also", "on top of that", "plus", "and"],
    "however": ["that said", "even so", "still", "all the same"],
    "therefore": ["so", "as a result", "that is why", "consequently"],
    "thus": ["so", "as a result", "in turn"],
    "in conclusion": ["taken together", "all told", "in the end"],
    "in summary": ["in short", "all told", "the upshot is"],
    "to sum up": ["in short", "the upshot is"],
    "overall": ["on the whole", "all told", "in the end"],
    "ultimately": ["in the end", "when it comes down to it"],
    "essentially": ["basically", "in practice"],
    "importantly": ["most of all", "above all"],
    "notably": ["in particular", "for example"],
    "significantly": ["markedly", "clearly", "substantially"],
    "additionally": ["also", "plus"],
}

#: Direct word-level replacements (exact, safe in almost any context).
WORD_SWAPS = {
    "delve": "dig",
    "delves": "digs",
    "delved": "dug",
    "delve into": "dig into",
    "delves into": "digs into",
    "delved into": "dug into",
    "utilize": "use",
    "utilizes": "uses",
    "utilized": "used",
    "utilizing": "using",
    "leverage": "build on",
    "leveraging": "building on",
    "unlock": "open up",
    "unlocks": "opens up",
    "unlocked": "opened up",
    "unlocking": "opening up",
    "robust": "solid",
    "holistic": "complete",
    "comprehensive": "thorough",
    "seamless": "smooth",
    "seamlessly": "smoothly",
    # Keep the following source preposition: “a plethora of opportunities”
    # becomes “a range of opportunities”, not “a range of of opportunities”.
    "plethora": "range",
    "myriad": "many",
    "multifaceted": "many-sided",
    "paramount": "essential",
    "pivotal": "key",
    "crucial": "key",
    "crucially": "above all",
    "cutting-edge": "current",
    "state-of-the-art": "current",
    "ever-evolving": "changing",
    "game-changer": "major shift",
    "landscape": "field",
    "tapestry": "mix",
    "realm": "world",
    "underscore": "highlight",
    "underscores": "highlights",
    "underscored": "highlighted",
    "underscoring": "highlighting",
    "testament": "proof",
    "elevate": "improve",
    "elevates": "improves",
    "elevated": "improved",
    "journey": "process",
    "navigate": "work through",
    "navigates": "works through",
    "navigating": "working through",
    "cornerstone": "foundation",
    "backbone": "core",
    "undoubtedly": "clearly",
    "consequently": "so",
    "numerous": "many",
    # Second expansion of word-level swaps.
    "modern-day": "modern",
    "synergy": "combined effort",
    "empower": "enable",
    "empowers": "enables",
    "empowered": "enabled",
    "empowering": "enabling",
    "foster": "encourage",
    "fosters": "encourages",
    "fostered": "encouraged",
    "fostering": "encouraging",
    "cultivate": "build",
    "cultivates": "builds",
    "cultivated": "built",
    "cultivating": "building",
    "facilitate": "help",
    "facilitates": "helps",
    "facilitated": "helped",
    "facilitating": "helping",
    "streamline": "simplify",
    "streamlines": "simplifies",
    "streamlined": "simplified",
    "streamlining": "simplifying",
    "showcase": "highlight",
    "showcases": "highlights",
    "showcased": "highlighted",
    "showcasing": "highlighting",
    "revolutionize": "transform",
    "revolutionizes": "transforms",
    "revolutionized": "transformed",
    "revolutionizing": "transforming",
    "harness": "use",
    "harnesses": "uses",
    "harnessed": "used",
    "harnessing": "using",
    "paradigm": "model",
    "ever-growing": "growing",
    "ever-increasing": "growing",
    "ever-changing": "changing",
    "ever-present": "constant",
    "game-changing": "major",
    "best-in-class": "top-tier",
    "actionable": "useful",
    # Third expansion of word-level swaps.
    "reimagine": "rethink",
    "reimagines": "rethinks",
    "reimagined": "rethought",
    "reimagining": "rethinking",
    "redefine": "rethink",
    "redefines": "rethinks",
    "redefined": "rethought",
    "redefining": "rethinking",
    "ever-expanding": "growing",
    # Fourth expansion: word-choice predictability. AI output leans on
    # mid-frequency formal vocabulary ("training echoes"); real detectors
    # weigh this, so the rewrite lowers rare-word density toward the common
    # core humans actually use. Each swap is meaning-preserving and absent
    # from the human corpus (verified: 0 hits each).
    "ultimately": "in the end",
    "increasingly": "more and more",
    "additionally": "also",
    "merely": "just",
    "demonstrate": "show",
    "demonstrates": "shows",
    "demonstrated": "showed",
    "demonstrating": "showing",
    "commence": "start",
    "commences": "starts",
    "commenced": "started",
    "commencing": "starting",
    "essentially": "basically",
    "subsequently": "then",
    "transformation": "change",
    "transformations": "changes",
    "adapt": "adjust",
    "adapts": "adjusts",
    "adapted": "adjusted",
    "adapting": "adjusting",
    "emerging": "new",
    "strategies": "plans",
    "strategy": "plan",
    "possibilities": "options",
    "innovation": "new ideas",
    "necessity": "need",
}

#: Multi-word phrases replaced wholesale (longer first so greedy matching wins).
PHRASE_SWAPS = {
    "in today's fast-paced world": "these days",
    "in today's ever-evolving world": "these days",
    "in today's world": "these days",
    "in today's digital age": "now",
    "in the ever-evolving landscape of": "in the changing world of",
    "in the evolving landscape of": "in the changing world of",
    "in the landscape of": "in the world of",
    "it is important to note that": "",
    "it is worth noting that": "",
    "it is essential to note that": "",
    "it is crucial to note that": "",
    "it is important to mention that": "",
    "it's important to note that": "",
    "it's worth noting that": "",
    "it's important to mention that": "",
    "it should be noted that": "",
    "it is noteworthy that": "",
    "navigate the complexities of": "work through",
    "navigate the challenges of": "work through",
    "navigate the intricacies of": "work through",
    "navigate the nuances of": "work through",
    "navigate the terrain of": "work through",
    "navigate the landscape of": "work through",
    "navigate the world of": "work through",
    "to sum up": "in short",
    "in essence": "",
    "all in all": "all told",
    "in the realm of": "in the world of",
    "the realm of": "the world of",
    # Hedging constructions — safe to remove anywhere (that-clauses).
    "it's worth mentioning that": "",
    "it is worth mentioning that": "",
    "it is worth emphasizing that": "",
    "it cannot be overstated that": "",
    "it can be seen that": "",
    "it is clear that": "",
    "it is evident that": "",
    "there is no doubt that": "",
    "one can observe that": "",
    "it goes without saying that": "",
    "it is safe to say that": "",
    "it is no secret that": "",
    # Clichés with safe plain-English replacements.
    "is a double-edged sword": "has trade-offs",
    "the elephant in the room": "the obvious issue",
    "elephant in the room": "obvious issue",
    "the tip of the iceberg": "the beginning",
    "low-hanging fruit": "easy wins",
    "silver bullet": "perfect solution",
    "paradigm shift": "fundamental change",
    "think outside the box": "think creatively",
    # Formulaic openers and transitions.
    "as a result of": "because of",
    "as a result": "so",
    "at the end of the day": "in the end",
    "when all is said and done": "in the end",
    "in recent years": "recently",
    "in the modern era": "these days",
    "when it comes to": "regarding",
    "in other words": "put differently",
    "that being said": "even so",
    "having said that": "even so",
    "deep dive into": "close look at",
    "a wide range of": "many",
    "a variety of": "many",
    "a number of": "several",
    # Second expansion: wordy constructions with safe plain-English swaps.
    "due to the fact that": "because",
    "despite the fact that": "although",
    "in the event that": "if",
    "as to whether": "whether",
    "with respect to": "about",
    "with regard to": "about",
    "at this point in time": "now",
    "at the present time": "now",
    "in the years to come": "in the future",
    "in the not-too-distant future": "soon",
    "this day and age": "these days",
    "in a nutshell": "in short",
    # ChatGPT self-reference tells — drop the announcement entirely. Comma
    # variants first (longest-first) so "As an AI, I …" loses its trailing
    # comma instead of leaving a stray one behind.
    "as an ai language model,": "",
    "as an ai language model": "",
    "as a language model,": "",
    "as a language model": "",
    "as an ai,": "",
    "as an ai": "",
    "owing to": "because of",
    "by the same token": "likewise",
    "in light of": "given",
    "in the grand scheme of things": "overall",
    "shed light on": "clarify",
    "sheds light on": "clarifies",
    "shedding light on": "clarifying",
    "at first glance": "on the surface",
    "without a doubt": "clearly",
    "as a matter of fact": "in fact",
    # Hedging constructions — safe to remove anywhere (that-clauses).
    "it bears mentioning that": "",
    "it should come as no surprise that": "",
    "the fact of the matter is that": "",
    "as we all know,": "",
    "it is imperative to": "we must",
    # "plays a role in" -> "is central to" (common literal forms).
    "plays a key role in": "is central to",
    "plays a crucial role in": "is central to",
    "plays a vital role in": "is central to",
    "plays a significant role in": "is central to",
    "plays an important role in": "is central to",
    "plays an essential role in": "is central to",
    "play a key role in": "are central to",
    "play a crucial role in": "are central to",
    "play a vital role in": "are central to",
    # Residual LLM-output tells (seen on StealthWriter-flagged samples):
    # meta phrases, "X lies at the intersection of" metaphors, ritual
    # academic verbs, and stock personas.
    "in the broad sense of the term": "",
    "in the broadest sense of the term": "",
    "lies at the intersection of": "combines",
    "sits at the intersection of": "combines",
    "falls at the crossroads of": "combines",
    "lies at the crossroads of": "combines",
    "critically analysed": "looked closely at",
    "critically analyse": "look closely at",
    "critically analyzes": "looks closely at",
    "critically analyzing": "looking closely at",
    "critically assessed": "carefully assessed",
    "discerning users": "thoughtful users",
    # Fourth expansion: institutional/academic scaffolding that survives LLM
    # rewrites (seen on a StealthWriter-flagged "reflective essay" sample).
    "our analysis focused on": "we focused on",
    "our analysis focuses on": "we focus on",
    "must also consider": "also need to think about",
    "intended and unintended": "expected and unexpected",
    "we first sought to": "we started by trying to",
    "we were also asked to": "we also had to",
    "this assignment required us to": "the assignment asked us to",
    "was successfully implemented": "was put into practice",
    "had been successfully implemented": "had been put into practice",
    "has been successfully implemented": "has been put into practice",
    "have been successfully implemented": "have been put into practice",
    # Third expansion: clichés and corporate tics with safe plain swaps.
    # Bare "hit the ground running" is tense-ambiguous (past *and* habitual
    # present), so it stays detector-only; only the unambiguous forms swap.
    "hits the ground running": "starts strong",
    "hitting the ground running": "starting strong",
    "raise the bar": "set a higher standard",
    "raises the bar": "sets a higher standard",
    "raised the bar": "set a higher standard",
    "raising the bar": "setting a higher standard",
    "pave the way for": "help create",
    "paves the way for": "helps create",
    "paved the way for": "helped create",
    "paving the way for": "helping create",
    "steep learning curve": "tough learning period",
    "at the heart of": "at the core of",
    # Article-aware: "a silver lining" -> "an upside" (never "a upside").
    "a silver lining": "an upside",
    "the silver lining": "the upside",
    "silver lining": "upside",
    "move the needle": "make a difference",
    "circle back on": "revisit",
    "circle back to": "revisit",
    "circle back": "revisit",
    "circled back": "revisited",
    "circling back": "revisiting",
    "circles back": "revisits",
    "touch base with": "check in with",
    "touch base": "check in",
    "touched base with": "checked in with",
    "touching base with": "checking in with",
    "drill down into": "dig into",
    "drill down": "dig deeper",
    "drilled down": "dug into",
    "drilling down": "digging into",
    "to that end": "for that reason",
    # Speech hedges — punctuation variants first so no leading ", " survives.
    "for what it's worth,": "",
    "for what it's worth": "",
    "let's be honest:": "",
    "let's be honest,": "",
    "let's be honest.": "",
    "more often than not": "most of the time",
    "one cannot": "we cannot",
    # Third expansion: modern LLM-era tells with safe plain-English swaps.
    # "understand/realize/recognize" hedge variants (that-clauses, safe to cut).
    "it is important to understand that": "",
    "it's important to understand that": "",
    "it is essential to understand that": "",
    "it is crucial to understand that": "",
    "it is vital to understand that": "",
    "it is important to realize that": "",
    "it is important to recognize that": "",
    "it is important to highlight that": "",
    "it's important to highlight that": "",
    "it is essential to highlight that": "",
    "it is crucial to highlight that": "",
    "it is worth highlighting that": "",
    "it's worth highlighting that": "",
    "keep in mind that": "",
    "bridge the gap": "close the gap",
    "a wealth of": "a lot of",
    "a treasure trove of": "a rich collection of",
    "treasure trove of": "rich collection of",
    "rapidly evolving": "changing fast",
    "digital landscape": "online world",
    "transform the way we": "change how we",
    "transforms the way we": "changes how we",
    "harness the power of": "use the power of",
    "unlock the full potential of": "make the most of",
    "unlock the potential of": "make the most of",
    "what's more,": "besides,",
    "what's more": "besides",
    "first and foremost": "above all",
    "last but not least": "finally",
    "to summarize": "in short",
    "to conclude": "in the end",
    "all things considered": "on balance",
    "in today's society": "these days",
    "in the digital age": "these days",
    # Wikipedia "Signs of AI writing" patterns (blader/humanizer set).
    "let's dive in": "",
    "let's jump in": "",
    "here's what you need to know": "",
    "without further ado": "",
    "buckle up": "",
    "great question": "",
    "you're absolutely right": "",
    "you are absolutely right": "",
    "excellent question": "",
    "excellent point": "",
    "nestled in": "located in",
    "nestled within": "located in",
    "breathtaking": "striking",
    "the future looks bright": "there is room to grow",
    "the sky's the limit": "there is a lot of room to grow",
    "the sky is the limit": "there is a lot of room to grow",
}

#: Near-synonym variety for common, register-neutral words. Used by the
#: intensity-controlled synonym pass so repeated runs (different seeds)
#: produce different prose instead of one fixed algorithm. Every entry is
#: safe in all four styles and keeps the meaning intact; the alternatives
#: are picked at random per occurrence.
SYNONYM_VARIETY: Dict[str, List[str]] = {
    "important": ["key", "significant", "notable"],
    "major": ["big", "significant"],
    "huge": ["enormous", "massive"],
    "big": ["large", "major"],
    "small": ["little", "minor"],
    "easy": ["simple", "straightforward"],
    "difficult": ["hard", "tough"],
    "fast": ["quick", "rapid"],
    "clear": ["obvious", "plain"],
    "often": ["frequently", "regularly"],
    "quickly": ["fast", "promptly"],
    "suddenly": ["all at once"],
}

#: Unambiguous contractions (contracted form never means something else).
#: Applied only by styles that welcome contractions (Business/Creative/
#: Casual) and only at higher rewrite intensity, so Academic prose stays
#: formal. Excludes "this is" / "which is" where the contraction is
#: invalid English.
CONTRACTION_SWAPS: Dict[str, str] = {
    "i am": "i'm",
    "we are": "we're",
    "you are": "you're",
    "they are": "they're",
    "he is": "he's",
    "she is": "she's",
    "it is": "it's",
    "that is": "that's",
    "what is": "what's",
    "there is": "there's",
    "here is": "here's",
    "who is": "who's",
    "do not": "don't",
    "does not": "doesn't",
    "did not": "didn't",
    "is not": "isn't",
    "are not": "aren't",
    "was not": "wasn't",
    "were not": "weren't",
    "cannot": "can't",
    "will not": "won't",
    "would not": "wouldn't",
    "should not": "shouldn't",
    "could not": "couldn't",
    "have not": "haven't",
    "has not": "hasn't",
    "had not": "hadn't",
    "must not": "mustn't",
    "we will": "we'll",
    "they will": "they'll",
    "i will": "i'll",
    "you will": "you'll",
    "it will": "it'll",
    "that will": "that'll",
    "there will": "there'll",
    "would have": "would've",
    "could have": "could've",
    "should have": "should've",
    "might have": "might've",
}

#: Rewrite intensity at which the variety passes (synonym swaps,
#: contractions) start firing. Below this the pipeline is exactly the
#: conservative baseline; at and above it the rewrite varies per run.
VARIETY_INTENSITY = 0.6

#: Hedging words that simply weaken the sentence — dropped only when a
#: transform already guarantees the sentence stays grammatical (see
#: _drop_weak_hedges, which only removes them at sentence start).
_START_HEDGES = [
    "arguably,",
    "notably,",
    "importantly,",
    "essentially,",
    "ultimately,",
    "overall,",
    "in essence,",
    "fundamentally,",
    "honestly? ",
    "frankly? ",
]

#: Em-dash punctuation swaps.
_EMDASH_SPLIT_RE = re.compile(r"\s*—\s*")
_EMDASH_JOIN_RE = re.compile(r"\s+—\s+")

#: Formulaic transition openers at the start of a sentence ("Furthermore, …").
_TRANSITION_OPENER_RE = re.compile(
    r"(?:(?<=^)|(?<=[.!?] ))(in conclusion|in summary|to sum up|furthermore|"
    r"moreover|additionally|however|therefore|thus|overall|ultimately|"
    r"essentially|importantly|notably)\b",
    re.IGNORECASE,
)

#: "in turn"/"as such" are only swapped in their discourse-connector senses
#: (sentence start, after "," or "and") — never in the literal ones
#: ("we each spoke in turn", "the plan, as such, was approved").
_IN_TURN_RE = re.compile(r"(?i)((?:^|[.!?]\s+)|(?:,\s*)|(?:\band\s+))in turn\b")
_AS_SUCH_RE = re.compile(r"(?i)((?:^|[.!?]\s+)|(?:,\s*)|(?:\band\s+))as such\b(?!\s*,)")

#: "moving forward" only swaps as a noun-ish subject ("moving forward is the
#: only option") — never the sentence adverb "Moving forward, we should …",
#: which is normal human usage and would read awkwardly rewritten. The
#: discriminator is what follows: the adverb takes a comma, the subject doesn't.
_MOVING_FORWARD_RE = re.compile(r"(?i)\bmoving forward\b(?!\s*,)")


# ---------------------------------------------------------------------------
# Sentence-level helpers
# ---------------------------------------------------------------------------

def _capitalize(s: str) -> str:
    if not s:
        return s
    return s[0].upper() + s[1:]


def _merge_cap(s: str) -> str:
    """Lowercase the head of *s* for a mid-sentence merge — but keep "I".

    "They came out sweet. I flipped them" -> ", and i flipped them" would
    corrupt the pronoun; this returns the head lowercased except for "I"
    (and preserves quoted/"'I'" starts).
    """
    if not s:
        return s
    head = s[0]
    rest = s[1:]
    if head == "I" and (not rest or rest[0].isalnum() or rest[0] in " '\"“”"):
        return s
    return head.lower() + rest


def _mergeable_pair(base: str, nxt: str) -> bool:
    """True when *base* can absorb *nxt* with ", and" and still read well.

    Blocks three cases: a fragment opener the discourse list already
    covers; anything that ends in a question mark being grafted onto a
    declarative ("…at two, and want to join?"); and a question serving as
    the base ("Can you check…, and remind me…" reads like one run-on
    question).
    """
    if not base or not nxt:
        return False
    if base.rstrip().endswith("?") or nxt.rstrip().endswith("?"):
        return False
    return not _DISCOURSE_MERGE_SKIP.match(nxt)


_SENTENCE_START_RE = re.compile(r"(?:(?<=^)|(?<=[.!?] ))\s*[a-z]")


def _repair_sentence_caps(text: str) -> str:
    """Capitalize the first letter of each sentence.

    Fixes drafts left lowercase by hedge removal ("it is clear that the
    plan worked" -> "the plan worked" should become "The plan worked").
    """

    def repl(m: re.Match) -> str:
        return m.group(0)[:-1] + m.group(0)[-1].upper()

    return _SENTENCE_START_RE.sub(repl, text)


def _first_word(s: str) -> str:
    return s.split()[0].lower().strip("“”\"'(),.!?;:") if s.split() else ""


def _replace_from_table(s: str, table: dict) -> Tuple[str, int]:
    """Apply longest-key-first replacements; return (new_s, count)."""
    changed = 0
    for key in sorted(table, key=len, reverse=True):
        if key in s.lower():
            # Case-preserving replace of first occurrence.
            idx = s.lower().find(key)
            original = s[idx : idx + len(key)]
            replacement = table[key]
            if original[0].isupper():
                replacement = replacement.capitalize() if replacement else replacement
            s = s[:idx] + replacement + s[idx + len(key) :]
            changed += 1
    return s, changed


def _drop_start_hedges(s: str) -> Tuple[str, bool]:
    """Remove hedging openers like 'Notably, ...' from a sentence start."""
    lowered = s.lower()
    for hedge in _START_HEDGES:
        if lowered.startswith(hedge):
            rest = s[len(hedge) :].strip()
            return _capitalize(rest), True
    return s, False


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------

def cut_filler(text: str, allowlist: Optional[set] = None) -> Tuple[str, bool]:
    """Replace or remove AI-filler words and phrases.

    *allowlist* (lowercase phrases) are left untouched — style profiles use
    this to keep vocabulary that is natural in their register.
    """
    allowlist = allowlist or set()
    changed = False

    # Phrase-level swaps (longest first).
    for phrase, replacement in sorted(PHRASE_SWAPS.items(), key=lambda kv: len(kv[0]), reverse=True):
        if phrase in allowlist:
            continue
        lowered = text.lower()
        while phrase in lowered:
            idx = lowered.find(phrase)
            original = text[idx : idx + len(phrase)]
            rep = replacement
            if original[0].isupper() and rep:
                rep = rep.capitalize()
            # Keep surrounding whitespace tidy.
            before, after = text[:idx], text[idx + len(phrase) :]
            rep = rep.rstrip()
            if rep:
                if after[:1] in ",.;:!?)]":
                    after = after.lstrip(" ")
                elif not after.startswith(" ") and before and not before.endswith(" "):
                    rep += " "
            else:
                # Removal: a sentence-ending period that belongs to the
                # deleted signpost ("Let's dive in. …") is dropped too,
                # instead of leaving an orphaned ". ". Punctuation like a
                # colon that *introduces* the following text ("in the broad
                # sense of the term: X, Y") is structural and stays.
                after = after.lstrip(" ")
                if after and after[0] in ".!?":
                    after = after[1:].lstrip(" ")
                if (before.endswith(" ") or not before) and after.startswith(" "):
                    after = after.lstrip(" ")
                # Kept punctuation (e.g. a structural colon) must not be
                # separated from the word before it by a space.
                if before.endswith(" ") and after and after[0] in r".:;,!?)]":
                    before = before.rstrip(" ")
            text = before + rep + after
            # A removal can orphan an empty parenthetical ("( )") when the
            # deleted phrase was the only thing inside it — tidy that.
            if not rep:
                text = re.sub(r"\(\s*\)", "", text)
                text = re.sub(r"\s{2,}", " ", text)
            lowered = text.lower()
            changed = True

    # Conditional transition swaps (discourse-usage only).
    text, changed = _swap_conditional_transitions(text, changed)

    # Formulaic transition openers at sentence starts ("Furthermore, …").
    text, changed = _swap_transition_openers(text, changed)

    # Hyphenated compounds first — the word-level pass below would split
    # "cutting-edge" into "cutting" + "-" + "edge" and miss the key.
    for key, rep in WORD_SWAPS.items():
        if "-" not in key or key in allowlist:
            continue
        pattern = re.compile(rf"\b{re.escape(key)}\b", re.IGNORECASE)

        def repl(m: re.Match, rep: str = rep) -> str:
            nonlocal changed
            changed = True
            word = m.group(0)
            return rep.capitalize() if word[0].isupper() else rep

        text = pattern.sub(repl, text)

    # Word-level swaps.
    words = re.split(r"(\W+)", text)
    new_words: List[str] = []
    for token in words:
        key = token.lower()
        if key in allowlist:
            new_words.append(token)
            continue
        if key in WORD_SWAPS:
            rep = WORD_SWAPS[key]
            if token[0].isupper() and rep:
                rep = rep.capitalize()
            new_words.append(rep)
            changed = True
        else:
            new_words.append(token)
    text = "".join(new_words)

    # Removing sentence-initial hedges can leave a lowercase start
    # ("it is clear that the plan worked" -> "the plan worked"); fix the
    # capitalization at each sentence boundary.
    text = _repair_sentence_caps(text)

    return text, changed


def _swap_transition_openers(text: str, changed: bool) -> Tuple[str, bool]:
    """Replace formulaic transition openers with plainer alternatives.

    Only touches transitions at the *start* of a sentence, so mid-sentence
    uses ("The results, however, were…") are left alone.
    """

    def repl(m: re.Match) -> str:
        nonlocal changed
        changed = True
        word = m.group(1)
        alternatives = TRANSITION_SWAPS.get(word.lower(), [])
        choice = alternatives[0] if alternatives else word.lower()
        if word[0].isupper():
            choice = choice.capitalize()
        return choice

    return _TRANSITION_OPENER_RE.sub(repl, text), changed


def _swap_conditional_transitions(text: str, changed: bool) -> Tuple[str, bool]:
    """Swap 'in turn'/'as such' only where they read as connectors.

    Both are ambiguous: "we each spoke in turn" and "the plan, as such,"
    use the literal senses and must be left alone. The detector flags every
    occurrence, so we can't do a blanket swap — this pass only rewrites the
    discourse usage (sentence start, or after "," / "and"), which is what
    machine prose overuses.
    """

    def _sentence_start(lead: str) -> bool:
        return lead == "" or lead[-2:] in (". ", "! ", "? ")

    def repl_in_turn(m: re.Match) -> str:
        nonlocal changed
        changed = True
        lead = m.group(1)
        return lead + ("Then" if _sentence_start(lead) else "then")

    def repl_as_such(m: re.Match) -> str:
        nonlocal changed
        changed = True
        lead = m.group(1)
        return lead + ("So" if _sentence_start(lead) else "so")

    def repl_moving_forward(m: re.Match) -> str:
        nonlocal changed
        changed = True
        rep = "pushing ahead"
        return rep.capitalize() if m.group(0)[0].isupper() else rep

    text = _IN_TURN_RE.sub(repl_in_turn, text)
    text = _AS_SUCH_RE.sub(repl_as_such, text)
    text = _MOVING_FORWARD_RE.sub(repl_moving_forward, text)
    return text, changed


def vary_openers(
    sentences: List[str],
    rng: random.Random,
    intensity: float = 0.5,
) -> Tuple[List[str], bool]:
    """Vary repetitive sentence openers using conservative synonym swaps.

    Only a subset of eligible sentences is rewritten (never all of them), so
    the result still sounds like one consistent writer. *intensity* scales
    how many eligible sentences actually change.
    """
    if len(sentences) < 3:
        return sentences, False

    changed = False
    out: List[str] = []
    for s in sentences:
        match = re.match(r"^([A-Za-z']+)\b", s)
        if not match:
            out.append(s)
            continue
        first = match.group(1).lower()
        rest = s[match.end() :]
        if first in TRANSITION_SWAPS and rest.lstrip().startswith(","):
            choice = rng.choice(TRANSITION_SWAPS[first])
            out.append(_capitalize(choice) + rest)
            changed = True
        else:
            out.append(s)

    # Also drop leading hedges on a fraction of eligible sentences. The
    # fraction scales with rewrite intensity (0.7 at the default 0.5).
    hedge_prob = 0.3 + 0.8 * intensity
    for i, s in enumerate(out):
        if _first_word(s).rstrip(",") in {
            "notably", "importantly", "essentially", "ultimately",
            "overall", "arguably",
        }:
            if rng.random() < hedge_prob:
                new_s, did = _drop_start_hedges(s)
                if did:
                    out[i] = new_s
                    changed = True

    return out, changed


#: Human texture markers — natural spoken discourse markers that break a
#: too-clean academic flow. Distinct from the AI-filler table: none of these
#: appear in AI_FILLERS (verified), so inserting them never contradicts the
#: detector.
_HUMAN_TEXTURE_OPENERS = [
    "In practice", "As it happens", "Admittedly",
    "In reality", "To be fair", "Mind you",
]

#: First words that are safe to lowercase when a marker is prepended (i.e.
#: common English starters — never proper nouns or acronyms).
_LOWERCASE_SAFE_STARTERS = {
    "the", "a", "an", "it", "we", "they", "he", "she", "you", "i",
    "this", "these", "those", "there", "most", "many", "some", "each",
    "every", "both", "one", "two", "three", "in", "for", "as", "while",
    "although", "when", "if", "after", "before", "since", "despite",
    "however", "therefore", "thus", "then", "our", "their", "its",
    "his", "her", "with", "through", "by", "from", "within", "across",
}


def add_human_texture(
    sentences: List[str],
    rng: random.Random,
    intensity: float = 0.5,
) -> Tuple[List[str], bool]:
    """Insert a few natural discourse markers at high intensity.

    Real writers break up a too-smooth flow with small asides and hedges;
    LLM output almost never does. Fires only at high intensity (>= 0.75),
    at most one marker per ~3 sentences, never on the first sentence, and
    never on pronoun/proper-noun or already-hedged openers — so the result
    reads like one person wrote it, not like a list of tics.
    """
    if intensity < 0.75 or len(sentences) < 4:
        return sentences, False
    limit = max(1, len(sentences) // 3)
    changed = False
    inserted = 0
    out = list(sentences)
    for i in range(1, len(out)):
        if inserted >= limit:
            break
        s = out[i].strip()
        if len(s) < 12:
            continue
        parts = s.split(" ", 1)
        if len(parts) != 2:
            continue
        first, rest = parts
        if first.lower() not in _LOWERCASE_SAFE_STARTERS:
            continue  # proper noun / acronym / pronoun — leave it alone
        if _first_word(s).rstrip(",") in {
            "in", "as", "admittedly", "to", "mind", "for", "honestly",
            "notably", "importantly", "essentially", "ultimately",
        }:
            continue  # already hedged or starts with a marker word
        if rng.random() < 0.35:
            marker = rng.choice(_HUMAN_TEXTURE_OPENERS)
            out[i] = f"{marker}, {first[0].lower()}{first[1:]} {rest}"
            changed = True
            inserted += 1
    return out, changed


_SPLIT_CONJUNCTIONS = re.compile(r"\s+,\s+(and|but|so|while|yet|whereas|which|because)\s+", re.I)
_SPLIT_SEMICOLONS = re.compile(r"\s*;\s*")

#: Second sentences that can't be grafted onto the previous one — they open
#: with a discourse marker and would read as a fragment ("…, and to that end,").
_DISCOURSE_MERGE_SKIP = re.compile(
    r"(?i)^(in turn|to that end|for that reason|as such|as a result|that said|"
    r"having said that|even so|all told|in the end|in short|in conclusion|"
    r"in summary|the upshot is|however|moreover|furthermore|therefore|thus|"
    r"consequently|additionally|in addition|meanwhile|nevertheless|nonetheless|"
    r"ultimately|essentially|importantly|notably|overall|arguably|in essence|"
    r"fundamentally|in other words|with that in mind)[, ]"
)


def vary_length(
    sentences: List[str],
    rng: random.Random,
    min_words: int = 14,
    max_words: int = 45,
    intensity: float = 0.5,
) -> Tuple[List[str], bool]:
    """Split long sentences and merge short adjacent ones to break flat rhythm.

    Splits happen only at conjunctions/semicolons so meaning is preserved;
    merges join two short sentences with a conjunction. Both are applied
    sparingly and only when they improve variety. *intensity* scales how
    often eligible sentences are actually restructured (matches the legacy
    probabilities at 0.5).
    """
    split_prob = 0.4 + 0.8 * intensity
    merge_prob = 0.2 + 0.6 * intensity
    if len(sentences) < 3:
        return sentences, False

    changed = False
    out: List[str] = []

    def split_long(s: str) -> List[str]:
        words = len(s.split())
        if words <= max_words:
            return [s]
        match = _SPLIT_CONJUNCTIONS.search(s)
        if match:
            head, tail = s[: match.start()].strip(), s[match.end() :].strip()
            if len(head.split()) >= 6 and len(tail.split()) >= 5:
                return [head.rstrip(",") + ".", _capitalize(tail) + "."]
        semi = _SPLIT_SEMICOLONS.search(s)
        if semi and len(s) > 60:
            head, tail = s[: semi.start()].strip(), s[semi.end() :].strip()
            if len(head.split()) >= 5 and len(tail.split()) >= 5:
                return [head + ".", _capitalize(tail) + "."]
        return [s]

    i = 0
    while i < len(sentences):
        s = sentences[i]
        if len(s.split()) > max_words and rng.random() < split_prob:
            parts = split_long(s)
            if len(parts) > 1:
                out.extend(parts)
                changed = True
                i += 1
                continue
        # Merge short sentence with the next short one — unless the next one
        # opens with a discourse marker, and never leave a ". ," seam.
        if (
            i + 1 < len(sentences)
            and 3 <= len(s.split()) <= min_words
            and 3 <= len(sentences[i + 1].split()) <= min_words
            and rng.random() < merge_prob
            and _mergeable_pair(s, sentences[i + 1])
        ):
            nxt = sentences[i + 1]
            merged = s.rstrip(" \t").rstrip(".?!") + ", and " + _merge_cap(nxt)
            out.append(merged)
            changed = True
            i += 2
            continue
        out.append(s)
        i += 1

    return out, changed


#: Mid-sentence conjunction / semicolon cuts for rhythm diversification.
_RHYTHM_CONJ_RE = re.compile(r",\s+(and|but|so|yet|whereas|while|because)\s+", re.I)
_RHYTHM_SEMI_RE = re.compile(r"\s*;\s*")
#: Comma + amplifying adverb — a safe cut when the tail is a full clause
#: ("…over longer periods, particularly in underserved areas…").
_RHYTHM_ADV_RE = re.compile(r",\s+(particularly|especially|notably)\s+", re.I)


def _split_mid_sentence(s: str) -> List[str]:
    """Split *s* at a comma-conjunction, semicolon, or amplifying adverb into
    two sentences.

    Returns ``[s]`` unchanged when there is no safe cut. Both halves must be
    substantial (>= 6 and >= 5 words) so the split never leaves a fragment
    behind; the tail is re-capitalized.
    """
    for pattern, min_head, min_tail in (
        (_RHYTHM_CONJ_RE, 8, 5),
        (_RHYTHM_ADV_RE, 12, 5),
        (_RHYTHM_SEMI_RE, 6, 5),
    ):
        match = pattern.search(s)
        if match:
            head, tail = s[: match.start()].strip(), s[match.end() :].strip()
            if len(head.split()) >= min_head and len(tail.split()) >= min_tail:
                tail = tail.rstrip(".?!;,") or tail
                return [head.rstrip(",") + ".", _capitalize(tail) + "."]
    return [s]


def diversify_rhythm(
    sentences: List[str],
    rng: random.Random,
    intensity: float = 0.5,
    source: Optional[str] = None,
    allowlist: Optional[set] = None,
) -> Tuple[List[str], bool]:
    """Restore human sentence-length variation (burstiness) in uniform prose.

    Real AI text tends to be a string of equally-sized 20-30 word sentences —
    the flat-rhythm tell that perplexity/burstiness detectors weigh heavily.
    This pass fires only when rhythm is *uniform* (coefficient of variation
    of sentence lengths < 0.30), there are at least 4 sentences, and the
    *source* text still carries AI tells (checked against the original input,
    not the mid-pipeline text — the pattern passes may already have cleared
    every tell while leaving the rhythm untouched). So naturally rhythmic
    prose is never touched (all 4-sentence human-corpus paragraphs carry zero
    detector issues, which gates the pass off). It then:

      * merges adjacent mid-length sentences into one longer sentence
        (", and"), and
      * splits sentences at comma-conjunctions / semicolons / amplifying
        adverbs into shorter ones,

    each on a fraction of eligible candidates scaled by *intensity*, then a
    deterministic refinement guarantees the variation actually lands. Meaning
    is preserved; only the rhythm changes.
    """
    if len(sentences) < 4:
        return sentences, False
    from .detectors import _sentence_stats  # local import to avoid cycles

    _, cv = _sentence_stats(sentences)
    if cv >= 0.30:
        return sentences, False
    if source:
        from .detectors import analyze  # local import to avoid cycles

        if not analyze(source, allowlist=allowlist or set()).issues:
            return sentences, False

    merge_prob = 0.35 + 0.4 * intensity
    split_prob = 0.35 + 0.4 * intensity
    changed = False
    out: List[str] = []
    i = 0
    while i < len(sentences):
        s = sentences[i]
        nxt = sentences[i + 1] if i + 1 < len(sentences) else None
        n_words = len(s.split())
        # Merge two mid-length neighbors into one longer sentence.
        if (
            nxt
            and 12 <= n_words <= 34
            and 12 <= len(nxt.split()) <= 34
            and n_words + len(nxt.split()) <= 55
            and rng.random() < merge_prob
            and _mergeable_pair(s, nxt)
        ):
            merged = s.rstrip(" \t").rstrip(".?!") + ", and " + _merge_cap(nxt)
            out.append(merged)
            changed = True
            i += 2
            continue
        # Split a mid-length sentence into shorter pieces.
        if 18 <= n_words <= 46 and rng.random() < split_prob:
            parts = _split_mid_sentence(s)
            if len(parts) > 1:
                out.extend(parts)
                changed = True
                i += 1
                continue
        out.append(s)
        i += 1

    # Guarantee the fix: if rhythm is STILL uniform after the probabilistic
    # pass (seed luck can leave it just under the bar), force safe splits and
    # merges until the coefficient of variation clears 0.30 or no candidate
    # remains. Meaning is preserved either way — only the rhythm changes.
    from .detectors import _sentence_stats as _stats2  # local import

    _, cv_after = _stats2(out)
    if cv_after < 0.30 and len(out) >= 4:
        refined, did = _force_rhythm_variety(out)
        if did:
            out = refined
            changed = True
    return out, changed


def _force_rhythm_variety(sentences: List[str]) -> Tuple[List[str], bool]:
    """Deterministically split/merge until sentence lengths vary.

    Splits come first (they add short sentences, which variance needs
    most); merges are only used when splits alone can't clear the bar.
    Respects the discourse-marker guard so merges never graft onto
    "Moreover, …"-style openers.
    """
    from .detectors import _sentence_stats  # local import to avoid cycles

    def varied(seq: List[str]) -> bool:
        _, cv = _sentence_stats(seq)
        return cv >= 0.30

    out = list(sentences)
    changed = False

    # Pass 1: split every eligible mid-length sentence at its first safe cut.
    for idx, s in enumerate(out):
        if varied(out):
            break
        if 18 <= len(s.split()) <= 46:
            parts = _split_mid_sentence(s)
            if len(parts) > 1:
                out[idx : idx + 1] = parts
                changed = True

    # Pass 2: merge adjacent mid-length pairs into longer sentences.
    if not varied(out):
        i = 0
        while i + 1 < len(out) and not varied(out):
            a, b = out[i], out[i + 1]
            if (
                12 <= len(a.split()) <= 34
                and 12 <= len(b.split()) <= 34
                and len(a.split()) + len(b.split()) <= 55
                and _mergeable_pair(a, b)
            ):
                out[i] = a.rstrip(" \t").rstrip(".?!") + ", and " + _merge_cap(b)
                del out[i + 1]
                changed = True
            else:
                i += 1

    return out, changed


def soften_emdash(text: str, rng: random.Random) -> Tuple[str, bool]:
    """Replace em dashes with commas or parentheses where that reads naturally."""
    if "—" not in text:
        return text, False
    changed = False

    def repl_commas(m: re.Match) -> str:
        nonlocal changed
        changed = True
        return ", "

    # Around a parenthetical (phrase ends with same comma-able boundary),
    # converting both sides is usually fine: "X — y — z" -> "X, y, z".
    text = _EMDASH_SPLIT_RE.sub(repl_commas, text, count=2)

    # Any remaining single em dashes become commas too.
    text = _EMDASH_SPLIT_RE.sub(repl_commas, text)
    return text, changed


#: Subordinating conjunctions whose clause can move to the front of the
#: sentence without changing meaning or register ("formal stays formal").
_FRONTABLE_SUBORDINATORS = re.compile(
    r"\b(because|although|though|while|whereas|since|if|unless|when|whenever)" 
    r"\b",
    re.I,
)

#: Guard: a subordinate clause almost always opens with a subject — a
#: determiner ("the/a/this"), a possessive ("my/our/its"), or a pronoun
#: ("we/they/it"). Requiring that keeps the tail genuinely a clause
#: ("because the team met the deadline") instead of a fragment
#: ("because of budget", "because tired").
_CLAUSE_TAIL_START = re.compile(
    r"^(?:the|a|an|my|our|their|his|her|its|your|this|that|these|those|we|they|he|"
    r"she|it|i|you|who|which|there)\b",
    re.I,
)


#: Headers / list lines that must never be restructured.
_NON_CLAUSE_SKIP = re.compile(
    r"(?i)^(\d+\.|[-*•]|\w+:)"
)


#: "not merely/just A but B" negative-parallelism scaffold — the
#: rhetorical construction LLMs lean on ("not merely a practical asset but
#: a condition"). Converted to a plain "both A and B" so the balanced
#: scaffold disappears instead of being reworded in place ("not just"
#: keeps the same tell shape). The X-side is limited to lowercase
#: word-group text (no punctuation) so nothing like a sentence boundary or
#: a quoted phrase is captured, and a following "but also" is collapsed.
_NEG_PARALLEL_RE = re.compile(
    r"\bnot (just|merely) ([a-z][a-z '\-]{2,60}?)\s+but\s+(also\s+)?",
    re.I,
)


def de_scaffold_negative_parallelism(
    sentences: List[str],
) -> Tuple[List[str], bool]:
    """Flatten the "not (just|merely) A but B" scaffold into "both A and B".

    Mirrors the detector's negative-parallelism pattern exactly (verified:
    zero hits on the human corpus), converting the balanced construction to
    a plain coordinated one — "Adaptability is not merely a practical
    asset but a condition" -> "Adaptability is both a practical asset and
    a condition". Fires whenever the construction appears, at any
    intensity, because a surviving scaffold is a detector hit the moment
    it ships.
    """
    changed = False
    out = list(sentences)
    for i, s in enumerate(out):
        m = _NEG_PARALLEL_RE.search(s)
        if not m:
            continue
        xside = m.group(2).strip()
        if not xside:
            continue
        replacement = f"both {xside} and "
        if m.start() == 0 or s[: m.start()].endswith(". "):
            replacement = replacement[0].upper() + replacement[1:]
        new_s = s[: m.start()] + replacement + s[m.end():]
        out[i] = new_s
        changed = True
    return out, changed


def front_subordinate_clauses(
    sentences: List[str],
    rng: random.Random,
    intensity: float = 0.5,
) -> Tuple[List[str], bool]:
    """Front some subordinate clauses ("X because Y" -> "Because Y, X").

    This is the register-preserving structural movement the top commercial
    engines (GPTinf, HumanizeAI.pro, Undetectable AI) share: it changes the
    sentence's shape and opener *without* pushing the register casual, so
    formal text stays formal while the structure stops being uniform. It is
    deliberately probabilistic and modest:

      * fires only at high intensity (>= 0.75),
      * fronts at most a quarter of eligible sentences (rng-scaled),
      * only when the tail after the conjunction is a genuine clause with a
        verb and >= 4 words, the head is a full sentence, and the sentence
        isn't a header/list line,
      * never touches the first sentence of a paragraph (openers matter),

    so natural prose is left alone and the change reads like a writer
    varying their own sentence structure, not a mechanical reorder.
    """
    if intensity < 0.75 or len(sentences) < 4:
        return sentences, False
    limit = max(1, len(sentences) // 4)
    changed = False
    moved = 0
    out = list(sentences)
    for i in range(1, len(out)):
        if moved >= limit:
            break
        s = out[i].strip()
        if len(s) < 25 or _NON_CLAUSE_SKIP.match(s):
            continue
        m = _FRONTABLE_SUBORDINATORS.search(s)
        if not m:
            continue
        head, tail = s[: m.start()].strip(), s[m.end():]
        tail = re.sub(r"[.!?]$", "", tail).strip()
        # Tail must be a real clause: opens with a subject, >= 4 words.
        if len(tail.split()) < 4 or not _CLAUSE_TAIL_START.match(tail):
            continue
        if not head or len(head.split()) < 6:
            continue
        if re.search(r"[.!?]\s*$", head) or not re.search(r"[a-z]\s+[a-z]", head):
            continue
        if rng.random() > 0.55:
            continue
        conj = m.group(1)
        # The head becomes the trailing main clause: drop its capital.
        lowered_head = head[0].lower() + head[1:]
        out[i] = f"{conj[0].upper()}{conj[1:]} {tail}, {lowered_head}."
        changed = True
        moved += 1
    return out, changed


def simplify_register(
    text: str,
    changed_in: bool = False,
    allowlist: Optional[set] = None,
) -> Tuple[str, bool]:
    """Demote formal/stiff words to the plain everyday words humans use.

    Uses the verified human-writing memory (``human_memory.PLAIN_SWAPS``):
    every source word is verified to have zero hits across the human
    corpus, so a demotion can never touch natural prose — it only fires on
    the formal vocabulary real people don't reach for (``utilize`` ->
    ``use``, ``commence`` -> ``start``, ``subsequently`` -> ``then``).

    *allowlist* (lowercase phrases, from the style profile) are left
    untouched: the business register keeps corporate idiom like "key
    takeaways" even though a bare "takeaways" would otherwise be demoted.

    Fires at any intensity, like ``cut_filler``: a stiff word is a
    register tell the moment it ships, and the swap is meaning-preserving
    by construction. Longer phrases are applied before single words so
    "in the event that" -> "if" wins over "event".
    """
    if not text:
        return text, changed_in
    from .human_memory import PLAIN_SWAPS

    allowlist = allowlist or set()
    changed = changed_in
    lowered = text.lower()
    # Longest keys first so multi-word phrases match before single words.
    # Precompiled once (lru_cache) so 1,000+ swaps don't recompile regexes
    # on every rewrite call.
    for pattern, plain in _plain_swap_patterns():
        def repl(m: re.Match, plain: str = plain) -> str:
            nonlocal changed
            # A style-register allowlist entry (e.g. "key takeaways" in
            # business) wins over the plain-word swap — the idiom is the
            # vocabulary that register actually uses.
            if _overlaps_allowlist(lowered, m, allowlist):
                return m.group(0)
            changed = True
            # Preserve capitalization: "Utilize" -> "Use".
            if m.group(0)[:1].isupper():
                return plain[0].upper() + plain[1:]
            return plain

        text = pattern.sub(repl, text)
        lowered = text.lower()
    return text, changed


@lru_cache(maxsize=1)
def _plain_swap_patterns() -> List[Tuple[re.Pattern, str]]:
    """Compiled ``(regex, plain)`` pairs, longest phrase first."""
    from .human_memory import PLAIN_SWAPS

    return [
        (re.compile(rf"\b{re.escape(phrase)}\b", re.I), PLAIN_SWAPS[phrase])
        for phrase in sorted(PLAIN_SWAPS, key=len, reverse=True)
    ]


def vary_synonyms(
    text: str,
    rng: random.Random,
    intensity: float = 1.0,
) -> Tuple[str, bool]:
    """Swap common words for near-synonyms, probabilistically per occurrence.

    This is the "not one algorithm" pass: given the same input, different
    seeds produce different word choices, and only a fraction of eligible
    occurrences change so the prose still reads like one writer. Only
    register-neutral, meaning-preserving swaps from SYNONYM_VARIETY fire;
    the pass does nothing below ``VARIETY_INTENSITY``.
    """
    if intensity < VARIETY_INTENSITY or not text:
        return text, False
    changed = False
    prob = 0.25 + 0.35 * intensity
    for word, choices in SYNONYM_VARIETY.items():
        pattern = re.compile(rf"\b{re.escape(word)}\b", re.I)

        def repl(m: re.Match, choices: List[str] = choices) -> str:
            nonlocal changed
            if rng.random() >= prob:
                return m.group(0)
            changed = True
            choice = rng.choice(choices)
            return choice.capitalize() if m.group(0)[0].isupper() else choice

        text = pattern.sub(repl, text)
    return text, changed


def add_contractions(
    text: str,
    rng: random.Random,
    intensity: float = 1.0,
) -> Tuple[str, bool]:
    """Contract unambiguous phrases ("we are" -> "we're") at random spots.

    Style-gated: only styles that set ``contractions=True`` run this pass,
    and only at ``intensity >= VARIETY_INTENSITY``. A subset of eligible
    spots contracts so the result still feels natural.
    """
    if intensity < VARIETY_INTENSITY or not text:
        return text, False
    changed = False
    prob = 0.25 + 0.35 * intensity
    for phrase, contraction in CONTRACTION_SWAPS.items():
        pattern = re.compile(rf"\b{re.escape(phrase)}\b", re.I)

        def repl(m: re.Match, contraction: str = contraction) -> str:
            nonlocal changed
            if rng.random() >= prob:
                return m.group(0)
            changed = True
            word = m.group(0)
            if word[0].isupper():
                return contraction[0].upper() + contraction[1:]
            return contraction

        text = pattern.sub(repl, text)
    return text, changed


def de_hedge(sentences: List[str], rng: random.Random) -> Tuple[List[str], bool]:
    """Remove empty hedging constructions like 'It is worth noting that'."""
    # PHRASE_SWAPS already handles the common forms in cut_filler; this pass
    # catches stragglers with 'that' variants inside sentence bodies.
    changed = False
    out: List[str] = []
    pattern = re.compile(
        r"\s+(it is|it's) (important|worth|essential|crucial|interesting|vital) "
        r"(to note|to mention|to remember|to highlight) that\b",
        re.I,
    )
    # "I'm sorry, but I cannot <verb>…" -> "I can't <verb>…" (the apology
    # opener is a ChatGPT tell; the verb itself is fine).
    sorry_pattern = re.compile(
        r"\bi'?m (sorry|afraid)[, ]*(but |that )?i (can'?t|cannot|do not|don'?t)\b",
        re.I,
    )
    # Bare "I cannot <flagged verb>…" -> "I can't <verb>…".
    cannot_pattern = re.compile(
        r"\bi cannot (assist|help you|provide|process|access|generate|fulfill|complete|create|write|share|verify|confirm|recommend)\b",
        re.I,
    )
    for s in sentences:
        new_s = pattern.sub("", s, count=1)
        new_s = sorry_pattern.sub("I can't", new_s, count=1)
        new_s = cannot_pattern.sub(lambda m: "I can't " + m.group(1), new_s, count=1)
        if new_s != s:
            new_s = re.sub(r" {2,}", " ", new_s).strip()
            if new_s and not new_s[0].isupper():
                new_s = _capitalize(new_s)
            if not new_s:
                changed = True  # sentence was pure filler; caller drops empties
                continue
            changed = True
        # Standalone fake-candid opener ("Honestly? It depends…"): the
        # sentence splitter consumed the "?", leaving a one-word sentence.
        # Drop it — it is the Wikipedia #33 conversational-rhetorical opener.
        if s.strip().lower().rstrip("?!") in ("honestly", "frankly") and new_s == s:
            changed = True
            continue
        out.append(new_s)
    return out, changed


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def rewrite(
    text: str,
    *,
    rng: Optional[random.Random] = None,
    allowlist: Optional[set] = None,
    adjust_length: bool = True,
    max_words: int = 45,
    min_words: int = 14,
    intensity: float = 0.5,
    contractions: bool = False,
) -> Tuple[str, List[str], bool]:
    """Run the full deterministic rewrite pipeline over *text*.

    Returns ``(rewritten_text, changed_sentences, anything_changed)`` where
    *changed_sentences* is the list of resulting sentences. Sentence
    boundaries are preserved so callers can diff before/after.

    *intensity* (0.0-1.0) scales how aggressively the rewrite restructures:
    the default 0.5 reproduces the legacy conservative behavior exactly,
    while higher values also fire the synonym-variety and contraction
    passes (``contractions`` must be enabled by the style profile) so
    repeated runs with different seeds produce different prose.
    """
    rng = rng or random.Random(0)
    allowlist = allowlist or set()
    intensity = max(0.0, min(1.0, intensity))

    sentences = split_sentences(text)
    if not sentences:
        return text, [], False

    changed = False

    # 0. Invisible-Unicode watermark hygiene (watermarks-remover Layer A):
    #     zero-width spaces, bidi controls, joiners, fillers — invisible
    #     machine fingerprints that must never survive into the output.
    clean, removed = strip_marks(" ".join(sentences))
    if removed:
        changed = True
        sentences = split_sentences(clean)
        if not sentences:
            return text, [], False

    # 1. Filler + buzzwords.
    new_text, did = cut_filler(" ".join(sentences), allowlist=allowlist)
    if did:
        changed = True
        sentences = split_sentences(new_text)
        if not sentences:
            return text, [], False

    # 1a. Plain register: demote formal/stiff words to the everyday words
    #     real humans use (verified human-writing memory — every swap
    #     source has zero hits on the human corpus, so natural prose is
    #     never touched). Runs before everything else so the plain version
    #     flows through the rest of the chain.
    new_text, did = simplify_register(" ".join(sentences), allowlist=allowlist)
    if did:
        changed = True
        sentences = split_sentences(new_text)
        if not sentences:
            return text, [], False

    # 1b. Negative-parallelism scaffold ("not merely X but Y") — flatten
    #     to "both X and Y" so the balanced construction can't survive as
    #     a tell. Runs before hedging/rhythm so the rebuilt sentence flows
    #     through the rest of the chain.
    sentences, did = de_scaffold_negative_parallelism(sentences)
    changed = changed or did
    sentences = [s for s in sentences if s.strip()]
    if not sentences:
        return text, [], False

    # 2. Hedging constructions.
    sentences, did = de_hedge(sentences, rng)
    changed = changed or did
    sentences = [s for s in sentences if s.strip()]
    if not sentences:
        return text, [], False

    # 3. Em dashes.
    joined, did = soften_emdash(" ".join(sentences), rng)
    if did:
        changed = True
        sentences = split_sentences(joined)

    # 4. Openers.
    sentences, did = vary_openers(sentences, rng, intensity=intensity)
    changed = changed or did

    # 4b. Human texture (high intensity only): a few natural asides/hedges.
    sentences, did = add_human_texture(sentences, rng, intensity=intensity)
    changed = changed or did

    # 5. Rhythm (only if the detector flagged uniformity, to avoid
    #    gratuitous restructuring). vary_length handles the long/short
    #    extremes; diversify_rhythm restores burstiness in uniformly
    #    mid-length prose — the shape real AI text is most often in.
    from .detectors import _sentence_stats  # local import to avoid cycles

    _, cv = _sentence_stats(sentences)
    if cv < 0.4 and len(sentences) >= 5:
        sentences, did = vary_length(
            sentences, rng, min_words=min_words, max_words=max_words, intensity=intensity
        )
        changed = changed or did
    sentences, did = diversify_rhythm(
        sentences, rng, intensity=intensity, source=text, allowlist=allowlist
    )
    changed = changed or did

    # 5b. Register-preserving clause movement (high intensity only): front
    #     some subordinate clauses ("X because Y" -> "Because Y, X"). This
    #     is the structural variation the top commercial engines share — it
    #     changes sentence shape and openers without pushing the register
    #     casual. Probabilistic and capped, so natural prose is untouched.
    sentences, did = front_subordinate_clauses(sentences, rng, intensity=intensity)
    changed = changed or did

    # 6. Intensity-controlled variety: synonym swaps and (style-gated)
    #    contractions. Both are probabilistic, so different seeds produce
    #    different prose — and both stay silent at the default intensity.
    joined = " ".join(sentences)
    joined, did = vary_synonyms(joined, rng, intensity)
    changed = changed or did
    if contractions:
        joined, did = add_contractions(joined, rng, intensity)
        changed = changed or did
    sentences = [s for s in split_sentences(joined) if s.strip()]
    if not sentences:
        return text, [], False

    result = " ".join(sentences)
    if not result.endswith((".", "!", "?", "…", '"')):
        result += "."
    return result, sentences, changed
