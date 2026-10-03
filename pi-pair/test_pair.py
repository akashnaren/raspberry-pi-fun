"""Stdlib tests for Pi 0.2 High helpers and the chat HTTP contract."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
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
    start = script.index("addEventListener('keydown'")
    end = script.index("document.querySelectorAll('.think-btn')", start)
    return script[start:end]


def _start(httpd: ThreadingHTTPServer) -> None:
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()


class OllamaFake(BaseHTTPRequestHandler):
    posts = 0
    last_payload = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.split("?")[0] != "/api/tags":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"models": [{"name": "qwen2.5:0.5b"}]}).encode()
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
            self.wfile.write(json.dumps({"message": {"content": "hel"}, "done": False}).encode() + b"\n")
            self.wfile.write(json.dumps({"message": {"content": "lo"}, "done": True}).encode() + b"\n")
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
        content = "llama:" + payload.get("model", "")
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
        self.assertFalse(model_on_peer([], "qwen2.5:0.5b", "llamacpp"))
        self.assertTrue(model_on_peer(["tiny"], "qwen2.5:0.5b", "llamacpp"))
        self.assertTrue(model_on_peer([], "qwen2.5:0.5b", "ollama"))
        self.assertTrue(model_on_peer(["qwen2.5:0.5b"], "qwen2.5:0.5b", "ollama"))
        self.assertTrue(model_on_peer(["qwen2.5:1.5b"], "qwen2.5:0.5b", "ollama"))
        self.assertFalse(model_on_peer(["tinyllama"], "qwen2.5:0.5b", "ollama"))

    def test_pin_does_not_fall_back(self):
        runtime.set_peers(DEFAULT_PEERS)
        health.peer_health = lambda peer: (False, [], "refused", peer["port"])
        with self.assertRaisesRegex(RuntimeError, "pi3 cannot be the brain"):
            pick("pi3", True, "qwen2.5:0.5b")
        with self.assertRaisesRegex(RuntimeError, "pi2 cannot be the brain"):
            pick("pi2", False, "qwen2.5:0.5b")
        with self.assertRaisesRegex(RuntimeError, "^pi4 offline$"):
            pick("pi4", True, "qwen2.5:0.5b")
        with self.assertRaisesRegex(RuntimeError, "^unknown peer pi9$"):
            pick("pi9", True, "qwen2.5:0.5b")

    def test_auto_is_pi4_only(self):
        runtime.set_peers(DEFAULT_PEERS)
        health.peer_health = lambda peer: (True, ["qwen2.5:0.5b"], None, peer["port"])
        names = [pick("auto", True, "qwen2.5:0.5b")["name"] for _ in range(3)]
        self.assertEqual(names, ["pi4", "pi4", "pi4"])

    def test_auto_miss_does_not_use_a_healthy_weak_peer(self):
        runtime.set_peers(DEFAULT_PEERS)

        def probe(peer):
            if peer["name"] == "pi4":
                return False, [], "down", peer["port"]
            return True, ["qwen2.5:0.5b"], None, peer["port"]

        health.peer_health = probe
        with self.assertRaisesRegex(RuntimeError, "pi4 unreachable on cache miss"):
            pick("auto", True, "qwen2.5:0.5b")

    def test_stream_line_parsers(self):
        self.assertEqual(ollama_delta('{"message":{"content":"hi"},"done":false}'), ("hi", False, False))
        self.assertEqual(ollama_delta('{"message":{"content":""},"done":true}'), ("", True, False))
        self.assertEqual(ollama_delta("not-json"), ("", False, True))
        self.assertEqual(llamacpp_delta("data: [DONE]"), ("", True, False))
        self.assertEqual(
            llamacpp_delta('data: {"choices":[{"delta":{"content":"yo"}}]}'),
            ("yo", False, False),
        )
        self.assertTrue(llamacpp_delta(": comment")[2])

    def test_llamacpp_model_swap(self):
        self.assertEqual(llamacpp_model({"models": ["tiny"]}, "qwen2.5:0.5b"), "tiny")
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
            return True, ["qwen2.5:0.5b"], None, peer["port"]

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


class PairHttp(unittest.TestCase):
    def setUp(self):
        self._peers = [dict(peer) for peer in runtime.PEERS]
        runtime.reset_health()
        self.servers = []
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["PI_PAIR_DATA"] = self._tmp.name
        os.environ["PI_PAIR_ROLE"] = "dataset"
        os.environ["PI_PAIR_CANNED"] = str(ROOT / "data" / "canned" / "canned_map.json")
        OllamaFake.posts = 0
        OllamaFake.last_payload = None
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
        os.environ.pop("PI_PAIR_CANNED", None)
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

    def _post(self, port, payload, headers=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, json.loads(response.read().decode())
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
        self.assertIn("<title>Pi 0.2 High</title>", html)
        self.assertIn("Pi 0.2 High", html)
        self.assertIn(runtime.MODEL, html)
        self.assertNotIn("__MODEL__", html)
        self.assertIn('data-think="low"', html)
        self.assertIn('data-think="medium"', html)
        self.assertIn('data-think="high"', html)
        self.assertIn('aria-label="Thinking"', html)
        self.assertIn('class="think-btn on" data-think="medium"', html)
        self.assertIn("Ask anything.", html)
        lowered = html.lower()
        for word in ("cache", "brain", "chip", "peer", "pi2", "pi3", "pi4"):
            self.assertNotIn(word, lowered)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/static/mesh.js", timeout=5) as response:
            script = response.read().decode()
        self.assertIn("MESH_DEFAULT_MODEL", script)
        self.assertIn("if(e.key!=='Enter') return;", script)
        self.assertIn("if(e.shiftKey) return;", script)
        self.assertIn("if(e.ctrlKey || e.metaKey)", script)
        self.assertNotIn("metaKey||e.ctrlKey", script)
        self.assertIn("/v1/flywheel/feedback", script)
        self.assertIn("Thumbs up", script)
        self.assertIn("Thumbs down", script)
        self.assertIn("think:effort", script)
        self.assertIn("X-Pi-Think", script)
        self.assertIn("let thinking='medium';", script)
        self.assertIn("el('span','pending')", script)
        self.assertIn("Searched", script)
        self.assertIn("Search failed", script)
        self.assertIn("X-Pi-Search", script)
        self.assertIn("search-note", script)
        self.assertIn("aria-label','Stop'", script)
        self.assertIn("Regenerate", script)
        self.assertIn("beginEdit", script)
        self.assertIn("'Edit'", script)
        handler = _composer_keydown(script)
        shift_at = handler.index("if(e.shiftKey) return;")
        prevent_at = handler.index("e.preventDefault();")
        send_at = handler.rindex("send();")
        newline_at = handler.index("+'\\n'+")
        self.assertLess(shift_at, prevent_at)
        self.assertLess(prevent_at, send_at)
        self.assertLess(handler.index("if(e.ctrlKey || e.metaKey)"), newline_at)
        self.assertNotIn("e.shiftKey){", handler)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
            health_body = json.loads(response.read().decode())
        self.assertEqual(health_body["peers_up"], 1)
        self.assertEqual(health_body["peers"][0]["kind"], "ollama")
        status, headers, body = self._post(
            port,
            {
                "model": "qwen2.5:0.5b",
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
        self.assertEqual(self.search_calls, ["Say hi in five words."])
        self.assertEqual(OllamaFake.last_payload["options"]["num_ctx"], 2048)
        self.assertEqual(OllamaFake.last_payload["keep_alive"], "5m")
        self.assertEqual(OllamaFake.last_payload["options"]["num_predict"], 256)
        stream = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "qwen2.5:0.5b",
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
            self.assertEqual(response.headers.get("X-Pi-Search"), "failed")
        self.assertIn("hel", raw)
        self.assertIn('"pi_search": "failed"', raw)
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
                "model": "qwen2.5:0.5b",
                "messages": [{"role": "user", "content": "novel offline probe"}],
                "stream": False,
            },
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 502)
        self.assertIn("pi4 offline", body["error"])

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
                "model": "qwen2.5:0.5b",
                "messages": [{"role": "user", "content": "Hi!"}],
                "stream": False,
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Pi-Chip"), "cache")
        self.assertEqual(headers.get("X-Pi-Peer"), "cache")
        self.assertEqual(body["pi_chip"], "cache")
        self.assertIn("Mesh assistant online", body["choices"][0]["message"]["content"])
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
                    "model": "qwen2.5:0.5b",
                    "messages": [{"role": "user", "content": "Hi!"}],
                    "stream": False,
                },
                {"X-Pi-Target": name, "X-Pi-Mesh": "on"},
            )
            self.assertEqual(status, 502)
            self.assertIn(f"{name} cannot be the brain", body["error"])
            self.assertIn("does not run a chat model", body["error"])
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
                "model": "qwen2.5:0.5b",
                "messages": [{"role": "user", "content": "a question the map has never seen"}],
                "stream": False,
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 502)
        self.assertIn("pi4 unreachable on cache miss", body["error"])
        self.assertNotIn("pi2", body["error"].split("Refusing")[0])
        self.assertEqual(OllamaFake.posts, 0)

    def test_direct_ollama_bypasses_cache(self):
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
                "model": "qwen2.5:0.5b",
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
        queued = (Path(os.environ["PI_PAIR_DATA"]) / "train" / "pending" / "queue.jsonl").read_text()
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
        expected = {"low": (0.6, 64), "medium": (0.7, 256), "high": (0.8, 768)}
        seen = {}
        for level, (temperature, num_predict) in expected.items():
            status, headers, body = self._post(
                port,
                {
                    "model": "qwen2.5:0.5b",
                    "messages": [{"role": "user", "content": "think " + level}],
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
            self.assertEqual(options["temperature"], temperature)
            self.assertEqual(options["num_predict"], num_predict)
            self.assertNotIn("think", OllamaFake.last_payload)
        self.assertEqual(len(set(seen.values())), 3)

    def test_feedback_rates_the_last_completion(self):
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
                "model": "qwen2.5:0.5b",
                "messages": [{"role": "user", "content": "label this miss"}],
                "stream": False,
            },
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200)
        answer = body["choices"][0]["message"]["content"]
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/flywheel/feedback",
            data=json.dumps({"vote": "down", "correction": "the corrected sentence"}).encode(),
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
            for line in (Path(os.environ["PI_PAIR_DATA"]) / "train" / "pending" / "queue.jsonl")
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
            for line in (Path(os.environ["PI_PAIR_DATA"]) / "train" / "pending" / "queue.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["vote"], "up")
        self.assertNotIn("correction", rows[0])
        canned = json.loads((ROOT / "data" / "canned" / "canned_map.json").read_text(encoding="utf-8"))
        self.assertIn("Mesh assistant online", canned["hi"])

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
                "model": "qwen2.5:0.5b",
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
                "model": "qwen2.5:0.5b",
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
        self.assertEqual(LlamaFake.last_payload["temperature"], 0.8)
        self.assertEqual(LlamaFake.last_payload["max_tokens"], 768)
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
        prompt = "How tall is the bench in the hall?"

        def fake(query, opener=None):
            self.search_calls.append(query)
            return {
                "status": "ok",
                "sources": [{"title": "Bench note", "url": "https://example.com/bench"}],
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
                "model": "qwen2.5:0.5b",
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
        queued = (Path(os.environ["PI_PAIR_DATA"]) / "train" / "pending" / "queue.jsonl").read_text()
        row = json.loads(queued.strip().splitlines()[-1])
        self.assertEqual(row["prompt"], prompt)
        self.assertNotIn("snippet", row["prompt"])
        self.assertEqual(row["answer"], "hello from peer")

    def test_failed_search_still_answers_locally(self):
        prompt = "What is the weather on the far pier?"

        def boom(query, opener=None):
            self.search_calls.append(query)
            raise RuntimeError("lookup down")

        pair_server.lookup_web = boom
        port = self._pi4()
        status, headers, body = self._post(
            port,
            {
                "model": "qwen2.5:0.5b",
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
                "model": "qwen2.5:0.5b",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertEqual(body["pi_search"], "failed")
        self.assertNotIn("Web search notes", json.dumps(OllamaFake.last_payload["messages"]))

    def test_followup_keeps_earlier_turns_on_pi4(self):
        port = self._pi4()
        status, headers, body = self._post(
            port,
            {
                "model": "qwen2.5:0.5b",
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
        self.assertEqual(planned["chat"]["body"]["messages"][0]["content"], "label from a bot")
        self.assertEqual(planned["chat"]["body"]["pi_target"], "auto")
        self.assertFalse(planned["chat"]["body"]["stream"])
        self.assertEqual(planned["feedback"]["body"]["vote"], "down")
        self.assertEqual(planned["feedback"]["body"]["correction"], "the sentence you wanted")
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
            for line in (Path(os.environ["PI_PAIR_DATA"]) / "train" / "pending" / "queue.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prompt"], "label from a bot")
        self.assertEqual(rows[0]["answer"], "hello from peer")
        self.assertEqual(rows[0]["vote"], "up")
        self.assertEqual(OllamaFake.posts, 1)


class ProductCopy(unittest.TestCase):
    """UI and installer keep Pi 0.2 High. READMEs stay plain and do not say Pi PAIR."""

    def test_static_and_readmes_copy(self):
        product = "Pi 0.2 High"
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
        if f"<title>{product}</title>" not in html:
            problems.append("static/index.html title is not Pi 0.2 High")
        if f'<div class="brand">{product}</div>' not in html:
            problems.append("static/index.html brand is not Pi 0.2 High")
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        if "Description=Pi 0.2 High\n" not in install:
            problems.append("install.sh Description is not Pi 0.2 High")
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
        if product in text:
            problems.append(f"{label} still says {product}")
        if "3D-printed server rack" not in text:
            problems.append(f"{label} does not mention the 3D-printed rack")
        hero = f"{prefix}rack-hero.jpg"
        if text.find(hero) == -1 or text.find(hero) > text.find(f"{prefix}rack-front.jpg"):
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
