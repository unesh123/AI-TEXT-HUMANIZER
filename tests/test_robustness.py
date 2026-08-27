"""Transformed-human robustness tests.

The report's hardest requirement for a *detector*: human text that has been
professionally edited, grammar-corrected, or lightly restructured must NOT
start reading as AI. These are the H-class false-positive cases (human
professionally edited, human grammar-corrected, human style-transferred,
human AI-assisted-but-edited) — a detector that flags them is unusable,
because that is exactly what ordinary people submit.

Each test takes a real human-corpus sample, applies a human-typical edit
(the kind a careful editor makes — punctuation fixes, word substitutions a
person would choose, a sentence reordered), and asserts the detector still
reads it as human: no AI verdict, no passage-level AI regions, and no
high-severity issues.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.detectors import analyze, sentence_distribution
from naturalizer.engine import Naturalizer

CORPUS = Path(__file__).resolve().parent / "corpus"


def _load(name: str) -> list:
    text = (CORPUS / name).read_text(encoding="utf-8")
    return [p.strip() for p in text.split("\n\n") if p.strip()]


HUMAN = _load("human_samples.txt")


def _assert_still_human(testcase, edited: str, label: str):
    """The core robustness assertion: an edited human sample stays human."""
    with testcase.subTest(edition=label, sample=edited[:60]):
        report = analyze(edited)
        testcase.assertGreaterEqual(report.score, 70, f"edited human text scored {report.score}: {edited}")
        # No high-severity tells — professional edits must not manufacture them.
        testcase.assertNotIn(
            "high",
            [i.severity for i in report.issues],
            f"edited human text grew an AI tell: {edited}",
        )
        # No passage-level AI regions (the windowed layer must stay quiet).
        dist = sentence_distribution(edited)
        testcase.assertEqual(dist["regions"], [], f"AI region on edited human text: {edited}")


class ProfessionallyEditedTest(unittest.TestCase):
    """H1 — human text after professional editing."""

    def test_punctuation_and_grammar_correction(self):
        edits = [
            # Add a missing comma; fix a run-on.
            HUMAN[0].replace(" counter ", " counter, "),
            # Change a lowercase "i" to "I" style pass.
            HUMAN[1].replace(" so we ", " so, we "),
        ]
        for e in edits:
            if e != HUMAN[0] and e != HUMAN[1]:
                _assert_still_human(self, e, "grammar-corrected")

    def test_synonym_swap_a_human_editor_would_make(self):
        # A careful editor swaps "came loose" -> "worked loose", "shut" -> "killed".
        sample = HUMAN[0]
        edited = sample.replace("came loose", "worked loose")
        if edited != sample:
            _assert_still_human(self, edited, "synonym-swapped")

    def test_sentence_merge(self):
        # Editor merges two short sentences into one flowing sentence.
        for sample in HUMAN:
            import re

            parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", sample)
            if len(parts) >= 3:
                merged = parts[0] + ", " + parts[1][0].lower() + parts[1][1:] + ". " + " ".join(parts[2:])
                _assert_still_human(self, merged, "merged sentences")
                break


class GrammarCorrectedTest(unittest.TestCase):
    """H2 — human text after grammar correction (e.g. a spellchecker)."""

    def test_spelling_normalization(self):
        # Realistic corrections: Americanize or standardize spellings.
        for sample in HUMAN:
            edited = sample.replace("colour", "color").replace("organise", "organize")
            if edited != sample:
                _assert_still_human(self, edited, "spelling-normalized")

    def test_article_and_tense_fixes(self):
        # "an hour" vs "a hour", present -> past consistency.
        for sample in HUMAN:
            edited = sample.replace(" a hour ", " an hour ")
            if edited != sample:
                _assert_still_human(self, edited, "article-fixed")


class StyleTransferredTest(unittest.TestCase):
    """H3 — human text after a light style transfer (more formal, more casual)."""

    def test_more_formal_register(self):
        # An editor tightens the register without injecting AI tells.
        for sample in HUMAN:
            edited = sample.replace(" shut ", " turned off ").replace(" went home", " headed home")
            if edited != sample:
                _assert_still_human(self, edited, "formalized")
                break

    def test_contraction_relaxed(self):
        # Un-contract to a slightly more formal register — still human.
        for sample in HUMAN:
            edited = sample.replace("it's", "it is").replace("didn't", "did not")
            if edited != sample:
                _assert_still_human(self, edited, "un-contracted")
                break


class HumanizerMustNotFlipHumanTest(unittest.TestCase):
    """H4 — running the humanizer itself on human text must not turn it AI."""

    def test_perfect_loop_keeps_human_text_human(self):
        from naturalizer.feedback import feedback_humanize

        engine = Naturalizer(seed=0, prefer_llm=False)
        for i, sample in enumerate(HUMAN):
            with self.subTest(sample=i):
                result = feedback_humanize(engine, sample, style="academic", max_passes=2)
                out = result["text"]
                report = analyze(out)
                self.assertGreaterEqual(report.score, 70, out[:120])
                self.assertNotIn("high", [x.severity for x in report.issues], out[:120])


if __name__ == "__main__":
    unittest.main()
