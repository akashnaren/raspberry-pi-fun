"""Structural checks for the ten chat abilities. No stored answers."""

from __future__ import annotations

import unittest

from pair.abilities import CATEGORIES, category_rates, tool_notes
from pair.server import Handler


class Abilities(unittest.TestCase):
    def test_each_ability_lifts_only_its_category(self):
        everything = category_rates()
        for name in CATEGORIES:
            self.assertGreaterEqual(everything[name], 0.9, name)
        for name in CATEGORIES:
            off = category_rates(set(CATEGORIES) - {name})
            self.assertGreater(everything[name], off[name], name)
            for other in CATEGORIES:
                if other == name:
                    continue
                self.assertEqual(everything[other], off[other], other)

    def test_a_calc_fence_asks_the_model_once_more(self):
        calls = []

        class Dummy:
            _extra_call = False

            def _decode_reply(self, *args, **kwargs):
                calls.append(args)
                return "equals 4", "m", True

        reply = "```calc\n2+2\n```"
        out = Handler._one_more_round(
            Dummy(), None, "ollama", "m", [], 0.3, 96, "add", None, reply
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("```calc", out)
        self.assertIn("equals 4", out)
        note = calls[0][3][-1]["content"]
        self.assertIn("Calculator: 2+2 = 4", note)
        self.assertEqual(len(tool_notes(reply, search=lambda _q: {})), len(note))

    def test_plain_text_does_not_take_a_second_call(self):
        class Dummy:
            _extra_call = False

            def _decode_reply(self, *args, **kwargs):
                raise AssertionError("second call")

        out = Handler._one_more_round(
            Dummy(), None, "ollama", "m", [], 0.3, 96, "hi", None, "hello"
        )
        self.assertEqual(out, "hello")


if __name__ == "__main__":
    unittest.main()
