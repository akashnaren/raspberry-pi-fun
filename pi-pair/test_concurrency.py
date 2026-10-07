"""Generation cap: concurrent misses, immediate 503, cache and page answers stay open."""

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
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair import runtime
from pair import server as pair_server
from pair.config import infer_slots
from pair.errors import BUSY, WAITING
from pair.gate import QUEUE_LIMIT, InferenceGate
from pair.knobs import parallel_limit
from pair.server import make_server


def _start(httpd: ThreadingHTTPServer) -> None:
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()


class HoldOllama(BaseHTTPRequestHandler):
    """Blocks inside /api/chat so tests can see overlapping generations."""

    lock = threading.Lock()
    inside = 0
    peak = 0
    posts = 0
    entered = threading.Event()
    release = threading.Event()

    def log_message(self, *_args):
        return

    def do_GET(self):
        body = json.dumps({"models": [{"name": "qwen3:0.6b"}]}).encode()
        self._send(body)

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        kind = type(self)
        with kind.lock:
            kind.posts += 1
            kind.inside += 1
            kind.peak = max(kind.peak, kind.inside)
            if kind.inside >= 2:
                kind.entered.set()
        kind.release.wait(timeout=8)
        with kind.lock:
            kind.inside -= 1
        self._send(json.dumps({"message": {"content": "held"}}).encode())

    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class GateUnit(unittest.TestCase):
    def test_full_gate_fails_immediately(self):
        gate = InferenceGate(2)
        self.assertTrue(gate.try_acquire())
        self.assertTrue(gate.try_acquire())
        self.assertEqual(gate.in_flight(), 2)
        started = time.perf_counter()
        self.assertFalse(gate.try_acquire())
        self.assertLess(time.perf_counter() - started, 0.2)
        self.assertEqual(gate.queue_limit, QUEUE_LIMIT)
        gate.release()
        self.assertEqual(gate.in_flight(), 1)
        self.assertTrue(gate.try_acquire())

    def test_extra_release_does_not_open_a_slot(self):
        gate = InferenceGate(1)
        gate.release()
        self.assertTrue(gate.try_acquire())
        self.assertFalse(gate.try_acquire())
        gate.release()
        gate.release()
        self.assertTrue(gate.try_acquire())
        self.assertFalse(gate.try_acquire())

    def test_wait_queue_grants_the_next_slot(self):
        gate = InferenceGate(1, queue_limit=1, wait_timeout=2)
        self.assertTrue(gate.try_acquire())
        self.assertEqual(gate.reserve(), "wait")
        self.assertEqual(gate.waiting(), 1)
        self.assertEqual(gate.reserve(), "full")

        def finish() -> None:
            gate.release()

        threading.Timer(0.05, finish).start()
        self.assertTrue(gate.acquire_reserved(1))
        self.assertEqual(gate.in_flight(), 1)
        self.assertEqual(gate.waiting(), 0)
        gate.release()

    def test_wait_queue_times_out(self):
        gate = InferenceGate(1, queue_limit=1, wait_timeout=0.2)
        self.assertTrue(gate.try_acquire())
        self.assertEqual(gate.reserve(), "wait")
        started = time.perf_counter()
        self.assertFalse(gate.acquire_reserved())
        self.assertGreaterEqual(time.perf_counter() - started, 0.15)
        self.assertEqual(gate.waiting(), 0)
        self.assertEqual(gate.in_flight(), 1)
        gate.release()

    def test_default_cap_matches_the_runtime_file(self):
        previous = os.environ.pop("PI_PAIR_SLOTS", None)
        try:
            limit = infer_slots()
            self.assertEqual(limit, 1)
            self.assertEqual(limit, parallel_limit())
            os.environ["PI_PAIR_SLOTS"] = "99"
            self.assertEqual(infer_slots(), 4)
            os.environ["PI_PAIR_SLOTS"] = "0"
            self.assertEqual(infer_slots(), 1)
            os.environ["PI_PAIR_SLOTS"] = "nope"
            self.assertEqual(infer_slots(), parallel_limit())
        finally:
            if previous is None:
                os.environ.pop("PI_PAIR_SLOTS", None)
            else:
                os.environ["PI_PAIR_SLOTS"] = previous

    def test_waiters_keep_fifo_order_and_report_position(self):
        gate = InferenceGate(1, queue_limit=8, wait_timeout=2)
        self.assertTrue(gate.try_acquire())
        status_a, ticket_a = gate.reserve_ticket()
        status_b, ticket_b = gate.reserve_ticket()
        self.assertEqual(status_a, "wait")
        self.assertEqual(status_b, "wait")
        self.assertEqual(gate.position(ticket_a), 2)
        self.assertEqual(gate.position(ticket_b), 3)
        self.assertGreater(gate.eta_s(2), 0)
        ticks: list[tuple[str, int]] = []
        order: list[str] = []

        def run(name: str, ticket) -> None:
            def on_tick(pos: int, _eta: int) -> None:
                ticks.append((name, pos))

            self.assertTrue(gate.wait(ticket, on_tick=on_tick, timeout=2))
            order.append(name)
            gate.release(ticket)

        first = threading.Thread(target=run, args=("a", ticket_a))
        second = threading.Thread(target=run, args=("b", ticket_b))
        first.start()
        second.start()
        time.sleep(0.05)
        gate.release()
        first.join(2)
        second.join(2)
        self.assertEqual(order, ["a", "b"])
        self.assertIn(("a", 2), ticks)
        self.assertIn(("b", 3), ticks)
        self.assertEqual(gate.in_flight(), 0)
        self.assertEqual(gate.waiting(), 0)

    def test_there_is_no_process_wide_inference_semaphore(self):
        self.assertFalse(hasattr(runtime, "_infer_sem"))
        self.assertIsInstance(runtime.gate, InferenceGate)


