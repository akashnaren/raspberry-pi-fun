"""Process-wide peer list, health cache, and inference slots."""
from __future__ import annotations

import threading

from pair.config import default_model, health_ttl, infer_slots, listen_host, listen_port, load_peers

PEERS: list[dict] = []
MODEL = default_model()
HOST = listen_host()
PORT = listen_port()
INFER_SLOTS = infer_slots()
HEALTH_CACHE_TTL = health_ttl()

_health_lock = threading.Lock()
_health_cache = {"t": 0.0, "peers": None}
_infer_sem = threading.Semaphore(INFER_SLOTS)


def configure() -> None:
    """Read env and peers.json. Safe to call again in tests."""
    global PEERS, MODEL, HOST, PORT, INFER_SLOTS, HEALTH_CACHE_TTL, _infer_sem
    PEERS = load_peers()
    MODEL = default_model()
    HOST = listen_host()
    PORT = listen_port()
    INFER_SLOTS = infer_slots()
    HEALTH_CACHE_TTL = health_ttl()
    _infer_sem = threading.Semaphore(INFER_SLOTS)
    reset_health()


def set_peers(peers: list[dict]) -> None:
    global PEERS
    PEERS = [dict(peer) for peer in peers]
    reset_health()


def reset_health() -> None:
    with _health_lock:
        _health_cache["t"] = 0.0
        _health_cache["peers"] = None


configure()
