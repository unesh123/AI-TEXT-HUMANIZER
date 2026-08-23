"""Naturalizer web server (zero-dependency).

Serves the web UI and a small JSON API:

    GET  /                     -> web UI (index.html)
    GET  /static/style.css     -> stylesheet
    POST /api/naturalize       -> {text, style?, use_llm?} -> result JSON
    POST /api/naturalize/stream-> {text, style?, use_llm?} -> SSE stream of
                                  status/delta/done events (word-by-word rewrite)
    POST /api/batch            -> {texts: [...], style?, use_llm?} -> results JSON
    POST /api/upload           -> multipart {file, style?, use_llm?} -> result JSON,
                                  or ?format=txt|docx|pdf to download the rewrite
    POST /api/export           -> {text, format} -> rewritten file download
    POST /api/detect           -> {text, style?} -> detector report (per-sentence
                                  labels, AI/mixed/human distribution, verdict)
    POST /api/plagiarism       -> {text, refs: [...]} -> similarity report
    POST /api/perfect          -> {text, style?, intensity?, seed?, provider?}
                               -> feedback-loop humanize {text, passes, scores, ...}
    POST /api/compare          -> {text, style?} -> runs the same input through every
                               configured humanizer (own engines + key-gated external
                               APIs), ranks them, returns {best, candidates, blocked}
    GET  /api/detectors        -> configured status of every detector (local, GPTZero, ...)
    GET  /api/history          -> saved history entries (input + rewrite + scores)
    POST /api/history/delete   -> {id} -> remove one entry
    POST /api/history/clear    -> wipe all history
    GET  /api/status           -> {version, styles, llm_configured, uploads}

Run:

    python server.py            # http://127.0.0.1:8000
    PORT=9000 python server.py  # custom port
"""

from __future__ import annotations

import json
import os
import queue
import re
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from naturalizer import STYLES, STYLE_NAMES, __version__
from naturalizer.engine import Naturalizer
from naturalizer.export import EXPORT_FORMATS, content_type, to_bytes
from naturalizer.extract import ExtractionError, detect_format, extract_text
from naturalizer.plans import (
    check_word_quota,
    current_plan,
    plan_features,
    record_usage,
    status as plan_status,
)

ROOT = Path(__file__).resolve().parent
INDEX_HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

engine = Naturalizer(seed=0)
_STARTED_AT = time.time()
_INVALID_JSON = object()

DEFAULT_MAX_UPLOAD = 10 * 1024 * 1024  # 10 MiB
DEFAULT_MAX_JSON = 2 * 1024 * 1024  # 2 MiB
DEFAULT_RATE_LIMIT = 120  # requests / min / IP on expensive endpoints

# Endpoints that consume real work (CPU, LLM tokens, disk) and get rate-limited.
_RATE_LIMITED_PATHS = {
    "/api/naturalize",
    "/api/naturalize/stream",
    "/api/perfect",
    "/api/upload",
    "/api/batch",
    "/api/plagiarism",
    "/api/detectors/scan",
    "/api/detect",
    "/api/compare",
}

# Sliding-window request log: client IP -> [timestamps].
_RATE_HITS: dict = {}
_RATE_LOCK = threading.Lock()


def _max_upload() -> int:
    """Upload size cap, overridable via MAX_UPLOAD_BYTES (handy for tests)."""
    return int(os.environ.get("MAX_UPLOAD_BYTES", str(DEFAULT_MAX_UPLOAD)))


def _max_json() -> int:
    """JSON request-body cap; 0 disables the limit."""
    return int(os.environ.get("MAX_JSON_BYTES", str(DEFAULT_MAX_JSON)))


def _rate_limit_per_min() -> int:
    """Per-IP request budget on expensive endpoints; 0 disables."""
    try:
        return int(os.environ.get("RATE_LIMIT_PER_MIN", str(DEFAULT_RATE_LIMIT)))
    except ValueError:
        return DEFAULT_RATE_LIMIT


def _allowed_origins() -> set:
    """Explicitly allowed cross-origin callers (beyond same-origin/loopback)."""
    return {
        o.strip().rstrip("/")
        for o in os.environ.get("ALLOWED_ORIGINS", "").split(",")
        if o.strip()
    }


def _loopback(host: str) -> bool:
    """Treat 127.0.0.1 / localhost / [::1] as one family (any port)."""
    host = host.strip().lower().rstrip(".")
    host = host.split(":")[0].strip("[]")
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0", "::")


def _security_headers() -> dict:
    """Baseline hardening headers applied to every response."""
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
    }


def _csp_policy(nonce: str) -> str:
    """Content-Security-Policy for the HTML shell (single inline script, nonce-gated)."""
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )


