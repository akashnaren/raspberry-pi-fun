"""Pull visible text out of a small PDF. No poppler and no third-party parser.

Page content is the text operators Tj and TJ, including hex strings such as
`<4869> Tj`. Each BT…ET block becomes one line. FlateDecode streams are
inflated with zlib, and ASCII85 / ASCIIHex wrappers are decoded first.
Any other filter is skipped. A JPEG scan has no text operators and returns
nothing, so the upload path can still OCR that file.

Inflation and the operator scan are bounded. Image, font, and metadata
streams are skipped from their dictionaries, and a missing `endstream` or
`ET` costs one pass. The request thread must stay able to serve `/health`.
"""

from __future__ import annotations

import base64
import binascii
import re
import zlib

_INFLATE_CAP = 1_000_000
_TOTAL_CAP = 4_000_000
_FILTER = re.compile(rb"/Filter\s*(\[[^\]]+\]|/[A-Za-z0-9]+)")
_WS = b" \t\r\n\f\v"
_HEX = b"0123456789abcdefABCDEF"


def _word(byte: int) -> bool:
    """ASCII word byte, matching the `\\b` a PDF token boundary uses."""
    return byte == 95 or 48 <= byte <= 57 or 65 <= byte <= 90 or 97 <= byte <= 122


def _bounded(data: bytes, index: int, token: bytes) -> bool:
    end = index + len(token)
    if data[index:end] != token:
        return False
    if index > 0 and _word(data[index - 1]):
        return False
    if end < len(data) and _word(data[end]):
        return False
    return True


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


def _dict_window(head: bytes) -> bytes:
    if not head:
        return b""
    start = head.rfind(b"<<")
    return head[start:] if start >= 0 else head


def _has_name(window: bytes, key: bytes, value: bytes) -> bool:
    return (
        re.search(rb"/" + re.escape(key) + rb"\s*/" + re.escape(value) + rb"\b", window)
        is not None
    )


def _skip_stream(head: bytes) -> bool:
    """True when this dictionary is not page content. Skip before any decode."""
    window = _dict_window(head)
    if not window:
        return False
    if re.search(rb"/FontFile[23]?\b", window):
        return True
    if re.search(rb"/Length1\b", window):
        return True
    if _has_name(window, b"Type", b"Metadata"):
        return True
    if _has_name(window, b"Type", b"XRef"):
        return True
    if _has_name(window, b"Type", b"ObjStm"):
        return True
    if _has_name(window, b"Subtype", b"Image"):
        return True
    if _has_name(window, b"Type", b"XObject") and _has_name(
        window, b"Subtype", b"Image"
    ):
        return True
    if _has_name(window, b"Subtype", b"Form") and re.search(rb"/Image\b", window):
        return True
    return False


def _inflate(raw: bytes) -> bytes | None:
    """Inflate zlib or raw deflate, capped so a zip-bomb cannot expand."""
    candidates = (raw, raw.strip(_WS))
    seen: set[bytes] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        for bits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                out = zlib.decompressobj(bits).decompress(candidate, _INFLATE_CAP)
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


def _iter_streams(data: bytes):
    """Yield `(body, head)` for each `stream` … `endstream`.

    A missing `endstream` yields the tail once and stops. `stream` inside
    the word `endstream` is not a new stream.
    """
    index = 0
    limit = len(data)
    while index < limit:
        start = data.find(b"stream", index)
        if start < 0:
            return
        if start >= 3 and data[start - 3 : start] == b"end":
            index = start + 6
            continue
        after = start + 6
        if data.startswith(b"\r\n", after):
            body_at = after + 2
        elif data.startswith(b"\n", after):
            body_at = after + 1
        else:
            index = start + 6
            continue
        end = data.find(b"endstream", body_at)
        head = data[max(0, start - 800) : start]
        if end < 0:
            yield data[body_at:], head
            return
        body_end = end
        if body_end > body_at and data[body_end - 1 : body_end] == b"\n":
            body_end -= 1
            if body_end > body_at and data[body_end - 1 : body_end] == b"\r":
                body_end -= 1
        yield data[body_at:body_end], head
        index = end + 9


def extract_pdf_text(data: bytes) -> str:
    """Joined page text, or empty when the file has no text operators."""
    if not data or not data.startswith(b"%PDF-"):
        return ""
    parts: list[str] = []
    total = 0
    for raw, head in _iter_streams(data):
        if _skip_stream(head):
            continue
        decoded = _apply_filters(raw, _filters(head))
        if not decoded:
            continue
        total += len(decoded)
        found = _operators(decoded)
        if found:
            parts.append(found)
        if total >= _TOTAL_CAP:
            break
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


