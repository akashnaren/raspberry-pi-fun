"""CORS on /api/* is an allowlist. The page and /v1 stay open."""

from __future__ import annotations

import os
import sys
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.server import allowed_api_origin, make_server


def _headers(port: int, method: str, path: str, headers: dict[str, str] | None = None):
    conn = HTTPConnection("127.0.0.1", port, timeout=2)
    conn.request(method, path, headers=headers or {})
    resp = conn.getresponse()
    found = {
        "status": resp.status,
        "origin": resp.getheader("Access-Control-Allow-Origin"),
        "vary": resp.getheader("Vary") or "",
        "allow": resp.getheader("Access-Control-Allow-Headers") or "",
    }
    resp.read()
    conn.close()
    return found


class ApiCors(unittest.TestCase):
    def test_origin_helper_rejects_foreign_and_script_urls(self):
        self.assertEqual(allowed_api_origin("", "127.0.0.1:18080", ""), "")
        self.assertEqual(allowed_api_origin("null", "127.0.0.1:18080", "null"), "")
        self.assertEqual(
            allowed_api_origin("javascript:alert(1)", "127.0.0.1:9", ""), ""
        )
        self.assertEqual(allowed_api_origin("data:text/html,hi", "127.0.0.1:9", ""), "")
        self.assertEqual(
            allowed_api_origin("https://evil.example", "127.0.0.1:18080", ""),
            "",
        )
        self.assertEqual(
            allowed_api_origin(
                "https://user:pass@lab.example", "127.0.0.1:9", "https://lab.example"
            ),
            "",
        )
        self.assertEqual(
            allowed_api_origin("http://127.0.0.1:18080", "127.0.0.1:18080", ""),
            "http://127.0.0.1:18080",
        )
        self.assertEqual(
            allowed_api_origin(
                "https://lab.example/", "127.0.0.1:9", "https://lab.example"
            ),
            "https://lab.example",
        )

    def test_api_responses_echo_only_an_allowed_origin(self):
        saved = os.environ.get("PI_PAIR_CORS_ORIGINS")
        os.environ["PI_PAIR_CORS_ORIGINS"] = "https://lab.example"
        httpd = make_server("127.0.0.1", 0)
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        thread.start()
        port = httpd.server_address[1]
        try:
            same = _headers(
                port,
                "OPTIONS",
                "/api/health",
                {"Origin": f"http://127.0.0.1:{port}"},
            )
            self.assertEqual(same["status"], 204)
            self.assertEqual(same["origin"], f"http://127.0.0.1:{port}")
            self.assertIn("Origin", same["vary"])
            self.assertIn("Authorization", same["allow"])
            self.assertIn("Content-Type", same["allow"])
            self.assertNotIn("*", same["allow"])

            missing = _headers(port, "OPTIONS", "/api/chat")
            self.assertIsNone(missing["origin"])
            self.assertIn("Origin", missing["vary"])

            foreign = _headers(
                port,
                "OPTIONS",
                "/api/chat",
                {"Origin": "https://evil.example"},
            )
            self.assertIsNone(foreign["origin"])

            listed = _headers(
                port,
                "OPTIONS",
                "/api/health",
                {"Origin": "https://lab.example"},
            )
            self.assertEqual(listed["origin"], "https://lab.example")

            script = _headers(
                port,
                "OPTIONS",
                "/api/health",
                {"Origin": "javascript:alert(1)"},
            )
            self.assertIsNone(script["origin"])

            page = _headers(port, "GET", "/", {"Origin": "https://evil.example"})
            self.assertEqual(page["origin"], "*")
            lane = _headers(
                port,
                "OPTIONS",
                "/v1/chat/completions",
                {"Origin": "https://evil.example"},
            )
            self.assertEqual(lane["origin"], "*")
            self.assertEqual(lane["allow"], "*")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            if saved is None:
                os.environ.pop("PI_PAIR_CORS_ORIGINS", None)
            else:
                os.environ["PI_PAIR_CORS_ORIGINS"] = saved


if __name__ == "__main__":
    unittest.main()
