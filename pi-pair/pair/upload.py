"""Turn one upload into text the chat can embed.

.txt and .md are decoded as text. An image, or a PDF that embeds a JPEG
scan, is OCR'd on this machine first. Both finish as the same string the
composer sends on /v1/chat/completions. There is no cloud OCR call.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from pair import ocr
from pair.pdftext import extract_pdf_text
from pair.turn import neutralize

MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_TEXT_CHARS = 4096

TEXT_EXTS = frozenset({".txt", ".md", ".markdown"})
TEXT_MIMES = frozenset({"text/plain", "text/markdown", "text/x-markdown"})
IMAGE_EXTS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp"}
)
IMAGE_MIMES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/gif",
        "image/webp",
        "image/tiff",
        "image/bmp",
        "image/x-ms-bmp",
    }
)


class UploadRejected(Exception):
    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def safe_name(name: str) -> str:
    base = (name or "").replace("\\", "/").split("/")[-1].strip()
    if not base or base in {".", ".."}:
        return "attachment"
    return base[:180]


def _ext(name: str) -> str:
    return Path(safe_name(name)).suffix.lower()


def _mime(mime: str) -> str:
    return (mime or "").split(";")[0].strip().lower()


def _image_magic(data: bytes) -> bool:
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith((b"GIF87a", b"GIF89a")):
        return True
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return True
    return False


def _looks_like_image(ext: str, kind: str, data: bytes) -> bool:
    if ext in IMAGE_EXTS or kind in IMAGE_MIMES or kind.startswith("image/"):
        return True
    return _image_magic(data)


def _is_pdf(ext: str, kind: str, data: bytes) -> bool:
    return ext == ".pdf" or kind == "application/pdf" or data.startswith(b"%PDF-")


def is_jpeg_scanned_pdf(data: bytes) -> bool:
    """A PDF page that is a JPEG image (DCTDecode), not a thumbnail inside text."""
    if not data.startswith(b"%PDF-"):
        return False
    return b"DCTDecode" in data


def route_for(name: str, mime: str, data: bytes) -> str:
    """Return 'text', 'pdf', or 'ocr'.

    .txt and .md are text even when the browser sends a generic MIME.
    A PDF with text operators is 'pdf'. A JPEG scan with no text is 'ocr'.
    """
    ext = _ext(name)
    kind = _mime(mime)
    if ext in TEXT_EXTS:
        return "text"
    if kind in TEXT_MIMES and ext not in IMAGE_EXTS and ext != ".pdf":
        return "text"
    if _is_pdf(ext, kind, data):
        if data.startswith(b"%PDF-"):
            if extract_pdf_text(data):
                return "pdf"
            if is_jpeg_scanned_pdf(data):
                return "ocr"
            raise UploadRejected("that PDF has no readable text", 415)
        if _looks_like_image(ext, kind, data):
            return "ocr"
        raise UploadRejected("only a JPEG-scanned PDF can be read", 415)
    if _looks_like_image(ext, kind, data):
        return "ocr"
    raise UploadRejected("unsupported file type", 415)


def ocr_kind(name: str, mime: str, data: bytes) -> str:
    """'pdf' when poppler must rasterize first, otherwise 'image'."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    return "image"


def cap_text(text: str) -> tuple[str, bool]:
    cleaned = neutralize(
        text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    ).strip()
    limit = MAX_TEXT_CHARS
    if len(cleaned) <= limit:
        return cleaned, False
    return cleaned[:limit].rstrip(), True


def decode_text(data: bytes) -> str:
    return data.decode("utf-8-sig", "replace").replace("\x00", "")


def _boundary(content_type: str) -> str:
    match = re.search(r'boundary="?([^";]+)"?', content_type, re.IGNORECASE)
    if not match or not match.group(1).strip():
        raise UploadRejected("missing multipart boundary", 400)
    return match.group(1).strip()


def _filename(disposition: str) -> str:
    starred = re.search(r"filename\*=([^;]+)", disposition, re.IGNORECASE)
    if starred:
        raw = starred.group(1).strip().strip('"')
        if "''" in raw:
            raw = raw.split("''", 1)[1]
        return unquote(raw)
    quoted = re.search(r'filename="([^"]*)"', disposition, re.IGNORECASE)
    if quoted:
        return quoted.group(1)
    plain = re.search(r"filename=([^;]+)", disposition, re.IGNORECASE)
    if plain:
        return plain.group(1).strip().strip('"')
    return ""


