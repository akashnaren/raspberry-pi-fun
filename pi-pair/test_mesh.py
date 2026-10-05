"""pi4 asks pi2 for search and falls back locally. pi3 publishes hashed votes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair import runtime
from pair import server as pair_server
from pair.embed import set_warm_status
from pair.guard import may_generate
from pair.lifecycle import post_train
from pair.mesh import lookup_for_brain, mesh_config, search_timeouts
from pair.publish import (
    redact_label,
    sync_huggingface,
    sync_kaggle,
    sync_public_labels,
)
from pair.server import SEARCH_BODY_CAP, make_server

SECRET = "zz-secret-bench-phrase email ada@example.com phone 415-555-0130"
TOKEN = "test-hf-token"
KAGGLE = "test-kaggle-token"
PEPPER = "ab" * 32


def _label_hmac(text: str, pepper: str = PEPPER) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{64}", pepper):
        key = bytes.fromhex(pepper)
    else:
        key = pepper.encode()
    return hmac.new(key, text.encode(), hashlib.sha256).hexdigest()


def _start(httpd: ThreadingHTTPServer) -> None:
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def _listen(servers: list, handler) -> int:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    servers.append(httpd)
    _start(httpd)
    return httpd.server_address[1]


class _Quiet:
    def log_message(self, *args):
        pass


class SearchPage(_Quiet, BaseHTTPRequestHandler):
    seen: list[str] = []

    def do_GET(self):
        if self.path.split("?")[0] != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode() or "{}")
        type(self).seen.append(
            self.path.split("?")[0] + " " + str(payload.get("q") or "")
        )
        body = json.dumps(
            {
                "status": "ok",
                "sources": [{"title": "Bench", "url": "https://example.com/bench"}],
                "context": "Web search notes.\nbench from pi2",
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SlowSearch(_Quiet, BaseHTTPRequestHandler):
    def do_POST(self):
        time.sleep(1.2)
        body = b'{"status":"ok","sources":[],"context":"late"}'
        try:
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return


class GarbageSearch(_Quiet, BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        body = b'{"nope":true}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FailedSearch(_Quiet, BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        body = b'{"status":"failed","sources":[],"context":""}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class OllamaPage(_Quiet, BaseHTTPRequestHandler):
    posts = 0
    last_payload = None

    def do_GET(self):
        body = json.dumps({"models": [{"name": "qwen2.5:0.5b"}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode() or "{}")
        type(self).posts += 1
        type(self).last_payload = payload
        body = json.dumps({"message": {"content": "local answer"}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MeshRoles(unittest.TestCase):
    def test_config_splits_generate_search_and_labels(self):
        cfg = mesh_config()
        self.assertEqual(cfg["generate"], ["pi4"])
        self.assertEqual(cfg["decode"], ["pi4"])
        self.assertEqual(cfg["embed"], ["pi4"])
        self.assertEqual(cfg["search"], ["pi2"])
        self.assertEqual(cfg["health"], ["pi2"])
        self.assertEqual(cfg["dataset_and_train"], ["pi3"])
        self.assertEqual(cfg["public_labels"], ["pi3"])
        self.assertEqual(cfg["hf_dataset"], "akashnaren/pi-mesh-labels")
        self.assertLessEqual(float(cfg["remote_search_connect_timeout_s"]), 1.0)
        peers = json.loads((ROOT / "peers.example.json").read_text(encoding="utf-8"))
        by_name = {peer["name"]: peer for peer in peers}
        self.assertEqual(by_name["pi2"]["role"], "health")
        self.assertEqual(by_name["pi3"]["role"], "dataset")
        self.assertEqual(by_name["pi4"]["role"], "brain")
        self.assertFalse(may_generate(by_name["pi2"]))
        self.assertFalse(may_generate(by_name["pi3"]))
        self.assertTrue(may_generate(by_name["pi4"]))
        self.assertIn("No decode", by_name["pi2"]["note"])
        self.assertIn("No decode", by_name["pi3"]["note"])

    def test_connect_budget_stays_short(self):
        previous = os.environ.get("PI_PAIR_REMOTE_SEARCH_CONNECT_TIMEOUT")
        read = os.environ.get("PI_PAIR_REMOTE_SEARCH_TIMEOUT")
        os.environ["PI_PAIR_REMOTE_SEARCH_CONNECT_TIMEOUT"] = "50"
        os.environ["PI_PAIR_REMOTE_SEARCH_TIMEOUT"] = "50"
        try:
            connect, read_s = search_timeouts()
        finally:
            if previous is None:
                os.environ.pop("PI_PAIR_REMOTE_SEARCH_CONNECT_TIMEOUT", None)
            else:
                os.environ["PI_PAIR_REMOTE_SEARCH_CONNECT_TIMEOUT"] = previous
            if read is None:
                os.environ.pop("PI_PAIR_REMOTE_SEARCH_TIMEOUT", None)
            else:
                os.environ["PI_PAIR_REMOTE_SEARCH_TIMEOUT"] = read
        self.assertLessEqual(connect, 2.0)
        self.assertLessEqual(read_s, 12.0)


class SearchRouting(unittest.TestCase):
    def setUp(self):
        self.servers: list[ThreadingHTTPServer] = []
        self._peers = [dict(peer) for peer in runtime.PEERS]
        self._env = {
            key: os.environ.get(key)
            for key in (
                "PI_PAIR_REMOTE_SEARCH",
                "PI_PAIR_REMOTE_SEARCH_CONNECT_TIMEOUT",
                "PI_PAIR_REMOTE_SEARCH_TIMEOUT",
                "PI_PAIR_ROLE",
            )
        }
        os.environ["PI_PAIR_REMOTE_SEARCH"] = "1"
        os.environ["PI_PAIR_ROLE"] = "brain"
        SearchPage.seen = []

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        runtime.PEERS = self._peers
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _peer(self, port: int) -> None:
        runtime.PEERS = [
            {
                "name": "pi2",
                "host": "127.0.0.1",
                "port": port,
                "kind": "health",
                "role": "health",
                "generative": False,
                "note": "",
            }
        ]

    def test_remote_hit_skips_local(self):
        port = _listen(self.servers, SearchPage)
        self._peer(port)
        local = []

        def fake(query):
            local.append(query)
            return {"status": "failed", "sources": [], "context": ""}

        found = lookup_for_brain("bench height", local=fake)
        self.assertEqual(found["via"], "pi2")
        self.assertEqual(found["status"], "ok")
        self.assertIn("bench from pi2", found["context"])
        self.assertEqual(local, [])

    def test_down_pi2_falls_back_locally(self):
        self._peer(1)
        os.environ["PI_PAIR_REMOTE_SEARCH_CONNECT_TIMEOUT"] = "0.4"
        local = []

        def fake(query):
            local.append(query)
            return {"status": "ok", "sources": [], "context": "local notes"}

        started = time.perf_counter()
        found = lookup_for_brain("bench height", local=fake)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0)
        self.assertEqual(local, ["bench height"])
        self.assertEqual(found["via"], "local")
        self.assertEqual(found["context"], "local notes")

    def test_slow_pi2_falls_back_before_the_read_budget(self):
        port = _listen(self.servers, SlowSearch)
        self._peer(port)
        os.environ["PI_PAIR_REMOTE_SEARCH_TIMEOUT"] = "0.3"
        os.environ["PI_PAIR_REMOTE_SEARCH_CONNECT_TIMEOUT"] = "0.4"
        local = []

        def fake(query):
            local.append(query)
            return {"status": "failed", "sources": [], "context": ""}

        started = time.perf_counter()
        found = lookup_for_brain("bench height", local=fake)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0)
        self.assertEqual(found["via"], "local")
        self.assertEqual(local, ["bench height"])

    def test_remote_off_stays_local(self):
        port = _listen(self.servers, SearchPage)
        self._peer(port)
        os.environ["PI_PAIR_REMOTE_SEARCH"] = "0"
        SearchPage.seen = []
        local = []

        def fake(query):
            local.append(query)
            return {"status": "failed", "sources": [], "context": ""}

        found = lookup_for_brain("bench height", local=fake)
        self.assertEqual(found["via"], "local")
        self.assertEqual(local, ["bench height"])
        self.assertEqual(SearchPage.seen, [])

    def test_failed_remote_is_not_retried_locally(self):
        port = _listen(self.servers, FailedSearch)
        self._peer(port)
        local = []

        def fake(query):
            local.append(query)
            return {"status": "ok", "sources": [], "context": "should not run"}

        found = lookup_for_brain("bench height", local=fake)
        self.assertEqual(found["via"], "pi2")
        self.assertEqual(found["status"], "failed")
        self.assertEqual(local, [])

    def test_garbage_remote_falls_back(self):
        port = _listen(self.servers, GarbageSearch)
        self._peer(port)
        local = []

        def fake(query):
            local.append(query)
            return {"status": "ok", "sources": [], "context": "local notes"}

        found = lookup_for_brain("bench height", local=fake)
        self.assertEqual(found["via"], "local")
        self.assertEqual(local, ["bench height"])


class SearchRoute(unittest.TestCase):
    def setUp(self):
        self.servers: list[ThreadingHTTPServer] = []
        self._role = os.environ.get("PI_PAIR_ROLE")
        self._lookup = pair_server.lookup_web
        self._chat = pair_server.chat_ollama
        self._stream = pair_server.stream_ollama

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        pair_server.lookup_web = self._lookup
        pair_server.chat_ollama = self._chat
        pair_server.stream_ollama = self._stream
        if self._role is None:
            os.environ.pop("PI_PAIR_ROLE", None)
        else:
            os.environ["PI_PAIR_ROLE"] = self._role

    def _post(self, port: int) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/search",
            data=json.dumps({"q": "bench height"}).encode(),
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode() or "{}")

    def test_health_serves_search_and_does_not_decode(self):
        calls = []

        def fake(query, opener=None):
            calls.append(query)
            return {
                "status": "ok",
                "sources": [{"title": "Bench", "url": "https://example.com/bench"}],
                "context": "notes",
            }

        def boom(*args, **kwargs):
            raise AssertionError("decode")

        pair_server.lookup_web = fake
        pair_server.chat_ollama = boom
        pair_server.stream_ollama = boom
        os.environ["PI_PAIR_ROLE"] = "health"
        httpd = make_server("127.0.0.1", 0)
        self.servers.append(httpd)
        _start(httpd)
        status, body = self._post(httpd.server_address[1])
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(calls, ["bench height"])
        self.assertNotIn("choices", body)

    def test_brain_and_dataset_refuse_search(self):
        for role in ("brain", "dataset"):
            os.environ["PI_PAIR_ROLE"] = role
            httpd = make_server("127.0.0.1", 0)
            self.servers.append(httpd)
            _start(httpd)
            status, body = self._post(httpd.server_address[1])
            self.assertEqual(status, 403, role)
            self.assertEqual(body["error"], "Search is not available from here.")
            self.assertNotIn("health host", body["error"])

    def test_search_rejects_a_body_over_4kb(self):
        calls = []

        def fake(query, opener=None):
            calls.append(query)
            return {"status": "ok", "sources": [], "context": ""}

        pair_server.lookup_web = fake
        os.environ["PI_PAIR_ROLE"] = "health"
        httpd = make_server("127.0.0.1", 0)
        self.servers.append(httpd)
        _start(httpd)
        port = httpd.server_address[1]
        self.assertEqual(SEARCH_BODY_CAP, 4096)
        exact = b'{"q":"' + (b"a" * (SEARCH_BODY_CAP - 8)) + b'"}'
        self.assertEqual(len(exact), SEARCH_BODY_CAP)
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/search",
            data=exact,
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 200)
        over = exact + b" "
        self.assertGreater(len(over), SEARCH_BODY_CAP)
        huge = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/search",
            data=over,
            headers={"content-type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(huge, timeout=2)
        self.assertEqual(raised.exception.code, 413)
        self.assertEqual(calls, ["a" * (SEARCH_BODY_CAP - 8)])


class ChatOffload(unittest.TestCase):
    def setUp(self):
        self.servers: list[ThreadingHTTPServer] = []
        self._peers = [dict(peer) for peer in runtime.PEERS]
        self._env = {
            key: os.environ.get(key)
            for key in (
                "PI_PAIR_ROLE",
                "PI_PAIR_REMOTE_SEARCH",
                "PI_PAIR_DATA",
                "PI_PAIR_CANNED",
                "HF_TOKEN",
                "KAGGLE_API_TOKEN",
            )
        }
        self._tmp = tempfile.TemporaryDirectory()
        self._lookup = pair_server.lookup_web
        self._images = pair_server.lookup_images
        os.environ["PI_PAIR_ROLE"] = "brain"
        os.environ["PI_PAIR_REMOTE_SEARCH"] = "1"
        os.environ["PI_PAIR_DATA"] = self._tmp.name
        os.environ["PI_PAIR_CANNED"] = str(ROOT / "data" / "canned" / "canned_map.json")
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("KAGGLE_API_TOKEN", None)
        set_warm_status("warming")
        self.local_calls = []

        def local(query, opener=None):
            self.local_calls.append(query)
            return {"status": "failed", "sources": [], "context": ""}

        pair_server.lookup_web = local
        pair_server.lookup_images = lambda query, opener=None: []
        runtime.reset_health()
        SearchPage.seen = []
        OllamaPage.posts = 0
        OllamaPage.last_payload = None

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        pair_server.lookup_web = self._lookup
        pair_server.lookup_images = self._images
        runtime.PEERS = self._peers
        runtime.reset_health()
        set_warm_status("ready")
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def _arm(self, search_port: int, ollama_port: int) -> int:
        runtime.set_peers(
            [
                {
                    "name": "pi2",
                    "host": "127.0.0.1",
                    "port": search_port,
                    "kind": "health",
                    "role": "health",
                    "generative": False,
                    "note": "",
                },
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": ollama_port,
                    "kind": "ollama",
                    "role": "brain",
                    "generative": True,
                    "note": "",
                },
            ]
        )
        runtime.reset_health()
        httpd = make_server("127.0.0.1", 0)
        self.servers.append(httpd)
        _start(httpd)
        return httpd.server_address[1]

    def _chat(self, port: int, mesh: str, target: str = "auto") -> tuple[int, dict]:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "qwen2.5:0.5b",
                    "messages": [
                        {"role": "user", "content": "How tall is the zinc bench today?"}
                    ],
                    "stream": False,
                }
            ).encode(),
            headers={
                "content-type": "application/json",
                "X-Pi-Mesh": mesh,
                "X-Pi-Target": target,
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode())

    def test_pi4_uses_pi2_then_falls_back_without_decoding_there(self):
        search_port = _listen(self.servers, SearchPage)
        ollama_port = _listen(self.servers, OllamaPage)
        port = self._arm(search_port, ollama_port)
        status, body = self._chat(port, "on")
        self.assertEqual(status, 200)
        self.assertEqual(body["choices"][0]["message"]["content"], "local answer")
        self.assertEqual(self.local_calls, [])
        self.assertEqual(OllamaPage.posts, 1)
        blob = json.dumps(OllamaPage.last_payload)
        self.assertIn("bench from pi2", blob)
        self.assertTrue(all(item.startswith("/v1/search ") for item in SearchPage.seen))
        self.assertFalse(
            may_generate({"name": "pi2", "role": "health", "generative": False})
        )

        self.servers[0].shutdown()
        self.servers[0].server_close()
        runtime.reset_health()
        status, body = self._chat(port, "on")
        self.assertEqual(status, 200)
        self.assertEqual(self.local_calls, ["How tall is the zinc bench today?"])
        self.assertEqual(OllamaPage.posts, 2)
        self.assertNotIn("bench from pi2", json.dumps(OllamaPage.last_payload))

    def test_mesh_off_does_not_ask_pi2(self):
        search_port = _listen(self.servers, SearchPage)
        ollama_port = _listen(self.servers, OllamaPage)
        port = self._arm(search_port, ollama_port)
        SearchPage.seen = []
        status, _body = self._chat(port, "off", "pi4")
        self.assertEqual(status, 200)
        self.assertEqual(SearchPage.seen, [])
        self.assertEqual(self.local_calls, [])
        self.assertEqual(OllamaPage.posts, 1)


class PublicLabels(unittest.TestCase):
    def setUp(self):
        self._env = {
            key: os.environ.get(key)
            for key in (
                "HF_TOKEN",
                "KAGGLE_API_TOKEN",
                "HF_DATASET_ID",
                "PI_PAIR_ROLE",
                "PI_PAIR_DATA",
                "PI_PAIR_LABEL_PEPPER",
            )
        }
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("KAGGLE_API_TOKEN", None)
        os.environ.pop("HF_DATASET_ID", None)
        os.environ.pop("PI_PAIR_LABEL_PEPPER", None)

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_redact_keeps_the_vote_and_drops_the_chat(self):
        os.environ["PI_PAIR_LABEL_PEPPER"] = PEPPER
        public = redact_label(
            {
                "prompt": SECRET,
                "answer": "the side door code is 2468",
                "correction": "use the side door",
                "vote": "down",
                "chip": "brain: pi4",
                "peer": "pi4",
            }
        )
        blob = json.dumps(public)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("ada@example.com", blob)
        self.assertNotIn("415-555-0130", blob)
        self.assertNotIn("2468", blob)
        self.assertNotIn("side door", blob)
        self.assertEqual(public["vote"], "down")
        plain = hashlib.sha256(SECRET.encode()).hexdigest()
        self.assertNotEqual(public["prompt_sha256"], plain)
        self.assertEqual(public["prompt_sha256"], _label_hmac(SECRET))
        self.assertNotEqual(
            public["answer_sha256"],
            hashlib.sha256(b"the side door code is 2468").hexdigest(),
        )
        self.assertNotEqual(
            public["correction_sha256"],
            hashlib.sha256(b"use the side door").hexdigest(),
        )
        self.assertTrue(public["redacted"])
        self.assertEqual(public["chip"], "brain: pi4")

    def test_published_hash_is_hmac_not_plain_sha256(self):
        samples = (
            "hi",
            "what is the weather today?",
            "How tall is the zinc bench today?",
        )
        os.environ["PI_PAIR_LABEL_PEPPER"] = PEPPER
        for text in samples:
            public = redact_label({"prompt": text, "answer": "sunny", "vote": "up"})
            plain_prompt = hashlib.sha256(text.encode()).hexdigest()
            plain_answer = hashlib.sha256(b"sunny").hexdigest()
            self.assertNotEqual(public["prompt_sha256"], plain_prompt, text)
            self.assertNotEqual(public["answer_sha256"], plain_answer, text)
            self.assertEqual(public["prompt_sha256"], _label_hmac(text))
            self.assertEqual(public["answer_sha256"], _label_hmac("sunny"))
        raw_pepper = ("R" * 31) + "!"
        self.assertGreaterEqual(len(raw_pepper.encode()), 32)
        os.environ["PI_PAIR_LABEL_PEPPER"] = raw_pepper
        public = redact_label(
            {"prompt": "hi", "answer": "sunny", "correction": "clear", "vote": "down"}
        )
        self.assertNotEqual(public["prompt_sha256"], hashlib.sha256(b"hi").hexdigest())
        self.assertEqual(public["prompt_sha256"], _label_hmac("hi", raw_pepper))
        self.assertNotEqual(
            public["correction_sha256"], hashlib.sha256(b"clear").hexdigest()
        )
        self.assertEqual(public["correction_sha256"], _label_hmac("clear", raw_pepper))
        os.environ["PI_PAIR_LABEL_PEPPER"] = "too-short"
        self.assertIsNone(
            redact_label({"prompt": "hi", "answer": "sunny", "vote": "up"})
        )
        os.environ.pop("PI_PAIR_LABEL_PEPPER", None)
        self.assertIsNone(
            redact_label({"prompt": "hi", "answer": "sunny", "vote": "up"})
        )
        os.environ["HF_TOKEN"] = TOKEN

        def opener(request, timeout):
            raise AssertionError(request.full_url)

        skipped = sync_huggingface(
            [{"prompt": "hi", "answer": "sunny", "vote": "up"}],
            opener=opener,
        )
        self.assertEqual(skipped["status"], "skipped")
        self.assertNotIn(hashlib.sha256(b"hi").hexdigest(), json.dumps(skipped))

    def test_missing_hf_token_does_not_reach_kaggle(self):
        os.environ["KAGGLE_API_TOKEN"] = KAGGLE

        def opener(request, timeout):
            raise AssertionError(request.full_url)

        result = sync_public_labels(
            [{"prompt": SECRET, "answer": "hidden reply", "vote": "up"}],
            opener=opener,
        )
        self.assertEqual(result["huggingface"]["status"], "skipped")
        self.assertEqual(result["huggingface"]["reason"], "HF_TOKEN unset")
        self.assertEqual(result["kaggle"]["reason"], "behind huggingface")
        self.assertNotIn(KAGGLE, json.dumps(result))
        self.assertNotIn(SECRET, json.dumps(result))

    def test_hf_upload_is_hashes_and_kaggle_follows(self):
        os.environ["HF_TOKEN"] = TOKEN
        os.environ["KAGGLE_API_TOKEN"] = KAGGLE
        os.environ["PI_PAIR_LABEL_PEPPER"] = PEPPER
        seen = []

        class Resp:
            def __init__(self):
                self.status = 200

            def read(self, _n=None):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(request, timeout):
            seen.append(request)
            return Resp()

        import pair.publish as publish

        order = []
        original = publish.sync_kaggle

        def wrapped(rows):
            order.append("kaggle")
            return original(rows)

        publish.sync_kaggle = wrapped
        try:
            result = sync_public_labels(
                [
                    {
                        "prompt": SECRET,
                        "answer": "hidden reply",
                        "correction": "public correction text",
                        "vote": "up",
                        "chip": "brain: pi4",
                        "peer": "pi4",
                    }
                ],
                opener=opener,
            )
        finally:
            publish.sync_kaggle = original
        self.assertEqual(result["huggingface"]["status"], "ok")
        self.assertEqual(result["huggingface"]["repo"], "akashnaren/pi-mesh-labels")
        self.assertFalse(result["huggingface"]["private"])
        self.assertEqual(result["kaggle"]["status"], "stub")
        self.assertEqual(order, ["kaggle"])
        self.assertEqual(
            [item.get_method() for item in seen],
            ["POST", "PUT", "POST"],
        )
        self.assertTrue(seen[0].full_url.endswith("/api/repos/create"))
        self.assertIn("/settings", seen[1].full_url)
        self.assertTrue(seen[2].full_url.endswith("/commit/main"))
        for item in seen:
            self.assertEqual(item.get_header("Authorization"), f"Bearer {TOKEN}")
            raw = item.data.decode()
            self.assertNotIn(TOKEN, raw)
            self.assertNotIn(KAGGLE, raw)
            self.assertNotIn(SECRET, raw)
            self.assertNotIn("hidden reply", raw)
            self.assertNotIn("ada@example.com", raw)
        create = json.loads(seen[0].data.decode())
        self.assertEqual(create["type"], "dataset")
        self.assertEqual(create["name"], "pi-mesh-labels")
        self.assertIs(create["private"], False)
        self.assertIs(json.loads(seen[1].data.decode())["private"], False)
        files = {}
        for line in seen[2].data.decode().splitlines():
            item = json.loads(line)
            if item["key"] == "file":
                files[item["value"]["path"]] = base64.b64decode(
                    item["value"]["content"]
                ).decode()
        self.assertNotIn(SECRET, files["labels.jsonl"])
        self.assertNotIn(SECRET, files["README.md"])
        row = json.loads(files["labels.jsonl"].splitlines()[0])
        self.assertEqual(row["vote"], "up")
        self.assertNotEqual(
            row["prompt_sha256"], hashlib.sha256(SECRET.encode()).hexdigest()
        )
        self.assertEqual(row["prompt_sha256"], _label_hmac(SECRET))
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn(KAGGLE, json.dumps(result))

    def test_hf_failure_skips_kaggle(self):
        os.environ["HF_TOKEN"] = TOKEN
        os.environ["KAGGLE_API_TOKEN"] = KAGGLE
        os.environ["PI_PAIR_LABEL_PEPPER"] = PEPPER

        def opener(request, timeout):
            raise urllib.error.URLError("down")

        result = sync_huggingface(
            [{"prompt": SECRET, "answer": "hidden reply", "vote": "up"}],
            opener=opener,
        )
        wrapped = sync_public_labels(
            [{"prompt": SECRET, "answer": "hidden reply", "vote": "up"}],
            opener=opener,
        )
        self.assertEqual(result["status"], "error")
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertEqual(wrapped["kaggle"]["reason"], "behind huggingface")
        self.assertNotEqual(wrapped["kaggle"].get("status"), "stub")

    def test_kaggle_stub_does_not_upload(self):
        os.environ["KAGGLE_API_TOKEN"] = KAGGLE
        with mock.patch("urllib.request.urlopen") as urlopen:
            result = sync_kaggle(
                [{"prompt": SECRET, "answer": "hidden reply", "vote": "up"}]
            )
        urlopen.assert_not_called()
        self.assertEqual(result["status"], "stub")
        self.assertNotIn(KAGGLE, json.dumps(result))
        self.assertNotIn(SECRET, json.dumps(result))

    def test_post_train_keeps_hashes_and_deletes_raw_chat(self):
        os.environ["PI_PAIR_ROLE"] = "dataset"
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = base / "data"
            shutil.copytree(ROOT / "data", data)
            os.environ["PI_PAIR_DATA"] = str(data)
            os.environ["PI_PAIR_LABEL_PEPPER"] = PEPPER
            queue = data / "train" / "pending" / "queue.jsonl"
            queue.parent.mkdir(parents=True, exist_ok=True)
            queue.write_text(
                json.dumps(
                    {
                        "prompt": SECRET,
                        "answer": "hidden reply",
                        "vote": "up",
                        "chip": "brain: pi4",
                        "peer": "pi4",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = post_train(root=data, adapters=base / "adapters")
            public = (data / "train" / "public" / "labels.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(SECRET, public)
            self.assertNotIn("hidden reply", public)
            self.assertNotIn(hashlib.sha256(SECRET.encode()).hexdigest(), public)
            self.assertIn(_label_hmac(SECRET), public)
            self.assertFalse(queue.exists())
            self.assertEqual(list((data / "train" / "active").glob("*.jsonl")), [])
            self.assertEqual(result["public_sync"]["huggingface"]["status"], "skipped")
            done = list((data / "train" / "done").glob("*.json"))
            self.assertEqual(len(done), 1)
            self.assertNotIn(SECRET, done[0].read_text(encoding="utf-8"))

    def test_sync_script_refuses_other_roles_and_skips_without_a_token(self):
        script = ROOT / "scripts" / "data" / "sync_mesh_labels.py"
        env = os.environ.copy()
        env["PI_PAIR_ROLE"] = "health"
        env.pop("HF_TOKEN", None)
        env.pop("KAGGLE_API_TOKEN", None)
        refused = subprocess.run(
            [sys.executable, str(script)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("pi3", refused.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            env["PI_PAIR_ROLE"] = "dataset"
            env["PI_PAIR_DATA"] = tmp
            ran = subprocess.run(
                [sys.executable, str(script)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(ran.returncode, 0, ran.stderr)
        body = json.loads(ran.stdout)
        self.assertEqual(body["huggingface"]["status"], "skipped")
        self.assertNotIn(TOKEN, ran.stdout)

    def test_repo_has_no_hub_tokens(self):
        install = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("Do not set HF_TOKEN", install)
        for line in ("'HF_TOKEN='", "'KAGGLE_API_TOKEN='", "'PI_PAIR_LABEL_PEPPER='"):
            self.assertIn(line, install)
        self.assertNotRegex(
            install, r"(?:HF_TOKEN|KAGGLE_API_TOKEN|PI_PAIR_LABEL_PEPPER)=[A-Za-z0-9]"
        )
        banned = re.compile(
            r"hf_[A-Za-z0-9]{8,}|HF_TOKEN\s*=\s*['\"]?[A-Za-z0-9]|KAGGLE_API_TOKEN\s*=\s*['\"]?[A-Za-z0-9]"
        )
        roots = [ROOT, ROOT.parent / "README.md"]
        skip = {".git", "node_modules", "__pycache__", "out"}
        for root in roots:
            paths = [root] if root.is_file() else root.rglob("*")
            for path in paths:
                if not path.is_file():
                    continue
                if any(part in skip for part in path.parts):
                    continue
                if path.suffix in {".png", ".jpg", ".jpeg", ".woff", ".woff2", ".ttf"}:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                self.assertIsNone(banned.search(text), path)


if __name__ == "__main__":
    unittest.main()