class ConcurrentChat(unittest.TestCase):
    def setUp(self):
        self._peers = [dict(peer) for peer in runtime.PEERS]
        self._slots = runtime.INFER_SLOTS
        self._gate = runtime.gate
        runtime.reset_health()
        self.servers = []
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["PI_PAIR_DATA"] = self._tmp.name
        os.environ["PI_PAIR_ROLE"] = "brain"
        os.environ["PI_PAIR_REMOTE_SEARCH"] = "0"
        os.environ["PI_PAIR_CANNED"] = str(ROOT / "data" / "canned" / "canned_map.json")
        HoldOllama.inside = 0
        HoldOllama.peak = 0
        HoldOllama.posts = 0
        HoldOllama.entered = threading.Event()
        HoldOllama.release = threading.Event()
        self._lookup = pair_server.lookup_web
        pair_server.lookup_web = lambda query, opener=None: {
            "status": "failed",
            "sources": [],
            "context": "",
        }

    def tearDown(self):
        HoldOllama.release.set()
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        pair_server.lookup_web = self._lookup
        runtime.PEERS = self._peers
        runtime.INFER_SLOTS = self._slots
        runtime.gate = self._gate
        runtime.reset_health()
        os.environ.pop("PI_PAIR_DATA", None)
        os.environ.pop("PI_PAIR_ROLE", None)
        os.environ.pop("PI_PAIR_REMOTE_SEARCH", None)
        os.environ.pop("PI_PAIR_CANNED", None)
        self._tmp.cleanup()

    def _listen(self, handler):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.servers.append(httpd)
        _start(httpd)
        return httpd.server_address[1]

    def _pair(self) -> int:
        httpd = make_server("127.0.0.1", 0)
        self.servers.append(httpd)
        _start(httpd)
        return httpd.server_address[1]

    def _pi4(self, handler=HoldOllama) -> int:
        peer_port = self._listen(handler)
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

    def _post(self, port, content, headers=None, stream=False, timeout=5):
        payload = {
            "model": "qwen3:0.6b",
            "messages": [{"role": "user", "content": content}],
            "stream": stream,
        }
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode()
                body = (
                    json.loads(raw or "{}")
                    if "json" in (response.headers.get("content-type") or "")
                    else raw
                )
                return response.status, response.headers, body
        except urllib.error.HTTPError as error:
            raw = error.read().decode()
            try:
                body = json.loads(raw or "{}")
            except json.JSONDecodeError:
                body = {"error": raw}
            return error.code, error.headers, body

    def test_two_misses_overlap_and_the_third_is_503(self):
        runtime.set_infer_slots(2)
        port = self._pi4()
        results = [None, None]

        def run(index: int) -> None:
            results[index] = self._post(
                port,
                f"novel overlap {index}",
                {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
                timeout=10,
            )

        threads = [threading.Thread(target=run, args=(index,)) for index in (0, 1)]
        for thread in threads:
            thread.start()
        self.assertTrue(HoldOllama.entered.wait(timeout=5))
        self.assertGreaterEqual(HoldOllama.peak, 2)
        self.assertEqual(runtime.gate.in_flight(), 2)
        runtime.gate.queue_limit = 0
        started = time.perf_counter()
        status, headers, body = self._post(
            port,
            "novel overlap rejected",
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
        )
        elapsed = time.perf_counter() - started
        self.assertEqual(status, 503)
        self.assertLess(elapsed, 0.5)
        self.assertEqual(body["error"], BUSY)
        self.assertNotIn("pi4", body["error"].lower())
        self.assertNotIn("generations", body["error"].lower())
        self.assertIn("application/json", headers.get("content-type", ""))
        runtime.reset_health()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=5
        ) as response:
            health = json.loads(response.read().decode())
        self.assertEqual(health["slots"], 2)
        self.assertEqual(health["in_flight"], 2)
        HoldOllama.release.set()
        for thread in threads:
            thread.join(timeout=5)
        self.assertTrue(all(item is not None and item[0] == 200 for item in results))
        self.assertEqual(runtime.gate.in_flight(), 0)

    def test_stream_over_cap_is_json_503(self):
        runtime.set_infer_slots(1)
        self.assertTrue(runtime.gate.try_acquire())
        runtime.gate.queue_limit = 0
        port = self._pi4()
        started = time.perf_counter()
        status, headers, body = self._post(
            port,
            "novel stream rejected",
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
            stream=True,
        )
        self.assertLess(time.perf_counter() - started, 0.5)
        self.assertEqual(status, 503)
        self.assertNotIn("event-stream", headers.get("content-type", ""))
        self.assertGreaterEqual(int(headers["Retry-After"]), 1)
        self.assertEqual(body["error"], BUSY)
        self.assertNotIn("generations", body["error"].lower())
        self.assertEqual(HoldOllama.posts, 0)
        runtime.gate.release()

    def test_stream_waits_then_answers(self):
        runtime.set_infer_slots(1)
        self.assertTrue(runtime.gate.try_acquire())
        runtime.gate.wait_timeout = 5
        port = self._pi4()
        HoldOllama.release.set()
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        holder: dict = {}
        got = threading.Event()
        done = threading.Event()

        def reader() -> None:
            try:
                payload = json.dumps(
                    {
                        "model": "qwen3:0.6b",
                        "messages": [{"role": "user", "content": "novel stream waits"}],
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
                while b"Waiting for a free slot" not in data:
                    piece = response.fp.read1(256)
                    if not piece:
                        break
                    data += piece
                holder["early"] = data
                holder["status"] = response.status
                got.set()
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
        self.assertEqual(holder.get("status"), 200)
        self.assertIn(b"Waiting for a free slot", holder["early"])
        self.assertNotIn(b"generations in flight", holder["early"])
        self.assertEqual(HoldOllama.posts, 0)
        runtime.gate.release()
        self.assertTrue(done.wait(4), holder)
        self.assertNotIn("error", holder, holder)
        self.assertIn(b"held", holder["all"])
        self.assertLess(
            holder["all"].index(b"Waiting for a free slot"),
            holder["all"].index(b"held"),
        )

    def test_wait_timeout_is_a_friendly_503(self):
        runtime.set_infer_slots(1)
        self.assertTrue(runtime.gate.try_acquire())
        runtime.gate.wait_timeout = 0.3
        port = self._pi4()
        started = time.perf_counter()
        status, _headers, body = self._post(
            port,
            "novel wait times out",
            {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
            timeout=3,
        )
        elapsed = time.perf_counter() - started
        self.assertGreaterEqual(elapsed, 0.2)
        self.assertLess(elapsed, 2)
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], BUSY)
        self.assertNotIn("pi4", body["error"].lower())
        self.assertEqual(HoldOllama.posts, 0)
        runtime.gate.release()

    def test_a_greeting_does_not_skip_a_full_gate(self):
        runtime.set_infer_slots(1)
        runtime.gate.queue_limit = 0
        self.assertTrue(runtime.gate.try_acquire())
        port = self._pi4()
        try:
            started = time.perf_counter()
            status, _headers, body = self._post(
                port,
                "Hi!",
                {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
            )
            self.assertLess(time.perf_counter() - started, 0.5)
            self.assertEqual(status, 503)
            self.assertGreaterEqual(int(_headers["Retry-After"]), 1)
            self.assertEqual(body["error"], BUSY)
            self.assertNotIn("Hi. What can I help you with?", json.dumps(body))
            self.assertEqual(HoldOllama.posts, 0)
        finally:
            runtime.gate.release()

    def test_a_stored_map_does_not_skip_a_full_gate(self):
        runtime.set_infer_slots(1)
        runtime.gate.queue_limit = 0
        self.assertTrue(runtime.gate.try_acquire())
        port = self._pi4()
        try:
            started = time.perf_counter()
            status, _headers, body = self._post(
                port,
                "a paraphrase the exact map does not contain",
                {"X-Pi-Target": "auto", "X-Pi-Mesh": "on"},
            )
            self.assertLess(time.perf_counter() - started, 0.5)
            self.assertEqual(status, 503)
            self.assertEqual(body["error"], BUSY)
            self.assertNotIn("stored sentence", json.dumps(body))
            self.assertEqual(HoldOllama.posts, 0)
        finally:
            runtime.gate.release()

    def test_weak_boards_still_do_not_generate(self):
        runtime.set_infer_slots(2)
        peer_port = self._listen(HoldOllama)
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
                "Hi!",
                {"X-Pi-Target": name, "X-Pi-Mesh": "on"},
            )
            self.assertEqual(status, 502)
            self.assertEqual(body["error"], "That machine cannot answer chats.")
            self.assertNotIn(name, body["error"])
        self.assertEqual(HoldOllama.posts, 0)

    def test_same_request_id_cancels_the_first(self):
        runtime.set_infer_slots(1)

        class SeqHold(HoldOllama):
            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                self.rfile.read(length)
                with type(self).lock:
                    type(self).posts += 1
                    number = type(self).posts
                if number == 1:
                    type(self).release.wait(timeout=8)
                self._send(json.dumps({"message": {"content": "held"}}).encode())

        SeqHold.posts = 0
        SeqHold.lock = threading.Lock()
        SeqHold.release = threading.Event()
        port = self._pi4(SeqHold)
        headers = {
            "X-Pi-Target": "pi4",
            "X-Pi-Mesh": "off",
            "X-Pi-Request-Id": "same-turn",
        }
        results: list = [None]
        peak = {"n": 0}
        stop = threading.Event()

        def watch() -> None:
            while not stop.wait(0.02):
                peak["n"] = max(peak["n"], runtime.gate.in_flight())

        def first() -> None:
            try:
                results[0] = self._post(port, "novel same id one", headers, timeout=5)
            except Exception as exc:
                results[0] = exc

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        threading.Thread(target=first, daemon=True).start()
        deadline = time.monotonic() + 3
        while SeqHold.posts < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertGreaterEqual(SeqHold.posts, 1)
        status, _headers, body = self._post(
            port, "novel same id two", headers, timeout=5
        )
        stop.set()
        SeqHold.release.set()
        self.assertEqual(status, 200, body)
        self.assertIn("held", json.dumps(body))
        self.assertLessEqual(peak["n"], 1)

    def test_page_shows_the_capacity_sentence(self):
        source = (ROOT / "web" / "src" / "main.ts").read_text(encoding="utf-8")
        errors = (ROOT / "web" / "src" / "errors.ts").read_text(encoding="utf-8")
        self.assertIn("WAITING_LINE", source)
        self.assertIn(WAITING, errors)
        self.assertNotIn("pi4 is at capacity", source)
        bundle = (ROOT / "static" / "mesh.js").read_text(encoding="utf-8")
        self.assertIn("Waiting for a free slot", bundle)
        self.assertNotIn("pi4 is at capacity", bundle)
        self.assertNotIn("(2 generations in flight)", bundle)


if __name__ == "__main__":
    unittest.main()