def _parts(body: bytes, boundary: str) -> list[tuple[dict[str, str], bytes]]:
    token = b"--" + boundary.encode("latin1", "replace")
    found: list[tuple[dict[str, str], bytes]] = []
    for chunk in body.split(token):
        if not chunk or chunk.startswith(b"--"):
            continue
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        elif chunk.startswith(b"\n"):
            chunk = chunk[1:]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        elif chunk.endswith(b"\n"):
            chunk = chunk[:-1]
        head, sep, data = chunk.partition(b"\r\n\r\n")
        if not sep:
            head, sep, data = chunk.partition(b"\n\n")
        if not sep:
            continue
        headers: dict[str, str] = {}
        for line in head.splitlines():
            if b":" not in line:
                continue
            key, value = line.split(b":", 1)
            headers[key.decode("latin1").lower().strip()] = value.decode(
                "latin1"
            ).strip()
        found.append((headers, data))
    return found


def file_part(body: bytes, content_type: str) -> tuple[str, str, bytes]:
    parts = _parts(body, _boundary(content_type))
    chosen = None
    for headers, data in parts:
        disposition = headers.get("content-disposition", "")
        if _filename(disposition):
            chosen = (headers, data)
            break
    if chosen is None and len(parts) == 1:
        chosen = parts[0]
    if chosen is None:
        raise UploadRejected("attachment must be a file", 400)
    headers, data = chosen
    name = safe_name(_filename(headers.get("content-disposition", "")) or "attachment")
    return name, headers.get("content-type", ""), data


def _discard(read, count: int) -> None:
    left = count
    while left > 0:
        chunk = read(min(65536, left))
        if not chunk:
            return
        left -= len(chunk)


def read_limited(content_length: str | None, read) -> bytes:
    """Read a body up to MAX_UPLOAD_BYTES. Over the cap raises 413."""
    cap = MAX_UPLOAD_BYTES
    if content_length is None or content_length == "":
        chunks = []
        total = 0
        while total <= cap:
            block = read(min(65536, cap + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
        data = b"".join(chunks)
        if len(data) > cap:
            raise UploadRejected("attachment is over 4 MB", 413)
        return data
    try:
        length = int(content_length)
    except ValueError:
        raise UploadRejected("bad content-length", 400) from None
    if length < 0:
        raise UploadRejected("bad content-length", 400)
    if length > cap:
        if length <= 8 * 1024 * 1024:
            read(length)
        else:
            _discard(read, cap + 1)
        raise UploadRejected("attachment is over 4 MB", 413)
    return read(length)


def ingest(content_type: str, body: bytes, filename: str = "") -> dict:
    """Route one body to text or local OCR and return the chat string."""
    if body is None:
        raise UploadRejected("attachment is empty", 400)
    if len(body) > MAX_UPLOAD_BYTES:
        raise UploadRejected("attachment is over 4 MB", 413)
    if not body:
        raise UploadRejected("attachment is empty", 400)
    if (content_type or "").lower().startswith("multipart/"):
        name, mime, data = file_part(body, content_type)
    else:
        name = safe_name(filename or "attachment")
        mime = content_type or ""
        data = body
    if not data:
        raise UploadRejected("attachment is empty", 400)
    route = route_for(name, mime, data)
    if route == "text":
        raw = decode_text(data)
    elif route == "pdf":
        raw = extract_pdf_text(data)
    else:
        if not ocr.try_acquire():
            raise UploadRejected("OCR is busy", 429)
        try:
            try:
                if ocr_kind(name, mime, data) == "pdf":
                    raw = ocr.recognize_pdf(data)
                else:
                    raw = ocr.recognize_image(data)
            except ocr.OcrNotInstalled:
                raise UploadRejected("OCR is not installed on this Pi", 503) from None
            except ocr.OcrFailed:
                raise UploadRejected("could not read that file", 422) from None
        finally:
            ocr.release()
    text, truncated = cap_text(str(raw or ""))
    if not text:
        raise UploadRejected("no text in that file", 422)
    kind = _mime(mime) or "application/octet-stream"
    return {
        "ok": True,
        "name": name,
        "mime": kind,
        "route": route,
        "text": text,
        "chars": len(text),
        "truncated": truncated,
    }
