"""Rest defers Pro rewarm and compaction. A user Pro warm stays immediate."""

from __future__ import annotations

import inspect
import io
import json
import os
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from pair.core import runtime, thermal
from pair.memory.compact import schedule
from pair.model.chat_once import ollama_payload, warm_in_flight
from pair.model.knobs import inference_knobs, runner_options
from pair.model.preload import (
    pro_preload_payload,
    rewarm_pro_if_evicted,
    start_pro_warm,
)
from pair.model.rest import _limits, defer_nonurgent
from pair.server import main as serve_main
from pair.server import start_model_warm
from pair.turn.shape import estimate_tokens, shape_messages
from tests.model import test_modes


def _cache(temp: float | None) -> None:
    with thermal._LOCK:
        thermal._SAMPLE["temp_c"] = temp
        thermal._SAMPLE["read_at"] = time.monotonic()


def _reset_latch() -> None:
    with thermal._LOCK:
        thermal._HOT = False
        thermal._SAMPLE["temp_c"] = None
        thermal._SAMPLE["read_at"] = 0.0


def _rest_on() -> dict:
    row = dict(inference_knobs())
    row["rest"] = {"enabled": True, "hot_c": 75, "cool_c": 68}
    return row


class _Resp:
    def __init__(self, payload: dict | None = None) -> None:
        self._raw = json.dumps(payload or {}).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *args) -> bool:
        return False


class RestRewarm(unittest.TestCase):
    def setUp(self) -> None:
        self._role = os.environ.get("PI_PAIR_ROLE")
        self._rest = os.environ.get("PI_PAIR_REST")
        os.environ["PI_PAIR_ROLE"] = "brain"
        os.environ.pop("PI_PAIR_REST", None)
        _reset_latch()
        self._knobs = patch("pair.model.rest.inference_knobs", side_effect=_rest_on)
        self._knobs.start()

    def tearDown(self) -> None:
        self._knobs.stop()
        _reset_latch()
        if self._role is None:
            os.environ.pop("PI_PAIR_ROLE", None)
        else:
            os.environ["PI_PAIR_ROLE"] = self._role
        if self._rest is None:
            os.environ.pop("PI_PAIR_REST", None)
        else:
            os.environ["PI_PAIR_REST"] = self._rest

    def _rewarm(self, resident: list[str]) -> tuple[list[dict], str]:
        seen: list[dict] = []

        def opener(request, timeout):
            del timeout
            seen.append(json.loads(request.data.decode()))
            return _Resp()

        buf = io.StringIO()
        with patch("pair.model.preload.resident_models", return_value=resident):
            with patch("pair.model.preload.open_json_request", opener):
                with patch("pair.model.preload._decode_idle", return_value=True):
                    with redirect_stdout(buf):
                        rewarm_pro_if_evicted()
        return seen, buf.getvalue()

    def test_hot_flash_missing_warms_flash(self) -> None:
        _cache(76)
        self.assertTrue(defer_nonurgent())
        seen, log = self._rewarm([])
        self.assertEqual([item["model"] for item in seen], ["qwen3:0.6b"])
        self.assertIn("rest: deferred pro rewarm", log)
        self.assertEqual(seen[0]["options"]["num_ctx"], 2048)
        self.assertEqual(seen[0]["options"]["num_batch"], 128)
        self.assertEqual(seen[0]["options"]["num_thread"], 4)
        self.assertEqual(seen[0]["keep_alive"], -1)

    def test_hot_pro_missing_does_not_warm(self) -> None:
        _cache(76)
        self.assertTrue(defer_nonurgent())
        seen, log = self._rewarm(["qwen3:0.6b"])
        self.assertEqual(seen, [])
        self.assertIn("rest: deferred pro rewarm", log)

    def test_cool_pro_missing_warms_pro(self) -> None:
        _cache(67)
        self.assertFalse(defer_nonurgent())
        seen, log = self._rewarm(["qwen3:0.6b"])
        self.assertEqual([item["model"] for item in seen], ["qwen3:1.7b"])
        self.assertNotIn("rest: deferred pro rewarm", log)
        self.assertEqual(seen[0]["keep_alive"], -1)


class UserProWarm(unittest.TestCase):
    def test_user_pro_chat_while_hot_warms_immediately(self) -> None:
        case = test_modes.ModeHttp("test_cold_pro_answers_on_flash_and_warms")
        case.setUp()
        _reset_latch()
        knobs = patch("pair.model.rest.inference_knobs", side_effect=_rest_on)
        knobs.start()
        try:
            _cache(76)
            self.assertTrue(defer_nonurgent())
            port = case._boot()
            status, _headers, body = case._post(
                port,
                {
                    "mode": "pro",
                    "messages": [
                        {"role": "user", "content": "cold pro while the board is hot"}
                    ],
                },
                {"X-Pi-Target": "pi4", "X-Pi-Mesh": "off"},
            )
            self.assertEqual(status, 200, body)
            self.assertTrue(defer_nonurgent())
            deadline = time.time() + 2
            while time.time() < deadline and not test_modes._posts("/api/generate"):
                time.sleep(0.02)
            warm = test_modes._posts("/api/generate")
            self.assertEqual(len(warm), 1)
            self.assertEqual(warm[0]["model"], "qwen3:1.7b")
            self.assertEqual(warm[0]["keep_alive"], -1)
            self.assertEqual(warm[0]["options"]["num_ctx"], 2048)
            self.assertEqual(warm[0]["options"]["num_batch"], 128)
            self.assertEqual(warm[0]["options"]["num_thread"], 4)
        finally:
            knobs.stop()
            _reset_latch()
            case.tearDown()


