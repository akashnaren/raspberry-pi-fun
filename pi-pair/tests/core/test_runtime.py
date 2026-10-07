"""One decode slot, a visible queue place, and a private temperature reading."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from pair.mesh.health import parse_temp_c, parse_throttled
from pair.model.sched import InferenceGate
from pair.routes.status import health_document, public_health
from tests.support.paths import ROOT


class OneSlot(unittest.TestCase):
    def test_the_second_reserve_waits_at_position_two(self):
        gate = InferenceGate(1, queue_limit=8, wait_timeout=2)
        status, _running = gate.reserve_ticket()
        self.assertEqual(status, "ready")
        status, waiting = gate.reserve_ticket()
        self.assertEqual(status, "wait")
        self.assertEqual(gate.position(waiting), 2)
        self.assertGreater(gate.eta_s(gate.position(waiting)), 0)

    def test_releasing_the_runner_hands_the_slot_to_the_waiter(self):
        gate = InferenceGate(1, queue_limit=8, wait_timeout=2)
        _status, running = gate.reserve_ticket()
        _status, waiting = gate.reserve_ticket()
        got: list[int] = []

        def take() -> None:
            self.assertTrue(gate.wait(waiting, timeout=2))
            got.append(gate.in_flight())
            gate.release(waiting)

        thread = threading.Thread(target=take)
        thread.start()
        time.sleep(0.05)
        gate.release(running)
        thread.join(2)
        self.assertEqual(got, [1])
        self.assertEqual(gate.in_flight(), 0)
        self.assertEqual(gate.waiting(), 0)


class Thermal(unittest.TestCase):
    def test_vcgencmd_lines_parse(self):
        self.assertEqual(parse_temp_c("temp=62.5'C\n"), 62.5)
        self.assertEqual(parse_throttled("throttled=0xe00008\n"), "0xe00008")
        self.assertIsNone(parse_temp_c(""))
        self.assertIsNone(parse_throttled("no flags"))

    def test_private_health_keeps_temp_and_the_public_copy_drops_it(self):
        with (
            patch("pair.routes.status.snapshot_peers", return_value=[]),
            patch(
                "pair.routes.status.board_thermal",
                return_value={"temp_c": 61.0, "throttled": "0x0"},
            ),
        ):
            doc = health_document()
        self.assertEqual(doc["temp_c"], 61.0)
        self.assertEqual(doc["throttled"], "0x0")
        self.assertNotIn("cooling", doc)
        shown = public_health(doc)
        self.assertNotIn("temp_c", shown)
        self.assertNotIn("throttled", shown)
        self.assertNotIn("cooling", shown)
        self.assertNotIn("temp_c", str(shown))

    def test_the_tree_has_no_heatsink_copy(self):
        targets = [
            ROOT / "pair",
            ROOT / "web" / "public" / "index.html",
            ROOT / "web" / "src",
            ROOT / "static" / "index.html",
        ]
        hits = []
        for target in targets:
            paths = [target] if target.is_file() else target.rglob("*")
            for path in paths:
                if not path.is_file() or path.suffix in {".png", ".woff2", ".jpg"}:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if "heatsink" in text.lower():
                    hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [])
