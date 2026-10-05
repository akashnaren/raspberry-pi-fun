"""Arctic embed client and canned-map near-match. No live Ollama."""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.canned import lookup, normalize_key, write_map
from pair.embed import (
    COSINE_THRESHOLD,
    EMBED_MODEL,
    PULL_COMMAND,
    cosine,
    embed_texts,
    ollama_embed_url,
    reset_embed_state,
    vectors_path,
)

FAST = "Normalize input, then probe the canned map."
PI4 = "Only pi4 generates tokens for the user."
HI = "Hi. What can I help you with?"


def _at(score: float) -> list[float]:
    other = math.sqrt(max(0.0, 1.0 - score * score))
    return [score, other]


class _Server:
    def __init__(self, respond):
        seen: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                payload = json.loads(self.rfile.read(length).decode() or "{}")
                seen.append({"path": self.path.split("?")[0], "payload": payload})
                code, body = respond(payload)
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.seen = seen
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        port = self.httpd.server_address[1]
        return f"http://127.0.0.1:{port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class EmbedClient(unittest.TestCase):
    def setUp(self):
        reset_embed_state()
        self._env = {
            key: os.environ.get(key)
            for key in ("PI_PAIR_EMBED", "PI_PAIR_EMBED_URL", "PI_PAIR_EMBED_TIMEOUT", "PI_PAIR_PEERS")
        }
        self.tmp = tempfile.TemporaryDirectory()
        self.servers: list[_Server] = []
        os.environ.pop("PI_PAIR_EMBED", None)
        os.environ.pop("PI_PAIR_EMBED_URL", None)

    def tearDown(self):
        for server in self.servers:
            server.close()
        self.tmp.cleanup()
        reset_embed_state()
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _serve(self, respond) -> _Server:
        server = _Server(respond)
        self.servers.append(server)
        return server

    def test_pull_command_is_documented(self):
        import pair.embed as embed

        self.assertEqual(PULL_COMMAND, "ollama pull snowflake-arctic-embed:m")
        self.assertIn(PULL_COMMAND, embed.__doc__)
        self.assertEqual(EMBED_MODEL, "snowflake-arctic-embed:m")
        self.assertEqual(COSINE_THRESHOLD, 0.85)

    def test_embed_client_posts_api_embed(self):
        def respond(payload):
            inputs = payload.get("input") or []
            return 200, {"embeddings": [[float(len(str(text))), 1.0] for text in inputs]}

        server = self._serve(respond)
        vectors = embed_texts(["hello", "there"], url=server.url)
        self.assertEqual(vectors, [[5.0, 1.0], [5.0, 1.0]])
        self.assertEqual(server.seen[0]["path"], "/api/embed")
        self.assertEqual(server.seen[0]["payload"]["input"], ["hello", "there"])
        self.assertEqual(server.seen[0]["payload"]["model"], "snowflake-arctic-embed:m")

    def test_embed_client_accepts_legacy_embedding_field(self):
        server = self._serve(lambda payload: (200, {"embedding": [0.25, 0.5]}))
        self.assertEqual(embed_texts(["only"], url=server.url), [[0.25, 0.5]])

    def test_embed_client_rejects_a_bad_body(self):
        server = self._serve(lambda payload: (200, {"embeddings": "nope"}))
        with self.assertRaises(ValueError):
            embed_texts(["hello"], url=server.url)

    def test_embed_client_rejects_http_error(self):
        server = self._serve(lambda payload: (404, {"error": "model not found"}))
        with self.assertRaises(OSError):
            embed_texts(["hello"], url=server.url)

    def test_embed_url_is_the_generative_ollama_peer(self):
        path = Path(self.tmp.name) / "peers.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "name": "pi3",
                        "host": "10.0.0.228",
                        "port": 18080,
                        "kind": "health",
                        "generative": False,
                        "role": "dataset",
                    },
                    {
                        "name": "pi4",
                        "host": "10.2.2.2",
                        "port": 11434,
                        "kind": "ollama",
                        "generative": True,
                        "role": "brain",
                    },
                ]
            ),
            encoding="utf-8",
        )
        os.environ["PI_PAIR_PEERS"] = str(path)
        self.assertEqual(ollama_embed_url(), "http://10.2.2.2:11434")
        os.environ["PI_PAIR_EMBED"] = "0"
        self.assertIsNone(ollama_embed_url())


