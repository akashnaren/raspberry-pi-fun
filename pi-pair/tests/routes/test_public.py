"""Public Funnel traffic is not treated as the LAN."""

from __future__ import annotations

import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from pair.core import runtime
from pair.core.config import listen_hosts
from pair.routes.base import BaseRoutes, trusted_client_ip
from pair.routes.flywheel import ENQUEUE_BODY_CAP
from pair.routes.limit import admit, reset_limits, route_class
from pair.server import make_server


class _Call(BaseRoutes):
    def __init__(self, peer: str, headers: dict):
        self.client_address = (peer, 9)
        self.headers = headers


class TrustedProxy(unittest.TestCase):
    def test_spoofed_forwarded_for_is_ignored_off_loopback(self):
        self.assertEqual(trusted_client_ip("10.0.0.5", "8.8.8.8"), "10.0.0.5")
        self.assertEqual(
            trusted_client_ip("192.0.2.10", "10.0.0.5, 8.8.8.8"),
            "192.0.2.10",
        )
        lan = _Call("10.0.0.5", {"X-Forwarded-For": "8.8.8.8"})
        self.assertFalse(lan._public())
        self.assertEqual(lan._client_ip(), "10.0.0.5")
        public = _Call("192.0.2.10", {"X-Forwarded-For": "10.0.0.5"})
        self.assertTrue(public._public())
        self.assertEqual(public._client_ip(), "192.0.2.10")

    def test_loopback_proxy_uses_the_appended_client(self):
        self.assertEqual(
            trusted_client_ip("127.0.0.1", "1.2.3.4, 203.0.113.9"),
            "203.0.113.9",
        )
        self.assertEqual(trusted_client_ip("::1", "203.0.113.8"), "203.0.113.8")
        self.assertEqual(trusted_client_ip("127.0.0.1", "not-an-ip"), "127.0.0.1")

    def test_funnel_header_is_public_even_from_loopback(self):
        plain = _Call("127.0.0.1", {"X-Forwarded-For": "8.8.8.8"})
        self.assertFalse(plain._public())
        funnel = _Call(
            "127.0.0.1",
            {
                "Tailscale-Funnel-Request": "true",
                "X-Forwarded-For": "10.0.0.5",
            },
        )
        self.assertTrue(funnel._public())
        self.assertEqual(funnel._client_ip(), "10.0.0.5")
        cloud = _Call("127.0.0.1", {"Cf-Connecting-Ip": "203.0.113.4"})
        self.assertTrue(cloud._public())


class RateLimit(unittest.TestCase):
    def tearDown(self):
        for name in (
            "PI_PAIR_RATE_DEFAULT",
            "PI_PAIR_RATE_CHAT",
            "PI_PAIR_RATE_UPLOAD",
            "PI_PAIR_RATE_SEARCH",
            "PI_PAIR_RATE_IMAGES",
        ):
            os.environ.pop(name, None)
        reset_limits()

    def test_classes_and_a_full_bucket_refuses(self):
        self.assertEqual(route_class("/v1/chat/completions"), "chat")
        self.assertEqual(route_class("/api/chat"), "chat")
        self.assertEqual(route_class("/v1/attachments"), "upload")
        self.assertEqual(route_class("/v1/search"), "search")
        self.assertEqual(route_class("/v1/images"), "images")
        os.environ["PI_PAIR_RATE_CHAT"] = "1/60"
        os.environ["PI_PAIR_RATE_DEFAULT"] = "2/60"
        reset_limits()
        self.assertTrue(admit("203.0.113.7", "/v1/chat/completions")[0])
        refused, retry = admit("203.0.113.7", "/api/chat")
        self.assertFalse(refused)
        self.assertGreaterEqual(retry, 1)
        self.assertTrue(admit("203.0.113.7", "/health")[0])

    def test_bucket_is_thread_safe(self):
        os.environ["PI_PAIR_RATE_DEFAULT"] = "5/3600"
        reset_limits()
        ok: list[int] = []
        lock = threading.Lock()

        def hit() -> None:
            allowed, _retry = admit("203.0.113.9", "/health")
            if allowed:
                with lock:
                    ok.append(1)

        threads = [threading.Thread(target=hit) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(ok), 5)


def _start(httpd) -> threading.Thread:
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    return thread


