from __future__ import annotations

import gzip
import hashlib
import threading
from pathlib import Path

from pair.core.config import STATIC_DIR
from pair.model.modes import mode_tips
from pair.routes.base import safe_write

_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".txt": "text/plain; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
}


def encoded_body(handler, body: bytes) -> tuple[bytes, str | None]:
    """Gzip only when the browser asks. Tests that omit the header stay plain."""
    accept = handler.headers.get("Accept-Encoding") or ""
    if "gzip" not in accept.lower() or len(body) < 800:
        return body, None
    packed = gzip.compress(body, compresslevel=6)
    if len(packed) >= len(body):
        return body, None
    return packed, "gzip"


# (path, mtime_ns, size, gzip_ok) -> (body, encoding, etag). Raw bytes, not gzip.
_STATIC_CACHE: dict[tuple[str, int, int, bool], tuple[bytes, str | None, str]] = {}
_STATIC_LOCK = threading.Lock()
_STATIC_CAP = 64


def _asset_etag(raw: bytes) -> str:
    return '"' + hashlib.sha1(raw).hexdigest()[:16] + '"'


def static_file(url_path: str) -> Path | None:
    if not url_path.startswith("/static/"):
        return None
    name = url_path[len("/static/") :]
    parts = name.split("/")
    if not parts or any(part in ("", ".", "..") or "\\" in part for part in parts):
        return None
    if len(parts) == 2 and parts[0] == "fonts":
        rel = parts
    elif len(parts) == 1:
        rel = parts
    else:
        return None
    root = STATIC_DIR.resolve()
    candidate = root.joinpath(*rel).resolve()
    allowed = {root, (root / "fonts").resolve()}
    if candidate.parent not in allowed or not candidate.is_file():
        return None
    return candidate


def index_body() -> bytes:
    """Fill the page from config: Flash and Pro tip text. Model tags stay off it."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    tips = mode_tips()
    html = html.replace("__FLASH_TIP__", tips["flash"])
    html = html.replace("__PRO_TIP__", tips["pro"])
    return html.encode("utf-8")


class StaticRoutes:
    def _wants_gzip(self) -> bool:
        accept = self.headers.get("Accept-Encoding") or ""
        return "gzip" in accept.lower()

    def _etag_matches(self, etag: str) -> bool:
        header = (self.headers.get("If-None-Match") or "").strip()
        if not header:
            return False
        if header == "*":
            return True
        return etag in {part.strip() for part in header.split(",") if part.strip()}

    def _cached_static(
        self, key: tuple[str, int, int, bool], load_raw
    ) -> tuple[bytes, str | None, str]:
        """Re-gzip a static body only when the file or the encoding changes."""
        with _STATIC_LOCK:
            hit = _STATIC_CACHE.get(key)
        if hit is not None:
            return hit
        raw = load_raw()
        body, encoding = encoded_body(self, raw)
        entry = (body, encoding, _asset_etag(raw))
        with _STATIC_LOCK:
            if key not in _STATIC_CACHE and len(_STATIC_CACHE) >= _STATIC_CAP:
                _STATIC_CACHE.pop(next(iter(_STATIC_CACHE)), None)
            _STATIC_CACHE.setdefault(key, entry)
            return _STATIC_CACHE[key]

    def _send_cached(
        self, content_type: str, key: tuple[str, int, int, bool], load_raw
    ) -> None:
        body, encoding, etag = self._cached_static(key, load_raw)
        if self._etag_matches(etag):
            self.send_response(304)
            self._cors()
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            if encoding:
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("content-length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self._cors()
        self.send_header("content-type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", etag)
        if encoding:
            self.send_header("Content-Encoding", encoding)
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)
