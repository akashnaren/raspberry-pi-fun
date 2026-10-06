"""Parser and fetch limits for the miss lookup. No live network."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import pair.search as search
from pair.search import (
    DEFAULT_RESULTS,
    MAX_RESULTS,
    PAGE_READ_CAP,
    SEARCH_TIMEOUT,
    _fetch,
    _public_http,
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
            rows.append(
                '<a class="result__a" href="https://example.com/login">Sign in</a>'
            )
            rows.append('<a class="result__snippet">please sign in</a>')
            rows.append('<a class="result__a" href="http://10.0.0.8/secret">Local</a>')
            rows.append('<a class="result__snippet">hidden</a>')
        rows.append(
            f'<a class="result__a" href="https://example.com/p{i}">Title {i}</a>'
        )
        rows.append(f'<a class="result__snippet">snippet {i} about the bench</a>')
    return "<html><body>" + "".join(rows) + "</body></html>"


class _Resp:
    def __init__(self, body, status=200, headers=None):
        self._body = body.encode() if isinstance(body, str) else body
        self.status = status
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.given = 0

    def read(self, n=-1):
        if n is None or n < 0:
            data, self._body = self._body, b""
        else:
            data, self._body = self._body[:n], self._body[n:]
        self.given += len(data)
        return data

    def close(self):
        return None


class SearchParse(unittest.TestCase):
    def setUp(self):
        self._resolve = search._resolve
        search._resolve = lambda host: ["8.8.8.8"]

    def tearDown(self):
        search._resolve = self._resolve

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
        self.assertNotIn("Bench page body text.", result["context"])
        self.assertNotIn("Text from the first page", result["context"])
        self.assertNotIn("secret()", result["context"])
        self.assertNotIn("https://example.com/login", json.dumps(result["sources"]))
        self.assertNotIn("127.0.0.1", json.dumps(result))
        pages = [url for url in fetched if "duckduckgo.com" not in url]
        self.assertEqual(pages, [])
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
            raise AssertionError(request.full_url)

        result = lookup_web("bench height", opener=opener)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sources"][0]["url"], "https://example.com/bench")
        self.assertIn("a short snippet about the bench", result["context"])
        self.assertNotIn("Text from the first page", result["context"])

    def test_default_keeps_three_snippets_and_skips_the_page(self):
        self.assertEqual(DEFAULT_RESULTS, 3)
        self.assertGreaterEqual(MAX_RESULTS, DEFAULT_RESULTS)
        self.assertLessEqual(MAX_RESULTS, 10)
        calls = []

        def opener(request, timeout=None):
            url = request.full_url
            calls.append((url, timeout))
            if "duckduckgo.com/html" in url:
                return _Resp(_many_html())
            raise AssertionError(url)

        result = lookup_web("bench height", opener=opener)
        urls = [item["url"] for item in result["sources"]]
        self.assertEqual(len(urls), DEFAULT_RESULTS)
        self.assertEqual(
            urls, [f"https://example.com/p{i}" for i in range(DEFAULT_RESULTS)]
        )
        self.assertIn(
            f"snippet {DEFAULT_RESULTS - 1} about the bench", result["context"]
        )
        self.assertNotIn(
            f"snippet {DEFAULT_RESULTS} about the bench", result["context"]
        )
        self.assertNotIn("example.com/login", json.dumps(result))
        self.assertNotIn("10.0.0.8", json.dumps(result))
        pages = [
            (url, timeout) for url, timeout in calls if "duckduckgo.com" not in url
        ]
        self.assertEqual(pages, [])
        search_calls = [timeout for url, timeout in calls if "duckduckgo.com" in url]
        self.assertEqual(search_calls, [SEARCH_TIMEOUT])
        self.assertLessEqual(len(result["context"]), 720)
        self.assertNotIn("Text from the first page", result["context"])

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
        self.assertEqual(
            len(lookup_web("bench", opener=opener, limit="8")["sources"]),
            DEFAULT_RESULTS,
        )

    def test_instant_topics_honor_the_same_cap(self):
        topics = []
        for i in range(15):
            if i == 2:
                topics.append(
                    {"FirstURL": "https://example.com/login", "Text": "sign in"}
                )
                topics.append("not a topic")
                continue
            topics.append(
                {
                    "FirstURL": f"https://example.com/t{i}",
                    "Text": f"instant topic {i} about the bench",
                }
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
        self.assertNotIn(
            "https://example.com/login", [item["url"] for item in capped["sources"]]
        )

    def test_pi_timeouts_stay_one_fetch(self):
        self.assertEqual(SEARCH_TIMEOUT, 3)
        self.assertLessEqual(SEARCH_TIMEOUT, 4)

    def test_page_reads_block_private_ranges_and_cap_the_body(self):
        blocked = (
            "http://10.1.2.3/secret",
            "http://127.0.0.1/secret",
            "http://169.254.1.1/secret",
            "http://100.64.0.1/secret",
            "http://100.127.255.9/secret",
            "http://[::ffff:10.1.2.3]/secret",
        )
        for url in blocked:
            self.assertFalse(_public_http(url), url)
        self.assertTrue(_public_http("https://example.com/bench"))
        self.assertTrue(_public_http("https://8.8.8.8/dns"))

        rows = []
        for url in blocked:
            rows.append(f'<a class="result__a" href="{url}">Hidden</a>')
            rows.append('<a class="result__snippet">do not fetch</a>')
        rows.append('<a class="result__a" href="https://example.com/ok">Public</a>')
        rows.append('<a class="result__snippet">visible snippet</a>')
        html = "<html><body>" + "".join(rows) + "</body></html>"
        fetched = []
        page = _Resp(b"P" * (PAGE_READ_CAP + 8000))

        def opener(request, timeout=None):
            url = request.full_url
            fetched.append(url)
            if "duckduckgo.com/html" in url:
                return _Resp(html)
            if url == "https://example.com/ok":
                return page
            raise AssertionError(url)

        result = lookup_web("bench height", opener=opener)
        self.assertEqual(
            [item["url"] for item in result["sources"]], ["https://example.com/ok"]
        )
        blob = json.dumps(result)
        for marker in (
            "10.1.2.3",
            "127.0.0.1",
            "169.254.1.1",
            "100.64.0.1",
            "100.127.255.9",
        ):
            self.assertNotIn(marker, blob)
        self.assertEqual(
            [url for url in fetched if "duckduckgo.com" not in url],
            [],
        )
        self.assertNotIn("Text from the first page", result["context"])

        def redirect(request, timeout=None):
            url = request.full_url
            fetched.append(url)
            if "duckduckgo.com/html" in url:
                body = (
                    '<html><body><a class="result__a" href="https://example.com/go">Go</a>'
                    '<a class="result__snippet">out</a></body></html>'
                )
                return _Resp(body)
            if url == "https://example.com/go":
                return _Resp(
                    "",
                    status=302,
                    headers={
                        "Location": "http://100.64.8.8/secret",
                        "Content-Type": "text/html",
                    },
                )
            raise AssertionError(url)

        fetched.clear()
        hopped = lookup_web("bench height", opener=redirect)
        self.assertNotIn("100.64.8.8", json.dumps(hopped))
        self.assertNotIn("Text from the first page", hopped["context"])
        self.assertFalse(any("100.64.8.8" in url for url in fetched))

    def test_hostname_resolving_to_private_or_tailscale_is_rejected(self):
        records = {
            "inside.example": ["10.1.2.3"],
            "tailscale.example": ["100.64.1.5"],
            "loop.example": ["127.0.0.1"],
            "link.example": ["169.254.4.4"],
            "split.example": ["8.8.8.8", "10.0.0.9"],
            "v6split.example": ["1.1.1.1", "fd7a:115c:a1e0::1"],
        }

        def resolve(host):
            if host in records:
                return list(records[host])
            if host in ("html.duckduckgo.com", "api.duckduckgo.com"):
                return ["8.8.8.8"]
            raise AssertionError(host)

        search._resolve = resolve
        for name in records:
            self.assertFalse(_public_http(f"https://{name}/secret"), name)
        calls = []

        def opener(request, timeout=None):
            calls.append(request.full_url)
            if "duckduckgo.com/html" in request.full_url:
                body = (
                    '<html><body><a class="result__a" href="https://inside.example/hid">Hidden</a>'
                    '<a class="result__snippet">no</a>'
                    '<a class="result__a" href="https://tailscale.example/ts">Tail</a>'
                    '<a class="result__snippet">no</a></body></html>'
                )
                return _Resp(body)
            if "api.duckduckgo.com" in request.full_url:
                return _Resp("{}", headers={"Content-Type": "application/json"})
            raise AssertionError(request.full_url)

        result = lookup_web("bench height", opener=opener)
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["status"], "failed")
        self.assertTrue(calls)
        self.assertTrue(all("duckduckgo.com" in url for url in calls))

    def test_public_hostname_is_allowed_and_connection_is_pinned(self):
        def resolve(host):
            if host == "public.example":
                return ["1.1.1.1", "2606:4700:4700::1111"]
            if host == "html.duckduckgo.com":
                return ["8.8.8.8"]
            if host == "missing.example":
                return []
            raise AssertionError(host)

        search._resolve = resolve
        self.assertTrue(_public_http("https://public.example/bench"))
        self.assertFalse(_public_http("https://missing.example/bench"))
        seen = []

        def opener(request, timeout=None):
            seen.append((request.full_url, request.pinned_ip))
            if "duckduckgo.com/html" in request.full_url:
                body = (
                    '<html><body><a class="result__a" href="https://public.example/bench">Bench</a>'
                    '<a class="result__snippet">a short snippet about the bench</a></body></html>'
                )
                return _Resp(body)
            if request.full_url == "https://public.example/bench":
                return _Resp("<html><body>from the pinned page</body></html>")
            raise AssertionError(request.full_url)

        result = lookup_web("bench height", opener=opener)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sources"][0]["url"], "https://public.example/bench")
        self.assertNotIn("from the pinned page", result["context"])
        self.assertEqual(
            seen,
            [
                ("https://html.duckduckgo.com/html/?q=bench+height", "8.8.8.8"),
            ],
        )

        created = []

        def create(address, timeout=None, source_address=None):
            created.append(address)
            raise OSError("stop")

        def boom(*_args, **_kwargs):
            raise AssertionError("re-resolved")

        for url, pin, port in (
            ("https://public.example/x", "1.1.1.1", 443),
            ("http://public.example/x", "1.1.1.1", 80),
        ):
            created.clear()
            with (
                mock.patch("socket.getaddrinfo", boom),
                mock.patch("socket.create_connection", create),
            ):
                with self.assertRaises(OSError):
                    _fetch(url, None, 1, 64)
            self.assertEqual(created, [(pin, port)], url)

    def test_redirect_rechecks_resolved_addresses(self):
        def resolve(host):
            return {
                "html.duckduckgo.com": ["8.8.8.8"],
                "public.example": ["1.1.1.1"],
                "evil.example": ["10.9.9.9"],
                "tail.example": ["100.64.0.2"],
                "next.example": ["9.9.9.9"],
            }[host]

        search._resolve = resolve
        seen = []

        def opener(request, timeout=None):
            seen.append((request.full_url, getattr(request, "pinned_ip", "")))
            if "duckduckgo.com/html" in request.full_url:
                body = (
                    '<html><body><a class="result__a" href="https://public.example/go">Go</a>'
                    '<a class="result__snippet">out</a></body></html>'
                )
                return _Resp(body)
            if request.full_url == "https://public.example/go":
                target = (
                    "https://evil.example/secret"
                    if not seen_tail["on"]
                    else "https://tail.example/secret"
                )
                return _Resp(
                    "",
                    status=302,
                    headers={"Location": target, "Content-Type": "text/html"},
                )
            if request.full_url == "https://next.example/ok":
                return _Resp("<html><body>second hop</body></html>")
            raise AssertionError(request.full_url)

        seen_tail = {"on": False}
        blocked = lookup_web("bench height", opener=opener)
        self.assertNotIn("10.9.9.9", json.dumps(blocked))
        self.assertNotIn("Text from the first page", blocked["context"])
        self.assertNotIn("https://evil.example/secret", [url for url, _pin in seen])
        self.assertNotIn("https://public.example/go", [url for url, _pin in seen])
        self.assertTrue(all("duckduckgo.com" in url for url, _pin in seen))

        seen.clear()
        seen_tail["on"] = True
        tail = lookup_web("bench height", opener=opener)
        self.assertNotIn("100.64.0.2", json.dumps(tail))
        self.assertFalse(any("tail.example" in url for url, _pin in seen))

        def follow(request, timeout=None):
            seen.append((request.full_url, request.pinned_ip))
            if "duckduckgo.com/html" in request.full_url:
                body = (
                    '<html><body><a class="result__a" href="https://public.example/go">Go</a>'
                    '<a class="result__snippet">out</a></body></html>'
                )
                return _Resp(body)
            if request.full_url == "https://public.example/go":
                return _Resp(
                    "",
                    status=302,
                    headers={
                        "Location": "https://next.example/ok",
                        "Content-Type": "text/html",
                    },
                )
            if request.full_url == "https://next.example/ok":
                return _Resp("<html><body>second hop</body></html>")
            raise AssertionError(request.full_url)

        seen.clear()
        hopped = lookup_web("bench height", opener=follow)
        self.assertNotIn("second hop", hopped["context"])
        self.assertTrue(all("duckduckgo.com" in url for url, _pin in seen))
