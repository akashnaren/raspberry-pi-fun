"""The assistant bubble stays off the page until the first reply token."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class AssistantBubble(unittest.TestCase):
    def test_bubble_appears_only_after_content(self):
        completed = subprocess.run(
            [
                "node",
                "--experimental-strip-types",
                str(ROOT / "web" / "bubble.test.mjs"),
            ],
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
