"""Command-line document naturalizer (pure stdlib).

Runs the same upload pipeline as the web UI from the terminal: extract text
from a TXT/DOCX/PDF file, naturalize it, and write the rewritten file back
out in your choice of format.

    python -m naturalizer.cli draft.docx                     # -> draft-naturalized.docx
    python -m naturalizer.cli draft.pdf -f txt               # -> draft-naturalized.txt
    python -m naturalizer.cli draft.pdf -o clean.docx        # explicit output name
    python -m naturalizer.cli a.txt b.docx                   # one output per input
    python -m naturalizer.cli draft.docx --json              # also print full result JSON

Exit codes: 0 = success, 1 = one or more files failed, 2 = bad usage.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .engine import Naturalizer
from .export import EXPORT_FORMATS, to_bytes
from .extract import ExtractionError, detect_format, extract_text

_PDF_WARNING = (
    "PDF text extraction is best-effort: reading order, layout, and non-Latin "
    "fonts may be imperfect, and scanned/image-only PDFs contain no extractable text."
)


def _default_output(input_path: str, fmt: str) -> Path:
    path = Path(input_path)
    return path.with_name(f"{path.stem}-naturalized.{fmt}")


def _parse(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="naturalizer",
        description="Extract text from TXT/DOCX/PDF files, naturalize it, and "
        "write the rewritten file back out.",
    )
    parser.add_argument("inputs", nargs="+", help="input file(s): .txt, .docx, or .pdf")
    parser.add_argument(
        "-o", "--output",
        help="output path (single input only; defaults to <stem>-naturalized.<format>)",
    )
    parser.add_argument(
        "-f", "--format", choices=list(EXPORT_FORMATS),
        help="output format (defaults to the input's format)",
    )
    parser.add_argument("--style", default="academic", help="style profile (default: academic)")
    parser.add_argument("--no-llm", action="store_true", help="never use the LLM backend")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="use the translation chain for maximum humanization",
    )
    parser.add_argument(
        "--chain-mode",
        choices=["standard", "extended", "multi", "hybrid", "best"],
        default="standard",
        help="chain strategy when --deep is used: standard (4-hop), extended (6-hop), multi (all providers), hybrid (chain+providers), best (try all) (default: standard)",
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "claude", "cx", "openai", "gemini", "qwen", "router", "codex", "hcns"],
        default="auto",
        help="LLM provider for the rewrite: auto (first configured), or a specific provider "
        "(claude | cx | openai | gemini | qwen | router | codex | hcns) (default: auto)",
    )
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing output files")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON on stdout")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse(argv)

    if len(args.inputs) > 1 and args.output:
        print("--output can only be used with a single input", file=sys.stderr)
        return 2

    engine = Naturalizer(seed=0)
    use_llm = False if args.no_llm else None
    ok = True
    deep = args.deep

    for input_path in args.inputs:
        try:
            data = Path(input_path).read_bytes()
            original, in_fmt = extract_text(data, input_path)
        except (OSError, ExtractionError) as exc:
            print(f"{input_path}: {exc}", file=sys.stderr)
            ok = False
            continue

        fmt = args.format
        if fmt is None:
            # An explicit output extension drives the format when -f is absent.
            if args.output and len(args.inputs) == 1:
                ext = Path(args.output).suffix.lower().lstrip(".")
                if ext in EXPORT_FORMATS:
                    fmt = ext
            if fmt is None:
                fmt = in_fmt
        if args.output and len(args.inputs) == 1:
            out_path = Path(args.output)
        else:
            out_path = _default_output(input_path, fmt)

        if out_path.exists() and not args.overwrite:
            print(f"{out_path}: already exists (use --overwrite to replace it)", file=sys.stderr)
            ok = False
            continue

        result = engine.naturalize(
            original, style=args.style, use_llm=use_llm, deep=deep, chain_mode=args.chain_mode, provider=args.provider
        )
        rewritten = result.llm_rewritten if result.llm_used else result.rewritten
        out_path.write_bytes(to_bytes(rewritten, fmt))

        if args.json:
            payload = result.to_dict()
            payload["input"] = str(input_path)
            payload["output"] = str(out_path)
            payload["format"] = fmt
            payload["warnings"] = [_PDF_WARNING] if in_fmt == "pdf" else []
            print(json.dumps(payload))
        else:
            llm_note = " (llm rewrite)" if result.llm_used else ""
            print(
                f"{input_path}: naturalness {result.score}/100, "
                f"{len(result.issues)} issue(s) -> {out_path}{llm_note}",
                file=sys.stderr,
            )
            if in_fmt == "pdf":
                print(f"  note: {_PDF_WARNING}", file=sys.stderr)

    return 0 if ok else 1


if __name__ == "__main__":
    # Only the real CLI entry point pulls credentials from .env.local / .env,
    # so in-process callers (e.g. tests) stay hermetic and deterministic.
    from .envfile import load_envfile

    load_envfile()
    sys.exit(main())
