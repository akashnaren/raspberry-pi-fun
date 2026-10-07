"""Arctic :xs stays on the dataset role and unloads before OCR."""

from __future__ import annotations


from tests.support.paths import ROOT
import importlib.util
import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pair.nodes import embedder
from pair.nodes.worker import handle, health_body
from pair.routes.status import health_document, public_health


class _Resp:
    def __init__(self, payload: dict):
        self.raw = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class EmbedderCase(unittest.TestCase):
    def setUp(self):
        self._env = {
            key: os.environ.get(key) for key in ("PI_PAIR_ROLE", "PI_PAIR_EMBED")
        }
        self._drain = embedder.DRAIN_S
        embedder._ocr_active = 0
        embedder._embed_active = 0
        embedder._ps_cache = None

    def tearDown(self):
        embedder.DRAIN_S = self._drain
        embedder._ocr_active = 0
        embedder._embed_active = 0
        embedder._ps_cache = None
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _enable(self, role="dataset", machine="aarch64", flag=None):
        os.environ["PI_PAIR_ROLE"] = role
        if flag is None:
            os.environ.pop("PI_PAIR_EMBED", None)
        else:
            os.environ["PI_PAIR_EMBED"] = flag
        patcher = patch("pair.nodes.embedder.platform.machine", return_value=machine)
        patcher.start()
        self.addCleanup(patcher.stop)


class Enabled(EmbedderCase):
    def test_truth_table(self):
        cases = (
            ("dataset", "aarch64", None, True),
            ("dataset", "arm64", None, True),
            ("dataset", "aarch64", "1", True),
            ("brain", "aarch64", None, False),
            ("health", "aarch64", None, False),
            ("dataset", "x86_64", None, False),
            ("dataset", "armv7l", None, False),
            ("dataset", "aarch64", "0", False),
        )
        for role, machine, flag, expect in cases:
            self._enable(role, machine, flag)
            self.assertEqual(embedder.enabled(), expect, (role, machine, flag))


class EmbedCalls(EmbedderCase):
    def test_vectors_are_checked_and_a_mismatch_skips(self):
        self._enable()

        def fake(request, timeout=1):
            payload = json.loads(request.data.decode())
            self.assertTrue(request.full_url.endswith("/api/embed"))
            self.assertEqual(payload["keep_alive"], "30s")
            self.assertTrue(payload["truncate"])
            rows = [[0.25, 0.5] for _ in payload["input"]]
            return _Resp({"embeddings": rows})

        with patch("pair.nodes.embedder.urllib.request.urlopen", fake):
            body = embedder.embed(["hello there", "hi"])
        self.assertEqual(body["skipped"], "")
        self.assertEqual(len(body["vectors"]), 2)
        self.assertEqual(body["vectors"][0], [0.25, 0.5])
        self.assertEqual(body["model"], embedder.EMBED_MODEL)

        def mismatch(request, timeout=1):
            return _Resp({"embeddings": [[0.1, 0.2]]})

        with patch("pair.nodes.embedder.urllib.request.urlopen", mismatch):
            bad = embedder.embed(["one", "two"])
        self.assertEqual(bad["skipped"], "error")
        self.assertEqual(bad["vectors"], [])

    def test_ocr_busy_returns_immediately(self):
        self._enable()
        with (
            patch.object(embedder, "unload"),
            patch(
                "pair.nodes.embedder.urllib.request.urlopen",
                side_effect=AssertionError("network"),
            ),
        ):
            embedder.ocr_enter()
            started = time.monotonic()
            body = embedder.embed(["hello"])
            elapsed = time.monotonic() - started
            embedder.ocr_exit()
        self.assertEqual(body["skipped"], "ocr_busy")
        self.assertLess(elapsed, 0.05)

    def test_low_memory_does_not_call_ollama(self):
        self._enable()
        with (
            patch.object(embedder, "mem_available_mb", return_value=10),
            patch(
                "pair.nodes.embedder.urllib.request.urlopen",
                side_effect=AssertionError("network"),
            ),
        ):
            body = embedder.embed(["hello"])
        self.assertEqual(body["skipped"], "low_memory")

    def test_too_many_skips(self):
        self._enable()
        with patch(
            "pair.nodes.embedder.urllib.request.urlopen",
            side_effect=AssertionError("network"),
        ):
            body = embedder.embed(["x"] * (embedder.MAX_TEXTS + 1))
        self.assertEqual(body["skipped"], "too_many")


