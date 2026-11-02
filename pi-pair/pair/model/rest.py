"""Hold non-urgent Pi 4 background work while the board stays hot."""

from __future__ import annotations

import os

from pair.core.thermal import hot
from pair.model.knobs import inference_knobs


def _limits() -> tuple[float, float] | None:
    if os.environ.get("PI_PAIR_REST", "").strip() == "0":
        return None
    row = inference_knobs().get("rest")
    if not isinstance(row, dict) or row.get("enabled") is not True:
        return None
    try:
        hot_c = float(row["hot_c"])
        cool_c = float(row["cool_c"])
    except (KeyError, TypeError, ValueError):
        return None
    if cool_c >= hot_c:
        return None
    return hot_c, cool_c


def enabled() -> bool:
    """True when the rest block is valid and PI_PAIR_REST is not 0."""
    return _limits() is not None


def defer_nonurgent() -> bool:
    """True when rest is on and the cached reading is in the hot latch."""
    limits = _limits()
    if limits is None:
        return False
    return hot(hot_c=limits[0], cool_c=limits[1])
