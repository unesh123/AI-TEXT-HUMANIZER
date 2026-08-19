"""Word-level diff between two texts, for before/after highlighting.

Uses a longest-common-subsequence comparison over word tokens so the output
is a minimal set of additions and removals. The result is a list of ops in
document order:

    [{"type": "same", "text": "..."}, {"type": "del", "text": "..."}, ...]

``type`` is one of ``same`` (present in both), ``del`` (only in the
original), or ``add`` (only in the rewrite). Concatenating the ``same`` and
``del`` texts reproduces the original; concatenating ``same`` and ``add``
reproduces the rewrite — this invariant is covered by the test suite.

Large inputs are handled with graceful degradation: word-level LCS for
moderate sizes, sentence-level LCS for very large ones, and an
everything-changed fallback beyond that.
"""

from __future__ import annotations

import re
from array import array
from typing import Dict, List, Tuple

#: Maximum cells in the LCS DP table (memory bound). 4M * 4 bytes = 16 MB.
_MAX_CELLS = 4_000_000

#: Word tokens include their trailing whitespace so document flow is kept.
_TOKEN_RE = re.compile(r"\S+\s*")

#: Sentence tokens (for the large-text fallback) also keep trailing spaces.
_SENTENCE_TOKEN_RE = re.compile(r"[^.!?]*[.!?]+\s*|[^.!?]+$")


def _tokens(text: str, pattern: re.Pattern) -> List[str]:
    return pattern.findall(text)


def _diff_sequences(a: List[str], b: List[str]) -> List[Dict[str, str]]:
    """LCS diff of two token sequences, using a flattened DP table."""
    n, m = len(a), len(b)
    if n == 0:
        return [{"type": "add", "text": t} for t in b]
    if m == 0:
        return [{"type": "del", "text": t} for t in a]

    width = m + 1
    dp = array("I", [0]) * ((n + 1) * width)

    for i in range(n - 1, -1, -1):
        row, nxt = i * width, (i + 1) * width
        for j in range(m - 1, -1, -1):
            if a[i].rstrip() == b[j].rstrip():
                dp[row + j] = dp[nxt + j + 1] + 1
            else:
                right, down = dp[row + j + 1], dp[nxt + j]
                dp[row + j] = down if down >= right else right

    ops: List[Dict[str, str]] = []
    i = j = 0
    while i < n and j < m:
        if a[i].rstrip() == b[j].rstrip():
            if a[i] == b[j]:
                ops.append({"type": "same", "text": a[i]})
            else:
                # Same word, different trailing whitespace. Keep the common
                # text as "same" and the spacing delta as its own del/add so
                # both sides reconstruct exactly.
                common = a[i] if len(a[i]) <= len(b[j]) else b[j]
                ops.append({"type": "same", "text": common})
                if len(a[i]) > len(b[j]):
                    ops.append({"type": "del", "text": a[i][len(common):]})
                elif len(b[j]) > len(a[i]):
                    ops.append({"type": "add", "text": b[j][len(common):]})
            i += 1
            j += 1
        elif dp[(i + 1) * width + j] >= dp[i * width + j + 1]:
            ops.append({"type": "del", "text": a[i]})
            i += 1
        else:
            ops.append({"type": "add", "text": b[j]})
            j += 1
    while i < n:
        ops.append({"type": "del", "text": a[i]})
        i += 1
    while j < m:
        ops.append({"type": "add", "text": b[j]})
        j += 1
    return ops


def _everything_changed(a: List[str], b: List[str]) -> List[Dict[str, str]]:
    """Degenerate fallback: mark the whole text as replaced."""
    return [{"type": "del", "text": t} for t in a] + [
        {"type": "add", "text": t} for t in b
    ]


def word_diff(before: str, after: str) -> List[Dict[str, str]]:
    """Return the minimal add/remove/same op list between two texts."""
    before = before or ""
    after = after or ""
    if before == after:
        return [{"type": "same", "text": t} for t in _tokens(before, _TOKEN_RE)]

    a, b = _tokens(before, _TOKEN_RE), _tokens(after, _TOKEN_RE)
    if len(a) * len(b) <= _MAX_CELLS:
        return _diff_sequences(a, b)

    # Very large inputs: compare at sentence granularity instead.
    sa, sb = _tokens(before, _SENTENCE_TOKEN_RE), _tokens(after, _SENTENCE_TOKEN_RE)
    if len(sa) * len(sb) <= _MAX_CELLS:
        return _diff_sequences(sa, sb)

    return _everything_changed(a, b)


def reconstruct(ops: List[Dict[str, str]], include: Tuple[str, ...]) -> str:
    """Rebuild text from *ops*, keeping only the listed types."""
    return "".join(op["text"] for op in ops if op["type"] in include)