def _parse_multipart(raw: bytes, boundary: bytes):
    """Yield ``(name, filename, content_type, data)`` for each form part."""
    delimiter = b"--" + boundary
    for chunk in raw.split(delimiter):
        if not chunk or chunk in (b"\r\n", b"--", b"--\r\n"):
            continue
        if chunk.startswith(b"--"):  # closing delimiter
            continue
        chunk = chunk[2:] if chunk.startswith(b"\r\n") else chunk
        header_blob, sep, body = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        if body.endswith(b"\r\n"):  # CRLF before the boundary belongs to it
            body = body[:-2]
        headers = {}
        for line in header_blob.split(b"\r\n"):
            if b":" not in line:
                continue
            key, _, value = line.partition(b":")
            headers[key.strip().lower().decode("latin-1")] = value.strip().decode("latin-1")
        disposition = headers.get("content-disposition", "")
        name = filename = None
        m = re.search(r'name="([^"]*)"', disposition)
        if m:
            name = m.group(1)
        m = re.search(r'filename="([^"]*)"', disposition)
        if m:
            filename = m.group(1)
        yield name, filename, headers.get("content-type"), body


def _safe_stem(filename: str) -> str:
    """A filesystem- and header-safe stem for download names."""
    stem = Path(filename or "document").stem
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    return clean or "document"


def _normalize_style(value):
    """Normalize a style label while preserving strict validation."""
    if not isinstance(value, str):
        return None
    style = value.strip().lower()
    return style if style in STYLES else None


def _normalize_provider(value) -> str:
    """Validate a provider choice from a request."""
    value = (value or "auto").lower().strip()
    try:
        from naturalizer.llm import PROVIDER_NAMES

        return value if value in PROVIDER_NAMES else "auto"
    except Exception:  # pragma: no cover - defensive
        return value if value in ("auto", "claude", "cx") else "auto"


def _normalize_intensity(value, cap: float = 1.0) -> float:
    """Clamp an intensity value to 0..1, then to the plan's ceiling."""
    try:
        intensity = float(value)
    except (TypeError, ValueError):
        intensity = 0.5
    return max(0.0, min(cap, intensity))


def _normalize_rewrite_mode(value) -> str:
    """Validate the document rewrite approach requested by the UI."""
    mode = str(value or "full").strip().lower()
    return mode if mode in {"light", "standard", "full"} else "full"


def _normalize_seed(value) -> int:
    """Non-negative seed for deterministic rewrite variety (default 0)."""
    try:
        seed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, seed)


def _apply_generated_provenance(result: dict, text: str) -> dict:
    """Mark an exact prior app-generated output as AI-derived.

    The local linguistic score remains available, but provenance takes
    precedence when the same application already knows it generated the text.
    """
    try:
        from naturalizer.history import find_generated_output

        entry = find_generated_output(text)
    except Exception:  # pragma: no cover - history must never break detection
        entry = None
    if not entry:
        return result
    marked = dict(result)
    sentences = []
    for sentence in result.get("sentences") or []:
        item = dict(sentence)
        item["label"] = "ai"
        issues = list(item.get("issues") or [])
        if "app-generated lineage" not in issues:
            issues.append("app-generated lineage")
        item["issues"] = issues
        sentences.append(item)
    marked.update({
        "verdict": "ai_derived",
        "confidence": 99,
        "distribution": {"ai": 100, "mix": 0, "human": 0},
        "sentences": sentences,
        "regions": ([{
            "start": 0,
            "end": len(sentences) - 1,
            "count": len(sentences),
            "text": " ".join(s.get("sentence", "") for s in sentences),
        }] if sentences else []),
        "provenance": {
            "matched": True,
            "mode": entry.get("mode", "naturalize"),
            "created_at": entry.get("iso"),
            "message": "This exact text matches a rewrite previously generated by this application.",
        },
    })
    return marked


def _save_history(
    result,
    text: str,
    style: str,
    mode: str,
    provider: str,
    intensity: float,
    seed: int = 0,
    extra: dict = None,
) -> None:
    """Persist one history entry for a completed rewrite run."""
    try:
        from naturalizer.history import save as history_save

        # NaturalizeResult (engine) vs result dict (perfect / stream to_dict)
        # vs feedback dict (perfect feedback_humanize) — normalize each.
        if isinstance(result, dict):
            if "text" in result:  # feedback_humanize result
                output = result.get("text", "")
                scores = result.get("scores") or []
                score = scores[-1] if scores else 0
                llm_used = bool(result.get("llm_used"))
                extra = {**(extra or {}), "passes": result.get("passes")}
            else:  # NaturalizeResult.to_dict()
                output = (
                    result.get("llm_rewritten") or result.get("rewritten") or ""
                )
                score = result.get("score", 0)
                llm_used = bool(result.get("llm_used"))
        else:
            output = result.llm_rewritten if result.llm_used else result.rewritten
            score = result.score
            llm_used = result.llm_used
        history_save(
            text,
            output,
            score,
            style=style,
            mode=mode,
            provider=provider,
            llm_used=llm_used,
            intensity=intensity,
            plan=current_plan(),
            extra=extra,
        )
    except Exception:  # pragma: no cover - history must never break a run
        pass


