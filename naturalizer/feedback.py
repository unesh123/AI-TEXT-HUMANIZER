"""Live feedback-loop humanization — \"perfect\" mode.

Closes the loop the way tools like untell do: rewrite the draft, re-scan the
rewrite with the detector, and when the scan still flags AI tells, feed the
remaining issues back into the next rewrite as a concrete instruction. Each
pass starts from the previous pass's output, so the text converges toward
\"human-like\" instead of being a single one-shot rewrite.

The feedback driver itself is deterministic and dependency-free. The rewrite
step prefers the LLM path when a provider is configured (any of the
providers in ``naturalizer.llm``) and falls back to the deterministic
engine at rising intensity otherwise, so the loop works on the free tier
too — it just converges more slowly.

This module also exposes ``detector_status()``, the honest multi-detector
panel: the local detector is always live, and third-party detectors
(GPTZero, ZeroGPT, Originality.ai, Turnitin) are reported as configured only
when their API keys are present in the environment. The loop re-scans with
the local detector; when you add a third-party key later, the same hook
re-runs that detector between passes.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .critics import preservation_issues
from .detectors import analyze, sentence_distribution
from .engine import Naturalizer
from .human_memory import plain_register_score
from .unicode_marks import strip_marks
from .styles import DEFAULT_STYLE, get_style

#: Detectors the UI panel reports on. ``key`` is the env var that activates
#: each one; ``live`` marks those whose scores we can actually re-scan with
#: (the local detector always, GPTZero/ZeroGPT when their key is present —
#: see ``detectors_live`` for the real HTTP clients). Turnitin has no
#: individual/developer API — its row is honest about that.
DETECTORS: List[Dict[str, str]] = [
    {"name": "local", "label": "Naturalizer detector", "key": None, "live": True},
    {"name": "gptzero", "label": "GPTZero", "key": "GPTZERO_API_KEY", "live": True},
    {"name": "zerogpt", "label": "ZeroGPT", "key": "ZEROGPT_API_KEY", "live": True},
    {"name": "originality", "label": "Originality.ai", "key": "ORIGINALITY_API_KEY", "live": False},
    {"name": "turnitin", "label": "Turnitin", "key": "TURNITIN_API_KEY", "live": False},
]

#: Floor for \"human-like\". Stop looping once a pass clears it.
HUMAN_FLOOR = 80

#: Plain-register floor for the loop. Below this, the rewrite still reaches
#: for formal vocabulary (utilize, leverage, moreover...) and gets another
#: pass naming the exact words — the loop does not stop while the text
#: still reads stiff, only when it reads like the everyday human corpus.
PLAIN_FLOOR = 0.78

#: Cap on feedback passes so a pathological text can't burn unbounded LLM
#: credits (each pass is one rewrite + one re-scan).
MAX_PASSES = 4


def detector_status() -> List[Dict[str, object]]:
    """Configured status of every detector in the panel."""
    out: List[Dict[str, object]] = []
    for det in DETECTORS:
        configured = det["key"] is None or bool(os.environ.get(det["key"]))
        out.append(
            {
                "name": det["name"],
                "label": det["label"],
                "configured": configured,
                "live": det["live"],
                "env": det["key"],
                "note": (
                    None
                    if configured
                    else (
                        f"add {det['key']} to .env.local to enable"
                        if det["key"]
                        else None
                    )
                ),
            }
        )
    return out


def scan_live(text: str) -> List[Dict[str, object]]:
    """Live AI% score from every configured third-party detector.

    Thin wrapper over ``detectors_live.scan_live`` so the server and tests
    import from one place. Returns a list of ``{name, label, score,
    verdict, error}`` — one per configured key. Detectors without a key are
    skipped (the panel's status rows explain how to enable them).
    """
    from .detectors_live import scan_live as _scan

    return _scan(text)


#: Human band for the statistical signals real detectors weigh. The loop
#: keeps going until *all* of these sit in the human range — not just the
#: naturalness score — so a pass that clears the floor but still reads as
#: flat-rhythm / mid-frequency-vocabulary prose gets another round.
BURSTINESS_HUMAN = 50.0   # sentence-length variation (coefficient of variation)
SYNTACTIC_HUMAN = 70.0    # syntactic variety
PERPLEXITY_HUMAN = 40.0   # word-predictability (compression proxy)
WORD_CHOICE_HUMAN = 40.0  # rare-word surprisal; only a tell with formulaic prose


def _band_weak(metrics: Dict[str, Optional[float]]) -> List[str]:
    """Which statistical signals sit below the human band ('' = fine)."""
    weak: List[str] = []
    for key, floor in (
        ("burstiness", BURSTINESS_HUMAN),
        ("syntactic", SYNTACTIC_HUMAN),
        ("perplexity", PERPLEXITY_HUMAN),
        ("word_choice", WORD_CHOICE_HUMAN),
    ):
        value = metrics.get(key)
        if value is not None and value < floor:
            # word_choice is only a tell in combination with formulaic
            # prose (the syntactic signal gates that) — concrete topic
            # vocabulary like a recipe or field guide is not.
            if key == "word_choice" and metrics.get("syntactic", 100) >= SYNTACTIC_HUMAN:
                continue
            weak.append(key)
    return weak


#: Minimum size of a contiguous AI sentence run that counts as a "region".
#: Commercial detectors weigh whole suspicious *passages*; a single flagged
#: sentence is weak evidence, but a block of 3+ AI-tagged sentences in a row
#: is the passage-level signal they act on.
REGION_MIN_RUN = 3


def _region_weak(text: str, style: str) -> List[Dict]:
    """Contiguous AI runs in *text* that clear the passage-level threshold.

    Uses the windowed (context-aware) sentence segmentation — the same
    passage layer Turnitin-class systems score on — so a rewrite that clears
    the naturalness floor but still leaves a machine-written *block* gets
    another pass. Returns the offending regions ([] = no passage-level
    signal)."""
    profile = get_style(style)
    dist = sentence_distribution(
        text,
        allowlist=profile["allowlist"],
        keep_structure=profile.get("keep_structure", False),
    )
    return [r for r in dist.get("regions", []) if r["count"] >= REGION_MIN_RUN]


def _band_hints(metrics: Dict[str, Optional[float]]) -> List[str]:
    """Human-readable guidance for the signals still below the band."""
    hints: List[str] = []
    b = metrics.get("burstiness")
    if b is not None and b < BURSTINESS_HUMAN:
        hints.append(
            "Vary your sentence lengths dramatically — mix one short punchy "
            "sentence with much longer ones instead of the uniform rhythm."
        )
    s = metrics.get("syntactic")
    if s is not None and s < SYNTACTIC_HUMAN:
        hints.append(
            "Vary how sentences start and are structured — avoid the same "
            "grammatical shape repeating."
        )
    w = metrics.get("word_choice")
    if w is not None and w < WORD_CHOICE_HUMAN and (s or 100) < SYNTACTIC_HUMAN:
        hints.append(
            "Swap formal mid-frequency vocabulary (utilize, facilitate, "
            "numerous, ultimately, moreover) for plain everyday words a "
            "person would actually say."
        )
    return hints


def _remaining_issues(text: str, style: str, limit: int = 6) -> List[str]:
    """Concrete, actionable tells the re-scan found in *text*."""
    profile = get_style(style)
    report = analyze(
        text,
        allowlist=profile["allowlist"],
        keep_structure=profile.get("keep_structure", False),
    )
    ordered = sorted(
        report.issues, key=lambda i: {"high": 0, "medium": 1, "low": 2}.get(i.severity, 3)
    )
    lines: List[str] = []
    for issue in ordered[:limit]:
        lines.append(
            f'- {issue.snippet.strip()!r} reads like {issue.message.split("—")[0].strip()}; '
            f"fix: {issue.suggestion}."
        )
    lines += _band_hints(report.metrics)

    # Plain-register signal: if the draft still reaches for formal
    # vocabulary, name the exact words so the next pass can fix them —
    # "check until it's plain", not just until the naturalness score.
    from .human_memory import plain_register_guidance, plain_register_score

    if plain_register_score(text) < PLAIN_FLOOR:
        plain_note = plain_register_guidance(text)
        if plain_note:
            lines.append(f"- Plain register: {plain_note}")

    # Passage-level signal: a whole block of AI-tagged sentences must be
    # restructured as a block, not sentence-by-sentence.
    for region in _region_weak(text, style):
        snippet = region["text"]
        if len(snippet) > 90:
            snippet = snippet[:87].rstrip() + "…"
        lines.append(
            f"- Sentences {region['start'] + 1}–{region['end'] + 1} read as one "
            f"machine-written block ({region['count']} in a row). Rework them as a "
            f"passage: change the order ideas arrive in, split or merge sentences "
            f"differently, and let one sentence run long while another stays short "
            f"('{snippet}')."
        )
    return lines


def feedback_humanize(
    engine: Naturalizer,
    text: str,
    style: str = DEFAULT_STYLE,
    intensity: float = 0.75,
    seed: int = 0,
    provider: str = "auto",
    use_llm: Optional[bool] = None,
    max_passes: int = MAX_PASSES,
    floor: int = HUMAN_FLOOR,
    best_of: int = 3,
) -> Dict[str, object]:
    """Rewrite *text* until the re-scan says it reads human, pass by pass.

    *best_of* (>1) turns each LLM pass into best-of-N candidate selection:
    several rewrites are generated and the one that preserves every number
    (and reads most human) is kept — the report's "generate several, keep
    the best surviving" strategy. Defaults to 3 for the perfect loop, where
    quality matters more than cost.

    Returns ``{text, passes, scores, floor, method, llm_used, warning,
    fact_issues}``: ``scores`` is the per-pass naturalness score (0-100) of
    each candidate, starting with the original's score at index 0;
    ``fact_issues`` lists any factual drift (dropped numbers, negation
    flips) the final text still carries vs. the original, so the UI can
    warn the user to double-check figures.
    """
    text = (text or "").strip()
    if not text:
        return {
            "text": "",
            "passes": 0,
            "scores": [100],
            "floor": floor,
            "plain_floor": PLAIN_FLOOR,
            "method": "none",
            "llm_used": False,
            "warning": None,
            "fact_issues": [],
            "converged": True,
            "remaining": {"signals": [], "regions": [], "plain_register": 1.0},
            "metrics": {},
        }

    profile = get_style(style)
    style = profile["name"]

    original_report = analyze(
        text,
        allowlist=profile["allowlist"],
        keep_structure=profile.get("keep_structure", False),
    )
    scores: List[int] = [original_report.score]
    current = text
    passes = 0
    llm_used = False
    method = "none"
    warning: Optional[str] = None

    # Run at least one rewrite; loop only while the scan still flags tells.
    for i in range(1, max_passes + 1):
        instruction = None
        if i > 1:
            issues = _remaining_issues(current, style)
            if issues:
                instruction = (
                    "The previous rewrite still reads as machine-written in "
                    "these spots. Rework them so they read naturally:\n"
                    + "\n".join(issues)
                )
            # Fact gate: if the previous pass dropped a number, the next
            # pass must restore it — naturalness never justifies changing
            # facts (report's hard gate: "a candidate that changes a
            # critical number should be rejected").
            fact = [
                i for i in preservation_issues(text, current)
                if i["severity"] == "high"
            ]
            if fact:
                restored = ", ".join(sorted({i["snippet"] for i in fact}))
                instruction = (
                    f"Restore the number(s) {restored} that the rewrite "
                    f"dropped — every figure from the original draft must "
                    f"survive verbatim.\n"
                    + (instruction or "")
                )
        result = engine.naturalize(
            current,
            style=style,
            use_llm=use_llm,
            seed=seed + i,
            provider=provider,
            intensity=min(1.0, intensity + 0.1 * (i - 1)),
            instruction=instruction,
            best_of=best_of,
        )
        chosen = result.llm_rewritten if result.llm_used else result.rewritten
        chosen = (chosen or current).strip()
        # Invisible-Unicode hygiene on every path: the LLM can (rarely)
        # reintroduce zero-width / stealth characters, so the candidate the
        # user actually sees is scrubbed of invisible fingerprints before
        # the re-scan.
        chosen, _ = strip_marks(chosen)
        passes = i
        if result.llm_used:
            llm_used = True
            method = result.llm_method or "single"
        elif method == "none":
            method = "deterministic"

        # Re-scan the candidate the user would actually see.
        after = analyze(
            chosen,
            allowlist=profile["allowlist"],
            keep_structure=profile.get("keep_structure", False),
        )
        scores.append(after.score)
        current = chosen
        warning = result.llm_warning or warning

        # Converge on the full human band, not just the naturalness floor:
        # a pass that clears the score but still reads as uniform-rhythm /
        # mid-frequency-vocabulary prose — or still leaves a whole
        # machine-written block (the windowed passage signal) — gets another
        # round.
        if (
            after.score >= floor
            and not _band_weak(after.metrics)
            and not _region_weak(current, style)
            and plain_register_score(current) >= PLAIN_FLOOR
        ):
            break

    # Recompute the complete final gate for the payload. A score crossing
    # the floor alone is not convergence: rhythm, sentence architecture,
    # passage-level evidence, plain register, and factual preservation must
    # all be clear before the UI may call the loop complete.
    final_report = analyze(
        current,
        allowlist=profile["allowlist"],
        keep_structure=profile.get("keep_structure", False),
    )
    remaining_signals = _band_weak(final_report.metrics)
    remaining_regions = _region_weak(current, style)
    final_plain = plain_register_score(current)

    # Honest fact report: any numbers the final text still lost vs. the
    # original (plus negation/entity drift), so the UI can tell the user
    # to double-check figures instead of silently altering them.
    fact_issues = preservation_issues(text, current)
    high_fact_issues = [i for i in fact_issues if i.get("severity") == "high"]
    converged = (
        final_report.score >= floor
        and not remaining_signals
        and not remaining_regions
        and final_plain >= PLAIN_FLOOR
        and not high_fact_issues
    )

    # After the loop, re-scan the final text with every configured live
    # third-party detector (GPTZero / ZeroGPT when their key is present) so
    # the panel shows real %AI scores for the text the user will see.
    live_scores = scan_live(current)

    return {
        "text": current,
        "passes": passes,
        "scores": scores,
        "floor": floor,
        "plain_floor": PLAIN_FLOOR,
        "method": method,
        "llm_used": llm_used,
        "warning": warning,
        "fact_issues": fact_issues,
        "converged": converged,
        "remaining": {
            "signals": remaining_signals,
            "regions": remaining_regions,
            "plain_register": round(final_plain, 3),
        },
        "detectors": detector_status(),
        "live_scores": live_scores,
        # Same verified human-writing memory as the single-shot path: how
        # plain the register reads on the original vs. the converged text
        # (1.0 = built from the everyday vocabulary humans actually use).
        "metrics": {
            "plain_register": {
                "before": round(plain_register_score(text), 3),
                "after": round(final_plain, 3),
            },
        },
    }
