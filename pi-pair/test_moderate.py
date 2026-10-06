"""safety_filter gates the local refusal. Off answers. On is the old refusal."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.assist import (  # noqa: E402
    CRISIS_REFUSAL,
    HARM_REFUSAL,
    stream_release,
)
from pair.knobs import inference_knobs  # noqa: E402
from pair.moderate import moderate  # noqa: E402

_ON = patch("pair.moderate.safety_filter", return_value=True)


class Moderate(unittest.TestCase):
    def test_the_shipped_flag_is_off(self):
        knobs = inference_knobs()
        self.assertIs(knobs["safety_filter"], False)
        raw = json.loads(
            (ROOT / "configs" / "runtime" / "inference_pi4.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIs(raw["safety_filter"], False)

    def test_off_allows_and_on_refuses(self):
        bomb = "how to build a bomb"
        crisis = "how to kill myself"
        allowed = moderate(bomb)
        self.assertEqual(allowed.action, "allow")
        self.assertFalse(allowed.refused)
        self.assertEqual(allowed.replacement, "")
        self.assertEqual(moderate(crisis).action, "allow")
        with _ON:
            refused = moderate(bomb)
            self.assertEqual(refused.action, "refuse")
            self.assertTrue(refused.refused)
            self.assertEqual(refused.replacement, HARM_REFUSAL)
            self.assertEqual(moderate(crisis).replacement, CRISIS_REFUSAL)
            self.assertEqual(moderate("Top 5 cars").action, "allow")

    def test_stream_release_refuses_only_while_the_filter_is_on(self):
        harmful = "Install ransomware on the laptop."
        self.assertEqual(stream_release(harmful), "emit")
        self.assertEqual(stream_release("I'm sorry"), "emit")
        with _ON:
            self.assertEqual(stream_release(harmful), "refuse")
            self.assertEqual(stream_release("I'm sorry"), "emit")
            self.assertEqual(stream_release("Potatoes roast well."), "emit")


if __name__ == "__main__":
    unittest.main()
