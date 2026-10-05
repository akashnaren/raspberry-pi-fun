"""Parser and fetch limits for the miss lookup. No live network."""
from __future__ import annotations

import json
import unittest

from pair.search import (
    DEFAULT_RESULTS,
    MAX_RESULTS,
    PAGE_TIMEOUT,
    SEARCH_TIMEOUT,
    lookup_web,
)

FIXTURE = """
<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fbench&amp;rut=1">Bench note</a>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fbench">a short snippet about the bench</a>
<a class="result__a" href="https://example.com/login">Sign in</a>
<a class="result__snippet">please sign in</a>
<a class="result__a" href="http://127.0.0.1/secret">Local</a>
<a class="result__snippet">hidden</a>
<a class="result__a" href="https://example.com/other">Other</a>
<a class="result__snippet">second public snippet</a>
</body></html>
"""

PAGE = "<html><head><style>.x{}</style></head><body><p>Bench page body text.</p><a href='https://example.com/next'>more</a><script>secret()</script></body></html>"


def _many_html() -> str:
    """Twelve public hits, with login and private links that must not take a slot."""
    rows = []
    for i in range(12):
        if i == 4:
            rows.append('<a class="result__a" href="https://example.com/login">Sign in</a>')
            rows.append('<a class="result__snippet">please sign in</a>')
            rows.append('<a class="result__a" href="http://10.0.0.8/secret">Local</a>')
            rows.append('<a class="result__snippet">hidden</a>')
        rows.append(f'<a class="result__a" href="https://example.com/p{i}">Title {i}</a>')
        rows.append(f'<a class="result__snippet">snippet {i} about the bench</a>')
    return "<html><body>" + "".join(rows) + "</body></html>"


class _Resp:
    def __init__(self, body, status=200, headers=None):
        self._body = body.encode() if isinstance(body, str) else body
        self.status = status
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}

    def read(self, n=-1):
        if n is None or n < 0:
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:n], self._body[n:]
        return data

    def close(self):
        return None


