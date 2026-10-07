"""Fairness, wait estimates, backpressure, and heat for the one decode slot."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from pair.model.sched import InferenceGate
from pair.model.sched import (
    admission_report,
    background_cancel_report,
    client_report,
    estimate_report,
    fairness_report,
    handoff_report,
    heat_report,
    tool_job_report,
)
from pair.mesh.tools import generation_routes


class SchedulerReports(unittest.TestCase):
    def test_six_clients_stay_within_two_turns(self):
        report = fairness_report()
        self.assertLessEqual(report["max_gap"], 2, report)
        self.assertTrue(report["ok"], report)

    def test_wait_estimates_stay_within_thirty_percent(self):
        report = estimate_report()
        self.assertLessEqual(report["error"], 0.30, report)
        self.assertLessEqual(report["ahead_error"], 0.30, report)
        self.assertTrue(report["ok"], report)

    def test_a_long_eta_waits_until_eight_people_are_queued(self):
        report = admission_report()
        self.assertEqual(report["queued"], 8)
        self.assertEqual(report["ninth"], "full")
        self.assertEqual(report["long_wait"], "wait")
        self.assertGreaterEqual(report["long_position"], 2)
        self.assertGreater(report["long_eta_s"], 180)
        self.assertGreater(report["long_retry_after_s"], 180)
        self.assertTrue(report["ok"], report)

    def test_one_client_may_queue_two(self):
        report = client_report()
        self.assertEqual(report["blocked"], "full")
        self.assertEqual(report["other"], "wait")
        self.assertTrue(report["ok"], report)

    def test_heat_and_handoff(self):
        heat = heat_report()
        self.assertEqual(heat["warm_delay_s"], 0)
        self.assertEqual(heat["hot_delay_s"], 5)
        self.assertTrue(heat["ok"], heat)
        handoff = handoff_report()
        self.assertTrue(handoff["ok"], handoff)
        self.assertEqual(generation_routes(), [])

    def test_wait_serves_clients_in_turn(self):
        gate = InferenceGate(1, queue_limit=8, wait_timeout=3)
        gate.note_rates(1_000_000, 1_000_000, 1, 1)
        status, holder = gate.reserve_ticket("hold")
        self.assertEqual(status, "ready")
        tickets = []
        for name in ("A", "A", "B"):
            status, ticket = gate.reserve_ticket(name)
            self.assertEqual(status, "wait")
            tickets.append(ticket)
        self.assertEqual(gate.queued_clients(), ["A", "B", "A"])
        order: list[str] = []

        def run(ticket) -> None:
            self.assertTrue(gate.wait(ticket, timeout=3))
            order.append(ticket.client)
            gate.release(ticket)

        threads = [threading.Thread(target=run, args=(ticket,)) for ticket in tickets]
        for thread in threads:
            thread.start()
        time.sleep(0.05)
        gate.release(holder)
        for thread in threads:
            thread.join(3)
        self.assertEqual(order, ["A", "B", "A"])
        self.assertEqual(gate.in_flight(), 0)

    def test_a_hot_board_delays_the_next_decode(self):
        gate = InferenceGate(1, queue_limit=4, wait_timeout=3)
        _status, holder = gate.reserve_ticket("hot")
        _status, waiter = gate.reserve_ticket("next")
        slept: list[float] = []

        def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        with (
            patch("pair.model.sched.heat_delay_s", return_value=5),
            patch("pair.model.sched.time.sleep", fake_sleep),
        ):

            def run() -> None:
                self.assertTrue(gate.wait(waiter, timeout=3))
                gate.release(waiter)

            thread = threading.Thread(target=run)
            thread.start()
            time.sleep(0.05)
            gate.release(holder)
            thread.join(3)
        self.assertEqual([item for item in slept if item >= 1], [5])
        self.assertFalse(thread.is_alive())

    def test_background_waits_and_cancels_within_200ms(self):
        gate = InferenceGate(1, queue_limit=4)
        started = threading.Event()
        _status, holder = gate.reserve_ticket("hold")

        def wait_for_idle(cancel) -> None:
            started.set()
            cancel.wait(0)

        self.assertTrue(gate.enqueue_background(wait_for_idle))
        time.sleep(0.05)
        self.assertFalse(started.is_set())
        self.assertFalse(gate.enqueue_background(lambda _cancel: None))
        gate.release(holder)
        self.assertTrue(started.wait(1))
        report = background_cancel_report()
        self.assertTrue(report["cancelled"], report)
        self.assertLess(report["elapsed_s"], 0.2, report)
        self.assertFalse(report["second_while_busy"], report)
        self.assertTrue(report["ok"], report)
        tools = tool_job_report()
        self.assertTrue(tools["ok"], tools)
        self.assertEqual(tools["routes"], [])
