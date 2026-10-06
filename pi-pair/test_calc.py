"""Ast-whitelist calculator notes."""

from __future__ import annotations

import unittest

from pair.calc import fully_answers, notes_for
from pair.turn import shape_messages


class CalculatorNotes(unittest.TestCase):
    def test_products_sums_and_powers(self):
        self.assertIn("847*23 = 19481", notes_for("847*23") or "")
        self.assertIn("1234+5678-999 = 5913", notes_for("1234+5678-999") or "")
        self.assertIn("351", notes_for("What is 15% of 2340?") or "")
        self.assertIn("(2+3)^2 = 25", notes_for("(2+3)^2") or "")

    def test_only_arithmetic_counts_as_a_full_answer(self):
        self.assertTrue(fully_answers("847*23"))
        self.assertTrue(fully_answers("  (2+3)^2 "))
        self.assertFalse(fully_answers("what is 847*23?"))
        self.assertFalse(fully_answers("What is 15% of 2340?"))
        self.assertFalse(fully_answers("847*23 please"))

    def test_unsafe_spans_are_skipped(self):
        self.assertIsNone(notes_for("2**99999"))
        self.assertIsNone(notes_for("1/0"))
        self.assertIsNone(notes_for("__import__('os')"))
        self.assertIsNone(notes_for("555-1234"))
        self.assertIsNone(notes_for("meet on 2024-05-01"))

    def test_the_note_is_placed_before_the_question(self):
        prompt = "what is 847*23?"
        rows = shape_messages([{"role": "user", "content": prompt}], prompt)
        self.assertEqual(rows[-1]["content"], prompt)
        self.assertIn("Calculator: 847*23 = 19481", rows[-2]["content"])
        self.assertTrue(rows[-2]["content"].startswith("Notes:"))


if __name__ == "__main__":
    unittest.main()
