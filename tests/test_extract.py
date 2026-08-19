"""Tests for pure-stdlib text extraction (TXT / DOCX / PDF)."""

import binascii
import io
import sys
import unittest
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.extract import (
    ExtractionError,
    detect_format,
    extract_text,
)


def _serialize_pdf(objects):
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for num, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % num
        out += obj
        out += b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref_pos)
    )
    return bytes(out)


def _pdf(content_stream, flt=b""):
    stream = content_stream
    filter_spec = b""
    if flt == b"/FlateDecode":
        stream = zlib.compress(content_stream)
        filter_spec = b" /Filter /FlateDecode"
    elif flt == b"/ASCIIHexDecode":
        stream = binascii.hexlify(content_stream)
        filter_spec = b" /Filter /ASCIIHexDecode"
    return _serialize_pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d %s>>\nstream\n%s\nendstream" % (len(stream), filter_spec, stream),
    ])


STREAM = (
    b"BT /F1 12 Tf 72 720 Td (Hello, world.) Tj ET\n"
    b"BT /F1 12 Tf 72 700 Td [(The quick) -50 (brown fox)] TJ ET\n"
    b"BT /F1 12 Tf 72 680 Td (A \\(parenthesis\\) and \\\\ backslash) Tj ET"
)


def _docx(document_xml):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<?xml version=\"1.0\"?><Types/>")
        zf.writestr("_rels/.rels", "<?xml version=\"1.0\"?><Relationships/>")
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


DOCX_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    '<w:p><w:r><w:t xml:space="preserve">First paragraph with a </w:t></w:r>'
    "<w:r><w:t>bold-ish run.</w:t></w:r></w:p>"
    '<w:p><w:r><w:t>Second paragraph</w:t></w:r><w:r><w:tab/></w:r>'
    "<w:r><w:t>after tab</w:t></w:r></w:p>"
    "</w:body></w:document>"
)


class ExtractTxtTest(unittest.TestCase):
    def test_utf8_with_bom(self):
        text, fmt = extract_text(b"\xef\xbb\xbfHello, world.\nIt works.", "notes.txt")
        self.assertEqual(text, "Hello, world.\nIt works.")
        self.assertEqual(fmt, "txt")

    def test_cp1252_smart_quotes(self):
        data = "Alice\u2019s \u201cquote\u201d \u2014 em dash".encode("cp1252")
        text, _ = extract_text(data, "x.txt")
        self.assertEqual(text, "Alice\u2019s \u201cquote\u201d \u2014 em dash")

    def test_empty_raises(self):
        with self.assertRaises(ExtractionError):
            extract_text(b"", "empty.txt")


class ExtractDocxTest(unittest.TestCase):
    def test_paragraphs_tabs_and_runs(self):
        text, fmt = extract_text(_docx(DOCX_XML), "memo.docx")
        self.assertEqual(fmt, "docx")
        self.assertEqual(
            text,
            "First paragraph with a bold-ish run.\nSecond paragraph\tafter tab",
        )

    def test_not_a_zip(self):
        with self.assertRaises(ExtractionError):
            extract_text(b"not a zip at all", "broken.docx")

    def test_zip_without_document_xml(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "hi")
        with self.assertRaises(ExtractionError):
            extract_text(buf.getvalue(), "fake.docx")


class ExtractPdfTest(unittest.TestCase):
    def test_plain_stream(self):
        text, fmt = extract_text(_pdf(STREAM), "report.pdf")
        self.assertEqual(fmt, "pdf")
        self.assertIn("Hello, world.", text)
        self.assertIn("The quick", text)
        self.assertIn("brown fox", text)
        self.assertIn("A (parenthesis) and \\ backslash", text)

    def test_flate_stream(self):
        text, _ = extract_text(_pdf(STREAM, b"/FlateDecode"), "report.pdf")
        self.assertIn("Hello, world.", text)
        self.assertIn("brown fox", text)

    def test_asciihex_stream(self):
        text, _ = extract_text(_pdf(STREAM, b"/ASCIIHexDecode"), "report.pdf")
        self.assertIn("Hello, world.", text)
        self.assertIn("brown fox", text)

    def test_not_a_pdf(self):
        with self.assertRaises(ExtractionError):
            extract_text(b"hello world", "fake.pdf")


class DetectFormatTest(unittest.TestCase):
    def test_by_extension(self):
        self.assertEqual(detect_format("a.docx"), "docx")
        self.assertEqual(detect_format("a.PDF"), "pdf")
        self.assertEqual(detect_format("a.txt"), "txt")
        self.assertEqual(detect_format("a.md"), "md")
        self.assertEqual(detect_format("README.markdown"), "markdown")

    def test_markdown_extracts_as_text(self):
        md = b"# Heading\n\nSome **bold** body text with _emphasis_.\n\n- item one\n- item two\n"
        text, fmt = extract_text(md, "notes.md")
        self.assertEqual(fmt, "md")
        self.assertIn("Heading", text)
        self.assertIn("Some **bold** body text with _emphasis_.", text)
        self.assertIn("item one", text)

    def test_by_magic_bytes(self):
        self.assertEqual(detect_format("noext", b"%PDF-1.7 ..."), "pdf")
        self.assertEqual(detect_format("noext", b"PK\x03\x04rest"), "docx")
        self.assertEqual(detect_format("noext", b"plain text"), "txt")


if __name__ == "__main__":
    unittest.main()
