"""Actions that warned on Node 20 stay pinned to Node 24 commit SHAs."""

from __future__ import annotations

import re
import unittest

from tests.support.paths import ROOT

REPO = ROOT.parent
WORKFLOWS = (
    REPO / ".github" / "workflows" / "cd.yml",
    REPO / ".github" / "workflows" / "ci.yml",
)
PIN = re.compile(
    r"actions/(?:checkout|setup-python|upload-artifact|setup-node)@[0-9a-f]{40}"
)
FLOATING = re.compile(
    r"actions/(?:checkout|setup-python|upload-artifact|setup-node)@v\d+"
)
# Current Node 24 major. v4 still declares node20. ci.yml only.
SETUP_NODE = "actions/setup-node@949feb2413d6458794dcd2491c4babbbce0c15c1"  # v7.1.0
# Node 24 releases. checkout v4 and setup-python v5 still declare node20.
NODE24 = (
    "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09",  # v5.1.0
    "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",  # v6.3.0
    "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f",  # v6.0.0
)
NODE20 = (
    "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",  # v4.4.0
    "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",  # v5.6.0
    "actions/upload-artifact@v4",
)


class ActionPins(unittest.TestCase):
    def test_checkout_and_setup_python_are_sha_pinned(self):
        for path in WORKFLOWS:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(FLOATING.search(text), path.name)
            self.assertGreaterEqual(len(PIN.findall(text)), 1, path.name)
            self.assertNotIn("actions/checkout@v", text)
            self.assertNotIn("actions/setup-python@v", text)
            self.assertNotIn("actions/upload-artifact@v", text)
            self.assertNotIn("actions/setup-node@v", text)

    def test_setup_node_is_pinned_to_node24(self):
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        cd = (REPO / ".github" / "workflows" / "cd.yml").read_text(encoding="utf-8")
        self.assertEqual(ci.count(SETUP_NODE), 2)
        self.assertNotIn("actions/setup-node@v", ci)
        self.assertNotIn("actions/setup-node@", cd)

    def test_node20_warning_actions_use_node24_releases(self):
        for path in WORKFLOWS:
            text = path.read_text(encoding="utf-8")
            for pin in NODE24:
                self.assertIn(pin, text, path.name)
            for pin in NODE20:
                self.assertNotIn(pin, text, path.name)


if __name__ == "__main__":
    unittest.main()