class FunnelHttp(unittest.TestCase):
    def setUp(self):
        self._role = os.environ.get("PI_PAIR_ROLE")
        self._data = os.environ.get("PI_PAIR_DATA")
        os.environ["PI_PAIR_ROLE"] = "dataset"
        reset_limits()
        with runtime._health_lock:
            runtime._health_cache["peers"] = [
                {
                    "name": "pi4",
                    "host": "10.0.0.181",
                    "port": 11434,
                    "role": "brain",
                    "ok": True,
                    "models": ["qwen3:0.6b"],
                    "latency_ms": 4,
                }
            ]
            runtime._health_cache["t"] = time.time()
        self.httpd = make_server("127.0.0.1", 0)
        self.thread = _start(self.httpd)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        reset_limits()
        for name in (
            "PI_PAIR_RATE_DEFAULT",
            "PI_PAIR_RATE_CHAT",
            "PI_PAIR_ROLE",
            "PI_PAIR_DATA",
        ):
            os.environ.pop(name, None)
        if self._role is not None:
            os.environ["PI_PAIR_ROLE"] = self._role
        if self._data is not None:
            os.environ["PI_PAIR_DATA"] = self._data

    def _open(self, method, path, body=None, headers=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers=headers or {},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def test_funnel_redacts_health_and_hides_docs_and_tools(self):
        funnel = {"Tailscale-Funnel-Request": "true"}
        status, _headers, raw = self._open("GET", "/health", headers=funnel)
        self.assertEqual(status, 200)
        text = raw.decode()
        self.assertNotIn("10.0.0.181", text)
        self.assertNotIn('"host"', text)
        self.assertNotIn("qwen", text.lower())
        open_status, _headers, open_raw = self._open("GET", "/health")
        self.assertEqual(open_status, 200)
        self.assertIn("10.0.0.181", open_raw.decode())

        for path in (
            "/docs",
            "/swagger",
            "/openapi.json",
            "/swagger.json",
            "/tools/health",
        ):
            hidden, _headers, _raw = self._open("GET", path, headers=funnel)
            self.assertEqual(hidden, 404, path)
        shown, _headers, _raw = self._open("GET", "/docs")
        self.assertEqual(shown, 200)
        posted, _headers, _raw = self._open(
            "POST",
            "/tools/extract",
            body=b"{}",
            headers={**funnel, "content-type": "application/json"},
        )
        self.assertEqual(posted, 404)

    def test_security_headers_and_rate_limit(self):
        status, headers, _raw = self._open("GET", "/health")
        self.assertEqual(status, 200)
        policy = headers.get("Content-Security-Policy") or ""
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")

        os.environ["PI_PAIR_RATE_DEFAULT"] = "1/60"
        reset_limits()
        funnel = {
            "Tailscale-Funnel-Request": "true",
            "X-Forwarded-For": "203.0.113.44",
        }
        first, _headers, _raw = self._open("GET", "/health", headers=funnel)
        self.assertEqual(first, 200)
        status, headers, raw = self._open("GET", "/health", headers=funnel)
        self.assertEqual(status, 429)
        self.assertGreaterEqual(int(headers.get("Retry-After") or "0"), 1)
        self.assertIn(b"Too many requests", raw)
        lan, _headers, _raw = self._open("GET", "/health")
        self.assertEqual(lan, 200)
        other, _headers, _raw = self._open(
            "GET",
            "/health",
            headers={
                "Tailscale-Funnel-Request": "true",
                "X-Forwarded-For": "203.0.113.45",
            },
        )
        self.assertEqual(other, 200)

    def test_enqueue_body_is_capped(self):
        blob = b'{"prompt":"hi","answer":"yo"}'
        self.assertLess(len(blob), ENQUEUE_BODY_CAP)
        over = b"{" + b"x" * (ENQUEUE_BODY_CAP + 8)
        status, _headers, _raw = self._open(
            "POST",
            "/v1/flywheel/enqueue",
            body=over,
            headers={"content-type": "application/json"},
        )
        self.assertEqual(status, 413)


class ListenHosts(unittest.TestCase):
    def test_default_is_loopback_plus_the_tailnet(self):
        saved = os.environ.pop("PI_PAIR_HOST", None)
        try:
            with patch(
                "pair.core.config.local_ipv4",
                return_value=[
                    ("eth0", "10.0.0.166"),
                    ("tailscale0", "100.64.1.8"),
                    ("wlan0", "192.168.1.20"),
                ],
            ):
                self.assertEqual(listen_hosts(), ["127.0.0.1", "100.64.1.8"])
            with patch(
                "pair.core.config.local_ipv4",
                return_value=[("tailscale0", "10.0.0.166")],
            ):
                self.assertEqual(listen_hosts(), ["127.0.0.1", "10.0.0.166"])
            os.environ["PI_PAIR_HOST"] = "127.0.0.1,10.0.0.228"
            self.assertEqual(listen_hosts(), ["127.0.0.1", "10.0.0.228"])
            os.environ["PI_PAIR_HOST"] = "0.0.0.0"
            self.assertEqual(listen_hosts(), ["0.0.0.0"])
        finally:
            if saved is None:
                os.environ.pop("PI_PAIR_HOST", None)
            else:
                os.environ["PI_PAIR_HOST"] = saved


if __name__ == "__main__":
    unittest.main()
