"""Sources pill, side panel, splash, settings, and the voice/send morph."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _node(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", "--experimental-strip-types", str(ROOT / "web" / script)],
        cwd=ROOT / "web",
        capture_output=True,
        text=True,
        check=False,
    )


class SourcesAndShell(unittest.TestCase):
    def test_sources_pill_count_stack_and_panel_links(self):
        completed = _node("sources.test.mjs")
        self.assertEqual(completed.returncode, 0, completed.stdout + "\n" + completed.stderr)
        self.assertIn("ok", completed.stdout)

    def test_morph_splash_settings_and_side_panel(self):
        completed = _node("shell.test.mjs")
        self.assertEqual(completed.returncode, 0, completed.stdout + "\n" + completed.stderr)
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
