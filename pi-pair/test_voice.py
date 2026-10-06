"""Dictation fills the box. Voice mode sends the utterance, speaks the reply, then listens again."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class SpokenTurn(unittest.TestCase):
    def test_reply_is_spoken_from_the_assistant_message_and_final_speech_is_sent(self):
        completed = subprocess.run(
            [
                "node",
                "--experimental-strip-types",
                str(ROOT / "web" / "voice.test.mjs"),
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

    def test_voice_and_typed_turns_share_one_history_body(self):
        completed = subprocess.run(
            [
                "node",
                "--experimental-strip-types",
                str(ROOT / "web" / "history.test.mjs"),
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
