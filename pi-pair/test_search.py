"""Parser and fetch limits for the miss lookup. No live network."""
from __future__ import annotations

import json
import unittest

from pair.search import lookup_web

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
        self.assertLessEqual(len(result["sources"]), 3)

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
