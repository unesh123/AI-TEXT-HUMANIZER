"""The Naturalizer engine: score a draft, then rewrite it.

The engine wires together the detector, the deterministic transforms, and
the optional LLM backend. It always produces a score and a deterministic
rewrite; when an LLM is configured it also produces a higher-quality
rewrite, which callers can prefer.
"""

from __future__ import annotations

import random
import re
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


def _ngram_overlap(original: str, rewritten: str, n: int = 5) -> Dict[str, float]:
    """Measure consecutive-word reuse, separate from semantic preservation."""
    def grams(value: str) -> set:
        words = re.findall(r"[A-Za-z0-9']+", value.lower())
        return {tuple(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}
    source = grams(original)
    output = grams(rewritten)
    shared = len(source & output)
    return {
        "source_ngrams": len(source),
        "output_ngrams": len(output),
        "shared_ngrams": shared,
        "reuse_percent": round((shared / len(source)) * 100, 1) if source else 0.0,
    }


def _requires_full_rewrite_retry(original: str, candidate: str) -> bool:
    """True when a purported full rewrite still copies the source too closely."""
    word_count = len(re.findall(r"[A-Za-z0-9']+", original))
    if word_count < 35:
        return False
    overlap = _ngram_overlap(original, candidate)["reuse_percent"]
    return overlap > 20.0


# Naturalizer is local-only: no cloud LLM backend and no translation
# chain. Every rewrite is deterministic, offline, and reviewable.


@dataclass
class NaturalizeResult:
    """Output of a single naturalize call."""

    original: str
    rewritten: str
    score: int
    issues: List[Dict] = field(default_factory=list)
    llm_rewritten: Optional[str] = None
    llm_used: bool = False
    # Legacy fields (local-only build): always None / False. Retained so the
    # API shape stays backward compatible with older clients.
    llm_method: Optional[str] = None
    llm_provider: Optional[str] = None
    style: str = DEFAULT_STYLE
    sentence_count: int = 0
    # Rewrite intensity (0-1) used for this run; higher = more restructuring
    # and more run-to-run variety.
    intensity: float = 0.5
    # Advanced detection signals: {"before": {...}, "after": {...}} — the
    # post-rewrite re-scan, so the UI can show the verification delta.
    metrics: Dict = field(default_factory=dict)
    # Legacy field (local-only build): always None.
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
        rewrite_mode: str = "full",
    ) -> NaturalizeResult:
        """Score *text* and produce a deterministic local rewrite.

        Naturalizer is local-only: there is no cloud LLM backend and no
        translation chain. The ``use_llm``, ``deep``, ``provider``,
        ``instruction``, and ``best_of`` parameters are retained for API
        backward compatibility but are inert — the deterministic engine is
        always used. *intensity* (0-1) scales how aggressively the
        deterministic rewrite restructures the prose, and *seed* controls
        run-to-run word-choice variety.
        """
        text = (text or "").strip()
        rewrite_mode = str(rewrite_mode or "full").strip().lower()
        if rewrite_mode not in {"light", "standard", "full"}:
            rewrite_mode = "full"
        intensity = max(0.0, min(1.0, intensity))
        mode_directives = {
            "light": "Make conservative clarity edits only. Preserve paragraph and sentence structure wherever possible.",
            "standard": "Rewrite each paragraph substantially while preserving its original order and factual content.",
            "full": "Re-author the entire document. Rebuild sentence structures and paragraph flow from the source meaning, not its wording.",
        }
        # Rewrite approach controls structure and prompting; intensity stays
        # within the value already capped by the active plan at the API layer.
        if rewrite_mode == "light":
            intensity = min(intensity, 0.45)
        instruction = "\n\n".join(part for part in (mode_directives[rewrite_mode], instruction) if part)
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

        # Provider execution: support StealthGPT API if configured
        from . import stealthgpt

        llm_used = False
        llm_rewritten = None
        llm_method: Optional[str] = None
        llm_provider: Optional[str] = None
        candidate: Optional[str] = None
        llm_warning: Optional[str] = None
        stealthgpt_meta: Dict = {}

        provider_norm = (provider or "auto").strip().lower()
        should_try_stealthgpt = (
            (provider_norm in {"stealthgpt", "stealth", "api"} or (provider_norm in {"auto", "local"} and use_llm))
            and stealthgpt.is_configured()
            and use_llm is not False
        )

        if should_try_stealthgpt:
            stealth_res, stealth_meta, stealth_err = stealthgpt.stealthify(
                text,
                style=style,
            )
            if stealth_res:
                llm_used = True
                llm_rewritten = stealth_res
                llm_provider = "stealthgpt"
                llm_method = f"stealthgpt-{stealth_meta.get('model', 'heavy')}"
                stealthgpt_meta = stealth_meta
            elif stealth_err:
                llm_warning = f"StealthGPT notice: {stealth_err} (using local deterministic rewrite)"

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
        after_verification = self.detect(chosen or text, style=style)
        after_report = analyze(
            chosen or text,
            allowlist=profile["allowlist"],
            keep_structure=profile.get("keep_structure", False),
        )
        from .human_memory import plain_register_score

        overlap = _ngram_overlap(text, chosen or text)
        full_retry_needed = rewrite_mode == "full" and _requires_full_rewrite_retry(text, chosen or text)
        metrics = {
            "before": report.metrics,
            "after": after_report.metrics,
            "after_score": after_report.score,
            "rewrite_mode": rewrite_mode,
            "source_overlap": overlap,
            "rewrite_quality": {
                "status": "review" if full_retry_needed else "verified",
                "full_reauthor_target": rewrite_mode == "full",
                "needs_review": full_retry_needed,
            },
            "detector_comparison": {
                "before": {
                    "score": report.score,
                    "verdict": self.detect(text, style=style)["verdict"],
                },
                "after": {
                    "score": after_verification["score"],
                    "verdict": after_verification["verdict"],
                    "confidence": after_verification["confidence"],
                    "distribution": after_verification["distribution"],
                },
            },
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
        if stealthgpt_meta:
            metrics["stealthgpt"] = stealthgpt_meta

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
        strong_ai_evidence = verdict == "ai" and (
            report.score <= 25 or dist["ai"] >= 75
        )
        if abstain and not strong_ai_evidence:
            # A short or structurally unsuitable sample cannot support an
            # authorship-style conclusion. Keep the raw score available but
            # surface an explicit uncertain verdict and cap confidence.
            verdict = "uncertain"
            confidence = min(confidence, 45)
        elif verdict == "human":
            # A local heuristic can say the prose looks human-like, but it
            # cannot prove independent human authorship. Keep the verdict for
            # usability, cap confidence, and let provenance override it when
            # the application knows it generated the exact text.
            confidence = min(confidence, 70)
        return {
            "score": report.score,
            "verdict": verdict,
            "confidence": confidence,
            "evidence_coverage": round(coverage, 3),
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
        rewrite_mode: str = "full",
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
                rewrite_mode=rewrite_mode,
            )
            for i, t in enumerate(texts)
        ]
