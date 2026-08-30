"""StealthGPT API integration for naturalizer."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

DEFAULT_BASE_URL = "https://www.stealthgpt.ai/api/stealthify"
DEFAULT_MODEL = "heavy"
DEFAULT_TONE = "Standard"

STYLE_TO_TONE = {
    "academic": "College",
    "business": "Standard",
    "casual": "HighSchool",
    "technical": "PhD",
    "creative": "Standard",
    "clear": "Standard",
}


def get_api_key() -> Optional[str]:
    for k in ("STEALTHGPT_API_KEY", "stealthgpt_ai_api_key", "STEALTH_KEY", "stealthgpt_api_key"):
        v = os.environ.get(k)
        if v and v.strip():
            return v.strip()
    return None


def is_configured() -> bool:
    return bool(get_api_key())


def get_base_url() -> str:
    raw = (os.environ.get("STEALTHGPT_BASE_URL") or DEFAULT_BASE_URL).strip()
    if raw.startswith("https://stealthgpt.ai/"):
        raw = raw.replace("https://stealthgpt.ai/", "https://www.stealthgpt.ai/")
    elif raw.startswith("http://stealthgpt.ai/"):
        raw = raw.replace("http://stealthgpt.ai/", "https://www.stealthgpt.ai/")
    return raw


def get_model() -> str:
    m = (os.environ.get("STEALTHGPT_MODEL") or DEFAULT_MODEL).strip().lower()
    return m if m in {"heavy", "lite"} else "heavy"


def stealthify(
    text: str,
    style: str = "academic",
    model: Optional[str] = None,
    tone: Optional[str] = None,
    timeout: int = 35,
) -> Tuple[Optional[str], Dict[str, Any], Optional[str]]:
    token = get_api_key()
    if not token:
        return None, {}, "StealthGPT API key not configured in .env.local"
    text = (text or "").strip()
    if not text:
        return "", {}, None
    selected_model = model or get_model()
    selected_tone = tone or STYLE_TO_TONE.get(style.lower(), DEFAULT_TONE)
    url = get_base_url()
    payload = {
        "prompt": text,
        "rephrase": True,
        "tone": selected_tone,
        "model": selected_model,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "api-token": token,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                rewritten = data.get("result") or ""
                metadata = {
                    "provider": "stealthgpt",
                    "model": selected_model,
                    "tone": selected_tone,
                    "howLikelyToBeDetected": data.get("howLikelyToBeDetected"),
                    "wordsSpent": data.get("wordsSpent"),
                    "remainingCredits": data.get("remainingCredits"),
                    "billingMode": data.get("billingMode"),
                }
                return rewritten, metadata, None
            return None, {}, f"Unexpected response format from StealthGPT: {raw[:200]}"
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        try:
            err_json = json.loads(err_body)
            msg = err_json.get("message") or err_json.get("error") or err_body
        except Exception:
            msg = err_body or e.reason
        return None, {}, f"StealthGPT API error ({e.code}): {msg}"
    except urllib.error.URLError as e:
        return None, {}, f"StealthGPT network error: {e.reason}"
    except Exception as e:
        return None, {}, f"StealthGPT request failed: {e}"
