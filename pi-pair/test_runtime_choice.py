"""Decision gates for the runtime and model-file experiments. No weights are loaded."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from pair.runtime_choice import choose_decoder, kl_ok, output_layer_ok, quality_held

ROOT = Path(__file__).resolve().parent


class RuntimeChoice(unittest.TestCase):
    def test_llama_server_wins_only_when_faster_and_quality_holds(self):
        self.assertEqual(
            choose_decoder(
                ollama_decode=5.7,
                llama_decode=6.3,
                ollama_prefill=80,
                llama_prefill=80,
                quality_held=True,
            ),
            "llama-server",
        )
        self.assertEqual(
            choose_decoder(
                ollama_decode=5.7,
                llama_decode=5.8,
                ollama_prefill=80,
                llama_prefill=90,
                quality_held=True,
            ),
            "ollama",
        )
        self.assertEqual(
            choose_decoder(
                ollama_decode=5.7,
                llama_decode=8.0,
                ollama_prefill=80,
                llama_prefill=160,
                quality_held=False,
            ),
            "ollama",
        )

    def test_kl_and_output_layer_gates(self):
        self.assertTrue(kl_ok(0.20, 0.195))
        self.assertFalse(kl_ok(0.22, 0.20))
        self.assertTrue(
            quality_held(
                kl_candidate=0.20,
                kl_baseline=0.195,
                eval_within_se=True,
                category_drop=2,
            )
        )
        self.assertFalse(
            quality_held(
                kl_candidate=0.20,
                kl_baseline=0.195,
                eval_within_se=True,
                category_drop=4,
            )
        )
        self.assertTrue(output_layer_ok(kl_delta=0.01, eval_flat=True))
        self.assertFalse(output_layer_ok(kl_delta=0.02, eval_flat=True))
        self.assertFalse(output_layer_ok(kl_delta=0.0, eval_flat=False))

    def test_quant_script_dry_run_does_not_download(self):
        script = ROOT / "scripts" / "build_quants.sh"
        completed = subprocess.run(
            ["bash", str(script), "--dry-run"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("dry-run", completed.stdout)
        self.assertNotIn("http", completed.stdout.lower())

    def test_vm_bench_stays_on_ollama_without_pi_timings(self):
        script = ROOT / "scripts" / "bench_runtime.py"
        completed = subprocess.run(
            ["python3", str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads((ROOT / "data" / "bench" / "runtime.json").read_text())
        self.assertEqual(report["decision"], "ollama")
        self.assertTrue(report["ok"])
        self.assertLessEqual(report["gates"]["kl_limit"], 0.01)