class OcrLock(EmbedderCase):
    def test_drain_waits_at_most_drain_s_then_unloads(self):
        self._enable()
        embedder.DRAIN_S = 0.12
        with embedder._COND:
            embedder._embed_active = 1
        started = time.monotonic()
        with patch.object(embedder, "unload") as unload:
            embedder.ocr_enter()
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.1)
        self.assertLessEqual(elapsed, embedder.DRAIN_S + 0.25)
        self.assertLess(elapsed, 3.6)
        self.assertEqual(unload.call_count, 1)
        with embedder._COND:
            embedder._embed_active = 0
        embedder.ocr_exit()

    def test_reentrant_ocr_unloads_once_and_exit_does_not_load(self):
        self._enable()
        with (
            patch.object(embedder, "unload") as unload,
            patch("pair.nodes.embedder.subprocess.run") as stop,
        ):
            embedder.ocr_enter()
            embedder.ocr_enter()
            self.assertEqual(unload.call_count, 1)
            unload.reset_mock()
            embedder.ocr_exit()
            embedder.ocr_exit()
        unload.assert_not_called()
        stop.assert_not_called()
        self.assertEqual(embedder._ocr_active, 0)

    def test_state_respects_the_ps_timeout(self):
        self._enable()

        def slow(request, timeout=1):
            time.sleep(timeout)
            raise TimeoutError("ps")

        started = time.monotonic()
        with patch("pair.nodes.embedder.urllib.request.urlopen", slow):
            value = embedder.state()
        elapsed = time.monotonic() - started
        self.assertEqual(value, "missing")
        self.assertLess(elapsed, 0.9)
        started = time.monotonic()
        again = embedder.state()
        self.assertEqual(again, "missing")
        self.assertLess(time.monotonic() - started, 0.05)


class MeshSurface(EmbedderCase):
    def test_dataset_health_reports_embed_and_the_brain_does_not(self):
        self._enable()
        with (
            patch("pair.routes.status.snapshot_peers", return_value=[]),
            patch("pair.nodes.embedder.state", return_value="unloaded"),
        ):
            doc = health_document()
        self.assertEqual(doc["services"]["embed"]["state"], "unloaded")
        self.assertTrue(doc["services"]["embed"]["ok"])
        shown = public_health(doc)
        self.assertNotIn("unloaded", json.dumps(shown))
        os.environ["PI_PAIR_ROLE"] = "brain"
        with patch("pair.routes.status.snapshot_peers", return_value=[]):
            brain = health_document()
        self.assertNotIn("embed", brain["services"])

    def test_worker_embed_is_off_without_network_and_health_names_it(self):
        os.environ["PI_PAIR_ROLE"] = "health"
        with patch(
            "pair.nodes.embedder.urllib.request.urlopen",
            side_effect=AssertionError("network"),
        ):
            status, body = handle("/tools/embed", {"texts": ["a"]})
        self.assertEqual(status, 200)
        self.assertEqual(body["skipped"], "off")
        self.assertEqual(body["vectors"], [])
        tool_health = health_body()
        self.assertNotIn("embed", tool_health)
        os.environ["PI_PAIR_ROLE"] = "dataset"
        with patch("pair.nodes.embedder.state", return_value="loaded"):
            tool_health = health_body()
        self.assertEqual(tool_health["embed"], "loaded")
        self.assertIn("/tools/embed", tool_health["routes"])

    def test_embedder_does_not_import_the_hot_path(self):
        source = Path(embedder.__file__).read_text(encoding="utf-8")
        for banned in (
            "pair.model.chat_once",
            "pair.core.runtime",
            "pair.model.sched",
            "pair.flywheel.miss_queue",
            "pair.render.images",
            "pair.memory.store",
        ):
            self.assertNotIn(banned, source)

    def test_threshold_disables_paraphrase_when_negatives_stay_high(self):
        path = ROOT / "scripts" / "eval" / "calibrate_paraphrase.py"
        spec = importlib.util.spec_from_file_location("calibrate_paraphrase", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.choose_threshold([0.2, 0.84]), 0.85)
        self.assertEqual(module.choose_threshold([0.9, 0.91]), 0.92)
        self.assertEqual(module.choose_threshold([0.995]), 1.01)


if __name__ == "__main__":
    unittest.main()
