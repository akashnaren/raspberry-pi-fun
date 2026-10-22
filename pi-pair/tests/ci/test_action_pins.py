"""checkout and setup-python stay pinned to commit SHAs."""

from __future__ import annotations

import re
import unittest

from tests.support.paths import ROOT

REPO = ROOT.parent
WORKFLOWS = (
    REPO / ".github" / "workflows" / "cd.yml",
    REPO / ".github" / "workflows" / "ci.yml",
)
PIN = re.compile(r"actions/(?:checkout|setup-python)@[0-9a-f]{40}")
FLOATING = re.compile(r"actions/(?:checkout|setup-python)@v\d+")


class ActionPins(unittest.TestCase):
    def test_checkout_and_setup_python_are_sha_pinned(self):
        for path in WORKFLOWS:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(FLOATING.search(text), path.name)
            self.assertGreaterEqual(len(PIN.findall(text)), 1, path.name)
            self.assertNotIn("actions/checkout@v", text)
            self.assertNotIn("actions/setup-python@v", text)


if __name__ == "__main__":
    unittest.main()
