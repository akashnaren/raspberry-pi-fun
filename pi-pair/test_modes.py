"""Flash is the default. Pro is opt-in. A mock Ollama records the switch."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pair import runtime
from pair import server as pair_server
from pair.modes import pull_needed, resolve_mode, tag_ready
from pair.turn import EFFORT_HINT
from pair.resident import cap_fits_residents, eviction_targets, protected_tags
from pair.server import make_server
from test_pair import ROOT


class ModeOllama(BaseHTTPRequestHandler):
    tags = ["qwen3:0.6b", "qwen3:1.7b"]
    loaded = ["qwen3:0.6b"]
    calls: list = []
    sticky = False
    block_chat = None
    ps_fail = False
    hold_load = False

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        type(self).calls.append(("GET", path, None))
        if path == "/api/tags":
            body = {"models": [{"name": name} for name in type(self).tags]}
            self._json(json.dumps(body).encode())
            return
        if path == "/api/ps":
            if type(self).ps_fail:
                self.send_response(500)
                self.end_headers()
                return
            body = {
                "models": [{"name": name, "model": name} for name in type(self).loaded]
            }
            self._json(json.dumps(body).encode())
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode() or "{}")
        path = self.path.split("?")[0]
        type(self).calls.append(("POST", path, payload))
        if path == "/api/generate":
            name = payload.get("model")
            alive = payload.get("keep_alive")
            if alive == 0 and not type(self).sticky:
                type(self).loaded = [item for item in type(self).loaded if item != name]
            elif name and name not in type(self).loaded and not type(self).hold_load:
                type(self).loaded = [*type(self).loaded, name]
            self._json(b"{}")
            return
        if path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return
        gate = type(self).block_chat
        if gate is not None:
            gate["entered"].set()
            gate["release"].wait(5)
            gate["wrote"] = True
        type(self).loaded = _after_chat(type(self).loaded, payload.get("model"))
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


def _after_chat(loaded, model):
    """MAX_LOADED_MODELS >= 2 keeps Flash and Pro together."""
    kept = list(loaded)
    if model and model not in kept:
        kept.append(model)
    return kept


def _reset_fake() -> None:
    ModeOllama.tags = ["qwen3:0.6b", "qwen3:1.7b"]
    ModeOllama.loaded = ["qwen3:0.6b"]
    ModeOllama.calls = []
    ModeOllama.sticky = False
    ModeOllama.block_chat = None
    ModeOllama.ps_fail = False
    ModeOllama.hold_load = False


def _posts(path: str) -> list[dict]:
    return [
        payload
        for method, seen, payload in ModeOllama.calls
        if method == "POST" and seen == path and payload
    ]


class ModeRules(unittest.TestCase):
    def test_unspecified_is_flash_and_pro_is_explicit(self):
        self.assertEqual(resolve_mode(None, None), ("flash", "qwen3:0.6b"))
        self.assertEqual(resolve_mode("", ""), ("flash", "qwen3:0.6b"))
        self.assertEqual(resolve_mode("   ", None), ("flash", "qwen3:0.6b"))
        self.assertEqual(resolve_mode("turbo", "llama3.2:1b"), ("flash", "qwen3:0.6b"))
        self.assertEqual(resolve_mode("pro", "qwen3:0.6b"), ("pro", "qwen3:1.7b"))
        self.assertEqual(resolve_mode("PRO", None), ("pro", "qwen3:1.7b"))
        self.assertEqual(resolve_mode("flash", "qwen3:1.7b"), ("flash", "qwen3:0.6b"))
        self.assertEqual(resolve_mode(None, "qwen3:1.7b"), ("pro", "qwen3:1.7b"))
        self.assertEqual(resolve_mode(None, "Pro"), ("pro", "qwen3:1.7b"))
        self.assertFalse(tag_ready(["qwen3:0.6b"], "pro", "qwen3:1.7b"))
        self.assertTrue(tag_ready(["qwen3:1.7b"], "pro", "qwen3:1.7b"))
        self.assertTrue(tag_ready([], "flash", "qwen3:0.6b"))
        self.assertFalse(tag_ready([], "pro", "qwen3:1.7b"))
        self.assertIn("ollama pull qwen3:1.7b", pull_needed("qwen3:1.7b"))
        self.assertIn("does not pull", pull_needed("qwen3:1.7b"))
        self.assertEqual(protected_tags(), ("qwen3:0.6b", "qwen3:1.7b"))
        flash = "qwen3:0.6b"
        running = [flash]
        self.assertEqual(eviction_targets(running, "qwen3:1.7b"), [])
        self.assertEqual(eviction_targets([flash, "qwen3:1.7b"], flash), [])

    def test_readme_and_installer_do_not_pull_pro(self):
        readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("qwen3:1.7b", readme)
        self.assertIn("does not pull Pro", readme)
        self.assertIn("ollama pull qwen3:1.7b", readme)
        self.assertIn("X-Pi-Mode", readme)
        self.assertNotIn("Loading Pro", readme)
        self.assertIn("keep_alive", readme)
        self.assertIn("MAX_LOADED_MODELS=2", readme)
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("does not pull it", script)
        self.assertIn("does not run ollama pull", script)
        executed = [
            line.strip()
            for line in script.splitlines()
            if "ollama pull" in line and not line.strip().startswith("echo")
        ]
        self.assertEqual(len(executed), 1)
        self.assertIn("OLLAMA_MODEL_PRIMARY", executed[0])
        self.assertIn('ollama rm "$REMOVED_EMBED"', script)
        self.assertNotIn('ollama pull "$REMOVED_EMBED"', script)
        self.assertTrue(all("1.5b" not in line for line in executed))
        self.assertTrue(cap_fits_residents(script))
        unit = (ROOT / "configs" / "runtime" / "ollama-lan.service").read_text(
            encoding="utf-8"
        )
        self.assertTrue(cap_fits_residents(unit))


class ModeHttp(unittest.TestCase):
    def setUp(self):
        self._peers = [dict(peer) for peer in runtime.PEERS]
        self._slots = runtime.INFER_SLOTS
        self._gate = runtime.gate
        runtime.reset_health()
        self.servers: list[ThreadingHTTPServer] = []
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["PI_PAIR_DATA"] = self._tmp.name
        os.environ["PI_PAIR_ROLE"] = "brain"
        os.environ["PI_PAIR_CANNED"] = str(ROOT / "data" / "canned" / "canned_map.json")
        self.search_calls: list[str] = []
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
        runtime.INFER_SLOTS = self._slots
        runtime.gate = self._gate
        runtime.reset_health()
        os.environ.pop("PI_PAIR_DATA", None)
        os.environ.pop("PI_PAIR_ROLE", None)
        os.environ.pop("PI_PAIR_CANNED", None)
        self._tmp.cleanup()

    def _listen(self, handler):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.servers.append(httpd)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd.server_address[1]

    def _pair(self) -> int:
        httpd = make_server("127.0.0.1", 0)
        self.servers.append(httpd)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd.server_address[1]

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

    def _boot(self) -> int:
        _reset_fake()
        peer_port = self._listen(ModeOllama)
        runtime_peers = [
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
        runtime.set_peers(runtime_peers)
        return self._pair()

    def test_omitted_mode_stays_on_flash_and_does_not_unload_it(self):
        port = self._boot()
        status, headers, body = self._post(
            port,
            {"messages": [{"role": "user", "content": "novel flash line"}]},
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Pi-Mode"), "flash")
        self.assertEqual(body["pi_mode"], "flash")
        self.assertEqual(body["pi_model"], "qwen3:0.6b")
        chats = _posts("/api/chat")
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]["model"], "qwen3:0.6b")
        self.assertEqual(chats[0]["keep_alive"], -1)
        self.assertNotIn("mode", chats[0])
        self.assertEqual(_posts("/api/generate"), [])
        self.assertEqual(ModeOllama.loaded, ["qwen3:0.6b"])

    def test_pro_keeps_flash_and_applies_thinking(self):
        port = self._boot()
        levels = {
            "low": (False, 0.5, 0.8, 96, 0),
            "medium": (False, 0.5, 0.8, 256, 0.5),
            "high": (False, 0.5, 0.8, 512, 0.5),
        }
        for level, (think, temperature, top_p, num_predict, penalty) in levels.items():
            _reset_fake()
            ModeOllama.loaded = ["qwen3:0.6b", "qwen3:1.7b"]
            status, headers, body = self._post(
                port,
                {
                    "mode": "pro",
                    "model": "qwen3:0.6b",
                    "messages": [
                        {
                            "role": "user",
                            "content": "Explain why this needs Pro " + level,
                        }
                    ],
                    "think": level,
                    "temperature": 0.1,
                    "max_tokens": 8,
                },
                {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
            )
            self.assertEqual(status, 200, level)
            self.assertEqual(headers.get("X-Pi-Mode"), "pro", level)
            self.assertEqual(body["pi_mode"], "pro", level)
            self.assertEqual(body["pi_think"], level, level)
            self.assertNotIn("loading", body["pi_stages"], level)
            self.assertIn("thinking", body["pi_stages"], level)
            self.assertNotIn("Loading Pro", json.dumps(body), level)
            chats = _posts("/api/chat")
            self.assertEqual(_posts("/api/generate"), [], level)
            self.assertEqual(len(chats), 1, level)
            self.assertEqual(chats[0]["model"], "qwen3:1.7b", level)
            self.assertEqual(chats[0]["keep_alive"], -1, level)
            self.assertEqual(chats[0]["think"], think, level)
            self.assertEqual(chats[0]["options"]["temperature"], temperature, level)
            self.assertEqual(chats[0]["options"]["top_p"], top_p, level)
            self.assertEqual(chats[0]["options"]["top_k"], 20, level)
            self.assertEqual(chats[0]["options"]["presence_penalty"], penalty, level)
            self.assertEqual(chats[0]["options"]["num_predict"], num_predict, level)
            for name in ("qwen3:0.6b", "qwen3:1.7b"):
                self.assertIn(name, ModeOllama.loaded, level)

    def test_every_mode_and_level_sends_think_false(self):
        port = self._boot()
        budgets = {"low": 96, "medium": 256, "high": 512}
        models = {"flash": "qwen3:0.6b", "pro": "qwen3:1.7b", "auto": "qwen3:0.6b"}
        for mode, model in models.items():
            for level, predict in budgets.items():
                _reset_fake()
                ModeOllama.loaded = ["qwen3:0.6b", "qwen3:1.7b"]
                label = f"{mode}/{level}"
                status, _headers, body = self._post(
                    port,
                    {
                        "mode": mode,
                        "messages": [
                            {
                                "role": "user",
                                "content": "Explain why the level is " + label,
                            }
                        ],
                        "think": level,
                        "temperature": 0.2,
                        "max_tokens": 8,
                    },
                    {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
                )
                self.assertEqual(status, 200, label)
                self.assertEqual(body["pi_think"], level, label)
                chats = _posts("/api/chat")
                self.assertEqual(len(chats), 1, label)
                self.assertFalse(chats[0]["think"], label)
                self.assertEqual(chats[0]["model"], model, label)
                options = chats[0]["options"]
                self.assertEqual(
                    options["temperature"], 0.5 if mode == "pro" else 0.3, label
                )
                self.assertEqual(options["top_p"], 0.8, label)
                self.assertEqual(options["top_k"], 20, label)
                penalty = 0.5 if mode == "pro" and level != "low" else 0
                self.assertEqual(options["presence_penalty"], penalty, label)
                self.assertEqual(options["num_predict"], predict, label)
                self.assertIn(
                    EFFORT_HINT[level], json.dumps(chats[0]["messages"]), label
                )

    def test_pro_does_not_evict_flash_and_flash_does_not_name_pro(self):
        port = self._boot()
        ModeOllama.loaded = ["qwen3:0.6b", "qwen3:1.7b"]
        ModeOllama.calls = []
        status, _headers, body = self._post(
            port,
            {
                "mode": "pro",
                "messages": [{"role": "user", "content": "pro beside flash"}],
            },
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_model"], "qwen3:1.7b")
        self.assertEqual(_posts("/api/generate"), [])
        for name in ("qwen3:0.6b", "qwen3:1.7b"):
            self.assertIn(name, ModeOllama.loaded)
        ModeOllama.calls = []
        status, _headers, body = self._post(
            port,
            {"messages": [{"role": "user", "content": "flash beside flash"}]},
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_mode"], "flash")
        self.assertEqual(body["pi_model"], "qwen3:0.6b")
        self.assertEqual(_posts("/api/generate"), [])
        chats = _posts("/api/chat")
        self.assertEqual(chats[0]["model"], "qwen3:0.6b")
        self.assertNotIn("qwen3:1.7b", json.dumps(chats))
        for name in ("qwen3:0.6b", "qwen3:1.7b"):
            self.assertIn(name, ModeOllama.loaded)

    def test_switch_back_to_flash_unloads_nothing(self):
        port = self._boot()
        ModeOllama.loaded = ["qwen3:0.6b", "qwen3:1.7b"]
        headers = {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"}
        status, _headers, body = self._post(
            port,
            {"mode": "pro", "messages": [{"role": "user", "content": "use pro once"}]},
            headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_model"], "qwen3:1.7b")
        self.assertIn("qwen3:0.6b", ModeOllama.loaded)
        self.assertIn("qwen3:1.7b", ModeOllama.loaded)
        ModeOllama.calls = []
        status, _headers, body = self._post(
            port,
            {
                "mode": "flash",
                "model": "qwen3:1.7b",
                "messages": [{"role": "user", "content": "back"}],
            },
            headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_mode"], "flash")
        self.assertEqual(body["pi_model"], "qwen3:0.6b")
        self.assertEqual(_posts("/api/generate"), [])
        self.assertEqual(_posts("/api/chat")[0]["model"], "qwen3:0.6b")
        self.assertIn("qwen3:1.7b", ModeOllama.loaded)
        ModeOllama.calls = []
        status, _headers, body = self._post(
            port,
            {"messages": [{"role": "user", "content": "stay"}]},
            headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_mode"], "flash")
        self.assertEqual(_posts("/api/generate"), [])
        self.assertEqual(_posts("/api/chat")[0]["model"], "qwen3:0.6b")
        self.assertNotIn("qwen3:1.7b", json.dumps(_posts("/api/chat")))

    def test_header_pro_and_model_alias(self):
        port = self._boot()
        ModeOllama.loaded = ["qwen3:0.6b", "qwen3:1.7b"]
        status, headers, body = self._post(
            port,
            {
                "model": "qwen3:0.6b",
                "messages": [{"role": "user", "content": "header pro"}],
            },
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off", "X-Pi-Mode": "pro"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Pi-Mode"), "pro")
        self.assertEqual(body["pi_model"], "qwen3:1.7b")
        _reset_fake()
        ModeOllama.loaded = ["qwen3:0.6b", "qwen3:1.7b"]
        status, _headers, body = self._post(
            port,
            {
                "model": "qwen3:1.7b",
                "messages": [{"role": "user", "content": "alias pro"}],
            },
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["pi_mode"], "pro")
        self.assertEqual(_posts("/api/chat")[0]["model"], "qwen3:1.7b")

    def test_a_greeting_calls_the_model_in_either_mode(self):
        port = self._boot()
        ModeOllama.loaded = ["qwen3:0.6b", "qwen3:1.7b"]
        for mode in ("flash", "pro"):
            ModeOllama.calls = []
            status, headers, body = self._post(
                port,
                {"mode": mode, "messages": [{"role": "user", "content": "Hi!"}]},
                {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
            )
            self.assertEqual(status, 200, mode)
            self.assertEqual(headers.get("X-Pi-Chip"), "brain: pi4", mode)
            self.assertEqual(headers.get("X-Pi-Mode"), mode, mode)
            self.assertNotEqual(body["pi_model"], "canned", mode)
            self.assertEqual(
                body["choices"][0]["message"]["content"], "hello from peer", mode
            )
            self.assertEqual(body["pi_mode"], mode, mode)
            self.assertTrue(_posts("/api/chat"), mode)
            self.assertEqual(self.search_calls, [])

    def test_missing_pro_does_not_ask_ollama_to_pull(self):
        port = self._boot()
        ModeOllama.tags = ["qwen3:0.6b"]
        ModeOllama.calls = []
        status, _headers, body = self._post(
            port,
            {
                "mode": "pro",
                "messages": [{"role": "user", "content": "need the larger tag"}],
            },
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 502)
        self.assertEqual(body["error"], "The larger model is not ready yet.")
        self.assertNotIn("ollama pull", body["error"])
        self.assertNotIn("pi4", body["error"])
        self.assertEqual(_posts("/api/chat"), [])
        self.assertEqual(_posts("/api/generate"), [])
        self.assertEqual(ModeOllama.loaded, ["qwen3:0.6b"])

    def test_pro_shares_the_inference_gate_with_flash(self):
        from pair.errors import BUSY

        port = self._boot()
        ModeOllama.loaded = ["qwen3:0.6b", "qwen3:1.7b"]
        runtime.set_infer_slots(1)
        runtime.gate.queue_limit = 0
        self.assertTrue(runtime.gate.try_acquire())
        headers = {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"}
        status, _headers, body = self._post(
            port,
            {
                "mode": "pro",
                "messages": [{"role": "user", "content": "pro waits for a slot"}],
            },
            headers,
        )
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], BUSY)
        self.assertEqual(_posts("/api/chat"), [])
        self.assertEqual(_posts("/api/generate"), [])
        runtime.gate.release()
        self.assertTrue(runtime.gate.try_acquire())
        status, _headers, body = self._post(
            port,
            {
                "mode": "flash",
                "messages": [{"role": "user", "content": "flash waits for a slot"}],
            },
            headers,
        )
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], BUSY)
        self.assertEqual(_posts("/api/chat"), [])
        runtime.gate.release()
        release = threading.Event()
        entered = threading.Event()
        ModeOllama.block_chat = {"release": release, "entered": entered, "wrote": False}
        self.addCleanup(release.set)
        holder: dict = {}
        done = threading.Event()

        def held() -> None:
            try:
                holder["result"] = self._post(
                    port,
                    {
                        "mode": "pro",
                        "messages": [
                            {"role": "user", "content": "pro holds the only slot"}
                        ],
                        "stream": False,
                    },
                    headers,
                )
            except Exception as exc:
                holder["error"] = repr(exc)
            finally:
                done.set()

        threading.Thread(target=held, daemon=True).start()
        self.assertTrue(entered.wait(4), holder)
        self.assertEqual(runtime.gate.in_flight(), 1)
        status, _headers, body = self._post(
            port,
            {"messages": [{"role": "user", "content": "flash sees the same gate"}]},
            headers,
        )
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], BUSY)
        self.assertEqual(len(_posts("/api/chat")), 1)
        self.assertEqual(_posts("/api/chat")[0]["model"], "qwen3:1.7b")
        release.set()
        self.assertTrue(done.wait(4), holder)
        self.assertNotIn("error", holder, holder)
        self.assertEqual(runtime.gate.in_flight(), 0)

    def test_stream_sends_thinking_before_model_tokens(self):
        release = threading.Event()
        entered = threading.Event()
        port = self._boot()
        ModeOllama.block_chat = {"release": release, "entered": entered, "wrote": False}
        self.addCleanup(release.set)
        conn = HTTPConnection("127.0.0.1", port, timeout=4)
        holder: dict = {}
        got = threading.Event()
        done = threading.Event()

        def reader() -> None:
            try:
                payload = json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": "stream early please"}
                        ],
                        "stream": True,
                    }
                ).encode()
                conn.request(
                    "POST",
                    "/v1/chat/completions",
                    body=payload,
                    headers={
                        "content-type": "application/json",
                        "X-Pi-Target": "pi4",
                        "X-Pi-Mesh": "off",
                    },
                )
                response = conn.getresponse()
                data = b""
                while b'"pi_status": "thinking"' not in data:
                    piece = response.read(256)
                    if not piece:
                        break
                    data += piece
                holder["early"] = data
                holder["mode"] = response.headers.get("X-Pi-Mode")
                got.set()
                release.wait(5)
                while True:
                    piece = response.read(4096)
                    if not piece:
                        break
                    data += piece
                holder["all"] = data
            except Exception as exc:
                holder["error"] = repr(exc)
            finally:
                got.set()
                done.set()
                conn.close()

        threading.Thread(target=reader, daemon=True).start()
        self.assertTrue(got.wait(4), holder)
        self.assertNotIn("error", holder, holder)
        self.assertEqual(holder.get("mode"), "flash")
        self.assertIn(b'"pi_status": "thinking"', holder["early"])
        self.assertIn(b'"pi_mode": "flash"', holder["early"])
        self.assertNotIn(b"hel", holder["early"])
        self.assertFalse(ModeOllama.block_chat["wrote"])
        release.set()
        self.assertTrue(done.wait(4), holder)
        self.assertNotIn("error", holder, holder)
        self.assertTrue(entered.is_set())
        self.assertIn(b"hel", holder["all"])
        self.assertLess(
            holder["all"].index(b'"pi_status": "answering"'),
            holder["all"].index(b"hel"),
        )
        chats = _posts("/api/chat")
        self.assertEqual(chats[0]["model"], "qwen3:0.6b")
        self.assertEqual(chats[0]["keep_alive"], -1)
        self.assertTrue(chats[0]["stream"])

    def test_warm_pro_stream_says_thinking_before_the_first_token(self):
        release = threading.Event()
        entered = threading.Event()
        port = self._boot()
        ModeOllama.loaded = ["qwen3:0.6b", "qwen3:1.7b"]
        ModeOllama.block_chat = {"release": release, "entered": entered, "wrote": False}
        self.addCleanup(release.set)
        conn = HTTPConnection("127.0.0.1", port, timeout=4)
        holder: dict = {}
        got = threading.Event()
        done = threading.Event()

        def reader() -> None:
            try:
                payload = json.dumps(
                    {
                        "mode": "pro",
                        "messages": [{"role": "user", "content": "load pro please"}],
                        "stream": True,
                    }
                ).encode()
                conn.request(
                    "POST",
                    "/v1/chat/completions",
                    body=payload,
                    headers={
                        "content-type": "application/json",
                        "X-Pi-Target": "pi4",
                        "X-Pi-Mesh": "off",
                    },
                )
                response = conn.getresponse()
                data = b""
                while b'"pi_status": "thinking"' not in data:
                    piece = response.read(256)
                    if not piece:
                        break
                    data += piece
                holder["early"] = data
                holder["mode"] = response.headers.get("X-Pi-Mode")
                holder["route"] = response.headers.get("X-Pi-Route")
                got.set()
                release.wait(5)
                while True:
                    piece = response.read(4096)
                    if not piece:
                        break
                    data += piece
                holder["all"] = data
            except Exception as exc:
                holder["error"] = repr(exc)
            finally:
                got.set()
                done.set()
                conn.close()

        threading.Thread(target=reader, daemon=True).start()
        self.assertTrue(got.wait(4), holder)
        self.assertNotIn("error", holder, holder)
        self.assertEqual(holder.get("mode"), "pro")
        self.assertEqual(holder.get("route"), "pro")
        self.assertIn(b'"pi_status": "thinking"', holder["early"])
        self.assertIn(b'"pi_mode": "pro"', holder["early"])
        self.assertNotIn(b"hel", holder["early"])
        self.assertFalse(ModeOllama.block_chat["wrote"])
        release.set()
        self.assertTrue(done.wait(4), holder)
        self.assertNotIn("error", holder, holder)
        self.assertNotIn(b'"pi_status": "loading"', holder["all"])
        self.assertNotIn(b"Loading Pro", holder["all"])
        self.assertIn(b"hel", holder["all"])
        self.assertLess(
            holder["all"].index(b'"pi_status": "thinking"'), holder["all"].index(b"hel")
        )
        chats = _posts("/api/chat")
        self.assertEqual(chats[0]["model"], "qwen3:1.7b")
        self.assertEqual(_posts("/api/generate"), [])
        for name in ("qwen3:0.6b", "qwen3:1.7b"):
            self.assertIn(name, ModeOllama.loaded)

    def test_cold_pro_answers_on_flash_and_warms(self):
        port = self._boot()
        ModeOllama.loaded = ["qwen3:0.6b"]
        ModeOllama.calls = []
        status, headers, body = self._post(
            port,
            {"mode": "pro", "messages": [{"role": "user", "content": "cold pro turn"}]},
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["pi_model"], "qwen3:0.6b")
        self.assertEqual(body["pi_route"], "flash")
        self.assertEqual(headers.get("X-Pi-Route"), "flash")
        self.assertNotIn("loading", body.get("pi_stages") or [])
        self.assertNotIn("Loading Pro", json.dumps(body))
        chats = _posts("/api/chat")
        self.assertEqual(chats[0]["model"], "qwen3:0.6b")
        deadline = time.time() + 2
        while time.time() < deadline and not _posts("/api/generate"):
            time.sleep(0.02)
        warm = _posts("/api/generate")
        self.assertEqual(len(warm), 1)
        self.assertEqual(warm[0]["model"], "qwen3:1.7b")
        self.assertEqual(warm[0]["keep_alive"], -1)
        self.assertEqual(warm[0]["options"]["num_ctx"], 2048)
        self.assertEqual(warm[0]["options"]["num_batch"], 128)
        self.assertEqual(warm[0]["options"]["num_thread"], 4)
        self.assertIn("qwen3:0.6b", ModeOllama.loaded)
        self.assertIn("qwen3:1.7b", ModeOllama.loaded)

    def test_health_reports_pro_model(self):
        port = self._boot()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=5
        ) as response:
            body = json.loads(response.read().decode())
        self.assertEqual(body["pro_model"], "qwen3:1.7b")
        self.assertIsNotNone(body["pro_model"])
        self.assertNotIn("Loading Pro", json.dumps(body))

    def test_a_request_does_not_cold_load(self):
        port = self._boot()
        ModeOllama.loaded = []
        ModeOllama.calls = []
        status, _headers, body = self._post(
            port,
            {"messages": [{"role": "user", "content": "resident flash please"}]},
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200, body)
        self.assertNotIn("error", body)
        self.assertNotIn("larger model", json.dumps(body).lower())
        self.assertNotEqual(status, 502)
        self.assertEqual(body["choices"][0]["message"]["content"], "hello from peer")
        self.assertEqual(body["pi_model"], "qwen3:0.6b")
        self.assertNotIn("Loading Pro", json.dumps(body))
        chats = _posts("/api/chat")
        self.assertEqual(chats[0]["model"], "qwen3:0.6b")
        warm = _posts("/api/generate")
        self.assertEqual(len(warm), 1)
        self.assertEqual(warm[0]["model"], "qwen3:0.6b")
        self.assertEqual(warm[0]["keep_alive"], -1)
        self.assertEqual(warm[0]["options"]["num_ctx"], 2048)
        self.assertEqual(warm[0]["options"]["num_batch"], 128)
        self.assertEqual(warm[0]["options"]["num_thread"], 4)
        self.assertEqual(ModeOllama.loaded, ["qwen3:0.6b"])

    def test_a_cold_flash_wait_is_not_a_502(self):
        from pair.preload import COLD_WAIT_S

        port = self._boot()
        ModeOllama.loaded = []
        ModeOllama.hold_load = True
        ModeOllama.calls = []
        self.assertGreaterEqual(COLD_WAIT_S, 30)
        with patch("pair.preload.COLD_WAIT_S", 0.05):
            status, _headers, body = self._post(
                port,
                {"messages": [{"role": "user", "content": "still cold flash"}]},
                {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
            )
        self.assertEqual(status, 200, body)
        self.assertNotEqual(status, 502)
        text = body["choices"][0]["message"]["content"]
        self.assertIn("warming up", text.lower())
        self.assertNotIn("larger model", text.lower())
        self.assertNotIn("larger model", json.dumps(body).lower())
        self.assertEqual(_posts("/api/chat"), [])

    def test_unknown_residency_does_not_drop_pro(self):
        port = self._boot()
        ModeOllama.ps_fail = True
        ModeOllama.loaded = ["qwen3:0.6b"]
        ModeOllama.calls = []
        status, _headers, body = self._post(
            port,
            {"mode": "pro", "messages": [{"role": "user", "content": "probe failed"}]},
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["pi_model"], "qwen3:1.7b")
        self.assertEqual(body["pi_route"], "pro")
        self.assertEqual(_posts("/api/generate"), [])
        self.assertNotIn("Loading Pro", json.dumps(body))
        self.assertNotIn("loading", body["pi_stages"])

    def test_numbered_list_raises_the_budget_and_continues(self):
        port = self._boot()
        status, _headers, body = self._post(
            port,
            {
                "messages": [{"role": "user", "content": "top 10 movies"}],
                "stream": False,
                "pi_mesh": "off",
            },
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off", "X-Pi-Mode": "flash"},
        )
        self.assertEqual(status, 200, body)
        chats = [
            item[2]
            for item in ModeOllama.calls
            if item[0] == "POST" and item[1] == "/api/chat"
        ]
        self.assertEqual(len(chats), 1)
        self.assertEqual(chats[0]["options"]["num_predict"], 256)
        blob = "\n".join(
            row.get("content", "")
            for row in chats[0]["messages"]
            if isinstance(row, dict)
        )
        self.assertIn("OpenPi", blob)
        self.assertNotIn("each line", blob)
        self.assertNotIn("Stop at item", blob)
        self.assertEqual(self.search_calls, [])


if __name__ == "__main__":
    unittest.main()
