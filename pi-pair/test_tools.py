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

from pair import ingest_job, ocr, runtime
from pair.guard import may_generate
from pair.health import peer_load
from pair.nodes import websearch
from pair.nodes.worker import forbidden_routes, handle
from pair.ocr import recognize_image
from pair.server import Handler, make_server
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


class ExtractOffload(unittest.TestCase):
    """Brain uploads OCR on pi3. A down pi3 falls back; a slow or 415 pi3 does not."""

    def setUp(self):
        self._role = os.environ.get("PI_PAIR_ROLE")
        self._inline = ingest_job.INLINE
        self._deadline = ingest_job.UPLOAD_DEADLINE_S
        self._peers = [dict(peer) for peer in runtime.PEERS]
        self._image = ocr.recognize_image
        self.servers = []
        self.extract_hits = 0
        self.local_runs = 0
        self.sleep_s = 0.0
        self.force_status = 0
        ingest_job.INLINE = True
        os.environ["PI_PAIR_ROLE"] = "brain"
        ocr.recognize_image = lambda _data: "from image"
        from pair.server import _local_extract as real_local

        real_extract = Handler._run_extract

        def local(*args, **kwargs):
            self.local_runs += 1
            return real_local(*args, **kwargs)

        def run_extract(handler, payload):
            self.extract_hits += 1
            if self.sleep_s:
                time.sleep(self.sleep_s)
                return 200, {"ok": True, "text": "late", "route": "ocr"}
            if self.force_status:
                return self.force_status, {
                    "ok": False,
                    "error": "that PDF has no readable text",
                    "status": self.force_status,
                }
            return real_extract(handler, payload)

        self._local_patch = patch("pair.server._local_extract", local)
        self._extract_patch = patch.object(Handler, "_run_extract", run_extract)
        self._local_patch.start()
        self._extract_patch.start()
        self.pi3 = self._serve()
        self.brain = self._serve()
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
                    "name": "pi3",
                    "host": "127.0.0.1",
                    "port": self.pi3,
                    "role": "dataset",
                    "generative": False,
                },
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": self.brain,
                    "role": "brain",
                    "generative": True,
                },
            ]
        )

    def tearDown(self):
        self._extract_patch.stop()
        self._local_patch.stop()
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()
        ingest_job.INLINE = self._inline
        ingest_job.UPLOAD_DEADLINE_S = self._deadline
        ocr.recognize_image = self._image
        runtime.set_peers(self._peers)
        if self._role is None:
            os.environ.pop("PI_PAIR_ROLE", None)
        else:
            os.environ["PI_PAIR_ROLE"] = self._role

    def _serve(self) -> int:
        httpd = make_server("127.0.0.1", 0)
        self.servers.append(httpd)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd.server_address[1]

    def _post(
        self, port: int, data: bytes, headers: dict[str, str], timeout: float = 5
    ):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/attachments",
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return (
                    response.status,
                    json.loads(response.read().decode()),
                    response.headers.get("X-Pi-Extract"),
                )
        except urllib.error.HTTPError as error:
            return (
                error.code,
                json.loads(error.read().decode() or "{}"),
                error.headers.get("X-Pi-Extract"),
            )

    def _post_image(self):
        return self._post(
            self.brain,
            b"\xff\xd8\xff\xd9",
            {"content-type": "image/jpeg", "x-filename": "pic.jpg"},
        )

    def test_image_upload_runs_on_pi3_once(self):
        status, body, via = self._post_image()
        self.assertEqual(status, 200, body)
        self.assertEqual(via, "pi3")
        self.assertEqual(body["text"], "from image")
        self.assertEqual(self.extract_hits, 1)
        self.assertEqual(self.local_runs, 0)

    def test_refused_pi3_falls_back_locally(self):
        for httpd in self.servers:
            if httpd.server_address[1] == self.pi3:
                httpd.shutdown()
                httpd.server_close()
        status, body, via = self._post_image()
        self.assertEqual(status, 200, body)
        self.assertEqual(via, "local")
        self.assertEqual(body["text"], "from image")
        self.assertEqual(self.local_runs, 1)

    def test_slow_pi3_is_504_without_a_local_run(self):
        self.sleep_s = 3
        ingest_job.UPLOAD_DEADLINE_S = 6
        started = time.monotonic()
        status, body, via = self._post_image()
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(status, 504, body)
        self.assertEqual(via, "pi3")
        self.assertEqual(self.local_runs, 0)

    def test_pi3_415_is_not_retried_locally(self):
        self.force_status = 415
        status, body, via = self._post_image()
        self.assertEqual(status, 415, body)
        self.assertEqual(via, "pi3")
        self.assertEqual(self.local_runs, 0)

    def test_legacy_text_pdf_is_not_rasterized(self):
        import base64
        import zlib

        from pair.nodes.worker import _extract

        content = zlib.compress(b"BT (Hello) Tj ET")
        pdf = (
            b"%PDF-1.4\n1 0 obj << /Length "
            + str(len(content)).encode("ascii")
            + b" /Filter /FlateDecode >>\nstream\n"
            + content
            + b"\nendstream\nendobj\n%%EOF"
        )
        with patch("pair.ocr.recognize_pdf") as raster:
            body = _extract(
                {
                    "filename": "note.pdf",
                    "kind": "pdf",
                    "data": base64.b64encode(pdf).decode("ascii"),
                }
            )
        raster.assert_not_called()
        self.assertTrue(body["ok"])
        self.assertEqual(body["text"], "Hello")


if __name__ == "__main__":
    unittest.main()
