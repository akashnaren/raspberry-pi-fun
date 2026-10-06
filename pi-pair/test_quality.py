"""Computed charts stay deterministic. Lists and sequences are the model's."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.charts import chart_json_ok, ready_chart, repair_chart_reply  # noqa: E402


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
