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
from pair.routes import limit as rate_limit
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

    def test_loopback_without_an_ingress_marker_uses_the_peer(self):
        self.assertEqual(
            trusted_client_ip("127.0.0.1", "1.2.3.4, 203.0.113.9"),
            "127.0.0.1",
        )
        self.assertEqual(
            trusted_client_ip("127.0.0.1", "junk, 198.51.100.7, 172.70.1.1"),
            "127.0.0.1",
        )
        self.assertEqual(trusted_client_ip("::1", "203.0.113.8"), "::1")
        self.assertEqual(trusted_client_ip("127.0.0.1", "not-an-ip"), "127.0.0.1")
        bare = _Call(
            "127.0.0.1",
            {
                "Cf-Connecting-Ip": "203.0.113.4",
                "X-Forwarded-For": "192.0.2.1",
            },
        )
        self.assertEqual(bare._client_ip(), "127.0.0.1")

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
    def _bucket(self, results_limit: str):
        os.environ["PI_PAIR_RATE_DEFAULT"] = results_limit
        reset_limits()

    def _clear_bucket(self):
        os.environ.pop("PI_PAIR_RATE_DEFAULT", None)
        reset_limits()

    def test_ingress_src_is_ignored(self):
        call = _Call(
            "::1",
            {
                "Tailscale-Ingress-Src": "198.51.100.2:4431",
                "X-Forwarded-For": "192.0.2.1",
                "Cf-Connecting-Ip": "203.0.113.4",
            },
        )
        self.assertEqual(call._client_ip(), "::1")
        v6 = _Call("127.0.0.1", {"Tailscale-Ingress-Src": "[2001:db8::5]:443"})
        self.assertEqual(v6._client_ip(), "127.0.0.1")

    def test_invalid_header_falls_through(self):
        call = _Call(
            "127.0.0.1",
            {"Cf-Connecting-Ip": "evil", "X-Forwarded-For": "192.0.2.1, 172.70.1.1"},
        )
        self.assertEqual(call._client_ip(), "127.0.0.1")
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
                        "Cf-Ray": "8a1b2c3d4e5f6a7b-EWR",
                        "Cf-Connecting-Ip": "203.0.113.77",
                        "X-Forwarded-For": f"203.0.113.77, 172.70.0.{hop}",
                    },
                )
                results.append(admit(call._client_ip(), "/health")[0])
            self.assertEqual(results, [True, True, False, False, False])
        finally:
            os.environ.pop("PI_PAIR_RATE_DEFAULT", None)
            reset_limits()

    def test_funnel_rotating_ingress_src_shares_one_bucket(self):
        self._bucket("2/60")
        try:
            results = []
            for hop in range(4):
                call = _Call(
                    "127.0.0.1",
                    {
                        "Tailscale-Funnel-Request": "?1",
                        "X-Forwarded-For": "198.51.100.2",
                        "Tailscale-Ingress-Src": f"203.0.113.{hop}:4431",
                        "Cf-Connecting-Ip": f"192.0.2.{hop}",
                    },
                )
                self.assertEqual(call._client_ip(), "198.51.100.2")
                results.append(admit(call._client_ip(), "/health")[0])
            self.assertEqual(results, [True, True, False, False])
        finally:
            self._clear_bucket()

    def test_funnel_visitors_with_one_forwarded_for_get_separate_buckets(self):
        self._bucket("1/60")
        try:

            def allowed(forwarded: str) -> bool:
                call = _Call(
                    "127.0.0.1",
                    {
                        "Tailscale-Funnel-Request": "?1",
                        "X-Forwarded-For": forwarded,
                        "Tailscale-Ingress-Src": "198.51.100.9:1",
                    },
                )
                return admit(call._client_ip(), "/health")[0]

            self.assertTrue(allowed("1.1.1.1"))
            self.assertFalse(allowed("1.1.1.1"))
            self.assertTrue(allowed("2.2.2.2"))
        finally:
            self._clear_bucket()

    def test_cloudflared_funnel_header_cannot_pick_the_bucket(self):
        self._bucket("2/60")
        try:
            results = []
            for hop in range(4):
                call = _Call(
                    "127.0.0.1",
                    {
                        "Cf-Ray": "8a1b2c3d4e5f6a7b-EWR",
                        "Cf-Connecting-Ip": "203.0.113.4",
                        "Tailscale-Funnel-Request": "?1",
                        "Tailscale-Ingress-Src": f"198.51.100.{hop}:9",
                        "X-Forwarded-For": f"192.0.2.{hop}",
                    },
                )
                self.assertEqual(call._client_ip(), "127.0.0.1")
                results.append(admit(call._client_ip(), "/health")[0])
            self.assertEqual(results, [True, True, False, False])
        finally:
            self._clear_bucket()

    def test_funnel_forged_cf_ray_shares_the_peer_bucket(self):
        self._bucket("1/60")
        try:
            results = []
            for hop in range(20):
                call = _Call(
                    "127.0.0.1",
                    {
                        "Tailscale-Funnel-Request": "?1",
                        "X-Forwarded-For": "6.6.6.6",
                        "Cf-Ray": "x",
                        "Cf-Connecting-Ip": f"7.7.7.{hop}",
                    },
                )
                self.assertEqual(call._client_ip(), "127.0.0.1")
                results.append(admit(call._client_ip(), "/health")[0])
            self.assertEqual(results, [True] + [False] * 19)
            loop = _Call(
                "127.0.0.1",
                {
                    "Cdn-Loop": "Cloudflare",
                    "Tailscale-Funnel-Request": "?1",
                    "Cf-Connecting-Ip": "203.0.113.9",
                    "X-Forwarded-For": "8.8.8.8",
                },
            )
            self.assertEqual(loop._client_ip(), "127.0.0.1")
        finally:
            self._clear_bucket()

    def test_funnel_rotating_forwarded_for_shares_one_bucket(self):
        self._bucket("2/60")
        try:
            results = []
            for hop in range(4):
                call = _Call(
                    "127.0.0.1",
                    {
                        "Tailscale-Funnel-Request": "true",
                        "X-Forwarded-For": f"203.0.113.{hop}, 172.70.0.{hop}",
                    },
                )
                self.assertEqual(call._client_ip(), "127.0.0.1")
                results.append(admit(call._client_ip(), "/health")[0])
            self.assertEqual(results, [True, True, False, False])
        finally:
            self._clear_bucket()

    def test_funnel_requires_exactly_one_forwarded_for(self):
        for forwarded in ("", "not-an-ip", "1.2.3.4, 5.6.7.8", "junk, 1.2.3.4"):
            call = _Call(
                "127.0.0.1",
                {
                    "Tailscale-Funnel-Request": "?1",
                    "X-Forwarded-For": forwarded,
                    "Tailscale-Ingress-Src": "198.51.100.2:4431",
                },
            )
            self.assertEqual(call._client_ip(), "127.0.0.1", forwarded)
        one = _Call(
            "127.0.0.1",
            {
                "Tailscale-Funnel-Request": "?1",
                "X-Forwarded-For": "203.0.113.8",
                "Tailscale-Ingress-Src": "198.51.100.2:4431",
            },
        )
        self.assertEqual(one._client_ip(), "203.0.113.8")

    def test_cloudflared_uses_only_cf_connecting_ip(self):
        ray = _Call(
            "127.0.0.1",
            {
                "Cf-Ray": "8a1b2c3d4e5f6a7b-EWR",
                "Cf-Connecting-Ip": "203.0.113.4",
                "Tailscale-Ingress-Src": "198.51.100.2:4431",
                "X-Forwarded-For": "192.0.2.1",
            },
        )
        self.assertEqual(ray._client_ip(), "203.0.113.4")
        loop = _Call(
            "::1",
            {
                "Cdn-Loop": "cloudflare; loops=1",
                "Cf-Connecting-Ip": "203.0.113.5",
                "X-Forwarded-For": "192.0.2.8",
            },
        )
        self.assertEqual(loop._client_ip(), "203.0.113.5")
        missing = _Call(
            "127.0.0.1",
            {
                "Cf-Ray": "8a1b2c3d4e5f6a7b-EWR",
                "Cf-Connecting-Ip": "not-an-ip",
                "Tailscale-Ingress-Src": "198.51.100.2:4431",
                "X-Forwarded-For": "192.0.2.9",
            },
        )
        self.assertEqual(missing._client_ip(), "127.0.0.1")

    def test_ipv6_clients_in_one_prefix_share_one_bucket(self):
        self._bucket("1/60")
        try:

            def hit(host: str) -> bool:
                call = _Call(
                    "127.0.0.1",
                    {
                        "Tailscale-Funnel-Request": "?1",
                        "X-Forwarded-For": host,
                        "Tailscale-Ingress-Src": "198.51.100.2:4431",
                    },
                )
                return admit(call._client_ip(), "/health")[0]

            self.assertTrue(hit("2001:db8:aa:bb::1"))
            self.assertFalse(hit("2001:db8:aa:bb::ffff"))
            self.assertTrue(hit("2001:db8:aa:bc::1"))
            self.assertTrue(hit("203.0.113.10"))
            self.assertTrue(hit("203.0.113.11"))
            self.assertFalse(hit("::ffff:203.0.113.10"))
        finally:
            self._clear_bucket()


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

    def test_cleanup_refills_before_dropping_idle_buckets(self):
        saved = rate_limit._CAP
        rate_limit._CAP = 10
        os.environ["PI_PAIR_RATE_DEFAULT"] = "1/60"
        reset_limits()
        try:
            self.assertTrue(admit("198.51.100.9", "/health", now=0)[0])
            self.assertFalse(admit("198.51.100.9", "/health", now=0)[0])
            self.assertTrue(admit("198.51.100.1", "/health", now=-100)[0])
            self.assertTrue(admit("198.51.100.2", "/health", now=-100)[0])
            rate_limit._CAP = 2
            self.assertTrue(admit("198.51.100.3", "/health", now=10)[0])
            self.assertFalse(admit("198.51.100.9", "/health", now=10)[0])
        finally:
            rate_limit._CAP = saved
            os.environ.pop("PI_PAIR_RATE_DEFAULT", None)
            reset_limits()

    def test_a_full_table_still_limits_a_new_client(self):
        os.environ["PI_PAIR_RATE_CHAT"] = "12/60"
        reset_limits()
        try:
            for i in range(rate_limit._CAP + 1):
                admit(f"2001:db8:0:{i:x}::1", "/api/chat", now=1.0)
            allowed = [admit("9.9.9.9", "/api/chat", now=1.0)[0] for _ in range(30)]
            self.assertEqual(allowed.count(True), 12)
        finally:
            os.environ.pop("PI_PAIR_RATE_CHAT", None)
            reset_limits()

    def test_full_table_evicts_the_least_recently_used(self):
        saved = rate_limit._CAP
        rate_limit._CAP = 2
        os.environ["PI_PAIR_RATE_DEFAULT"] = "1/60"
        reset_limits()
        try:
            self.assertTrue(admit("203.0.113.1", "/health", now=0)[0])
            self.assertTrue(admit("203.0.113.2", "/health", now=1)[0])
            self.assertFalse(admit("203.0.113.1", "/health", now=2)[0])
            self.assertTrue(admit("203.0.113.3", "/health", now=2)[0])
            self.assertFalse(admit("203.0.113.1", "/health", now=2)[0])
            self.assertFalse(admit("203.0.113.3", "/health", now=2)[0])
            self.assertTrue(admit("203.0.113.2", "/health", now=2)[0])
        finally:
            rate_limit._CAP = saved
            os.environ.pop("PI_PAIR_RATE_DEFAULT", None)
            reset_limits()


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

    def test_funnel_redacts_health_and_hides_tools(self):
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

        hidden, _headers, _raw = self._open("GET", "/tools/health", headers=funnel)
        self.assertEqual(hidden, 404)
        shown, _headers, _raw = self._open("GET", "/docs")
        self.assertEqual(shown, 200)
        posted, _headers, _raw = self._open(
            "POST",
            "/tools/extract",
            body=b"{}",
            headers={**funnel, "content-type": "application/json"},
        )
        self.assertEqual(posted, 404)

    def test_public_tunnels_serve_api_docs(self):
        cloudflare = {"Cf-Connecting-Ip": "203.0.113.9", "Cf-Ray": "abc"}
        for headers in ({"Tailscale-Funnel-Request": "true"}, cloudflare):
            for path in ("/docs", "/docs/", "/swagger", "/openapi.json", "/swagger.json"):
                status, _headers, raw = self._open("GET", path, headers=headers)
                self.assertEqual(status, 200, (path, headers))
                self.assertNotIn(b"10.0.0.", raw, path)
            tools, _headers, _raw = self._open("GET", "/tools/health", headers=headers)
            self.assertEqual(tools, 404)

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
            path="/tools/health",
        )
        self.assertTrue(funnel._public())
        self.assertTrue(funnel._guard())
        self.assertEqual(funnel.codes, [404])
        funnel_docs = _Mesh(
            "100.100.197.18",
            {"Tailscale-Funnel-Request": "true"},
            path="/docs",
        )
        self.assertFalse(funnel_docs._guard())

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
