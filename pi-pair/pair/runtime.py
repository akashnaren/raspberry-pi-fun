"""Process-wide peer list, health cache, and the generation cap."""

from __future__ import annotations

import threading

from pair.config import (
    default_model,
    health_ttl,
    infer_slots,
    listen_host,
    listen_port,
    load_peers,
)
from pair.gate import InferenceGate
from pair.knobs import clamp_parallel, queue_limit

PEERS: list[dict] = []
MODEL = default_model()
HOST = listen_host()
PORT = listen_port()
INFER_SLOTS = infer_slots()
HEALTH_CACHE_TTL = health_ttl()

_health_lock = threading.Lock()
_health_cache = {
    "t": 0.0,
    "peers": None,
    "seen": {},
    "gen": 0,
    "refreshing": False,
    "thread": None,
}
gate = InferenceGate(INFER_SLOTS, queue_limit=queue_limit())
# Live turns, keyed by X-Pi-Request-Id, so a retry can cancel the one before it.
requests: dict = {}


def configure() -> None:
    """Read env and peers.json. Safe to call again in tests."""
    global PEERS, MODEL, HOST, PORT, INFER_SLOTS, HEALTH_CACHE_TTL, gate
    PEERS = load_peers()
    MODEL = default_model()
    HOST = listen_host()
    PORT = listen_port()
    INFER_SLOTS = infer_slots()
    HEALTH_CACHE_TTL = health_ttl()
    gate = InferenceGate(INFER_SLOTS, queue_limit=queue_limit())
    requests.clear()
    reset_health()


def set_infer_slots(limit: int) -> None:
    """Pin the cap. Tests use this so they do not depend on process env."""
    global INFER_SLOTS, gate
    INFER_SLOTS = clamp_parallel(int(limit))
    gate = InferenceGate(INFER_SLOTS, queue_limit=queue_limit())


def set_peers(peers: list[dict]) -> None:
    global PEERS
    PEERS = [dict(peer) for peer in peers]
    reset_health()


def reset_health() -> None:
    with _health_lock:
        _health_cache["t"] = 0.0
        _health_cache["peers"] = None
        _health_cache["seen"] = {}
        _health_cache["gen"] = int(_health_cache.get("gen") or 0) + 1
        _health_cache["refreshing"] = False
        _health_cache["thread"] = None


configure()
