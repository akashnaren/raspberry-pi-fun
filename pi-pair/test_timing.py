"""pi_timing comes from Ollama's eval counters and the wall clock."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pair import runtime
from pair.server import make_server
from pair.timing import from_ollama, present


class TimingMath(unittest.TestCase):
    def test_nanoseconds_become_milliseconds(self):
        usage = from_ollama(
            {
                "prompt_eval_count": 12,
                "prompt_eval_duration": 4_000_000,
                "eval_count": 3,
                "eval_duration": 6_500_000,
            }
        )
        self.assertEqual(usage["prefill_tokens"], 12)
        self.assertEqual(usage["prefill_ms"], 4)
        self.assertEqual(usage["eval_tokens"], 3)
        self.assertEqual(usage["eval_ms"], 6)
        self.assertEqual(from_ollama(None)["eval_tokens"], 0)

    def test_public_timing_is_only_the_total(self):
        shown = present(
            {
                "queue_ms": 8,
                "search_ms": 3,
                "prefill_tokens": 12,
                "prefill_ms": 4,
                "eval_tokens": 3,
                "eval_ms": 6,
                "total_ms": 40,
            },
            True,
        )
        self.assertEqual(shown, 40)


class _Ollama(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"models": [{"name": "qwen3:0.6b"}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("content-type", "application/x-ndjson")
        self.end_headers()
        done = {
            "message": {"content": "noted"},
            "done": True,
            "prompt_eval_count": 12,
            "prompt_eval_duration": 4_000_000,
            "eval_count": 3,
            "eval_duration": 6_000_000,
        }
        self.wfile.write(json.dumps(done).encode() + b"\n")


class TimingHttp(unittest.TestCase):
    def setUp(self):
        self._peers = [dict(peer) for peer in runtime.PEERS]
        self._slots = runtime.INFER_SLOTS
        self._gate = runtime.gate
        runtime.reset_health()
        self.servers: list[ThreadingHTTPServer] = []
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["PI_PAIR_DATA"] = self._tmp.name
        os.environ["PI_PAIR_ROLE"] = "brain"
        canned = Path(self._tmp.name) / "canned_map.json"
        canned.write_text("{}", encoding="utf-8")
        os.environ["PI_PAIR_CANNED"] = str(canned)

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        runtime.PEERS = self._peers
        runtime.INFER_SLOTS = self._slots
        runtime.gate = self._gate
        runtime.reset_health()
        os.environ.pop("PI_PAIR_DATA", None)
        os.environ.pop("PI_PAIR_ROLE", None)
        os.environ.pop("PI_PAIR_CANNED", None)
        self._tmp.cleanup()

    def _boot(self) -> int:
        peer = ThreadingHTTPServer(("127.0.0.1", 0), _Ollama)
        self.servers.append(peer)
        threading.Thread(target=peer.serve_forever, daemon=True).start()
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": peer.server_address[1],
                    "kind": "ollama",
                    "note": "",
                    "generative": True,
                    "role": "brain",
                }
            ]
        )
        httpd = make_server("127.0.0.1", 0)
        self.servers.append(httpd)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd.server_address[1]

    def _post(self, port: int, headers: dict | None = None) -> dict:
        merged = {
            "content-type": "application/json",
            "X-Pi-Target": "pi4",
            "X-Pi-Mesh": "off",
        }
        if headers:
            merged.update(headers)
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "timing probe question please"}
                    ],
                    "stream": False,
                }
            ).encode(),
            headers=merged,
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode())

    def test_lan_breakdown_and_public_total(self):
        port = self._boot()
        body = self._post(port)
        timing = body["pi_timing"]
        self.assertEqual(timing["prefill_tokens"], 12)
        self.assertEqual(timing["prefill_ms"], 4)
        self.assertEqual(timing["eval_tokens"], 3)
        self.assertEqual(timing["eval_ms"], 6)
        self.assertEqual(timing["search_ms"], 0)
        self.assertGreaterEqual(timing["queue_ms"], 0)
        self.assertGreaterEqual(timing["total_ms"], 0)
        public = self._post(port, {"Cf-Ray": "abc"})
        self.assertIsInstance(public["pi_timing"], int)
        self.assertGreaterEqual(public["pi_timing"], 0)
