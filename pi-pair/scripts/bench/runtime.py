#!/usr/bin/env python3
"""VM stand-in for the runtime decision. Tok/s on pi4 is for Akash.

The script records the gates and applies them to fixed examples. It does not
download weights and it does not start a model.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.model.runtime_choice import (
    DECODE_GAIN,
    KL_LIMIT,
    PREFILL_GAIN,
    choose_decoder,
    kl_ok,
    output_layer_ok,
)


def main() -> int:
    binary = shutil.which("llama-server")
    decode_wins = choose_decoder(
        ollama_decode=5.7,
        llama_decode=6.3,
        ollama_prefill=80,
        llama_prefill=80,
        quality_held=True,
    )
    stay = choose_decoder(
        ollama_decode=5.7,
        llama_decode=5.8,
        ollama_prefill=80,
        llama_prefill=90,
        quality_held=True,
    )
    blocked = choose_decoder(
        ollama_decode=5.7,
        llama_decode=8.0,
        ollama_prefill=80,
        llama_prefill=160,
        quality_held=False,
    )
    report = {
        "where": "vm",
        "llama_server": binary or "not installed",
        "measured_tok_s": False,
        "decision": "ollama",
        "reason": "no pi4 timings; llama-server comparison was not run",
        "gates": {
            "decode_gain": DECODE_GAIN,
            "prefill_gain": PREFILL_GAIN,
            "kl_limit": KL_LIMIT,
        },
        "examples": {
            "decode_5_7_to_6_3": decode_wins,
            "within_5_percent": stay,
            "faster_but_quality_fails": blocked,
            "kl_0_005_ok": kl_ok(0.205, 0.200),
            "output_layer_0_02_rejected": output_layer_ok(
                kl_delta=0.02, eval_flat=True
            ),
        },
        "targets": {
            "flash_tok_s": 6.3,
            "flash_tok_s_overclock": 6.8,
            "pro_tok_s": 3.4,
            "flash_ttft_s": 1.5,
            "pro_ttft_s": 3.0,
        },
        "ok": decode_wins == "llama-server"
        and stay == "ollama"
        and blocked == "ollama"
        and kl_ok(0.205, 0.200)
        and not output_layer_ok(kl_delta=0.02, eval_flat=True),
    }
    out = ROOT / "data" / "bench"
    out.mkdir(parents=True, exist_ok=True)
    (out / "runtime.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Runtime bench",
        "",
        f"- decision: {report['decision']}",
        f"- llama-server: {report['llama_server']}",
        f"- decode gate: {DECODE_GAIN}",
        f"- prefill gate: {PREFILL_GAIN}",
        f"- KL limit: {KL_LIMIT}",
        f"- example 5.7 to 6.3: {decode_wins}",
        f"- ok: {report['ok']}",
        "",
    ]
    (out / "runtime.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
