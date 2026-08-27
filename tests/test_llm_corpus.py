"""Corpus tests for the LLM rewrite paths.

The deterministic engine has a corpus guarantee: every AI-heavy sample must
score >= 75 after a deterministic rewrite (see test_corpus.py). These tests
extend the same guarantee to the LLM paths — single-pass and deep
(translation chain) — using *recorded* outputs from the real providers.

The fixtures in ``tests/corpus/llm_single_fixtures.json`` and
``tests/corpus/llm_deep_fixtures.json`` were generated with::

    python tools/gen_llm_fixtures.py

They are baked in so this suite stays hermetic — no network, no API keys at
test time. The fixture-count assertions fail if the corpus and fixtures
drift out of sync, which forces a regeneration whenever the corpus or the
LLM prompts change.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.detectors import analyze

CORPUS = Path(__file__).resolve().parent / "corpus"

#: Post-rewrite naturalness floors (measured minima included the generation
#: runs that produced the fixtures — see tools/gen_llm_fixtures.py output).
#: LLM output is polished through the deterministic engine, so these sit
#: comfortably above the deterministic floor of 75.
SINGLE_FLOOR = 80  # measured min 94 on the real single-pass rewrites
DEEP_FLOOR = 75    # measured min 82 on the real chain rewrites


def _ai_sample_count() -> int:
    text = (CORPUS / "ai_samples.txt").read_text(encoding="utf-8")
    return len([p for p in text.split("\n\n") if p.strip()])


def _load_fixtures(name: str) -> list:
    """Load fixtures, tolerating a missing/partial file.

    A missing file yields [] so the suite imports cleanly and the
    size-assertion below reports the real problem (run
    ``python tools/gen_llm_fixtures.py``).
    """
    path = CORPUS / name
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):  # {"0": {...}, ...}
        return [data[k] for k in sorted(data, key=int)]
    return list(data)


class LlmSinglePassFixtureTest(unittest.TestCase):
    fixtures = _load_fixtures("llm_single_fixtures.json")

    def test_fixtures_match_corpus_size(self):
        self.assertEqual(len(self.fixtures), _ai_sample_count())

    def test_every_single_pass_rewrite_clears_floor(self):
        for i, fx in enumerate(self.fixtures):
            with self.subTest(sample=i):
                report = analyze(fx["text"])
                self.assertGreaterEqual(
                    report.score,
                    SINGLE_FLOOR,
                    f"single-pass LLM rewrite of sample {i} scored "
                    f"{report.score} — regenerate fixtures after prompt/LLM "
                    f"changes and re-check the floor",
                )


class LlmDeepFixtureTest(unittest.TestCase):
    fixtures = _load_fixtures("llm_deep_fixtures.json")

    def test_fixtures_match_corpus_size(self):
        self.assertEqual(len(self.fixtures), _ai_sample_count())

    def test_every_deep_rewrite_clears_floor(self):
        for i, fx in enumerate(self.fixtures):
            with self.subTest(sample=i):
                self.assertEqual(fx.get("method"), "chain", fx.get("error", ""))
                report = analyze(fx["text"])
                self.assertGreaterEqual(
                    report.score,
                    DEEP_FLOOR,
                    f"deep-chain rewrite of sample {i} scored {report.score} "
                    f"— regenerate fixtures after prompt/LLM changes and "
                    f"re-check the floor",
                )


if __name__ == "__main__":
    unittest.main()
