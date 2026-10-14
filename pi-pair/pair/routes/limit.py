"""Per-IP token bucket for public traffic. Stdlib only.

LAN peers are not counted. The caller passes the client address from the
trusted-proxy helper. IPv6 addresses share a /64. IPv4 keeps the full address.
"""

from __future__ import annotations

import ipaddress
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

    def refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated)
        self.updated = now
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)

    def take(self, now: float) -> tuple[bool, int]:
        self.refill(now)
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


def _prune(now: float) -> None:
    if len(_BUCKETS) <= _CAP:
        return
    for bucket in _BUCKETS.values():
        bucket.refill(now)
    idle = [key for key, bucket in _BUCKETS.items() if bucket.tokens >= bucket.burst]
    for key in idle:
        _BUCKETS.pop(key, None)
    while len(_BUCKETS) > _CAP:
        _BUCKETS.pop(next(iter(_BUCKETS)), None)


def _bucket_id(ip: str) -> str:
    """IPv6 shares a /64. IPv4, including a v4-mapped address, stays whole."""
    text = (ip or "").strip()
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return text or "-"
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def admit(ip: str, path: str, now: float | None = None) -> tuple[bool, int]:
    """Take one token. The second value is Retry-After seconds when refused."""
    kind = route_class(path)
    key = f"{kind}:{_bucket_id(ip)}"
    moment = time.monotonic() if now is None else now
    rate, burst = _limit(kind)
    with _LOCK:
        bucket = _BUCKETS.get(key)
        if bucket is None or bucket.rate != rate or bucket.burst != burst:
            bucket = _Bucket(rate, burst, moment)
            _BUCKETS[key] = bucket
            _prune(moment)
        return bucket.take(moment)
