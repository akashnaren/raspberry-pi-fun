"""pi4 inference knobs. Operator file, not a board measurement."""

from __future__ import annotations

import json

from pair.config import ROOT

_DEFAULTS = {
    "model": "qwen3:0.6b",
    "pro_model": "qwen3:1.7b",
    "num_ctx": 2048,
    # -1 keeps the weights loaded. A short duration would reset the server TTL
    # on every chat call and drop Flash or Pro.
    "keep_alive": -1,
    # Sequences in flight on the chat tag being decoded. Ollama sizes that
    # model's key/value cache as num_ctx * this value. Flash is the default
    # tag. Pro is a separate resident tag and uses the same router slot gate.
    # Clamped to 1..4. Default 2. Four sequences at num_ctx 2048 stayed
    # under 1 GB RSS on the previous Flash tag, but that cap was too slow
    # on pi4 (p95 34.9s).
    "ollama_num_parallel": 2,
    # Pi 4 is four Cortex-A72 cores. Ollama forwards num_thread as llama.cpp -t
    # only when the request sets it; otherwise the runner auto-detects.
    "num_thread": 4,
    # Prompt-ingest batch. Ollama's default is 512, which is wider than this
    # board's 1MB L2 wants while a search note is being prefilled.
    "num_batch": 128,
    # Flash is the common path. A shorter context is a smaller key/value cache
    # on the four Pi 4 cores. Pro keeps the full window for code and math.
    "flash_num_ctx": 1536,
    "pro_num_ctx": 2048,
    "flash_num_thread": 4,
    "pro_num_thread": 4,
    "flash_num_batch": 128,
    "pro_num_batch": 64,
    # Characters of search notes pasted into the prompt. Sources on the page
    # are not cut. A shorter note is a shorter prefill.
    "search_note_chars": 640,
    # Characters of one attachment kept in the prompt. The upload route may
    # return more for the composer. The model sees this cut, inside a fence.
    "attachment_chars": 1200,
}


def decode_effort(name: str | None, prompt: str = ""):
    """Low, Medium, or High. None when the caller did not name a level.

    The plan carries Ollama's `think` flag, the Qwen3 sample, and the answer
    budget. Only High thinks, and that cap is separate from the answer.
    """
    from pair.think import decode_plan

    return decode_plan(name, prompt)


def _as_int(value, fallback: int) -> int:
    try:
        if value is None or value == "":
            return int(fallback)
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def mode_limits(model: str, knobs: dict | None = None) -> dict:
    """num_ctx, num_thread, and num_batch for Flash or Pro.

    An unknown tag uses the Flash numbers. num_predict stays with the caller.
    """
    row = knobs if knobs is not None else inference_knobs()
    flash = str(row.get("model") or "")
    pro = str(row.get("pro_model") or "")
    name = str(model or "")
    if pro and name == pro and name != flash:
        prefix = "pro"
        batch_fallback = 64
        ctx_fallback = _as_int(row.get("num_ctx"), 2048)
    else:
        prefix = "flash"
        batch_fallback = _as_int(row.get("num_batch"), 128)
        ctx_fallback = _as_int(
            row.get("flash_num_ctx"), _as_int(row.get("num_ctx"), 2048)
        )
    thread_fallback = _as_int(row.get("num_thread"), 4)
    return {
        "num_ctx": _as_int(row.get(f"{prefix}_num_ctx"), ctx_fallback),
        "num_thread": _as_int(row.get(f"{prefix}_num_thread"), thread_fallback),
        "num_batch": _as_int(row.get(f"{prefix}_num_batch"), batch_fallback),
    }


def ollama_options(
    temperature: float,
    max_tokens: int,
    knobs: dict | None = None,
    model: str | None = None,
) -> dict:
    """Decode options Ollama already accepts. Context and threads follow the mode."""
    row = knobs if knobs is not None else inference_knobs()
    limits = mode_limits(model or "", row)
    options = {
        "temperature": temperature,
        "num_predict": max_tokens,
        "num_ctx": limits["num_ctx"],
    }
    threads = limits.get("num_thread")
    batch = limits.get("num_batch")
    if threads:
        options["num_thread"] = int(threads)
    if batch:
        options["num_batch"] = int(batch)
    return options


def keep_alive(knobs: dict | None = None):
    """The one keep_alive knob for the pi4 brain.

    Chat and stream send this value. -1 matches the Ollama JSON
    number that leaves a model loaded until the process stops, so Flash
    and an opted-in Pro can stay resident together. A missing or blank
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


def attachment_limit(knobs: dict | None = None) -> int:
    row = knobs if knobs is not None else inference_knobs()
    try:
        return max(
            0,
            int(
                row.get("attachment_chars")
                if row.get("attachment_chars") is not None
                else 1200
            ),
        )
    except (TypeError, ValueError):
        return 1200


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


_KNOBS: dict = {"key": None, "data": None}


def inference_knobs() -> dict:
    """Operator file merged over the defaults. Repeat reads skip the disk."""
    path = ROOT / "configs" / "runtime" / "inference_pi4.json"
    try:
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = None
    cached = _KNOBS.get("data")
    if _KNOBS.get("key") == key and isinstance(cached, dict):
        return dict(cached)
    merged = dict(_DEFAULTS)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        loaded = None
    if isinstance(loaded, dict):
        merged.update(loaded)
    _KNOBS["key"] = key
    _KNOBS["data"] = merged
    return dict(merged)
