"""Render plain text back out as TXT, DOCX, or PDF (pure stdlib).

The DOCX writer emits a minimal-but-valid WordprocessingML package. The PDF
writer emits a simple Helvetica text reflow (no original layout is preserved
-- the rewritten text is re-wrapped onto letter pages). Both round-trip
through :mod:`naturalizer.extract`.
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

# Formats the exporter can render. (text/plain, docx, pdf)
EXPORT_FORMATS = ("txt", "docx", "pdf")

_CONTENT_TYPES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

_FILE_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "docx": _CONTENT_TYPES,
    "pdf": "application/pdf",
}


def to_bytes(text: str, fmt: str) -> bytes:
    """Render *text* into the given format. Raises ValueError for bad *fmt*."""
    if fmt == "txt":
        return _to_txt(text)
    if fmt == "docx":
        return _to_docx(text)
    if fmt == "pdf":
        return _to_pdf(text)
    raise ValueError(f"unknown format '{fmt}' (expected one of {EXPORT_FORMATS})")


def content_type(fmt: str) -> str:
    """HTTP Content-Type for a format (falls back to octet-stream)."""
    return _FILE_TYPES.get(fmt, "application/octet-stream")


# -- TXT ------------------------------------------------------------------

def _to_txt(text: str) -> bytes:
    return text.encode("utf-8")


# -- DOCX ------------------------------------------------------------------

_DOCX_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    f'<Override PartName="/word/document.xml" ContentType="{_CONTENT_TYPES}.main+xml"/>'
    "</Types>"
)

_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def _to_docx(text: str) -> bytes:
    paragraphs = []
    for para in text.split("\n"):
        runs = []
        for idx, seg in enumerate(para.split("\t")):
            if idx:
                runs.append("<w:tab/>")
            if seg:
                runs.append(f'<w:r><w:t xml:space="preserve">{escape(seg)}</w:t></w:r>')
        paragraphs.append(f"<w:p>{''.join(runs)}</w:p>")

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{_DOCX_NS}"><w:body>'
        + "".join(paragraphs)
        + "</w:body></w:document>"
    ).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", _RELS_XML)
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


# -- PDF ------------------------------------------------------------------

def _to_pdf(text: str) -> bytes:
    lines = _wrap(text, width=90) or [""]
    per_page = 52
    pages = [lines[i : i + per_page] for i in range(0, len(lines), per_page)]

    font_num = 4
    page_nums = list(range(3, 3 + len(pages)))
    content_nums = list(range(font_num + 1, font_num + 1 + len(pages)))

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (b" ".join(b"%d 0 R" % n for n in page_nums), len(pages)),
    ]
    for pn, cn in zip(page_nums, content_nums):
        objs.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (font_num, cn)
        )
    objs.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    for chunk in pages:
        content = _pdf_content(chunk)
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")

    return _serialize_pdf(objs)


def _pdf_content(lines: list) -> bytes:
    out = bytearray(b"BT\n/F1 11 Tf\n50 730 Td\n13.5 TL\n")
    for line in lines:
        out += b"(" + _escape_pdf_line(line) + b") Tj\nT*\n"
    out += b"ET"
    return bytes(out)


def _escape_pdf_line(line: str) -> bytes:
    raw = line.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _wrap(text: str, width: int) -> list:
    lines = []
    for para in text.split("\n"):
        words = para.split()
        if not words:
            lines.append("")
            continue
        line = ""
        for word in words:
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def _serialize_pdf(objects: list) -> bytes:
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
