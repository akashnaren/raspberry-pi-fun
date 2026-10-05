"""Auto picks Flash or Pro through modes.py. Off-list models stay on those tags."""

from __future__ import annotations

import importlib
import unittest

from pair.modes import (
    FLASH_MODEL,
    PRO_MODEL,
    mode_tips,
    resolve_auto,
    resolve_mode,
    task_tier,
)
from pair.resident import eviction_targets


class TaskTier(unittest.TestCase):
    def test_short_chitchat_is_flash(self):
        for line in (
            "nice weather today",
            "thanks for the note",
            "ok",
            "how is the coffee",
        ):
            self.assertEqual(task_tier(line), "flash", line)

    def test_hard_lines_are_pro(self):
        samples = {
            "math": "What is the derivative of x squared?",
            "code": "Write a python function that reverses a list.",
            "steps": "Explain the orbit step by step.",
            "search": "Search for the latest raspberry pi news.",
            "length": "word " * 50,
            "list": "top 10 movies from 2019",
        }
        for name, line in samples.items():
            self.assertEqual(task_tier(line), "pro", name)

    def test_plots_and_plain_lists_stay_on_flash(self):
        long_plot = "plot a bar chart of " + ("picnic " * 40)
        self.assertEqual(task_tier(long_plot), "flash")
        self.assertEqual(task_tier("make a list of picnic foods"), "flash")
        attached = "plot the bars\n\n---\n" + ("ocr text " * 80)
        self.assertEqual(task_tier(attached), "flash")
        self.assertEqual(
            task_tier("Write a python function that reverses a list."), "pro"
        )
        self.assertEqual(task_tier("Search for the latest raspberry pi news."), "pro")


class Resolve(unittest.TestCase):
    def test_explicit_mode_overrides_the_heuristic(self):
        hard = "Write a python function that reverses a list."
        flash_mode, flash_tag = resolve_mode("flash", PRO_MODEL)
        pro_mode, pro_tag = resolve_mode("pro", FLASH_MODEL)
        self.assertEqual(flash_mode, "flash")
        self.assertEqual(flash_tag, FLASH_MODEL)
        self.assertEqual(pro_mode, "pro")
        self.assertEqual(pro_tag, PRO_MODEL)
        route, tag, reason = resolve_auto(hard, [FLASH_MODEL, PRO_MODEL])
        self.assertEqual(route, "pro")
        self.assertEqual(tag, PRO_MODEL)
        self.assertEqual(reason, "heuristic")
        easy_route, easy_tag, _reason = resolve_auto(
            "nice weather today", [FLASH_MODEL, PRO_MODEL]
        )
        self.assertEqual(easy_route, "flash")
        self.assertEqual(easy_tag, FLASH_MODEL)

    def test_auto_falls_back_when_pro_is_not_installed(self):
        hard = "Search for the latest raspberry pi news."
        route, tag, reason = resolve_auto(hard, [FLASH_MODEL])
        self.assertEqual(route, "flash")
        self.assertEqual(tag, FLASH_MODEL)
        self.assertEqual(reason, "pro-unavailable")

    def test_auto_uses_pro_when_the_tag_is_listed(self):
        route, tag, _reason = resolve_auto(
            "Explain the orbit step by step.",
            [FLASH_MODEL, PRO_MODEL],
        )
        self.assertEqual(route, "pro")
        self.assertEqual(tag, PRO_MODEL)

    def test_legacy_and_empty_modes_clamp_to_the_allowlist(self):
        self.assertEqual(resolve_mode("", "custom:tiny"), ("flash", FLASH_MODEL))
        self.assertEqual(resolve_mode(None, "llama3.2:1b"), ("flash", FLASH_MODEL))
        self.assertEqual(resolve_mode("turbo", "custom:tiny"), ("flash", FLASH_MODEL))
        knobs = {"model": "custom:flash", "pro_model": "custom:pro"}
        self.assertEqual(resolve_mode("", "nope", knobs), ("flash", "custom:flash"))
        self.assertEqual(resolve_mode("pro", "nope", knobs), ("pro", "custom:pro"))
        self.assertEqual(resolve_mode(None, "custom:pro", knobs), ("pro", "custom:pro"))
        tips = mode_tips(knobs)
        self.assertEqual(tips["flash"], "custom:flash, the fast resident model.")
        self.assertEqual(tips["pro"], "custom:pro, loaded when the question needs it.")
        self.assertNotIn("qwen2.5", tips["flash"] + tips["pro"])
        route, tag, _reason = resolve_auto(
            "Write a python function", [FLASH_MODEL], knobs
        )
        self.assertEqual(route, "flash")
        self.assertEqual(tag, "custom:flash")

    def test_auto_does_not_plan_an_eviction(self):
        self.assertEqual(
            eviction_targets(["qwen2.5:0.5b", "snowflake-arctic-embed:m"], PRO_MODEL),
            [],
        )
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("pair.mode")


if __name__ == "__main__":
    unittest.main()
