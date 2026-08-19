"""Extract plain text from TXT, Markdown, DOCX, and PDF files (pure stdlib).

PDF extraction is deliberately best-effort: it pulls text-showing operators
out of content streams (Flate/ASCIIHex/ASCII85 decoded) in file/object order.
Layout, reading order, embedded fonts, and scanned images are not handled --
callers should surface that caveat to users.
"""

from __future__ import annotations

import binascii
import io
import re
import zlib
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

# Formats the extractor understands. Markdown is handled as plain text.
SUPPORTED = ("txt", "md", "markdown", "docx", "pdf")

# cp1252/WinAnsi -> Unicode for bytes 0x80-0x9F (the "smart punctuation" range).
_CP1252 = {
    0x80: "\u20ac", 0x82: "\u201a", 0x83: "\u0192", 0x84: "\u201e", 0x85: "\u2026",
    0x86: "\u2020", 0x87: "\u2021", 0x88: "\u02c6", 0x89: "\u2030", 0x8A: "\u0160",
    0x8B: "\u2039", 0x8C: "\u0152", 0x8E: "\u017d", 0x91: "\u2018", 0x92: "\u2019",
    0x93: "\u201c", 0x94: "\u201d", 0x95: "\u2022", 0x96: "\u2013", 0x97: "\u2014",
    0x98: "\u02dc", 0x99: "\u2122", 0x9A: "\u0161", 0x9B: "\u203a", 0x9C: "\u0153",
    0x9E: "\u017e", 0x9F: "\u0178",
}


class ExtractionError(Exception):
    """Raised when a document's text cannot be extracted."""


def detect_format(filename: str = "", data: Optional[bytes] = None) -> str:
    """Return the format for a document, by extension then by magic bytes."""
    data = data or b""
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext in SUPPORTED:
        return ext
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return "docx"
    return "txt"


def extract_text(data: bytes, filename: str = "") -> Tuple[str, str]:
    """Extract text from *data*, returning ``(text, format)``.

    Raises :class:`ExtractionError` when the document can't be read or yields
    no text.
    """
    fmt = detect_format(filename, data)
    if fmt in ("txt", "md", "markdown"):
        text = _extract_txt(data)
    elif fmt == "docx":
        text = _extract_docx(data)
    elif fmt == "pdf":
        text = _extract_pdf(data)
    else:  # pragma: no cover - detect_format only returns supported formats
        raise ExtractionError(f"unsupported format '{fmt}'")

    text = text.strip()
    if not text:
        raise ExtractionError(
            "no text could be extracted (empty document, or scanned/image-only PDF)"
        )
    return text, fmt


# -- TXT -----------------------------------------------------------------

def _extract_txt(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding).replace("\u00a0", " ")
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")  # never raises


# -- DOCX -----------------------------------------------------------------

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = "{%s}" % _W_NS


