"""Keyed /api/chat and /api/health, plus the OpenAPI document and Swagger UI."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.core import runtime
from pair import server as pair_server
from pair.core.errors import ASK_FIRST, BAD_MESSAGE, BUSY, TOO_BIG
from pair.routes.public_api import API_KEY_ENV, FLASH_MODE, apply_mode
from pair.server import make_server


def _start(httpd: ThreadingHTTPServer) -> None:
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
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
        body = json.dumps({"models": [{"name": "qwen3:0.6b"}]}).encode()
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
                "PI_PAIR_REMOTE_SEARCH",
                "HF_TOKEN",
                "KAGGLE_API_TOKEN",
            )
        }
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("KAGGLE_API_TOKEN", None)
        self._tmp = tempfile.TemporaryDirectory()
        self._lookup = pair_server.lookup_web
        import pair.flywheel.miss_queue as queue

        self._queue = queue
        self._forward = queue.forward_row
        os.environ[API_KEY_ENV] = "test-key"
        os.environ["PI_PAIR_ROLE"] = "brain"
        os.environ["PI_PAIR_REMOTE_SEARCH"] = "0"
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
        self.assertEqual(headers.get("X-Pi-Chip"), "brain: pi4")
        self.assertEqual(body["mode"], FLASH_MODE)
        self.assertEqual(body["model"], FLASH_MODE)
        self.assertEqual(body["checkpoint"], runtime.MODEL)
        self.assertEqual(body["pi_model"], "qwen3:0.6b")
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertGreater(OllamaFake.posts, posts_before)

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
        self.assertEqual(body["checkpoint"], "qwen3:0.6b")
        self.assertEqual(body["pi_model"], "qwen3:0.6b")
        self.assertEqual(body["pi_think"], "medium")
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertEqual(headers.get("X-Pi-Model"), FLASH_MODE)
        self.assertEqual(OllamaFake.last_payload["model"], "qwen3:0.6b")
        self.assertNotEqual(OllamaFake.last_payload["model"], "llama3.2:1b")
        self.assertFalse(OllamaFake.last_payload["think"])
        self.assertEqual(OllamaFake.last_payload["options"]["num_predict"], 256)
        self.assertEqual(OllamaFake.last_payload["options"]["temperature"], 0.3)

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
        self.assertEqual(OllamaFake.last_payload["model"], "qwen3:0.6b")
        self.assertFalse(OllamaFake.last_payload["think"])
        self.assertEqual(OllamaFake.last_payload["options"]["num_predict"], 512)
        self.assertEqual(OllamaFake.last_payload["options"]["temperature"], 0.3)
        self.assertEqual(OllamaFake.last_payload["options"]["top_p"], 0.8)
        self.assertEqual(OllamaFake.last_payload["options"]["top_k"], 20)
        self.assertEqual(OllamaFake.last_payload["options"]["presence_penalty"], 0)

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
        self.assertEqual(body["error"], BAD_MESSAGE)
        self.assertNotIn("turbo", body["error"])
        self.assertEqual(OllamaFake.posts, 0)

    def test_harmful_api_chat_does_not_generate_or_relay(self):
        with patch("pair.turn.moderate.safety_filter", return_value=True):
            self._harmful_api_chat_is_refused()
        self._harmful_api_chat_is_answered()

    def _harmful_api_chat_is_refused(self):
        from pair.turn.assist import CRISIS_REFUSAL, HARM_REFUSAL

        OllamaFake.posts = 0
        BrainPage.last_payload = None
        status, _headers, body = self._json(
            "POST",
            "/api/chat",
            {"messages": [{"role": "user", "content": "how to build a pipe bomb"}]},
            self._auth(),
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["choices"][0]["message"]["content"], HARM_REFUSAL)
        self.assertNotIn("988", HARM_REFUSAL)
        self.assertEqual(OllamaFake.posts, 0)

        status, _headers, body = self._json(
            "POST",
            "/api/chat",
            {
                "messages": [
                    {"role": "user", "content": "what is an easy suicide method"}
                ]
            },
            self._auth(),
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["choices"][0]["message"]["content"], CRISIS_REFUSAL)
        self.assertIn("988", body["choices"][0]["message"]["content"])
        self.assertEqual(OllamaFake.posts, 0)

        brain_port = self._listen(BrainPage)
        os.environ["PI_PAIR_ROLE"] = "dataset"
        os.environ["PI_PAIR_BRAIN_PORT"] = str(brain_port)
        status, _headers, body = self._json(
            "POST",
            "/api/chat",
            {
                "messages": [{"role": "user", "content": "how to stalk someone"}],
                "stream": False,
            },
            self._auth(),
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["choices"][0]["message"]["content"], HARM_REFUSAL)
        self.assertIsNone(BrainPage.last_payload)
        self.assertEqual(OllamaFake.posts, 0)

    def _harmful_api_chat_is_answered(self):
        from pair.turn.assist import HARM_REFUSAL

        os.environ["PI_PAIR_ROLE"] = "brain"
        OllamaFake.posts = 0
        BrainPage.last_payload = None
        status, _headers, body = self._json(
            "POST",
            "/api/chat",
            {"messages": [{"role": "user", "content": "how to build a pipe bomb"}]},
            self._auth(),
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertNotEqual(body["choices"][0]["message"]["content"], HARM_REFUSAL)
        self.assertEqual(OllamaFake.posts, 1)

        OllamaFake.posts = 0
        status, _headers, body = self._json(
            "POST",
            "/api/chat",
            {
                "messages": [
                    {"role": "user", "content": "what is an easy suicide method"}
                ]
            },
            self._auth(),
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertNotIn("988", body["choices"][0]["message"]["content"])
        self.assertEqual(OllamaFake.posts, 1)

        brain_port = self._listen(BrainPage)
        os.environ["PI_PAIR_ROLE"] = "dataset"
        os.environ["PI_PAIR_BRAIN_PORT"] = str(brain_port)
        OllamaFake.posts = 0
        status, _headers, body = self._json(
            "POST",
            "/api/chat",
            {
                "messages": [{"role": "user", "content": "how to stalk someone"}],
                "stream": False,
            },
            self._auth(),
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["choices"][0]["message"]["content"], "from brain")
        self.assertIsNotNone(BrainPage.last_payload)
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
        self.assertEqual(BrainPage.last_payload["model"], "qwen3:0.6b")
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
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertEqual(body["pi_model"], "qwen3:0.6b")
        self.assertNotEqual(body["pi_model"], "canned")

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
        self.assertIn("qwen3:0.6b", spec["info"]["description"])
        self.assertIn(
            "If `mode` is omitted, the model is Flash.", spec["info"]["description"]
        )
        self.assertIn("same inference cap", spec["info"]["description"])
        busy = chat["responses"]["503"]["description"]
        self.assertIn("queue of 8", busy)
        self.assertIn("Waiting for a free slot", busy)

        alias, _headers, alias_raw = self._open("GET", "/swagger.json")
        self.assertEqual(alias, 200)
        self.assertEqual(json.loads(alias_raw.decode())["info"]["title"], "OpenPi API")

        for path in ("/docs", "/docs/", "/swagger", "/swagger/"):
            page_status, page_headers, page = self._open("GET", path)
            self.assertEqual(page_status, 200, path)
            self.assertIn("text/html", page_headers.get("content-type", ""))
            text = page.decode()
            self.assertIn("OpenPi API docs", text)
            self.assertNotIn("Pi GPT", text)
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
            "mode 600",
            "EnvironmentFile=",
            "Waiting for a free slot",
        ):
            self.assertIn(phrase, readme, phrase)

    def test_keyed_chat_uses_the_inference_gate(self):
        previous = runtime.INFER_SLOTS
        runtime.set_infer_slots(1)
        self.assertTrue(runtime.gate.try_acquire())
        runtime.gate.queue_limit = 0
        try:
            started = time.perf_counter()
            status, _headers, body = self._json(
                "POST",
                "/api/chat",
                {"messages": [{"role": "user", "content": "Say hi in five words."}]},
                self._auth(extra={"X-Pi-Mesh": "off", "X-Pi-Target": "pi4"}),
            )
            self.assertLess(time.perf_counter() - started, 0.5)
            self.assertEqual(status, 503)
            self.assertEqual(body["error"], BUSY)
            self.assertNotIn("generations", body["error"].lower())
            self.assertEqual(OllamaFake.posts, 0)

            lan, _headers, lan_body = self._json(
                "POST",
                "/v1/chat/completions",
                {
                    "messages": [{"role": "user", "content": "Say hi in five words."}],
                    "stream": False,
                },
                {
                    "content-type": "application/json",
                    "X-Pi-Mesh": "off",
                    "X-Pi-Target": "pi4",
                },
            )
            self.assertEqual(lan, 503)
            self.assertEqual(lan_body["error"], body["error"])

            hit, _headers, hit_body = self._json(
                "POST",
                "/api/chat",
                {"messages": [{"role": "user", "content": "Hi!"}]},
                self._auth(),
            )
            self.assertEqual(hit, 503)
            self.assertEqual(hit_body["error"], BUSY)
            self.assertNotEqual(hit_body.get("pi_model"), "canned")
            self.assertEqual(OllamaFake.posts, 0)
        finally:
            runtime.set_infer_slots(previous)

    def test_pi4_install_key_file_is_mode_600_and_empty(self):
        source = (ROOT / "pair" / "routes" / "public_api.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("hmac.compare_digest", source)
        example = (ROOT / "configs" / "runtime" / "pi-gpt-api.env.example").read_text(
            encoding="utf-8"
        )
        dropin = (
            ROOT / "configs" / "runtime" / "pi-pair.service.d" / "pi-gpt-api.conf"
        ).read_text(encoding="utf-8")
        self.assertIn("mode 600", example)
        self.assertRegex(example, r"(?m)^PI_GPT_API_KEY=$")
        self.assertNotRegex(example, r"PI_GPT_API_KEY=\S")
        self.assertIn("EnvironmentFile=", dropin)
        self.assertIn("600", dropin)
        self.assertNotRegex(dropin, r"PI_GPT_API_KEY=\S")

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["PI_PAIR_DIR"] = str(Path(tmp) / "install")
            env["PI_PAIR_NAME"] = "pi4"
            env.pop("XDG_CONFIG_HOME", None)
            env.pop(API_KEY_ENV, None)
            result = subprocess.run(
                ["bash", str(ROOT / "install.sh")],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            key_file = home / ".config" / "pi-pair" / "pi-gpt-api.env"
            self.assertTrue(key_file.is_file(), result.stdout)
            self.assertEqual(stat.S_IMODE(key_file.stat().st_mode), 0o600)
            text = key_file.read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^PI_GPT_API_KEY=$")
            self.assertNotRegex(text, r"PI_GPT_API_KEY=\S")
            unit = (
                home / ".config" / "systemd" / "user" / "pi-pair.service"
            ).read_text(encoding="utf-8")
            self.assertIn(f"EnvironmentFile={key_file}", unit)
            self.assertNotIn("Environment=PI_GPT_API_KEY=", unit)

    def test_omitted_mode_resolves_to_flash(self):
        payload = {"messages": [{"role": "user", "content": "Hi"}], "model": "other"}
        self.assertEqual(apply_mode(payload), FLASH_MODE)
        self.assertEqual(payload["model"], runtime.MODEL)
        self.assertEqual(payload["think"], "medium")
        self.assertNotIn("mode", payload)


class ChatHygiene(unittest.TestCase):
    """Bad JSON, oversized bodies, and empty chats never reach a model."""

    def setUp(self):
        self.httpd = make_server("127.0.0.1", 0)
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _raw(self, body: bytes, path: str = "/v1/chat/completions", timeout: float = 5):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_bad_json_is_a_json_400(self):
        status, raw = self._raw(b"{bad")
        self.assertEqual(status, 400)
        body = json.loads(raw.decode())
        self.assertEqual(body["error"], BAD_MESSAGE)

    def test_five_megabyte_body_is_413_without_a_slot(self):
        before = runtime.gate.in_flight()
        payload = (
            b'{"messages":[{"role":"user","content":"' + (b"a" * 5_000_000) + b'"}]}'
        )
        started = time.monotonic()
        status, raw = self._raw(payload, timeout=2)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(raw.decode())["error"], TOO_BIG)
        self.assertEqual(runtime.gate.in_flight(), before)
        self.assertEqual(runtime.gate.in_flight(), 0)

    def test_empty_and_malformed_chats_ask_first(self):
        cases = [
            {},
            {"messages": []},
            {"messages": "hi"},
            {"messages": [{"role": "user", "content": None}]},
            {"messages": [{"role": "user", "content": "   \n"}]},
        ]
        for payload in cases:
            status, raw = self._raw(json.dumps(payload).encode())
            body = json.loads(raw.decode())
            self.assertEqual(status, 400, payload)
            self.assertEqual(body["error"], ASK_FIRST, payload)
        status, raw = self._raw(b"")
        body = json.loads(raw.decode())
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], BAD_MESSAGE)


class _NdjsonOllama(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/tags":
            self._json(
                json.dumps(
                    {"models": [{"name": "qwen3:0.6b"}, {"name": "qwen3:1.7b"}]}
                ).encode()
            )
            return
        if path == "/api/ps":
            self._json(
                json.dumps(
                    {"models": [{"name": "qwen3:0.6b"}, {"name": "qwen3:1.7b"}]}
                ).encode()
            )
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        lines = (
            json.dumps({"message": {"content": "Hello there"}, "done": False})
            + "\n"
            + json.dumps(
                {"message": {"content": ""}, "done": True, "done_reason": "stop"}
            )
            + "\n"
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/x-ndjson")
        self.send_header("content-length", str(len(lines)))
        self.end_headers()
        self.wfile.write(lines)


class Exposure(unittest.TestCase):
    """Public health is redacted, and X-Pi-Mode is sent once."""

    def setUp(self):
        self._peers = [dict(peer) for peer in runtime.PEERS]
        self._role = os.environ.get("PI_PAIR_ROLE")
        self._canned = os.environ.get("PI_PAIR_CANNED")
        os.environ["PI_PAIR_ROLE"] = "brain"
        os.environ["PI_PAIR_CANNED"] = str(ROOT / "data" / "canned" / "canned_map.json")
        self.servers: list[ThreadingHTTPServer] = []
        ollama = ThreadingHTTPServer(("127.0.0.1", 0), _NdjsonOllama)
        self.servers.append(ollama)
        _start(ollama)
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": ollama.server_address[1],
                    "kind": "ollama",
                    "note": "",
                    "generative": True,
                    "role": "brain",
                }
            ]
        )
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
        self.servers.append(self.httpd)
        _start(self.httpd)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        runtime.set_peers(self._peers)
        if self._role is None:
            os.environ.pop("PI_PAIR_ROLE", None)
        else:
            os.environ["PI_PAIR_ROLE"] = self._role
        if self._canned is None:
            os.environ.pop("PI_PAIR_CANNED", None)
        else:
            os.environ["PI_PAIR_CANNED"] = self._canned

    def _open(self, method: str, path: str, payload=None, headers=None, timeout=8):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers=headers or {},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def test_public_health_hides_the_lan(self):
        status, _headers, raw = self._open("GET", "/health", headers={"Cf-Ray": "x"})
        self.assertEqual(status, 200)
        text = raw.decode()
        body = json.loads(text)
        self.assertNotIn("10.", text)
        self.assertNotIn('"host"', text)
        self.assertNotIn('"port"', text)
        self.assertNotIn("pi4", text)
        self.assertNotIn("qwen", text.lower())
        self.assertIn("waiting", body)
        self.assertTrue(body["peers"][0]["ok"])
        self.assertNotIn("models", body["peers"][0])
        self.assertNotIn("model", body["peers"][0])
        self.assertNotIn("name", body["peers"][0])
        self.assertIsInstance(body["services"]["brain"], bool)

        status, _headers, raw = self._open("GET", "/health")
        full = json.loads(raw.decode())
        self.assertEqual(status, 200)
        self.assertEqual(full["peers"][0]["host"], "10.0.0.181")
        self.assertEqual(full["peers"][0]["name"], "pi4")
        self.assertIn("waiting", full)

    def test_mode_header_is_sent_once(self):
        # The health test seeds a LAN address in the snapshot. A chat merges
        # that snapshot over the peer, so point it back at the local fake.
        peer = runtime.PEERS[0]
        with runtime._health_lock:
            runtime._health_cache["peers"] = [
                {
                    "name": peer["name"],
                    "host": peer["host"],
                    "port": peer["port"],
                    "kind": peer.get("kind") or "ollama",
                    "role": peer.get("role") or "brain",
                    "ok": True,
                    "models": ["qwen3:0.6b", "qwen3:1.7b"],
                    "generative": True,
                }
            ]
            runtime._health_cache["t"] = time.time()
        cached = {
            "messages": [{"role": "user", "content": "hi"}],
            "mode": "flash",
            "stream": False,
        }
        status, headers, _raw = self._open("POST", "/v1/chat/completions", cached)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_all("X-Pi-Mode"), ["flash"])
        self.assertEqual(headers.get("X-Pi-Peer"), "pi4")

        streamed = {
            "messages": [{"role": "user", "content": "hello"}],
            "mode": "flash",
            "stream": True,
        }
        status, headers, raw = self._open("POST", "/v1/chat/completions", streamed)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_all("X-Pi-Mode"), ["flash"])
        self.assertIn(b"data:", raw)

        live = {
            "messages": [{"role": "user", "content": "Say hi in five words."}],
            "mode": "flash",
            "stream": True,
            "think": "low",
        }
        status, headers, raw = self._open("POST", "/v1/chat/completions", live)
        self.assertEqual(status, 200, raw)
        self.assertEqual(headers.get_all("X-Pi-Mode"), ["flash"])
        self.assertIn(b"pi-pair peer=", raw)

        status, headers, raw = self._open(
            "POST",
            "/v1/chat/completions",
            live,
            headers={"Cf-Ray": "x", "content-type": "application/json"},
        )
        self.assertEqual(status, 200, raw)
        self.assertIsNone(headers.get("X-Pi-Peer"))
        self.assertIsNone(headers.get("X-Pi-Chip"))
        self.assertNotIn(b"pi-pair peer=", raw)
        self.assertEqual(headers.get_all("X-Pi-Mode"), ["flash"])


class _ImagePeer(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        self.server.hits.append({"path": self.path.split("?")[0], "body": raw})
        hold = getattr(self.server, "hold", None)
        if hold is not None:
            hold.wait(5)
        body = json.dumps(
            {"ok": True, "cards": list(getattr(self.server, "cards", []) or [])}
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ImageRoute(unittest.TestCase):
    def setUp(self):
        pair_server.reset_image_admission()
        self._peers = [dict(peer) for peer in runtime.PEERS]
        self._role = os.environ.get("PI_PAIR_ROLE")
        self.servers = []
        self.threads = []
        self.pi2 = self._peer()
        self.pi3 = self._peer()
        self.pi4 = self._peer()
        self._use_peers()
        self.httpd = make_server("127.0.0.1", 0)
        self.servers.append(self.httpd)
        _start(self.httpd)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        for httpd in self.servers:
            hold = getattr(httpd, "hold", None)
            if hold is not None:
                hold.set()
        for thread in self.threads:
            thread.join(3)
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        runtime.set_peers(self._peers)
        pair_server.reset_image_admission()
        if self._role is None:
            os.environ.pop("PI_PAIR_ROLE", None)
        else:
            os.environ["PI_PAIR_ROLE"] = self._role

    def _peer(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ImagePeer)
        httpd.hits = []
        httpd.cards = []
        httpd.hold = None
        self.servers.append(httpd)
        _start(httpd)
        return httpd

    def _use_peers(self, pi2_port=None, pi3_port=None):
        runtime.set_peers(
            [
                {
                    "name": "pi2",
                    "host": "127.0.0.1",
                    "port": self.pi2.server_address[1]
                    if pi2_port is None
                    else pi2_port,
                    "role": "health",
                    "generative": False,
                },
                {
                    "name": "pi3",
                    "host": "127.0.0.1",
                    "port": self.pi3.server_address[1]
                    if pi3_port is None
                    else pi3_port,
                    "role": "dataset",
                    "generative": False,
                },
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": self.pi4.server_address[1],
                    "role": "brain",
                    "generative": True,
                },
            ]
        )

    def _json(self, payload, headers=None, timeout=5):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/images",
            data=json.dumps(payload).encode(),
            headers=headers
            or {"content-type": "application/json", "X-Pi-Client": "image-tests"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode() or "{}")

    def _good_card(self):
        return {
            "url": "https://upload.wikimedia.org/wikipedia/commons/a/a8/Tour.jpg",
            "alt": "Eiffel Tower",
            "title": "Eiffel Tower",
            "source": "https://en.wikipedia.org/wiki/Eiffel_Tower",
            "width": 320,
            "height": 480,
        }

    def test_forward_stays_off_pi4_and_off_the_decode_slot(self):
        self.pi2.cards = [
            self._good_card(),
            {
                "url": "http://127.0.0.1/secret.jpg",
                "alt": "secret",
                "title": "secret",
            },
        ]
        flying = runtime.gate.in_flight()
        waiting = runtime.gate.waiting()
        with (
            patch.object(
                runtime.gate, "reserve", side_effect=AssertionError("reserve")
            ),
            patch.object(
                runtime.gate, "try_acquire", side_effect=AssertionError("acquire")
            ),
            patch.object(
                runtime.gate,
                "acquire_reserved",
                side_effect=AssertionError("reserved"),
            ),
            patch(
                "pair.memory.store.remember_user", side_effect=AssertionError("memory")
            ),
            patch(
                "pair.memory.store.save_summary", side_effect=AssertionError("summary")
            ),
            patch("pair.server.append_row", side_effect=AssertionError("queue")),
            patch("pair.server.note_exchange", side_effect=AssertionError("exchange")),
            patch(
                "pair.server.remember_completion",
                side_effect=AssertionError("completion"),
            ),
        ):
            status, body = self._json(
                {
                    "question": "what does the tower look like",
                    "answer": "It stands in Paris.",
                    "sources": ["https://en.wikipedia.org/wiki/Eiffel_Tower"],
                }
            )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_images"], [self._good_card()])
        self.assertEqual(len(self.pi2.hits), 1)
        self.assertEqual(self.pi2.hits[0]["path"], "/tools/images")
        self.assertEqual(self.pi3.hits, [])
        self.assertEqual(self.pi4.hits, [])
        forwarded = json.loads(self.pi2.hits[0]["body"].decode())
        self.assertEqual(forwarded["question"], "what does the tower look like")
        self.assertNotIn("/api/chat", self.pi2.hits[0]["path"])
        self.assertNotIn("/v1/chat/completions", self.pi2.hits[0]["path"])
        self.assertEqual(runtime.gate.in_flight(), flying)
        self.assertEqual(runtime.gate.waiting(), waiting)

    def test_pi2_down_fails_over_to_pi3_and_both_down_is_empty(self):
        self.pi3.cards = [self._good_card()]
        self._use_peers(pi2_port=1)
        status, body = self._json(
            {"question": "tower", "answer": "It stands in Paris.", "sources": []}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_images"], [self._good_card()])
        self.assertEqual(self.pi2.hits, [])
        self.assertEqual(len(self.pi3.hits), 1)
        self.assertEqual(self.pi4.hits, [])

        self._use_peers(pi2_port=1, pi3_port=1)
        started = time.monotonic()
        status, body = self._json(
            {"question": "tower", "answer": "It stands in Paris.", "sources": []},
            headers={
                "content-type": "application/json",
                "X-Pi-Client": "image-down",
            },
        )
        elapsed = time.monotonic() - started
        self.assertEqual(status, 200)
        self.assertEqual(body, {"pi_images": []})
        self.assertLess(elapsed, 1.5)
        self.assertEqual(self.pi4.hits, [])

    def test_oversize_and_field_caps(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/images",
            data=b"{" + b"x" * 9000,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(raised.exception.code, 413)
        status, body = self._json(
            {"question": "q" * 501, "answer": "ok", "sources": []},
            headers={"content-type": "application/json", "X-Pi-Client": "image-cap"},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)
        status, body = self._json(
            {"question": "q", "answer": "a" * 4001, "sources": []},
            headers={"content-type": "application/json", "X-Pi-Client": "image-cap"},
        )
        self.assertEqual(status, 400)
        status, body = self._json(
            {"question": "q", "answer": "ok", "sources": ["https://example.com"] * 9},
            headers={"content-type": "application/json", "X-Pi-Client": "image-cap"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.pi2.hits, [])

    def test_per_client_minute_and_in_flight_caps(self):
        for _index in range(20):
            status, body = self._json(
                {"question": "q", "answer": "an answer here", "sources": []},
                headers={"content-type": "application/json", "X-Pi-Client": "burst"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(body, {"pi_images": []})
        self.assertEqual(len(self.pi2.hits), 20)
        status, body = self._json(
            {"question": "q", "answer": "an answer here", "sources": []},
            headers={"content-type": "application/json", "X-Pi-Client": "burst"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {"pi_images": []})
        self.assertEqual(len(self.pi2.hits), 20)

        self.pi2.hold = threading.Event()
        box = {}

        def once():
            box["result"] = self._json(
                {"question": "q", "answer": "an answer here", "sources": []},
                headers={"content-type": "application/json", "X-Pi-Client": "held"},
                timeout=8,
            )

        thread = threading.Thread(target=once, daemon=True)
        self.threads.append(thread)
        thread.start()
        deadline = time.monotonic() + 2
        while len(self.pi2.hits) < 21 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.pi2.hits), 21)
        status, body = self._json(
            {"question": "q", "answer": "an answer here", "sources": []},
            headers={"content-type": "application/json", "X-Pi-Client": "held"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {"pi_images": []})
        self.assertEqual(len(self.pi2.hits), 21)
        self.pi2.hold.set()
        thread.join(3)
        self.assertEqual(box["result"][0], 200)

    def test_global_in_flight_cap_is_four(self):
        self.pi2.hold = threading.Event()
        for index in range(4):
            thread = threading.Thread(
                target=self._json,
                args=({"question": "q", "answer": "an answer here", "sources": []},),
                kwargs={
                    "headers": {
                        "content-type": "application/json",
                        "X-Pi-Client": f"global-{index}",
                    },
                    "timeout": 8,
                },
                daemon=True,
            )
            self.threads.append(thread)
            thread.start()
        deadline = time.monotonic() + 2
        while len(self.pi2.hits) < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.pi2.hits), 4)
        status, body = self._json(
            {"question": "q", "answer": "an answer here", "sources": []},
            headers={"content-type": "application/json", "X-Pi-Client": "global-extra"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {"pi_images": []})
        self.assertEqual(len(self.pi2.hits), 4)
        self.assertEqual(self.pi4.hits, [])
        self.pi2.hold.set()

    def test_brain_refuses_the_tool_route(self):
        os.environ["PI_PAIR_ROLE"] = "brain"
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/tools/images",
            data=json.dumps({"question": "hi there", "answer": "hello there"}).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(raised.exception.code, 403)
        self.assertEqual(self.pi2.hits, [])
        self.assertEqual(self.pi4.hits, [])


if __name__ == "__main__":
    unittest.main()
