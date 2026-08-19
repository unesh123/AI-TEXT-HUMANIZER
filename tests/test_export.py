"""Round-trip tests for the TXT/DOCX/PDF exporters.

Each exporter must produce a file that :mod:`naturalizer.extract` can read
back, so uploads and downloads stay consistent.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from naturalizer.export import EXPORT_FORMATS, to_bytes
from naturalizer.extract import extract_text

SHORT_TEXT = "These days, technology plays a key role.\n\nOrganizations build on it daily."

LONG_TEXT = (
    "The quick brown fox jumps over the lazy dog while the workers measure the "
    "length of the fence. " * 6
) + "End of the long sample."


class ExportTxtTest(unittest.TestCase):
    def test_round_trip(self):
        text, _ = extract_text(to_bytes(SHORT_TEXT, "txt"), "out.txt")
        self.assertEqual(text, SHORT_TEXT)


class ExportDocxTest(unittest.TestCase):
    def test_round_trip_preserves_paragraphs_and_tabs(self):
        doc = "First line.\n\nSecond\twith a tab.\nThird line."
        data = to_bytes(doc, "docx")
        self.assertTrue(data.startswith(b"PK\x03\x04"))
        text, fmt = extract_text(data, "out.docx")
        self.assertEqual(fmt, "docx")
        self.assertEqual(text, doc)

    def test_escapes_are_safe(self):
        doc = "One <two> & \"three\" 'four'."
        text, _ = extract_text(to_bytes(doc, "docx"), "out.docx")
        self.assertEqual(text, doc)


class ExportPdfTest(unittest.TestCase):
    def test_round_trip_short(self):
        data = to_bytes(SHORT_TEXT, "pdf")
        self.assertTrue(data.startswith(b"%PDF-"))
        text, fmt = extract_text(data, "out.pdf")
        self.assertEqual(fmt, "pdf")
        self.assertEqual(text, SHORT_TEXT)

    def test_long_text_reflows_but_keeps_words(self):
        data = to_bytes(LONG_TEXT, "pdf")
        text, _ = extract_text(data, "out.pdf")
        original_words = LONG_TEXT.split()
        extracted_words = text.split()
        for word in original_words:
            self.assertIn(word, extracted_words)

    def test_unicode_is_not_mangled(self):
        doc = "Prices rose \u2014 a lot \u2014 she said."
        text, _ = extract_text(to_bytes(doc, "pdf"), "out.pdf")
        self.assertIn("\u2014", text)
        self.assertIn("she said.", text)


class ExportValidationTest(unittest.TestCase):
    def test_unknown_format_raises(self):
        with self.assertRaises(ValueError):
            to_bytes("hi", "nope")

    def test_supported_formats(self):
        self.assertEqual(EXPORT_FORMATS, ("txt", "docx", "pdf"))


if __name__ == "__main__":
    unittest.main()