def _extract_docx(data: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ExtractionError("not a valid DOCX (zip) archive") from exc

    names = set(zf.namelist())
    if "word/document.xml" not in names:
        raise ExtractionError("DOCX has no word/document.xml (not a Word document?)")

    try:
        root = ET.fromstring(zf.read("word/document.xml"))
    except ET.ParseError as exc:
        raise ExtractionError("DOCX body XML is malformed") from exc

    paragraphs = []
    for para in root.iter(_W + "p"):
        parts: List[str] = []
        for node in para.iter():
            tag = node.tag
            if tag == _W + "t":
                parts.append(node.text or "")
            elif tag == _W + "tab":
                parts.append("\t")
            elif tag in (_W + "br", _W + "cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


# -- PDF ------------------------------------------------------------------

def _extract_pdf(data: bytes) -> str:
    if not data.startswith(b"%PDF-"):
        raise ExtractionError("not a PDF file")

    objects: dict = {}
    for m in re.finditer(rb"(\d+)\s+\d+\s+obj\b(.*?)endobj", data, re.S):
        objects[int(m.group(1))] = m.group(2)
    if not objects:
        raise ExtractionError("PDF contains no parseable objects")

    chunks: List[str] = []
    for body in objects.values():
        stream_match = re.search(rb"stream\r?\n(.*?)endstream", body, re.S)
        if not stream_match:
            continue
        try:
            decoded = _decode_stream(body, stream_match.group(1))
        except Exception:
            continue  # image data, unsupported filter, malformed — skip
        if not decoded:
            continue
        chunks.append(_text_from_content(decoded))

    text = _collapse("\n".join(chunks))
    return text


def _decode_stream(dict_body: bytes, raw: bytes) -> Optional[bytes]:
    """Decode a content stream according to its /Filter entries."""
    raw = raw.rstrip(b"\r\n\t ")
    filters = _filters(dict_body)
    if "dct" in filters:
        return None  # JPEG image stream

    data = raw
    # Filters are listed in application order; decode in reverse.
    for flt in reversed(filters):
        try:
            if flt == "flate":
                data = _flate(data)
            elif flt == "asciihex":
                data = _ascii_hex(data)
            elif flt == "ascii85":
                data = _ascii_85(data)
            else:
                return None  # unknown filter — can't decode
        except Exception:
            return None
    return data


def _filters(dict_body: bytes) -> list:
    """Filter names in application order (order matters when decoding)."""
    m = re.search(rb"/Filter\s*(/FlateDecode|/ASCIIHexDecode|/ASCII85Decode|/DCTDecode|\[[^\]]*\])", dict_body)
    if not m:
        return []
    spec = m.group(1)
    if spec.startswith(b"["):
        names = re.findall(rb"/(FlateDecode|ASCIIHexDecode|ASCII85Decode|DCTDecode)", spec)
    else:
        names = [spec[1:]]  # drop the leading "/"
    return [_norm_filter(n) for n in names]


def _norm_filter(name: bytes) -> str:
    """'FlateDecode' -> 'flate', 'ASCIIHexDecode' -> 'asciihex', ..."""
    raw = name.decode("ascii").lower()
    return raw[:-6] if raw.endswith("decode") else raw


def _flate(data: bytes) -> bytes:
    try:
        return zlib.decompress(data)
    except zlib.error:
        d = zlib.decompressobj(-zlib.MAX_WBITS)  # raw deflate
        return d.decompress(data) + d.flush()


def _ascii_hex(data: bytes) -> bytes:
    cleaned = re.sub(rb"\s", b"", data).rstrip(b">")
    if len(cleaned) % 2:
        cleaned += b"0"
    return binascii.unhexlify(cleaned)


def _ascii_85(data: bytes) -> bytes:
    cleaned = re.sub(rb"\s", b"", data).split(b"~>")[0]
    return binascii.a2b_ascii85(cleaned)


def _text_from_content(content: bytes) -> str:
    """Pull the strings shown by Tj/TJ/' operators out of a content stream."""
    out: List[str] = []
    for kind, tok in _scan_content(content):
        if kind == "str":
            out.append(_decode_pdf_string(tok))
        elif kind == "hex":
            out.append(_decode_hex_string(tok))
        elif kind == "op":
            if tok in (b"Td", b"TD", b"T*", b"Tm", b"'"):
                out.append("\n")
    return _collapse("".join(out))


def _scan_content(content: bytes) -> Iterator[Tuple[str, bytes]]:
    """Tokenize a content stream into strings (literal/hex) and operators."""
    i, n = 0, len(content)
    while i < n:
        c = content[i : i + 1]
        if c in b" \t\r\n\f":
            i += 1
            continue
        if c == b"(":  # literal string, possibly escaped with \ and nested parens
            buf = bytearray()
            depth = 1
            j = i + 1
            while j < n and depth:
                ch = content[j : j + 1]
                if ch == b"\\":
                    if j + 1 >= n:
                        break
                    e = content[j + 1 : j + 2]
                    if e == b"n":
                        buf += b"\n"
                    elif e == b"r":
                        buf += b"\r"
                    elif e == b"t":
                        buf += b"\t"
                    elif e == b"b":
                        buf += b"\b"
                    elif e == b"f":
                        buf += b"\f"
                    elif e in b"()\\":
                        buf += e
                    elif e in b"01234567":  # octal escape, up to 3 digits
                        k = j + 1
                        digits = b""
                        while k < n and len(digits) < 3 and content[k : k + 1] in b"01234567":
                            digits += content[k : k + 1]
                            k += 1
                        buf += bytes([int(digits, 8) & 0xFF])
                        j = k - 1
                    else:
                        buf += e
                    j += 2
                    continue
                if ch == b"(":
                    depth += 1
                    buf += ch
                elif ch == b")":
                    depth -= 1
                    if depth:
                        buf += ch
                else:
                    buf += ch
                j += 1
            yield ("str", bytes(buf))
            i = j
        elif c == b"<":  # hex string
            end = content.find(b">", i)
            if end == -1:
                return
            yield ("hex", content[i + 1 : end])
            i = end + 1
        else:  # operator or number token
            j = i
            while j < n and content[j : j + 1] not in b" \t\r\n\f()<>":
                j += 1
            yield ("op", content[i:j])
            i = j


def _decode_pdf_string(raw: bytes) -> str:
    if raw.startswith(b"\xfe\xff"):
        try:
            return raw[2:].decode("utf-16-be")
        except UnicodeDecodeError:
            pass
    if raw.startswith(b"\xff\xfe"):
        try:
            return raw[2:].decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    return _decode_latin(raw)


def _decode_hex_string(raw: bytes) -> str:
    cleaned = re.sub(rb"\s", b"", raw)
    if len(cleaned) % 2:
        cleaned += b"0"
    try:
        data = binascii.unhexlify(cleaned)
    except (binascii.Error, ValueError):
        return ""
    return _decode_pdf_string(data)


def _decode_latin(raw: bytes) -> str:
    """Decode WinAnsi-ish bytes: cp1252 when possible, latin-1 + fix-up otherwise."""
    try:
        text = raw.decode("cp1252")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
        text = "".join(_CP1252.get(ord(ch), ch) for ch in text)
    return text.replace("\u00a0", " ")


def _collapse(text: str) -> str:
    """Clean operator noise: stray line-start spaces and 2+ blank lines."""
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
