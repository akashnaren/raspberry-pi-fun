"""pi4 inference knobs. Operator file, not a board measurement."""
from __future__ import annotations

import json

from pair.config import ROOT

_DEFAULTS = {
    "model": "qwen2.5:0.5b",
    "num_ctx": 2048,
    # -1 keeps the weights loaded. A short duration would reset the server TTL
    # on every chat or embed call and unload the other model.
    "keep_alive": -1,
    "ollama_num_parallel": 1,
    # Pi 4 is four Cortex-A72 cores. Ollama forwards num_thread as llama.cpp -t
    # only when the request sets it; otherwise the runner auto-detects.
    "num_thread": 4,
    # Prompt-ingest batch. Ollama's default is 512, which is wider than this
    # board's 1MB L2 wants while a search note is being prefilled.
    "num_batch": 128,
    # Characters of search notes pasted into the prompt. Sources on the page
    # are not cut. A shorter note is a shorter prefill.
    "search_note_chars": 640,
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


def ollama_options(temperature: float, max_tokens: int, knobs: dict | None = None) -> dict:
    """Decode options Ollama already accepts. The model name is not one of them."""
    row = knobs if knobs is not None else inference_knobs()
    options = {
        "temperature": temperature,
        "num_predict": max_tokens,
        "num_ctx": int(row.get("num_ctx") or 2048),
    }
    threads = row.get("num_thread")
    batch = row.get("num_batch")
    if threads:
        options["num_thread"] = int(threads)
    if batch:
        options["num_batch"] = int(batch)
    return options


def keep_alive(knobs: dict | None = None):
    """The one keep_alive knob for the pi4 brain.

    Chat, stream, and embed all send this value. -1 matches the Ollama JSON
    number that leaves a model loaded until the process stops, so arctic-embed
    and qwen can stay resident together. A missing or blank knob is that same
    default. 0 is left alone: Ollama unloads when the call returns.
    """
    row = knobs if knobs is not None else inference_knobs()
    if "keep_alive" not in row:
        return -1
    value = row.get("keep_alive")
    if value is None or value == "":
        return -1
    return value


def search_note_limit(knobs: dict | None = None) -> int:
    row = knobs if knobs is not None else inference_knobs()
    try:
        return max(0, int(row.get("search_note_chars") or 0))
    except (TypeError, ValueError):
        return 0


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
