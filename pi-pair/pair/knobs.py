"""pi4 inference knobs. Operator file, not a board measurement."""
from __future__ import annotations

import json

from pair.config import ROOT

_DEFAULTS = {
    "model": "qwen2.5:0.5b",
    "pro_model": "qwen2.5:1.5b",
    "num_ctx": 2048,
    # -1 keeps the weights loaded. A short duration would reset the server TTL
    # on every chat or embed call and drop Arctic, Flash, or Pro.
    "keep_alive": -1,
    # Sequences in flight on the chat tag being decoded. Ollama sizes that
    # model's key/value cache as num_ctx * this value. Flash is the default
    # tag. Pro is a separate resident tag and uses the same router slot gate.
    # Arctic stays loaded beside them (Ollama runs the embedder at parallel 1).
    # Clamped to 1..4. Default 2. Four sequences of qwen2.5:0.5b at num_ctx
    # 2048 stayed under 1 GB RSS, but that cap was too slow on pi4 (p95 34.9s).
    "ollama_num_parallel": 2,
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


# Flash (qwen2.5:0.5b) and Pro (qwen2.5:1.5b) have no separate reasoning channel.
# These three presets change the decode Ollama already accepts: temperature and
# num_predict. Medium is the default on whichever mode was selected.
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
    number that leaves a model loaded until the process stops, so Arctic,
    Flash, and an opted-in Pro can stay resident together. A missing or blank
    knob is that same default. 0 is left alone: Ollama unloads when the call
    returns. A Flash or Pro chat does not send 0.
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


PARALLEL_MIN = 1
PARALLEL_MAX = 4


def clamp_parallel(value: int) -> int:
    if value < PARALLEL_MIN:
        return PARALLEL_MIN
    if value > PARALLEL_MAX:
        return PARALLEL_MAX
    return value


def parallel_limit(knobs: dict | None = None) -> int:
    """In-flight generations, and the OLLAMA_NUM_PARALLEL the Pi should run."""
    row = knobs if knobs is not None else inference_knobs()
    try:
        value = int(row.get("ollama_num_parallel"))
    except (TypeError, ValueError):
        value = int(_DEFAULTS["ollama_num_parallel"])
    return clamp_parallel(value)


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
