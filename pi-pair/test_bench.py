"""VM bench: the second turn's uncached prefill is at least 40% smaller."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "scripts" / "bench_pi4.py"
_SPEC = importlib.util.spec_from_file_location("bench_pi4", _PATH)
assert _SPEC and _SPEC.loader
_BENCH = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BENCH)
run_vm = _BENCH.run_vm


class VmBench(unittest.TestCase):
    def test_second_turn_ttft_drops(self):
        result = run_vm()
        turns = result["turns"]
        self.assertTrue(turns["prefix_identical"])
        self.assertGreaterEqual(turns["ttft_drop"], 0.4)
        self.assertLess(turns["second_ttft_ms"], turns["first_ttft_ms"])
        self.assertIn("llama_server", result["llama"])


if __name__ == "__main__":
    unittest.main()
