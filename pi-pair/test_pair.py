"""Stdlib tests for pi-pair helpers and the chat HTTP contract."""
from __future__ import annotations

import json
import os
import sys
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
from pair.chat import llamacpp_model
from pair.config import DEFAULT_PEERS, load_peers, normalize_peer
from pair.peers import model_on_peer, pick
from pair.server import make_server
from pair.stream import llamacpp_delta, ollama_delta


def _start(httpd: ThreadingHTTPServer) -> None:
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()


class OllamaFake(BaseHTTPRequestHandler):
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
        runtime.reset_rr()

    def tearDown(self):
        health.peer_health = self._health
        runtime.PEERS = self._peers
        runtime.reset_health()
        runtime.reset_rr()

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
        with self.assertRaisesRegex(RuntimeError, "^pi3 offline$"):
            pick("pi3", True, "qwen2.5:0.5b")
        with self.assertRaisesRegex(RuntimeError, r"^pi2 offline \(llama.cpp\)$"):
            pick("pi2", False, "qwen2.5:0.5b")
        with self.assertRaisesRegex(RuntimeError, "^unknown peer pi9$"):
            pick("pi9", True, "qwen2.5:0.5b")

    def test_auto_round_robin(self):
        runtime.set_peers(
            [
                {"name": "pi3", "host": "10.0.0.1", "port": 11434, "kind": "ollama", "note": ""},
                {"name": "pi4", "host": "10.0.0.2", "port": 11434, "kind": "ollama", "note": ""},
            ]
        )
        health.peer_health = lambda peer: (True, ["qwen2.5:0.5b"], None, peer["port"])
        names = [pick("auto", True, "qwen2.5:0.5b")["name"] for _ in range(3)]
        self.assertEqual(names, ["pi3", "pi4", "pi3"])

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


class PairHttp(unittest.TestCase):
    def setUp(self):
        self._peers = [dict(peer) for peer in runtime.PEERS]
        runtime.reset_health()
        runtime.reset_rr()
        self.servers = []

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        runtime.PEERS = self._peers
        runtime.reset_health()
        runtime.reset_rr()

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
                    "name": "pi3",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "ollama",
                    "note": "",
                }
            ]
        )
        port = self._pair()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            html = response.read().decode()
        self.assertIn("/static/mesh.css", html)
        self.assertIn("/static/mesh.js", html)
        self.assertIn("<title>pi-pair</title>", html)
        self.assertIn("pi-pair", html)
        self.assertIn(runtime.MODEL, html)
        self.assertNotIn("__MODEL__", html)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/static/mesh.js", timeout=5) as response:
            script = response.read().decode()
        self.assertIn("MESH_DEFAULT_MODEL", script)
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
        self.assertEqual(headers.get("X-Pi-Peer"), "pi3")
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertEqual(body["pi_peer"], "pi3")
        stream = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "qwen2.5:0.5b",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                    "pi_target": "pi3",
                    "pi_mesh": "on",
                }
            ).encode(),
            headers={
                "content-type": "application/json",
                "X-Pi-Target": "pi3",
                "X-Pi-Mesh": "on",
            },
        )
        with urllib.request.urlopen(stream, timeout=5) as response:
            raw = response.read().decode()
            self.assertIn("text/event-stream", response.headers.get("content-type", ""))
            self.assertEqual(response.headers.get("X-Pi-Peer"), "pi3")
        self.assertIn("hel", raw)
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
                    "name": "pi3",
                    "host": "127.0.0.1",
                    "port": 1,
                    "kind": "ollama",
                    "note": "",
                }
            ]
        )
        port = self._pair()
        status, _headers, body = self._post(
            port,
            {
                "model": "qwen2.5:0.5b",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            },
            {"X-Pi-Target": "pi3", "X-Pi-Mesh": "on"},
        )
        self.assertEqual(status, 502)
        self.assertIn("pi3 offline", body["error"])

    def test_llamacpp_rewrites_model(self):
        peer_port = self._listen(LlamaFake)
        runtime.set_peers(
            [
                {
                    "name": "pi2",
                    "host": "127.0.0.1",
                    "port": peer_port,
                    "kind": "llamacpp",
                    "note": "llama.cpp",
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
            {"X-Pi-Target": "pi2", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Pi-Peer"), "pi2")
        self.assertEqual(body["pi_model"], "tiny")
        self.assertEqual(body["choices"][0]["message"]["content"], "llama:tiny")
        self.assertEqual(body["pi_kind"], "llamacpp")


class PlainCopy(unittest.TestCase):
    """UI, installer, and READMEs say pi-pair. No leftover product names."""

    def test_plain_names(self):
        banned = ("Pi 0.2 High", "Pi PAIR", "PI PAIR", "Meridian", "fishbowl", "world-office")
        files = [
            ROOT / "static" / "index.html",
            ROOT / "static" / "mesh.js",
            ROOT / "static" / "mesh.css",
            ROOT / "README.md",
            ROOT.parent / "README.md",
            ROOT / "install.sh",
            ROOT / "mesh-hello.sh",
            ROOT / "start.sh",
            ROOT / "mini_chat.py",
            ROOT / "pair" / "server.py",
            ROOT / "pair" / "__init__.py",
        ]
        problems = []
        for path in files:
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(ROOT.parent)
            for word in banned:
                if word in text:
                    problems.append(f"{rel} still says {word}")
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        if "<title>pi-pair</title>" not in html:
            problems.append("static/index.html title is not pi-pair")
        if '<div class="brand">pi-pair</div>' not in html:
            problems.append("static/index.html brand is not pi-pair")
        pi_readme = (ROOT / "README.md").read_text(encoding="utf-8")
        root_readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
        if not pi_readme.startswith("# pi-pair\n"):
            problems.append("pi-pair/README.md title is not pi-pair")
        for label, text in (("pi-pair/README.md", pi_readme), ("README.md", root_readme)):
            if "3D-printed server rack" not in text:
                problems.append(f"{label} does not mention the 3D-printed rack")
            if "rpi-pi2" not in text or "rpi-pi3" not in text or "rpi-pi4" not in text:
                problems.append(f"{label} does not name the Tailscale hosts")
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        if "Description=pi-pair\n" not in install:
            problems.append("install.sh Description is not pi-pair")
        if "PI_PAIR_NAME" in install:
            problems.append("install.sh still sets unused PI_PAIR_NAME")
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
