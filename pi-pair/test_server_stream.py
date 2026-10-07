"""A soft-refusal opener streams immediately. The model is called once."""

from __future__ import annotations

import json
import unittest
from http.client import HTTPConnection


def _content_deltas(raw: str) -> list[str]:
    found = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        piece = ((payload.get("choices") or [{}])[0].get("delta") or {}).get(
            "content"
        ) or ""
        if piece:
            found.append(piece)
    return found


class SoftRefusalStream(unittest.TestCase):
    def setUp(self):
        from test_turn import TurnHttp

        self.http = TurnHttp()
        self.http.setUp()

    def tearDown(self):
        self.http.tearDown()

    def test_a_soft_refusal_opener_streams_immediately(self):
        from pair.core import runtime
        from test_turn import ScriptOllama

        opener = "I'm sorry, "
        rest = "but I can't assist with that."
        ScriptOllama.replies = [
            {"chunks": [opener, rest], "done_reason": "stop"},
        ]
        ScriptOllama.seen = []
        ScriptOllama.posts = 0
        peer_port = self.http._listen(ScriptOllama)
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
        port = self.http._pair()
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/v1/chat/completions",
            body=json.dumps(
                {
                    "messages": [{"role": "user", "content": "Top 5 cars"}],
                    "stream": True,
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
        pieces = _content_deltas(raw)
        self.assertEqual(ScriptOllama.posts, 1)
        self.assertEqual(len(ScriptOllama.seen), 1)
        self.assertEqual(pieces[0], opener)
        self.assertEqual("".join(pieces), opener + rest)
        blob = json.dumps(ScriptOllama.seen)
        self.assertNotIn("Answer helpfully if the request is safe.", blob)

    def test_a_length_stop_replaces_a_dangling_marker(self):
        from pair.core import runtime
        from test_turn import ScriptOllama

        ScriptOllama.replies = [
            {
                "chunks": ["- **Value 1:** 1\n- **Value 2"],
                "done_reason": "length",
            }
        ]
        ScriptOllama.seen = []
        ScriptOllama.posts = 0
        peer_port = self.http._listen(ScriptOllama)
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
        port = self.http._pair()
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/v1/chat/completions",
            body=json.dumps(
                {
                    "messages": [{"role": "user", "content": "Top 5 cars"}],
                    "stream": True,
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
        frames = []
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            frames.append(json.loads(data))
        replaced = [frame for frame in frames if frame.get("pi_replace")]
        self.assertTrue(replaced)
        text = ((replaced[-1].get("choices") or [{}])[0].get("delta") or {}).get(
            "content"
        ) or ""
        self.assertEqual(text.count("**") % 2, 0)
        self.assertIn("Value 2", text)
        self.assertIn("…", text)
        self.assertNotIn("**Value 2", text)
