"""Arctic embed paraphrases hit the canned map. Ollama /api/embed is mocked."""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair import runtime
from pair.canned import (
    lookup,
    reset_warm_state,
    set_warm_status,
    start_canned_warm,
    warm_at_start,
    warm_status,
)
from pair.embed import (
    COSINE_MIN,
    EMBED_MODEL,
    cosine,
    embed_url,
    ollama_base,
    on_pi4,
    reset_embed_cache,
    warm_canned_embeddings,
)


def _toward_who(score: float) -> list[float]:
    """Unit vector whose cosine with [1, 0, 0] is `score`."""
    rest = math.sqrt(max(0.0, 1.0 - score * score))
    return [score, rest, 0.0]


WHO = [1.0, 0.0, 0.0]
STATUS = [0.0, 0.0, 1.0]
HI = [0.0, -1.0, 0.0]
UNRELATED_VEC = [0.0, 1.0, 0.0]

PARAPHRASE = "which board may generate replies"
JUST = "what machine is allowed to produce answers"
BELOW = "how does the fleet stay warm"
UNRELATED = "recipe for sourdough bread"
STATUS_PARAPHRASE = "how is the mesh doing"
CLOSER = "what generates on this fleet"

ANSWERS = {
    "hi": "Hi. What can I help you with?",
    "status": "Mesh nominal.",
    "who generates": "Only pi4 generates tokens for the user.",
    "brain board": "pi4 is the sole generative brain.",
}
DEFAULT_MAP = {key: ANSWERS[key] for key in ("hi", "status", "who generates")}

VECTORS = {
    "who generates": WHO,
    "status": STATUS,
    "hi": HI,
    "brain board": _toward_who(0.86),
    PARAPHRASE: _toward_who(0.90),
    JUST: _toward_who(0.851),
    BELOW: _toward_who(0.80),
    UNRELATED: UNRELATED_VEC,
    STATUS_PARAPHRASE: [0.0, math.sqrt(1.0 - 0.90 * 0.90), 0.90],
    CLOSER: _toward_who(0.99),
}


