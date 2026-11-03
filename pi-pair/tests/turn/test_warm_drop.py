"""Warm-drop regressions. Each one fails on 40388975."""

from __future__ import annotations

import io
import json
import os
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from pair.core import runtime
from pair.core.cancel import ClientGone
from pair.model import chat_once
from pair.model.chat_once import (
    begin_turn,
    close_warm,
    drop_warm_slot,
    end_turn,
    hold_warm_slot,
    join_startup_retry,
    warm_chat_model,
    warm_residents,
    warm_token,
)
from pair.model.modes import FLASH_MODEL, PRO_MODEL
from pair.model.preload import (
    REWARM_PAUSE_S,
    join_pro_warms,
    rewarm_pro_if_evicted,
    schedule_pro_warm,
    warm_model,
)


class _Body:
    def __init__(self, raw: bytes):
        self._raw = raw

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _peer() -> dict:
    return {
        "name": "pi4",
        "host": "127.0.0.1",
        "port": 9,
        "kind": "ollama",
        "generative": True,
        "role": "brain",
    }


def _drain_gate() -> None:
    while runtime.gate.in_flight():
        runtime.gate.release()
    while runtime.gate.waiting():
        ticket = runtime.gate._queue[0]
        runtime.gate._local.ticket = ticket
        runtime.gate.cancel_wait()


