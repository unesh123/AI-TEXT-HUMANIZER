# Naturalizer

Naturalizer is a **local-first writing-quality assistant** for improving clarity, tone, structure, and readability. It can analyze linguistic signals in a draft, suggest concrete edits, produce a reviewable rewrite, compare before and after text, check supplied reference passages for local overlap, and extract text from common document formats.

Naturalizer is not an authorship oracle. Detector scores are heuristic writing signals, not proof of human authorship, originality, academic integrity, or compliance with an institution’s policy. Users remain responsible for reviewing facts, citations, meaning, and the rules that apply to their work.

## Approved features

| Feature | What it does | Boundary |
|---|---|---|
| Local humanizer | Improves phrasing, clarity, rhythm, and tone | Does not promise any external detector outcome |
| Local detector | Reports explainable linguistic signals and confidence | Does not identify authorship with certainty |
| Before/after review | Shows the original and edited text with a word-level diff | Users review and approve every change |
| Style profiles | Supports Academic, Business, Creative, and Casual registers | Changes tone, not authorship or provenance |
| Reference-overlap check | Compares against text the user supplies | No commercial database or internet-wide originality claim |
| Document handling | Extracts text from TXT, MD, DOCX, and PDF | PDF layout and scanned-document fidelity are best-effort |
| Batch mode | Processes user-provided documents locally on Pro | Not a live-chat or autonomous publishing system |
| Saved history | Keeps local, reviewable run history | No hidden cloud account or remote document store |

## Hard non-goals

Naturalizer does not provide tools designed to bypass Turnitin or another detector, guarantee undetectable output, or promise guaranteed detection evasion. It does not reverse-engineer StealthGPT, StealthWriter, or any private commercial system; match proprietary output; scrape competitor products; or expose third-party detector scores as an optimization target.

It does not claim that AI text can be made 100% undetectable, convert any AI text into human text across every context, or establish human authorship. It does not include a research-scale library of conversation algorithms, unsupported multilingual humanization, cloud-based processing that contradicts its local-first promise, real-time humanization for live chat, or features that normalize academic dishonesty.

## Quick start

Requires Python 3.9 or newer. The runtime uses the Python standard library for the local application.

```bash
python server.py
# http://127.0.0.1:8000

# Optional custom port
PORT=9000 python server.py
```

On Windows, double-click `start.bat` to start the local server and open the browser.

## Command line

The command-line interface supports the same local extract, rewrite, and export workflow:

```bash
python -m naturalizer.cli draft.docx
python -m naturalizer.cli draft.pdf -f txt
python -m naturalizer.cli draft.docx -o clean.pdf
python -m naturalizer.cli draft.docx --json
```

Use `--style business`, `--no-llm`, and `--overwrite` as needed. Any output should be reviewed before it is submitted, published, or used in a consequential setting.

## Local API

| Endpoint | Purpose |
|---|---|
| `GET /api/status` | Returns local capabilities, styles, upload limits, and plan status |
| `POST /api/detect` | Analyzes local writing signals and returns a transparent heuristic result |
| `POST /api/naturalize` | Produces a local reviewable rewrite with score, issues, and before/after metrics |
| `POST /api/upload` | Extracts text from an uploaded local document and returns a reviewable result |
| `POST /api/plagiarism` | Checks overlap only against reference text supplied in the request |
| `POST /api/export` | Exports reviewed text as TXT, DOCX, or PDF |
| `GET /api/history` | Reads locally saved run history |
| `POST /api/history/clear` | Clears locally saved run history |
| `POST /api/history/delete` | Deletes one locally saved history entry |
| `GET /api/benchmark` | Runs the bundled local regression benchmark |

The following classes of endpoints are intentionally unavailable: external detector scoring, competitor-provider comparison, feedback loops aimed at detector outcomes, deep translation-chain processing, cloud model routing, and real-time streaming humanization.

## Responsible usage

Use Naturalizer to make your own writing clearer, more readable, and easier to review. Do not use it to conceal authorship, misrepresent work, evade institutional review, or submit material that you have not checked for factual accuracy and policy compliance. When a detector result matters, treat it as one imperfect signal among many and follow the relevant institution’s guidance.

## Testing

Run the standard-library test suite with:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

The repository contains historical detector and rewrite fixtures. Those fixtures are regression data only; they do not expand the approved product scope or authorize external-provider integrations.

## License and contribution boundary

Contributions should preserve the local-first architecture and the hard non-goals in [`PRODUCT_SCOPE.md`](PRODUCT_SCOPE.md). New functionality involving authorship claims, external detector optimization, private commercial reverse-engineering, unsupported language generation, or cloud processing requires explicit scope review before implementation.
