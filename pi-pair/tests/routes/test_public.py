"""Public Funnel traffic is not treated as the LAN."""

from __future__ import annotations

import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

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

    def test_loopback_proxy_uses_the_original_client(self):
        self.assertEqual(
            trusted_client_ip("127.0.0.1", "1.2.3.4, 203.0.113.9"),
            "1.2.3.4",
        )
        self.assertEqual(
            trusted_client_ip("127.0.0.1", "junk, 198.51.100.7, 172.70.1.1"),
            "198.51.100.7",
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


class ProxyClientHeaders(unittest.TestCase):
    def test_cf_connecting_ip_wins_on_loopback(self):
        call = _Call(
            "127.0.0.1",
            {
                "Cf-Connecting-Ip": "203.0.113.4",
                "Tailscale-Ingress-Src": "198.51.100.2:4431",
                "X-Forwarded-For": "192.0.2.1, 172.70.9.9",
            },
        )
        self.assertEqual(call._client_ip(), "203.0.113.4")

    def test_tailscale_ingress_src_is_next(self):
        call = _Call(
            "::1",
            {
                "Tailscale-Ingress-Src": "198.51.100.2:4431",
                "X-Forwarded-For": "192.0.2.1",
            },
        )
        self.assertEqual(call._client_ip(), "198.51.100.2")
        v6 = _Call("127.0.0.1", {"Tailscale-Ingress-Src": "[2001:db8::5]:443"})
        self.assertEqual(v6._client_ip(), "2001:db8::5")

    def test_invalid_header_falls_through(self):
        call = _Call(
            "127.0.0.1",
            {"Cf-Connecting-Ip": "evil", "X-Forwarded-For": "192.0.2.1, 172.70.1.1"},
        )
        self.assertEqual(call._client_ip(), "192.0.2.1")
        bare = _Call("127.0.0.1", {"Cf-Connecting-Ip": "nope"})
        self.assertEqual(bare._client_ip(), "127.0.0.1")

    def test_headers_ignored_from_non_loopback_peer(self):
        for peer in ("10.0.0.5", "100.101.102.103", "192.0.2.10"):
            call = _Call(
                peer,
                {
                    "Cf-Connecting-Ip": "203.0.113.4",
                    "Tailscale-Ingress-Src": "198.51.100.2:1",
                    "X-Forwarded-For": "192.0.2.1",
                },
            )
            self.assertEqual(call._client_ip(), peer)

    def test_rotating_cloudflare_hops_share_one_bucket(self):
        os.environ["PI_PAIR_RATE_DEFAULT"] = "2/60"
        reset_limits()
        try:
            results = []
            for hop in range(5):
                call = _Call(
                    "127.0.0.1",
                    {
                        "Cf-Connecting-Ip": "203.0.113.77",
                        "X-Forwarded-For": f"203.0.113.77, 172.70.0.{hop}",
                    },
                )
                results.append(admit(call._client_ip(), "/health")[0])
            self.assertEqual(results, [True, True, False, False, False])
        finally:
            os.environ.pop("PI_PAIR_RATE_DEFAULT", None)
            reset_limits()


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


class _Mesh(BaseRoutes):
    def __init__(self, peer: str, headers: dict | None = None, path: str = "/health"):
        self.client_address = (peer, 9)
        self.headers = headers or {}
        self.path = path
        self.codes: list[int] = []

    def send_response(self, code: int, message: str | None = None) -> None:
        self.codes.append(code)

    def send_header(self, keyword: str, value: str) -> None:
        return None

    def end_headers(self) -> None:
        return None

    def _cors(self) -> None:
        return None

    def _drain_request_body(self) -> None:
        return None

    def _error(
        self, message: str, status: int = 502, headers: dict | None = None
    ) -> None:
        self.codes.append(status)


class ListenHosts(unittest.TestCase):
    def test_default_bind_is_every_interface(self):
        saved = os.environ.pop("PI_PAIR_HOST", None)
        try:
            self.assertEqual(listen_hosts(), ["0.0.0.0"])
            os.environ["PI_PAIR_HOST"] = ""
            self.assertEqual(listen_hosts(), ["0.0.0.0"])
            os.environ["PI_PAIR_HOST"] = "127.0.0.1,10.0.0.228"
            self.assertEqual(listen_hosts(), ["127.0.0.1", "10.0.0.228"])
        finally:
            if saved is None:
                os.environ.pop("PI_PAIR_HOST", None)
            else:
                os.environ["PI_PAIR_HOST"] = saved
        script = Path(__file__).resolve().parents[2] / "install.sh"
        self.assertIn(
            "Environment=PI_PAIR_HOST=0.0.0.0", script.read_text(encoding="utf-8")
        )

    def test_lan_peer_is_reachable_and_not_public(self):
        for peer in ("10.0.0.181", "10.0.0.228", "10.0.0.166"):
            call = _Mesh(peer, path="/docs")
            self.assertFalse(call._public(), peer)
            self.assertFalse(call._guard(), peer)
            self.assertEqual(call.codes, [])

        saved_role = os.environ.get("PI_PAIR_ROLE")
        os.environ["PI_PAIR_ROLE"] = "dataset"
        reset_limits()
        with runtime._health_lock:
            runtime._health_cache["peers"] = [
                {
                    "name": "pi2",
                    "host": "10.0.0.181",
                    "port": 18080,
                    "role": "health",
                    "ok": True,
                    "models": [],
                    "latency_ms": 3,
                }
            ]
            runtime._health_cache["t"] = time.time()
        httpd = make_server("0.0.0.0", 0)
        thread = _start(httpd)
        port = httpd.server_address[1]
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{port}/health")
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read()
                self.assertEqual(response.status, 200)
            self.assertIn(b"10.0.0.181", body)
            docs = urllib.request.Request(f"http://127.0.0.1:{port}/docs")
            with urllib.request.urlopen(docs, timeout=5) as response:
                self.assertEqual(response.status, 200)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            reset_limits()
            if saved_role is None:
                os.environ.pop("PI_PAIR_ROLE", None)
            else:
                os.environ["PI_PAIR_ROLE"] = saved_role

    def test_tailnet_peer_is_trusted_until_a_tunnel_header(self):
        for peer in (
            "100.100.197.18",
            "100.64.0.1",
            "100.127.255.254",
            "fd7a:115c:a1e0::1",
            "fd7a:115c:a1e0:1::abcd",
            "::ffff:100.64.1.8",
        ):
            call = _Mesh(peer, path="/docs")
            self.assertFalse(call._public(), peer)
            self.assertFalse(call._guard(), peer)
            self.assertEqual(call.codes, [], peer)

        outside = _Mesh("100.128.0.1", path="/docs")
        self.assertTrue(outside._public())
        other_ula = _Mesh("fd7a:115c:a1e1::1", path="/docs")
        self.assertTrue(other_ula._public())

        funnel = _Mesh(
            "100.100.197.18",
            {"Tailscale-Funnel-Request": "true"},
            path="/docs",
        )
        self.assertTrue(funnel._public())
        self.assertTrue(funnel._guard())
        self.assertEqual(funnel.codes, [404])

        cloud = _Mesh("fd7a:115c:a1e0::1", {"Cf-Connecting-Ip": "203.0.113.9"})
        self.assertTrue(cloud._public())
        ray = _Mesh("100.64.1.8", {"Cf-Ray": "abc"})
        self.assertTrue(ray._public())

        os.environ["PI_PAIR_RATE_DEFAULT"] = "1/60"
        reset_limits()
        try:
            mesh = _Mesh("100.64.8.8", path="/health")
            self.assertFalse(mesh._limited())
            self.assertFalse(mesh._limited())
            self.assertEqual(mesh.codes, [])
            limited = _Mesh(
                "100.64.8.8",
                {"Tailscale-Funnel-Request": "true"},
                path="/health",
            )
            self.assertFalse(limited._limited())
            self.assertTrue(limited._limited())
            self.assertEqual(limited.codes, [429])
        finally:
            os.environ.pop("PI_PAIR_RATE_DEFAULT", None)
            reset_limits()


if __name__ == "__main__":
    unittest.main()
