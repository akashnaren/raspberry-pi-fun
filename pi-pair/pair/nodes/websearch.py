"""pi2 search. DuckDuckGo lite and Wikipedia, in parallel, cached for 10 minutes.

Pure Python so it can run on an armv7 board with about 1 GB of RAM. No model.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

CACHE_S = 600.0
DDG_LITE = "https://lite.duckduckgo.com/lite/"
WIKI = "https://en.wikipedia.org/api/rest_v1/page/summary/"

_CACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()


def _cache_get(key: str, now: float) -> dict | None:
    with _LOCK:
        row = _CACHE.get(key)
        if row is None or now - row[0] > CACHE_S:
            return None
        return dict(row[1])


def _cache_put(key: str, value: dict, now: float) -> None:
    with _LOCK:
        _CACHE[key] = (now, dict(value))


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _snippets_from_lite(html: str, limit: int = 3) -> list[dict]:
    """Pull result anchors out of DuckDuckGo lite. Generic, not a topic list."""
    rows = []
    lower = html or ""
    cursor = 0
    while len(rows) < limit:
        start = lower.find('class="result-link"', cursor)
        if start < 0:
            break
        href = lower.find('href="', start)
        end_tag = lower.find("</a>", start)
        if href < 0 or end_tag < 0:
            break
        url = lower[href + 6 : lower.find('"', href + 6)]
        title = lower[lower.find(">", start) + 1 : end_tag]
        title = " ".join(title.replace("<b>", "").replace("</b>", "").split())
        snippet = ""
        snip = lower.find('class="result-snippet"', end_tag)
        if snip > 0:
            snippet = " ".join(
                lower[lower.find(">", snip) + 1 : lower.find("</td>", snip)].split()
            )[:240]
        if url and title:
            rows.append({"title": title[:120], "url": url, "snippet": snippet})
        cursor = end_tag + 4
    return rows


def _wiki_row(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None
    title = str(payload.get("title") or "").strip()
    extract = str(payload.get("extract") or "").strip()
    url = ""
    content = payload.get("content_urls") or {}
    desktop = content.get("desktop") if isinstance(content, dict) else None
    if isinstance(desktop, dict):
        url = str(desktop.get("page") or "")
    if not title or not extract:
        return None
    return {"title": title[:120], "url": url, "snippet": extract[:240]}


def search(
    query: str,
    *,
    fetch=None,
    now: float | None = None,
    limit: int = 3,
) -> dict:
    """Return numbered snippets. `fetch(url) -> (status, body)` is injectable."""
    text = " ".join((query or "").split())[:240]
    if not text:
        return {"status": "failed", "sources": [], "context": ""}
    moment = time.time() if now is None else now
    cached = _cache_get(text.lower(), moment)
    if cached is not None:
        return cached
    getter = fetch or _fetch
    wiki_url = WIKI + urllib.parse.quote(text.replace(" ", "_"))
    ddg_url = DDG_LITE + "?" + urllib.parse.urlencode({"q": text})
    box: dict[str, tuple[int, str]] = {}

    def pull(name: str, url: str) -> None:
        try:
            box[name] = getter(url)
        except Exception:
            box[name] = (0, "")

    threads = [
        threading.Thread(target=pull, args=("ddg", ddg_url), daemon=True),
        threading.Thread(target=pull, args=("wiki", wiki_url), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
    sources: list[dict] = []
    wiki_status, wiki_body = box.get("wiki", (0, ""))
    if wiki_status == 200 and wiki_body:
        try:
            row = _wiki_row(json.loads(wiki_body))
        except json.JSONDecodeError:
            row = None
        if row:
            sources.append(row)
    ddg_status, ddg_body = box.get("ddg", (0, ""))
    if ddg_status == 200 and ddg_body:
        for row in _snippets_from_lite(ddg_body, limit=limit):
            if len(sources) >= limit:
                break
            sources.append(row)
    status = "ok" if sources else "failed"
    lines = [
        f"[{index}] {row['title']}: {row['snippet']}".strip()
        for index, row in enumerate(sources, start=1)
    ]
    result = {"status": status, "sources": sources[:limit], "context": "\n".join(lines)}
    if status == "ok":
        _cache_put(text.lower(), result, moment)
    return result


def _fetch(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"user-agent": "OpenPi-search"})
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.status, response.read().decode("utf-8", "replace")
