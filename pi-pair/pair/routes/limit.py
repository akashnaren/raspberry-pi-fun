"""Per-IP token bucket for public traffic. Stdlib only.

LAN peers are not counted. The caller passes the client address from the
trusted-proxy helper, so a spoofed X-Forwarded-For cannot pick a bucket.
"""

from __future__ import annotations

import os
import threading
import time

# count/seconds. Chat, upload, search, and images are tighter than the rest.
_DEFAULTS = {
    "default": (60, 60),
    "chat": (12, 60),
    "upload": (6, 60),
    "search": (20, 60),
    "images": (20, 60),
}
_ENV = {
    "default": "PI_PAIR_RATE_DEFAULT",
    "chat": "PI_PAIR_RATE_CHAT",
    "upload": "PI_PAIR_RATE_UPLOAD",
    "search": "PI_PAIR_RATE_SEARCH",
    "images": "PI_PAIR_RATE_IMAGES",
}
_LOCK = threading.Lock()
_CAP = 4096


class _Bucket:
    def __init__(self, rate: float, burst: float, now: float) -> None:
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.updated = now

    def take(self, now: float) -> tuple[bool, int]:
        elapsed = max(0.0, now - self.updated)
        self.updated = now
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True, 0
        missing = 1.0 - self.tokens
        wait = missing / self.rate if self.rate > 0 else 60.0
        return False, min(86400, max(1, int(wait + 0.999)))


_BUCKETS: dict[str, _Bucket] = {}


def reset_limits() -> None:
    """Drop every bucket. Tests call this after changing the env."""
    with _LOCK:
        _BUCKETS.clear()


def route_class(path: str) -> str:
    """Which bucket a path draws from. Unknown paths use the default."""
    path = (path or "").split("?", 1)[0]
    if path in ("/v1/chat/completions", "/api/chat"):
        return "chat"
    if path == "/v1/attachments" or path.startswith("/v1/files"):
        return "upload"
    if path == "/v1/search":
        return "search"
    if path == "/v1/images":
        return "images"
    return "default"


def _parse(raw: str) -> tuple[int, int] | None:
    if "/" not in raw:
        return None
    left, right = raw.split("/", 1)
    try:
        count = int(left.strip())
        window = int(right.strip())
    except ValueError:
        return None
    if count < 1 or window < 1 or count > 100_000 or window > 86_400:
        return None
    return count, window


def _limit(kind: str) -> tuple[float, float]:
    """Tokens per second, and the burst, for one class."""
    count, window = _DEFAULTS[kind]
    parsed = _parse(os.environ.get(_ENV[kind], "").strip())
    if parsed is not None:
        count, window = parsed
    return count / window, float(count)


def _prune() -> None:
    if len(_BUCKETS) <= _CAP:
        return
    idle = [key for key, bucket in _BUCKETS.items() if bucket.tokens >= bucket.burst]
    for key in idle:
        _BUCKETS.pop(key, None)
    while len(_BUCKETS) > _CAP:
        _BUCKETS.pop(next(iter(_BUCKETS)), None)


def admit(ip: str, path: str, now: float | None = None) -> tuple[bool, int]:
    """Take one token. The second value is Retry-After seconds when refused."""
    kind = route_class(path)
    key = f"{kind}:{ip or '-'}"
    moment = time.monotonic() if now is None else now
    rate, burst = _limit(kind)
    with _LOCK:
        bucket = _BUCKETS.get(key)
        if bucket is None or bucket.rate != rate or bucket.burst != burst:
            bucket = _Bucket(rate, burst, moment)
            _BUCKETS[key] = bucket
            _prune()
        return bucket.take(moment)