class SemanticLookup(unittest.TestCase):
    def setUp(self):
        reset_embed_state()
        self._env = {
            key: os.environ.get(key)
            for key in ("PI_PAIR_EMBED", "PI_PAIR_EMBED_URL", "PI_PAIR_CANNED", "PI_PAIR_PEERS")
        }
        self.tmp = tempfile.TemporaryDirectory()
        self.servers: list[_Server] = []
        os.environ.pop("PI_PAIR_EMBED", None)
        os.environ.pop("PI_PAIR_EMBED_URL", None)
        os.environ.pop("PI_PAIR_CANNED", None)
        self.fast = [1.0, 0.0]
        self.who = [0.0, 1.0]
        self.keys = ["hello", "what is the fast path", "who generates"]
        self.catalog = {
            "hello": [0.0, -1.0],
            "what is the fast path": self.fast,
            "who generates": self.who,
            "how does the fast path work": _at(0.851),
            "what is a quick route": _at(0.849),
            "which board generates": [0.1, 0.995],
            "recipe for sourdough bread": [-1.0, 0.0],
        }
        self.table = {
            "what is the fast path": FAST,
            "who generates": PI4,
            "Hello!": HI,
        }
        self.path = Path(self.tmp.name) / "canned_map.json"
        self.path.write_text(json.dumps(self.table), encoding="utf-8")

    def tearDown(self):
        for server in self.servers:
            server.close()
        self.tmp.cleanup()
        reset_embed_state()
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _embedder(self, calls: list[list[str]]):
        def embedder(texts):
            calls.append(list(texts))
            return [list(self.catalog[text]) for text in texts]

        return embedder

    def _serve(self, respond) -> _Server:
        server = _Server(respond)
        self.servers.append(server)
        return server

    def test_exact_normalize_key_does_not_embed(self):
        def embedder(texts):
            raise AssertionError(texts)

        self.assertEqual(normalize_key("  HELLO!!! "), "hello")
        self.assertEqual(lookup("  HELLO!!! ", self.path, embedder=embedder), HI)
        self.assertEqual(lookup("Hello!", self.path, embedder=embedder), HI)
        self.assertIsNone(lookup("   ", self.path, embedder=embedder))

    def test_paraphrase_hits_and_unrelated_misses(self):
        calls: list[list[str]] = []
        embedder = self._embedder(calls)
        paraphrase = self.catalog["how does the fast path work"]
        near = self.catalog["what is a quick route"]
        unrelated = self.catalog["recipe for sourdough bread"]
        self.assertGreater(cosine(self.fast, paraphrase), 0.85)
        self.assertLess(cosine(self.fast, near), 0.85)
        self.assertLess(cosine(self.who, near), 0.85)
        self.assertLess(cosine(self.fast, unrelated), 0.85)
        self.assertLess(cosine(self.who, unrelated), 0.85)
        self.assertEqual(
            lookup("How does the FAST path work??", self.path, embedder=embedder),
            FAST,
        )
        self.assertIsNone(lookup("what is a quick route", self.path, embedder=embedder))
        self.assertIsNone(lookup("recipe for sourdough bread", self.path, embedder=embedder))
        self.assertEqual(calls[0], self.keys)
        self.assertEqual(calls[1], ["how does the fast path work"])

    def test_best_key_wins(self):
        calls: list[list[str]] = []
        found = lookup("which board generates", self.path, embedder=self._embedder(calls))
        self.assertEqual(found, PI4)
        self.assertGreater(cosine(self.who, self.catalog["which board generates"]), 0.85)
        self.assertLess(cosine(self.fast, self.catalog["which board generates"]), 0.85)

    def test_vectors_are_stored_and_reused(self):
        calls: list[list[str]] = []
        embedder = self._embedder(calls)
        self.assertEqual(lookup("how does the fast path work", self.path, embedder=embedder), FAST)
        sidecar = vectors_path(self.path)
        self.assertTrue(sidecar.is_file())
        stored = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(stored["model"], EMBED_MODEL)
        self.assertEqual(set(stored["vectors"]), set(self.keys))
        reset_embed_state()
        calls.clear()
        self.assertIsNone(lookup("recipe for sourdough bread", self.path, embedder=embedder))
        self.assertEqual(calls, [["recipe for sourdough bread"]])
        stored["model"] = "other-model"
        sidecar.write_text(json.dumps(stored), encoding="utf-8")
        reset_embed_state()
        calls.clear()
        self.assertEqual(lookup("how does the fast path work", self.path, embedder=embedder), FAST)
        self.assertEqual(calls[0], self.keys)

    def test_write_map_drops_the_sidecar(self):
        lookup("how does the fast path work", self.path, embedder=self._embedder([]))
        self.assertTrue(vectors_path(self.path).is_file())
        write_map({"hi": HI}, self.path)
        self.assertFalse(vectors_path(self.path).exists())
        self.assertEqual(lookup("hi", self.path, embedder=self._embedder([])), HI)

    def test_embed_failure_is_a_miss(self):
        def boom(texts):
            raise RuntimeError("ollama down")

        self.assertIsNone(lookup("how does the fast path work", self.path, embedder=boom))
        self.assertEqual(lookup("what is the fast path", self.path, embedder=boom), FAST)

    def test_lookup_uses_ollama_embed_api(self):
        def respond(payload):
            inputs = payload.get("input")
            if payload.get("model") != "snowflake-arctic-embed:m" or not isinstance(inputs, list):
                return 500, {"error": "request"}
            try:
                vectors = [self.catalog[text] for text in inputs]
            except KeyError:
                return 500, {"error": "unknown"}
            return 200, {"embeddings": vectors}

        server = self._serve(respond)
        os.environ["PI_PAIR_EMBED_URL"] = server.url
        os.environ["PI_PAIR_CANNED"] = str(self.path)
        self.assertEqual(lookup("How does the fast path work?"), FAST)
        self.assertTrue(server.seen)
        self.assertTrue(all(item["path"] == "/api/embed" for item in server.seen))
        self.assertEqual(server.seen[0]["payload"]["model"], "snowflake-arctic-embed:m")
        self.assertEqual(server.seen[0]["payload"]["input"], self.keys)
        self.assertEqual(server.seen[1]["payload"]["input"], ["how does the fast path work"])
        reset_embed_state()
        before = len(server.seen)
        self.assertIsNone(lookup("recipe for sourdough bread"))
        self.assertEqual(server.seen[before]["payload"]["input"], ["recipe for sourdough bread"])
        self.assertEqual(lookup("Hello!"), HI)
        self.assertEqual(len(server.seen), before + 1)

    def test_off_switch_skips_semantic_lookup(self):
        def respond(payload):
            return 200, {"embeddings": [[1.0, 0.0]]}

        server = self._serve(respond)
        os.environ["PI_PAIR_EMBED"] = "off"
        os.environ["PI_PAIR_EMBED_URL"] = server.url
        self.assertIsNone(lookup("how does the fast path work", self.path))
        self.assertEqual(server.seen, [])
        self.assertEqual(lookup("what is the fast path", self.path), FAST)


if __name__ == "__main__":
    unittest.main()
