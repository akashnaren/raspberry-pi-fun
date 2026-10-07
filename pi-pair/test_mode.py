"""Auto picks Flash or Pro through modes.py. Off-list models stay on those tags."""

from __future__ import annotations

import unittest

from pair.model.modes import (
    FLASH_MODEL,
    PRO_MODEL,
    mode_tips,
    resolve_auto,
    resolve_mode,
)


class Resolve(unittest.TestCase):
    def test_explicit_pro_is_the_only_way_off_flash(self):
        hard = "Write a python function that reverses a list."
        flash_mode, flash_tag = resolve_mode("flash", PRO_MODEL)
        pro_mode, pro_tag = resolve_mode("pro", FLASH_MODEL)
        self.assertEqual(flash_mode, "flash")
        self.assertEqual(flash_tag, FLASH_MODEL)
        self.assertEqual(pro_mode, "pro")
        self.assertEqual(pro_tag, PRO_MODEL)
        for line in (
            hard,
            "nice weather today",
            "Search for the latest raspberry pi news.",
            "top 10 movies from 2019",
            "word " * 50,
        ):
            route, tag, reason = resolve_auto(line, [FLASH_MODEL, PRO_MODEL])
            self.assertEqual(route, "flash", line)
            self.assertEqual(tag, FLASH_MODEL, line)
            self.assertEqual(reason, "default", line)

    def test_legacy_and_empty_modes_clamp_to_the_allowlist(self):
        self.assertEqual(resolve_mode("", "custom:tiny"), ("flash", FLASH_MODEL))
        self.assertEqual(resolve_mode(None, "llama3.2:1b"), ("flash", FLASH_MODEL))
        self.assertEqual(resolve_mode("turbo", "custom:tiny"), ("flash", FLASH_MODEL))
        knobs = {"model": "custom:flash", "pro_model": "custom:pro"}
        self.assertEqual(resolve_mode("", "nope", knobs), ("flash", "custom:flash"))
        self.assertEqual(resolve_mode("pro", "nope", knobs), ("pro", "custom:pro"))
        self.assertEqual(resolve_mode(None, "custom:pro", knobs), ("pro", "custom:pro"))
        tips = mode_tips(knobs)
        self.assertEqual(tips["flash"], "Fast answers for everyday questions.")
        self.assertEqual(
            tips["pro"], "Slower, more careful answers for harder questions."
        )
        self.assertNotIn("qwen2.5", tips["flash"] + tips["pro"])
        self.assertEqual(tips["auto"], "Auto stays on Flash. It does not choose Pro.")
        route, tag, reason = resolve_auto(
            "Write a python function", [FLASH_MODEL], knobs
        )
        self.assertEqual(route, "flash")
        self.assertEqual(tag, "custom:flash")
        self.assertEqual(reason, "default")


if __name__ == "__main__":
    unittest.main()