def _load_body(handler: BaseHTTPRequestHandler):
    """Read + parse a JSON body. Returns ``None`` when the body exceeds
    ``MAX_JSON_BYTES`` (caller replies 413) and ``{}`` on parse failure.

    A body that is too large is *drained* (read and discarded) before the
    caller replies 413: closing the socket while the client is still
    uploading resets the connection, which makes clients see a network
    error instead of the clean 413."""
    length = int(handler.headers.get("Content-Length", 0))
    cap = _max_json()
    if cap and length > cap:
        if length:
            try:
                handler.rfile.read(length)
            except OSError:  # pragma: no cover - client vanished mid-upload
                pass
        return None
    raw = handler.rfile.read(length) if length else b"{}"
    if cap and len(raw) > cap:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return _INVALID_JSON


class Handler(BaseHTTPRequestHandler):
    server_version = "Naturalizer/" + __version__

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):  # quieter logs
        print(f"[{self.log_date_time_string()}] {self.address_string()} " + fmt % args)

    def _origin_allowed(self) -> bool:
        """True when a request's Origin is same-origin/loopback or allow-listed."""
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if not origin:
            return True  # curl, same-origin GETs, health checks
        if origin in _allowed_origins():
            return True
        try:
            scheme, _, hostport = origin.partition("://")
            if scheme not in ("http", "https") or not hostport:
                return False
            host = hostport.split("/")[0]
        except Exception:
            return False
        req_host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
        if _loopback(req_host) and _loopback(host):
            return True  # localhost <-> 127.0.0.1 <-> [::1] family
        return host.lower().rstrip(".") == req_host.rstrip(".")

    def _common_headers(self) -> dict:
        """Security headers + conditional CORS for the current request."""
        headers = _security_headers()
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if origin and self._origin_allowed():
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
        return headers

    def _send_headers(self, status: int, headers: dict) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        headers = self._common_headers()
        headers["Content-Type"] = "application/json; charset=utf-8"
        headers["Content-Length"] = str(len(body))
        headers["Cache-Control"] = "no-store"
        self._send_headers(status, headers)
        self.wfile.write(body)

    def _send_index(self) -> None:
        """Serve the HTML shell with a per-request CSP nonce on the inline script."""
        nonce = secrets.token_hex(16)
        body = INDEX_HTML.replace("__CSP_NONCE__", nonce).encode("utf-8")
        headers = _security_headers()
        headers["Content-Type"] = "text/html; charset=utf-8"
        headers["Content-Length"] = str(len(body))
        headers["Cache-Control"] = "no-cache"
        headers["Content-Security-Policy"] = _csp_policy(nonce)
        self._send_headers(200, headers)
        self.wfile.write(body)

    def _send_text(self, status: int, text: str, ctype: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        headers = self._common_headers()
        headers["Content-Type"] = ctype
        headers["Content-Length"] = str(len(body))
        headers["Cache-Control"] = "no-cache"
        self._send_headers(status, headers)
        self.wfile.write(body)

    def _send_css(self, body: bytes) -> None:
        headers = self._common_headers()
        headers["Content-Type"] = "text/css; charset=utf-8"
        headers["Content-Length"] = str(len(body))
        headers["Cache-Control"] = "public, max-age=86400"
        self._send_headers(200, headers)
        self.wfile.write(body)

    def _send_file(self, body: bytes, fmt: str, filename: str) -> None:
        headers = self._common_headers()
        headers["Content-Type"] = content_type(fmt)
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        headers["Content-Length"] = str(len(body))
        headers["Cache-Control"] = "no-store"
        self._send_headers(200, headers)
        self.wfile.write(body)

    # -- routes -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        try:
            self._route_get()
        except Exception:  # pragma: no cover - defensive: never drop the connection
            traceback.print_exc()
            try:
                self._send_json(500, {"error": "internal server error"})
            except Exception:
                pass

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._route_post()
        except Exception:  # pragma: no cover - defensive: never drop the connection
            traceback.print_exc()
            try:
                self._send_json(500, {"error": "internal server error"})
            except Exception:
                pass

    def do_OPTIONS(self) -> None:  # noqa: N802 (CORS preflight)
        if not self._origin_allowed():
            self._send_json(403, {"error": "origin not allowed"})
            return
        origin = (self.headers.get("Origin") or "").rstrip("/")
        headers = {
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Max-Age": "86400",
            "Content-Length": "0",
        }
        if origin:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
        self._send_headers(204, headers)

    def _route_get(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_index()
        elif path == "/robots.txt":
            self._send_text(200, "User-agent: *\nDisallow: /api/\n")
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif path == "/static/style.css":
            self._send_css(STYLE_CSS.encode("utf-8"))
        elif path == "/api/status":
            self._send_json(200, self._status())
        elif path in ("/api/health", "/healthz"):
            state_dir = os.environ.get("NATURALIZER_STATE_DIR", str(ROOT / "state"))
            self._send_json(200, {
                "status": "ok",
                "service": "naturalizer",
                "version": __version__,
                "uptime_seconds": round(max(0.0, time.time() - _STARTED_AT), 3),
                "checks": {
                    "engine": True,
                    "state_directory": os.path.isdir(state_dir) or os.access(os.path.dirname(state_dir) or ".", os.W_OK),
                },
            })
        elif path == "/api/benchmark":
            from naturalizer.benchmark import run_benchmark

            self._send_json(200, run_benchmark().to_dict())
        elif path == "/api/detectors":
            from naturalizer.feedback import detector_status

            self._send_json(200, {"detectors": detector_status()})
        elif path == "/api/history":
            from naturalizer.history import list_entries, public

            limit = None
            try:
                limit = int(parse_qs(urlparse(self.path).query).get("limit", ["50"])[0])
            except ValueError:
                pass
            entries = [public(e) for e in list_entries(limit or 50)]
            self._send_json(200, {"entries": entries, "count": len(entries)})
        else:
            self._send_json(404, {"error": "not found"})

    def _rate_limited(self, path: str) -> bool:
        """Sliding-window per-IP budget for expensive endpoints."""
        budget = _rate_limit_per_min()
        if budget <= 0 or path not in _RATE_LIMITED_PATHS:
            return False
        now = time.time()
        key = self.client_address[0]
        with _RATE_LOCK:
            hits = _RATE_HITS.setdefault(key, [])
            while hits and hits[0] < now - 60:
                hits.pop(0)
            if len(hits) >= budget:
                return True
            hits.append(now)
            if len(_RATE_HITS) > 4096:  # bound memory for many unique IPs
                for k in [k for k, v in _RATE_HITS.items() if not v]:
                    _RATE_HITS.pop(k, None)
        return False

    def _route_post(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        # Cross-origin state changes: only same-origin/loopback or allow-listed.
        if not self._origin_allowed():
            self._send_json(403, {"error": "origin not allowed"})
            return
        if self._rate_limited(path):
            self._send_json(429, {
                "error": "Too many requests — slow down and try again in a minute.",
                "retry_after": 60,
            })
            return

        if path == "/api/upload":
            self._handle_upload(parsed)
            return
        if path == "/api/naturalize/stream":
            data = _load_body(self)
            if data is None:
                self._send_json(413, {"error": "request body too large"})
                return
            if data is _INVALID_JSON:
                self._send_json(400, {"error": "invalid JSON body"})
                return
            self._handle_stream(data)
            return

        data = _load_body(self)
        if data is None:
            self._send_json(413, {"error": "request body too large"})
            return
        if data is _INVALID_JSON:
            self._send_json(400, {"error": "invalid JSON body"})
            return

        if path == "/api/history/clear":
            from naturalizer.history import clear as clear_history

            self._send_json(200, {"cleared": clear_history()})
            return
        if path == "/api/history/delete":
            entry_id = data.get("id", "")
            if not isinstance(entry_id, str) or not entry_id:
                self._send_json(400, {"error": "missing 'id'"})
                return
            from naturalizer.history import remove as remove_entry

            if not remove_entry(entry_id):
                self._send_json(404, {"error": "entry not found"})
                return
            self._send_json(200, {"deleted": True})
            return
        if path == "/api/detectors/scan":
            text = data.get("text", "")
            if not isinstance(text, str) or not text.strip():
                self._send_json(400, {"error": "missing or empty 'text'"})
                return
            from naturalizer.feedback import scan_live

            self._send_json(200, {"results": scan_live(text)})
            return
        if path == "/api/import-url":
            url = (data.get("url") or "").strip()
            if not url or not url.startswith(("http://", "https://")):
                self._send_json(400, {"error": "Provide a valid http/https URL."})
                return
            try:
                import urllib.request as _ureq
                import html as _html
                import re as _re
                req = _ureq.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; Naturalizer/1.0)",
                        "Accept": "text/html,application/xhtml+xml,*/*",
                    },
                )
                with _ureq.urlopen(req, timeout=10) as resp:
                    raw = resp.read(1_000_000).decode("utf-8", errors="replace")
                # Remove script/style blocks
                raw = _re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", raw)
                # Remove HTML tags
                text = _re.sub(r"<[^>]+>", " ", raw)
                # Decode HTML entities
                text = _html.unescape(text)
                # Collapse whitespace
                text = _re.sub(r"[ \t]+", " ", text)
                text = _re.sub(r"\n{3,}", "\n\n", text)
                # Trim long lines (navigation junk) - keep only paragraphs with >= 40 chars
                lines = [l.strip() for l in text.splitlines()]
                lines = [l for l in lines if len(l) >= 40]
                text = "\n\n".join(lines).strip()
                if not text:
                    self._send_json(422, {"error": "No readable text found at that URL. Try a different page."})
                    return
                # Trim to 10,000 chars to avoid huge payloads
                if len(text) > 10000:
                    text = text[:10000] + "\n\n[... text truncated at 10,000 chars ...]"
                self._send_json(200, {"text": text, "url": url, "chars": len(text)})
            except Exception as exc:
                self._send_json(502, {"error": f"Could not fetch URL: {exc}"})
            return
        if path == "/api/export":
            self._handle_export(data)
        elif path == "/api/plagiarism":
            from naturalizer.plagiarism import check as check_plagiarism

            text = data.get("text", "")
            refs = data.get("refs", [])
            if not isinstance(text, str) or not text.strip():
                self._send_json(400, {"error": "missing or empty 'text'"})
                return
            if not isinstance(refs, list):
                self._send_json(400, {"error": "'refs' must be a list of strings"})
                return
            self._send_json(200, check_plagiarism(text, refs).to_dict())
        elif path == "/api/detect":
            text = data.get("text", "")
            if not isinstance(text, str) or not text.strip():
                self._send_json(400, {"error": "missing or empty 'text'"})
                return
            style_raw = data.get("style", "academic")
            style = _normalize_style(style_raw)
            if style is None:
                self._send_json(400, {"error": f"unknown style '{style_raw}'", "styles": STYLE_NAMES})
                return
            detected = engine.detect(text, style=style)
            self._send_json(200, _apply_generated_provenance(detected, text))
        elif path == "/api/perfect":
            text = data.get("text", "")
            if not isinstance(text, str) or not text.strip():
                self._send_json(400, {"error": "missing or empty 'text'"})
                return
            style_raw = data.get("style", "academic")
            style = _normalize_style(style_raw)
            if style is None:
                self._send_json(400, {"error": f"unknown style '{style_raw}'", "styles": STYLE_NAMES})
                return
            features = plan_features()
            if not features["llm"]:
                # The feedback loop runs on the deterministic engine too, but
                # it converges far better with an LLM — gate like the LLM path.
                self._send_json(402, {
                    "error": "Perfect humanize (feedback loop) needs an LLM provider — "
                    "it's a Pro feature; set NATURALIZER_PLAN=pro or configure "
                    "an LLM in .env.local.",
                    "plan": plan_status(),
                })
                return
            allowed, quota_err = check_word_quota(len(text.split()))
            if not allowed:
                self._send_json(429, {"error": quota_err, "plan": plan_status()})
                return
            from naturalizer.feedback import feedback_humanize

            provider = _normalize_provider(data.get("provider"))
            intensity = _normalize_intensity(data.get("intensity"))
            seed = _normalize_seed(data.get("seed"))
            result = feedback_humanize(
                engine,
                text,
                style=style,
                intensity=intensity,
                seed=seed,
                provider=provider,
            )
            record_usage(len(text.split()))
            _save_history(
                result, text, style, "perfect", provider, intensity,
                seed=seed, extra={"passes": result.get("passes")},
            )
            self._send_json(200, result)
        elif path == "/api/compare":
            text = data.get("text", "")
            if not isinstance(text, str) or not text.strip():
                self._send_json(400, {"error": "missing or empty 'text'"})
                return
            style_raw = data.get("style", "academic")
            style = _normalize_style(style_raw)
            if style is None:
                self._send_json(400, {"error": f"unknown style '{style_raw}'", "styles": STYLE_NAMES})
                return
            from naturalizer.compare import run_comparison

            # Server-side comparison stays on the deterministic engines (fast,
            # free) + key-gated external APIs; live site scraping is CLI-only.
            report = run_comparison(text, style=style, use_llm=False)
            self._send_json(200, report)
        elif path == "/api/naturalize":
            text = data.get("text", "")
            if not isinstance(text, str) or not text.strip():
                self._send_json(400, {"error": "missing or empty 'text'"})
                return
            style_raw = data.get("style", "academic")
            style = _normalize_style(style_raw)
            if style is None:
                self._send_json(400, {"error": f"unknown style '{style_raw}'", "styles": STYLE_NAMES})
                return
            features = plan_features()
            plan_note = None
            use_llm = data.get("use_llm")
            deep = bool(data.get("deep"))
            if not features["llm"]:
                if use_llm:
                    plan_note = (
                        "Free plan — the LLM rewrite is a Pro feature; using the "
                        "deterministic rewrite instead."
                    )
                use_llm = False
            if deep and not features["deep"]:
                self._send_json(402, {
                    "error": "Deep humanize (translation chain) is a Pro feature — "
                    "set NATURALIZER_PLAN=pro or configure an LLM in .env.local.",
                    "plan": plan_status(),
                })
                return
            allowed, quota_err = check_word_quota(len(text.split()))
            if not allowed:
                self._send_json(429, {"error": quota_err, "plan": plan_status()})
                return
            provider = _normalize_provider(data.get("provider"))
            intensity = _normalize_intensity(
                data.get("intensity"), cap=features.get("max_intensity", 1.0)
            )
            seed = _normalize_seed(data.get("seed"))
            rewrite_mode = _normalize_rewrite_mode(data.get("rewrite_mode"))
            result = engine.naturalize(
                text,
                style=style,
                use_llm=use_llm,
                deep=deep,
                provider=provider,
                seed=seed,
                intensity=intensity,
                rewrite_mode=rewrite_mode,
            )
            record_usage(len(text.split()))
            _save_history(result, text, style, "naturalize", provider, intensity, seed=seed)
            payload = result.to_dict()
            if plan_note:
                payload["plan_note"] = plan_note
            self._send_json(200, payload)
        elif path == "/api/batch":
            texts = data.get("texts")
            if not isinstance(texts, list) or not texts:
                self._send_json(400, {"error": "missing or empty 'texts' list"})
                return
            if not plan_features()["batch"]:
                self._send_json(402, {
                    "error": "Batch mode is a Pro feature — set NATURALIZER_PLAN=pro "
                    "or configure an LLM in .env.local.",
                    "plan": plan_status(),
                })
                return
            style_raw = data.get("style", "academic")
            style = _normalize_style(style_raw)
            if style is None:
                self._send_json(400, {"error": f"unknown style '{style_raw}'", "styles": STYLE_NAMES})
                return
            use_llm = data.get("use_llm")
            deep = bool(data.get("deep"))
            provider = _normalize_provider(data.get("provider"))
            intensity = _normalize_intensity(data.get("intensity"))
            seed = _normalize_seed(data.get("seed"))
            rewrite_mode = _normalize_rewrite_mode(data.get("rewrite_mode"))
            results = engine.batch(
                [t for t in texts if isinstance(t, str)],
                style=style, use_llm=use_llm, deep=deep, provider=provider,
                intensity=intensity, seed=seed, rewrite_mode=rewrite_mode,
            )
            for i, r in enumerate(results):
                _save_history(
                    r, texts[i] if isinstance(texts[i], str) else "", style,
                    "batch", provider, intensity, seed=seed + i,
                )
            self._send_json(200, {"results": [r.to_dict() for r in results]})
        else:
            self._send_json(404, {"error": "not found"})

    # -- streaming -----------------------------------------------------------

    def _handle_stream(self, data: dict) -> None:
        """SSE stream of a naturalize run: status -> delta* -> done|error."""
        text = data.get("text", "")
        if not isinstance(text, str) or not text.strip():
            self._send_json(400, {"error": "missing or empty 'text'"})
            return
        style_raw = data.get("style", "academic")
        style = _normalize_style(style_raw)
        if style is None:
            self._send_json(400, {"error": f"unknown style '{style_raw}'", "styles": STYLE_NAMES})
            return
        features = plan_features()
        plan_note = None
        use_llm = data.get("use_llm")
        if not features["llm"]:
            if use_llm:
                plan_note = (
                    "Free plan — the LLM rewrite is a Pro feature; using the "
                    "deterministic rewrite instead."
                )
            use_llm = False
        deep = bool(data.get("deep"))
        if deep and not features["deep"]:
            self._send_json(402, {
                "error": "Deep humanize (translation chain) is a Pro feature — "
                "set NATURALIZER_PLAN=pro or configure an LLM in .env.local.",
                "plan": plan_status(),
            })
            return
        allowed, quota_err = check_word_quota(len(text.split()))
        if not allowed:
            self._send_json(429, {"error": quota_err, "plan": plan_status()})
            return
        provider = _normalize_provider(data.get("provider"))
        intensity = _normalize_intensity(
            data.get("intensity"), cap=features.get("max_intensity", 1.0)
        )
        seed = _normalize_seed(data.get("seed"))
        rewrite_mode = _normalize_rewrite_mode(data.get("rewrite_mode"))

        headers = self._common_headers()
        headers["Content-Type"] = "text/event-stream; charset=utf-8"
        headers["Cache-Control"] = "no-cache"
        headers["Connection"] = "close"
        self._send_headers(200, headers)

        def _sse(event: str, payload: dict) -> None:
            body = json.dumps(payload)
            self.wfile.write(f"event: {event}\ndata: {body}\n\n".encode("utf-8"))
            self.wfile.flush()

        from naturalizer.streaming import naturalize_stream

        # Heartbeats: the generator runs on a worker thread and events are
        # pumped over a queue. While the generator is quiet (a slow LLM or
        # a deep-chain hop), a `ping` event is sent every few seconds so the
        # client knows the connection is alive instead of staring at a
        # frozen "Streaming…". Total runtime is capped so a stuck provider
        # can never hold the connection open forever.
        _PING_EVERY = 8.0      # seconds of silence before a ping
        _STREAM_CAP = 600.0    # hard ceiling for one stream (seconds)
        ev_q: "queue.Queue" = queue.Queue()

        def _pump() -> None:
            try:
                for event in naturalize_stream(
                    text,
                    style=style,
                    use_llm=use_llm,
                    deep=deep,
                    provider=provider,
                    intensity=intensity,
                    seed=seed,
                    rewrite_mode=rewrite_mode,
                ):
                    ev_q.put(("event", event))
            except Exception as exc:  # pragma: no cover - defensive
                ev_q.put(("error", f"stream failed: {exc}"))
            ev_q.put(("stop", None))

        threading.Thread(target=_pump, daemon=True).start()
        started = time.time()
        try:
            while True:
                try:
                    kind, payload = ev_q.get(timeout=_PING_EVERY)
                except queue.Empty:
                    if time.time() - started > _STREAM_CAP:
                        _sse("error", {"message": "stream timed out"})
                        break
                    _sse("ping", {})
                    continue
                if kind == "stop":
                    break
                if kind == "error":
                    _sse("error", {"message": payload})
                    break
                event = payload
                if event["type"] == "status":
                    status = {"step": event["step"]}
                    if event.get("detail"):
                        status["detail"] = event["detail"]
                    _sse("status", status)
                elif event["type"] == "delta":
                    _sse("delta", {"text": event["text"]})
                elif event["type"] == "clear":
                    # Deterministic preview finished; the LLM rewrite is
                    # about to stream — tell the client to reset the pane.
                    _sse("clear", {})
                elif event["type"] == "done":
                    result = event["result"]
                    if plan_note:
                        result["plan_note"] = plan_note
                    _sse("done", result)
                    record_usage(len(text.split()))
                    _save_history(
                        result, text, style, "naturalize", provider,
                        intensity, seed=seed,
                    )
                elif event["type"] == "error":
                    _sse("error", {"message": event["message"]})
        except (BrokenPipeError, ConnectionResetError):  # client went away
            pass
        finally:
            # SSE has no content-length: the stream ends when the server
            # closes the connection (the client reads until EOF).
            self.close_connection = True

    # -- upload / export ----------------------------------------------------

    def _handle_upload(self, parsed) -> None:
        content_type_header = self.headers.get("Content-Type", "")
        m = re.search(r"boundary=([^;]+)", content_type_header)
        if "multipart/form-data" not in content_type_header or not m:
            self._send_json(400, {"error": "expected multipart/form-data"})
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            self._send_json(411, {"error": "Content-Length required"})
            return
        limit = _max_upload()
        if length > limit:
            if length:
                try:
                    self.rfile.read(length)  # drain so the client gets a clean 413
                except OSError:  # pragma: no cover - client vanished mid-upload
                    pass
            self._send_json(413, {"error": f"file too large (max {limit} bytes)"})
            return
        raw = self.rfile.read(length)
        if len(raw) > limit:
            self._send_json(413, {"error": f"file too large (max {limit} bytes)"})
            return

        file_name = file_data = file_type = None
        style = None
        use_llm = None
        deep = False
        provider = "auto"
        intensity = 0.5
        seed = 0
        boundary = m.group(1).strip('"').encode("utf-8")
        for name, filename, part_type, body in _parse_multipart(raw, boundary):
            if name == "file" and filename and body:
                file_name, file_data, file_type = filename, body, part_type
            elif name == "style":
                style = body.decode("utf-8", "replace").strip()
            elif name == "use_llm":
                value = body.decode("utf-8", "replace").strip().lower()
                if value in ("1", "true", "on", "yes"):
                    use_llm = True
                elif value in ("0", "false", "off", "no"):
                    use_llm = False
            elif name == "deep":
                value = body.decode("utf-8", "replace").strip().lower()
                if value in ("1", "true", "on", "yes"):
                    deep = True
            elif name == "provider":
                provider = _normalize_provider(body.decode("utf-8", "replace").strip())
            elif name == "intensity":
                try:
                    intensity = float(body.decode("utf-8", "replace").strip())
                except ValueError:
                    intensity = 0.5
            elif name == "seed":
                try:
                    seed = max(0, int(body.decode("utf-8", "replace").strip()))
                except ValueError:
                    seed = 0

        if file_data is None:
            self._send_json(400, {"error": "missing 'file' part"})
            return
        if style:
            normalized_style = _normalize_style(style)
            if normalized_style is None:
                self._send_json(400, {"error": f"unknown style '{style}'", "styles": STYLE_NAMES})
                return
            style = normalized_style

        fmt = detect_format(file_name, file_data)
        try:
            original, fmt = extract_text(file_data, file_name)
        except ExtractionError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        warnings = []
        if fmt == "pdf":
            warnings.append(
                "PDF text extraction is best-effort: reading order, layout, and "
                "non-Latin fonts may be imperfect, and scanned/image-only PDFs "
                "contain no extractable text."
            )

        features = plan_features()
        plan_note = None
        if not features["llm"]:
            if use_llm:
                plan_note = (
                    "Free plan — the LLM rewrite is a Pro feature; using the "
                    "deterministic rewrite instead."
                )
            use_llm = False
        if deep and not features["deep"]:
            self._send_json(402, {
                "error": "Deep humanize (translation chain) is a Pro feature — "
                "set NATURALIZER_PLAN=pro or configure an LLM in .env.local.",
                "plan": plan_status(),
            })
            return
        allowed, quota_err = check_word_quota(len(original.split()))
        if not allowed:
            self._send_json(429, {"error": quota_err, "plan": plan_status()})
            return
        intensity = _normalize_intensity(
            intensity, cap=features.get("max_intensity", 1.0)
        )
        result = engine.naturalize(
            original,
            style=style or "academic",
            use_llm=use_llm,
            deep=deep,
            provider=provider,
            intensity=intensity,
            seed=seed,
        )
        record_usage(len(original.split()))

        download_fmt = (parse_qs(parsed.query).get("format") or [None])[0]
        if download_fmt:
            if download_fmt not in EXPORT_FORMATS:
                self._send_json(
                    400, {"error": f"unknown format '{download_fmt}'", "formats": list(EXPORT_FORMATS)}
                )
                return
            rewritten = result.llm_rewritten if result.llm_used else result.rewritten
            body = to_bytes(rewritten, download_fmt)
            self._send_file(body, download_fmt, f"{_safe_stem(file_name)}-naturalized.{download_fmt}")
            return

        _save_history(result, original, style or "academic", "upload", provider, intensity, seed=seed)
        payload = result.to_dict()
        payload["format"] = fmt
        payload["warnings"] = warnings
        if plan_note:
            payload["plan_note"] = plan_note
        self._send_json(200, payload)

    def _handle_export(self, data: dict) -> None:
        text = data.get("text", "")
        fmt = data.get("format", "")
        if not isinstance(text, str) or not text.strip():
            self._send_json(400, {"error": "missing or empty 'text'"})
            return
        if fmt not in EXPORT_FORMATS:
            self._send_json(400, {"error": f"unknown format '{fmt}'", "formats": list(EXPORT_FORMATS)})
            return
        self._send_file(to_bytes(text, fmt), fmt, f"naturalized.{fmt}")

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _status() -> dict:
        try:
            from naturalizer.llm import llm_available, llm_provider_choices, llm_provider_label

            llm = llm_available()
            llm_label = llm_provider_label()
            provider_choices = llm_provider_choices()
        except Exception:  # pragma: no cover
            llm = False
            llm_label = None
            provider_choices = [
                {"name": "auto", "label": "Auto (first available)", "configured": True}
            ]
        return {
            "name": "naturalizer",
            "version": __version__,
            "styles": STYLE_NAMES,
            "style_labels": {name: STYLES[name]["label"] for name in STYLE_NAMES},
            "llm_configured": llm,
            "llm_model": llm_label,
            "providers": provider_choices,
            "uploads": {
                "formats": ["txt", "md", "markdown", "docx", "pdf"],
                "export_formats": list(EXPORT_FORMATS),
                "max_bytes": _max_upload(),
            },
            "plagiarism": True,
            "plan": plan_status(),
            "benchmark": True,
            "perfect": True,
            "detectors": True,
            "stream": True,
        }


def main() -> None:
    # Pull provider credentials from .env.local / .env (never overwrites env).
    from naturalizer.envfile import load_envfile

    load_envfile()
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "127.0.0.1")
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Naturalizer v{__version__} running at http://{host}:{port}")
    print(f"LLM backend: {_llm_status()}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.server_close()


def _llm_on() -> bool:
    try:
        from naturalizer.llm import llm_available

        return llm_available()
    except Exception:  # pragma: no cover
        return False


def _llm_status() -> str:
    try:
        from naturalizer.llm import llm_provider_label

        label = llm_provider_label()
        if label:
            return f"configured — {label}"
    except Exception:  # pragma: no cover
        pass
    return "off (deterministic only)"


if __name__ == "__main__":
    main()
