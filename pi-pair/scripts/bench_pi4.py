#!/usr/bin/env python3
"""VM bench for prompt-cache TTFT. The Pi protocol is the same script with --pi.

Stdlib only. The VM run does not need the Pi: it shapes two turns and charges
prefill time only for tokens that are not a byte prefix of the previous prompt.
A llama-server binary is optional. When it is missing, the comparison is recorded
as not installed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.turn import PERSONA, estimate_tokens, shape_messages

# Milliseconds per uncached token on the VM stand-in. The ratio is what the
# pass criteria checks, not this constant.
MS_PER_TOKEN = 8.0


def render_prompt(rows: list) -> str:
    return "\n".join(
        f"{row.get('role')}:{row.get('content')}"
        for row in rows
        if isinstance(row, dict)
    )


def uncached_tokens(previous: str, current: str) -> int:
    """Tokens in `current` after the shared byte prefix."""
    shared = 0
    for left, right in zip(previous, current):
        if left != right:
            break
        shared += 1
    return estimate_tokens(current[shared:])


def vm_turns() -> dict:
    """First turn versus a follow-up that reuses the persona prefix."""
    first_user = "What is the capital of Australia?"
    first = shape_messages(
        [{"role": "user", "content": first_user}],
        first_user,
        effort="medium",
    )
    second_user = "And who wrote Pride and Prejudice?"
    second = shape_messages(
        [
            {"role": "user", "content": first_user},
            {"role": "assistant", "content": "Canberra."},
            {"role": "user", "content": second_user},
        ],
        second_user,
        effort="medium",
    )
    first_text = render_prompt(first)
    second_text = render_prompt(second)
    first_tokens = estimate_tokens(first_text)
    second_new = uncached_tokens(first_text, second_text)
    first_ttft = round(first_tokens * MS_PER_TOKEN, 1)
    second_ttft = round(second_new * MS_PER_TOKEN, 1)
    drop = 0.0 if first_ttft <= 0 else round(1 - (second_ttft / first_ttft), 3)
    return {
        "persona_chars": len(PERSONA),
        "first_tokens": first_tokens,
        "second_uncached_tokens": second_new,
        "first_ttft_ms": first_ttft,
        "second_ttft_ms": second_ttft,
        "ttft_drop": drop,
        "prefix_identical": first[0]["content"] == second[0]["content"] == PERSONA,
        "tok_s_note": "decode tok/s is unchanged when the context length is held equal",
    }


def llama_note() -> dict:
    binary = shutil.which("llama-server")
    if not binary:
        return {
            "llama_server": "not installed",
            "compared": False,
            "flags": "--flash-attn -b 128 -ub 128 -t 4 --cache-ram 512 --kv-unified",
        }
    return {
        "llama_server": binary,
        "compared": False,
        "note": "present; Pi run compares it",
    }


def markdown_table(result: dict) -> str:
    turns = result["turns"]
    return "\n".join(
        [
            "| metric | value |",
            "| --- | --- |",
            f"| first TTFT ms | {turns['first_ttft_ms']} |",
            f"| second TTFT ms | {turns['second_ttft_ms']} |",
            f"| TTFT drop | {turns['ttft_drop']} |",
            f"| prefix identical | {turns['prefix_identical']} |",
            f"| llama-server | {result['llama']['llama_server']} |",
        ]
    )


def run_vm() -> dict:
    result = {"where": "vm", "turns": vm_turns(), "llama": llama_note()}
    out = ROOT / "data" / "bench"
    out.mkdir(parents=True, exist_ok=True)
    (out / "vm.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (out / "vm.md").write_text(markdown_table(result) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Bench Flash TTFT on a prefix cache")
    parser.add_argument("--pi", action="store_true", help="Akash runs this on pi4")
    args = parser.parse_args()
    if args.pi:
        print(
            "Run this on pi4 when it is below 60 C. The VM report is data/bench/vm.md"
        )
        return
    result = run_vm()
    print(markdown_table(result))
    drop = result["turns"]["ttft_drop"]
    if drop < 0.4:
        raise SystemExit(f"second-turn TTFT drop {drop} is under 40%")


if __name__ == "__main__":
    main()
