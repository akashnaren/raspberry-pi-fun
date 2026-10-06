"""Pull visible text out of a small PDF. No poppler and no third-party parser.

Page content is the text operators Tj and TJ, including hex strings such as
`<4869> Tj`. Each BT…ET block becomes one line. FlateDecode streams are
inflated with zlib, and ASCII85 / ASCIIHex wrappers are decoded first.
Any other filter is skipped. A JPEG scan has no text operators and returns
nothing, so the upload path can still OCR that file.
"""

from __future__ import annotations

import base64
import binascii
import re
import zlib

_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\n?endstream", re.S)
_FILTER = re.compile(rb"/Filter\s*(\[[^\]]+\]|/[A-Za-z0-9]+)")
_BT = re.compile(rb"\bBT\b(.*?)\bET\b", re.S)
_ITEM = re.compile(
    rb"(\((?:\\.|[^)\\])*\))\s*Tj\b"
    rb"|<([0-9A-Fa-f\s]*)>\s*Tj\b"
    rb"|\[(.*?)\]\s*TJ\b",
    re.S,
)
_PIECE = re.compile(rb"(\((?:\\.|[^)\\])*\))|<([0-9A-Fa-f\s]*)>")
_WS = b" \t\r\n\f\v"


def _filters(head: bytes) -> list[bytes]:
    """Filter names from the dictionary that opens this stream."""
    if not head:
        return []
    start = head.rfind(b"<<")
    window = head[start:] if start >= 0 else head
    match = _FILTER.search(window)
    if not match:
        return []
    return re.findall(rb"/([A-Za-z0-9]+)", match.group(1))


def _inflate(raw: bytes) -> bytes | None:
    """Inflate zlib or raw deflate. Trailing bytes are leftover, not an error."""
    candidates = (raw, raw.strip(_WS))
    seen: set[bytes] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        for bits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                out = zlib.decompressobj(bits).decompress(candidate)
            except zlib.error:
                continue
            if out:
                return out
    return None


def _ascii85(raw: bytes) -> bytes | None:
    body = bytes(char for char in raw if char not in _WS)
    if body.startswith(b"<~"):
        body = body[2:]
    if body.endswith(b"~>"):
        body = body[:-2]
    if not body:
        return None
    try:
        return base64.a85decode(body, adobe=False)
    except (ValueError, binascii.Error):
        return None


def _asciihex(raw: bytes) -> bytes | None:
    text = raw.split(b">", 1)[0]
    digits = bytes(char for char in text if char not in _WS)
    if not digits:
        return None
    if len(digits) % 2:
        digits += b"0"
    try:
        return binascii.unhexlify(digits)
    except binascii.Error:
        return None


def _apply_filters(raw: bytes, names: list[bytes]) -> bytes | None:
    """Decode a content stream. Decode filters run in the listed order."""
    data: bytes | None = raw
    if not names:
        inflated = _inflate(data or b"")
        return inflated if inflated is not None else data
    for name in names:
        if data is None:
            return None
        if name == b"ASCII85Decode":
            data = _ascii85(data)
        elif name == b"FlateDecode":
            data = _inflate(data)
        elif name == b"ASCIIHexDecode":
            data = _asciihex(data)
        else:
            return None
    return data


def extract_pdf_text(data: bytes) -> str:
    """Joined page text, or empty when the file has no text operators."""
    if not data or not data.startswith(b"%PDF-"):
        return ""
    parts: list[str] = []
    for match in _STREAM.finditer(data):
        raw = match.group(1)
        head = data[max(0, match.start() - 800) : match.start()]
        decoded = _apply_filters(raw, _filters(head))
        if not decoded:
            continue
        found = _operators(decoded)
        if found:
            parts.append(found)
    return "\n".join(parts).strip()


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
        mapping = {
            ord("n"): 10,
            ord("r"): 13,
            ord("t"): 9,
            ord("b"): 8,
            ord("f"): 12,
            ord("("): 40,
            ord(")"): 41,
            ord("\\"): 92,
        }
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
    return out.decode("utf-8", "replace").replace("\x00", "").strip()


def _hex_word(body: bytes) -> str:
    digits = bytes(char for char in body if char not in _WS)
    if not digits:
        return ""
    if len(digits) % 2:
        digits += b"0"
    try:
        word = binascii.unhexlify(digits)
    except binascii.Error:
        return ""
    return word.decode("utf-8", "replace").replace("\x00", "").strip()


def _array_text(body: bytes) -> str:
    words: list[str] = []
    for match in _PIECE.finditer(body):
        if match.group(1) is not None:
            word = _unescape(match.group(1))
        else:
            word = _hex_word(match.group(2) or b"")
        if word:
            words.append(word)
    return " ".join(words).strip()


def _block_text(raw: bytes) -> str:
    words: list[str] = []
    for match in _ITEM.finditer(raw):
        if match.group(1) is not None:
            word = _unescape(match.group(1))
        elif match.group(2) is not None:
            word = _hex_word(match.group(2))
        else:
            word = _array_text(match.group(3) or b"")
        if word:
            words.append(word)
    return " ".join(words).strip()


def _operators(raw: bytes) -> str:
    found = list(_BT.finditer(raw))
    chunks = [match.group(1) for match in found] or [raw]
    lines = [text for chunk in chunks if (text := _block_text(chunk))]
    return "\n".join(lines).strip()
