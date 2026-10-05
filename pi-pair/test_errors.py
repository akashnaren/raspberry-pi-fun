"""User-facing failures stay one plain sentence. The raw cause is logged."""
from __future__ import annotations

import unittest

from pair.errors import (
    BAD_MESSAGE,
    BUSY,
    FILE_EMPTY,
    FILE_TYPE,
    GENERIC,
    MODEL_MISSING,
    NO_CHAT,
    OCR_BUSY,
    OCR_MISSING,
    PDF_SCAN,
    SEARCH_WHERE,
    TIMEOUT,
    TOO_BIG,
    UNREACHABLE,
    friendly_body,
    friendly_error,
)


class FriendlyMap(unittest.TestCase):
    def test_each_failure_is_a_sentence_without_internals(self):
        samples = {
            "": GENERIC,
            "Traceback (most recent call last): boom": GENERIC,
            '{"error": "secret"}': GENERIC,
            "x" * 260: GENERIC,
            "pi4 is at capacity (2 generations in flight). Try again in a moment.": BUSY,
            "pi4 unreachable on cache miss": UNREACHABLE,
            "pi4 offline": UNREACHABLE,
            "connection refused": UNREACHABLE,
            "pi3 cannot be the brain": NO_CHAT,
            "pi2 does not run a chat model": NO_CHAT,
            "qwen2.5:1.5b is not on pi4. This router does not pull it. On pi4, when you mean to: ollama pull qwen2.5:1.5b": MODEL_MISSING,
            "only a JPEG-scanned PDF can be read": PDF_SCAN,
            "OCR is not installed on this Pi": OCR_MISSING,
            "OCR is busy": OCR_BUSY,
            "unsupported file type": FILE_TYPE,
            "attachment is over 4 MB": TOO_BIG,
            "that took too long": TIMEOUT,
            "search is served on the health host": SEARCH_WHERE,
            "unknown mode turbo": BAD_MESSAGE,
            "attachment is empty": FILE_EMPTY,
            "HTTP 502 from upstream": GENERIC,
        }
        banned = ("pi2", "pi3", "pi4", "ollama", "traceback", "generations in flight", "HTTP", "{", "[")
        with self.assertLogs("pi-pair.errors", level="WARNING") as logs:
            for raw, want in samples.items():
                shown = friendly_error(raw)
                self.assertEqual(shown, want, raw)
                self.assertTrue(shown.strip(), raw)
                low = shown.lower()
                for word in banned:
                    self.assertNotIn(word.lower(), low, shown)
        self.assertTrue(any("user failure:" in line for line in logs.output))

    def test_operator_strings_pass_through(self):
        for raw in (
            "missing or invalid API key",
            "PI_GPT_API_KEY is not set",
            "could not read that file",
            "no text in that file",
        ):
            self.assertEqual(friendly_error(raw), raw)

    def test_relay_body_is_rewritten(self):
        raw = b'{"error": "pi4 offline"}'
        body = friendly_body(raw)
        self.assertIn(UNREACHABLE.encode(), body)
        self.assertNotIn(b"pi4", body)
        junk = friendly_body(b"not-json")
        self.assertIn(GENERIC.encode(), junk)


if __name__ == "__main__":
    unittest.main()
