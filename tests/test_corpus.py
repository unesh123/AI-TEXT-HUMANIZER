"""Corpus tests: AI-heavy samples must score low, human samples must score high.

The corpus lives in tests/corpus/ — each file holds paragraphs separated by
blank lines. Keep the human samples free of AI tells; keep the AI samples
deliberately stuffed with them.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.detectors import analyze

CORPUS = Path(__file__).resolve().parent / "corpus"


def _load(name: str) -> list:
    text = (CORPUS / name).read_text(encoding="utf-8")
    return [p.strip() for p in text.split("\n\n") if p.strip()]


class AiCorpusTest(unittest.TestCase):
    samples = _load("ai_samples.txt")

    def test_corpus_is_not_empty(self):
        self.assertGreaterEqual(len(self.samples), 5)

    def test_every_ai_sample_scores_low(self):
        for i, sample in enumerate(self.samples):
            with self.subTest(sample=i):
                report = analyze(sample)
                self.assertLess(report.score, 60, sample[:80])
                self.assertTrue(report.issues, "expected issues for AI-heavy sample")

    def test_ai_corpus_covers_all_detector_kinds(self):
        kinds = set()
        for sample in self.samples:
            kinds |= {issue.kind for issue in analyze(sample).issues}
        for expected in ("filler", "cliche", "hedge", "transition", "formulaic", "structure"):
            self.assertIn(expected, kinds)

    def test_rewrite_raises_ai_scores(self):
        """The point of the humanizer: rewritten AI text should score human."""
        from naturalizer.engine import Naturalizer

        eng = Naturalizer(seed=0)
        for i, sample in enumerate(self.samples):
            with self.subTest(sample=i):
                result = eng.naturalize(sample, use_llm=False)
                after = analyze(result.rewritten).score
                self.assertGreaterEqual(after, 75, result.rewritten[:140])


class LiveLlmCorpusTest(unittest.TestCase):
    """The external corpus: real commercial-LLM output.

    These samples are NOT part of the tuned AI set (ai_samples.txt) — they
    are genuine live LLM essays (see live_llm_samples.txt header for
    provenance). The point of this test is honesty: modern LLM prose
    mostly reads human-grade to the local heuristic detector, and the
    benchmark reports that separately instead of hiding it.
    """
    samples = [
        p.strip()
        for p in _load("live_llm_samples.txt")
        if not p.startswith("#")
    ]

    def test_corpus_is_not_empty(self):
        self.assertGreaterEqual(len(self.samples), 5)

    def test_samples_are_essay_length(self):
        for i, sample in enumerate(self.samples):
            with self.subTest(sample=i):
                self.assertGreater(len(sample.split()), 100, sample[:80])

    def test_no_crafted_tells_shoved_in(self):
        """Guard against backsliding: never stuff these with AI tells.

        They must stay what they are — genuine model output — or the
        "external" label becomes a lie and the benchmark's honesty check
        stops measuring anything real. Single ordinary homonyms ("drive",
        "challenge") appear naturally in real prose, so only multi-word
        phrase tells count as evidence of crafting.
        """
        from naturalizer.human_memory import AI_TELLS

        phrase_tells = [t for t in AI_TELLS if " " in t]
        for i, sample in enumerate(self.samples):
            with self.subTest(sample=i):
                lowered = sample.lower()
                hits = [t for t in phrase_tells if t in lowered]
                # Real output may brush a phrase or two; it must not be
                # constructed out of them.
                self.assertLess(
                    len(hits), 3, f"sample {i} stuffed with tells: {hits}"
                )


class HumanCorpusTest(unittest.TestCase):
    samples = _load("human_samples.txt")

    def test_corpus_is_not_empty(self):
        self.assertGreaterEqual(len(self.samples), 5)

    def test_every_human_sample_scores_high(self):
        for i, sample in enumerate(self.samples):
            with self.subTest(sample=i):
                report = analyze(sample)
                self.assertGreaterEqual(report.score, 75, sample[:80])

    def test_human_samples_have_no_high_severity_issues(self):
        for i, sample in enumerate(self.samples):
            with self.subTest(sample=i):
                severities = [issue.severity for issue in analyze(sample).issues]
                self.assertNotIn("high", severities)

    def test_human_samples_survive_rewrite_untouched(self):
        """Natural prose is left alone — the swaps must not over-rewrite it.

        Several samples deliberately contain phrases the rewrite knows about
        ("on the same page", "necessary evil", literal "in turn" / "as
        such", present-tense "hit the ground running", "Moving forward,",
        "bandwidth for"); they must all survive verbatim.
        """
        from naturalizer.transforms import rewrite

        for i, sample in enumerate(self.samples):
            with self.subTest(sample=i):
                out, _, changed = rewrite(sample)
                self.assertFalse(
                    changed, f"rewrite altered human prose:\n{sample}\n->\n{out}"
                )


if __name__ == "__main__":
    unittest.main()
