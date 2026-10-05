"""Canned hits are exact or normalized strings. No embed model is required."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pair.canned import lookup, normalize_key
from pair.config import ollama_base
from pair.public_api import openapi_document
from pair.server import health_document

ROOT = Path(__file__).resolve().parent


class CannedExact(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = {
            key: os.environ.get(key)
            for key in (
                "PI_PAIR_CANNED",
                "PI_PAIR_ROLE",
                "PI_PAIR_NAME",
                "PI_PAIR_OLLAMA",
            )
        }
        self.path = Path(self._tmp.name) / "canned_map.json"
        self.path.write_text(
            json.dumps({"who is the brain": "pi4 is the brain.", "Hello.": "Hi."}),
            encoding="utf-8",
        )
        os.environ["PI_PAIR_CANNED"] = str(self.path)

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def test_exact_and_normalized_keys_hit(self):
        self.assertEqual(lookup("who is the brain"), "pi4 is the brain.")
        self.assertEqual(lookup("  Who is the brain? "), "pi4 is the brain.")
        self.assertEqual(lookup("HELLO!"), "Hi.")
        self.assertEqual(normalize_key("Hello."), "hello")

    def test_a_paraphrase_misses_without_calling_the_network(self):
        for role in ("brain", "health", "dataset"):
            os.environ["PI_PAIR_ROLE"] = role
            with mock.patch(
                "urllib.request.urlopen", side_effect=AssertionError("network")
            ):
                self.assertIsNone(
                    lookup("which board runs the brain"),
                    role,
                )

    def test_health_does_not_report_embed_status(self):
        with mock.patch("pair.server.snapshot_peers", return_value=[]):
            body = health_document()
        self.assertNotIn("warm", body)
        health = openapi_document()["components"]["schemas"]["Health"]["properties"]
        self.assertNotIn("warm", health)

    def test_installer_removes_the_embed_tag_and_docs_do_not_score_it(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn('REMOVED_EMBED="snowflake-arctic-embed:m"', script)
        self.assertIn('ollama rm "$REMOVED_EMBED"', script)
        self.assertNotIn('ollama pull "$REMOVED_EMBED"', script)
        self.assertIn("removes `snowflake-arctic-embed:m`", readme)
        self.assertNotIn("ollama pull snowflake-arctic-embed", readme)
        self.assertNotIn("/api/embed", readme)
        self.assertNotIn("cosine", readme.lower())
        for path in (ROOT / "pair").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("pair.embed", source, path.name)
            self.assertNotIn("snowflake", source, path.name)
            self.assertNotIn("/api/embed", source, path.name)

    def test_ollama_base_ignores_a_missing_embed_path(self):
        os.environ["PI_PAIR_OLLAMA"] = "http://127.0.0.1:11434"
        self.assertEqual(ollama_base(), "http://127.0.0.1:11434")
        os.environ["PI_PAIR_OLLAMA"] = "http://0.0.0.0:11434"
        self.assertEqual(ollama_base(), "http://127.0.0.1:11434")


if __name__ == "__main__":
    unittest.main()
