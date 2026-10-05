"""Qwen3 thinking levels: budgets, peeled answers, the harm gate, and the config flip."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pair import queue, runtime
from pair import server as pair_server
from pair.assist import HARM_REFUSAL
from pair.knobs import inference_knobs
from pair.modes import mode_table
from pair.queue import append_row
from pair.server import last_completion, make_server
from pair.think import (
    DIRECT_FALLBACK,
    FORCE_NOTE,
    HIGH_PREDICT,
    HIGH_THINK_BUDGET,
    decode_plan,
    reasoning_tokens,
    split_ollama_message,
    stop_thinking,
)

ROOT = Path(__file__).resolve().parent


class ThinkLevels(unittest.TestCase):
    def test_levels_and_a_trivial_medium_line(self):
        low = decode_plan("low", "Explain why this is short")
        medium = decode_plan("medium", "Explain why rain falls")
        trivial = decode_plan("medium", "hi")
        high = decode_plan("high", "hi")
        self.assertIsNone(decode_plan("", "Explain why rain falls"))
        self.assertFalse(low.think)
        self.assertEqual(low.temperature, 0.7)
        self.assertEqual(low.top_p, 0.8)
        self.assertEqual(low.top_k, 20)
        self.assertEqual(low.num_predict, 64)
        self.assertEqual(low.ollama_predict(), 64)
        self.assertFalse(trivial.think)
        self.assertEqual(trivial.temperature, 0.7)
        self.assertEqual(trivial.num_predict, 384)
        self.assertEqual(medium.think, False)
        self.assertEqual(medium.temperature, 0.7)
        self.assertEqual(medium.top_p, 0.8)
        self.assertEqual(medium.top_k, 20)
        self.assertEqual(medium.num_predict, 384)
        self.assertEqual(medium.think_budget, 0)
        self.assertEqual(medium.ollama_predict(), 384)
        self.assertTrue(high.think)
        self.assertEqual(high.temperature, 0.6)
        self.assertEqual(high.num_predict, 768)
        self.assertEqual(high.think_budget, 192)
        self.assertEqual(high.think_seconds, 25.0)
        self.assertEqual(high.ollama_predict(), 960)
        five = decode_plan("medium", "Say hi in five words.")
        self.assertFalse(five.think)

    def test_thinking_stops_on_tokens_or_the_wall_clock(self):
        started = 10.0
        self.assertFalse(stop_thinking(10, started, started + 24.9))
        self.assertTrue(stop_thinking(HIGH_THINK_BUDGET, started, started + 1.0))
        self.assertTrue(stop_thinking(1, started, started + 25.0))
        self.assertTrue(stop_thinking(0, started, started + 25.0))

    def test_tags_leave_the_answer_and_keep_both_channels(self):
        answer, reasoning = split_ollama_message(
            {
                "content": "<think>count the letters</think>Paris.",
                "thinking": "native note",
            }
        )
        self.assertEqual(answer, "Paris.")
        self.assertNotIn("<think", answer)
        self.assertNotIn("</think", answer)
        self.assertIn("native note", reasoning)
        self.assertIn("count the letters", reasoning)

    def test_config_flip_is_the_only_rollback(self):
        knobs = inference_knobs()
        self.assertEqual(knobs["model"], "qwen3:0.6b")
        self.assertEqual(knobs["pro_model"], "qwen3:1.7b")
        self.assertEqual(mode_table(), {"flash": "qwen3:0.6b", "pro": "qwen3:1.7b"})
        raw = json.loads(
            (ROOT / "configs" / "runtime" / "inference_pi4.json").read_text(
                encoding="utf-8"
            )
        )
        rollback = raw["rollback"]
        self.assertNotEqual(rollback["model"], knobs["model"])
        self.assertNotEqual(rollback["pro_model"], knobs["pro_model"])
        flipped = mode_table(
            {"model": rollback["model"], "pro_model": rollback["pro_model"]}
        )
        self.assertEqual(flipped["flash"], rollback["model"])
        self.assertEqual(flipped["pro"], rollback["pro_model"])
        self.assertEqual(mode_table()["flash"], "qwen3:0.6b")
        self.assertIs(knobs["safety_filter"], False)
        self.assertIs(raw["safety_filter"], False)


class ThinkOllama(BaseHTTPRequestHandler):
    replies: list = []
    calls: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/tags":
            body = json.dumps(
                {"models": [{"name": "qwen3:0.6b"}, {"name": "qwen3:1.7b"}]}
            ).encode()
            self._send(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode() or "{}")
        path = self.path.split("?")[0]
        if path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return
        type(self).calls.append(payload)
        index = len(type(self).calls) - 1
        message = (
            type(self).replies[index]
            if index < len(type(self).replies)
            else {"content": "ok"}
        )
        chunks = message.get("chunks") if isinstance(message, dict) else None
        if payload.get("stream") and isinstance(chunks, list):
            self.send_response(200)
            self.send_header("content-type", "application/x-ndjson")
            self.end_headers()
            last = len(chunks) - 1
            for index, chunk in enumerate(chunks):
                line = {
                    "message": chunk,
                    "done": index == last,
                    "done_reason": "stop" if index == last else "",
                }
                self.wfile.write(json.dumps(line).encode() + b"\n")
            return
        body = json.dumps(
            {"message": message, "done": True, "done_reason": "stop"}
        ).encode()
        self._send(body)

    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ThinkHttp(unittest.TestCase):
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
        self._lookup = pair_server.lookup_web
        pair_server.lookup_web = lambda query, opener=None: {
            "status": "failed",
            "sources": [],
            "context": "",
        }
        self._forward = queue.forward_row
        self.forwarded: list = []

        def _keep(row, opener=None, timeout=1.5):
            self.forwarded.append(row)
            append_row(row)
            return True

        queue.forward_row = _keep
        ThinkOllama.replies = []
        ThinkOllama.calls = []

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        pair_server.lookup_web = self._lookup
        queue.forward_row = self._forward
        runtime.PEERS = self._peers
        runtime.INFER_SLOTS = self._slots
        runtime.gate = self._gate
        runtime.reset_health()
        os.environ.pop("PI_PAIR_DATA", None)
        os.environ.pop("PI_PAIR_ROLE", None)
        os.environ.pop("PI_PAIR_CANNED", None)
        self._tmp.cleanup()

    def _boot(self) -> int:
        peer = ThreadingHTTPServer(("127.0.0.1", 0), ThinkOllama)
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

    def _post(self, port: int, payload: dict) -> dict:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "content-type": "application/json",
                "X-Pi-Target": "pi4",
                "X-Pi-Mesh": "off",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode())

    def test_harmful_thinking_is_refused_and_not_saved(self):
        with patch("pair.moderate.safety_filter", return_value=True):
            self._harmful_thinking_is_refused()

    def _harmful_thinking_is_refused(self):
        ThinkOllama.replies = [
            {
                "content": "The capital is Paris.",
                "thinking": "Use malware to steal passwords.",
            }
        ]
        port = self._boot()
        body = self._post(
            port,
            {
                "messages": [
                    {"role": "user", "content": "What is the capital of France?"}
                ],
                "stream": False,
                "think": "high",
            },
        )
        message = body["choices"][0]["message"]
        self.assertEqual(message["content"], HARM_REFUSAL)
        self.assertNotIn("reasoning_content", message)
        self.assertNotIn("malware", json.dumps(message))
        saved = last_completion()
        self.assertEqual(saved["answer"], HARM_REFUSAL)
        self.assertNotIn("malware", json.dumps(saved))
        self.assertEqual(self.forwarded, [])
        queued = Path(self._tmp.name) / "train" / "pending" / "queue.jsonl"
        if queued.exists():
            self.assertNotIn("malware", queued.read_text(encoding="utf-8"))

    def test_harmful_thinking_is_answered_when_the_filter_is_off(self):
        ThinkOllama.replies = [
            {
                "content": "The capital is Paris.",
                "thinking": "Use malware to steal passwords.",
            }
        ]
        port = self._boot()
        body = self._post(
            port,
            {
                "messages": [
                    {"role": "user", "content": "What is the capital of France?"}
                ],
                "stream": False,
                "think": "high",
            },
        )
        message = body["choices"][0]["message"]
        self.assertEqual(message["content"], "The capital is Paris.")
        self.assertIn("malware", message.get("reasoning_content") or "")
        self.assertNotEqual(message["content"], HARM_REFUSAL)
        self.assertEqual(self.forwarded[0]["answer"], "The capital is Paris.")

    def test_streamed_thinking_stops_before_the_harmful_span(self):
        with patch("pair.moderate.safety_filter", return_value=True):
            self._streamed_thinking_is_refused()

    def _streamed_thinking_is_refused(self):
        ThinkOllama.replies = [
            {
                "chunks": [
                    {"thinking": "Blue light scatters more. "},
                    {"thinking": "Use malware to steal passwords."},
                ]
            }
        ]
        port = self._boot()
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/v1/chat/completions",
            body=json.dumps(
                {
                    "messages": [{"role": "user", "content": "Why is the sky blue?"}],
                    "stream": True,
                    "think": "high",
                }
            ).encode(),
            headers={
                "content-type": "application/json",
                "X-Pi-Target": "pi4",
                "X-Pi-Mesh": "off",
            },
        )
        raw = conn.getresponse().read().decode()
        conn.close()
        self.assertIn("Blue light scatters more.", raw)
        self.assertIn("pi_reasoning_clear", raw)
        self.assertIn(HARM_REFUSAL, raw)
        self.assertNotIn("malware", raw.lower())
        self.assertEqual(self.forwarded, [])
        saved = last_completion()
        self.assertEqual(saved["answer"], HARM_REFUSAL)
        self.assertNotIn("malware", json.dumps(saved))

    def test_streamed_thinking_is_answered_when_the_filter_is_off(self):
        ThinkOllama.replies = [
            {
                "chunks": [
                    {"thinking": "Use malware to steal passwords."},
                    {"content": "Shorter wavelengths scatter more."},
                ]
            }
        ]
        port = self._boot()
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/v1/chat/completions",
            body=json.dumps(
                {
                    "messages": [{"role": "user", "content": "Why is the sky blue?"}],
                    "stream": True,
                    "think": "high",
                }
            ).encode(),
            headers={
                "content-type": "application/json",
                "X-Pi-Target": "pi4",
                "X-Pi-Mesh": "off",
            },
        )
        raw = conn.getresponse().read().decode()
        conn.close()
        self.assertIn("malware", raw.lower())
        self.assertIn("Shorter wavelengths scatter more.", raw)
        self.assertNotIn(HARM_REFUSAL, raw)
        self.assertNotIn("pi_reasoning_clear", raw)
        self.assertNotIn("pi_replace", raw)
        self.assertEqual(
            last_completion()["answer"], "Shorter wavelengths scatter more."
        )
        self.assertEqual(
            self.forwarded[0]["answer"], "Shorter wavelengths scatter more."
        )

    def test_cap_forces_one_answer_and_drops_the_follow_up_thinking(self):
        ThinkOllama.replies = [
            {"content": "", "thinking": "a " * 200},
            {"content": "the answer", "thinking": "should not stick"},
        ]
        port = self._boot()
        body = self._post(
            port,
            {
                "messages": [
                    {"role": "user", "content": "Explain why the sky is blue today"}
                ],
                "stream": False,
                "think": "high",
            },
        )
        self.assertEqual(len(ThinkOllama.calls), 2)
        self.assertTrue(ThinkOllama.calls[0]["think"])
        self.assertEqual(
            ThinkOllama.calls[0]["options"]["num_predict"],
            HIGH_PREDICT + HIGH_THINK_BUDGET,
        )
        self.assertFalse(ThinkOllama.calls[1]["think"])
        self.assertEqual(ThinkOllama.calls[1]["options"]["num_predict"], HIGH_PREDICT)
        follow = json.dumps(ThinkOllama.calls[1]["messages"])
        self.assertIn(FORCE_NOTE, follow)
        self.assertIn("Partial reasoning", follow)
        self.assertIn("a a", follow)
        message = body["choices"][0]["message"]
        self.assertEqual(message["content"], "the answer")
        self.assertLessEqual(
            reasoning_tokens(message["reasoning_content"]), HIGH_THINK_BUDGET
        )
        self.assertNotIn("should not stick", json.dumps(body))
        saved = last_completion()
        blob = json.dumps({"saved": saved, "queued": self.forwarded})
        self.assertNotIn(FORCE_NOTE, blob)
        self.assertNotIn("should not stick", blob)
        self.assertEqual(saved["answer"], "the answer")
        queued = Path(self._tmp.name) / "train" / "pending" / "queue.jsonl"
        self.assertTrue(queued.exists())
        text = queued.read_text(encoding="utf-8")
        self.assertIn("the answer", text)
        self.assertNotIn(FORCE_NOTE, text)
        self.assertNotIn("should not stick", text)

    def test_high_reply_is_never_empty(self):
        ThinkOllama.replies = [{"content": "Paris.", "thinking": "short note"}]
        port = self._boot()
        answered = self._post(
            port,
            {
                "messages": [
                    {"role": "user", "content": "What is the capital of France?"}
                ],
                "stream": False,
                "think": "high",
            },
        )
        self.assertEqual(answered["choices"][0]["message"]["content"], "Paris.")
        self.assertEqual(len(ThinkOllama.calls), 1)

        ThinkOllama.calls = []
        ThinkOllama.replies = [
            {"content": "", "thinking": ""},
            {"content": "   ", "thinking": "still nothing"},
        ]
        empty = self._post(
            port,
            {
                "messages": [{"role": "user", "content": "Explain why the tide turns"}],
                "stream": False,
                "think": "high",
            },
        )
        message = empty["choices"][0]["message"]
        self.assertTrue(message["content"].strip())
        self.assertEqual(message["content"], DIRECT_FALLBACK)
        self.assertEqual(len(ThinkOllama.calls), 2)
        self.assertTrue(ThinkOllama.calls[0]["think"])
        self.assertFalse(ThinkOllama.calls[1]["think"])

    def test_high_thinking_stays_on_flash_unless_pro_was_chosen(self):
        ThinkOllama.replies = [{"content": "reversed.", "thinking": "walk the indexes"}]
        port = self._boot()
        hard = "Write a python function that reverses a list."
        auto = self._post(
            port,
            {
                "pi_mode": "auto",
                "think": "high",
                "messages": [{"role": "user", "content": hard}],
                "stream": False,
            },
        )
        self.assertEqual(auto["pi_mode"], "auto")
        self.assertEqual(auto["pi_route"], "flash")
        self.assertEqual(auto["pi_model"], "qwen3:0.6b")
        self.assertEqual(ThinkOllama.calls[0]["model"], "qwen3:0.6b")
        self.assertTrue(auto["choices"][0]["message"]["content"].strip())

        ThinkOllama.calls = []
        ThinkOllama.replies = [
            {"content": "reversed on pro.", "thinking": "walk the indexes"}
        ]
        chosen = self._post(
            port,
            {
                "pi_mode": "pro",
                "think": "high",
                "messages": [{"role": "user", "content": hard}],
                "stream": False,
            },
        )
        self.assertEqual(chosen["pi_mode"], "pro")
        self.assertEqual(chosen["pi_route"], "pro")
        self.assertEqual(chosen["pi_model"], "qwen3:1.7b")
        self.assertEqual(ThinkOllama.calls[0]["model"], "qwen3:1.7b")
        self.assertTrue(chosen["choices"][0]["message"]["content"].strip())
