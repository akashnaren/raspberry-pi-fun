"""Peer health probes and a short cache so phones do not stampede /api/tags.

`/health` returns the last snapshot once one exists. A stale snapshot is
refreshed off the request, so queue, disk, and load scans stay off this
path and one slow poll cannot hold the handler past the peer timeout.
A peer that was up stays up through PEER_GRACE_S after a single miss, so
peers_up does not flap when one probe exceeds PEER_PROBE_S.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

from pair.config import PI2_ALT_PORTS
from pair.guard import may_generate
from pair import runtime

# Peer /health and /api/tags budget. Callers treat a miss past this as down.
PEER_PROBE_S = 2.5
# One timed-out poll stays inside this window. The next miss can drop peers_up.
PEER_GRACE_S = 12.0


def _get_json(url: str, timeout: float = PEER_PROBE_S):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _local_health(peer: dict) -> bool:
    """True when this process is the peer's own /health server."""
    if (peer.get("kind") or "") != "health":
        return False
    role = str(peer.get("role") or "").strip().lower()
    if role not in {"health", "dataset"}:
        return False
    env_role = os.environ.get("PI_PAIR_ROLE", "").strip().lower()
    return env_role == role


def peer_health(peer: dict, *, alt_ports=None, get_json=None):
    if _local_health(peer):
        return True, [], None, peer["port"]
    kind = peer.get("kind") or "ollama"
    ports = [peer["port"]]
    if peer["name"] == "pi2":
        extra = PI2_ALT_PORTS if alt_ports is None else alt_ports
        ports = list(dict.fromkeys([peer["port"], *extra]))
    fetch = get_json or _get_json
    last_err = None
    for port in ports:
        try:
            if kind == "health":
                fetch(f"http://{peer['host']}:{port}/health", timeout=PEER_PROBE_S)
                return True, [], None, port
            if kind == "llamacpp":
                data = fetch(f"http://{peer['host']}:{port}/v1/models", timeout=3.0)
                models = []
                for model in data.get("data") or []:
                    mid = model.get("id") or model.get("name")
                    if mid:
                        models.append(mid)
                return True, models, None, port
            data = fetch(f"http://{peer['host']}:{port}/api/tags", timeout=PEER_PROBE_S)
            models = [item.get("name") or item.get("model") for item in data.get("models", [])]
            models = [name for name in models if name]
            return True, models, None, port
        except Exception as error:
            last_err = str(error)
    return False, [], last_err, peer["port"]


def _row(peer: dict, ok: bool, models, err, port) -> dict:
    return {
        "name": peer["name"],
        "host": peer["host"],
        "port": port,
        "kind": peer.get("kind") or "ollama",
        "ok": ok,
        "models": models,
        "error": err,
        "note": peer.get("note") or "",
        "generative": may_generate(peer),
        "role": peer.get("role") or "",
    }


def _with_grace(rows: list) -> list:
    """Keep a peer that was up when this probe is the one inside the grace window."""
    now = time.monotonic()
    with runtime._health_lock:
        seen = runtime._health_cache.setdefault("seen", {})
        out = []
        for row in rows:
            name = row["name"]
            if row.get("ok"):
                seen[name] = {"ok_at": now, "row": dict(row)}
                out.append(row)
                continue
            prior = seen.get(name)
            ok_at = float(prior.get("ok_at") or 0) if isinstance(prior, dict) else 0.0
            if isinstance(prior, dict) and prior.get("row") and (now - ok_at) < PEER_GRACE_S:
                out.append(dict(prior["row"]))
                continue
            out.append(row)
        return out


def _collect() -> list:
    rows = []
    for peer in list(runtime.PEERS):
        ok, models, err, port = peer_health(peer)
        rows.append(_row(peer, ok, models, err, port))
    return _with_grace(rows)


def _store(peers: list) -> None:
    with runtime._health_lock:
        runtime._health_cache["t"] = time.time()
        runtime._health_cache["peers"] = peers


def _refresh(gen: int) -> None:
    peers = None
    try:
        with runtime._health_lock:
            if runtime._health_cache.get("gen") != gen:
                return
        peers = _collect()
    except Exception:
        peers = None
    finally:
        with runtime._health_lock:
            if peers is not None and runtime._health_cache.get("gen") == gen:
                runtime._health_cache["t"] = time.time()
                runtime._health_cache["peers"] = peers
            if runtime._health_cache.get("gen") == gen:
                runtime._health_cache["refreshing"] = False


def _kick_refresh() -> None:
    """Caller holds the health lock."""
    if runtime._health_cache.get("refreshing"):
        return
    gen = int(runtime._health_cache.get("gen") or 0)
    runtime._health_cache["refreshing"] = True
    thread = threading.Thread(target=_refresh, args=(gen,), name="peer-health", daemon=True)
    runtime._health_cache["thread"] = thread
    thread.start()


def join_refresh(timeout: float = 2.0) -> None:
    """Wait for a background probe. Tests use this so the thread does not leak."""
    with runtime._health_lock:
        thread = runtime._health_cache.get("thread")
    if thread is not None:
        thread.join(timeout)


def snapshot_peers(force: bool = False):
    """Cached peer health. A stale cache returns immediately and refreshes behind it."""
    now = time.time()
    with runtime._health_lock:
        cached = runtime._health_cache["peers"]
        fresh = (
            cached is not None
            and (now - float(runtime._health_cache["t"] or 0)) < runtime.HEALTH_CACHE_TTL
        )
        if not force and fresh:
            return cached
        if not force and cached is not None:
            _kick_refresh()
            return cached
    return _store_collected()


def _store_collected() -> list:
    peers = _collect()
    _store(peers)
    return peers
