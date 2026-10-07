"""pi4 keep_alive stays -1 so chat and stream do not shrink model TTL."""

from __future__ import annotations


from tests.support.paths import ROOT
import json
import os
import unittest
from unittest.mock import patch


from pair.model.chat_once import chat_ollama
from pair.model.knobs import inference_knobs, keep_alive
from pair.model.chat_stream import stream_ollama


class _Body:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode()
        self._pending = self._raw + b"\n"

    def read(self) -> bytes:
        return self._raw

    def readline(self) -> bytes:
        line, self._pending = self._pending, b""
        return line

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class KeepAliveDefault(unittest.TestCase):
    def setUp(self):
        self._role = os.environ.get("PI_PAIR_ROLE")
        os.environ["PI_PAIR_ROLE"] = "brain"

    def tearDown(self):
        if self._role is None:
            os.environ.pop("PI_PAIR_ROLE", None)
        else:
            os.environ["PI_PAIR_ROLE"] = self._role

    def test_pi4_knob_defaults_to_forever(self):
        knobs = inference_knobs()
        self.assertEqual(knobs["role"], "pi4")
        self.assertEqual(knobs["keep_alive"], -1)
        self.assertIsInstance(knobs["keep_alive"], int)
        self.assertEqual(keep_alive(), -1)
        self.assertEqual(keep_alive({}), -1)
        self.assertEqual(keep_alive({"keep_alive": None}), -1)
        self.assertEqual(keep_alive({"keep_alive": ""}), -1)
        self.assertEqual(keep_alive({"keep_alive": 0}), 0)
        shipped = json.loads(
            (ROOT / "configs" / "runtime" / "inference_pi4.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(shipped["keep_alive"], -1)
        self.assertEqual(shipped["ollama_num_parallel"], 1)
        self.assertEqual(shipped["ollama_max_queue"], 8)

    def test_chat_and_stream_send_minus_one(self):
        seen = []

        def urlopen(request, timeout=None):
            payload = json.loads(request.data.decode())
            seen.append(payload)
            if payload.get("stream"):
                return _Body({"message": {"content": "hi"}, "done": True})
            return _Body({"message": {"content": "hi"}})

        peer = {
            "name": "pi4",
            "host": "127.0.0.1",
            "port": 9,
            "generative": True,
            "role": "brain",
        }
        with patch("pair.model.chat_once.urllib.request.urlopen", urlopen):
            text, model = chat_ollama(
                peer, "qwen3:0.6b", [{"role": "user", "content": "hi"}]
            )
            chunks = list(
                stream_ollama(peer, "qwen3:0.6b", [{"role": "user", "content": "hi"}])
            )

        self.assertEqual(text, "hi")
        self.assertEqual(model, "qwen3:0.6b")
        self.assertEqual(chunks, ["hi"])
        self.assertEqual(len(seen), 2)
        for payload in seen:
            self.assertEqual(payload["keep_alive"], -1)
            self.assertIsInstance(payload["keep_alive"], int)
            self.assertIn('"keep_alive": -1', json.dumps(payload))

    def test_install_keeps_two_models_resident(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        unit = (ROOT / "configs" / "runtime" / "ollama-lan.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("OLLAMA_MAX_LOADED_MODELS=2", script)
        self.assertIn("OLLAMA_KEEP_ALIVE=-1", script)
        self.assertIn("OLLAMA_MAX_LOADED_MODELS=2", unit)
        self.assertIn("OLLAMA_KEEP_ALIVE=-1", unit)
        self.assertNotIn("OLLAMA_MAX_LOADED_MODELS=1", unit)
        # pi4 stays at two resident chat tags. The =1 cap is the pi3 embed drop-in.
        self.assertEqual(script.count("OLLAMA_MAX_LOADED_MODELS=1"), 1)
        self.assertLess(
            script.index("pi3-embed.conf"),
            script.index("OLLAMA_MAX_LOADED_MODELS=1"),
        )
        self.assertIn("ollama-lan.service", script)
        self.assertIn(
            'Environment="OLLAMA_NUM_PARALLEL=${OLLAMA_NUM_PARALLEL}"', script
        )
        self.assertIn("Environment=OLLAMA_NUM_PARALLEL=${OLLAMA_NUM_PARALLEL}", script)
        self.assertIn("Environment=OLLAMA_NUM_PARALLEL=1", unit)
        self.assertNotIn("OLLAMA_NUM_PARALLEL=2", unit)
        for path in (ROOT / "pair").rglob("*.py"):
            self.assertNotIn('"5m"', path.read_text(encoding="utf-8"), path.name)
        self.assertNotIn(
            '"5m"',
            (ROOT / "configs" / "runtime" / "inference_pi4.json").read_text(
                encoding="utf-8"
            ),
        )


class PoolClose(unittest.TestCase):
    def test_close_does_not_drain_an_unfinished_body(self):
        from pair.model.http_pool import _Body

        class _Response:
            def __init__(self):
                self.reads = 0
                self.status = 200
                self.headers = {}
                self.will_close = False
                self._closed = False

            def read(self, amt=-1):
                self.reads += 1
                return b"still here"

            def readline(self, amt=-1):
                self.reads += 1
                return b"still here\n"

            def isclosed(self):
                return self._closed

            def close(self):
                self._closed = True

        class _Sock:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

            def shutdown(self, how):
                self.closed = True

        class _Conn:
            def __init__(self):
                self.sock = _Sock()

        response = _Response()
        conn = _Conn()
        reused = []
        body = _Body(response, reused.append, connection=conn)
        body.close()
        self.assertEqual(response.reads, 0)
        self.assertEqual(reused, [False])
        self.assertTrue(conn.sock.closed)


if __name__ == "__main__":
    unittest.main()
