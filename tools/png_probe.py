#!/usr/bin/env python
"""Probe a PNG screenshot for rendered highlight colors (pure stdlib).

Decodes the PNG (IHDR + IDAT + filter reconstruction, 8-bit RGB/RGBA)
and counts pixels near the Naturalizer diff highlight colors, so we can
confirm the word-level diff actually paints in the browser render rather
than only being declared in CSS.

Usage:  python tools/png_probe.py <png> [--del rgb --add rgb --out json]
"""

from __future__ import annotations

import json
import struct
import sys
import zlib


def _chunks(data: bytes):
    pos = 8  # skip PNG signature
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        yield ctype, body
        pos += 12 + length


def decode_png(data: bytes):
    """Return (width, height, bytes-per-pixel, scanlines[height][row]) as bytes rows."""
    width = height = bpp = None
    raw = bytearray()
    for ctype, body in _chunks(data):
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", body[:10])
            if bit_depth != 8:
                raise ValueError(f"unsupported bit depth {bit_depth}")
            channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
            bpp = channels
        elif ctype == b"IDAT":
            raw += body
        elif ctype == b"IEND":
            break
    if width is None or bpp is None:
        raise ValueError("no IHDR/IDAT found")
    stride = width * bpp
    decompressed = zlib.decompress(bytes(raw))
    rows: list[bytearray] = []
    pos = 0
    prev = bytearray(stride)
    for _ in range(height):
        filt = decompressed[pos]
        pos += 1
        line = bytearray(decompressed[pos : pos + stride])
        pos += stride
        if filt == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filt == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filt == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filt == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        rows.append(line)
        prev = line
    return width, height, bpp, rows


def count_color(rows, bpp, target, tol=12, x0=None, x1=None):
    """Count pixels within tolerance of target across (optionally x-clipped) rows."""
    n = 0
    total = 0
    for row in rows:
        for x in range(0, len(row), bpp):
            if x0 is not None and x // bpp < x0:
                continue
            if x1 is not None and x // bpp >= x1:
                continue
            total += 1
            if all(abs(row[x + c] - target[c]) <= tol for c in range(3)):
                n += 1
    return n, total


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a: b for a, b in zip(sys.argv[1::2], sys.argv[2::2]) if a.startswith("--")}
    if not args:
        print(__doc__)
        return 2
    path = args[0]
    with open(path, "rb") as fh:
        data = fh.read()
    w, h, bpp, rows = decode_png(data)
    del_color = tuple(int(flags.get("--del", "153,27,27").split(",")[i]) for i in range(3))
    add_color = tuple(int(flags.get("--add", "22,101,52").split(",")[i]) for i in range(3))
    del_bg = tuple(int(flags.get("--delbg", "253,226,226").split(",")[i]) for i in range(3))
    add_bg = tuple(int(flags.get("--addbg", "220,252,231").split(",")[i]) for i in range(3))
    mid = w // 2
    result = {
        "file": path,
        "size": f"{w}x{h}",
        "del_text_pixels": count_color(rows, bpp, del_color)[0],
        "del_bg_pixels": count_color(rows, bpp, del_bg)[0],
        "del_bg_pixels_left_half": count_color(rows, bpp, del_bg, x0=0, x1=mid)[0],
        "del_bg_pixels_right_half": count_color(rows, bpp, del_bg, x0=mid, x1=w)[0],
        "add_text_pixels": count_color(rows, bpp, add_color)[0],
        "add_bg_pixels": count_color(rows, bpp, add_bg)[0],
        "add_bg_pixels_left_half": count_color(rows, bpp, add_bg, x0=0, x1=mid)[0],
        "add_bg_pixels_right_half": count_color(rows, bpp, add_bg, x0=mid, x1=w)[0],
    }
    if flags.get("--out"):
        with open(flags["--out"], "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
