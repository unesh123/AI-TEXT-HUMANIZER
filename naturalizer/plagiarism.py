"""Local similarity ("plagiarism") check — pure stdlib, no external service.

Compares a document against reference texts the user supplies using word
n-gram *containment*: how much of the query's word sequences also appear in
a reference. This catches verbatim and near-verbatim copying, which is what
a real Turnitin-style flag usually looks like.

Honest limits (surfaced in the UI and in :func:`check`'s note):

* It only sees the reference texts you provide — it cannot scan the
  internet or a commercial database.
* It is n-gram overlap, so heavy paraphrase or synonym rewriting will score
  lower than the underlying borrowing.
* Scores are a heuristic (like every detector), not proof of authorship.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List

from .detectors import split_sentences

#: Words per shingle. Long enough to be distinctive, short enough to catch
#: copied phrasing with light re-arrangement.
_SHINGLE_N = 8
#: Below this query length the n-gram set is tiny; shrink the shingle size
#: so short passages still produce enough shingles to compare.
_SHORT_THRESHOLD_WORDS = 40
_SHORT_SHINGLE_N = 4
#: A query sentence whose shingles overlap any reference this much is
#: reported as a matching span.
_MATCH_THRESHOLD = 0.5

#: Similarity scores at/above these boundaries get the corresponding verdict.
_HIGH_VERDICT = 50
_MEDIUM_VERDICT = 20


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9'’ ]+", " ", text.lower())).strip()


def _words(text: str) -> List[str]:
    return _normalize(text).split()


def _shingles(words: List[str], n: int) -> set:
    return {tuple(words[i : i + n]) for i in range(max(0, len(words) - n + 1))}


def _containment(query: set, ref: set) -> float:
    if not query:
        return 0.0
    return len(query & ref) / len(query)


@dataclass
class MatchSpan:
    """One query sentence that overlaps reference material."""

    sentence: str
    snippet: str
    refs: List[int]  # indices into the provided reference list

    def to_dict(self) -> Dict:
        return {"sentence": self.sentence, "snippet": self.snippet, "refs": self.refs}


@dataclass
class PlagiarismReport:
    """Similarity of *text* against the supplied *refs*."""

    score: int              # 0-100 worst-case containment across refs
    verdict: str            # "low" | "medium" | "high"
    word_count: int
    per_ref: List[Dict]     # [{index, score}]
    matching: List[MatchSpan] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict:
        return {
            "score": self.score,
            "verdict": self.verdict,
            "word_count": self.word_count,
            "per_ref": self.per_ref,
            "matching": [m.to_dict() for m in self.matching],
            "note": self.note,
        }


def check(text: str, refs: List[str]) -> PlagiarismReport:
    """Score *text* for overlap against each reference in *refs*."""
    text = (text or "").strip()
    if not text:
        return PlagiarismReport(score=0, verdict="low", word_count=0, per_ref=[], note="No text to check.")

    refs = [r for r in (refs or []) if isinstance(r, str) and r.strip()]
    if not refs:
        return PlagiarismReport(
            score=0,
            verdict="low",
            word_count=len(_words(text)),
            per_ref=[],
            note="No reference texts provided — similarity can only be measured against sources you supply.",
        )

    q_words = _words(text)
    n = _SHORT_SHINGLE_N if len(q_words) < _SHORT_THRESHOLD_WORDS else _SHINGLE_N
    q_sh = _shingles(q_words, n)

    ref_shingles = []
    per_ref = []
    best = 0.0
    for i, ref in enumerate(refs):
        r_words = _words(ref)
        if not r_words:
            continue
        r_sh = _shingles(r_words, n)
        ref_shingles.append(r_sh)
        c = _containment(q_sh, r_sh)
        per_ref.append({"index": i, "score": round(c * 100)})
        best = max(best, c)

    # Sentence-level matching spans (against the union of all references).
    matching: List[MatchSpan] = []
    if ref_shingles:
        union = set().union(*ref_shingles)
        for sent in split_sentences(text):
            s_words = _words(sent)
            if len(s_words) < 4:
                continue
            s_sh = _shingles(s_words, n)
            c = _containment(s_sh, union)
            if c >= _MATCH_THRESHOLD:
                snippet = " ".join(s_words[:min(14, len(s_words))])
                hit_refs = [
                    i
                    for i, r_sh in enumerate(ref_shingles)
                    if _containment(s_sh, r_sh) >= _MATCH_THRESHOLD
                ]
                matching.append(MatchSpan(sentence=sent, snippet=snippet, refs=hit_refs or [0]))

    score = round(best * 100)
    verdict = "high" if score >= _HIGH_VERDICT else ("medium" if score >= _MEDIUM_VERDICT else "low")

    note = (
        f"Similarity is word n-gram overlap ({n}-grams) against {len(refs)} reference "
        f"text(s). It catches verbatim copying but not heavy paraphrase, and only "
        f"covers the sources you provided — not the internet."
    )
    if verdict == "high":
        note = "Substantial overlap with the provided sources — review and rewrite the matching passages. " + note
    elif verdict == "medium":
        note = "Noticeable overlap with the provided sources. " + note
    else:
        note = "Little or no verbatim overlap with the provided sources. " + note

    return PlagiarismReport(
        score=score,
        verdict=verdict,
        word_count=len(q_words),
        per_ref=per_ref,
        matching=matching,
        note=note,
    )
