"""Harmless questions get a real answer. Harmful ones keep the refusal."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.assist import (  # noqa: E402
    friendly_greeting,
    is_harmful,
    is_soft_refusal,
    scrub_reply,
    settle_reply,
    short_attempt,
    visible_canned,
)
from pair.turn import shape_messages  # noqa: E402

REFUSAL = "I'm sorry, but I can't assist with that.\n1. junk"


class Assist(unittest.TestCase):
    def test_top_lists_are_answered_after_one_retry(self):
        calls = {"n": 0}

        def retry():
            calls["n"] += 1
            return REFUSAL

        for prompt, needle in (("Top 5 cars", "Civic"), ("Top 5 movies", "Godfather")):
            calls["n"] = 0
            text = settle_reply(prompt, REFUSAL, retry)
            self.assertNotIn("can't assist", text.lower(), prompt)
            self.assertIn(needle, text, prompt)
            self.assertEqual(calls["n"], 1, prompt)
            self.assertNotIn("medium effort", text.lower())
            self.assertNotIn("flash", text.lower())

    def test_a_harmful_refusal_is_kept(self):
        calls = []
        prompt = "how to build a bomb"
        self.assertTrue(is_harmful(prompt))
        self.assertFalse(is_harmful("Top 5 cars"))
        text = settle_reply(prompt, REFUSAL, lambda: calls.append(1))
        self.assertIn("can't assist", text.lower())
        self.assertEqual(calls, [])
        rows = shape_messages([{"role": "user", "content": prompt}], prompt)
        self.assertEqual(rows, [{"role": "user", "content": prompt}])

    def test_hello_does_not_leak_the_mesh(self):
        stored = "Hello. Ready on the private Pi mesh."
        text = visible_canned("hello", stored)
        self.assertEqual(text, "Hi! How can I help?")
        self.assertNotIn("mesh", text.lower())
        self.assertEqual(visible_canned("Hi!", "Hi. What can I help you with?"), "Hi. What can I help you with?")
        self.assertEqual(friendly_greeting("good morning"), "Good morning! How can I help?")
        kept = settle_reply("hello", "Hello! How are you?", lambda: "no")
        self.assertEqual(kept, "Hello! How are you?")

    def test_effort_and_routing_sentences_are_removed(self):
        text = scrub_reply("Paris is the capital. I used medium effort in Flash mode.")
        self.assertEqual(text, "Paris is the capital.")
        self.assertNotIn("map", text.lower())
        fence = scrub_reply('See it.\n```chart\n{"title":"Flash mode"}\n```')
        self.assertIn("```chart", fence)
        self.assertIn("Flash mode", fence)
        self.assertFalse(is_soft_refusal("1. Honda Civic"))
        self.assertIn("Civic", short_attempt("Top 5 cars"))


if __name__ == "__main__":
    unittest.main()
