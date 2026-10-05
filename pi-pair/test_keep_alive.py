"""pi4 keep_alive stays -1 so chat, stream, and embed do not shrink model TTL."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.chat import chat_ollama
from pair.embed import embed_texts
from pair.knobs import inference_knobs, keep_alive
from pair.stream import stream_ollama


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
            (ROOT / "configs" / "runtime" / "inference_pi4.json").read_text(encoding="utf-8")
        )
        self.assertEqual(shipped["keep_alive"], -1)

    def test_chat_stream_and_embed_send_minus_one(self):
        seen = []

        def urlopen(request, timeout=None):
            payload = json.loads(request.data.decode())
            seen.append(payload)
            if payload.get("stream"):
                return _Body({"message": {"content": "hi"}, "done": True})
            if "input" in payload:
                return _Body({"embeddings": [[0.1, 0.2]]})
            return _Body({"message": {"content": "hi"}})

        peer = {
            "name": "pi4",
            "host": "127.0.0.1",
            "port": 9,
            "generative": True,
            "role": "brain",
        }
        with (
            patch("pair.chat.urllib.request.urlopen", urlopen),
            patch("pair.stream.urllib.request.urlopen", urlopen),
            patch("pair.embed.urllib.request.urlopen", urlopen),
        ):
            text, model = chat_ollama(
                peer, "qwen2.5:0.5b", [{"role": "user", "content": "hi"}]
            )
            chunks = list(
                stream_ollama(peer, "qwen2.5:0.5b", [{"role": "user", "content": "hi"}])
            )
            vectors = embed_texts(["hi"])

        self.assertEqual(text, "hi")
        self.assertEqual(model, "qwen2.5:0.5b")
        self.assertEqual(chunks, ["hi"])
        self.assertEqual(vectors, [[0.1, 0.2]])
        self.assertEqual(len(seen), 3)
        for payload in seen:
            self.assertEqual(payload["keep_alive"], -1)
            self.assertIsInstance(payload["keep_alive"], int)
            self.assertIn('"keep_alive": -1', json.dumps(payload))

    def test_install_keeps_two_models_resident(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        unit = (ROOT / "configs" / "runtime" / "ollama-lan.service").read_text(encoding="utf-8")
        for text in (script, unit):
            self.assertIn("OLLAMA_MAX_LOADED_MODELS=3", text)
            self.assertIn("OLLAMA_KEEP_ALIVE=-1", text)
            self.assertNotIn("OLLAMA_MAX_LOADED_MODELS=1", text)
        self.assertIn("ollama-lan.service", script)
        self.assertIn('Environment="OLLAMA_NUM_PARALLEL=${OLLAMA_NUM_PARALLEL}"', script)
        self.assertIn("Environment=OLLAMA_NUM_PARALLEL=${OLLAMA_NUM_PARALLEL}", script)
        self.assertNotIn("OLLAMA_NUM_PARALLEL=1", script)
        self.assertIn("Environment=OLLAMA_NUM_PARALLEL=4", unit)
        self.assertNotIn("OLLAMA_NUM_PARALLEL=1", unit)
        readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("OLLAMA_MAX_LOADED_MODELS=3", readme)
        self.assertIn("OLLAMA_KEEP_ALIVE=-1", readme)
        self.assertIn("ollama-lan", readme)
        for path in (ROOT / "pair").glob("*.py"):
            self.assertNotIn('"5m"', path.read_text(encoding="utf-8"), path.name)
        self.assertNotIn(
            '"5m"',
            (ROOT / "configs" / "runtime" / "inference_pi4.json").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
