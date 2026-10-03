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


# qwen2.5:0.5b has no separate reasoning channel. These three presets change the
# decode Ollama already accepts: temperature and num_predict. Medium is the default.
EFFORT = {
    "low": {"temperature": 0.6, "num_predict": 64},
    "medium": {"temperature": 0.7, "num_predict": 256},
    "high": {"temperature": 0.8, "num_predict": 768},
}


def decode_effort(name: str | None) -> tuple[str, float, int] | None:
    key = (name or "").strip().lower()
    row = EFFORT.get(key)
    if not row:
        return None
    return key, float(row["temperature"]), int(row["num_predict"])


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
