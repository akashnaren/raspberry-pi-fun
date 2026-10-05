"""Keyed /api/chat and /api/health, plus the OpenAPI document and Swagger UI."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair import runtime
from pair import server as pair_server
from pair.public_api import API_KEY_ENV, FLASH_MODE, apply_mode
from pair.server import make_server


def _start(httpd: ThreadingHTTPServer) -> None:
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()


class BrainPage(BaseHTTPRequestHandler):
    """Stand-in for pi4's page when a non-brain router relays /api/chat."""

    last_payload = None

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode() or "{}")
        type(self).last_payload = payload
        body = json.dumps(
            {
                "id": "pi-pair",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "from brain"},
                        "finish_reason": "stop",
                    }
                ],
                "pi_model": payload.get("model"),
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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
        self._json(json.dumps({"message": {"content": "hello from peer"}}).encode())

    def _json(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class PublicApi(unittest.TestCase):
    def setUp(self):
        self.servers: list[ThreadingHTTPServer] = []
        self._peers = [dict(peer) for peer in runtime.PEERS]
        self._env = {
            name: os.environ.get(name)
            for name in (
                API_KEY_ENV,
                "PI_PAIR_ROLE",
                "PI_PAIR_DATA",
                "PI_PAIR_CANNED",
                "PI_PAIR_BRAIN_PORT",
            )
        }
        self._tmp = tempfile.TemporaryDirectory()
        self._lookup = pair_server.lookup_web
        import pair.queue as queue

        self._queue = queue
        self._forward = queue.forward_row
        os.environ[API_KEY_ENV] = "test-key"
        os.environ["PI_PAIR_ROLE"] = "brain"
        os.environ["PI_PAIR_DATA"] = self._tmp.name
        os.environ["PI_PAIR_CANNED"] = str(ROOT / "data" / "canned" / "canned_map.json")
        pair_server.lookup_web = lambda prompt: {
            "status": "failed",
            "sources": [],
            "context": "",
        }
        queue.forward_row = lambda row, opener=None, timeout=1.5: True
        OllamaFake.posts = 0
        OllamaFake.last_payload = None
        BrainPage.last_payload = None
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
        self.port = self._listen_pair()

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        pair_server.lookup_web = self._lookup
        self._queue.forward_row = self._forward
        runtime.PEERS = self._peers
        runtime.reset_health()
        for name, previous in self._env.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self._tmp.cleanup()

    def _listen(self, handler) -> int:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.servers.append(httpd)
        _start(httpd)
        return httpd.server_address[1]

    def _listen_pair(self) -> int:
        httpd = make_server("127.0.0.1", 0)
        self.servers.append(httpd)
        _start(httpd)
        return httpd.server_address[1]

    def _open(self, method: str, path: str, payload=None, headers=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers=headers or {},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def _json(self, method: str, path: str, payload=None, headers=None):
        status, response_headers, raw = self._open(method, path, payload, headers)
        body = json.loads(raw.decode() or "{}")
        return status, response_headers, body

    def _auth(self, extra=None, key: str = "test-key", scheme: str = "bearer") -> dict:
        headers = {"content-type": "application/json"}
        if scheme == "bearer":
            headers["Authorization"] = f"Bearer {key}"
        elif scheme == "x-api-key":
            headers["X-API-Key"] = key
        if extra:
            headers.update(extra)
        return headers

    def test_chat_endpoint_defaults_to_flash(self):
        posts_before = OllamaFake.posts
        status, headers, body = self._json(
            "POST",
            "/api/chat",
            {"messages": [{"role": "user", "content": "Hi!"}]},
            self._auth(scheme="x-api-key"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Pi-Mode"), FLASH_MODE)
        self.assertEqual(headers.get("X-Pi-Model"), FLASH_MODE)
        self.assertEqual(headers.get("X-Pi-Chip"), "cache")
        self.assertEqual(body["mode"], FLASH_MODE)
        self.assertEqual(body["model"], FLASH_MODE)
        self.assertEqual(body["checkpoint"], runtime.MODEL)
        self.assertEqual(body["pi_model"], "canned")
        self.assertEqual(body["choices"][0]["message"]["content"], "Hi. What can I help you with?")
        self.assertEqual(OllamaFake.posts, posts_before)

        status, headers, body = self._json(
            "POST",
            "/api/chat",
            {
                "messages": [{"role": "user", "content": "Say hi in five words."}],
                "model": "llama3.2:1b",
            },
            self._auth(),
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["mode"], FLASH_MODE)
        self.assertEqual(body["model"], FLASH_MODE)
        self.assertEqual(body["checkpoint"], "qwen2.5:0.5b")
        self.assertEqual(body["pi_model"], "qwen2.5:0.5b")
        self.assertEqual(body["pi_think"], "medium")
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertEqual(headers.get("X-Pi-Model"), FLASH_MODE)
        self.assertEqual(OllamaFake.last_payload["model"], "qwen2.5:0.5b")
        self.assertNotEqual(OllamaFake.last_payload["model"], "llama3.2:1b")
        self.assertEqual(OllamaFake.last_payload["options"]["num_predict"], 256)
        self.assertEqual(OllamaFake.last_payload["options"]["temperature"], 0.7)

        status, _headers, body = self._json(
            "POST",
            "/api/chat",
            {
                "messages": [{"role": "user", "content": "Say hi in five words."}],
                "mode": "high",
            },
            self._auth(),
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["mode"], "high")
        self.assertEqual(body["model"], FLASH_MODE)
        self.assertEqual(body["pi_think"], "high")
        self.assertEqual(OllamaFake.last_payload["model"], "qwen2.5:0.5b")
        self.assertEqual(OllamaFake.last_payload["options"]["num_predict"], 768)
        self.assertEqual(OllamaFake.last_payload["options"]["temperature"], 0.8)

    def test_chat_requires_the_api_key(self):
        missing, headers, body = self._json(
            "POST",
            "/api/chat",
            {"messages": [{"role": "user", "content": "Hi!"}]},
            {"content-type": "application/json"},
        )
        self.assertEqual(missing, 401)
        self.assertEqual(body["error"], "missing or invalid API key")
        self.assertIn("Bearer", headers.get("WWW-Authenticate") or "")
        self.assertEqual(OllamaFake.posts, 0)

        wrong, _headers, body = self._json(
            "POST",
            "/api/chat",
            {"messages": [{"role": "user", "content": "Hi!"}]},
            self._auth(key="nope"),
        )
        self.assertEqual(wrong, 401)
        self.assertEqual(body["error"], "missing or invalid API key")
        self.assertEqual(OllamaFake.posts, 0)

        os.environ.pop(API_KEY_ENV)
        closed, _headers, body = self._json(
            "POST",
            "/api/chat",
            {"messages": [{"role": "user", "content": "Hi!"}]},
            self._auth(),
        )
        self.assertEqual(closed, 503)
        self.assertIn(API_KEY_ENV, body["error"])
        self.assertEqual(OllamaFake.posts, 0)

        bad, _headers, body = self._json(
            "POST",
            "/api/chat",
            {"messages": [{"role": "user", "content": "Hi!"}], "mode": "turbo"},
            self._auth(),
        )
        # The key was popped above; restore it before the mode check.
        self.assertEqual(bad, 503)

        os.environ[API_KEY_ENV] = "test-key"
        bad, _headers, body = self._json(
            "POST",
            "/api/chat",
            {"messages": [{"role": "user", "content": "Hi!"}], "mode": "turbo"},
            self._auth(),
        )
        self.assertEqual(bad, 400)
        self.assertIn("turbo", body["error"])
        self.assertEqual(OllamaFake.posts, 0)

    def test_non_brain_relays_the_flash_checkpoint(self):
        brain_port = self._listen(BrainPage)
        os.environ["PI_PAIR_ROLE"] = "dataset"
        os.environ["PI_PAIR_BRAIN_PORT"] = str(brain_port)
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": 11434,
                    "kind": "ollama",
                    "note": "",
                    "generative": True,
                    "role": "brain",
                }
            ]
        )
        status, headers, body = self._json(
            "POST",
            "/api/chat",
            {"messages": [{"role": "user", "content": "Say hi in five words."}]},
            self._auth(),
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(BrainPage.last_payload["model"], "qwen2.5:0.5b")
        self.assertEqual(BrainPage.last_payload["think"], "medium")
        self.assertNotIn("mode", BrainPage.last_payload)
        self.assertEqual(body["mode"], FLASH_MODE)
        self.assertEqual(body["model"], FLASH_MODE)
        self.assertEqual(body["choices"][0]["message"]["content"], "from brain")
        self.assertEqual(headers.get("X-Pi-Model"), FLASH_MODE)
        self.assertEqual(OllamaFake.posts, 0)

    def test_lan_chat_stays_open_when_the_key_is_set(self):
        status, headers, body = self._json(
            "POST",
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "Hi!"}], "stream": False},
            {"content-type": "application/json"},
        )
        self.assertEqual(status, 200, body)
        self.assertNotIn("mode", body)
        self.assertIsNone(headers.get("X-Pi-Model"))
        self.assertEqual(body["choices"][0]["message"]["content"], "Hi. What can I help you with?")
        self.assertEqual(body["pi_model"], "canned")

        health_status, _headers, health = self._json("GET", "/health")
        self.assertEqual(health_status, 200)
        self.assertTrue(health["ok"])
        self.assertNotIn("public_model", health)

        denied, _headers, denied_body = self._json("GET", "/api/health")
        self.assertEqual(denied, 401)
        self.assertEqual(denied_body["error"], "missing or invalid API key")

        ok, _headers, keyed = self._json("GET", "/api/health", headers=self._auth())
        self.assertEqual(ok, 200, keyed)
        self.assertTrue(keyed["ok"])
        self.assertEqual(keyed["public_model"], FLASH_MODE)
        self.assertEqual(keyed["default_mode"], FLASH_MODE)
        self.assertEqual(keyed["checkpoint"], runtime.MODEL)
        self.assertEqual(keyed["peers_up"], 1)

    def test_openapi_and_swagger_document_auth(self):
        status, headers, raw = self._open("GET", "/openapi.json")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers.get("content-type", ""))
        spec = json.loads(raw.decode())
        self.assertTrue(spec["openapi"].startswith("3."))
        self.assertIn("/api/chat", spec["paths"])
        self.assertIn("/api/health", spec["paths"])
        self.assertIn("/docs", spec["paths"])
        security = spec["components"]["securitySchemes"]
        self.assertEqual(security["bearerAuth"]["scheme"], "bearer")
        self.assertEqual(security["apiKeyAuth"]["name"], "X-API-Key")
        self.assertIn(API_KEY_ENV, security["bearerAuth"]["description"])
        chat = spec["paths"]["/api/chat"]["post"]
        self.assertEqual(chat["security"], [{"bearerAuth": []}, {"apiKeyAuth": []}])
        mode = spec["components"]["schemas"]["ChatRequest"]["properties"]["mode"]
        self.assertEqual(mode["default"], FLASH_MODE)
        self.assertIn("flash", mode["enum"])
        self.assertIn(API_KEY_ENV, spec["info"]["description"])
        self.assertIn("qwen2.5:0.5b", spec["info"]["description"])
        self.assertIn("If `mode` is omitted, the model is Flash.", spec["info"]["description"])

        alias, _headers, alias_raw = self._open("GET", "/swagger.json")
        self.assertEqual(alias, 200)
        self.assertEqual(json.loads(alias_raw.decode())["info"]["title"], "Pi GPT API")

        for path in ("/docs", "/docs/", "/swagger", "/swagger/"):
            page_status, page_headers, page = self._open("GET", path)
            self.assertEqual(page_status, 200, path)
            self.assertIn("text/html", page_headers.get("content-type", ""))
            text = page.decode()
            self.assertIn("Pi GPT Swagger UI", text)
            self.assertIn("/openapi.json", text)
            self.assertIn("swagger-ui-bundle", text)
            self.assertIn(API_KEY_ENV, text)
            self.assertIn("X-API-Key", text)
            self.assertIn("POST /api/chat", text)

        readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
        for phrase in (
            "PI_GPT_API_KEY",
            "/openapi.json",
            "/api/chat",
            "Swagger UI",
            "If `mode` is omitted, the model is Flash.",
            "Authorization: Bearer",
            "X-API-Key",
        ):
            self.assertIn(phrase, readme, phrase)

    def test_omitted_mode_resolves_to_flash(self):
        payload = {"messages": [{"role": "user", "content": "Hi"}], "model": "other"}
        self.assertEqual(apply_mode(payload), FLASH_MODE)
        self.assertEqual(payload["model"], runtime.MODEL)
        self.assertEqual(payload["think"], "medium")
        self.assertNotIn("mode", payload)


if __name__ == "__main__":
    unittest.main()
