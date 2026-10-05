"""Deterministic sequences, source-backed lists, and computed charts."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.charts import chart_json_ok, ready_chart, repair_chart_reply  # noqa: E402
from pair.lists import ground_category_list  # noqa: E402
from pair.sequences import sequence_answer, sequence_values  # noqa: E402

HORROR_NOTES = """Web search notes.
Text from the first page:
1. The Exorcist
2. Hereditary
3. Get Out
4. The Shining
5. Halloween
6. Psycho
"""


class Sequences(unittest.TestCase):
    def test_five_prime_runs_are_the_same_five_primes(self):
        for _ in range(5):
            self.assertEqual(sequence_values("Top 5 primes"), [2, 3, 5, 7, 11])
            self.assertEqual(
                sequence_answer("Top 5 primes"),
                "1. 2\n2. 3\n3. 5\n4. 7\n5. 11",
            )

    def test_fibonacci_and_the_other_counted_sequences(self):
        self.assertEqual(
            sequence_values("first 8 Fibonacci numbers"),
            [0, 1, 1, 2, 3, 5, 8, 13],
        )
        self.assertEqual(sequence_values("first 4 squares"), [1, 4, 9, 16])
        self.assertEqual(sequence_values("first 3 cubes"), [1, 8, 27])
        self.assertEqual(sequence_values("first 4 even numbers"), [2, 4, 6, 8])
        self.assertEqual(sequence_values("first 4 odd numbers"), [1, 3, 5, 7])
        self.assertEqual(sequence_values("first 5 factorials"), [1, 2, 6, 24, 120])
        self.assertEqual(sequence_values("first 4 multiples of 3"), [3, 6, 9, 12])
        self.assertIsNone(sequence_values("Top 5 horror movies"))
        capped = sequence_answer("first 1001 primes")
        self.assertTrue(capped.startswith("Showing 1000 items (capped from 1001)."))
        self.assertIn("\n1000. ", capped)
        self.assertNotIn("\n1001. ", capped)


class GroundedLists(unittest.TestCase):
    def test_horror_list_uses_the_source_titles(self):
        invented = (
            "1. The Shapen\n2. The Exorcist\n3. Hereditary\n4. Get Out\n5. Halloween"
        )
        done = ground_category_list("Top 5 horror movies", invented, HORROR_NOTES)
        titles = [line.split(". ", 1)[1] for line in done.splitlines()]
        self.assertEqual(
            titles,
            ["The Exorcist", "Hereditary", "Get Out", "The Shining", "Halloween"],
        )
        self.assertNotIn("Shapen", done)
        self.assertNotIn("Psycho", done)
        sparse = "Web search notes.\n- Halloween is a horror film."
        kept = ground_category_list("Top 5 horror movies", invented, sparse)
        self.assertEqual(kept, invented)


class ComputedCharts(unittest.TestCase):
    def test_square_plot_emits_a_chart_fence(self):
        prompt = "plot y=x^2 from 0 to 5"
        fence = ready_chart(prompt)
        self.assertIsNotNone(fence)
        self.assertIn("```chart", fence)
        body = fence.split("\n", 1)[1].rsplit("\n", 1)[0]
        spec = json.loads(body)
        series = spec["data"][0]
        self.assertEqual(series["x"], [0, 1, 2, 3, 4, 5])
        self.assertEqual(series["y"], [0, 1, 4, 9, 16, 25])
        self.assertTrue(chart_json_ok(body))
        repaired = repair_chart_reply(
            "I could not draw that chart.",
            lambda: "I could not draw that chart.",
            prompt=prompt,
        )
        self.assertIn("```chart", repaired)
        self.assertNotIn("could not draw", repaired.lower())


if __name__ == "__main__":
    unittest.main()
