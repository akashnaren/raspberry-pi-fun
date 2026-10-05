"""Dictation mic is gray when idle and mint while listening."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class MicButtonColor(unittest.TestCase):
    def test_listening_and_idle_use_different_color_classes(self):
        completed = subprocess.run(
            ["node", "--experimental-strip-types", str(ROOT / "web" / "mic-button.test.mjs")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + "\n" + completed.stderr,
        )
        self.assertIn("ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
