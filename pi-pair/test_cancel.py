"""Disconnects and retries drop a generation instead of running it to the end."""

from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

from pair.cancel import Cancel, ClientGone, peer_closed
from pair.chat import chat_ollama
from pair.gate import InferenceGate


class _SlowBody:
    def __init__(self) -> None:
        self._lines = 0
        self._stop = threading.Event()

    def readline(self, amt: int = -1) -> bytes:
        del amt
        if self._stop.is_set():
            return b""
        time.sleep(0.05)
        if self._stop.is_set():
            return b""
        self._lines += 1
        if self._lines > 40:
            return b""
        return json.dumps({"message": {"content": "x"}, "done": False}).encode() + b"\n"

    def abort(self) -> None:
        self._stop.set()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class CancelToken(unittest.TestCase):
    def test_peer_closed_sees_a_hung_up_socket(self):
        left, right = socket.socketpair()
        try:
            self.assertFalse(peer_closed(left))
            right.close()
            self.assertTrue(peer_closed(left))
        finally:
            left.close()

    def test_ndjson_stops_when_cancel_is_set(self):
        gate = InferenceGate(1)
        self.assertTrue(gate.try_acquire())
        cancel = Cancel()
        body = _SlowBody()
        peer = {
            "name": "pi4",
            "host": "127.0.0.1",
            "port": 9,
            "kind": "ollama",
            "generative": True,
            "role": "brain",
        }

        def open_json(url, payload, timeout, cancel=None):
            del url, payload, timeout
            if cancel is not None:
                cancel.attach(body.abort)
            return body

        def run() -> None:
            try:
                chat_ollama(
                    peer,
                    "qwen3:0.6b",
                    [{"role": "user", "content": "hi"}],
                    cancel=cancel,
                )
            finally:
                gate.release()

        worker = threading.Thread(target=run)
        with patch("pair.stream.open_json", open_json):
            worker.start()
            time.sleep(0.06)
            started = time.monotonic()
            cancel.set()
            worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertEqual(gate.in_flight(), 0)

    def test_a_cancelled_ticket_never_takes_a_slot(self):
        gate = InferenceGate(1, queue_limit=4, wait_timeout=2)
        self.assertTrue(gate.try_acquire())
        status, ticket = gate.reserve_ticket()
        self.assertEqual(status, "wait")
        cancel = Cancel()
        outcome: list[str] = []

        def run() -> None:
            try:
                gate.wait(ticket, cancel=cancel, timeout=2)
                outcome.append("acquired")
            except ClientGone:
                outcome.append("gone")

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.05)
        cancel.set()
        worker.join(1)
        self.assertEqual(outcome, ["gone"])
        self.assertEqual(gate.waiting(), 0)
        gate.release()
        time.sleep(0.05)
        self.assertEqual(gate.in_flight(), 0)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
