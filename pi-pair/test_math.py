"""Rendered formulas. Math questions are answered by the model."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class RenderedMath(unittest.TestCase):
    def test_formula_markup_is_rendered_not_backslash_source(self):
        completed = subprocess.run(
            ["node", "--experimental-strip-types", str(ROOT / "web" / "math.test.mjs")],
            cwd=ROOT / "web",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode, 0, completed.stdout + "\n" + completed.stderr
        )
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
