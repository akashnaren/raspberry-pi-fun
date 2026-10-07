"""Token ledger, idle compaction, and facts that survive two folds."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from pair import memory, runtime
from pair.compact import plan, run_compact, schedule, should_compact
from pair.context import Ledger, ledger_for, reset, verbatim_budget
from pair.memory import scope_key
from pair.server import make_server
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
        self.assertTrue(rows[0]["text"].startswith("The user said:"))
        self.assertTrue(memory.delete_fact(rows[0]["id"]))
        self.assertEqual(memory.list_facts(), [])
        memory.remember_user(["again"])
        memory.clear_facts()
        self.assertEqual(memory.list_facts(), [])
        book = ledger_for("default", 2048)
        book.observe(10)
        self.assertEqual(memory.stats()["used"], 10)
        self.assertGreaterEqual(memory.stats()["compactions"], 0)

    def test_filler_summary_keeps_the_locker_code(self):
        turns = []
        for index in range(12):
            turns.append(
                {"role": "user", "content": f"day {index} talks about the weather"}
            )
            turns.append({"role": "assistant", "content": "Noted."})
        turns.append({"role": "user", "content": "My locker code is 4417."})
        turns.append({"role": "assistant", "content": "Noted."})
        for index in range(12):
            turns.append(
                {
                    "role": "user",
                    "content": f"later day {index} talks about the weather",
                }
            )
            turns.append({"role": "assistant", "content": "Noted."})
        result = run_compact(
            turns,
            256,
            idle=lambda: True,
            generate=lambda _draft: "The user talked about the weather for many days.",
        )
        self.assertTrue(result["ok"])
        self.assertIn("4417", result["summary"])
        self.assertIn("My locker code is 4417.", result["facts"])
        memory.remember_user(result["facts"], scope="chat-a")
        memory.save_summary(result["summary"], result["elapsed_ms"], scope="chat-a")
        asked = "What is my locker code?"
        shaped = shape_messages(
            [{"role": "user", "content": asked}],
            asked,
            facts=memory.facts_block("chat-a"),
            summary=memory.summary_text("chat-a"),
        )
        blob = "\n".join(str(row["content"]) for row in shaped)
        self.assertIn("4417", blob)
        self.assertIn("The user said:", blob)
        other = shape_messages(
            [{"role": "user", "content": asked}],
            asked,
            facts=memory.facts_block("chat-b"),
            summary=memory.summary_text("chat-b"),
        )
        other_blob = "\n".join(str(row["content"]) for row in other)
        self.assertNotIn("4417", other_blob)

    def test_a_later_number_replaces_the_earlier_line(self):
        folded = plan(
            [
                {"role": "user", "content": "The locker code is 1111."},
                {"role": "user", "content": "The locker code is 4417."},
                {"role": "user", "content": "thanks for the weather update today"},
                {"role": "assistant", "content": "Glad to help with that."},
            ],
            64,
        )
        joined = "\n".join(folded["facts"])
        self.assertIn("4417", joined)
        self.assertNotIn("1111", joined)
        memory.remember_user(
            ["the locker code is 1111", "the locker code is 4417"],
            scope="codes",
        )
        stored = " ".join(row["text"] for row in memory.list_facts("codes"))
        self.assertIn("4417", stored)
        self.assertNotIn("1111", stored)

    def test_idle_memory_files_expire_and_facts_json_is_removed(self):
        import time
        from pathlib import Path

        memory.remember_user(["the locker code is 4417"], scope="keep-me")
        memory.remember_user(["an old note"], scope="stale-chat")
        folder = Path(self._tmp.name) / "memory"
        stale = folder / "stale-chat.json"
        old = time.time() - memory.MEMORY_TTL_S - 30
        os.utime(stale, (old, old))
        (folder / "facts.json").write_text("{}\n", encoding="utf-8")
        removed = memory.purge_memory()
        self.assertGreaterEqual(removed, 2)
        self.assertFalse((folder / "facts.json").exists())
        self.assertFalse(stale.exists())
        kept = " ".join(row["text"] for row in memory.list_facts("keep-me"))
        self.assertIn("4417", kept)
        self.assertIn("The user said:", kept)

    def test_a_read_does_not_extend_expiry_and_the_sweep_is_hourly(self):
        import time
        from pathlib import Path

        memory._last_purge = 0
        try:
            memory.remember_user(["the locker code is 4417"], scope="fresh-chat")
            folder = Path(self._tmp.name) / "memory"
            path = folder / "fresh-chat.json"
            written = time.time() - 100
            os.utime(path, (written, written))
            memory.list_facts("fresh-chat")
            memory.summary_text("fresh-chat")
            memory.facts_block("fresh-chat")
            memory.stats()
            self.assertEqual(path.stat().st_mtime, written)
            self.assertIn("4417", memory.list_facts("fresh-chat")[0]["text"])
            expired = time.time() - memory.MEMORY_TTL_S - 10
            os.utime(path, (expired, expired))
            self.assertIn("4417", memory.list_facts("fresh-chat")[0]["text"])
            self.assertTrue(path.exists())
            memory._last_purge = time.time() - memory.PURGE_INTERVAL_S - 1
            self.assertEqual(memory.list_facts("fresh-chat"), [])
            self.assertFalse(path.exists())
        finally:
            memory._last_purge = 0

    def test_two_sessions_cannot_read_or_clear_each_other(self):
        alice = scope_key("chat-a", "client-a")
        bob = scope_key("chat-b", "client-b")
        memory.remember_user(["the locker code is 4182"], scope=alice)
        memory.save_summary("code 4182", 12, scope=alice)
        memory.remember_user(["the dog is named Biscuit"], scope=bob)
        memory.remember_user(["the default secret is 9991"])
        self.assertTrue(any("4182" in row["text"] for row in memory.list_facts(alice)))
        self.assertFalse(any("4182" in row["text"] for row in memory.list_facts(bob)))
        memory.clear_facts(bob)
        memory.clear_facts("")
        self.assertTrue(any("4182" in row["text"] for row in memory.list_facts(alice)))
        self.assertIn("4182", memory.summary_text(alice))
        self.assertEqual(memory.list_facts(""), [])
        self.assertEqual(memory.summary_text(""), "")
        httpd = make_server("127.0.0.1", 0)
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        thread.start()
        try:
            host, port = httpd.server_address
            base = f"http://{host}:{port}/v1/memory"

            def fetch(headers=None, method="GET"):
                req = urllib.request.Request(base, headers=headers or {}, method=method)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return json.loads(resp.read().decode())

            seen = fetch({"X-Pi-Chat": "chat-a", "X-Pi-Client": "client-a"})
            self.assertTrue(any("4182" in row["text"] for row in seen["facts"]))
            self.assertIn("4182", seen["summary"])
            other = fetch({"X-Pi-Chat": "chat-b", "X-Pi-Client": "client-b"})
            self.assertEqual(other["facts"], [])
            self.assertNotIn("4182", other["summary"])
            public = fetch()
            self.assertEqual(public["facts"], [])
            self.assertEqual(public["summary"], "")
            self.assertNotIn("9991", json.dumps(public))
            self.assertNotIn("4182", json.dumps(public))
            fetch(
                {"X-Pi-Chat": "chat-b", "X-Pi-Client": "client-b"},
                method="DELETE",
            )
            fetch(method="DELETE")
            still = fetch({"X-Pi-Chat": "chat-a", "X-Pi-Client": "client-a"})
            self.assertTrue(any("4182" in row["text"] for row in still["facts"]))
            self.assertIn("4182", still["summary"])
            shaped = shape_messages(
                [{"role": "user", "content": "hello"}],
                "hello",
                facts=memory.facts_block(bob),
                summary=memory.summary_text(bob),
            )
            blob = "\n".join(str(row["content"]) for row in shaped)
            self.assertNotIn("4182", blob)
            self.assertNotIn("9991", blob)
        finally:
            httpd.shutdown()
            httpd.server_close()


class MemoryRingHttp(unittest.TestCase):
    def test_usage_and_manual_compact(self):
        import urllib.error

        httpd = make_server("127.0.0.1", 0)
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        thread.start()
        host, port = httpd.server_address
        base = f"http://{host}:{port}/v1/memory"
        compact = base + "/compact"
        headers = {
            "X-Pi-Chat": "ring-chat",
            "X-Pi-Client": "ring-client",
            "Content-Type": "application/json",
        }

        def open_json(req):
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.status, json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode()
                return exc.code, json.loads(raw or "{}")

        try:
            status, public = open_json(urllib.request.Request(base))
            self.assertEqual(status, 200)
            self.assertEqual(public["usage"]["used"], 0)
            self.assertEqual(public["usage"]["compactions"], 0)
            self.assertGreater(public["usage"]["num_ctx"], 0)
            self.assertEqual(public["compact_at"], 0.70)
            self.assertEqual(public["compact_busy_at"], 0.50)
            status, seen = open_json(urllib.request.Request(base, headers=headers))
            self.assertEqual(status, 200)
            self.assertIn("used", seen["usage"])
            self.assertIn("num_ctx", seen["usage"])
            self.assertEqual(seen["compact_at"], 0.70)
            status, denied = open_json(
                urllib.request.Request(compact, data=b"{}", method="POST")
            )
            self.assertEqual(status, 400)
            self.assertEqual(denied, {"ok": False, "reason": "no_session"})
            payload = json.dumps(
                {
                    "messages": [
                        {"role": "system", "content": "skip"},
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": 3},
                    ]
                }
            ).encode()
            with (
                patch.object(runtime.gate, "in_flight", return_value=0),
                patch.object(runtime.gate, "waiting", return_value=0),
                patch("pair.compact.schedule", return_value=True) as scheduled,
            ):
                with patch.object(runtime.gate, "in_flight", return_value=1):
                    status, busy = open_json(
                        urllib.request.Request(
                            compact, data=payload, headers=headers, method="POST"
                        )
                    )
                self.assertEqual(status, 409)
                self.assertEqual(busy["reason"], "busy")
                self.assertFalse(scheduled.called)
                status, queued = open_json(
                    urllib.request.Request(
                        compact, data=payload, headers=headers, method="POST"
                    )
                )
                self.assertEqual(status, 200)
                self.assertEqual(queued, {"ok": True, "queued": True})
                self.assertEqual(scheduled.call_count, 1)
                self.assertEqual(
                    scheduled.call_args.kwargs["scope"],
                    scope_key("ring-chat", "ring-client"),
                )
                self.assertEqual(
                    scheduled.call_args.args[0],
                    [{"role": "user", "content": "hello"}],
                )
                status, limited = open_json(
                    urllib.request.Request(
                        compact, data=payload, headers=headers, method="POST"
                    )
                )
                self.assertEqual(status, 429)
                self.assertEqual(limited["reason"], "rate")
                self.assertEqual(scheduled.call_count, 1)
            huge = urllib.request.Request(
                compact,
                data=b"{" + b"x" * 70000,
                headers=headers,
                method="POST",
            )
            status, _over = open_json(huge)
            self.assertEqual(status, 413)
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
