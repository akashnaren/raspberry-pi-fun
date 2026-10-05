"""A reply chart is Plotly from structured JSON. Missing data is not a plot."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class ChartRender(unittest.TestCase):
    def test_client_renders_chart_json_and_skips_empty_data(self):
        plotly = ROOT / "static" / "plotly.min.js"
        self.assertTrue(plotly.is_file(), "npm build did not include Plotly")
        self.assertGreater(plotly.stat().st_size, 100_000)
        completed = subprocess.run(
            ["node", "--experimental-strip-types", str(ROOT / "web" / "chart.test.mjs")],
            cwd=ROOT / "web",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + "\n" + completed.stderr)
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
