"""Live third-party AI-detector clients — GPTZero and ZeroGPT.

These are the actual HTTP calls behind the 🔌 Detectors panel. Each detector
is a thin ``urllib`` client (no dependencies, matching the rest of the
project) that sends the text and parses the provider's own AI probability
into a 0-100 score, plus a human-readable verdict.

Provider contracts (verified against their current public docs):

* **GPTZero** — ``POST https://api.gptzero.me/v2/predict/text`` with the
  ``x-api-key`` header and ``{"document": text}``. The response's
  ``documents[0].completely_generated_prob`` (0-1) is the AI probability;
  ``document_classification`` is ``HUMAN_ONLY`` / ``MIXED`` / ``AI_ONLY``.
* **ZeroGPT** — ``POST https://api.zerogpt.com/api/detect/detectText``
  with ``{"input_text": text}``. The response's ``data.fakePercentage``
  (0-100) is the AI probability. (ZeroGPT's free web tool uses this same
  endpoint; keys are accepted via the ``X-API-KEY`` header when present.)

Keys come from the environment: ``GPTZERO_API_KEY`` and
``ZEROGPT_API_KEY`` (loaded from ``.env.local`` by the entry points).
Everything is defensive: a missing key, network error, or unexpected
response shape turns into a ``{"error": ...}`` row — never a crash, and
the UI shows exactly which detector failed and why.

All HTTP is ``urllib`` so the test suite can mock ``urllib.request.urlopen``
exactly like the LLM tests do — no real keys, no real network.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Dict, List, Optional

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_TIMEOUT = 30

#: Name -> env var that activates the live client.
LIVE_DETECTORS: List[Dict[str, str]] = [
    {"name": "gptzero", "label": "GPTZero", "key": "GPTZERO_API_KEY"},
    {"name": "zerogpt", "label": "ZeroGPT", "key": "ZEROGPT_API_KEY"},
]


def _ai_percent(prob_0_1) -> Optional[int]:
    """Normalize a 0-1 AI probability to an int 0-100 (None if unusable)."""
    if prob_0_1 is None:
        return None
    try:
        value = float(prob_0_1)
    except (TypeError, ValueError):
        return None
    return int(round(max(0.0, min(1.0, value)) * 100))


def _verdict(percent: Optional[int]) -> str:
    """Human-readable verdict from a 0-100 AI score."""
    if percent is None:
        return "unknown"
    if percent >= 75:
        return "AI"
    if percent >= 50:
        return "likely AI"
    if percent >= 25:
        return "mixed"
    return "human"


def _post_json(url: str, payload: dict, headers: dict) -> Optional[dict]:
    """POST JSON, return parsed body or None on any network failure."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
            **headers,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def check_gptzero(text: str, api_key: Optional[str] = None) -> Dict[str, object]:
    """Live GPTZero scan. Returns ``{name, label, score, verdict, error}``."""
    key = api_key or os.environ.get("GPTZERO_API_KEY")
    if not key:
        return {
            "name": "gptzero", "label": "GPTZero",
            "score": None, "verdict": None,
            "error": "GPTZERO_API_KEY missing — see README to get a key",
        }
    try:
        body = _post_json(
            "https://api.gptzero.me/v2/predict/text",
            {"document": text},
            {"x-api-key": key},
        )
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as exc:
        return {
            "name": "gptzero", "label": "GPTZero",
            "score": None, "verdict": None,
            "error": f"GPTZero request failed: {exc}",
        }
    try:
        doc = body["documents"][0]
        percent = _ai_percent(doc.get("completely_generated_prob"))
    except (KeyError, IndexError, TypeError):
        # Fall back to class probabilities if the primary field is absent.
        try:
            probs = body.get("class_probabilities") or {}
            percent = _ai_percent(probs.get("ai"))
        except (KeyError, TypeError, ValueError):
            percent = None
    if percent is None:
        return {
            "name": "gptzero", "label": "GPTZero",
            "score": None, "verdict": None,
            "error": "GPTZero response had no AI probability (check quota?)",
        }
    return {
        "name": "gptzero", "label": "GPTZero",
        "score": percent, "verdict": _verdict(percent), "error": None,
    }


def check_zerogpt(text: str, api_key: Optional[str] = None) -> Dict[str, object]:
    """Live ZeroGPT scan. Returns ``{name, label, score, verdict, error}``."""
    key = api_key or os.environ.get("ZEROGPT_API_KEY")
    headers = {"X-API-KEY": key} if key else {}
    try:
        body = _post_json(
            "https://api.zerogpt.com/api/detect/detectText",
            {"input_text": text},
            headers,
        )
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as exc:
        return {
            "name": "zerogpt", "label": "ZeroGPT",
            "score": None, "verdict": None,
            "error": f"ZeroGPT request failed: {exc}",
        }
    try:
        raw = body["data"]["fakePercentage"]
        percent = int(round(float(str(raw).replace("%", "").strip())))
    except (KeyError, IndexError, TypeError, ValueError):
        # ZeroGPT's public endpoint now 403s without a paid account — surface
        # the provider's own message ("Please make a purchase…") so the UI
        # isn't left guessing about quotas.
        provider_msg = body.get("message") if isinstance(body, dict) else None
        error = (
            f"ZeroGPT response had no fakePercentage{': ' + provider_msg if provider_msg else ''} "
            "(the API now requires a paid account; add ZEROGPT_API_KEY or use GPTZero)"
        )
        return {
            "name": "zerogpt", "label": "ZeroGPT",
            "score": None, "verdict": None,
            "error": error,
        }
    return {
        "name": "zerogpt", "label": "ZeroGPT",
        "score": max(0, min(100, percent)), "verdict": _verdict(percent), "error": None,
    }


#: name -> client function.
_CHECKERS = {
    "gptzero": check_gptzero,
    "zerogpt": check_zerogpt,
}


def scan_live(text: str) -> List[Dict[str, object]]:
    """Run every configured live detector against *text*.

    Returns one result dict per *configured* detector (missing keys are
    reported as errors so the UI can show the add-a-key hint). Detectors
    without a key in the environment are skipped entirely — the panel's
    status rows already explain how to enable them.
    """
    results: List[Dict[str, object]] = []
    for det in LIVE_DETECTORS:
        if not os.environ.get(det["key"]):
            continue
        checker = _CHECKERS[det["name"]]
        results.append(checker(text))
    return results
