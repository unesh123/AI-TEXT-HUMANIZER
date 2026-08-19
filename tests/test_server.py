"""Smoke tests for the HTTP server (boots on an ephemeral port)."""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tests.testutil as _testutil  # noqa: F401  (scrub LLM env — hermetic suite)

from naturalizer.engine import Naturalizer
from naturalizer.export import to_bytes
from naturalizer.extract import extract_text
from server import Handler, ThreadingHTTPServer


BOUNDARY = "----nat-test-boundary"


def _multipart(fields, boundary=BOUNDARY):
    """Build a multipart/form-data body. fields: name -> (filename, ctype, bytes)."""
    chunks = []
    for name, (filename, ctype, data) in fields.items():
        disp = f'form-data; name="{name}"'
        if filename:
            disp += f'; filename="{filename}"'
        head = f"Content-Disposition: {disp}"
        if ctype:
            head += f"\r\nContent-Type: {ctype}"
        chunks.append(f"--{boundary}\r\n{head}\r\n\r\n".encode() + data + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Isolate plan usage + saved history from the repo's real state dir.
        cls.state_dir = tempfile.mkdtemp()
        cls._old_state_dir = os.environ.get("NATURALIZER_STATE_DIR")
        os.environ["NATURALIZER_STATE_DIR"] = cls.state_dir
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        if cls._old_state_dir is None:
            os.environ.pop("NATURALIZER_STATE_DIR", None)
        else:
            os.environ["NATURALIZER_STATE_DIR"] = cls._old_state_dir
        shutil.rmtree(cls.state_dir, ignore_errors=True)

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            e.close()
            return e.code, json.loads(body)

    def test_status_endpoint(self):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/api/status", timeout=10
        ) as resp:
            status = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(status["name"], "naturalizer")
        self.assertIn("academic", status["styles"])

    def test_health_endpoint(self):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/api/health", timeout=10
        ) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["service"], "naturalizer")
        self.assertIn("uptime_seconds", health)
        self.assertTrue(health["checks"]["engine"])

    def test_index_served(self):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/", timeout=10
        ) as resp:
            html = resp.read().decode("utf-8")
        self.assertIn("Naturalizer", html)

    def test_invalid_json_returns_bad_request(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/naturalize",
            data=b"{not-json}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertEqual(body["error"], "invalid JSON body")

    def test_cors_preflight(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/naturalize",
            headers={"Origin": "http://127.0.0.1:3000", "Access-Control-Request-Method": "POST"},
            method="OPTIONS",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 204)
            self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"), "http://127.0.0.1:3000")
            self.assertIn("POST", resp.headers.get("Access-Control-Allow-Methods", ""))

    def test_naturalize_endpoint(self):
        status, body = self._post(
            "/api/naturalize",
            {"text": "In today's fast-paced world, it is important to note that tech matters."},
        )
        self.assertEqual(status, 200)
        self.assertIn("rewritten", body)
        self.assertIn("score", body)

    def test_history_saved_on_naturalize(self):
        self._post("/api/history/clear", {})
        text = "Furthermore, the data was noisy and required further analysis."
        status, body = self._post("/api/naturalize", {"text": text})
        self.assertEqual(status, 200)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/api/history?limit=10", timeout=10
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        entries = data["entries"]
        self.assertEqual(data["count"], 1)
        self.assertEqual(entries[0]["input"], text)
        self.assertEqual(entries[0]["output"], body["rewritten"])
        self.assertEqual(entries[0]["score"], body["score"])
        self.assertEqual(entries[0]["mode"], "naturalize")
        self.assertIn("iso", entries[0])

    def test_history_delete_and_clear(self):
        self._post("/api/history/clear", {})
        self._post("/api/naturalize", {"text": "First draft that is clearly robotic."})
        self._post("/api/naturalize", {"text": "Second draft that is clearly robotic."})
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/api/history?limit=10", timeout=10
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data["count"], 2)
        first_id = data["entries"][1]["id"]  # older entry
        status, body = self._post("/api/history/delete", {"id": first_id})
        self.assertEqual(status, 200)
        self.assertTrue(body["deleted"])
        status, body = self._post("/api/history/delete", {"id": first_id})
        self.assertEqual(status, 404)
        status, body = self._post("/api/history/clear", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["cleared"], 1)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/api/history?limit=10", timeout=10
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data["count"], 0)

    def test_naturalize_stream_endpoint(self):
        # The SSE endpoint returns text/event-stream with status/delta/done
        # events; deltas reassemble the deterministic rewrite (no LLM in the
        # test env).
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/naturalize/stream",
            data=json.dumps({
                "text": "In today's fast-paced world, it is important to note "
                "that technology plays a crucial role.",
                "style": "academic",
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            self.assertEqual(
                resp.headers.get("Content-Type"),
                "text/event-stream; charset=utf-8",
            )
            raw = resp.read().decode("utf-8")
        self.assertIn("event: status", raw)
        self.assertIn("event: delta", raw)
        self.assertIn("event: done", raw)
        self.assertIn('"step": "analyzing"', raw)
        # The done event carries a full result payload.
        done = raw.split("event: done\ndata: ")[1].split("\n\n")[0]
        payload = json.loads(done)
        self.assertIn("rewritten", payload)
        self.assertIn("score", payload)
        self.assertIn("metrics", payload)

    def test_naturalize_stream_rejects_empty(self):
        status, body = self._post("/api/naturalize/stream", {"text": ""})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_naturalize_stream_rejects_bad_style(self):
        status, body = self._post(
            "/api/naturalize/stream", {"text": "hello there", "style": "nope"}
        )
        self.assertEqual(status, 400)

    def test_naturalize_accepts_deep_flag(self):
        # No LLM is configured in the test process, so deep just falls back
        # to the deterministic path — the point is the flag is accepted and
        # the response carries the llm_method field.
        status, body = self._post(
            "/api/naturalize",
            {"text": "Furthermore, the data was noisy.", "deep": True},
        )
        self.assertEqual(status, 200)
        self.assertIn("llm_method", body)
        self.assertFalse(body["llm_used"])

    def test_naturalize_rejects_empty(self):
        status, body = self._post("/api/naturalize", {"text": ""})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_naturalize_accepts_case_insensitive_style(self):
        status, body = self._post(
            "/api/naturalize",
            {"text": "Furthermore, the data was noisy.", "style": "Academic"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["style"], "academic")

    def test_detect_accepts_case_insensitive_style(self):
        status, body = self._post(
            "/api/detect",
            {"text": "Furthermore, the data was noisy.", "style": " BUSINESS "},
        )
        self.assertEqual(status, 200)
        self.assertIn("verdict", body)

    def test_naturalize_rejects_bad_style(self):
        status, body = self._post(
            "/api/naturalize", {"text": "hello there", "style": "nope"}
        )
        self.assertEqual(status, 400)

    def test_naturalize_accepts_titlecase_style(self):
        """Regression: 'Academic' (title-case) must be normalized, not rejected."""
        status, body = self._post(
            "/api/naturalize",
            {"text": "Furthermore, the results were significant.", "style": "Academic"},
        )
        self.assertEqual(status, 200)

    def test_naturalize_accepts_uppercase_style(self):
        """Regression: 'BUSINESS' (upper-case) must be normalized, not rejected."""
        status, body = self._post(
            "/api/naturalize",
            {"text": "Furthermore, the results were significant.", "style": "BUSINESS"},
        )
        self.assertEqual(status, 200)

    def test_detect_accepts_mixed_case_style(self):
        """Regression: /api/detect must also accept mixed-case style labels."""
        status, body = self._post(
            "/api/detect",
            {"text": "Furthermore, the results were significant.", "style": "Academic"},
        )
        self.assertEqual(status, 200)

    def test_batch_endpoint(self):
        status, body = self._post(
            "/api/batch", {"texts": ["One sentence.", "Furthermore, data was noisy."]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(body["results"]), 2)

    def test_plagiarism_endpoint_high(self):
        sample = (
            "Technology has quietly permeated every aspect of our daily lives. "
            "Digital tools have reshaped how organizations operate, and "
            "businesses that fail to adapt risk falling behind in an "
            "increasingly competitive landscape."
        )
        status, body = self._post(
            "/api/plagiarism", {"text": sample, "refs": [sample]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["verdict"], "high")
        self.assertGreaterEqual(body["score"], 80)
        self.assertIn("per_ref", body)
        self.assertIsInstance(body["matching"], list)
        self.assertTrue(body["matching"])

    def test_plagiarism_endpoint_unrelated(self):
        status, body = self._post(
            "/api/plagiarism",
            {
                "text": "This paragraph is about baking bread at home.",
                "refs": ["Technology permeates daily life and business operations."],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["verdict"], "low")

    def test_plagiarism_endpoint_missing_refs(self):
        status, body = self._post(
            "/api/plagiarism", {"text": "Some text to check.", "refs": []}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["verdict"], "low")
        self.assertIn("note", body)

    def test_404(self):
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/api/does-not-exist", timeout=10
            )
            self.fail("expected HTTPError")
        except urllib.error.HTTPError as e:
            e.read()
            e.close()
            self.assertEqual(e.code, 404)

    # -- upload / export -------------------------------------------------

    def _post_raw(self, path, body, content_type, headers=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers={"Content-Type": content_type, **(headers or {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as e:
            body = e.read()
            e.close()
            return e.code, body, e.headers

    def test_status_advertises_uploads(self):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/api/status", timeout=10
        ) as resp:
            status = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(status["uploads"]["formats"], ["txt", "md", "markdown", "docx", "pdf"])
        self.assertGreater(status["uploads"]["max_bytes"], 0)

    def test_detect_endpoint(self):
        status, body = self._post(
            "/api/detect",
            {
                "text": "In today's world, it is important to note that tech plays a crucial role. "
                "The wiring came loose behind the counter and we fixed it."
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("verdict", body)
        self.assertIn("confidence", body)
        self.assertIn("distribution", body)
        self.assertIn("sentences", body)
        self.assertEqual(
            set(body["distribution"]), {"ai", "mix", "human"}
        )
        self.assertTrue(all(s["label"] in ("ai", "mix", "human") for s in body["sentences"]))

    def test_detect_rejects_empty(self):
        status, body = self._post("/api/detect", {"text": ""})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_status_advertises_providers(self):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/api/status", timeout=10
        ) as resp:
            status = json.loads(resp.read().decode("utf-8"))
        self.assertIn("providers", status)
        names = [p["name"] for p in status["providers"]]
        self.assertIn("auto", names)
        self.assertIn("claude", names)
        self.assertIn("cx", names)

    def test_naturalize_accepts_provider(self):
        status, body = self._post(
            "/api/naturalize",
            {
                "text": "Furthermore, the data was noisy.",
                "provider": "cx",
                "use_llm": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("llm_provider", body)
        # With no provider keys in the test env, an explicit provider must
        # explain the fallback in the API response too.
        self.assertIn("llm_warning", body)
        self.assertIn("cx isn't configured", body["llm_warning"])

    def test_naturalize_accepts_new_provider(self):
        status, body = self._post(
            "/api/naturalize",
            {
                "text": "Furthermore, the data was noisy.",
                "provider": "gemini",
                "use_llm": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("llm_warning", body)
        self.assertIn("gemini isn't configured", body["llm_warning"])

    def test_perfect_endpoint(self):
        status, body = self._post(
            "/api/perfect",
            {
                "text": "In today's fast-paced world, it is important to note that tech plays a crucial role. "
                "Furthermore, the ever-evolving landscape of digital tools transforms the way we work. "
                "Moreover, organizations must leverage cutting-edge solutions to remain competitive.",
                "style": "academic",
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("text", body)
        self.assertIn("passes", body)
        self.assertIn("scores", body)
        self.assertIn("detectors", body)
        self.assertGreaterEqual(body["passes"], 1)
        self.assertEqual(len(body["scores"]), body["passes"] + 1)
        self.assertTrue(body["text"].strip())

    def test_perfect_rejects_empty(self):
        status, body = self._post("/api/perfect", {"text": ""})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_detectors_endpoint(self):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/api/detectors", timeout=10
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = [d["name"] for d in data["detectors"]]
        self.assertIn("local", names)
        self.assertIn("gptzero", names)
        local = next(d for d in data["detectors"] if d["name"] == "local")
        self.assertTrue(local["configured"])
        self.assertTrue(local["live"])

    def test_detectors_scan_endpoint(self):
        # No keys in the hermetic env -> no third-party results, no crash.
        status, body = self._post("/api/detectors/scan", {"text": "Hello world."})
        self.assertEqual(status, 200)
        self.assertEqual(body["results"], [])

        status, body = self._post("/api/detectors/scan", {"text": "  "})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_naturalize_returns_metrics(self):
        status, body = self._post(
            "/api/naturalize",
            {
                "text": "In today's world, it is important to note that tech plays a crucial role. "
                "Furthermore, we must leverage cutting-edge tools to remain competitive. "
                "Moreover, this is essential to highlight in any discussion of modern business.",
                "style": "academic",
            },
        )
        self.assertEqual(status, 200)
        m = body.get("metrics")
        self.assertIsNotNone(m)
        self.assertEqual(set(m), {"before", "after", "after_score", "plain_register"})
        self.assertIn("before", m["plain_register"])
        self.assertIn("after", m["plain_register"])
        self.assertEqual(
            set(m["before"]),
            {"perplexity", "burstiness", "syntactic", "coherence", "word_choice"},
        )
        self.assertIsInstance(m["after_score"], int)

    def test_upload_markdown_returns_json_result(self):
        body = _multipart({
            "file": ("notes.md", "text/markdown", b"# Notes\n\nFurthermore, data was noisy."),
            "style": (None, None, b"academic"),
        })
        status, raw, _ = self._post_raw(
            "/api/upload", body, f"multipart/form-data; boundary={BOUNDARY}"
        )
        self.assertEqual(status, 200)
        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(data["format"], "md")
        self.assertEqual(data["warnings"], [])
        self.assertIn("metrics", data)

    def test_upload_txt_returns_json_result(self):
        body = _multipart({
            "file": ("draft.txt", "text/plain", b"In today's world, tech matters a lot."),
            "style": (None, None, b"academic"),
        })
        status, raw, _ = self._post_raw(
            "/api/upload", body, f"multipart/form-data; boundary={BOUNDARY}"
        )
        self.assertEqual(status, 200)
        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(data["format"], "txt")
        self.assertEqual(data["warnings"], [])
        self.assertIn("original", data)
        self.assertIn("rewritten", data)
        self.assertIn("diff", data)
        self.assertEqual(data["original"], "In today's world, tech matters a lot.")

    def test_upload_txt_download_docx(self):
        original = "Furthermore, the data was noisy and required cleanup."
        body = _multipart({"file": ("draft.txt", "text/plain", original.encode())})
        status, raw, headers = self._post_raw(
            "/api/upload?format=docx",
            body,
            f"multipart/form-data; boundary={BOUNDARY}",
        )
        self.assertEqual(status, 200)
        self.assertIn(
            "openxmlformats-officedocument.wordprocessingml.document",
            headers.get_content_type(),
        )
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        self.assertTrue(raw.startswith(b"PK\x03\x04"))
        expected = Naturalizer(seed=0).naturalize(
            original, style="academic", use_llm=False
        ).rewritten
        text, fmt = extract_text(raw, "draft-naturalized.docx")
        self.assertEqual(fmt, "docx")
        self.assertEqual(text, expected)

    def test_upload_pdf_warns_about_best_effort(self):
        pdf = to_bytes("Hello, world. This is a small PDF.", "pdf")
        body = _multipart({
            "file": ("report.pdf", "application/pdf", pdf),
            "use_llm": (None, None, b"false"),
        })
        status, raw, _ = self._post_raw(
            "/api/upload", body, f"multipart/form-data; boundary={BOUNDARY}"
        )
        self.assertEqual(status, 200)
        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(data["format"], "pdf")
        self.assertTrue(data["warnings"])
        self.assertIn("Hello, world.", data["original"])

    def test_upload_pdf_download_pdf(self):
        pdf = to_bytes("Round trip through PDF upload and download.", "pdf")
        body = _multipart({"file": ("report.pdf", "application/pdf", pdf)})
        status, raw, headers = self._post_raw(
            "/api/upload?format=pdf",
            body,
            f"multipart/form-data; boundary={BOUNDARY}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/pdf")
        self.assertTrue(raw.startswith(b"%PDF-"))
        text, _ = extract_text(raw, "out.pdf")
        self.assertTrue(text)

    def test_upload_rejects_missing_file(self):
        body = _multipart({"style": (None, None, b"academic")})
        status, raw, _ = self._post_raw(
            "/api/upload", body, f"multipart/form-data; boundary={BOUNDARY}"
        )
        self.assertEqual(status, 400)
        self.assertIn("file", json.loads(raw.decode("utf-8"))["error"])

    def test_upload_rejects_non_multipart(self):
        status, raw, _ = self._post_raw("/api/upload", b"nope", "application/json")
        self.assertEqual(status, 400)

    def test_upload_rejects_bad_style(self):
        body = _multipart({
            "file": ("d.txt", "text/plain", b"some text here"),
            "style": (None, None, b"bogus"),
        })
        status, raw, _ = self._post_raw(
            "/api/upload", body, f"multipart/form-data; boundary={BOUNDARY}"
        )
        self.assertEqual(status, 400)

    def test_upload_size_cap(self):
        old = os.environ.get("MAX_UPLOAD_BYTES")
        os.environ["MAX_UPLOAD_BYTES"] = "200"
        try:
            body = _multipart({
                "file": ("big.txt", "text/plain", b"x" * 1024),
            })
            status, raw, _ = self._post_raw(
                "/api/upload", body, f"multipart/form-data; boundary={BOUNDARY}"
            )
            self.assertEqual(status, 413)
            self.assertIn("too large", json.loads(raw.decode("utf-8"))["error"])
        finally:
            if old is None:
                os.environ.pop("MAX_UPLOAD_BYTES", None)
            else:
                os.environ["MAX_UPLOAD_BYTES"] = old

    def test_export_downloads_pdf(self):
        status, raw, headers = self._post_raw(
            "/api/export",
            json.dumps({"text": "This is the naturalized result.", "format": "pdf"}).encode(),
            "application/json",
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/pdf")
        self.assertTrue(raw.startswith(b"%PDF-"))
        text, _ = extract_text(raw, "out.pdf")
        self.assertIn("naturalized result", text)

    def test_export_rejects_unknown_format(self):
        status, raw, _ = self._post_raw(
            "/api/export",
            json.dumps({"text": "hi", "format": "nope"}).encode(),
            "application/json",
        )
        self.assertEqual(status, 400)

    def test_export_rejects_empty_text(self):
        status, raw, _ = self._post_raw(
            "/api/export",
            json.dumps({"text": "  ", "format": "txt"}).encode(),
            "application/json",
        )
        self.assertEqual(status, 400)


    # -- production hardening -------------------------------------------

    def test_security_headers_on_index(self):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/", timeout=10
        ) as resp:
            headers = resp.headers
            html = resp.read().decode("utf-8")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertIn("no-referrer", headers.get("Referrer-Policy", ""))
        self.assertIn("same-origin", headers.get("Cross-Origin-Opener-Policy", ""))
        self.assertIn("no-cache", headers.get("Cache-Control", ""))
        csp = headers.get("Content-Security-Policy", "")
        self.assertIn("script-src 'self' 'nonce-", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("object-src 'none'", csp)
        # The nonce in the header must match the inline script tag.
        nonce = csp.split("nonce-", 1)[1].split("'", 1)[0]
        self.assertIn(f'<script nonce="{nonce}">', html)

    def test_robots_and_favicon(self):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/robots.txt", timeout=10
        ) as resp:
            body = resp.read().decode("utf-8")
        self.assertIn("Disallow: /api/", body)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/favicon.ico", timeout=10
        ) as resp:
            self.assertEqual(resp.status, 204)

    def test_origin_block_on_post(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/history/clear",
            data=b"{}",
            headers={"Content-Type": "application/json", "Origin": "http://evil.example"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("expected HTTPError")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            e.close()
            self.assertEqual(e.code, 403)
            self.assertIn("origin not allowed", body)

    def test_loopback_origin_allowed(self):
        # localhost -> 127.0.0.1 (the loopback family) must still work.
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/naturalize",
            data=json.dumps({"text": "Furthermore, the data was noisy."}).encode(),
            headers={"Content-Type": "application/json", "Origin": f"http://localhost:{self.port}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            body = json.loads(resp.read().decode("utf-8"))
        self.assertIn("rewritten", body)
        self.assertEqual(resp.headers.get("Access-Control-Allow-Origin"),
                         f"http://localhost:{self.port}")

    def test_options_preflight(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/naturalize",
            headers={"Origin": f"http://127.0.0.1:{self.port}"},
            method="OPTIONS",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 204)
            self.assertIn("POST", resp.headers.get("Access-Control-Allow-Methods", ""))
            self.assertEqual(
                resp.headers.get("Access-Control-Allow-Origin"),
                f"http://127.0.0.1:{self.port}",
            )

    def test_json_body_size_cap(self):
        old = os.environ.get("MAX_JSON_BYTES")
        os.environ["MAX_JSON_BYTES"] = "200"
        try:
            status, body = self._post(
                "/api/naturalize", {"text": "x" * 500}
            )
            self.assertEqual(status, 413)
            self.assertIn("too large", body["error"])
        finally:
            if old is None:
                os.environ.pop("MAX_JSON_BYTES", None)
            else:
                os.environ["MAX_JSON_BYTES"] = old

    def test_rate_limit_returns_429(self):
        import server as server_mod

        old = os.environ.get("RATE_LIMIT_PER_MIN")
        os.environ["RATE_LIMIT_PER_MIN"] = "3"
        try:
            with server_mod._RATE_LOCK:
                server_mod._RATE_HITS.clear()
            statuses = []
            for _ in range(4):
                status, body = self._post("/api/detect", {"text": "Hello world."})
                statuses.append((status, body))
            self.assertEqual(statuses[0][0], 200)
            self.assertEqual(statuses[1][0], 200)
            self.assertEqual(statuses[2][0], 200)
            self.assertEqual(statuses[3][0], 429)
            self.assertIn("Too many requests", statuses[3][1]["error"])
        finally:
            if old is None:
                os.environ.pop("RATE_LIMIT_PER_MIN", None)
            else:
                os.environ["RATE_LIMIT_PER_MIN"] = old
            with server_mod._RATE_LOCK:
                server_mod._RATE_HITS.clear()

    def test_unhandled_exception_returns_500(self):
        import server as server_mod

        original = server_mod.engine.detect

        def boom(text, style="academic"):
            raise RuntimeError("simulated failure")

        server_mod.engine.detect = boom
        try:
            status, body = self._post("/api/detect", {"text": "Hello world."})
        finally:
            server_mod.engine.detect = original
        self.assertEqual(status, 500)
        self.assertEqual(body["error"], "internal server error")


if __name__ == "__main__":
    unittest.main()
