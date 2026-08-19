"""Factual preservation critics + best-of-N candidate ranking.

The rewrite engine can produce beautiful prose that subtly changes the
original's facts — a dropped number, a flipped negation, a lost proper noun.
These critics are the hard gates the report-style architecture demands: a
candidate that changes a critical fact is rejected *regardless of how
natural it sounds*, and only survivors of the gates are ranked by quality.

* ``preservation_issues(original, candidate)`` — the drift found between
  two texts (lost numbers, negation flips, lost proper nouns), each with a
  severity. Lost numbers are the hard gate; negation and entity drift are
  strong flags (a rewrite can legitimately restructure those, but only when
  meaning is preserved — the feedback loop uses them as next-pass
  instructions, not outright rejections).
* ``rank_candidates(original, candidates, …)`` — pick the best rewrite from
  a pool. Candidates that preserve every number beat candidates that don't,
  regardless of how fluent the fact-drifting one reads; ties break on the
  local naturalness score. Returns the chosen text plus any warnings about
  the runner-ups that were rejected for fact drift.

Everything is deterministic, pure, and dependency-free (only the detector
for scoring), so the gates are testable and never call the network.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from .detectors import analyze

#: A number token: integers, decimals, and percentages ("42", "3.5", "90%",
#: "1,000"). Trailing "%" is part of the token so a rewrite that changes
#: "90%" to "90" is caught as a fact change.
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?%?")

#: Negation words — a count flip (original has several, rewrite has none, or
#: vice versa) signals a possible meaning change.
_NEGATION_WORDS = (
    "not never no without cannot can't don't doesn't didn't won't isn't "
    "aren't wasn't weren't hasn't haven't hadn't nor neither nothing none "
    "nobody nowhere no one"
).split()

#: A capitalized token that could be a proper noun ("Smith", "Paris",
#: "GPTZero") or an all-caps acronym ("AI", "NHS").
_CAPITALIZED_RE = re.compile(r"\b[A-Z][a-z]{2,}\b|\b[A-Z]{2,}\b")

#: Common capitalized words that are *not* proper nouns — sentence-initial
#: function words ("The", "This", "However") and day/month names that are
#: not evidence of entity drift. Anything else capitalized mid-sentence is
#: treated as a proper noun.
_NON_ENTITY_CAPS = {
    "the", "this", "that", "these", "those", "it", "he", "she", "they",
    "we", "you", "i", "but", "and", "or", "so", "if", "then", "while",
    "when", "where", "how", "why", "what", "who", "whom", "which", "as",
    "in", "on", "at", "by", "for", "with", "from", "of", "to", "a", "an",
    "however", "therefore", "moreover", "furthermore", "additionally",
    "although", "though", "because", "since", "until", "before", "after",
    "during", "first", "second", "third", "finally", "lastly", "indeed",
    "thus", "hence", "consequently", "meanwhile", "also", "too", "very",
    "every", "each", "both", "some", "any", "all", "such", "other",
    "another", "many", "much", "most", "more", "few", "several", "no",
    "not", "only", "same", "still", "even", "just", "once", "now", "here",
    "there", "almost", "actually", "generally", "typically", "usually",
    "often", "always", "never", "sometimes", "perhaps", "maybe", "please",
    "dear", "hello", "hi", "hey", "thanks", "thank", "yes", "okay", "ok",
}


def _norm_number(token: str) -> str:
    """Normalize a number token for comparison (drop digit-grouping commas)."""
    return re.sub(r"(?<=\d),(?=\d)", "", token).lower()


def extract_numbers(text: str) -> set:
    """The normalized number tokens in *text* (integers, decimals, percents)."""
    return {_norm_number(m.group(0)) for m in _NUMBER_RE.finditer(text)}


#: Abbreviations whose trailing period must not be read as a sentence
#: boundary ("Dr. Smith", "Prof. Jones", "e.g. apples"). A capitalized word
#: right after one of these is a proper noun, not a sentence-initial word.
#: Listed without the trailing dot; the pattern adds it (``before`` ends at
#: the period itself).
_ABBREV = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|St|Sr|Jr|Rev|Hon|Gen|Col|Capt|Lt|Sgt|etc|e\.g|i\.e|vs|inc|ltd|co|approx|est|dept|univ)\s*\.$",
    re.I,
)


def _sentence_start_positions(text: str) -> set:
    """Character offsets where each sentence begins (first non-space char).

    A sentence starts at the first token of the text or after ``.``/``!``/``?``
    followed by whitespace — *except* when the period belongs to a known
    abbreviation ("Dr.", "e.g."), so "Dr. Smith" does not split into two
    pseudo-sentences."""
    starts = set()
    if not text.strip():
        return starts
    starts.add(len(text) - len(text.lstrip()))  # first non-space char
    for m in re.finditer(r"[.!?]\s+(?=[A-Z])", text):
        before = text[: m.start() + 1].rstrip()
        if _ABBREV.search(before):
            continue
        # Skip the whitespace between the period and the next word.
        nxt = m.end()
        while nxt < len(text) and text[nxt] == " ":
            nxt += 1
        starts.add(nxt)
    return starts


def extract_entities(text: str) -> set:
    """Capitalized tokens in *text* that look like proper nouns / acronyms.

    Sentence-initial capitalized words are skipped (they are usually just
    first words), as are common non-entity capitals ("The", "However",
    "Monday" is deliberately *not* excluded — but those are rare enough in
    body copy that losing one is worth flagging as drift).
    """
    starts = _sentence_start_positions(text)
    entities = set()
    for m in _CAPITALIZED_RE.finditer(text):
        word = m.group(0)
        if m.start() in starts:
            continue  # sentence-initial — usually the first word, not a name
        if word.lower() in _NON_ENTITY_CAPS:
            continue
        entities.add(word)
    return entities


def extract_negations(text: str) -> int:
    """Count of negation words in *text*."""
    lowered = text.lower()
    return sum(len(re.findall(rf"\b{re.escape(w)}\b", lowered)) for w in _NEGATION_WORDS)


def preservation_issues(
    original: str, candidate: str
) -> List[Dict[str, str]]:
    """Fact drift between *original* and *candidate*.

    Returns a list of ``{severity, kind, message, snippet}``:

    * ``high``  — a number in the original is missing from the rewrite (the
      hard gate: a candidate that drops "42%" or "800 words" is rejected).
    * ``medium`` — a negation count flip (meaning may have inverted) or a
      proper noun in the original that vanished.
    """
    issues: List[Dict[str, str]] = []

    lost = extract_numbers(original) - extract_numbers(candidate)
    for num in sorted(lost):
        issues.append(
            {
                "severity": "high",
                "kind": "number",
                "message": f"the rewrite dropped the number '{num}'",
                "snippet": num,
            }
        )

    orig_neg = extract_negations(original)
    cand_neg = extract_negations(candidate)
    if orig_neg == 0 and cand_neg > 0:
        issues.append(
            {
                "severity": "medium",
                "kind": "negation",
                "message": "the rewrite introduces a negation the original did not have",
                "snippet": "negation added",
            }
        )
    elif orig_neg > 0 and cand_neg == 0:
        issues.append(
            {
                "severity": "medium",
                "kind": "negation",
                "message": "the rewrite drops every negation from the original",
                "snippet": "negation removed",
            }
        )

    lost_entities = extract_entities(original) - extract_entities(candidate)
    for ent in sorted(lost_entities):
        issues.append(
            {
                "severity": "medium",
                "kind": "entity",
                "message": f"the proper noun '{ent}' is missing from the rewrite",
                "snippet": ent,
            }
        )

    return issues


def _phrase_reuse_percent(original: str, candidate: str, n: int = 5) -> float:
    """Share of source n-grams reused by a candidate; lower means a fuller rewrite."""
    source_words = re.findall(r"[A-Za-z0-9']+", original.lower())
    candidate_words = re.findall(r"[A-Za-z0-9']+", candidate.lower())
    if len(source_words) < n:
        return 0.0
    source = {tuple(source_words[i:i + n]) for i in range(len(source_words) - n + 1)}
    output = {tuple(candidate_words[i:i + n]) for i in range(max(0, len(candidate_words) - n + 1))}
    return (len(source & output) / len(source)) * 100 if source else 0.0


def _has_hard_drift(issues: Sequence[Dict[str, str]]) -> bool:
    """True when a candidate fails the hard fact gate (lost numbers)."""
    return any(i["severity"] == "high" for i in issues)


def rank_candidates(
    original: str,
    candidates: Sequence[str],
    style: str = "academic",
    allowlist: Optional[set] = None,
) -> Tuple[Optional[str], List[Dict[str, str]]]:
    """Pick the best rewrite from *candidates* (best-of-N selection).

    Ranking rule, in priority order:

    1. Fact preservation first — a candidate that preserves every number
       beats any candidate that dropped one, *regardless of fluency* (the
       report's hard gate: never accept a fact-drifting rewrite while a
       faithful one exists).
    2. Naturalness second — among fact-faithful candidates (and among
       fact-drifting ones if *all* drift), the highest local detector score
       wins.

    Returns ``(best_text, warnings)`` where *best_text* is ``None`` when the
    pool is empty, and *warnings* explains any runner-ups that were skipped
    for fact drift (so the caller can surface "rejected one rewrite because
    it changed facts" honestly).
    """
    if not candidates:
        return None, []

    scored: List[Tuple[bool, float, int, str, List[Dict[str, str]]]] = []
    for cand in candidates:
        issues = preservation_issues(original, cand)
        report = analyze(cand, allowlist=allowlist)
        scored.append(
            (not _has_hard_drift(issues), _phrase_reuse_percent(original, cand), report.score, cand, issues)
        )

    # Sort: factual survival first, then lower phrase reuse, then naturalness.
    # This stops a near-copy from beating a genuinely re-authored alternative.
    scored.sort(key=lambda row: (not row[0], row[1], -row[2]))

    best = scored[0]
    warnings: List[Dict[str, str]] = []
    if not best[0]:
        warnings.append(
            {
                "severity": "warning",
                "kind": "fact_drift",
                "message": (
                    "every candidate rewrite changed a fact (a number was "
                    "dropped); the least-damaging one was kept — verify "
                    "numbers manually"
                ),
                "snippet": ", ".join(i["snippet"] for i in best[4]) or "numbers",
            }
        )
    else:
        for _, _, _, _, issues in scored[1:]:
            if _has_hard_drift(issues):
                warnings.append(
                    {
                        "severity": "warning",
                        "kind": "fact_drift",
                        "message": "a more fluent rewrite was rejected because it changed facts",
                        "snippet": ", ".join(i["snippet"] for i in issues),
                    }
                )
    return best[3], warnings
