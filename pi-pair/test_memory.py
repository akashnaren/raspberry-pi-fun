"""Token ledger, idle compaction, and facts that survive two folds."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest

from pair import memory
from pair.compact import plan, run_compact, schedule, should_compact
from pair.context import Ledger, ledger_for, reset, verbatim_budget
from pair.turn import estimate_tokens, shape_messages


class LedgerTests(unittest.TestCase):
    def test_used_stays_within_five_percent_of_the_model_count(self):
        text = "a" * 320
        guess = estimate_tokens(text)
        book = Ledger(2048)
        book.note_estimate(text)
        reported = 103
        self.assertLess(abs(guess - reported) / reported, 0.05)
        book.observe(reported)
        self.assertTrue(book.within(reported))
        self.assertFalse(book.within(120))
        self.assertEqual(book.used(), reported)

    def test_prefix_is_stable_until_compaction(self):
        turns = [
            {"role": "user", "content": "What is the capital of Australia?"},
            {"role": "assistant", "content": "Canberra."},
            {"role": "user", "content": "And who wrote Pride and Prejudice?"},
        ]
        facts = "user said the locker code is 4182"
        first = shape_messages(
            turns, turns[-1]["content"], facts=facts, summary="Canberra."
        )
        second = shape_messages(
            turns, turns[-1]["content"], facts=facts, summary="Canberra."
        )
        self.assertEqual(first, second)
        self.assertEqual(first[0]["role"], "system")
        self.assertEqual(first[1]["content"], facts)
        self.assertTrue(str(first[2]["content"]).startswith("Summary:"))


class CompactTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._data = os.environ.get("PI_PAIR_DATA")
        os.environ["PI_PAIR_DATA"] = self._tmp.name
        reset()

    def tearDown(self):
        if self._data is None:
            os.environ.pop("PI_PAIR_DATA", None)
        else:
            os.environ["PI_PAIR_DATA"] = self._data
        self._tmp.cleanup()
        reset()

    def test_compaction_does_not_run_while_decode_is_active(self):
        turns = [{"role": "user", "content": "hello"}]
        self.assertFalse(schedule(turns, 2048, idle=lambda: False))
        busy = run_compact(turns, 2048, idle=lambda: False)
        self.assertFalse(busy["ok"])
        self.assertEqual(busy["reason"], "busy")
        self.assertEqual(memory.list_facts(), [])

    def test_summary_waits_until_the_slot_is_free(self):
        import time

        from pair import runtime
        from pair.sched import InferenceGate

        previous = runtime.gate
        gate = InferenceGate(1, queue_limit=4)
        runtime.gate = gate
        started = threading.Event()
        try:
            _status, holder = gate.reserve_ticket("hold")
            turns = [{"role": "user", "content": "the locker code is 4182"}]
            for index in range(8):
                turns.append(
                    {"role": "user", "content": f"turn {index} talks about the weather"}
                )

            def generate(draft: str) -> str:
                started.set()
                return draft

            self.assertTrue(schedule(turns, 64, idle=lambda: True, generate=generate))
            time.sleep(0.05)
            self.assertFalse(started.is_set())
            self.assertEqual(memory.list_facts(), [])
            gate.release(holder)
            self.assertTrue(started.wait(1))
            deadline = time.perf_counter() + 1
            while time.perf_counter() < deadline and not memory.list_facts():
                time.sleep(0.02)
            self.assertTrue(any("4182" in row["text"] for row in memory.list_facts()))
        finally:
            runtime.gate = previous

    def test_forty_turns_keep_the_needle_after_two_compactions(self):
        needle = "the locker code is 4182"
        turns = [{"role": "user", "content": needle}]
        for index in range(39):
            turns.append(
                {"role": "user", "content": f"turn {index} talks about the weather"}
            )
            turns.append({"role": "assistant", "content": "Noted."})
        baseline = 1 if needle in plan(turns, 512)["facts"][0] else 0
        self.assertEqual(baseline, 1)
        kept = 0
        for _ in range(2):
            result = run_compact(
                turns, 512, idle=lambda: True, generate=lambda draft: draft
            )
            self.assertTrue(result["ok"])
            memory.remember_user(result["facts"])
            memory.save_summary(result["summary"], result["elapsed_ms"])
            turns = result["keep"] + [{"role": "user", "content": "what was the code?"}]
        recalled = any("4182" in row["text"] for row in memory.list_facts())
        kept = 1 if recalled else 0
        self.assertGreaterEqual(kept / baseline, 0.9)
        self.assertLessEqual(estimate_tokens(memory.facts_block()), 150)
        self.assertTrue(should_compact(400, 512, busy=False))
        self.assertFalse(should_compact(100, 512, busy=False))
        self.assertTrue(should_compact(300, 512, busy=True))
        self.assertLessEqual(verbatim_budget(512), 512 * 0.4 + 1)

    def test_delete_and_clear(self):
        memory.remember_user(["the code is 4182"])
        rows = memory.list_facts()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["text"].startswith("user said"))
        self.assertTrue(memory.delete_fact(rows[0]["id"]))
        self.assertEqual(memory.list_facts(), [])
        memory.remember_user(["again"])
        memory.clear_facts()
        self.assertEqual(memory.list_facts(), [])
        book = ledger_for("default", 2048)
        book.observe(10)
        self.assertEqual(memory.stats()["used"], 10)
        self.assertGreaterEqual(memory.stats()["compactions"], 0)


if __name__ == "__main__":
    unittest.main()