def _close_at(raw: bytes, start: int, closer: int) -> int | None:
    """Index of the matching closer. A missing closer stops the scan once."""
    index = start + 1
    limit = len(raw)
    while index < limit:
        byte = raw[index]
        if byte == 0x5C:
            index += 2
            continue
        if byte == closer:
            return index
        index += 1
    return None


def _skip_ws(raw: bytes, index: int) -> int:
    limit = len(raw)
    while index < limit and raw[index] in _WS:
        index += 1
    return index


def _token_at(raw: bytes, index: int, token: bytes) -> bool:
    if not raw.startswith(token, index):
        return False
    end = index + len(token)
    return end >= len(raw) or not _word(raw[end])


def _array_text(body: bytes) -> str:
    words: list[str] = []
    index = 0
    limit = len(body)
    while index < limit:
        byte = body[index]
        if byte == 0x28:
            end = _close_at(body, index, 0x29)
            if end is None:
                break
            word = _unescape(body[index : end + 1])
            if word:
                words.append(word)
            index = end + 1
            continue
        if byte == 0x3C:
            end = body.find(b">", index + 1)
            if end < 0:
                break
            word = _hex_word(body[index + 1 : end])
            if word:
                words.append(word)
            index = end + 1
            continue
        index += 1
    return " ".join(words).strip()


def _block_text(raw: bytes) -> str:
    """Tj / TJ words in order. Each `[` stops at the next `]`, or the scan stops."""
    words: list[str] = []
    index = 0
    limit = len(raw)
    while index < limit:
        byte = raw[index]
        if byte == 0x28:
            end = _close_at(raw, index, 0x29)
            if end is None:
                break
            nxt = _skip_ws(raw, end + 1)
            if _token_at(raw, nxt, b"Tj"):
                word = _unescape(raw[index : end + 1])
                if word:
                    words.append(word)
                index = nxt + 2
                continue
            index = end + 1
            continue
        if byte == 0x3C:
            if index + 1 < limit and raw[index + 1] == 0x3C:
                index += 2
                continue
            end = index + 1
            hex_ok = True
            while end < limit and raw[end] != 0x3E:
                if raw[end] not in _HEX and raw[end] not in _WS:
                    hex_ok = False
                    break
                end += 1
            if not hex_ok or end >= limit:
                index += 1
                continue
            nxt = _skip_ws(raw, end + 1)
            if _token_at(raw, nxt, b"Tj"):
                word = _hex_word(raw[index + 1 : end])
                if word:
                    words.append(word)
                index = nxt + 2
                continue
            index = end + 1
            continue
        if byte == 0x5B:
            end = _close_at(raw, index, 0x5D)
            if end is None:
                break
            nxt = _skip_ws(raw, end + 1)
            if _token_at(raw, nxt, b"TJ"):
                word = _array_text(raw[index + 1 : end])
                if word:
                    words.append(word)
                index = nxt + 2
                continue
            index = end + 1
            continue
        index += 1
    return " ".join(words).strip()


def _bt_chunks(raw: bytes) -> list[bytes]:
    """Bodies of BT…ET. A missing ET takes the tail once."""
    chunks: list[bytes] = []
    index = 0
    limit = len(raw)
    while index < limit:
        found = raw.find(b"BT", index)
        if found < 0:
            break
        if not _bounded(raw, found, b"BT"):
            index = found + 1
            continue
        end = found + 2
        close = -1
        while end < limit:
            end = raw.find(b"ET", end)
            if end < 0:
                break
            if _bounded(raw, end, b"ET"):
                close = end
                break
            end += 1
        if close < 0:
            chunks.append(raw[found + 2 :])
            break
        chunks.append(raw[found + 2 : close])
        index = close + 2
    return chunks


def _printable_line(text: str) -> bool:
    """Drop a line that is mostly binary that happened to contain Tj."""
    if not text:
        return False
    good = sum(1 for char in text if char.isprintable() or char.isspace())
    return good / len(text) >= 0.6


def _operators(raw: bytes) -> str:
    if b"Tj" not in raw and b"TJ" not in raw:
        return ""
    chunks = _bt_chunks(raw) or [raw]
    lines = [
        text
        for chunk in chunks
        if (text := _block_text(chunk)) and _printable_line(text)
    ]
    return "\n".join(lines).strip()
