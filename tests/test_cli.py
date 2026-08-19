"""Tests for the command-line naturalizer pipeline."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.cli import main
from naturalizer.export import to_bytes
from naturalizer.extract import extract_text

AI_DRAFT = (
    "In today's fast-paced world, it is important to note that technology "
    "plays a crucial role. Furthermore, we must leverage cutting-edge solutions."
)


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "draft.txt").write_text(AI_DRAFT, encoding="utf-8")
        (self.dir / "draft.docx").write_bytes(to_bytes(AI_DRAFT, "docx"))
        (self.dir / "draft.pdf").write_bytes(to_bytes(AI_DRAFT, "pdf"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_txt_default_output(self):
        code = main([str(self.dir / "draft.txt")])
        self.assertEqual(code, 0)
        out = self.dir / "draft-naturalized.txt"
        self.assertTrue(out.exists())
        text, _ = extract_text(out.read_bytes(), out.name)
        self.assertNotIn("fast-paced", text)
        self.assertIn("technology", text)

    def test_docx_to_pdf(self):
        code = main(["-f", "pdf", str(self.dir / "draft.docx")])
        self.assertEqual(code, 0)
        out = self.dir / "draft-naturalized.pdf"
        self.assertTrue(out.read_bytes().startswith(b"%PDF-"))
        text, _ = extract_text(out.read_bytes(), out.name)
        self.assertNotIn("furthermore", text.lower())

    def test_explicit_output(self):
        out = self.dir / "clean.docx"
        code = main(["-o", str(out), str(self.dir / "draft.txt")])
        self.assertEqual(code, 0)
        self.assertTrue(out.read_bytes().startswith(b"PK\x03\x04"))

    def test_multiple_inputs(self):
        code = main([str(self.dir / "draft.txt"), str(self.dir / "draft.pdf")])
        self.assertEqual(code, 0)
        self.assertTrue((self.dir / "draft-naturalized.txt").exists())
        self.assertTrue((self.dir / "draft-naturalized.pdf").exists())

    def test_existing_output_requires_overwrite(self):
        out = self.dir / "draft-naturalized.txt"
        out.write_text("keep me", encoding="utf-8")
        code = main([str(self.dir / "draft.txt")])
        self.assertEqual(code, 1)
        self.assertEqual(out.read_text(encoding="utf-8"), "keep me")
        code = main(["--overwrite", str(self.dir / "draft.txt")])
        self.assertEqual(code, 0)
        self.assertNotEqual(out.read_text(encoding="utf-8"), "keep me")

    def test_json_output(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--json", str(self.dir / "draft.txt")])
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertIn("score", payload)
        self.assertEqual(payload["format"], "txt")
        self.assertEqual(payload["warnings"], [])
        self.assertIn("original", payload)

    def test_json_reports_pdf_warning(self):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--json", str(self.dir / "draft.pdf")])
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["format"], "pdf")
        self.assertTrue(payload["warnings"])

    def test_missing_file_fails(self):
        code = main([str(self.dir / "nope.pdf")])
        self.assertEqual(code, 1)

    def test_corrupt_docx_fails(self):
        bad = self.dir / "broken.docx"
        bad.write_bytes(b"this is not a zip")
        code = main([str(bad)])
        self.assertEqual(code, 1)
        self.assertFalse((self.dir / "broken-naturalized.docx").exists())

    def test_output_with_multiple_inputs_is_usage_error(self):
        code = main(["-o", str(self.dir / "x.txt"), str(self.dir / "draft.txt"), str(self.dir / "draft.pdf")])
        self.assertEqual(code, 2)

    def test_no_llm_flag_accepted(self):
        code = main(["--no-llm", str(self.dir / "draft.txt")])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