class RestCompaction(unittest.TestCase):
    def setUp(self) -> None:
        self._rest = os.environ.get("PI_PAIR_REST")
        self._role = os.environ.get("PI_PAIR_ROLE")
        os.environ.pop("PI_PAIR_REST", None)
        os.environ["PI_PAIR_ROLE"] = "brain"
        _reset_latch()
        self.turns = [{"role": "user", "content": "the locker code is 4182"}]
        self._knobs = patch("pair.model.rest.inference_knobs", side_effect=_rest_on)
        self._knobs.start()

    def tearDown(self) -> None:
        self._knobs.stop()
        _reset_latch()
        if self._rest is None:
            os.environ.pop("PI_PAIR_REST", None)
        else:
            os.environ["PI_PAIR_REST"] = self._rest
        if self._role is None:
            os.environ.pop("PI_PAIR_ROLE", None)
        else:
            os.environ["PI_PAIR_ROLE"] = self._role

    def test_hot_compaction_does_not_enqueue_and_cool_does(self) -> None:
        queued: list = []

        def capture(fn) -> bool:
            queued.append(fn)
            return True

        _cache(76)
        self.assertTrue(defer_nonurgent())
        buf = io.StringIO()
        with patch.object(runtime.gate, "enqueue_background", capture):
            with redirect_stdout(buf):
                self.assertFalse(
                    schedule(self.turns, 2048, idle=lambda: True, scope="rest-hot")
                )
        self.assertEqual(queued, [])
        self.assertIn("rest: deferred compaction", buf.getvalue())
        _cache(67)
        self.assertFalse(defer_nonurgent())
        buf = io.StringIO()
        with patch.object(runtime.gate, "enqueue_background", capture):
            with redirect_stdout(buf):
                self.assertTrue(
                    schedule(self.turns, 2048, idle=lambda: True, scope="rest-cool")
                )
        self.assertEqual(len(queued), 1)
        self.assertNotIn("rest: deferred compaction", buf.getvalue())

    def test_deferred_compaction_does_not_overflow_context(self) -> None:
        turns = []
        for index in range(30):
            turns.append(
                {
                    "role": "user",
                    "content": (
                        f"turn {index} talks about the weather and the locker. " * 40
                    ),
                }
            )
            turns.append({"role": "assistant", "content": "Noted. " * 30})
        turns.append({"role": "user", "content": "what was the code?"})
        raw = sum(estimate_tokens(str(row["content"])) for row in turns)
        self.assertGreater(raw, 2048)
        _cache(76)
        queued: list = []

        def capture(fn) -> bool:
            queued.append(fn)
            return True

        buf = io.StringIO()
        with patch.object(runtime.gate, "enqueue_background", capture):
            with redirect_stdout(buf):
                self.assertFalse(
                    schedule(turns, 2048, idle=lambda: True, scope="rest-fit")
                )
        self.assertEqual(queued, [])
        self.assertIn("rest: deferred compaction", buf.getvalue())
        shaped = shape_messages(turns, "what was the code?", knobs={"num_ctx": 2048})
        used = sum(estimate_tokens(str(row.get("content") or "")) for row in shaped)
        self.assertLessEqual(used, 2048)
        self.assertEqual(shaped[-1]["role"], "user")

    def test_dataset_role_at_80c_still_enqueues_compaction(self) -> None:
        os.environ["PI_PAIR_ROLE"] = "dataset"
        _cache(80)
        self.assertIsNone(_limits())
        self.assertFalse(defer_nonurgent())
        queued: list = []

        def capture(fn) -> bool:
            queued.append(fn)
            return True

        buf = io.StringIO()
        with patch.object(runtime.gate, "enqueue_background", capture):
            with redirect_stdout(buf):
                self.assertTrue(
                    schedule(self.turns, 2048, idle=lambda: True, scope="rest-pi3")
                )
        self.assertEqual(len(queued), 1)
        self.assertNotIn("rest: deferred compaction", buf.getvalue())


