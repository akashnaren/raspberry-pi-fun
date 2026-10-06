"""Diagrams, document excerpts, and the Pro preload."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.docfit import DOC_FIT_CHARS, excerpt_limit, fit_document, fit_outbound  # noqa: E402
from pair.turn import is_structured_request, structure_hint  # noqa: E402
from pair.preload import (  # noqa: E402
    PRELOAD_TIMEOUT_S,
    pro_preload_payload,
    rewarm_pro_if_evicted,
)


class Diagrams(unittest.TestCase):
    def test_only_a_diagram_skips_search_and_gets_a_hint(self):
        self.assertFalse(is_structured_request("graph the temperature this week"))
        self.assertIsNone(structure_hint("graph the temperature this week"))
        self.assertFalse(is_structured_request("make a table of name and year"))
        self.assertIsNone(structure_hint("make a table of name and year"))
        self.assertFalse(is_structured_request("Plot me a parabolic curve"))
        self.assertTrue(is_structured_request("draw a flowchart of the login steps"))
        hint = structure_hint("draw a flowchart of the login steps")
        self.assertIn("mermaid", hint)
        self.assertNotIn("```chart", hint)
        self.assertNotIn("```table", hint)
        self.assertFalse(is_structured_request("where is the hall bench"))


class Documents(unittest.TestCase):
    def test_excerpt_follows_the_question(self):
        filler = "padding " * 400
        body = filler + " The catalyst heats the chamber to 400 degrees. " + filler
        fitted = fit_document(body, "What temperature does the catalyst reach?")
        self.assertLessEqual(len(fitted), DOC_FIT_CHARS)
        self.assertIn("400 degrees", fitted)
        self.assertNotIn(body, fitted)
        outbound = fit_outbound(
            [
                {
                    "role": "user",
                    "content": "What temperature does the catalyst reach?\n\n---\n"
                    + body,
                }
            ]
        )
        self.assertEqual(outbound[0]["role"], "system")
        self.assertIn("400 degrees", outbound[1]["content"])
        self.assertNotIn(body, outbound[1]["content"])

    def test_excerpt_budget_follows_num_ctx(self):
        question = "What temperature does the catalyst reach?"
        wide = excerpt_limit(2048, question, 256)
        tight = excerpt_limit(512, question, 256)
        self.assertEqual(DOC_FIT_CHARS, excerpt_limit(2048))
        self.assertLessEqual(wide, 4096)
        self.assertLess(tight, wide)
        self.assertLess(excerpt_limit(2048, "note " * 900, 768), wide)
        filler = "padding " * 800
        body = filler + " The catalyst heats the chamber to 400 degrees. " + filler
        fitted = fit_document(body, question, wide)
        self.assertLessEqual(len(fitted), wide)
        self.assertIn("400 degrees", fitted)
        small = fit_document(body, question, tight)
        self.assertLessEqual(len(small), tight)
        self.assertLess(len(small), len(fitted))
        self.assertIn("400 degrees", small)
        outbound = fit_outbound(
            [{"role": "user", "content": question + "\n\n---\n" + body}],
            num_ctx=512,
            reply_tokens=256,
        )
        sent = outbound[1]["content"]
        _question, excerpt = sent.split("\n---\n", 1)
        self.assertLessEqual(len(excerpt.strip()), tight)
        self.assertIn("400 degrees", excerpt)
        self.assertNotIn(body, sent)


class Preload(unittest.TestCase):
    def test_pro_payload_keeps_the_model_resident(self):
        payload = pro_preload_payload()
        self.assertEqual(payload["model"], "qwen3:1.7b")
        self.assertEqual(payload["keep_alive"], -1)
        self.assertNotEqual(payload["keep_alive"], 0)
        self.assertEqual(payload["options"]["num_predict"], 1)
        self.assertEqual(payload["options"]["num_ctx"], 2048)
        self.assertEqual(payload["options"]["num_batch"], 128)
        self.assertEqual(payload["options"]["num_thread"], 4)
        flash = pro_preload_payload("qwen3:0.6b")
        self.assertEqual(flash["options"]["num_ctx"], 2048)
        self.assertEqual(flash["options"]["num_batch"], 128)
        self.assertEqual(flash["options"]["num_thread"], 4)
        self.assertGreaterEqual(PRELOAD_TIMEOUT_S, 180)
        self.assertFalse(payload["think"])
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('"keep_alive":-1', script)
        self.assertIn('"think": False', script)
        self.assertIn('ollama show "$OLLAMA_PRO_MODEL"', script)
        executed = [
            line.strip()
            for line in script.splitlines()
            if "ollama pull" in line and not line.strip().startswith("echo")
        ]
        self.assertTrue(all("1.5b" not in line for line in executed))

    def test_an_evicted_tag_is_warmed_again(self):
        seen: list[dict] = []

        class _Resp:
            def read(self) -> bytes:
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(request, timeout):
            del timeout
            seen.append(json.loads(request.data.decode()))
            return _Resp()

        env = {"PI_PAIR_ROLE": "brain"}
        with patch.dict(os.environ, env, clear=False):
            with patch("pair.preload.resident_models", return_value=[]):
                with patch("pair.preload.open_json_request", opener):
                    rewarm_pro_if_evicted()
        models = [item["model"] for item in seen]
        self.assertEqual(models, ["qwen3:0.6b", "qwen3:1.7b"])
        self.assertTrue(all(item["keep_alive"] == -1 for item in seen))
        seen.clear()
        with patch.dict(os.environ, env, clear=False):
            with patch("pair.preload.resident_models", return_value=["qwen3:0.6b"]):
                with patch("pair.preload.open_json_request", opener):
                    rewarm_pro_if_evicted()
        self.assertEqual([item["model"] for item in seen], ["qwen3:1.7b"])
        self.assertEqual(seen[0]["keep_alive"], -1)


class WebPolish(unittest.TestCase):
    def test_streaming_math_diagrams_and_presence(self):
        completed = subprocess.run(
            [
                "node",
                "--experimental-strip-types",
                str(ROOT / "web" / "polish.test.mjs"),
            ],
            cwd=ROOT / "web",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode, 0, completed.stdout + "\n" + completed.stderr
        )
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
