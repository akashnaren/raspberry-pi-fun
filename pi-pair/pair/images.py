"""Public image cards for a visual chat turn.

Wikipedia's summary API, no key. Any miss returns an empty list.
The reply field is ``pi_images``. See docs/IMAGES.md.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from ipaddress import ip_address
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

SEARCH_API = "https://en.wikipedia.org/w/api.php"
SUMMARY_API = "https://en.wikipedia.org/api/rest_v1/page/summary/"
USER_AGENT = (
    "PiGPT/1.0 (local chat image cards; +https://github.com/akashnaren/raspberry-pi-fun)"
)
FETCH_TIMEOUT = 3
MAX_CARDS = 3
MAX_LIST_CARDS = 8
MAX_TRIES = 4
_IMAGE_HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org"}
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_GENERIC = {
    "movie",
    "movies",
    "film",
    "films",
    "poster",
    "posters",
    "picture",
    "pictures",
    "photo",
    "photos",
    "image",
    "images",
    "cinema",
    "actor",
    "actress",
    "painting",
    "portrait",
    "artwork",
}
_VISUAL = re.compile(
    r"\b(?:movies?|films?|cinema|posters?|actors?|actresses?|paintings?|"
    r"portraits?|artworks?|photographs?|photos?|pictures?|images?)\b"
    r"|\b(?:tv|television)\s+shows?\b"
    r"|\blooks?\s+like\b"
    r"|\balbum\s+covers?\b",
    re.I,
)
_MOVIE = re.compile(r"\b(?:movies?|films?|cinema|posters?)\b", re.I)
_SEVERAL = re.compile(r"\b(?:movies|films|posters|pictures|photos|images)\b", re.I)
_VISUAL_NOUN = re.compile(
    r"\b(?:cars?|automobiles?|vehicles?|movies?|films?|shows?|"
    r"products?|phones?|laptops?|gadgets?|places?|cities|landmarks?|"
    r"restaurants?|hotels?|paintings?|animals?|dogs?|birds?|games?|"
    r"albums?|books?|watches?|cameras?)\b",
    re.I,
)
_LISTISH = re.compile(r"\b(?:top|best|list|rank)\b", re.I)
_ITEM_LINE = re.compile(r"(?m)^\s*(?:\d{1,2}[\.\)]\s+|[-*]\s+)(.+)$")
_LEAD = re.compile(
    r"^(?:"
    r"please\s+|can you\s+|could you\s+|would you\s+|"
    r"tell me about\s+|talk about\s+|describe\s+|explain\s+|show me\s+|give me\s+|"
    r"what is\s+|what's\s+|who is\s+|who's\s+|what are\s+|"
    r"(?:a\s+|an\s+)?pictures?\s+of\s+|(?:a\s+|an\s+)?photos?\s+of\s+|"
    r"(?:an\s+|a\s+)?images?\s+of\s+|"
    r"what does\s+|what do\s+|"
    r"posters?\s+(?:for|of)\s+|"
    r"the\s+|a\s+|an\s+"
    r")+",
    re.I,
)
_TRAIL = re.compile(r"(?:\s+looks?\s+like)+\s*$", re.I)
_FILM_WORD = re.compile(r"\bfilms?\b", re.I)


class _NoFollow(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(_NoFollow)


def suits_visuals(text: str) -> bool:
    return bool(_VISUAL.search(text or ""))


def visual_mode(prompt: str) -> str:
    """`none`, `one`, or `each`.

    A single picture stays one card. A list of cars, movies, products, or
    places gets one card per item. Abstract and math lists stay text.
    """
    text = prompt or ""
    if not text.strip():
        return "none"
    noun = _VISUAL_NOUN.search(text)
    visual = bool(noun or suits_visuals(text))
    if not visual:
        return "none"
    from pair.lists import list_count

    counted = list_count(text)
    plural = bool(_SEVERAL.search(text) or (noun and _LISTISH.search(text)))
    if counted or plural:
        return "each"
    return "one"


def item_names(answer: str, limit: int) -> list[str]:
    """Titles from a numbered or bulleted reply. The trailing blurb is dropped."""
    names: list[str] = []
    for match in _ITEM_LINE.finditer(answer or ""):
        raw = match.group(1).strip()
        raw = re.sub(r"[*_`]+", "", raw)
        raw = re.split(r"\s+[—–]\s+|\s+-\s+", raw, maxsplit=1)[0]
        raw = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", raw)
        raw = raw.strip(" .\"'")
        if len(raw) < 2 or len(raw) > 80:
            continue
        names.append(raw)
        if len(names) >= limit:
            break
    return names


def search_phrase(text: str) -> str:
    """Wikipedia search text. Empty when the line should not look up a picture."""
    raw = " ".join((text or "").split())
    if not raw or not suits_visuals(raw):
        return ""
    cleaned = raw.strip(" ?!.,;:\"'")
    previous = None
    while cleaned and cleaned != previous:
        previous = cleaned
        cleaned = _LEAD.sub("", cleaned).strip()
        cleaned = _TRAIL.sub("", cleaned).strip(" ?!.,;:\"'")
    cleaned = re.sub(r"^(?:movie|film)\s+", "", cleaned, count=1, flags=re.I).strip()
    cleaned = " ".join(cleaned.split())[:160].strip()
    if not cleaned or cleaned.lower() in _GENERIC:
        return ""
    if _MOVIE.search(raw):
        cleaned = re.sub(r"\bmovies\b", "films", cleaned, count=1, flags=re.I)
        if not _FILM_WORD.search(cleaned):
            cleaned = f"{cleaned} film"
    cleaned = " ".join(cleaned.split())[:180].strip()
    if not cleaned or cleaned.lower() in _GENERIC:
        return ""
    return cleaned


def _clip(text: str, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit].strip()


def _absolute(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def _public_http(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.port not in (None, 80, 443):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".local") or host.endswith(".localhost"):
        return False
    try:
        ip = ip_address(host)
    except ValueError:
        return True
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def _image_url(url: str) -> bool:
    if not _public_http(url):
        return False
    if _host(url) not in _IMAGE_HOSTS:
        return False
    path = (urlparse(url).path or "").lower()
    return path.endswith(_IMAGE_EXT)


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


def sanitize_card(item: object) -> dict | None:
    """Keep one public card. Drop anything that is not a Wikimedia image."""
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
        value = item.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 8000:
            card[key] = value
    return card


def _header(headers, name: str) -> str:
    if headers is None or not hasattr(headers, "get"):
        return ""
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    return str(value or "")


def _read_capped(response, cap: int = 200000) -> bytes:
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


def _titles(phrase: str, opener, limit: int = 5) -> list[str]:
    cap = max(1, min(int(limit), 8))
    url = SEARCH_API + "?" + urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": phrase,
            "srlimit": str(cap),
            "srnamespace": "0",
            "format": "json",
            "formatversion": "2",
        }
    )
    data = _fetch_json(url, opener)
    query = data.get("query") if isinstance(data.get("query"), dict) else {}
    rows = query.get("search") if isinstance(query.get("search"), list) else []
    found: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title or title.lower().endswith("(disambiguation)"):
            continue
        found.append(title)
    return found[:cap]


def _mentions_film(title: str, description: str) -> bool:
    blob = f"{title} {description}".lower()
    return any(word in blob for word in ("film", "movie", "cinema", "poster"))


def _summary_card(title: str, opener, movie: bool) -> dict | None:
    slug = quote(title.replace(" ", "_"), safe="")
    data = _fetch_json(SUMMARY_API + slug, opener)
    if str(data.get("type") or "") == "disambiguation":
        return None
    page_title = _clip(data.get("title") or title, 120)
    description = _clip(data.get("description") or "", 140)
    if movie and not _mentions_film(page_title, description):
        return None
    thumb = data.get("thumbnail") if isinstance(data.get("thumbnail"), dict) else {}
    content = data.get("content_urls") if isinstance(data.get("content_urls"), dict) else {}
    desktop = content.get("desktop") if isinstance(content.get("desktop"), dict) else {}
    page = _absolute(str(desktop.get("page") or ""))
    if not page and page_title:
        page = "https://en.wikipedia.org/wiki/" + quote(page_title.replace(" ", "_"), safe="")
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


def lookup_images(query: str, opener=None) -> list[dict]:
    """Cards for a visual question. Empty list when none suit or the lookup fails."""
    phrase = search_phrase(query)
    if not phrase:
        return []
    try:
        titles = _titles(phrase, opener)
    except Exception:
        return []
    movie = bool(_MOVIE.search(query or ""))
    want = MAX_CARDS if _SEVERAL.search(query or "") else 1
    cards: list[dict] = []
    tries = 0
    for title in titles:
        if len(cards) >= want or tries >= MAX_TRIES:
            break
        tries += 1
        try:
            card = _summary_card(title, opener, movie)
        except Exception:
            card = None
        if not card:
            continue
        if any(item["url"] == card["url"] for item in cards):
            continue
        cards.append(card)
    return cards


def _lookup_named(
    name: str,
    opener,
    movie: bool,
    phrase: str | None = None,
    tries: int = 4,
) -> dict | None:
    query = phrase or (f"{name} film" if movie else name)
    window = 8 if movie else 5
    try:
        titles = _titles(query, opener, limit=window)
    except Exception:
        return None
    for title in titles[:tries]:
        try:
            card = _summary_card(title, opener, movie)
        except Exception:
            card = None
        if card:
            return card
    return None


def cards_for_answer(prompt: str, answer: str, opener=None) -> list[dict]:
    """Cards for this turn. `each` looks up every listed item. `none` is empty."""
    mode = visual_mode(prompt)
    if mode == "none":
        return []
    if mode == "one":
        return lookup_images(prompt, opener=opener)
    from pair.lists import list_count

    counted = list_count(prompt) or MAX_LIST_CARDS
    limit = min(counted, MAX_LIST_CARDS)
    names = item_names(answer, limit)
    movie = bool(_MOVIE.search(prompt or "") or re.search(r"\b(?:movies?|films?)\b", prompt or "", re.I))
    if not names:
        return []

    def fetch(name: str, phrase: str | None = None) -> dict | None:
        return _lookup_named(name, opener, movie, phrase=phrase, tries=4 if movie else 2)

    workers = min(4, len(names))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        found = list(pool.map(fetch, names))
    if movie:
        missing = [index for index, card in enumerate(found) if not card]
        if missing:
            refill = min(4, len(missing))
            with ThreadPoolExecutor(max_workers=refill) as pool:
                again = list(
                    pool.map(
                        lambda index: _lookup_named(
                            names[index],
                            opener,
                            True,
                            phrase=f"{names[index]} (film)",
                            tries=4,
                        ),
                        missing,
                    )
                )
            for index, card in zip(missing, again):
                if card:
                    found[index] = card
    cards: list[dict] = []
    for card in found:
        if not card:
            continue
        if any(item["url"] == card["url"] for item in cards):
            continue
        cards.append(card)
        if len(cards) >= limit:
            break
    return cards