class RestOptions(unittest.TestCase):
    def setUp(self) -> None:
        self._rest = os.environ.get("PI_PAIR_REST")
        self._role = os.environ.get("PI_PAIR_ROLE")
        os.environ.pop("PI_PAIR_REST", None)
        os.environ["PI_PAIR_ROLE"] = "brain"
        _reset_latch()
        self._knobs = patch("pair.model.rest.inference_knobs", side_effect=_rest_on)
        self._knobs.start()

    def tearDown(self) -> None:
        self._knobs.stop()
        _reset_latch()
        if self._rest is None:
            os.environ.pop("PI_PAIR_REST", None)
        else:
            os.environ["PI_PAIR_REST"] = self._rest
        if self._role is None:
            os.environ.pop("PI_PAIR_ROLE", None)
        else:
            os.environ["PI_PAIR_ROLE"] = self._role

    def test_num_thread_batch_and_ctx_do_not_change_with_heat(self) -> None:
        _cache(67)
        cool = {
            "flash": runner_options("flash"),
            "pro": runner_options("pro"),
            "warm": pro_preload_payload("qwen3:1.7b")["options"],
            "chat": ollama_payload(
                "qwen3:0.6b",
                [{"role": "user", "content": "hi"}],
                0.7,
                32,
                False,
            )["options"],
        }
        _cache(76)
        self.assertTrue(defer_nonurgent())
        hot = {
            "flash": runner_options("flash"),
            "pro": runner_options("pro"),
            "warm": pro_preload_payload("qwen3:1.7b")["options"],
            "chat": ollama_payload(
                "qwen3:0.6b",
                [{"role": "user", "content": "hi"}],
                0.7,
                32,
                False,
            )["options"],
        }
        self.assertEqual(cool, hot)
        for row in (hot["flash"], hot["pro"], hot["warm"], hot["chat"]):
            self.assertEqual(row["num_ctx"], 2048)
            self.assertEqual(row["num_batch"], 128)
            self.assertEqual(row["num_thread"], 4)


class RestStartup(unittest.TestCase):
    def setUp(self) -> None:
        self._role = os.environ.get("PI_PAIR_ROLE")
        self._rest = os.environ.get("PI_PAIR_REST")
        self._peers = [dict(peer) for peer in runtime.PEERS]
        os.environ["PI_PAIR_ROLE"] = "brain"
        os.environ.pop("PI_PAIR_REST", None)
        _reset_latch()
        runtime.set_peers(
            [
                {
                    "name": "pi4",
                    "host": "127.0.0.1",
                    "port": 9,
                    "kind": "ollama",
                    "note": "",
                    "generative": True,
                    "role": "brain",
                }
            ]
        )

    def tearDown(self) -> None:
        _reset_latch()
        runtime.set_peers(self._peers)
        if self._role is None:
            os.environ.pop("PI_PAIR_ROLE", None)
        else:
            os.environ["PI_PAIR_ROLE"] = self._role
        if self._rest is None:
            os.environ.pop("PI_PAIR_REST", None)
        else:
            os.environ["PI_PAIR_REST"] = self._rest

    def test_startup_warms_each_tag_once(self) -> None:
        source = inspect.getsource(serve_main)
        self.assertLess(
            source.find("start_model_warm("), source.find("start_pro_warm(")
        )
        loaded: list[str] = []
        warms: list[tuple[str, str]] = []
        ps_seen = threading.Event()
        started = threading.Event()
        release = threading.Event()

        def urlopen(request, timeout=None):
            del timeout
            if isinstance(request, str):
                url = request
                payload = None
            else:
                url = request.full_url
                payload = json.loads(request.data.decode()) if request.data else None
            if url.rstrip("/").endswith("/api/ps"):
                ps_seen.set()
                body = {
                    "models": [{"name": name, "model": name} for name in list(loaded)]
                }
                return _Resp(body)
            model = str((payload or {}).get("model") or "")
            warms.append((url, model))
            if not started.is_set():
                started.set()
                release.wait(3)
            if model and model not in loaded:
                loaded.append(model)
            return _Resp({"message": {"content": "ok"}})

        try:
            with patch("urllib.request.urlopen", urlopen):
                with patch("pair.model.preload.REWARM_PAUSE_S", 3600):
                    start_model_warm()
                    start_pro_warm()
                    self.assertTrue(started.wait(2), "startup warm never asked Ollama")
                    self.assertFalse(
                        ps_seen.wait(0.4),
                        "watchdog checked before the startup warm finished",
                    )
                    release.set()
                    self.assertTrue(
                        ps_seen.wait(2), "watchdog did not check after warm"
                    )
                    pending = warm_in_flight()
                    if pending is not None:
                        pending.join(2)
                    time.sleep(0.05)
        finally:
            release.set()
        counts = {tag: 0 for tag in ("qwen3:0.6b", "qwen3:1.7b")}
        for url, model in warms:
            self.assertIn("/api/chat", url)
            self.assertNotIn("/api/generate", url)
            counts[model] = counts.get(model, 0) + 1
        self.assertEqual(counts["qwen3:0.6b"], 1)
        self.assertEqual(counts["qwen3:1.7b"], 1)


if __name__ == "__main__":
    unittest.main()
