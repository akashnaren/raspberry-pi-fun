"""DEPLOYED_SHA parses to 12 hex characters or the literal unknown."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pair.core.stamp import deployed_commit, parse_commit, read_commit
from pair.routes.status import public_health


class ParseCommit(unittest.TestCase):
    def test_one_trailing_newline_and_a_full_match(self):
        samples = {
            "0123456789abcdef\n": "0123456789ab",
            "0123456789abcdef": "0123456789ab",
            "0123456789ab": "0123456789ab",
            "abcdef0": "abcdef0",
            "0123456789a": "0123456789a",
            "0123456789abcdef0123456789abcdef01234567": "0123456789ab",
            "0123456789abcdef0123456789abcdef01234567\n": "0123456789ab",
            "": "unknown",
            "\n": "unknown",
            "abcdef": "unknown",
            "0123456789abcdef0123456789abcdef012345678": "unknown",
            "0123456789ABCD\n": "unknown",
            "0123456789abcdef\r\n": "unknown",
            "0123456789abcdef\n\n": "unknown",
            "0123456789abcdef-dirty\n": "unknown",
            "0123456789ab\n ": "unknown",
            " 0123456789ab\n": "unknown",
            "0123456789ab \n": "unknown",
            "ABCDEF0": "unknown",
        }
        for text, expected in samples.items():
            with self.subTest(text=text):
                self.assertEqual(parse_commit(text), expected)


class ReadCommit(unittest.TestCase):
    def test_missing_empty_invalid_and_unreadable_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.assertEqual(read_commit(directory), "unknown")
            path = directory / "DEPLOYED_SHA"
            path.write_bytes(b"")
            self.assertEqual(read_commit(directory), "unknown")
            path.write_text("0123456789abcdef\n", encoding="utf-8")
            self.assertEqual(read_commit(directory), "0123456789ab")
            path.write_text("abcdef0\n", encoding="utf-8")
            self.assertEqual(read_commit(directory), "abcdef0")
            path.write_bytes(b"\xff")
            self.assertEqual(read_commit(directory), "unknown")
            path.write_bytes(b"0123456789abcdef\n" + (b"g" * 200))
            self.assertEqual(read_commit(directory), "unknown")
            path.unlink()
            path.mkdir()
            self.assertEqual(read_commit(directory), "unknown")
            path.rmdir()
            path.write_text("0123456789ab", encoding="utf-8")
            path.chmod(0)
            try:
                self.assertEqual(read_commit(directory), "unknown")
            finally:
                path.chmod(0o644)

    def test_deployed_commit_stays_put(self):
        first = deployed_commit()
        self.assertIsInstance(first, str)
        self.assertEqual(deployed_commit(), first)

    def test_public_health_uses_the_process_commit(self):
        shown = public_health(
            {"commit": "ffffffffffff", "services": {}, "peers": [], "ok": True}
        )
        self.assertEqual(shown["commit"], deployed_commit())
        self.assertNotEqual(shown["commit"], "ffffffffffff")


if __name__ == "__main__":
    unittest.main()
