"""Regenerate the LLM corpus fixtures (real provider calls).

Records the actual single-pass and deep (translation-chain) LLM rewrites of
every AI-heavy corpus sample into ``tests/corpus/llm_*_fixtures.json``,
together with the post-rewrite naturalness score. The test suite then
asserts those recorded outputs clear the floor without ever calling the
network again.

Run from the project root (loads ``.env.local`` / ``.env`` for the
providers, exactly like the server does)::

    python tools/gen_llm_fixtures.py            # single-pass + deep (slow)
    python tools/gen_llm_fixtures.py --single   # single-pass only (fast)
    python tools/gen_llm_fixtures.py --deep     # deep chain only (~2.5 min/sample)

The floors in ``tests/test_llm_corpus.py`` (SINGLE_FLOOR / DEEP_FLOOR)
must stay <= the minimum score printed here.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from naturalizer.detectors import analyze  # noqa: E402
from naturalizer.engine import Naturalizer  # noqa: E402
from naturalizer.envfile import load_envfile  # noqa: E402


def _ai_samples() -> list:
    text = (ROOT / "tests" / "corpus" / "ai_samples.txt").read_text(encoding="utf-8")
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _write(path: Path, results: dict) -> None:
    path.write_text(
        json.dumps(results, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"wrote {path.name}: {len(results)} samples")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--single", action="store_true", help="single-pass only")
    ap.add_argument("--deep", action="store_true", help="deep chain only")
    args = ap.parse_args()

    load_envfile()  # entry point — the server/CLI behavior
    from naturalizer.llm import llm_available  # noqa: E402

    if not llm_available():
        print("No LLM provider configured — nothing to generate.", file=sys.stderr)
        raise SystemExit(1)

    blocks = _ai_samples()
    eng = Naturalizer(seed=0)
    corpus_dir = ROOT / "tests" / "corpus"
    start = time.time()

    if not args.deep:
        results = {}
        for i, b in enumerate(blocks):
            t0 = time.time()
            r = eng.naturalize(b, use_llm=True, deep=False)
            out = r.llm_rewritten or r.rewritten
            score = analyze(out).score
            results[i] = {"score": score, "text": out, "method": r.llm_method}
            print(f"single[{i}] score={score} ({time.time()-t0:.0f}s)")
        _write(corpus_dir / "llm_single_fixtures.json", results)
        print(f"single-pass done in {time.time()-start:.0f}s")

    if not args.single:
        results = {}
        for i, b in enumerate(blocks):
            t0 = time.time()
            try:
                r = eng.naturalize(b, use_llm=True, deep=True)
                out = r.llm_rewritten or r.rewritten
                score = analyze(out).score
                results[i] = {"score": score, "text": out, "method": r.llm_method}
                print(f"deep[{i}] score={score} method={r.llm_method} ({time.time()-t0:.0f}s)")
            except Exception as exc:  # pragma: no cover - live-provider edge
                results[i] = {"score": None, "text": "", "error": repr(exc)[:200]}
                print(f"deep[{i}] ERROR {exc!r}")
            _write(corpus_dir / "llm_deep_fixtures.json", results)
        print(f"deep done in {time.time()-start:.0f}s")

    print("\nDone. Check that the floors in tests/test_llm_corpus.py stay "
          "<= the minimum scores printed above.")


if __name__ == "__main__":
    main()
