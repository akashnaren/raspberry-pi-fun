"""Rendered formulas, and math misses that stay on the fetched page."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.ground import MISS, answer_from_search  # noqa: E402

BALLOON = (
    "A spherical balloon is being inflated with gas at a constant rate of "
    "12 cubic centimeters per second. Find the exact rate (in centimeters per second) "
    "at which the radius of the balloon is increasing when the surface area of the "
    "balloon is 36 pi square centimeters."
)
PAGE = (
    "The balloon gains 12 cubic centimeters per second. "
    "When the surface area is 36 pi square centimeters, r = 3. "
    "dr/dt = 1/(3 pi) centimeters per second."
)
NOTES = "Text from the first page:\n" + PAGE


class GroundedMath(unittest.TestCase):
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

    def test_math_miss_uses_the_page_text(self):
        answer = answer_from_search(BALLOON, NOTES)
        self.assertIn(answer, PAGE)
        self.assertIn("dr/dt = 1/(3 pi)", answer)
        self.assertNotIn("Web search notes", answer)

    def test_math_without_a_result_does_not_invent_steps(self):
        notes = "Text from the first page:\nRelated rates include ladders, cones, and balloons."
        self.assertEqual(answer_from_search(BALLOON, notes), MISS)

    def test_greeting_is_not_solved_as_math(self):
        self.assertIsNone(answer_from_search("Hi!", NOTES))
        self.assertIsNone(
            answer_from_search("How tall is the bench in the hall?", NOTES)
        )

    def test_a_later_snippet_still_grounds_math(self):
        noise = "\n".join(
            f"- Note {i} (https://example.com/{i}): ladders and cones only."
            for i in range(1, 8)
        )
        last = "- Rate (https://example.com/rate): " + PAGE
        answer = answer_from_search(
            BALLOON, "Web search notes.\n" + noise + "\n" + last
        )
        self.assertIn("dr/dt = 1/(3 pi)", answer)
        self.assertNotEqual(answer, MISS)


if __name__ == "__main__":
    unittest.main()