class WarmDrop(unittest.TestCase):
    def tearDown(self):
        join_startup_retry()
        join_pro_warms()
        drop_warm_slot()
        _drain_gate()
        with chat_once._GUARD:
            chat_once._TURNS = 0
            chat_once._WARM_HELD = False
            if hasattr(chat_once, "_WARM_CLOSE_TIMED_OUT"):
                chat_once._WARM_CLOSE_TIMED_OUT = False

    def test_a_busy_decode_gate_blocks_the_warm_and_retries(self):
        self.assertTrue(runtime.gate.try_acquire())
        status, ticket = runtime.gate.reserve_ticket("warm-drop")
        self.assertEqual(status, "wait")
        seen: list[str] = []

        def open_json(url, payload, timeout, cancel=None):
            del url, timeout, cancel
            seen.append(payload.get("model"))
            return _Body(json.dumps({"message": {"content": "ok"}}).encode())

        try:
            held = hold_warm_slot()
            self.assertIsNone(held)
            if held is not None:
                drop_warm_slot()
            runtime.gate.release()
            self.assertEqual(runtime.gate.in_flight(), 0)
            self.assertGreater(runtime.gate.waiting(), 0)
            held = hold_warm_slot()
            self.assertIsNone(held)
            if held is not None:
                drop_warm_slot()
            runtime.gate._local.ticket = ticket
            runtime.gate.cancel_wait()
            self.assertEqual(runtime.gate.waiting(), 0)
            self.assertTrue(runtime.gate.try_acquire())
            with patch("pair.model.chat_once.open_json", open_json):
                loaded = warm_residents(_peer(), timeout=1)
                time.sleep(0.15)
                self.assertEqual(seen, [])
                self.assertEqual(loaded, [])
                runtime.gate.release()
                deadline = time.time() + 2
                while time.time() < deadline and PRO_MODEL not in seen:
                    time.sleep(0.02)
            self.assertIn(FLASH_MODEL, seen)
            self.assertIn(PRO_MODEL, seen)
        finally:
            _drain_gate()
            drop_warm_slot()

    def test_a_gate_taken_during_the_claim_does_not_post(self):
        entered = threading.Event()
        release = threading.Event()
        real_idle = chat_once._decode_idle
        state = {"stalled": False}
        posts: list[str] = []
        holder: dict = {}

        def idle() -> bool:
            ok = real_idle()
            if ok and not state["stalled"]:
                state["stalled"] = True
                entered.set()
                self.assertTrue(release.wait(2))
                return True
            return real_idle()

        def open_json(url, payload, timeout, cancel=None):
            del url, timeout, cancel
            posts.append(str(payload.get("model")))
            return _Body(json.dumps({"message": {"content": "ok"}}).encode())

        real_hold = chat_once.hold_warm_slot

        def spy_hold():
            slot = real_hold()
            holder["slot"] = slot
            return slot

        def run() -> None:
            holder["ok"] = warm_chat_model(_peer(), PRO_MODEL, timeout=1)

        try:
            with patch("pair.model.chat_once._decode_idle", idle):
                with patch("pair.model.chat_once.hold_warm_slot", spy_hold):
                    with patch("pair.model.chat_once.open_json", open_json):
                        thread = threading.Thread(target=run)
                        thread.start()
                        self.assertTrue(entered.wait(2))
                        self.assertTrue(runtime.gate.try_acquire())
                        release.set()
                        thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertIsNone(holder.get("slot"))
            self.assertFalse(holder.get("ok"))
            self.assertEqual(posts, [])
            self.assertFalse(chat_once.warm_slot_busy())
        finally:
            release.set()
            _drain_gate()
            drop_warm_slot()

    def test_a_lane_timeout_puts_the_models_back(self):
        seen: list[str] = []

        def open_json(url, payload, timeout, cancel=None):
            del url, timeout, cancel
            seen.append(payload.get("model"))
            return _Body(json.dumps({"message": {"content": "ok"}}).encode())

        turn = begin_turn()
        try:
            with patch("pair.model.chat_once.open_json", open_json):
                chat_once._queue_warm_retry(_peer(), [FLASH_MODEL, PRO_MODEL], 0.05)
                time.sleep(0.25)
                self.assertEqual(seen, [])
                thread = chat_once._RETRY_THREAD
                self.assertIsNotNone(thread)
                self.assertTrue(thread.is_alive())
                end_turn(turn)
                turn = None
                deadline = time.time() + 2
                while time.time() < deadline and seen != [FLASH_MODEL, PRO_MODEL]:
                    time.sleep(0.02)
            self.assertEqual(seen, [FLASH_MODEL, PRO_MODEL])
        finally:
            end_turn(turn)

    def test_a_turn_that_ends_during_the_warm_requeues_it(self):
        seen: list[str] = []
        entered = threading.Event()
        release = threading.Event()

        def open_json(url, payload, timeout, cancel=None):
            del url, timeout, cancel
            seen.append(payload.get("model"))
            if len(seen) == 1:
                entered.set()
                self.assertTrue(release.wait(3))
                raise TimeoutError("closed")
            return _Body(json.dumps({"message": {"content": "ok"}}).encode())

        holder: dict = {}

        def run() -> None:
            holder["loaded"] = warm_residents(_peer(), timeout=1)

        try:
            with patch("pair.model.chat_once.WARM_CLOSE_WAIT_S", 0.05):
                with patch("pair.model.chat_once.open_json", open_json):
                    thread = threading.Thread(target=run)
                    thread.start()
                    self.assertTrue(entered.wait(2))
                    turn = begin_turn()
                    end_turn(turn)
                    release.set()
                    thread.join(2)
                    self.assertFalse(thread.is_alive())
                    deadline = time.time() + 2
                    while time.time() < deadline and seen.count(FLASH_MODEL) < 2:
                        time.sleep(0.02)
            self.assertGreaterEqual(seen.count(FLASH_MODEL), 2)
            self.assertEqual(holder.get("loaded"), [])
        finally:
            release.set()

    def test_a_warm_posts_with_the_token_the_slot_returns(self):
        posted = []
        real_hold = chat_once.hold_warm_slot

        def swap_then_hold():
            close_warm()
            return real_hold()

        def open_json(url, payload, timeout, cancel=None):
            del url, payload, timeout
            posted.append(cancel)
            self.assertIs(cancel, warm_token())
            self.assertFalse(cancel.gone())
            return _Body(json.dumps({"message": {"content": "ok"}}).encode())

        with patch("pair.model.chat_once.hold_warm_slot", swap_then_hold):
            with patch("pair.model.chat_once.open_json", open_json):
                self.assertTrue(warm_chat_model(_peer(), FLASH_MODEL, timeout=1))
        self.assertEqual(len(posted), 1)
        self.assertFalse(chat_once.warm_slot_busy())

    def test_a_user_pro_warm_waits_until_the_lane_is_idle(self):
        from pair.model import preload

        posts: list[str] = []

        def open_json(url, payload, timeout, cancel=None):
            del url, timeout, cancel
            posts.append(payload.get("model"))
            return _Body(b"{}")

        self.assertTrue(runtime.gate.try_acquire())
        try:
            with patch("pair.model.preload.open_json", open_json):
                with patch.object(preload, "PRELOAD_TIMEOUT_S", 0.05):
                    schedule_pro_warm("127.0.0.1", 9, PRO_MODEL)
                    time.sleep(1.0)
                    self.assertEqual(posts, [])
                    runtime.gate.release()
                    deadline = time.time() + 2
                    while time.time() < deadline and not posts:
                        time.sleep(0.02)
            self.assertEqual(posts, [PRO_MODEL])
        finally:
            _drain_gate()

    def test_a_user_pro_warm_retries_a_lost_hold(self):
        posts: list[str] = []
        clock = {"now": time.monotonic()}
        real_sleep = time.sleep

        def fake_mono() -> float:
            return clock["now"]

        def fake_sleep(seconds: float) -> None:
            del seconds
            clock["now"] += 50.0
            real_sleep(0.005)

        def open_json(url, payload, timeout, cancel=None):
            del url, timeout, cancel
            posts.append(payload.get("model"))
            return _Body(b"{}")

        held = hold_warm_slot()
        self.assertIsNotNone(held)
        try:
            with patch("pair.model.preload.time.monotonic", fake_mono):
                with patch("pair.model.preload.time.sleep", fake_sleep):
                    with patch("pair.model.preload.open_json", open_json):
                        schedule_pro_warm("127.0.0.1", 9, PRO_MODEL)
                        real_sleep(0.4)
                        self.assertEqual(posts, [])
                        drop_warm_slot()
                        held = None
                        deadline = time.time() + 2
                        while time.time() < deadline and not posts:
                            real_sleep(0.02)
            self.assertEqual(posts, [PRO_MODEL])
        finally:
            if held is not None:
                drop_warm_slot()

    def test_a_cancelled_pro_warm_posts_once_after_the_turn(self):
        posts: list[str] = []
        entered = threading.Event()

        def open_json(url, payload, timeout, cancel=None):
            del url, timeout
            posts.append(str(payload.get("model")))
            if len(posts) == 1:
                entered.set()
                deadline = time.time() + 2
                while time.time() < deadline:
                    if cancel is not None and cancel.gone():
                        raise ClientGone()
                    time.sleep(0.01)
                raise ClientGone()
            return _Body(b"{}")

        turn = None
        try:
            with patch("pair.model.preload.open_json", open_json):
                schedule_pro_warm("127.0.0.1", 9, PRO_MODEL)
                schedule_pro_warm("127.0.0.1", 9, PRO_MODEL)
                self.assertTrue(entered.wait(2))
                names = [
                    thread.name
                    for thread in threading.enumerate()
                    if thread.name == "pro-rewarm"
                ]
                self.assertEqual(names, ["pro-rewarm"])
                turn = begin_turn()
                end_turn(turn)
                turn = None
                deadline = time.time() + 2
                while time.time() < deadline and len(posts) < 2:
                    time.sleep(0.02)
                self.assertEqual(posts, [PRO_MODEL, PRO_MODEL])
                time.sleep(0.3)
                self.assertEqual(posts, [PRO_MODEL, PRO_MODEL])
        finally:
            end_turn(turn)

    def test_a_cancelled_watchdog_warm_is_logged_and_retried(self):
        posts: list[str] = []

        def open_json_request(request, timeout, cancel=None):
            del timeout, cancel
            posts.append("watch")
            raise ClientGone()

        def open_json(url, payload, timeout, cancel=None):
            del url, timeout, cancel
            posts.append(str(payload.get("model")))
            return _Body(b"{}")

        buf = io.StringIO()
        with patch.dict(os.environ, {"PI_PAIR_ROLE": "brain"}):
            with patch("pair.model.preload.open_json_request", open_json_request):
                with patch("pair.model.preload.open_json", open_json):
                    with redirect_stdout(buf):
                        warm_model(PRO_MODEL)
                    deadline = time.time() + 2
                    while time.time() < deadline and PRO_MODEL not in posts:
                        time.sleep(0.02)
        text = buf.getvalue()
        self.assertIn(PRO_MODEL, text)
        self.assertIn("cancelled", text.lower())
        self.assertEqual(posts, ["watch", PRO_MODEL])

    def test_two_interruptions_leave_the_reload_to_the_watchdog(self):
        posts: list[str] = []

        def open_json(url, payload, timeout, cancel=None):
            del url, timeout, cancel
            posts.append(str(payload.get("model")))
            raise ClientGone()

        with patch("pair.model.preload.open_json", open_json):
            schedule_pro_warm("127.0.0.1", 9, PRO_MODEL)
            deadline = time.time() + 3
            while time.time() < deadline and len(posts) < 2:
                time.sleep(0.02)
            self.assertEqual(posts, [PRO_MODEL, PRO_MODEL])
            time.sleep(0.4)
            self.assertEqual(posts, [PRO_MODEL, PRO_MODEL])

    def test_five_schedules_post_at_most_twice(self):
        posts: list[str] = []

        def open_json(url, payload, timeout, cancel=None):
            del url, timeout, cancel
            posts.append(str(payload.get("model")))
            raise ClientGone()

        def settled() -> bool:
            return not any(
                thread.name == "pro-rewarm" for thread in threading.enumerate()
            )

        with patch("pair.model.preload.open_json", open_json):
            for _ in range(5):
                before = len(posts)
                schedule_pro_warm("127.0.0.1", 9, PRO_MODEL)
                deadline = time.time() + 4
                while time.time() < deadline:
                    if settled() and (len(posts) > before or len(posts) >= 2):
                        break
                    time.sleep(0.02)
                self.assertTrue(settled())
        self.assertEqual(posts, [PRO_MODEL, PRO_MODEL])

    def test_watchdog_loads_pro_after_the_pause(self):
        self.assertEqual(REWARM_PAUSE_S, 120.0)
        posts: list[str] = []
        clock = {"extra": 0.0}
        real = time.monotonic

        def monotonic() -> float:
            return real() + clock["extra"]

        def open_json(url, payload, timeout, cancel=None):
            del url, timeout, cancel
            posts.append(str(payload.get("model")))
            raise ClientGone()

        def open_json_request(request, timeout, cancel=None):
            del timeout, cancel
            payload = json.loads(request.data.decode())
            posts.append("watch:" + str(payload.get("model")))
            return _Body(b"{}")

        def settled() -> bool:
            return not any(
                thread.name == "pro-rewarm" for thread in threading.enumerate()
            )

        with patch("pair.model.preload.time.monotonic", monotonic):
            with patch("pair.model.preload.open_json", open_json):
                schedule_pro_warm("127.0.0.1", 9, PRO_MODEL)
                deadline = time.time() + 4
                while time.time() < deadline and len(posts) < 2:
                    time.sleep(0.02)
                self.assertEqual(posts, [PRO_MODEL, PRO_MODEL])
                deadline = time.time() + 2
                while time.time() < deadline and not settled():
                    time.sleep(0.02)
                self.assertTrue(settled())
                with patch.dict(os.environ, {"PI_PAIR_ROLE": "brain"}):
                    with patch(
                        "pair.model.preload.resident_models",
                        return_value=[FLASH_MODEL],
                    ):
                        with patch(
                            "pair.model.preload.open_json_request",
                            open_json_request,
                        ):
                            with patch(
                                "pair.model.preload._decode_idle",
                                return_value=True,
                            ):
                                with patch(
                                    "pair.model.preload.defer_nonurgent",
                                    return_value=False,
                                ):
                                    rewarm_pro_if_evicted()
                                    self.assertEqual(posts, [PRO_MODEL, PRO_MODEL])
                                    clock["extra"] = REWARM_PAUSE_S + 1
                                    schedule_pro_warm("127.0.0.1", 9, PRO_MODEL)
                                    time.sleep(0.8)
                                    self.assertTrue(settled())
                                    self.assertEqual(posts, [PRO_MODEL, PRO_MODEL])
                                    rewarm_pro_if_evicted()
        self.assertEqual(posts, [PRO_MODEL, PRO_MODEL, "watch:" + PRO_MODEL])

    def test_a_hot_board_does_not_retry_a_cancelled_pro_warm(self):
        posts: list[str] = []

        def open_json_request(request, timeout, cancel=None):
            del request, timeout, cancel
            posts.append("watch")
            raise ClientGone()

        def open_json(url, payload, timeout, cancel=None):
            del url, payload, timeout, cancel
            posts.append("retry")
            return _Body(b"{}")

        buf = io.StringIO()
        with patch.dict(os.environ, {"PI_PAIR_ROLE": "brain"}):
            with patch("pair.model.preload.open_json_request", open_json_request):
                with patch("pair.model.preload.open_json", open_json):
                    with patch("pair.model.preload.defer_nonurgent", return_value=True):
                        with redirect_stdout(buf):
                            warm_model(PRO_MODEL)
                        deadline = time.time() + 1.2
                        while time.time() < deadline:
                            self.assertNotIn("retry", posts)
                            time.sleep(0.05)
        self.assertEqual(posts, ["watch"])
        self.assertIn("deferred", buf.getvalue().lower())

    def test_schedule_pro_warm_dedupes_one_tag(self):
        entered = threading.Event()
        release = threading.Event()

        def open_json(url, payload, timeout, cancel=None):
            del url, payload, timeout, cancel
            entered.set()
            release.wait(3)
            return _Body(b"{}")

        try:
            with patch("pair.model.preload.open_json", open_json):
                schedule_pro_warm("127.0.0.1", 9, PRO_MODEL)
                schedule_pro_warm("127.0.0.1", 9, PRO_MODEL)
                self.assertTrue(entered.wait(2))
                names = [
                    thread.name
                    for thread in threading.enumerate()
                    if thread.name == "pro-rewarm"
                ]
                self.assertEqual(names, ["pro-rewarm"])
        finally:
            release.set()

    def test_warm_model_logs_when_the_slot_or_the_hold_fails(self):
        buf = io.StringIO()
        env = {"PI_PAIR_ROLE": "brain"}
        held = hold_warm_slot()
        self.assertIsNotNone(held)
        try:
            with patch.dict(os.environ, env):
                with redirect_stdout(buf):
                    warm_model(PRO_MODEL)
        finally:
            drop_warm_slot()
        slot_log = buf.getvalue()
        self.assertIn(PRO_MODEL, slot_log)
        self.assertIn("busy", slot_log.lower())

        buf = io.StringIO()
        turn = begin_turn()
        try:
            with patch.dict(os.environ, env):
                with redirect_stdout(buf):
                    warm_model(FLASH_MODEL)
        finally:
            end_turn(turn)
        hold_log = buf.getvalue()
        self.assertIn(FLASH_MODEL, hold_log)
        self.assertIn("busy", hold_log.lower())

    def test_only_the_first_turn_waits_out_a_stuck_warm(self):
        held = hold_warm_slot()
        self.assertIsNotNone(held)
        first_turn = None
        second_turn = None
        try:
            with patch("pair.model.chat_once.WARM_CLOSE_WAIT_S", 0.05):
                started = time.perf_counter()
                first_turn = begin_turn()
                first = time.perf_counter() - started
                end_turn(first_turn)
                first_turn = None
                started = time.perf_counter()
                second_turn = begin_turn()
                second = time.perf_counter() - started
                end_turn(second_turn)
                second_turn = None
            self.assertGreaterEqual(first, 0.04)
            self.assertLess(second, 0.025)
            self.assertTrue(chat_once.warm_slot_busy())
        finally:
            end_turn(first_turn)
            end_turn(second_turn)
            drop_warm_slot()


if __name__ == "__main__":
    unittest.main()
