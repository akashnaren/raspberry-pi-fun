"""Board temperature from sysfs. This never changes runner options."""

from __future__ import annotations

import threading
import time

_ZONE = "/sys/class/thermal/thermal_zone0/temp"
_INTERVAL_S = 5.0
_LOCK = threading.Lock()
_SAMPLE = {"temp_c": None, "read_at": 0.0}
_HOT = False


def read_temp_c(path: str = _ZONE) -> float | None:
    """Millidegrees in the thermal zone, or None when the file is missing."""
    try:
        raw = open(path, encoding="utf-8").read().strip()
        value = float(raw)
    except (OSError, ValueError):
        return None
    if value > 200:
        return value / 1000.0
    return value


def sample(now: float | None = None, path: str = _ZONE) -> dict:
    """The latest reading, refreshed at most every five seconds."""
    moment = time.monotonic() if now is None else now
    with _LOCK:
        stale = moment - float(_SAMPLE["read_at"] or 0) >= _INTERVAL_S
        if stale or _SAMPLE["read_at"] == 0:
            _SAMPLE["temp_c"] = read_temp_c(path)
            _SAMPLE["read_at"] = moment
        return {"temp_c": _SAMPLE["temp_c"]}


def above(limit_c: float, now: float | None = None) -> bool:
    """True when the last reading is hotter than `limit_c`."""
    temp = sample(now).get("temp_c")
    if temp is None:
        return False
    return float(temp) > float(limit_c)


def hot(hot_c: float = 75.0, cool_c: float = 68.0) -> bool:
    """Hysteresis on the cached zone reading. A missing reading is not hot.

    The latch turns on at or above `hot_c` and off at or below `cool_c`.
    A missing reading clears the latch. This does not start the sampler thread.
    """
    global _HOT
    temp = sample().get("temp_c")
    with _LOCK:
        if temp is None:
            _HOT = False
            return False
        reading = float(temp)
        if reading >= float(hot_c):
            _HOT = True
        elif reading <= float(cool_c):
            _HOT = False
        return _HOT


def start() -> threading.Thread:
    """Background reader. A chat call does not wait on this."""

    def loop() -> None:
        while True:
            sample()
            time.sleep(_INTERVAL_S)

    thread = threading.Thread(target=loop, name="thermal", daemon=True)
    thread.start()
    return thread
