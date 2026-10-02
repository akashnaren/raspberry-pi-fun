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
from pair.chat import llamacpp_model
from pair.config import DEFAULT_PEERS, load_peers, normalize_peer
from pair.peers import model_on_peer, pick
from pair.server import make_server
from pair.stream import llamacpp_delta, ollama_delta


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

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
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
        self.assertEqual(headers.get("X-Pi-Peer"), "pi4")
        self.assertEqual(headers.get("X-Pi-Chip"), "brain: pi4")
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertEqual(body["pi_peer"], "pi4")
        self.assertEqual(body["pi_chip"], "brain: pi4")
        self.assertEqual(OllamaFake.last_payload["options"]["num_ctx"], 2048)
        self.assertEqual(OllamaFake.last_payload["keep_alive"], "5m")
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
        queued = (Path(os.environ["PI_PAIR_DATA"]) / "train" / "pending" / "queue.jsonl").read_text()
        self.assertIn("Hi!", queued)

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


class ProductCopy(unittest.TestCase):
    """UI and installer keep Pi 0.2 High. READMEs stay plain and do not say Pi PAIR."""

    def test_static_and_readmes_copy(self):
        product = "Pi 0.2 High"
        files = [
            ROOT / "static" / "index.html",
            ROOT / "static" / "mesh.js",
            ROOT / "static" / "mesh.css",
            ROOT / "README.md",
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
        pi_readme = (ROOT / "README.md").read_text(encoding="utf-8")
        root_readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
        if not pi_readme.startswith("# pi-pair\n"):
            problems.append("pi-pair/README.md title is not pi-pair")
        for label, text, readme_dir, prefix in (
            ("pi-pair/README.md", pi_readme, ROOT, "docs/rack/"),
            ("README.md", root_readme, ROOT.parent, "pi-pair/docs/rack/"),
        ):
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
