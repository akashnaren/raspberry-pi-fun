"""Mesh tool dispatch: hedge, breaker, failover, and no generation on pi2/pi3."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from pair import runtime
from pair.guard import may_generate
from pair.health import peer_load
from pair.nodes import websearch
from pair.nodes.worker import forbidden_routes, handle
from pair.ocr import recognize_image
from pair.server import make_server
from pair.tools import (
    SEARCH_MEDIAN_TARGET_S,
    SEARCH_P95_TARGET_S,
    Dispatcher,
    Tool,
    ToolError,
    generation_routes,
    latency_stats,
    registry,
    schedule_prefix_prime,
    simulated_search_latencies,
)


class Dispatch(unittest.TestCase):
    def test_registry_never_points_at_generation(self):
        self.assertEqual(generation_routes(), [])
        self.assertEqual(forbidden_routes(), [])
        for tool in registry().values():
            self.assertNotIn("pi4", tool.nodes)
            self.assertTrue(tool.path.startswith("/tools/"))
        images = registry()["images"]
        self.assertEqual(images.nodes, ("pi2", "pi3"))
        self.assertEqual(images.path, "/tools/images")
        self.assertEqual(images.timeout, 5.0)
        self.assertTrue(images.retry_safe)
        self.assertNotIn(
            images.path, ("/api/chat", "/api/generate", "/v1/chat/completions")
        )
        with self.assertRaises(ValueError):
            Tool("images", "/api/chat", ("pi2",), 5.0, True)
        with self.assertRaises(ValueError):
            Tool("images", "/v1/chat/completions", ("pi2",), 5.0, True)
        with self.assertRaises(ValueError):
            Tool("images", "/tools/images", ("pi4",), 5.0, True)
        embed = registry()["embed"]
        self.assertEqual(embed.nodes, ("pi3",))
        self.assertEqual(embed.path, "/tools/embed")
        self.assertEqual(embed.timeout, 3.0)
        self.assertTrue(embed.retry_safe)
        self.assertEqual(generation_routes(), [])
        with self.assertRaises(ValueError):
            Tool("embed", "/tools/embed", ("pi4",), 3.0, True)

    def test_least_loaded_node_is_first_and_a_fast_success_skips_the_hedge(self):
        calls = []

        def invoke(node, tool, payload):
            calls.append(node)
            return {"ok": True, "node": node}

        dispatcher = Dispatcher(
            invoke,
            load=lambda _names: {"pi2": {"load": 5}, "pi3": {"load": 1}},
            hedge_s=0.2,
        )
        result = dispatcher.call("search", {"q": "bench"})
        self.assertEqual(result["node"], "pi3")
        self.assertEqual(calls, ["pi3"])

    def test_a_slow_node_is_hedged(self):
        calls = []

        def invoke(node, tool, payload):
            calls.append(node)
            if node == "pi2":
                time.sleep(0.4)
                return {"ok": True, "node": node}
            return {"ok": True, "node": node}

        dispatcher = Dispatcher(
            invoke,
            tools={
                "search": Tool("search", "/tools/search", ("pi2", "pi3"), 2.0, True)
            },
            hedge_s=0.05,
        )
        result = dispatcher.call("search", {"q": "bench"})
        self.assertEqual(result["node"], "pi3")
        self.assertIn("pi2", calls)
        self.assertIn("pi3", calls)

    def test_breaker_opens_after_three_failures_and_the_next_node_answers(self):
        calls = []

        def invoke(node, tool, payload):
            calls.append(node)
            if node == "pi2":
                raise RuntimeError("down")
            return {"ok": True, "node": node}

        tool = Tool("search", "/tools/search", ("pi2", "pi3"), 2.0, True)
        dispatcher = Dispatcher(invoke, tools={"search": tool}, hedge_s=0.01)
        for _ in range(3):
            result = dispatcher.call("search", {"q": "bench"})
            self.assertEqual(result["node"], "pi3")
        before = calls.count("pi2")
        self.assertGreaterEqual(before, 3)
        result = dispatcher.call("search", {"q": "again"})
        self.assertEqual(result["node"], "pi3")
        self.assertEqual(calls.count("pi2"), before)

    def test_failover_when_the_first_node_fails(self):
        def invoke(node, tool, payload):
            if node == "pi3":
                raise RuntimeError("busy")
            return {"ok": True, "node": node}

        dispatcher = Dispatcher(
            invoke,
            load=lambda _names: {"pi2": {"load": 2}, "pi3": {"load": 0}},
            hedge_s=0.01,
        )
        result = dispatcher.call("extract", {"filename": "a.txt"})
        self.assertEqual(result["node"], "pi2")

    def test_an_open_breaker_with_no_backup_is_an_error(self):
        def invoke(node, tool, payload):
            raise RuntimeError("down")

        tool = Tool("tokenize", "/tools/tokenize", ("pi3",), 0.2, False)
        dispatcher = Dispatcher(invoke, tools={"tokenize": tool}, hedge_s=0.01)
        for _ in range(3):
            with self.assertRaises(ToolError):
                dispatcher.call("tokenize", {"text": "hi"})
        with self.assertRaises(ToolError):
            dispatcher.call("tokenize", {"text": "hi"})


class SearchSim(unittest.TestCase):
    def test_vm_search_stays_under_the_latency_targets(self):
        stats = latency_stats(simulated_search_latencies())
        self.assertGreaterEqual(stats["n"], 50)
        self.assertLess(stats["median_s"], SEARCH_MEDIAN_TARGET_S)
        self.assertLess(stats["p95_s"], SEARCH_P95_TARGET_S)
        self.assertLess(stats["median_s"], 1.0)

    def test_parallel_search_is_cached(self):
        websearch.clear_cache()
        hits = []

        def fetch(url):
            hits.append(url)
            if "wikipedia" in url:
                return (
                    200,
                    json.dumps(
                        {
                            "title": "Bench",
                            "extract": "A long seat.",
                            "content_urls": {
                                "desktop": {"page": "https://example.test/bench"}
                            },
                        }
                    ),
                )
            return (
                200,
                '<a class="result-link" href="https://example.test/a">Height</a>'
                '<td class="result-snippet">about 18 inches</td>',
            )

        first = websearch.search("hall bench", fetch=fetch, now=1000)
        second = websearch.search("hall bench", fetch=fetch, now=1001)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second, first)
        self.assertGreaterEqual(len(first["sources"]), 1)
        self.assertIn("[1]", first["context"])
        self.assertEqual(len(hits), 2)
        websearch.clear_cache()

    def test_load_snapshot_is_reused_for_two_seconds(self):
        calls = []

        def fetch(name):
            calls.append(name)
            return {"load": 1, "queue": 2, "temp_c": 40}

        first = peer_load(["pi2"], fetch=fetch, now=10)
        second = peer_load(["pi2"], fetch=fetch, now=11)
        self.assertEqual(first["pi2"]["load"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(second["pi2"]["queue"], 2)
        third = peer_load(["pi2"], fetch=fetch, now=13)
        self.assertEqual(len(calls), 2)
        self.assertEqual(third["pi2"]["temp_c"], 40)


class WorkerHttp(unittest.TestCase):
    def setUp(self):
        self._role = os.environ.get("PI_PAIR_ROLE")
        self._ocr = os.environ.get("PI_PAIR_OCR_URL")
        self.servers = []

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        if self._role is None:
            os.environ.pop("PI_PAIR_ROLE", None)
        else:
            os.environ["PI_PAIR_ROLE"] = self._role
        if self._ocr is None:
            os.environ.pop("PI_PAIR_OCR_URL", None)
        else:
            os.environ["PI_PAIR_OCR_URL"] = self._ocr

    def _serve(self) -> int:
        httpd = make_server("127.0.0.1", 0)
        self.servers.append(httpd)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd.server_address[1]

    def _post(self, port, path, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode() or "{}")

    def test_dataset_search_route_is_not_a_chat_route(self):
        os.environ["PI_PAIR_ROLE"] = "dataset"
        port = self._serve()
        status, body = self._post(port, "/api/chat", {"messages": []})
        self.assertNotEqual(status, 200)
        self.assertNotIn("tokens", body)
        status, body = self._post(port, "/tools/tokenize", {"text": "abcd"})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(body["tokens"], 1)
        status, body = self._post(port, "/api/generate", {})
        self.assertEqual(status, 404)

    def test_brain_refuses_search_and_may_extract_text(self):
        os.environ["PI_PAIR_ROLE"] = "brain"
        port = self._serve()
        status, body = self._post(port, "/tools/search", {"q": "bench"})
        self.assertEqual(status, 403)
        self.assertNotIn("generate", body["error"])
        import base64

        status, body = self._post(
            port,
            "/tools/extract",
            {
                "filename": "note.txt",
                "kind": "text",
                "data": base64.b64encode(b"hello file").decode(),
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["text"], "hello file")

    def test_remote_ocr_does_not_run_a_local_binary(self):
        os.environ["PI_PAIR_ROLE"] = "brain"
        os.environ["PI_PAIR_OCR_URL"] = "http://127.0.0.1:9/tools/extract"

        class _Body:
            def __init__(self, raw):
                self.raw = raw

            def read(self):
                return self.raw

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_open(request, timeout):
            self.assertIn("/tools/extract", request.full_url)
            self.assertNotIn("/api/", request.full_url)
            return _Body(json.dumps({"text": "from pi3"}).encode())

        with patch("urllib.request.urlopen", fake_open):
            with patch("pair.ocr.run_local") as local:
                text = recognize_image(b"not-an-image")
        self.assertEqual(text, "from pi3")
        local.assert_not_called()

    def test_prefix_prime_waits_until_the_decode_slot_is_idle(self):
        previous = list(runtime.PEERS)
        runtime.set_peers(
            [
                {
                    "name": "pi2",
                    "host": "127.0.0.1",
                    "port": 9,
                    "role": "health",
                    "generative": False,
                },
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": 9,
                    "role": "brain",
                    "generative": True,
                },
            ]
        )
        self.assertTrue(runtime.gate.try_acquire())
        try:
            self.assertFalse(schedule_prefix_prime([{"role": "user", "content": "hi"}]))
        finally:
            runtime.gate.release()
            runtime.set_peers(previous)
        self.assertFalse(may_generate({"name": "pi2", "role": "health"}))

    def test_images_handler_calls_cards_and_the_brain_refuses_the_route(self):
        with patch(
            "pair.images.cards", return_value={"ok": True, "cards": []}
        ) as mocked:
            status, body = handle(
                "/tools/images", {"question": "hi there", "answer": "hello there"}
            )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["cards"], [])
        mocked.assert_called_once()
        os.environ["PI_PAIR_ROLE"] = "brain"
        port = self._serve()
        status, body = self._post(
            port, "/tools/images", {"question": "hi there", "answer": "hello there"}
        )
        self.assertEqual(status, 403)

    def test_worker_handle_rejects_a_generation_path(self):
        status, body = handle("/api/chat", {})
        self.assertEqual(status, 404)
        self.assertIn("does not generate", body["error"])

    def test_compact_plan_and_memory_do_not_generate(self):
        self.assertEqual(generation_routes(), [])
        self.assertEqual(forbidden_routes(), [])
        previous = os.environ.get("PI_PAIR_DATA")
        tmp = tempfile.TemporaryDirectory()
        os.environ["PI_PAIR_DATA"] = tmp.name
        try:
            self._compact_and_memory()
        finally:
            if previous is None:
                os.environ.pop("PI_PAIR_DATA", None)
            else:
                os.environ["PI_PAIR_DATA"] = previous
            tmp.cleanup()

    def _compact_and_memory(self) -> None:
        status, body = handle(
            "/tools/compact_plan",
            {
                "num_ctx": 2,
                "turns": [
                    {"role": "user", "content": "the locker code is 4182"},
                    {"role": "assistant", "content": "Noted."},
                ],
            },
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["model"])
        self.assertNotIn("tokens", body)
        self.assertIn("4182", " ".join(body["facts"]))
        status, stored = handle(
            "/tools/memory", {"op": "put", "text": "the locker code is 4182"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(stored["model"])
        self.assertTrue(stored["facts"][0]["text"].startswith("The user said:"))


if __name__ == "__main__":
    unittest.main()
