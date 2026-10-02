"""pi4 inference knobs. Operator file, not a board measurement."""
from __future__ import annotations

import json

from pair.config import ROOT

_DEFAULTS = {
    "model": "qwen2.5:0.5b",
    "num_ctx": 2048,
    "keep_alive": "5m",
    "ollama_num_parallel": 1,
}


def inference_knobs() -> dict:
    path = ROOT / "configs" / "runtime" / "inference_pi4.json"
    merged = dict(_DEFAULTS)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return merged
    if isinstance(loaded, dict):
        merged.update(loaded)
    return merged
