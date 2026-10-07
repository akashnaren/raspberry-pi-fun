"""VM bench: the second turn's uncached prefill is at least 40% smaller."""

from __future__ import annotations

import importlib.util
import unittest

from tests.support.paths import ROOT

_PATH = ROOT / "scripts" / "bench" / "pi4.py"
_SPEC = importlib.util.spec_from_file_location("bench_pi4", _PATH)
assert _SPEC and _SPEC.loader
_BENCH = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BENCH)


class VmBench(unittest.TestCase):
    def test_second_turn_ttft_drops(self):
        result = {"turns": _BENCH.vm_turns(), "llama": _BENCH.llama_note()}
        turns = result["turns"]
        self.assertTrue(turns["prefix_identical"])
        self.assertGreaterEqual(turns["ttft_drop"], 0.4)
        self.assertLess(turns["second_ttft_ms"], turns["first_ttft_ms"])
        self.assertIn("llama_server", result["llama"])

    def test_router_unit_stays_on_core_zero(self):
        path = ROOT / "configs" / "runtime" / "pi-pair.service.d" / "router.conf"
        text = path.read_text(encoding="utf-8")
        self.assertIn("CPUAffinity=0", text)
        self.assertIn("Nice=5", text)


if __name__ == "__main__":
    unittest.main()
