"""Stdlib tests for the Pi GPT 1.0 helpers and the chat HTTP contract."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair import health, runtime
from pair import server as pair_server
from pair.chat import llamacpp_model
from pair.config import DEFAULT_PEERS, load_peers, normalize_peer
from pair.peers import model_on_peer, pick
from pair.server import make_server
from pair.stream import llamacpp_delta, ollama_delta


def _composer_keydown(script: str) -> str:
    start = script.index('addEventListener("keydown"')
    end = script.index('querySelectorAll("[data-think]")', start)
    return script[start:end]


def _sse_payloads(raw: str) -> list[dict]:
    payloads = []
    for line in raw.splitlines():
        trimmed = line.strip()
        if not trimmed.startswith("data:"):
            continue
        data = trimmed[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payloads.append(json.loads(data))
        except json.JSONDecodeError:
            continue
    return payloads


def _statuses(raw: str) -> list[str]:
    return [
        str(item["pi_status"]) for item in _sse_payloads(raw) if item.get("pi_status")
    ]


def _start(httpd: ThreadingHTTPServer) -> None:
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()


class OllamaFake(BaseHTTPRequestHandler):
    posts = 0
    last_payload = None
    catalog = ["qwen3:0.6b"]

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.split("?")[0] != "/api/tags":
            self.send_response(404)
            self.end_headers()
            return
        names = list(type(self).catalog)
        body = json.dumps({"models": [{"name": name} for name in names]}).encode()
        self._json(body)

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode() or "{}")
        type(self).posts += 1
        type(self).last_payload = payload
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "application/x-ndjson")
            self.end_headers()
            self.wfile.write(
                json.dumps({"message": {"content": "hel"}, "done": False}).encode()
                + b"\n"
            )
            self.wfile.write(
                json.dumps(
                    {"message": {"content": "lo from peer"}, "done": True}
                ).encode()
                + b"\n"
            )
            return
        self._json(json.dumps({"message": {"content": "hello from peer"}}).encode())

    def _json(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LlamaFake(BaseHTTPRequestHandler):
    last_payload = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "tiny"}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode() or "{}")
        type(self).last_payload = payload
        content = "llama:" + str(payload.get("model") or "")
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            chunk = {
                "choices": [{"delta": {"content": content}, "finish_reason": "stop"}]
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]}
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class PairHelpers(unittest.TestCase):
    def setUp(self):
        self._peers = [dict(peer) for peer in runtime.PEERS]
        self._health = health.peer_health
        runtime.reset_health()

    def tearDown(self):
        health.peer_health = self._health
        runtime.PEERS = self._peers
        runtime.reset_health()

    def test_example_matches_builtin_fleet(self):
        example = json.loads((ROOT / "peers.example.json").read_text(encoding="utf-8"))
        self.assertEqual(example, DEFAULT_PEERS)
        self.assertEqual(load_peers(), DEFAULT_PEERS)

    def test_load_peers_file_and_wrapper(self):
        path = ROOT / "_test_peers.json"
        path.write_text(
            json.dumps(
                {
                    "peers": [
                        {
                            "name": "pi4",
                            "host": "pi4.local",
                            "port": 11434,
                            "kind": "ollama",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        try:
            loaded = load_peers(path)
        finally:
            path.unlink()
        self.assertEqual(loaded[0]["host"], "pi4.local")
        self.assertEqual(loaded[0]["note"], "")
        with self.assertRaises(ValueError):
            normalize_peer({"name": "pi2", "host": "x", "port": 1, "kind": "cuda"})

    def test_model_match_rules(self):
        self.assertFalse(model_on_peer([], "qwen3:0.6b", "llamacpp"))
        self.assertTrue(model_on_peer(["tiny"], "qwen3:0.6b", "llamacpp"))
        self.assertTrue(model_on_peer([], "qwen3:0.6b", "ollama"))
        self.assertTrue(model_on_peer(["qwen3:0.6b"], "qwen3:0.6b", "ollama"))
        self.assertTrue(model_on_peer(["qwen3:1.7b"], "qwen3:0.6b", "ollama"))
        self.assertFalse(model_on_peer(["tinyllama"], "qwen3:0.6b", "ollama"))

    def test_pin_does_not_fall_back(self):
        runtime.set_peers(DEFAULT_PEERS)
        health.peer_health = lambda peer: (False, [], "refused", peer["port"])
        with self.assertRaisesRegex(RuntimeError, "pi3 cannot be the brain"):
            pick("pi3", True, "qwen3:0.6b")
        with self.assertRaisesRegex(RuntimeError, "pi2 cannot be the brain"):
            pick("pi2", False, "qwen3:0.6b")
        with self.assertRaisesRegex(RuntimeError, "^pi4 offline$"):
            pick("pi4", True, "qwen3:0.6b")
        with self.assertRaisesRegex(RuntimeError, "^unknown peer pi9$"):
            pick("pi9", True, "qwen3:0.6b")

    def test_auto_is_pi4_only(self):
        runtime.set_peers(DEFAULT_PEERS)
        health.peer_health = lambda peer: (True, ["qwen3:0.6b"], None, peer["port"])
        names = [pick("auto", True, "qwen3:0.6b")["name"] for _ in range(3)]
        self.assertEqual(names, ["pi4", "pi4", "pi4"])

    def test_auto_miss_does_not_use_a_healthy_weak_peer(self):
        runtime.set_peers(DEFAULT_PEERS)

        def probe(peer):
            if peer["name"] == "pi4":
                return False, [], "down", peer["port"]
            return True, ["qwen3:0.6b"], None, peer["port"]

        health.peer_health = probe
        with self.assertRaisesRegex(RuntimeError, "pi4 unreachable on cache miss"):
            pick("auto", True, "qwen3:0.6b")

    def test_stream_line_parsers(self):
        self.assertEqual(
            ollama_delta('{"message":{"content":"hi"},"done":false}'),
            ("hi", False, False),
        )
        self.assertEqual(
            ollama_delta('{"message":{"content":""},"done":true}'), ("", True, False)
        )
        self.assertEqual(ollama_delta("not-json"), ("", False, True))
        self.assertEqual(llamacpp_delta("data: [DONE]"), ("", True, False))
        self.assertEqual(
            llamacpp_delta('data: {"choices":[{"delta":{"content":"yo"}}]}'),
            ("yo", False, False),
        )
        self.assertTrue(llamacpp_delta(": comment")[2])

    def test_llamacpp_model_swap(self):
        self.assertEqual(llamacpp_model({"models": ["tiny"]}, "qwen3:0.6b"), "tiny")
        self.assertEqual(llamacpp_model({"models": ["tiny"]}, "tiny"), "tiny")

    def test_pi2_alt_port_uses_injected_probe(self):
        seen = []

        def fake(url, timeout=2.5):
            seen.append(url)
            if url.endswith(":9/v1/models"):
                raise OSError("down")
            if url.endswith(":8080/v1/models"):
                return {"data": [{"id": "tiny"}]}
            raise OSError(url)

        ok, models, err, port = health.peer_health(
            {"name": "pi2", "host": "127.0.0.1", "port": 9, "kind": "llamacpp"},
            alt_ports=[8080],
            get_json=fake,
        )
        self.assertTrue(ok)
        self.assertEqual(models, ["tiny"])
        self.assertEqual(port, 8080)
        self.assertIsNone(err)
        self.assertEqual(len(seen), 2)

    def test_health_snapshot_caches_within_ttl(self):
        previous = runtime.HEALTH_CACHE_TTL
        runtime.HEALTH_CACHE_TTL = 60
        calls = {"n": 0}

        def fake(peer, *, alt_ports=None, get_json=None):
            calls["n"] += 1
            return True, ["qwen3:0.6b"], None, peer["port"]

        previous_peers = list(runtime.PEERS)
        previous_probe = health.peer_health
        try:
            runtime.set_peers(
                [
                    {
                        "name": "pi3",
                        "host": "10.0.0.1",
                        "port": 11434,
                        "kind": "ollama",
                        "note": "",
                    }
                ]
            )
            health.peer_health = fake
            first = health.snapshot_peers()
            second = health.snapshot_peers()
            self.assertEqual(calls["n"], 1)
            self.assertEqual(first, second)
            self.assertEqual(first[0]["name"], "pi3")
            self.assertTrue(first[0]["ok"])
            forced = health.snapshot_peers(force=True)
            self.assertEqual(calls["n"], 2)
            self.assertEqual(forced[0]["name"], "pi3")
        finally:
            runtime.HEALTH_CACHE_TTL = previous
            health.peer_health = previous_probe
            runtime.set_peers(previous_peers)

    def test_local_health_does_not_probe_itself(self):
        seen = []

        def fake(url, timeout=2.5):
            seen.append((url, timeout))
            return {"ok": True}

        peer = {
            "name": "pi3",
            "host": "10.0.0.228",
            "port": 18080,
            "kind": "health",
            "role": "dataset",
        }
        previous = os.environ.get("PI_PAIR_ROLE")
        try:
            os.environ["PI_PAIR_ROLE"] = "dataset"
            ok, _models, err, port = health.peer_health(peer, get_json=fake)
            self.assertTrue(ok)
            self.assertIsNone(err)
            self.assertEqual(port, 18080)
            self.assertEqual(seen, [])
            os.environ["PI_PAIR_ROLE"] = "brain"
            ok, _models, err, port = health.peer_health(peer, get_json=fake)
            self.assertTrue(ok)
            self.assertEqual(
                seen, [("http://10.0.0.228:18080/health", health.PEER_PROBE_S)]
            )
            self.assertEqual(health.PEER_PROBE_S, 2.5)
        finally:
            if previous is None:
                os.environ.pop("PI_PAIR_ROLE", None)
            else:
                os.environ["PI_PAIR_ROLE"] = previous

    def test_health_skips_queue_disk_and_load_scans(self):
        def boom(*_args, **_kwargs):
            raise AssertionError("scan on the health path")

        previous_probe = health.peer_health
        previous_peers = list(runtime.PEERS)
        try:
            health.peer_health = lambda peer, **_kwargs: (True, [], None, peer["port"])
            runtime.set_peers(
                [
                    {
                        "name": "pi3",
                        "host": "10.0.0.228",
                        "port": 18080,
                        "kind": "health",
                        "role": "dataset",
                        "generative": False,
                        "note": "",
                    }
                ]
            )
            with (
                patch("shutil.disk_usage", boom),
                patch("os.getloadavg", boom),
                patch("pair.queue._pending_jsonl", boom),
            ):
                body = pair_server.health_document()
            self.assertEqual(body["peers_up"], 1)
        finally:
            health.peer_health = previous_probe
            runtime.set_peers(previous_peers)

    def test_one_failed_probe_does_not_drop_peers_up_inside_grace(self):
        """A miss inside the short grace keeps peers_up. The peer timeout stays 2.5s."""
        self.assertEqual(health.PEER_PROBE_S, 2.5)
        self.assertGreater(health.PEER_GRACE_S, health.PEER_PROBE_S)
        self.assertLess(health.PEER_GRACE_S, 16)
        clock = {"t": 1000.0}
        calls = {"n": 0}

        def fake(peer, *, alt_ports=None, get_json=None):
            del alt_ports, get_json
            calls["n"] += 1
            if calls["n"] == 1:
                return True, [], None, peer["port"]
            return False, [], "timed out", peer["port"]

        previous_probe = health.peer_health
        previous_peers = list(runtime.PEERS)
        try:
            runtime.set_peers(
                [
                    {
                        "name": "pi3",
                        "host": "10.0.0.228",
                        "port": 18080,
                        "kind": "health",
                        "role": "dataset",
                        "generative": False,
                        "note": "",
                    }
                ]
            )
            health.peer_health = fake
            with patch("pair.health.time.monotonic", lambda: clock["t"]):
                first = health.snapshot_peers(force=True)
                self.assertEqual(sum(peer["ok"] for peer in first), 1)
                clock["t"] += 3
                second = health.snapshot_peers(force=True)
                self.assertEqual(sum(peer["ok"] for peer in second), 1)
                clock["t"] += health.PEER_GRACE_S
                third = health.snapshot_peers(force=True)
                self.assertEqual(sum(peer["ok"] for peer in third), 0)
        finally:
            health.peer_health = previous_probe
            runtime.set_peers(previous_peers)

    def test_stale_health_returns_before_a_slow_probe(self):
        """A rescan slower than the 2.5s peer timeout stays off the /health return."""
        release = threading.Event()
        entered = threading.Event()
        calls = {"n": 0}

        def fake(peer, *, alt_ports=None, get_json=None):
            del alt_ports, get_json
            calls["n"] += 1
            if calls["n"] == 1:
                return True, [], None, peer["port"]
            entered.set()
            self.assertTrue(release.wait(3))
            return False, [], "timed out", peer["port"]

        previous = runtime.HEALTH_CACHE_TTL
        previous_probe = health.peer_health
        previous_peers = list(runtime.PEERS)
        try:
            runtime.HEALTH_CACHE_TTL = 0
            runtime.set_peers(
                [
                    {
                        "name": "pi3",
                        "host": "10.0.0.228",
                        "port": 18080,
                        "kind": "health",
                        "role": "dataset",
                        "generative": False,
                        "note": "",
                    }
                ]
            )
            health.peer_health = fake
            first = health.snapshot_peers()
            self.assertEqual(sum(peer["ok"] for peer in first), 1)
            started = time.monotonic()
            second = health.snapshot_peers()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, health.PEER_PROBE_S)
            self.assertEqual(sum(peer["ok"] for peer in second), 1)
            self.assertTrue(entered.wait(1))
        finally:
            release.set()
            health.join_refresh(3)
            runtime.HEALTH_CACHE_TTL = previous
            health.peer_health = previous_probe
            runtime.set_peers(previous_peers)


class PairHttp(unittest.TestCase):
    def setUp(self):
        self._peers = [dict(peer) for peer in runtime.PEERS]
        runtime.reset_health()
        self.servers = []
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["PI_PAIR_DATA"] = self._tmp.name
        os.environ["PI_PAIR_ROLE"] = "brain"
        os.environ["PI_PAIR_REMOTE_SEARCH"] = "0"
        os.environ["PI_PAIR_CANNED"] = str(ROOT / "data" / "canned" / "canned_map.json")
        OllamaFake.posts = 0
        OllamaFake.last_payload = None
        OllamaFake.catalog = ["qwen3:0.6b"]
        self.search_calls = []
        self._lookup_web = pair_server.lookup_web

        def _stub_search(query, opener=None):
            self.search_calls.append(query)
            return {"status": "failed", "sources": [], "context": ""}

        pair_server.lookup_web = _stub_search

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        pair_server.lookup_web = self._lookup_web
        runtime.PEERS = self._peers
        runtime.reset_health()
        os.environ.pop("PI_PAIR_DATA", None)
        os.environ.pop("PI_PAIR_ROLE", None)
        os.environ.pop("PI_PAIR_REMOTE_SEARCH", None)
        os.environ.pop("PI_PAIR_CANNED", None)
        os.environ.pop("PI_PAIR_BRAIN_PORT", None)
        os.environ.pop("OLLAMA_MAX_LOADED_MODELS", None)
        self._tmp.cleanup()

    def _listen(self, handler):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.servers.append(httpd)
        _start(httpd)
        return httpd.server_address[1]

    def _pair(self):
        httpd = make_server("127.0.0.1", 0)
        self.servers.append(httpd)
        _start(httpd)
        return httpd.server_address[1]

    def _pi3_accepts_forwarded_rows(self):
        """Stand in for pi3 so a brain-role server can be checked for the queued row."""
        import pair.queue as queue

        original_row = queue.forward_row
        original_feedback = queue.forward_feedback

        def _row(payload, opener=None, timeout=1.5):
            queue.append_row(payload)
            return True

        def _feedback(payload, opener=None, timeout=1.5):
            os.environ["PI_PAIR_ROLE"] = "dataset"
            try:
                queue.apply_label(
                    str(payload.get("prompt") or ""),
                    str(payload.get("answer") or ""),
                    str(payload.get("vote") or ""),
                    str(payload.get("correction") or ""),
                    chip=str(payload.get("chip") or ""),
                    peer=str(payload.get("peer") or ""),
                )
            finally:
                os.environ["PI_PAIR_ROLE"] = "brain"
            return True

        queue.forward_row = _row
        queue.forward_feedback = _feedback

        def _restore():
            queue.forward_row = original_row
            queue.forward_feedback = original_feedback
            os.environ["PI_PAIR_ROLE"] = "brain"

        self.addCleanup(_restore)

    def _post(self, port, payload, headers=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return (
                    response.status,
                    response.headers,
                    json.loads(response.read().decode()),
                )
        except urllib.error.HTTPError as error:
            raw = error.read().decode()
            return error.code, error.headers, json.loads(raw or "{}")

    def test_ollama_chat_and_static_ui(self):
        peer_port = self._listen(OllamaFake)
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "ollama",
                    "note": "",
                    "generative": True,
                    "role": "brain",
                }
            ]
        )
        port = self._pair()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            html = response.read().decode()
        self.assertIn("/static/mesh.css", html)
        self.assertIn("/static/mesh.js", html)
        self.assertIn("<title>OpenPi</title>", html)
        self.assertNotIn("<title>OpenPi — MicroAstra</title>", html)
        self.assertIn("/static/favicon.svg", html)
        self.assertIn("/static/favicon-32.png", html)
        self.assertIn("apple-touch-icon.png", html)
        self.assertIn("OpenPi — MicroAstra", html)
        self.assertIn('aria-label="Voice"', html)
        self.assertNotIn("jsdelivr", html)
        self.assertNotIn("katex", html.lower())
        self.assertNotIn("MESH_DEFAULT_MODEL", html)
        self.assertNotIn("__MODEL__", html)
        self.assertNotIn("qwen", html.lower())
        self.assertIn('data-think="low"', html)
        self.assertIn('data-think="medium"', html)
        self.assertIn('data-think="high"', html)
        self.assertIn('aria-label="Thinking"', html)
        self.assertIn('class="think-btn on" data-think="medium"', html)
        self.assertIn('data-mode="auto"', html)
        self.assertIn('data-mode="flash"', html)
        self.assertIn('data-mode="pro"', html)
        self.assertIn('aria-label="Model mode"', html)
        self.assertIn('id="modeLabel">Auto</span>', html)
        self.assertIn("Ask anything.", html)
        lowered = html.lower()
        for word in ("cache", "brain", "chip", "peer", "pi2", "pi3", "pi4"):
            self.assertNotIn(word, lowered)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/static/mesh.js", timeout=5
        ) as response:
            script = response.read().decode()
        for needle in (
            "/v1/flywheel/feedback",
            "Thumbs up",
            "Thumbs down",
            "Searched",
            "Search failed",
            "X-Pi-Think",
            "X-Pi-Mode",
            "Waiting for a free slot",
            "X-Pi-Search",
            "search-note",
            "Retry",
            "Edit",
            "pi_status",
            "speechSynthesis",
            "webkitSpeechRecognition",
            "Stop",
        ):
            self.assertIn(needle, script, needle)
        self.assertNotIn("MESH_DEFAULT_MODEL", script)
        self.assertNotIn("qwen", script.lower())
        self.assertNotIn("Loading Pro", script)
        source = (ROOT / "web" / "src" / "main.ts").read_text(encoding="utf-8")
        self.assertIn('if (event.key !== "Enter") return;', source)
        self.assertIn("if (event.shiftKey) return;", source)
        self.assertIn("event.ctrlKey || event.metaKey", source)
        self.assertNotIn("metaKey||e.ctrlKey", source)
        self.assertIn("think: effort", source)
        self.assertIn("X-Pi-Route", source)
        settings_src = (ROOT / "web" / "src" / "settings.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn('thinking: "medium"', settings_src)
        self.assertIn('mode: "auto"', settings_src)
        self.assertIn('el("span", "pending")', source)
        self.assertIn("beginEdit", source)
        handler = _composer_keydown(source)
        shift_at = handler.index("if (event.shiftKey) return;")
        prevent_at = handler.index("event.preventDefault();")
        send_at = handler.rindex("send();")
        newline_at = handler.index('+ "\\n" +')
        self.assertLess(shift_at, prevent_at)
        self.assertLess(prevent_at, send_at)
        self.assertLess(handler.index("event.ctrlKey || event.metaKey"), newline_at)
        css = (ROOT / "static" / "mesh.css").read_text(encoding="utf-8")
        self.assertIn(".flex", css)
        self.assertIn(".stage", css)
        self.assertIn(".think-btn", css)
        self.assertIn(".mode-btn", css)
        self.assertIn(".mode-menu", css)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=5
        ) as response:
            health_body = json.loads(response.read().decode())
        self.assertEqual(health_body["mode"], "flash")
        self.assertEqual(health_body["modes"]["flash"], "qwen3:0.6b")
        self.assertEqual(health_body["modes"]["pro"], "qwen3:1.7b")
        self.assertEqual(health_body["pro_model"], "qwen3:1.7b")
        self.assertIsNotNone(health_body["pro_model"])
        self.assertIn("Fast answers for everyday questions.", html)
        self.assertIn("Slower, more careful answers for harder questions.", html)
        self.assertNotIn("__FLASH_TIP__", html)
        self.assertNotIn("__PRO_TIP__", html)
        source_page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("__FLASH_TIP__", source_page)
        self.assertIn("__PRO_TIP__", source_page)
        self.assertNotIn("qwen3:0.6b, the fast resident model.", source_page)
        self.assertNotIn("qwen3:1.7b, loaded when the question needs it.", source_page)
        self.assertEqual(health_body["peers_up"], 1)
        self.assertEqual(health_body["peers"][0]["kind"], "ollama")
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": "Say hi in five words."}],
                "stream": False,
                "pi_target": "auto",
                "pi_mesh": "on",
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Pi-Peer"), "pi4")
        self.assertEqual(headers.get("X-Pi-Chip"), "brain: pi4")
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertEqual(body["pi_peer"], "pi4")
        self.assertEqual(body["pi_chip"], "brain: pi4")
        self.assertEqual(body["pi_search"], "failed")
        self.assertIn("searching", body["pi_stages"])
        self.assertEqual(self.search_calls, ["Say hi in five words."])
        self.assertEqual(OllamaFake.last_payload["options"]["num_ctx"], 1536)
        self.assertEqual(OllamaFake.last_payload["keep_alive"], -1)
        self.assertIsInstance(OllamaFake.last_payload["keep_alive"], int)
        self.assertEqual(OllamaFake.last_payload["options"]["num_predict"], 256)
        self.assertEqual(OllamaFake.last_payload["options"]["num_thread"], 4)
        self.assertEqual(OllamaFake.last_payload["options"]["num_batch"], 128)
        stream = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "qwen3:0.6b",
                    "messages": [{"role": "user", "content": "Say hi in five words."}],
                    "stream": True,
                    "pi_target": "pi4",
                    "pi_mesh": "on",
                }
            ).encode(),
            headers={
                "content-type": "application/json",
                "X-Pi-Target": "pi4",
                "X-Pi-Mesh": "on",
            },
        )
        with urllib.request.urlopen(stream, timeout=5) as response:
            raw = response.read().decode()
            self.assertIn("text/event-stream", response.headers.get("content-type", ""))
            self.assertEqual(response.headers.get("X-Pi-Peer"), "pi4")
            self.assertEqual(response.headers.get("X-Pi-Chip"), "brain: pi4")
            self.assertIsNone(response.headers.get("X-Pi-Search"))
        self.assertEqual(OllamaFake.last_payload["keep_alive"], -1)
        self.assertIs(OllamaFake.last_payload["stream"], True)
        self.assertEqual(
            _statuses(raw), ["thinking", "searching", "searching", "answering"]
        )
        self.assertLess(raw.index('"pi_status": "answering"'), raw.index("hel"))
        self.assertIn("hel", raw)
        self.assertIn('"pi_search"', raw)
        self.assertIn('"pi_tool": "search"', raw)
        self.assertIn("lo", raw)
        self.assertIn("data: [DONE]", raw)
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/static/../mini_chat.py")
        escaped = conn.getresponse()
        escaped.read()
        self.assertEqual(escaped.status, 404)
        conn.close()

    def test_pinned_offline_is_502(self):
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": 1,
                    "kind": "ollama",
                    "note": "",
                    "generative": True,
                    "role": "brain",
                }
            ]
        )
        port = self._pair()
        status, _headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": "novel offline probe"}],
                "stream": False,
            },
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 502)
        self.assertEqual(body["error"], "The chat service is not reachable. Try again.")
        self.assertNotIn("pi4", body["error"])

    def test_cache_hit_skips_pi4(self):
        peer_port = self._listen(OllamaFake)
        OllamaFake.posts = 0
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "ollama",
                    "generative": True,
                    "role": "brain",
                    "note": "",
                }
            ]
        )
        port = self._pair()
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": "Hi!"}],
                "stream": False,
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Pi-Chip"), "cache")
        self.assertEqual(headers.get("X-Pi-Peer"), "cache")
        self.assertEqual(body["pi_chip"], "cache")
        self.assertEqual(
            body["choices"][0]["message"]["content"], "Hi. What can I help you with?"
        )
        lowered_hit = body["choices"][0]["message"]["content"].lower()
        for word in ("mesh", "board", "cache", "brain", "chip", "peer"):
            self.assertNotIn(word, lowered_hit)
        self.assertEqual(OllamaFake.posts, 0)
        self.assertEqual(self.search_calls, [])
        self.assertIsNone(headers.get("X-Pi-Search"))
        self.assertNotIn("pi_search", body)
        queue = Path(os.environ["PI_PAIR_DATA"]) / "train" / "pending" / "queue.jsonl"
        self.assertFalse(queue.exists())

    def test_pin_weak_peer_rejects(self):
        peer_port = self._listen(OllamaFake)
        OllamaFake.posts = 0
        runtime.set_peers(
            [
                {
                    "name": "pi2",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "ollama",
                    "generative": True,
                    "role": "brain",
                    "note": "",
                },
                {
                    "name": "pi3",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "ollama",
                    "generative": True,
                    "role": "brain",
                    "note": "",
                },
            ]
        )
        port = self._pair()
        for name in ("pi2", "pi3"):
            status, _headers, body = self._post(
                port,
                {
                    "model": "qwen3:0.6b",
                    "messages": [{"role": "user", "content": "Hi!"}],
                    "stream": False,
                },
                {"X-Pi-Target": name, "X-Pi-Mesh": "on"},
            )
            self.assertEqual(status, 502)
            self.assertEqual(body["error"], "That machine cannot answer chats.")
            self.assertNotIn(name, body["error"])
        self.assertEqual(OllamaFake.posts, 0)

    def test_cache_miss_pi4_down_does_not_call_pi3(self):
        peer_port = self._listen(OllamaFake)
        OllamaFake.posts = 0
        runtime.set_peers(
            [
                {
                    "name": "pi3",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "ollama",
                    "generative": False,
                    "role": "dataset",
                    "note": "",
                },
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": 1,
                    "kind": "ollama",
                    "generative": True,
                    "role": "brain",
                    "note": "",
                },
            ]
        )
        port = self._pair()
        status, _headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [
                    {"role": "user", "content": "a question the map has never seen"}
                ],
                "stream": False,
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 502)
        self.assertEqual(body["error"], "The chat service is not reachable. Try again.")
        self.assertNotIn("pi4", body["error"])
        self.assertNotIn("pi2", body["error"])
        self.assertNotIn("pi3", body["error"])
        self.assertEqual(OllamaFake.posts, 0)

    def test_direct_ollama_bypasses_cache(self):
        self._pi3_accepts_forwarded_rows()
        peer_port = self._listen(OllamaFake)
        OllamaFake.posts = 0
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "ollama",
                    "generative": True,
                    "role": "brain",
                    "note": "",
                }
            ]
        )
        port = self._pair()
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": "Hi!"}],
                "stream": False,
            },
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Pi-Chip"), "brain: pi4")
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertEqual(OllamaFake.posts, 1)
        self.assertEqual(self.search_calls, [])
        self.assertIsNone(headers.get("X-Pi-Search"))
        queued = (
            Path(os.environ["PI_PAIR_DATA"]) / "train" / "pending" / "queue.jsonl"
        ).read_text()
        self.assertIn("Hi!", queued)

    def test_think_level_changes_num_predict(self):
        peer_port = self._listen(OllamaFake)
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "ollama",
                    "generative": True,
                    "role": "brain",
                    "note": "",
                }
            ]
        )
        port = self._pair()
        expected = {
            "low": (False, 0.3, 0.8, 160),
            "medium": (False, 0.3, 0.8, 256),
            "high": (False, 0.3, 0.8, 512),
        }
        seen = {}
        for level, (think, temperature, top_p, num_predict) in expected.items():
            status, headers, body = self._post(
                port,
                {
                    "model": "qwen3:0.6b",
                    "messages": [
                        {"role": "user", "content": "Explain why the level is " + level}
                    ],
                    "stream": False,
                    "think": level,
                    "temperature": 0.7,
                    "max_tokens": 256,
                },
                {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("X-Pi-Think"), level)
            self.assertEqual(body["pi_think"], level)
            options = OllamaFake.last_payload["options"]
            seen[level] = options["num_predict"]
            self.assertEqual(OllamaFake.last_payload["think"], think)
            self.assertEqual(options["temperature"], temperature)
            self.assertEqual(options["top_p"], top_p)
            self.assertEqual(options["top_k"], 20)
            self.assertEqual(options["presence_penalty"], 0)
            self.assertEqual(options["num_predict"], num_predict)
        self.assertEqual(len(set(seen.values())), 3)
        status, _headers, _body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "think": "medium",
            },
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(OllamaFake.last_payload["think"])
        self.assertEqual(OllamaFake.last_payload["options"]["temperature"], 0.3)
        self.assertEqual(OllamaFake.last_payload["options"]["num_predict"], 256)

    def test_feedback_rates_the_last_completion(self):
        self._pi3_accepts_forwarded_rows()
        peer_port = self._listen(OllamaFake)
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "ollama",
                    "generative": True,
                    "role": "brain",
                    "note": "",
                }
            ]
        )
        port = self._pair()
        status, _headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": "label this miss"}],
                "stream": False,
            },
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200)
        answer = body["choices"][0]["message"]["content"]
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/flywheel/feedback",
            data=json.dumps(
                {"vote": "down", "correction": "the corrected sentence"}
            ).encode(),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            labeled = json.loads(response.read().decode())
            self.assertEqual(response.status, 200)
        self.assertEqual(labeled["prompt"], "label this miss")
        self.assertEqual(labeled["answer"], answer)
        self.assertEqual(labeled["vote"], "down")
        self.assertEqual(labeled["correction"], "the corrected sentence")
        rows = [
            json.loads(line)
            for line in (
                Path(os.environ["PI_PAIR_DATA"]) / "train" / "pending" / "queue.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prompt"], "label this miss")
        self.assertEqual(rows[0]["answer"], answer)
        self.assertEqual(rows[0]["vote"], "down")
        self.assertEqual(rows[0]["correction"], "the corrected sentence")
        again = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/flywheel/feedback",
            data=json.dumps(
                {"vote": "up", "prompt": "label this miss", "answer": answer}
            ).encode(),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(again, timeout=5) as response:
            updated = json.loads(response.read().decode())
            self.assertEqual(response.status, 200)
        self.assertEqual(updated["vote"], "up")
        self.assertEqual(updated["correction"], "")
        rows = [
            json.loads(line)
            for line in (
                Path(os.environ["PI_PAIR_DATA"]) / "train" / "pending" / "queue.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vote"], "up")
        self.assertNotIn("correction", rows[0])
        canned = json.loads(
            (ROOT / "data" / "canned" / "canned_map.json").read_text(encoding="utf-8")
        )
        self.assertEqual(canned["hi"], "Hi. What can I help you with?")

    def test_llamacpp_rewrites_model(self):
        peer_port = self._listen(LlamaFake)
        runtime.set_peers(
            [
                {
                    "name": "edge",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "llamacpp",
                    "note": "",
                    "generative": True,
                    "role": "brain",
                }
            ]
        )
        port = self._pair()
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "max_tokens": 8,
            },
            {"X-Pi-Target": "edge", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Pi-Peer"), "edge")
        self.assertEqual(body["pi_model"], "tiny")
        self.assertEqual(body["choices"][0]["message"]["content"], "llama:tiny")
        self.assertEqual(body["pi_kind"], "llamacpp")
        self.assertEqual(LlamaFake.last_payload["max_tokens"], 8)
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": "hi again"}],
                "stream": False,
                "think": "high",
                "temperature": 0.2,
                "max_tokens": 8,
            },
            {"X-Pi-Target": "edge", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Pi-Think"), "high")
        self.assertEqual(body["pi_think"], "high")
        self.assertEqual(LlamaFake.last_payload["temperature"], 0.3)
        self.assertEqual(LlamaFake.last_payload["max_tokens"], 512)
        self.assertNotIn("think", LlamaFake.last_payload)

    def _pi4(self):
        peer_port = self._listen(OllamaFake)
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "ollama",
                    "generative": True,
                    "role": "brain",
                    "note": "",
                }
            ]
        )
        return self._pair()

    def test_miss_puts_search_snippets_in_the_prompt(self):
        self._pi3_accepts_forwarded_rows()
        prompt = "Search for how tall the bench in the hall is"

        def fake(query, opener=None):
            self.search_calls.append(query)
            return {
                "status": "ok",
                "sources": [
                    {"title": "Bench note", "url": "https://example.com/bench"}
                ],
                "context": (
                    "Web search notes.\n"
                    "- Bench note (https://example.com/bench): a short snippet about the bench"
                ),
            }

        pair_server.lookup_web = fake
        port = self._pi4()
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.search_calls, [prompt])
        blob = json.dumps(OllamaFake.last_payload["messages"])
        self.assertIn("a short snippet about the bench", blob)
        self.assertIn("https://example.com/bench", blob)
        self.assertEqual(body["pi_search"], "ok")
        self.assertEqual(body["pi_sources"][0]["url"], "https://example.com/bench")
        self.assertEqual(headers.get("X-Pi-Search"), "ok")
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        queued = (
            Path(os.environ["PI_PAIR_DATA"]) / "train" / "pending" / "queue.jsonl"
        ).read_text()
        row = json.loads(queued.strip().splitlines()[-1])
        self.assertEqual(row["prompt"], prompt)
        self.assertNotIn("snippet", row["prompt"])
        self.assertEqual(row["answer"], "hello from peer")

    def test_failed_search_still_answers_locally(self):
        prompt = "What is the latest weather on the far pier?"

        def boom(query, opener=None):
            self.search_calls.append(query)
            raise RuntimeError("lookup down")

        pair_server.lookup_web = boom
        port = self._pi4()
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertEqual(body["pi_search"], "failed")
        self.assertEqual(headers.get("X-Pi-Search"), "failed")
        self.assertEqual(body["pi_sources"], [])
        messages = OllamaFake.last_payload["messages"]
        self.assertTrue(any(item.get("content") == prompt for item in messages))
        self.assertNotIn("Web search notes", json.dumps(messages))

        def empty(query, opener=None):
            return {"status": "failed", "sources": [], "context": ""}

        pair_server.lookup_web = empty
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertEqual(body["pi_search"], "failed")
        self.assertNotIn(
            "Web search notes", json.dumps(OllamaFake.last_payload["messages"])
        )

    def test_followup_keeps_earlier_turns_on_pi4(self):
        port = self._pi4()
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [
                    {"role": "user", "content": "What changed?"},
                    {"role": "assistant", "content": "A short note."},
                    {"role": "user", "content": "Hi!"},
                ],
                "stream": False,
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertNotEqual(headers.get("X-Pi-Chip"), "cache")
        self.assertEqual(OllamaFake.posts, 1)
        blob = json.dumps(OllamaFake.last_payload["messages"])
        self.assertIn("What changed?", blob)
        self.assertIn("A short note.", blob)
        self.assertIn("Hi!", blob)

    def test_chat_label_script_votes_the_reply(self):
        import subprocess

        self._pi3_accepts_forwarded_rows()

        port = self._pi4()
        script = ROOT / "scripts" / "chat_label.py"
        dry = subprocess.run(
            [
                sys.executable,
                str(script),
                "--dry-run",
                "--base",
                "http://127.0.0.1:1",
                "--prompt",
                "label from a bot",
                "--vote",
                "down",
                "--correction",
                "the sentence you wanted",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(dry.returncode, 0, dry.stderr)
        planned = json.loads(dry.stdout)
        self.assertTrue(planned["dry_run"])
        self.assertEqual(planned["chat"]["headers"]["X-Pi-Target"], "auto")
        self.assertEqual(planned["chat"]["headers"]["X-Pi-Mesh"], "on")
        self.assertEqual(planned["chat"]["headers"]["X-Pi-Mode"], "flash")
        self.assertEqual(planned["chat"]["body"]["mode"], "flash")
        self.assertEqual(
            planned["chat"]["body"]["messages"][0]["content"], "label from a bot"
        )
        self.assertEqual(planned["chat"]["body"]["pi_target"], "auto")
        self.assertFalse(planned["chat"]["body"]["stream"])
        self.assertEqual(planned["feedback"]["body"]["vote"], "down")
        self.assertEqual(
            planned["feedback"]["body"]["correction"], "the sentence you wanted"
        )
        self.assertIn("/v1/chat/completions", planned["chat"]["url"])
        self.assertIn("/v1/flywheel/feedback", planned["feedback"]["url"])
        live = subprocess.run(
            [
                sys.executable,
                str(script),
                "--base",
                f"http://127.0.0.1:{port}",
                "--prompt",
                "label from a bot",
                "--vote",
                "up",
                "--timeout",
                "10",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(live.returncode, 0, live.stderr)
        labeled = json.loads(live.stdout)
        self.assertEqual(labeled["vote"], "up")
        self.assertEqual(labeled["prompt"], "label from a bot")
        self.assertEqual(labeled["answer"], "hello from peer")
        rows = [
            json.loads(line)
            for line in (
                Path(os.environ["PI_PAIR_DATA"]) / "train" / "pending" / "queue.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prompt"], "label from a bot")
        self.assertEqual(rows[0]["answer"], "hello from peer")
        self.assertEqual(rows[0]["vote"], "up")
        self.assertEqual(OllamaFake.posts, 1)

    def test_health_and_dataset_forward_and_do_not_search(self):
        peer_port = self._listen(OllamaFake)
        OllamaFake.posts = 0
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "10.0.0.166",
                    "port": 11434,
                    "kind": "ollama",
                    "generative": True,
                    "role": "brain",
                    "note": "",
                }
            ]
        )
        self.assertEqual(
            pair_server.brain_chat_url(),
            "http://10.0.0.166:18080/v1/chat/completions",
        )
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "ollama",
                    "generative": True,
                    "role": "brain",
                    "note": "",
                }
            ]
        )
        relayed = []
        original = pair_server.relay_chat

        def fake_relay(payload, target, mesh, mode=""):
            relayed.append(
                {
                    "target": target,
                    "mesh": mesh,
                    "mode": mode,
                    "body": json.loads(payload.decode() or "{}"),
                }
            )
            body = json.dumps(
                {
                    "choices": [{"message": {"content": "from pi4"}}],
                    "pi_search": "ok",
                }
            ).encode()
            return 200, {"content-type": "application/json", "X-Pi-Search": "ok"}, body

        pair_server.relay_chat = fake_relay
        self.addCleanup(lambda: setattr(pair_server, "relay_chat", original))
        for role in ("health", "dataset"):
            os.environ["PI_PAIR_ROLE"] = role
            port = self._pair()
            status, headers, body = self._post(
                port,
                {
                    "model": "qwen3:0.6b",
                    "messages": [{"role": "user", "content": "Hi!"}],
                    "stream": False,
                },
                {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
            )
            self.assertEqual(status, 200, role)
            self.assertEqual(headers.get("X-Pi-Chip"), "cache", role)
            self.assertEqual(relayed, [], role)
            status, headers, body = self._post(
                port,
                {
                    "model": "qwen3:0.6b",
                    "messages": [
                        {"role": "user", "content": "a question the map has never seen"}
                    ],
                    "stream": False,
                },
                {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
            )
            self.assertEqual(status, 200, role)
            self.assertEqual(body["choices"][0]["message"]["content"], "from pi4", role)
            self.assertEqual(headers.get("X-Pi-Search"), "ok", role)
            self.assertEqual(len(relayed), 1, role)
            self.assertEqual(relayed[0]["target"], "auto", role)
            self.assertEqual(relayed[0]["mesh"], "on", role)
            self.assertEqual(
                relayed[0]["body"]["messages"][0]["content"],
                "a question the map has never seen",
            )
            relayed.clear()
        self.assertEqual(self.search_calls, [])
        self.assertEqual(OllamaFake.posts, 0)
        pair_server.relay_chat = original
        os.environ["PI_PAIR_ROLE"] = "brain"
        os.environ["PI_PAIR_BRAIN_PORT"] = "1"
        with self.assertRaises(RuntimeError) as caught:
            original(b"{}", "auto", "on")
        self.assertIn("pi4 unreachable on cache miss", str(caught.exception))
        self.assertEqual(OllamaFake.posts, 0)

    def _stream_raw(self, port, content, headers=None, extra=None):
        payload = {
            "model": "qwen3:0.6b",
            "messages": [{"role": "user", "content": content}],
            "stream": True,
        }
        if extra:
            payload.update(extra)
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json", **(headers or {})},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.headers, response.read().decode()

    def test_status_events_follow_the_work(self):
        port = self._pi4()
        headers, raw = self._stream_raw(
            port,
            "Search for where the hall bench is",
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(headers.get("X-Pi-Peer"), "pi4")
        self.assertIsNone(headers.get("X-Pi-Search"))
        self.assertEqual(
            _statuses(raw), ["thinking", "searching", "searching", "answering"]
        )
        self.assertLess(
            raw.index('"pi_status": "thinking"'), raw.index('"pi_status": "searching"')
        )
        self.assertLess(
            raw.index('"pi_status": "searching"'), raw.index('"pi_status": "answering"')
        )
        self.assertLess(raw.index('"pi_status": "answering"'), raw.index("hel"))
        tools = [
            item.get("pi_tool")
            for item in _sse_payloads(raw)
            if item.get("pi_status") == "searching"
        ]
        self.assertEqual(tools, ["search", "search"])
        searched = [
            item for item in _sse_payloads(raw) if item.get("pi_search") == "failed"
        ]
        self.assertTrue(searched)
        self.assertEqual(searched[0]["pi_sources"], [])
        final = _sse_payloads(raw)[-1]
        self.assertEqual(final["pi_stages"], ["thinking", "searching", "answering"])
        self.assertIn("data: [DONE]", raw)

        self.search_calls.clear()
        OllamaFake.posts = 0
        _headers, hit = self._stream_raw(
            port,
            "Hi!",
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(_statuses(hit), ["answering"])
        self.assertNotIn("searching", _statuses(hit))
        self.assertNotIn("pi_search", hit)
        self.assertEqual(self.search_calls, [])
        self.assertEqual(OllamaFake.posts, 0)
        self.assertIn("Hi. What can I help you with?", hit)
        self.assertNotIn("mesh", hit.lower())

        self.search_calls.clear()
        _headers, direct = self._stream_raw(
            port,
            "a direct line",
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(_statuses(direct), ["thinking", "answering"])
        self.assertNotIn('"pi_status": "searching"', direct)
        self.assertEqual(self.search_calls, [])
        self.assertEqual(OllamaFake.posts, 1)

        status, response_headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [
                    {"role": "user", "content": "Search for how tall the hall bench is"}
                ],
                "stream": False,
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_stages"], ["thinking", "searching", "answering"])
        self.assertEqual(body["pi_search"], "failed")
        self.assertEqual(response_headers.get("X-Pi-Search"), "failed")

        status, response_headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": "Hi!"}],
                "stream": False,
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_stages"], ["answering"])
        self.assertNotIn("pi_search", body)
        self.assertIsNone(response_headers.get("X-Pi-Search"))

    def test_search_note_is_capped_before_prefill(self):
        from pair.knobs import search_note_limit

        limit = search_note_limit()
        self.assertEqual(limit, 720)
        prompt = "Search for how wide the east window is."
        page = "snippet " * 400

        def fake(query, opener=None):
            self.search_calls.append(query)
            return {
                "status": "ok",
                "sources": [
                    {"title": "Window note", "url": "https://example.com/window"}
                ],
                "context": "Web search notes.\n" + page,
            }

        pair_server.lookup_web = fake
        port = self._pi4()
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_stages"], ["thinking", "searching", "answering"])
        self.assertEqual(body["pi_sources"][0]["url"], "https://example.com/window")
        self.assertEqual(headers.get("X-Pi-Search"), "ok")
        messages = OllamaFake.last_payload["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertTrue(messages[0]["content"].startswith("You are OpenPi"))
        notes = messages[-2]
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(notes["role"], "system")
        self.assertTrue(notes["content"].startswith("Notes:"))
        self.assertIn("Web search notes.", notes["content"])
        self.assertLessEqual(len(notes["content"]), limit + len("Notes:\n"))
        self.assertIn("snippet", notes["content"])
        self.assertLess(len(notes["content"]), len(page))

    def test_math_is_answered_by_the_model(self):
        prompt = (
            "A spherical balloon is being inflated with gas at a constant rate of "
            "12 cubic centimeters per second. Find the exact rate at which the radius "
            "is increasing when the surface area is 36 pi square centimeters."
        )

        def fake(query, opener=None):
            self.search_calls.append(query)
            return {
                "status": "ok",
                "sources": [
                    {"title": "Balloon note", "url": "https://example.com/balloon"}
                ],
                "context": "Text from the first page:\ndr/dt = 1/(3 pi)",
            }

        pair_server.lookup_web = fake
        port = self._pi4()
        OllamaFake.posts = 0
        self.search_calls.clear()
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertGreaterEqual(OllamaFake.posts, 1)
        self.assertEqual(self.search_calls, [prompt])
        self.assertIn("searching", body["pi_stages"])
        self.assertEqual(headers.get("X-Pi-Peer"), "pi4")
        self.assertEqual(headers.get("X-Pi-Search"), "ok")

        OllamaFake.posts = 0
        self.search_calls.clear()
        _headers, direct = self._stream_raw(
            port,
            prompt,
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(_statuses(direct), ["thinking", "answering"])
        self.assertNotIn('"pi_status": "searching"', direct)
        self.assertIn("hel", direct)
        self.assertIn("lo from peer", direct)
        self.assertEqual(self.search_calls, [])
        self.assertEqual(OllamaFake.posts, 1)

    def test_math_stream_uses_the_model(self):
        prompt = (
            "A spherical balloon is inflated at 12 cubic centimeters per second. "
            "Find the exact rate at which the radius grows when the surface area "
            "is 36 pi square centimeters."
        )

        def fake(query, opener=None):
            self.search_calls.append(query)
            return {"status": "ok", "sources": [], "context": "dr/dt = 1/(3 pi)"}

        pair_server.lookup_web = fake
        port = self._pi4()
        OllamaFake.posts = 0
        self.search_calls.clear()
        headers, raw = self._stream_raw(
            port,
            prompt,
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(headers.get("X-Pi-Peer"), "pi4")
        self.assertEqual(
            _statuses(raw), ["thinking", "searching", "searching", "answering"]
        )
        self.assertIn("hel", raw)
        self.assertIn("lo from peer", raw)
        self.assertEqual(OllamaFake.posts, 1)
        self.assertEqual(self.search_calls, [prompt])

    def _brain(self):
        peer_port = self._listen(OllamaFake)
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "ollama",
                    "generative": True,
                    "role": "brain",
                    "note": "",
                }
            ]
        )
        return self._pair()

    def test_canned_hit_does_not_load_a_chat_model(self):
        OllamaFake.posts = 0
        port = self._brain()
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:1.7b",
                "pi_mode": "auto",
                "messages": [{"role": "user", "content": "Hi!"}],
                "stream": False,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Pi-Chip"), "cache")
        self.assertEqual(body["pi_model"], "canned")
        self.assertEqual(body["pi_mode"], "auto")
        self.assertEqual(body["pi_route"], "canned")
        self.assertNotIn("pi_resident", body)
        self.assertEqual(OllamaFake.posts, 0)

    def test_auto_easy_uses_flash_and_explicit_pro_overrides(self):
        port = self._brain()
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "pi_mode": "auto",
                "messages": [{"role": "user", "content": "nice weather on the porch"}],
                "stream": False,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Pi-Peer"), "pi4")
        self.assertEqual(body["pi_mode"], "auto")
        self.assertEqual(body["pi_route"], "flash")
        self.assertEqual(headers.get("X-Pi-Route"), "flash")
        self.assertNotIn("pi_resident", body)
        self.assertEqual(OllamaFake.last_payload["model"], "qwen3:0.6b")
        OllamaFake.catalog = ["qwen3:0.6b", "qwen3:1.7b"]
        runtime.reset_health()
        status, _headers, body = self._post(
            port,
            {
                "pi_mode": "pro",
                "messages": [{"role": "user", "content": "nice weather on the porch"}],
                "stream": False,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_mode"], "pro")
        self.assertEqual(body["pi_route"], "pro")
        self.assertEqual(OllamaFake.last_payload["model"], "qwen3:1.7b")

    def test_auto_hard_uses_pro_when_present_and_flash_when_it_is_not(self):
        hard = "Write a python function that reverses a list."
        port = self._brain()
        status, _headers, body = self._post(
            port,
            {
                "pi_mode": "auto",
                "messages": [{"role": "user", "content": hard}],
                "stream": False,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_route"], "flash")
        self.assertEqual(OllamaFake.last_payload["model"], "qwen3:0.6b")
        OllamaFake.catalog = ["qwen3:0.6b", "qwen3:1.7b"]
        runtime.reset_health()
        os.environ["OLLAMA_MAX_LOADED_MODELS"] = "2"
        status, headers, body = self._post(
            port,
            {
                "pi_mode": "auto",
                "think": "high",
                "messages": [{"role": "user", "content": hard}],
                "stream": False,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_mode"], "auto")
        self.assertEqual(body["pi_route"], "flash")
        self.assertEqual(headers.get("X-Pi-Route"), "flash")
        self.assertNotIn("pi_resident", body)
        self.assertEqual(body["pi_think"], "high")
        self.assertEqual(OllamaFake.last_payload["model"], "qwen3:0.6b")
        self.assertFalse(OllamaFake.last_payload["think"])
        self.assertEqual(OllamaFake.last_payload["options"]["temperature"], 0.3)
        self.assertEqual(OllamaFake.last_payload["options"]["top_p"], 0.8)
        self.assertEqual(OllamaFake.last_payload["options"]["top_k"], 20)
        self.assertEqual(OllamaFake.last_payload["options"]["presence_penalty"], 0)
        self.assertEqual(OllamaFake.last_payload["options"]["num_predict"], 512)
        status, headers, body = self._post(
            port,
            {
                "pi_mode": "pro",
                "think": "high",
                "messages": [{"role": "user", "content": hard}],
                "stream": False,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_mode"], "pro")
        self.assertEqual(body["pi_route"], "pro")
        self.assertEqual(headers.get("X-Pi-Route"), "pro")
        self.assertEqual(OllamaFake.last_payload["model"], "qwen3:1.7b")
        self.assertFalse(OllamaFake.last_payload["think"])
        self.assertEqual(OllamaFake.last_payload["options"]["temperature"], 0.5)
        self.assertEqual(OllamaFake.last_payload["options"]["presence_penalty"], 0.5)
        os.environ["OLLAMA_MAX_LOADED_MODELS"] = "3"
        runtime.reset_health()
        status, _headers, body = self._post(
            port,
            {
                "pi_mode": "flash",
                "messages": [{"role": "user", "content": hard}],
                "stream": False,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_mode"], "flash")
        self.assertEqual(body["pi_route"], "flash")
        self.assertNotIn("pi_resident", body)
        self.assertEqual(OllamaFake.last_payload["model"], "qwen3:0.6b")

    def test_flash_search_keeps_three_sources(self):
        def fake(query, opener=None):
            self.search_calls.append(query)
            return {
                "status": "ok",
                "context": "notes about the bench",
                "sources": [
                    {"title": f"T{i}", "url": f"https://ex{i}.test/a"} for i in range(5)
                ],
            }

        pair_server.lookup_web = fake
        port = self._brain()
        status, _headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [
                    {"role": "user", "content": "where is the current long bench"}
                ],
                "stream": False,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(body["pi_sources"]), 3)
        self.assertEqual(body["pi_sources"][2]["url"], "https://ex2.test/a")

    def test_search_sources_stop_at_eight(self):
        def fake(query, opener=None):
            self.search_calls.append(query)
            return {
                "status": "ok",
                "context": "notes about the bench",
                "sources": [
                    {"title": f"T{i}", "url": f"https://ex{i}.test/a"}
                    for i in range(12)
                ],
            }

        pair_server.lookup_web = fake
        OllamaFake.catalog = ["qwen3:0.6b", "qwen3:1.7b"]
        port = self._brain()
        status, _headers, body = self._post(
            port,
            {
                "mode": "pro",
                "model": "qwen3:1.7b",
                "messages": [
                    {"role": "user", "content": "where is the current long bench"}
                ],
                "stream": False,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(body["pi_sources"]), 8)
        self.assertEqual(body["pi_sources"][0]["url"], "https://ex0.test/a")
        self.assertEqual(body["pi_sources"][7]["url"], "https://ex7.test/a")
        self.assertEqual(body["pi_mode"], "pro")
        self.assertEqual(OllamaFake.last_payload["model"], "qwen3:1.7b")


class ProductCopy(unittest.TestCase):
    """The page title is OpenPi — MicroAstra. READMEs stay plain and do not say Pi PAIR."""

    def test_static_and_readmes_copy(self):
        product = "OpenPi — MicroAstra"
        retired = "Pi 0.2 High"
        files = [
            ROOT / "static" / "index.html",
            ROOT / "static" / "mesh.js",
            ROOT / "static" / "mesh.css",
            ROOT.parent / "README.md",
            ROOT / "install.sh",
            ROOT / "mesh-hello.sh",
            ROOT / "start.sh",
        ]
        problems = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(ROOT.parent)
            if "Pi PAIR" in text or "PI PAIR" in text:
                problems.append(f"{rel} still says Pi PAIR")
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        if "<title>OpenPi</title>" not in html:
            problems.append("static/index.html title is not OpenPi")
        if f"<title>{product}</title>" in html:
            problems.append("static/index.html tab title still includes MicroAstra")
        if f'id="brandName">{product}</span>' not in html:
            problems.append("static/index.html brand is not OpenPi — MicroAstra")
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        if "Description=Pi GPT 1.0\n" not in install:
            problems.append("install.sh Description is not Pi GPT 1.0")
        readme_images = (
            "rack-hero.jpg",
            "rack-front.jpg",
            "rack-top.jpg",
        )
        if (ROOT / "README.md").exists():
            problems.append("pi-pair/README.md competes with the repo root README")
        root_names = [
            path.name
            for path in ROOT.parent.iterdir()
            if path.is_file() and path.name.lower() == "readme.md"
        ]
        if root_names != ["README.md"]:
            problems.append(f"repo root README set is {root_names}")
        root_readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
        label = "README.md"
        text = root_readme
        readme_dir = ROOT.parent
        prefix = "pi-pair/docs/rack/"
        if not text.startswith("# pi-pair\n"):
            problems.append("README.md title is not pi-pair")
        if retired in text:
            problems.append(f"{label} still says {retired}")
        if "3D-printed server rack" not in text:
            problems.append(f"{label} does not mention the 3D-printed rack")
        hero = f"{prefix}rack-hero.jpg"
        if text.find(hero) == -1 or text.find(hero) > text.find(
            f"{prefix}rack-front.jpg"
        ):
            problems.append(f"{label} hero is not rack-hero.jpg")
        if "-render.jpg" in text:
            problems.append(f"{label} still links a CGI render")
        for name in readme_images:
            rel = f"{prefix}{name}"
            if f"]({rel})" not in text:
                problems.append(f"{label} missing image {rel}")
            if not (readme_dir / rel).is_file():
                problems.append(f"missing {rel}")
        for name in (
            "rack-hero-render.jpg",
            "rack-front-render.jpg",
            "rack-top-render.jpg",
            "rack-hero-readme.jpg",
            "rack-hero-studio.jpg",
            "rack-front-ports.jpg",
            "rack-front-ports-readme.jpg",
            "rack-front-ports-studio.jpg",
            "rack-top-readme.jpg",
            "rack-top-studio.jpg",
        ):
            if (ROOT / "docs" / "rack" / name).exists():
                problems.append(f"old photo still present: docs/rack/{name}")
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
