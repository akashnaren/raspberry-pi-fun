"""Wall-clock and Ollama eval timing for one chat.

The final response carries `pi_timing`. The process log always prints the
full breakdown. A public response keeps only `total_ms`, as a number.
"""

from __future__ import annotations

import json

_USAGE = ("prefill_tokens", "prefill_ms", "eval_tokens", "eval_ms")


def blank() -> dict:
    return {
        "queue_ms": 0,
        "search_ms": 0,
        "prefill_tokens": 0,
        "prefill_ms": 0,
        "eval_tokens": 0,
        "eval_ms": 0,
        "total_ms": 0,
    }


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def from_ollama(obj: dict | None) -> dict:
    """Read Ollama's prompt_eval_* and eval_* fields. Durations are nanoseconds."""
    row = obj if isinstance(obj, dict) else {}
    return {
        "prefill_tokens": _as_int(row.get("prompt_eval_count")),
        "prefill_ms": _as_int(row.get("prompt_eval_duration")) // 1_000_000,
        "eval_tokens": _as_int(row.get("eval_count")),
        "eval_ms": _as_int(row.get("eval_duration")) // 1_000_000,
    }


def assemble(
    *,
    queue_ms: int = 0,
    search_ms: int = 0,
    usage: dict | None = None,
    total_ms: int = 0,
) -> dict:
    timing = blank()
    timing["queue_ms"] = max(0, _as_int(queue_ms))
    timing["search_ms"] = max(0, _as_int(search_ms))
    taken = usage if isinstance(usage, dict) else {}
    for key in _USAGE:
        timing[key] = max(0, _as_int(taken.get(key)))
    timing["total_ms"] = max(0, _as_int(total_ms))
    return timing


def present(timing: dict, public: bool):
    """LAN gets the breakdown. The public link gets total_ms only."""
    row = assemble(
        queue_ms=timing.get("queue_ms", 0),
        search_ms=timing.get("search_ms", 0),
        usage=timing,
        total_ms=timing.get("total_ms", 0),
    )
    print("pi_timing " + json.dumps(row), flush=True)
    if public:
        return row["total_ms"]
    return row