class SearchParse(unittest.TestCase):
    def test_parses_snippets_and_fetches_one_public_page(self):
        fetched = []

        def opener(request, timeout=None):
            url = request.full_url
            fetched.append(url)
            if "duckduckgo.com/html" in url:
                return _Resp(FIXTURE)
            if url == "https://example.com/bench":
                return _Resp(PAGE)
            raise AssertionError(url)

        result = lookup_web("how tall is the bench", opener=opener)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sources"][0]["title"], "Bench note")
        self.assertEqual(result["sources"][0]["url"], "https://example.com/bench")
        self.assertIn("a short snippet about the bench", result["context"])
        self.assertIn("Bench page body text.", result["context"])
        self.assertNotIn("secret()", result["context"])
        self.assertNotIn("https://example.com/login", json.dumps(result["sources"]))
        self.assertNotIn("127.0.0.1", json.dumps(result))
        pages = [url for url in fetched if "duckduckgo.com" not in url]
        self.assertEqual(pages, ["https://example.com/bench"])
        self.assertEqual(
            [item["url"] for item in result["sources"]],
            ["https://example.com/bench", "https://example.com/other"],
        )

    def test_login_wall_drops_page_text(self):
        def opener(request, timeout=None):
            if "duckduckgo.com/html" in request.full_url:
                return _Resp(FIXTURE)
            wall = "<html><body><h1>Sign in</h1><label>Password</label>secret bench fact</body></html>"
            return _Resp(wall)

        result = lookup_web("bench", opener=opener)
        self.assertEqual(result["status"], "ok")
        self.assertIn("a short snippet about the bench", result["context"])
        self.assertNotIn("secret bench fact", result["context"])
        self.assertNotIn("Text from the first page", result["context"])

    def test_opener_error_is_failed(self):
        def opener(request, timeout=None):
            raise OSError("down")

        result = lookup_web("anything new", opener=opener)
        self.assertEqual(result, {"status": "failed", "sources": [], "context": ""})

    def test_instant_answer_when_html_is_empty(self):
        def opener(request, timeout=None):
            if "duckduckgo.com/html" in request.full_url:
                return _Resp("<html></html>")
            if "api.duckduckgo.com" in request.full_url:
                body = json.dumps(
                    {
                        "Heading": "Bench",
                        "AbstractText": "a short snippet about the bench",
                        "AbstractURL": "https://example.com/bench",
                    }
                )
                return _Resp(body, headers={"Content-Type": "application/json"})
            if request.full_url == "https://example.com/bench":
                return _Resp("<html><body>from instant</body></html>")
            raise AssertionError(request.full_url)

        result = lookup_web("bench height", opener=opener)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sources"][0]["url"], "https://example.com/bench")
        self.assertIn("a short snippet about the bench", result["context"])
        self.assertIn("from instant", result["context"])

    def test_default_keeps_more_than_three_from_one_page(self):
        self.assertGreaterEqual(DEFAULT_RESULTS, 6)
        self.assertLessEqual(DEFAULT_RESULTS, 8)
        self.assertGreater(DEFAULT_RESULTS, 3)
        self.assertGreaterEqual(MAX_RESULTS, DEFAULT_RESULTS)
        self.assertLessEqual(MAX_RESULTS, 10)
        calls = []

        def opener(request, timeout=None):
            url = request.full_url
            calls.append((url, timeout))
            if "duckduckgo.com/html" in url:
                return _Resp(_many_html())
            if url == "https://example.com/p0":
                return _Resp(PAGE)
            raise AssertionError(url)

        result = lookup_web("bench height", opener=opener)
        urls = [item["url"] for item in result["sources"]]
        self.assertEqual(len(urls), DEFAULT_RESULTS)
        self.assertEqual(urls, [f"https://example.com/p{i}" for i in range(DEFAULT_RESULTS)])
        self.assertIn(f"snippet {DEFAULT_RESULTS - 1} about the bench", result["context"])
        self.assertNotIn(f"snippet {DEFAULT_RESULTS} about the bench", result["context"])
        self.assertNotIn("example.com/login", json.dumps(result))
        self.assertNotIn("10.0.0.8", json.dumps(result))
        pages = [(url, timeout) for url, timeout in calls if "duckduckgo.com" not in url]
        self.assertEqual(pages, [("https://example.com/p0", PAGE_TIMEOUT)])
        search_calls = [timeout for url, timeout in calls if "duckduckgo.com" in url]
        self.assertEqual(search_calls, [SEARCH_TIMEOUT])

    def test_hard_cap_and_explicit_limit(self):
        def opener(request, timeout=None):
            if "duckduckgo.com/html" in request.full_url:
                return _Resp(_many_html())
            return _Resp("<html><body>page</body></html>")

        capped = lookup_web("bench", opener=opener, limit=MAX_RESULTS + 25)
        self.assertEqual(len(capped["sources"]), MAX_RESULTS)
        self.assertLessEqual(len(capped["sources"]), 10)
        self.assertIn("snippet 9 about the bench", capped["context"])
        self.assertNotIn("snippet 10 about the bench", capped["context"])

        six = lookup_web("bench", opener=opener, limit=6)
        self.assertEqual(
            [item["url"] for item in six["sources"]],
            [f"https://example.com/p{i}" for i in range(6)],
        )
        self.assertEqual(len(lookup_web("bench", opener=opener, limit=0)["sources"]), 1)
        self.assertEqual(len(lookup_web("bench", opener=opener, limit="8")["sources"]), DEFAULT_RESULTS)

    def test_instant_topics_honor_the_same_cap(self):
        topics = []
        for i in range(15):
            if i == 2:
                topics.append({"FirstURL": "https://example.com/login", "Text": "sign in"})
                topics.append("not a topic")
                continue
            topics.append(
                {"FirstURL": f"https://example.com/t{i}", "Text": f"instant topic {i} about the bench"}
            )

        def opener(request, timeout=None):
            url = request.full_url
            if "duckduckgo.com/html" in url:
                return _Resp("<html></html>", headers={"Content-Type": "text/html"})
            if "api.duckduckgo.com" in url:
                body = json.dumps(
                    {
                        "Heading": "Bench",
                        "AbstractText": "a short snippet about the bench",
                        "AbstractURL": "https://example.com/bench",
                        "RelatedTopics": topics,
                    }
                )
                return _Resp(body, headers={"Content-Type": "application/json"})
            if url == "https://example.com/bench":
                return _Resp("<html><body>from instant</body></html>")
            raise AssertionError(url)

        result = lookup_web("bench height", opener=opener)
        urls = [item["url"] for item in result["sources"]]
        self.assertEqual(len(urls), DEFAULT_RESULTS)
        self.assertEqual(urls[0], "https://example.com/bench")
        self.assertNotIn("https://example.com/login", urls)
        self.assertEqual(len(urls), len(set(urls)))

        capped = lookup_web("bench height", opener=opener, limit=40)
        self.assertEqual(len(capped["sources"]), MAX_RESULTS)
        self.assertNotIn("https://example.com/login", [item["url"] for item in capped["sources"]])

    def test_pi_timeouts_stay_one_fetch(self):
        self.assertGreaterEqual(SEARCH_TIMEOUT, 2)
        self.assertGreaterEqual(PAGE_TIMEOUT, 1)
        self.assertLessEqual(SEARCH_TIMEOUT + PAGE_TIMEOUT, 12)
        self.assertLessEqual((2 * SEARCH_TIMEOUT) + PAGE_TIMEOUT, 15)
