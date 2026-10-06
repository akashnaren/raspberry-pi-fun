"""Wikimedia image cards for a finished chat turn.

Runs on pi2, or on pi3 when pi2 is down. It builds English Wikipedia API
URLs itself, keeps a small cache, and returns at most four cards. A miss
is an empty list. The page asks only after the answer is already on screen.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
from ipaddress import ip_address, ip_network
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

SEARCH_API = "https://en.wikipedia.org/w/api.php"
SUMMARY_API = "https://en.wikipedia.org/api/rest_v1/page/summary/"
USER_AGENT = "PiGPT/1.0 (local chat image cards; +https://github.com/akashnaren/raspberry-pi-fun)"
FETCH_TIMEOUT = 2.5
JOB_BUDGET_S = 4.0
MAX_CARDS = 4
MAX_CANDIDATES = 6
HIT_TTL_S = 24 * 60 * 60
MISS_TTL_S = 60 * 60
SEARCH_TTL_S = 10 * 60
_BODY_CAP = 200_000
_IMAGE_HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org"}
_CGNAT = ip_network("100.64.0.0/10")
_FENCE_CLOSED = re.compile(r"```[\s\S]*?```")
_FENCE_OPEN = re.compile(r"```[\s\S]*\Z")
_ITEM_LINE = re.compile(r"(?m)^\s*(?:\d{1,2}[.)]\s+|[-*]\s+)(.+)$")
_HEAD_CUT = re.compile(r"\s+[—–]\s+|\s+-\s+|:\s+|\s+\(")
_BOLD = re.compile(r"\*\*([^*\n]{2,80})\*\*")
_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
_MISSING = object()
_JOBS = threading.BoundedSemaphore(2)


class _NoFollow(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(_NoFollow)


class _TtlCache:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._rows: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, now: float):
        with self._lock:
            row = self._rows.get(key)
            if row is None:
                return _MISSING
            exp, value = row
            if exp <= now:
                self._rows.pop(key, None)
                return _MISSING
            self._rows.pop(key, None)
            self._rows[key] = row
            return value

    def put(self, key: str, value, ttl: float, now: float) -> None:
        with self._lock:
            self._rows.pop(key, None)
            self._rows[key] = (now + ttl, value)
            while len(self._rows) > self.limit:
                self._rows.pop(next(iter(self._rows)), None)

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()


_CARDS = _TtlCache(512)
_SEARCHES = _TtlCache(128)


def reset_image_cache() -> None:
    """Drop cached lookups. Tests call this between cases."""
    _CARDS.clear()
    _SEARCHES.clear()


def normalize_title(text: str) -> str:
    """Casefold, strip one trailing parenthetical, then drop punctuation."""
    raw = unicodedata.normalize("NFKC", str(text or "")).strip()
    raw = _PAREN.sub("", raw).strip()
    raw = raw.casefold()
    cleaned = []
    for ch in raw:
        if ch.isalnum() or ch.isspace():
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())


def _clip(text: str, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit].strip()


def _as_text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _absolute(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def _blocked_ip(host: str) -> bool:
    try:
        ip = ip_address(host)
    except ValueError:
        return False
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    return ip.version == 4 and ip in _CGNAT


def _public_http(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.port not in (None, 80, 443):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        not host
        or host == "localhost"
        or host.endswith(".local")
        or host.endswith(".localhost")
    ):
        return False
    if _blocked_ip(host):
        return False
    return True


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def _png_allowed() -> bool:
    return os.environ.get("PI_PAIR_IMAGE_PNG") == "1"


def _raster_path(path: str) -> bool:
    lowered = (path or "").lower()
    if ".svg" in lowered:
        return False
    if lowered.endswith((".jpg", ".jpeg", ".webp")):
        return True
    return bool(_png_allowed() and lowered.endswith(".png"))


def _image_url(url: str) -> bool:
    if not _public_http(url):
        return False
    if _host(url) not in _IMAGE_HOSTS:
        return False
    return _raster_path(urlparse(url).path or "")


def _page_url(url: str) -> bool:
    if not _public_http(url) or urlparse(url).scheme != "https":
        return False
    host = _host(url)
    return host == "wikipedia.org" or host.endswith(".wikipedia.org")


def _api_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and _host(url) == "en.wikipedia.org"
        and _public_http(url)
    )


def _wiki_title(url: str) -> str:
    """Title from an English Wikipedia page link. The URL is never fetched."""
    raw = (url or "").strip()
    if not raw or raw.startswith("//"):
        return ""
    parsed = urlparse(raw)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return ""
    host = (parsed.hostname or "").lower().rstrip(".")
    if host != "en.wikipedia.org":
        return ""
    path = parsed.path or ""
    marker = "/wiki/"
    if not path.startswith(marker):
        return ""
    slug = unquote(path[len(marker) :])
    if not slug or "/" in slug or slug.startswith("."):
        return ""
    title = " ".join(slug.replace("_", " ").split())
    if len(title) < 2 or len(title) > 180:
        return ""
    return title


def sanitize_card(item: object) -> dict | None:
    """Keep one public card. Drop anything that is not a Wikimedia photo."""
    if not isinstance(item, dict):
        return None
    url = _absolute(str(item.get("url") or ""))
    if not _image_url(url):
        return None
    title = _clip(item.get("title") or item.get("alt") or "", 120)
    alt = _clip(item.get("alt") or title, 180)
    if not title or not alt:
        return None
    card: dict = {"url": url, "alt": alt, "title": title}
    caption = _clip(item.get("caption") or "", 140)
    if caption:
        card["caption"] = caption
    source = _absolute(str(item.get("source") or ""))
    if source and _page_url(source):
        card["source"] = source
    for key in ("width", "height"):
        if key not in item or item.get(key) in (None, ""):
            continue
        value = item.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 < value <= 8000
        ):
            return None
        card[key] = value
    return card


def _outside_fences(text: str) -> str:
    closed = _FENCE_CLOSED.sub("\n", text or "")
    return _FENCE_OPEN.sub("\n", closed)


def _clean_head(raw: str) -> str:
    text = _HEAD_CUT.split(str(raw or "").replace("\r", ""), maxsplit=1)[0]
    text = text.strip().strip("*_`").strip()
    if text.startswith("**") and text.endswith("**") and len(text) > 4:
        text = text[2:-2].strip()
    return text.strip(" \"'")


def _source_titles(sources) -> list[str]:
    if not isinstance(sources, list):
        return []
    found = []
    for item in sources[:8]:
        url = ""
        if isinstance(item, str):
            url = item
        elif isinstance(item, dict):
            url = str(item.get("url") or "")
        title = _wiki_title(url)
        if title:
            found.append(title)
    return found


def candidates(question: str, answer: str, sources) -> list[dict]:
    """Structural names only: source links, then list heads and bold spans.

    `question` is accepted so the call shape matches the page payload. A
    question lookup happens later, and only when this list is empty.
    """
    del question
    found: list[dict] = []
    seen: set[str] = set()

    def add(title: str, signal: int, limit: int) -> None:
        if len(found) >= MAX_CANDIDATES:
            return
        text = " ".join(str(title or "").split())
        if len(text) < 2 or len(text) > limit:
            return
        norm = normalize_title(text)
        if len(norm) < 2 or norm in seen:
            return
        seen.add(norm)
        found.append({"title": text, "norm": norm, "signal": signal})

    for title in _source_titles(sources):
        add(title, 1, 180)
    plain = _outside_fences(answer or "")
    for match in _ITEM_LINE.finditer(plain):
        add(_clean_head(match.group(1)), 2, 80)
    for bold in _BOLD.findall(plain):
        add(bold.strip(), 2, 80)
    return found


def _header(headers, name: str) -> str:
    if headers is None or not hasattr(headers, "get"):
        return ""
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    return str(value or "")


def _read_capped(response, cap: int = _BODY_CAP) -> bytes:
    try:
        data = response.read(cap)
    except TypeError:
        data = response.read()
    if isinstance(data, str):
        data = data.encode()
    if not isinstance(data, bytes):
        return b""
    return data[:cap]


def _close(response) -> None:
    close = getattr(response, "close", None)
    if close:
        close()


def _open(request, opener):
    try:
        if opener is not None:
            return opener(request, timeout=FETCH_TIMEOUT)
        return _OPENER.open(request, timeout=FETCH_TIMEOUT)
    except HTTPError as error:
        return error


def _fetch_json(url: str, opener) -> dict:
    current = url
    redirects = 0
    while True:
        if not _api_url(current):
            raise ValueError("blocked url")
        request = Request(
            current,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response = _open(request, opener)
        status = getattr(response, "status", None)
        if status is None:
            status = getattr(response, "code", 200)
        headers = getattr(response, "headers", {}) or {}
        if status in (301, 302, 303, 307, 308):
            loc = _header(headers, "Location")
            _close(response)
            if not loc or redirects >= 2:
                raise ValueError("redirect")
            current = urljoin(current, loc)
            redirects += 1
            continue
        try:
            if status >= 400:
                raise ValueError("http")
            body = _read_capped(response)
        finally:
            _close(response)
        data = json.loads(body.decode("utf-8", "replace") or "{}")
        if not isinstance(data, dict):
            raise ValueError("json")
        return data


def _titles(phrase: str, opener) -> list[str]:
    url = (
        SEARCH_API
        + "?"
        + urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": phrase,
                "srlimit": "5",
                "srnamespace": "0",
                "format": "json",
                "formatversion": "2",
            }
        )
    )
    data = _fetch_json(url, opener)
    query = data.get("query") if isinstance(data.get("query"), dict) else {}
    rows = query.get("search") if isinstance(query.get("search"), list) else []
    found: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title or title.casefold().endswith("(disambiguation)"):
            continue
        found.append(title)
    return found[:5]


def _summary_card(title: str, opener) -> dict | None:
    slug = quote(title.replace(" ", "_"), safe="")
    data = _fetch_json(SUMMARY_API + slug, opener)
    if str(data.get("type") or "") != "standard":
        return None
    page_title = _clip(data.get("title") or title, 120)
    description = _clip(data.get("description") or "", 140)
    thumb = data.get("thumbnail") if isinstance(data.get("thumbnail"), dict) else {}
    content = (
        data.get("content_urls") if isinstance(data.get("content_urls"), dict) else {}
    )
    desktop = content.get("desktop") if isinstance(content.get("desktop"), dict) else {}
    page = _absolute(str(desktop.get("page") or ""))
    if not page and page_title:
        page = "https://en.wikipedia.org/wiki/" + quote(
            page_title.replace(" ", "_"), safe=""
        )
    alt = _clip(f"{page_title}. {description}".strip(" ."), 180) or page_title
    card = {
        "url": _absolute(str(thumb.get("source") or "")),
        "alt": alt,
        "title": page_title,
        "source": page,
        "width": thumb.get("width"),
        "height": thumb.get("height"),
    }
    if description:
        card["caption"] = description
    return sanitize_card(card)


def _span(page: str, blob: str) -> bool:
    if not page or not blob:
        return False
    return f" {page} " in f" {blob} "


def _title_ok(candidate: dict, card: dict, question: str, answer: str) -> bool:
    page = normalize_title(str(card.get("title") or ""))
    if not page:
        return False
    signal = int(candidate.get("signal") or 0)
    cand = str(candidate.get("norm") or "")
    if signal == 3:
        blob = normalize_title(f"{question}\n{answer}")
        return _span(page, blob)
    if signal == 1:
        if page == cand:
            return True
        if len(page) < 3 or len(cand) < 3:
            return False
        return _span(page, cand) or _span(cand, page)
    return page == cand


def _lookup_one(candidate: dict, opener, question: str, answer: str, deadline: float):
    if time.monotonic() >= deadline:
        return None
    key = str(candidate.get("norm") or "")
    if not key:
        return None
    cached = _CARDS.get(key, time.monotonic())
    if cached is _MISSING:
        try:
            card = _summary_card(str(candidate.get("title") or ""), opener)
        except Exception:
            return None
        _CARDS.put(key, card, HIT_TTL_S if card else MISS_TTL_S, time.monotonic())
    else:
        card = cached
    if not isinstance(card, dict):
        return None
    if not _title_ok(candidate, card, question, answer):
        return None
    return card


def _search_title(question: str, opener, deadline: float) -> str:
    query = " ".join((question or "").split())[:160].strip()
    if not query or time.monotonic() >= deadline:
        return ""
    key = normalize_title(query) or query.casefold()
    cached = _SEARCHES.get(key, time.monotonic())
    if cached is not _MISSING:
        return str(cached or "")
    try:
        titles = _titles(query, opener)
    except Exception:
        return ""
    title = titles[0] if titles else ""
    _SEARCHES.put(key, title, SEARCH_TTL_S, time.monotonic())
    return title


def _resolve(fn, items: list, deadline: float) -> list:
    if not items:
        return []
    results = [None] * len(items)
    slots = threading.Semaphore(3)

    def run(index: int, item) -> None:
        if time.monotonic() >= deadline:
            return
        acquired = slots.acquire(timeout=max(0.0, deadline - time.monotonic()))
        if not acquired:
            return
        try:
            if time.monotonic() < deadline:
                results[index] = fn(item)
        except Exception:
            results[index] = None
        finally:
            slots.release()

    threads = []
    for index, item in enumerate(items):
        thread = threading.Thread(
            target=run, args=(index, item), name="pi-image", daemon=True
        )
        threads.append(thread)
        thread.start()
    for thread in threads:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        thread.join(left)
    return results


def _cards(payload: dict, opener) -> dict:
    question = _as_text(payload.get("question"), 500)
    answer = _as_text(payload.get("answer"), 4000)
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    deadline = time.monotonic() + max(0.05, float(JOB_BUDGET_S))
    found = candidates(question, answer, sources[:8])
    if not found:
        title = _search_title(question, opener, deadline)
        norm = normalize_title(title)
        if title and norm:
            found = [{"title": title, "norm": norm, "signal": 3}]
    resolved = _resolve(
        lambda item: _lookup_one(item, opener, question, answer, deadline),
        found,
        deadline,
    )
    cards: list[dict] = []
    for card in resolved:
        if not isinstance(card, dict):
            continue
        if any(item.get("url") == card.get("url") for item in cards):
            continue
        cards.append(card)
        if len(cards) >= MAX_CARDS:
            break
    return {"ok": True, "cards": cards}


def cards(payload, opener=None) -> dict:
    """Look up cards for one finished turn. Overflow and misses are empty."""
    if not isinstance(payload, dict):
        return {"ok": True, "cards": []}
    if not _JOBS.acquire(blocking=False):
        return {"ok": True, "cards": []}
    try:
        return _cards(payload, opener)
    except Exception:
        return {"ok": True, "cards": []}
    finally:
        _JOBS.release()
