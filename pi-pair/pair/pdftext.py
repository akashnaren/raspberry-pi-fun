"""Pull visible text out of a small PDF. No poppler and no third-party parser.

Page content is the text operators Tj and TJ. FlateDecode streams are
inflated with zlib. A JPEG scan has no text operators and returns nothing,
so the upload path can still OCR that file.
"""
from __future__ import annotations

import re
import zlib

_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\n?endstream", re.S)
_TJ = re.compile(rb"\((?:\\.|[^)\\])*\)\s*Tj")
_ARRAY = re.compile(rb"\[(.*?)\]\s*TJ", re.S)
_LIT = re.compile(rb"\((?:\\.|[^)\\])*\)")


def extract_pdf_text(data: bytes) -> str:
    """Joined page text, or empty when the file has no text operators."""
    if not data or not data.startswith(b"%PDF-"):
        return ""
    parts: list[str] = []
    for match in _STREAM.finditer(data):
        raw = match.group(1)
        head = data[max(0, match.start() - 240) : match.start()]
        if b"FlateDecode" in head:
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                continue
        found = _operators(raw)
        if found:
            parts.append(found)
    return " ".join(parts).strip()


def _unescape(token: bytes) -> str:
    body = token[1:-1]
    out = bytearray()
    index = 0
    while index < len(body):
        char = body[index]
        if char != 0x5C or index + 1 >= len(body):
            out.append(char)
            index += 1
            continue
        nxt = body[index + 1]
        mapping = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12, ord("("): 40, ord(")"): 41, ord("\\"): 92}
        if nxt in mapping:
            out.append(mapping[nxt])
            index += 2
            continue
        if 48 <= nxt <= 55:
            digits = body[index + 1 : index + 4]
            take = 0
            value = 0
            for piece in digits:
                if not 48 <= piece <= 55:
                    break
                value = (value << 3) + (piece - 48)
                take += 1
            out.append(value & 0xFF)
            index += 1 + take
            continue
        out.append(nxt)
        index += 2
    return out.decode("utf-8", "replace").strip()


def _operators(raw: bytes) -> str:
    words: list[str] = []
    for match in _TJ.finditer(raw):
        word = _unescape(match.group(0).split(None, 1)[0])
        if word:
            words.append(word)
    for match in _ARRAY.finditer(raw):
        for literal in _LIT.finditer(match.group(1)):
            word = _unescape(literal.group(0))
            if word:
                words.append(word)
    return " ".join(words).strip()
