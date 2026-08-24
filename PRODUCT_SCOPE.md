# Naturalizer Product Scope

Naturalizer is a **local-first writing-quality assistant**. Its purpose is to help people improve clarity, tone, structure, and readability while making every transformation reviewable. It may identify linguistic signals, explain possible issues, suggest edits, compare a draft with a rewritten version, and check supplied reference text for local overlap. It does not determine who authored a document, and it does not promise how any external service will classify a document.

The product must remain transparent about uncertainty. Scores are heuristic writing signals, not proof of authorship, originality, academic integrity, or compliance with an institution’s policy. Users remain responsible for reviewing meaning, citations, factual accuracy, and the rules that apply to their work.

| Approved capability | Boundary |
|---|---|
| Local rewriting | Improve clarity and natural phrasing without promising detector outcomes. |
| Detector analysis | Explain local linguistic signals with honest uncertainty and abstention. |
| Reviewable before/after output | Keep edits visible so users can inspect and accept them. |
| Local reference-overlap check | Compare only text the user supplies; do not imply access to commercial databases. |
| TXT/MD/DOCX/PDF text extraction | Best-effort text extraction only; no promise of full layout preservation. |
| Batch processing | Process user-provided documents locally; no live-chat automation. |
| Optional presentation or style controls | Adjust tone and readability, not authorship or detector outcomes. |

## Hard non-goals

Naturalizer must not provide tools designed to bypass Turnitin or other detection systems, guarantee undetectable output, or claim guaranteed detection evasion. It must not reverse-engineer StealthGPT, StealthWriter, or any other private commercial algorithm; reproduce proprietary output; scrape competitor interfaces to imitate their behavior; or expose third-party detector scores as an optimization target.

Naturalizer must not market a “perfect bypass,” “100% undetectable” result, or universal conversion of any AI text into human text across every context. It must not offer a research-scale collection of conversation algorithms, multi-language humanization without appropriate training data, cloud processing that contradicts the local-first promise, or real-time humanization for live chat. It must not normalize academic dishonesty or present rewriting as a way to conceal authorship.

Any future feature that affects authorship, academic work, external detector interaction, or cloud processing must be reviewed against this scope before implementation.
