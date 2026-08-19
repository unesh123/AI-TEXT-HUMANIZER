"""The Naturalizer engine: score a draft, then rewrite it.

The engine wires together the detector, the deterministic transforms, and
the optional LLM backend. It always produces a score and a deterministic
rewrite; when an LLM is configured it also produces a higher-quality
rewrite, which callers can prefer.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .detectors import (
    abstain_reasons,
    analyze,
    evidence_coverage,
    sentence_distribution,
)
from .diff import word_diff
from .critics import preservation_issues
from .styles import DEFAULT_STYLE, get_style
from .transforms import rewrite as deterministic_rewrite

try:
    from .llm import llm_available, llm_provider_label, rewrite_with_llm, rewrite_with_llm_details
    from .chain import run_chain
except ImportError:  # pragma: no cover - defensive
    llm_available = lambda provider="auto": False
    llm_provider_label = lambda provider="auto": None
    rewrite_with_llm = lambda text, style="academic", provider="auto", instruction=None, voice=0, best_of=1: None
    rewrite_with_llm_details = lambda text, style="academic", provider="auto", instruction=None, voice=0, best_of=1: None
    run_chain = lambda text, style="academic", provider="auto": None


@dataclass
class NaturalizeResult:
    """Output of a single naturalize call."""

    original: str
    rewritten: str
    score: int
    issues: List[Dict] = field(default_factory=list)
    llm_rewritten: Optional[str] = None
    llm_used: bool = False
    # "chain" (translation chain) or "single" (one-pass LLM rewrite).
    llm_method: Optional[str] = None
    # Provider used for the LLM rewrite ("claude" | "cx"), when one ran.
    llm_provider: Optional[str] = None
    style: str = DEFAULT_STYLE
    sentence_count: int = 0
    # Rewrite intensity (0-1) used for this run; higher = more restructuring
    # and more run-to-run variety.
    intensity: float = 0.5
    # Advanced detection signals: {"before": {...}, "after": {...}} — the
    # post-rewrite re-scan, so the UI can show the verification delta.
    metrics: Dict = field(default_factory=dict)
    # Explains a failed/explicitly-selected provider, so a silent fallback
    # to the deterministic rewrite is never unexplained ("cx" 401, etc.).
    llm_warning: Optional[str] = None

    def to_dict(self) -> Dict:
        chosen = self.llm_rewritten if self.llm_used else self.rewritten
        return {
            "original": self.original,
            "rewritten": self.rewritten,
            "llm_rewritten": self.llm_rewritten,
            "llm_used": self.llm_used,
            "llm_method": self.llm_method,
            "llm_provider": self.llm_provider,
            "llm_warning": self.llm_warning,
            "score": self.score,
            "issues": self.issues,
            "style": self.style,
            "sentence_count": self.sentence_count,
            "intensity": self.intensity,
            "metrics": self.metrics,
            "diff": word_diff(self.original, chosen),
        }


class Naturalizer:
    """Stateful wrapper so callers can configure defaults once."""

    def __init__(self, seed: int = 0, prefer_llm: bool = True):
        self.seed = seed
        self.prefer_llm = prefer_llm

    def naturalize(
        self,
        text: str,
        style: str = DEFAULT_STYLE,
        use_llm: Optional[bool] = None,
        seed: Optional[int] = None,
        deep: bool = False,
        provider: str = "auto",
        intensity: float = 0.5,
        instruction: Optional[str] = None,
        best_of: int = 1,
    ) -> NaturalizeResult:
        """Score *text* and produce rewritten versions.

        *use_llm*: ``None`` means "use LLM if configured and *prefer_llm*",
        ``True`` forces the LLM path (falling back to deterministic on
        failure), ``False`` disables it. *deep* routes the LLM rewrite
        through the 4-hop translation chain (EN->中文->日本語->suomi->EN)
        for maximum structural disruption; it falls back to the single-pass
        rewrite, then deterministic, if the chain fails. *intensity* (0-1)
        scales how aggressively the deterministic rewrite restructures the
        prose — higher values also add run-to-run variety, which ``seed``
        controls (a different seed yields different word choices).
        *best_of* (>1) generates that many LLM candidates and keeps the
        best that survives the factual critics (numbers preserved, then
        highest naturalness) — best-of-N selection, off by default so the
        single-shot path stays cheap and deterministic.
        """
        text = (text or "").strip()
        intensity = max(0.0, min(1.0, intensity))
        if not text:
            return NaturalizeResult(
                original="", rewritten="", score=100, style=style, intensity=intensity
            )

        profile = get_style(style)
        style = profile["name"]  # resolve aliases/unknown names to a real style
        report = analyze(
            text,
            allowlist=profile["allowlist"],
            keep_structure=profile.get("keep_structure", False),
        )

        rng = random.Random(seed if seed is not None else self.seed)
        rewritten, _, _ = deterministic_rewrite(
            text,
            rng=rng,
            allowlist=profile["allowlist"],
            min_words=profile["min_words"],
            max_words=profile["max_words"],
            intensity=intensity,
            contractions=profile.get("contractions", False),
        )

        llm_used = False
        llm_rewritten = None
        llm_method: Optional[str] = None
        llm_provider: Optional[str] = None
        candidate: Optional[str] = None
        want_llm = self.prefer_llm if use_llm is None else use_llm
        if want_llm and llm_available(provider):
            if deep:
                try:
                    candidate = run_chain(text, style=style, provider=provider)
                except Exception:  # pragma: no cover - network/timeout edge
                    candidate = None
                if candidate:
                    llm_method = "chain"
                    label = llm_provider_label(provider)
                    llm_provider = (label or "").split(" ")[0] or None
                else:
                    candidate_provider = rewrite_with_llm_details(
                        text, style=style, provider=provider, voice=rng.randrange(1, 5)
                    )
                    if candidate_provider:
                        candidate, llm_provider = candidate_provider
                        llm_method = "single"
            else:
                candidate_provider = rewrite_with_llm_details(
                    text,
                    style=style,
                    provider=provider,
                    instruction=instruction,
                    voice=rng.randrange(1, 5),
                    best_of=best_of,
                )
                if candidate_provider:
                    candidate, llm_provider = candidate_provider
                    llm_method = "single"
            # *llm_provider* already holds the provider that actually
            # served (failover-aware): when the user asked for cx but
            # cx was rate-limited, this is "claude" — never a lie.
            if candidate:
                # Polish the LLM output with the deterministic engine. The
                # translation chain (and to a lesser degree any LLM) can
                # re-render tells — e.g. a cliché that survives four hops —
                # so the final pass guarantees LLM output clears the same
                # naturalness floor as the deterministic path.
                polished, _, _ = deterministic_rewrite(
                    candidate,
                    rng=rng,
                    allowlist=profile["allowlist"],
                    min_words=profile["min_words"],
                    max_words=profile["max_words"],
                    intensity=intensity,
                    contractions=profile.get("contractions", False),
                )
                llm_rewritten = polished.strip() or candidate
                llm_used = True

        # When the user picked a specific provider and *no* provider could
        # produce a rewrite (every configured gateway failed), say so
        # instead of silently falling back. A failed first choice that
        # recovered on the failover provider stays quiet — the text is
        # real and served, no scare message needed. (Auto mode is designed
        # to fall back, so it stays quiet too.)
        llm_warning: Optional[str] = None
        if want_llm and provider != "auto" and not llm_used:
            try:
                from .llm import PROVIDER_NAMES

                if provider in PROVIDER_NAMES:
                    if not llm_available(provider):
                        llm_warning = (
                            f"{provider} isn't configured — add its env vars to "
                            ".env.local (see README). Using the deterministic "
                            "rewrite instead."
                        )
                    else:
                        llm_warning = (
                            f"{provider} call failed (check the gateway URL and "
                            f"API key in .env.local). Using the deterministic "
                            "rewrite instead."
                        )
            except Exception:  # pragma: no cover - defensive
                pass

        # Semantic-preservation gate: a fluent rewrite that drops a number
        # is still wrong. Revert hard factual drift before returning it.
        deterministic_semantic_issues = preservation_issues(text, rewritten or text)
        if any(issue.get("severity") == "high" for issue in deterministic_semantic_issues):
            rewritten = text
            deterministic_semantic_issues = preservation_issues(text, rewritten)
        llm_semantic_issues = []
        if llm_used and llm_rewritten:
            llm_semantic_issues = preservation_issues(text, llm_rewritten)
            if any(issue.get("severity") == "high" for issue in llm_semantic_issues):
                llm_rewritten = rewritten
                llm_warning = (
                    (llm_warning + " ") if llm_warning else ""
                ) + "LLM output was replaced by a fact-preserving rewrite because it changed a number."
                llm_semantic_issues = preservation_issues(text, llm_rewritten)
        # Post-rewrite verification scan: re-run the advanced signals on the
        # text the user will actually see, so the UI can show before/after.
        chosen = llm_rewritten if llm_used else rewritten
        semantic_issues = preservation_issues(text, chosen or text)
        after_report = analyze(
            chosen or text,
            allowlist=profile["allowlist"],
            keep_structure=profile.get("keep_structure", False),
        )
        from .human_memory import plain_register_score

        metrics = {
            "before": report.metrics,
            "after": after_report.metrics,
            "after_score": after_report.score,
            # Verified human-writing memory: how plain the register is
            # (fraction of words from the everyday vocabulary humans
            # actually use). 1.0 = reads like the human corpus.
            "plain_register": {
                "before": round(plain_register_score(text), 3),
                "after": round(plain_register_score(chosen or text), 3),
            },
            "semantic_preservation": {
                "issues": semantic_issues,
                "hard_drift": any(i.get("severity") == "high" for i in semantic_issues),
                "deterministic_issues": deterministic_semantic_issues,
                "llm_issues": llm_semantic_issues,
            },
        }

        return NaturalizeResult(
            original=text,
            rewritten=rewritten if rewritten.strip() else text,
            llm_rewritten=llm_rewritten,
            llm_used=llm_used,
            llm_method=llm_method,
            llm_provider=llm_provider,
            score=report.score,
            issues=[i.to_dict() for i in report.issues],
            style=style,
            sentence_count=report.sentence_count,
            intensity=intensity,
            metrics=metrics,
            llm_warning=llm_warning,
        )

    def detect(
        self,
        text: str,
        style: str = DEFAULT_STYLE,
    ) -> Dict:
        """Detector view: per-sentence AI/human labels plus overall verdict.

        Mirrors the classic detector dashboards: an AI/mixed/human sentence
        distribution (windowed — context-aware, like the passage scoring
        Turnitin-class systems use), the overall naturalness score, the
        advanced signals, contiguous AI "regions" (passage-level evidence),
        and a confidence-style verdict that honestly abstains when the
        sample is too short or list-dominated to support a claim.
        """
        text = (text or "").strip()
        profile = get_style(style)
        style = profile["name"]
        report = analyze(
            text,
            allowlist=profile["allowlist"],
            keep_structure=profile.get("keep_structure", False),
        )
        dist = sentence_distribution(
            text,
            allowlist=profile["allowlist"],
            keep_structure=profile.get("keep_structure", False),
        )
        human_pct = dist["human"]
        if human_pct >= 70 and report.score >= 70:
            verdict = "human"
        elif human_pct >= 40 and report.score >= 50:
            verdict = "mixed"
        else:
            verdict = "ai"

        # Evidence-based confidence: how many statistical signals actually
        # measured something, blended with how decisive the score is. A score
        # on a 20-word sample carries almost no statistical evidence, so its
        # confidence is capped regardless of the number.
        coverage = evidence_coverage(report)
        base = (
            max(40, min(99, report.score))
            if verdict == "human"
            else (max(40, min(99, 100 - report.score)) if verdict == "ai" else 62)
        )
        confidence = int(round(base * (0.7 + 0.3 * coverage)))
        confidence = max(30, min(99, confidence))

        abstain = abstain_reasons(text, report)
        return {
            "score": report.score,
            "verdict": verdict,
            "confidence": confidence,
            "distribution": {"ai": dist["ai"], "mix": dist["mix"], "human": dist["human"]},
            "sentences": dist["sentences"],
            "regions": dist["regions"],
            "abstain": abstain,
            "metrics": report.metrics,
            "issues": [i.to_dict() for i in report.issues],
            "word_count": len(text.split()),
        }

    def batch(
        self,
        texts: List[str],
        style: str = DEFAULT_STYLE,
        use_llm: Optional[bool] = None,
        deep: bool = False,
        provider: str = "auto",
        intensity: float = 0.5,
        seed: Optional[int] = None,
    ) -> List[NaturalizeResult]:
        """Naturalize many texts, returning one result per input."""
        base = self.seed if seed is None else seed
        return [
            self.naturalize(
                t,
                style=style,
                use_llm=use_llm,
                deep=deep,
                provider=provider,
                seed=base + i,
                intensity=intensity,
            )
            for i, t in enumerate(texts)
        ]
