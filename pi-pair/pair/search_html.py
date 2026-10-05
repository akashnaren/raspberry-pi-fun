"""HTML helpers for a DuckDuckGo result page and one fetched article.

The fetch path in search.py decides which URL is public and which address
to pin. This module only turns bytes of HTML into titles, links, and text.
"""
from __future__ import annotations

from html.parser import HTMLParser

_VOID = {"area", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
_SKIP = {"script", "style", "noscript"}


class _ResultParser(HTMLParser):
    def __init__(self, unwrap, title_cap: int, snippet_cap: int) -> None:
        super().__init__(convert_charrefs=True)
        self._unwrap = unwrap
        self._title_cap = title_cap
        self._snippet_cap = snippet_cap
        self.results: list[dict] = []
        self._mode = ""
        self._depth = 0
        self._href = ""
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        attr = {key: value or "" for key, value in attrs}
        classes = set(attr.get("class", "").split())
        if self._mode:
            if tag not in _VOID:
                self._depth += 1
            return
        if tag == "a" and "result__a" in classes:
            self._mode = "a"
            self._depth = 1
            self._href = attr.get("href", "")
            self._buf = []
        elif "result__snippet" in classes:
            self._mode = "s"
            self._depth = 1
            self._buf = []

    def handle_endtag(self, tag):
        if not self._mode or tag in _VOID:
            return
        self._depth -= 1
        if self._depth > 0:
            return
        text = " ".join("".join(self._buf).split())
        if self._mode == "a":
            url = self._unwrap(self._href)
            if url:
                title = (text or url)[: self._title_cap]
                self.results.append({"title": title, "url": url, "snippet": ""})
        elif self._mode == "s" and self.results and not self.results[-1]["snippet"]:
            self.results[-1]["snippet"] = text[: self._snippet_cap]
        self._mode = ""
        self._buf = []

    def handle_data(self, data):
        if self._mode:
            self._buf.append(data)


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def plain_text(raw: str, cap: int) -> str:
    parser = _TextParser()
    parser.feed(raw)
    text = " ".join("".join(parser.parts).split())
    head = text[:400].lower()
    if "password" in head and ("sign in" in head or "log in" in head):
        return ""
    return text[:cap]


def parse_result_page(
    page: str,
    limit: int,
    unwrap,
    title_cap: int,
    snippet_cap: int,
) -> list[dict]:
    parser = _ResultParser(unwrap, title_cap, snippet_cap)
    parser.feed(page)
    return parser.results[:limit]
