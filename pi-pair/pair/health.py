"""Peer health probes and a short cache so phones do not stampede /api/tags."""
from __future__ import annotations

import json
import time
import urllib.request

from pair.config import PI2_ALT_PORTS
from pair import runtime


def _get_json(url: str, timeout: float = 2.5):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode())


def peer_health(peer: dict, *, alt_ports=None, get_json=None):
    kind = peer.get("kind") or "ollama"
    ports = [peer["port"]]
    if peer["name"] == "pi2":
        extra = PI2_ALT_PORTS if alt_ports is None else alt_ports
        ports = list(dict.fromkeys([peer["port"], *extra]))
    fetch = get_json or _get_json
    last_err = None
    for port in ports:
        try:
            if kind == "llamacpp":
                data = fetch(f"http://{peer['host']}:{port}/v1/models", timeout=3.0)
                models = []
                for model in data.get("data") or []:
                    mid = model.get("id") or model.get("name")
                    if mid:
                        models.append(mid)
                return True, models, None, port
            data = fetch(f"http://{peer['host']}:{port}/api/tags", timeout=2.5)
            models = [item.get("name") or item.get("model") for item in data.get("models", [])]
            models = [name for name in models if name]
            return True, models, None, port
        except Exception as error:
            last_err = str(error)
    return False, [], last_err, peer["port"]


def snapshot_peers(force: bool = False):
    """Cached peer health (~HEALTH_CACHE_TTL) so phones don't stampede tags."""
    now = time.time()
    with runtime._health_lock:
        cached = runtime._health_cache["peers"]
        if (
            not force
            and cached is not None
            and (now - runtime._health_cache["t"]) < runtime.HEALTH_CACHE_TTL
        ):
            return cached
    peers = []
    for peer in runtime.PEERS:
        ok, models, err, port = peer_health(peer)
        peers.append(
            {
                "name": peer["name"],
                "host": peer["host"],
                "port": port,
                "kind": peer.get("kind") or "ollama",
                "ok": ok,
                "models": models,
                "error": err,
                "note": peer.get("note") or "",
            }
        )
    with runtime._health_lock:
        runtime._health_cache["t"] = time.time()
        runtime._health_cache["peers"] = peers
    return peers
