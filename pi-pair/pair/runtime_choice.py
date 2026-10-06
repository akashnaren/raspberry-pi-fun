"""Which decoder to keep. Numbers come from a bench; this only applies the gates.

Draft-model speculation is rejected: Flash is CPU-bound, so checking draft
tokens costs about as much as writing them, and a second model uses pi4 RAM.
"""

from __future__ import annotations

KL_LIMIT = 0.01
DECODE_GAIN = 0.05
PREFILL_GAIN = 0.20


def kl_ok(candidate: float, baseline: float, limit: float = KL_LIMIT) -> bool:
    """True when the candidate's KL is at most `limit` above the baseline."""
    return float(candidate) - float(baseline) <= float(limit)


def quality_held(
    *,
    kl_candidate: float,
    kl_baseline: float,
    eval_within_se: bool,
    category_drop: float,
) -> bool:
    """KL gate plus the 200-item rule: within one standard error, no category down more than 3."""
    return (
        kl_ok(kl_candidate, kl_baseline)
        and bool(eval_within_se)
        and float(category_drop) <= 3.0
    )


def choose_decoder(
    *,
    ollama_decode: float,
    llama_decode: float,
    ollama_prefill: float,
    llama_prefill: float,
    quality_held: bool,
) -> str:
    """llama-server only when it is clearly faster and quality holds. Otherwise Ollama."""
    if not quality_held:
        return "ollama"
    decode_faster = float(llama_decode) >= float(ollama_decode) * (1.0 + DECODE_GAIN)
    prefill_faster = float(llama_prefill) >= float(ollama_prefill) * (
        1.0 + PREFILL_GAIN
    )
    if decode_faster or prefill_faster:
        return "llama-server"
    return "ollama"


def output_layer_ok(*, kl_delta: float, eval_flat: bool) -> bool:
    """Keep a Q4_K output layer only when KL rises by at most 0.01 and eval is flat."""
    return float(kl_delta) <= KL_LIMIT and bool(eval_flat)