class EmbedFake(BaseHTTPRequestHandler):
    hits: list = []
    mode = "ok"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length).decode() or "{}")
        path = self.path.split("?")[0]
        type(self).hits.append((path, payload))
        mode = type(self).mode
        if mode == "http":
            body = b'{"error":"embed down"}'
            self._send(500, body)
            return
        if mode == "garbage":
            self._send(200, b"not-json")
            return
        if path != "/api/embed":
            self._send(404, b'{"error":"wrong path"}')
            return
        texts = payload.get("input")
        if isinstance(texts, str):
            texts = [texts]
        if not isinstance(texts, list):
            texts = []
        if mode == "short":
            vectors = [WHO]
        else:
            vectors = [VECTORS.get(text, UNRELATED_VEC) for text in texts]
        self._send(200, json.dumps({"embeddings": vectors}).encode())

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SemanticMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), EmbedFake)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self._env = {
            key: os.environ.get(key)
            for key in (
                "PI_PAIR_ROLE",
                "PI_PAIR_NAME",
                "PI_PAIR_CANNED",
                "PI_PAIR_OLLAMA",
                "OLLAMA_HOST",
                "PI_PAIR_HOST",
                "PI_PAIR_PORT",
                "PI_PAIR_PEERS",
                "PI_PAIR_DATA",
            )
        }
        self.tmp = tempfile.TemporaryDirectory()
        self.map_path = Path(self.tmp.name) / "canned_map.json"
        EmbedFake.hits = []
        EmbedFake.mode = "ok"
        reset_embed_cache()
        reset_warm_state()
        os.environ["PI_PAIR_ROLE"] = "brain"
        os.environ["PI_PAIR_OLLAMA"] = f"http://127.0.0.1:{self.port}"
        os.environ["PI_PAIR_CANNED"] = str(self.map_path)
        os.environ.pop("PI_PAIR_NAME", None)
        os.environ.pop("OLLAMA_HOST", None)
        self._write(DEFAULT_MAP)

    def tearDown(self):
        self.tmp.cleanup()
        reset_embed_cache()
        reset_warm_state()
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        runtime.configure()

    def _write(self, table: dict[str, str]) -> None:
        self.map_path.write_text(json.dumps(table), encoding="utf-8")
        reset_embed_cache()
        EmbedFake.hits = []

    def test_threshold_is_about_0_85(self):
        self.assertEqual(COSINE_MIN, 0.85)
        self.assertEqual(EMBED_MODEL, "snowflake-arctic-embed:m")
        self.assertGreaterEqual(cosine(WHO, VECTORS[PARAPHRASE]), 0.85)
        self.assertGreaterEqual(cosine(WHO, VECTORS[JUST]), 0.85)
        self.assertLess(cosine(WHO, VECTORS[BELOW]), 0.85)
        self.assertLess(cosine(WHO, VECTORS[UNRELATED]), 0.5)
        self.assertGreaterEqual(cosine(STATUS, VECTORS[STATUS_PARAPHRASE]), 0.85)

    def test_paraphrase_cosine_hits_the_map(self):
        self.assertEqual(
            lookup("Which board may generate replies?"),
            ANSWERS["who generates"],
        )
        self.assertTrue(EmbedFake.hits)
        for path, payload in EmbedFake.hits:
            self.assertEqual(path, "/api/embed")
            self.assertEqual(payload["model"], "snowflake-arctic-embed:m")
            self.assertEqual(payload["keep_alive"], -1)
            self.assertIsInstance(payload["keep_alive"], int)
            self.assertIsInstance(payload["input"], list)
        sent = [text for _path, payload in EmbedFake.hits for text in payload["input"]]
        self.assertIn(PARAPHRASE, sent)
        self.assertNotIn("Which board may generate replies?", sent)

    def test_cosine_just_above_0_85_hits(self):
        self.assertEqual(lookup(JUST), ANSWERS["who generates"])

    def test_cosine_under_0_85_misses(self):
        self.assertIsNone(lookup(BELOW))
        self.assertTrue(EmbedFake.hits)

    def test_unrelated_line_misses(self):
        self.assertIsNone(lookup("Recipe for sourdough bread."))
        sent = [text for _path, payload in EmbedFake.hits for text in payload["input"]]
        self.assertIn(UNRELATED, sent)

    def test_status_paraphrase_returns_the_status_answer(self):
        self.assertEqual(lookup(STATUS_PARAPHRASE), ANSWERS["status"])

    def test_higher_cosine_wins_when_two_keys_clear_the_bar(self):
        self._write(
            {
                "brain board": ANSWERS["brain board"],
                "who generates": ANSWERS["who generates"],
            }
        )
        brain_score = cosine(VECTORS[CLOSER], VECTORS["brain board"])
        who_score = cosine(VECTORS[CLOSER], WHO)
        self.assertGreaterEqual(brain_score, 0.85)
        self.assertGreater(who_score, brain_score)
        self.assertEqual(lookup(CLOSER), ANSWERS["who generates"])

    def test_exact_key_does_not_call_embed(self):
        self.assertEqual(lookup("Who Generates!"), ANSWERS["who generates"])
        self.assertEqual(lookup("Hi!"), ANSWERS["hi"])
        self.assertEqual(EmbedFake.hits, [])

    def test_folded_key_does_not_call_embed(self):
        self._write({"Who Generates": ANSWERS["who generates"]})
        self.assertEqual(lookup("who generates"), ANSWERS["who generates"])
        self.assertEqual(EmbedFake.hits, [])

    def test_empty_line_does_not_call_embed(self):
        self.assertIsNone(lookup("   ???"))
        self.assertEqual(EmbedFake.hits, [])

    def test_dataset_and_health_roles_stay_exact(self):
        for role in ("dataset", "health"):
            os.environ["PI_PAIR_ROLE"] = role
            os.environ.pop("PI_PAIR_NAME", None)
            EmbedFake.hits = []
            reset_embed_cache()
            self.assertFalse(on_pi4(), role)
            self.assertIsNone(lookup(PARAPHRASE), role)
            self.assertEqual(EmbedFake.hits, [], role)
            self.assertEqual(lookup("Hi!"), ANSWERS["hi"], role)

    def test_name_pi4_enables_semantic_match(self):
        os.environ.pop("PI_PAIR_ROLE", None)
        os.environ["PI_PAIR_NAME"] = "rpi-pi4"
        self.assertTrue(on_pi4())
        self.assertEqual(lookup(PARAPHRASE), ANSWERS["who generates"])

    def test_name_pi3_does_not_embed(self):
        os.environ.pop("PI_PAIR_ROLE", None)
        os.environ["PI_PAIR_NAME"] = "rpi-pi3"
        self.assertFalse(on_pi4())
        self.assertIsNone(lookup(PARAPHRASE))
        self.assertEqual(EmbedFake.hits, [])

    def test_embed_http_error_is_a_miss(self):
        EmbedFake.mode = "http"
        self.assertIsNone(lookup(PARAPHRASE))

    def test_garbage_embed_payload_is_a_miss(self):
        EmbedFake.mode = "garbage"
        self.assertIsNone(lookup(PARAPHRASE))

    def test_short_embed_payload_is_a_miss(self):
        EmbedFake.mode = "short"
        self.assertIsNone(lookup(PARAPHRASE))

    def test_key_vectors_are_cached(self):
        lookup(PARAPHRASE)
        self.assertGreaterEqual(len(EmbedFake.hits), 2)
        EmbedFake.hits = []
        lookup(JUST)
        self.assertEqual(len(EmbedFake.hits), 1)
        self.assertEqual(EmbedFake.hits[0][1]["input"], [JUST])

    def _fake_embed(self, calls: list):
        def fake(texts):
            copied = list(texts)
            calls.append(copied)
            return [VECTORS.get(text, UNRELATED_VEC) for text in copied]

        return fake

    def test_warm_then_miss_embeds_only_the_query(self):
        calls: list = []
        with mock.patch("pair.embed.embed_texts", side_effect=self._fake_embed(calls)):
            warm_canned_embeddings(DEFAULT_MAP)
            self.assertEqual(calls, [sorted(DEFAULT_MAP)])
            calls.clear()
            warm_at_start()
            self.assertEqual(calls, [])
            self.assertEqual(lookup(PARAPHRASE), ANSWERS["who generates"])
            self.assertEqual(lookup(JUST), ANSWERS["who generates"])
            self.assertEqual(calls, [[PARAPHRASE], [JUST]])

    def test_startup_warm_uses_normalized_keys(self):
        self._write({"Who Generates": ANSWERS["who generates"], "Hi": ANSWERS["hi"]})
        calls: list = []
        with mock.patch("pair.embed.embed_texts", side_effect=self._fake_embed(calls)):
            warm_at_start()
            self.assertEqual(calls, [["hi", "who generates"]])
            calls.clear()
            self.assertEqual(lookup(PARAPHRASE), ANSWERS["who generates"])
            self.assertEqual(calls, [[PARAPHRASE]])

    def test_changed_map_after_warm_reembeds_keys(self):
        calls: list = []
        with mock.patch("pair.embed.embed_texts", side_effect=self._fake_embed(calls)):
            warm_at_start()
            calls.clear()
            self.map_path.write_text(
                json.dumps({"brain board": ANSWERS["brain board"]}),
                encoding="utf-8",
            )
            self.assertEqual(lookup(CLOSER), ANSWERS["brain board"])
            self.assertEqual(calls, [["brain board"], [CLOSER]])

    def test_warm_failure_falls_back_to_lazy_fill(self):
        calls: list = []
        state = {"fail": True}

        def fake(texts):
            copied = list(texts)
            calls.append(copied)
            if state["fail"]:
                return None
            return [VECTORS.get(text, UNRELATED_VEC) for text in copied]

        with mock.patch("pair.embed.embed_texts", side_effect=fake):
            warm_at_start()
            self.assertEqual(calls, [sorted(DEFAULT_MAP)])
            state["fail"] = False
            calls.clear()
            self.assertEqual(lookup(PARAPHRASE), ANSWERS["who generates"])
            self.assertEqual(calls, [sorted(DEFAULT_MAP), [PARAPHRASE]])

    def test_pi2_and_pi3_never_warm_or_embed(self):
        calls: list = []
        with mock.patch("pair.embed.embed_texts", side_effect=self._fake_embed(calls)):
            for role, name in (("health", "pi2"), ("dataset", "pi3")):
                os.environ["PI_PAIR_ROLE"] = role
                os.environ["PI_PAIR_NAME"] = name
                calls.clear()
                reset_embed_cache()
                warm_at_start()
                warm_canned_embeddings(DEFAULT_MAP)
                self.assertEqual(calls, [], role)
                self.assertIsNone(lookup(PARAPHRASE), role)
                self.assertEqual(lookup("Hi!"), ANSWERS["hi"], role)
                self.assertEqual(calls, [], role)
            os.environ.pop("PI_PAIR_ROLE", None)
            for name in ("pi2", "rpi-pi3"):
                os.environ["PI_PAIR_NAME"] = name
                calls.clear()
                reset_embed_cache()
                self.assertFalse(on_pi4(), name)
                warm_at_start()
                warm_canned_embeddings(DEFAULT_MAP)
                self.assertEqual(calls, [], name)
                self.assertIsNone(lookup(PARAPHRASE), name)
                self.assertEqual(calls, [], name)

    def test_mid_warm_miss_is_exact_or_one_query(self):
        release = threading.Event()
        entered = threading.Event()
        calls: list = []

        def fake(texts):
            copied = list(texts)
            calls.append(copied)
            if copied == sorted(DEFAULT_MAP):
                entered.set()
                release.wait(5)
            return [VECTORS.get(text, UNRELATED_VEC) for text in copied]

        thread: threading.Thread | None = None
        try:
            with mock.patch("pair.embed.embed_texts", side_effect=fake):
                thread = start_canned_warm()
                self.assertTrue(entered.wait(3), "warm did not start")
                self.assertEqual(warm_status(), "warming")
                self.assertEqual(lookup("Hi!"), ANSWERS["hi"])
                before = len(calls)
                self.assertIsNone(lookup(PARAPHRASE))
                self.assertEqual(calls[before:], [])
                self.assertFalse(release.is_set())
                release.set()
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(warm_status(), "ready")
                calls.clear()
                self.assertEqual(lookup(PARAPHRASE), ANSWERS["who generates"])
                self.assertEqual(calls, [[PARAPHRASE]])
        finally:
            release.set()
            if thread is not None:
                thread.join(timeout=2)

        calls.clear()
        with mock.patch("pair.embed.embed_texts", side_effect=self._fake_embed(calls)):
            warm_canned_embeddings(DEFAULT_MAP)
        calls.clear()
        set_warm_status("warming")
        try:
            with mock.patch(
                "pair.embed.embed_texts", side_effect=RuntimeError("embed down")
            ):
                self.assertIsNone(lookup(PARAPHRASE))
                self.assertEqual(lookup("Hi!"), ANSWERS["hi"])
        finally:
            reset_warm_state()

        calls.clear()
        set_warm_status("warming")
        try:
            with mock.patch(
                "pair.embed.embed_texts", side_effect=self._fake_embed(calls)
            ):
                self.assertEqual(lookup(JUST), ANSWERS["who generates"])
                self.assertEqual(calls, [[JUST]])
        finally:
            reset_warm_state()

    def test_health_and_exact_chat_answer_before_warm_finishes(self):
        from pair import server as server_mod

        release = threading.Event()
        entered = threading.Event()

        def fake(texts):
            entered.set()
            release.wait(5)
            return [VECTORS.get(text, UNRELATED_VEC) for text in texts]

        peers = Path(self.tmp.name) / "peers.json"
        peers.write_text(
            json.dumps(
                [
                    {
                        "name": "pi4",
                        "host": "127.0.0.1",
                        "port": 1,
                        "kind": "ollama",
                        "generative": True,
                        "role": "brain",
                    }
                ]
            ),
            encoding="utf-8",
        )
        os.environ["PI_PAIR_PEERS"] = str(peers)
        os.environ["PI_PAIR_HOST"] = "127.0.0.1"
        os.environ["PI_PAIR_PORT"] = "0"
        os.environ["PI_PAIR_DATA"] = self.tmp.name
        captured: dict = {}
        real_make = server_mod.make_server

        def spy(host=None, port=None):
            httpd = real_make(host, port)
            captured["httpd"] = httpd
            return httpd

        httpd = None
        try:
            with (
                mock.patch("pair.embed.embed_texts", side_effect=fake),
                mock.patch("pair.server.make_server", side_effect=spy),
            ):
                worker = threading.Thread(target=server_mod.main, daemon=True)
                worker.start()
                self.assertTrue(entered.wait(3), "warm did not start")
                httpd = captured["httpd"]
                port = httpd.server_address[1]
                self.assertGreater(port, 0)
                status, health = self._request(port, "/health")
                self.assertEqual(status, 200)
                self.assertTrue(health["ok"])
                self.assertEqual(health["warm"], "warming")
                status, chat = self._request(
                    port,
                    "/v1/chat/completions",
                    {
                        "model": "qwen2.5:0.5b",
                        "messages": [{"role": "user", "content": "Hi!"}],
                        "stream": False,
                    },
                )
                self.assertEqual(status, 200)
                self.assertEqual(chat["pi_chip"], "cache")
                self.assertEqual(
                    chat["choices"][0]["message"]["content"], ANSWERS["hi"]
                )
                self.assertTrue(entered.is_set())
                self.assertFalse(release.is_set())
                release.set()
                self.assertIsNotNone(server_mod.warm_thread)
                server_mod.warm_thread.join(timeout=2)
                self.assertFalse(server_mod.warm_thread.is_alive())
                status, ready = self._request(port, "/health")
                self.assertEqual(status, 200)
                self.assertEqual(ready["warm"], "ready")
        finally:
            release.set()
            if httpd is not None:
                httpd.shutdown()
                httpd.server_close()
            reset_warm_state()

    def _request(
        self, port: int, path: str, payload: dict | None = None
    ) -> tuple[int, dict]:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=data,
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode() or "{}")

    def test_off_brain_serves_without_embedding(self):
        from pair import server as server_mod

        calls: list = []

        def fake(texts):
            calls.append(list(texts))
            return [VECTORS.get(text, UNRELATED_VEC) for text in texts]

        def serve():
            calls.append("serve")

        with (
            mock.patch("pair.embed.embed_texts", side_effect=fake),
            mock.patch("pair.server.make_server") as make_server,
        ):
            make_server.return_value.serve_forever.side_effect = serve
            for role, name in (("dataset", "pi3"), ("health", "pi2")):
                os.environ["PI_PAIR_ROLE"] = role
                os.environ["PI_PAIR_NAME"] = name
                calls.clear()
                reset_embed_cache()
                reset_warm_state()
                server_mod.main()
                self.assertIsNotNone(server_mod.warm_thread)
                server_mod.warm_thread.join(timeout=2)
                self.assertEqual(calls, ["serve"], role)
                self.assertEqual(warm_status(), "ready", role)

    def test_ollama_base_defaults_and_rewrites_bind_all(self):
        os.environ.pop("PI_PAIR_OLLAMA", None)
        os.environ.pop("OLLAMA_HOST", None)
        self.assertEqual(ollama_base(), "http://127.0.0.1:11434")
        os.environ["PI_PAIR_OLLAMA"] = "0.0.0.0:11434"
        self.assertEqual(ollama_base(), "http://127.0.0.1:11434")
        os.environ["PI_PAIR_OLLAMA"] = "http://127.0.0.1:11434/api/embed"
        self.assertEqual(embed_url(), "http://127.0.0.1:11434/api/embed")

    def test_readme_and_installer_pull_the_embed_model(self):
        readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("ollama pull snowflake-arctic-embed:m", readme)
        self.assertIn("/api/embed", readme)
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('OLLAMA_EMBED_MODEL="snowflake-arctic-embed:m"', script)
        self.assertIn('ollama pull "$OLLAMA_EMBED_MODEL"', script)


if __name__ == "__main__":
    unittest.main()
