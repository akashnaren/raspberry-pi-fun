"""Mesh offload. pi4 generates. pi2 searches. A down pi2 falls back on pi4."""

from __future__ import annotations

import json
import os
import time
from urllib.parse import urlparse

import http.client

from pair.core.config import ROOT
from pair.flywheel.miss_queue import node_role
from pair.search.lookup import lookup_web
from pair.core import runtime

MESH_PATH = ROOT / "configs" / "runtime" / "mesh_roles.json"
DEFAULT_DATASET = "akashnaren/pi-mesh-labels"
_DEFAULTS = {
    "generate": ["pi4"],
    "decode": ["pi4"],
    "search": ["pi2"],
    "health": ["pi2"],
    "dataset_and_train": ["pi3"],
    "public_labels": ["pi3"],
    "remote_search_connect_timeout_s": 0.6,
    "remote_search_read_timeout_s": 3,
    "hf_dataset": DEFAULT_DATASET,
}
_CONNECT_CAP = 2.0
_READ_CAP = 12.0


def mesh_config() -> dict:
    merged = dict(_DEFAULTS)
    try:
        loaded = json.loads(MESH_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return merged
    if isinstance(loaded, dict):
        merged.update(loaded)
    return merged


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _clamp(value: float, cap: float, fallback: float) -> float:
    if value <= 0 or value > cap:
        return fallback
    return value


def search_timeouts() -> tuple[float, float]:
    """Connect stays short so a dead pi2 cannot stall pi4. Read covers one lookup."""
    cfg = mesh_config()
    connect_default = float(cfg.get("remote_search_connect_timeout_s") or 0.6)
    read_default = float(cfg.get("remote_search_read_timeout_s") or 3)
    connect = _env_float("PI_PAIR_REMOTE_SEARCH_CONNECT_TIMEOUT", connect_default)
    read = _env_float("PI_PAIR_REMOTE_SEARCH_TIMEOUT", read_default)
    return (
        _clamp(connect, _CONNECT_CAP, 0.6),
        _clamp(read, _READ_CAP, 3.0),
    )


def remote_search_enabled() -> bool:
    raw = os.environ.get("PI_PAIR_REMOTE_SEARCH", "").strip().lower()
    if raw in ("0", "off", "false", "no"):
        return False
    if raw in ("1", "on", "true", "yes"):
        return True
    return node_role() == "brain"


def search_peer() -> dict | None:
    peers = list(runtime.PEERS)
    for peer in peers:
        if peer.get("name") == "pi2":
            return peer
    for peer in peers:
        if peer.get("role") == "health" and peer.get("name") != "pi4":
            return peer
    return None


def _valid_search(raw: bytes) -> dict | None:
    try:
        data = json.loads(raw.decode("utf-8", "replace") or "{}")
    except (json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    if status not in ("ok", "failed"):
        return None
    sources = data.get("sources")
    context = data.get("context")
    if not isinstance(sources, list) or not isinstance(context, str):
        return None
    return {"status": status, "sources": sources, "context": context}


def _http_post(
    url: str, payload: dict, connect_s: float, read_s: float
) -> tuple[int, bytes]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or 80
    path = parsed.path or "/"
    body = json.dumps(payload).encode()
    conn = http.client.HTTPConnection(host, port, timeout=connect_s)
    try:
        conn.connect()
        if conn.sock is not None:
            conn.sock.settimeout(read_s)
        conn.request(
            "POST",
            path,
            body=body,
            headers={
                "content-type": "application/json",
                "content-length": str(len(body)),
            },
        )
        response = conn.getresponse()
        return response.status, response.read(65536)
    finally:
        conn.close()


def lookup_remote(query: str) -> dict | None:
    """POST /v1/search on pi2. None means pi4 should look locally."""
    peer = search_peer()
    if not peer:
        return None
    text = " ".join((query or "").split())
    if not text:
        return None
    port_raw = os.environ.get("PI_PAIR_SEARCH_PORT", "").strip()
    try:
        port = int(port_raw) if port_raw else int(peer["port"])
    except (TypeError, ValueError):
        return None
    url = f"http://{peer['host']}:{port}/v1/search"
    connect_s, read_s = search_timeouts()
    try:
        status, raw = _http_post(url, {"q": text}, connect_s, read_s)
    except Exception:
        return None
    if status != 200:
        return None
    return _valid_search(raw)


def lookup_for_brain(
    query: str,
    *,
    local=None,
    limit: int | None = None,
    deadline: float | None = None,
) -> dict:
    """pi4 chat search. Remote pi2 first, then the local lookup if pi2 is down.

    `deadline` is a monotonic timestamp. When less than 1.5s remains, the
    local fallback is skipped so a dead pi2 cannot eat the rest of the budget.
    """
    local_fn = local or lookup_web
    if remote_search_enabled():
        found = lookup_remote(query)
        if found is not None:
            copied = dict(found)
            copied["via"] = "pi2"
            return copied
    if deadline is not None and deadline - time.monotonic() < 1.5:
        failed = {"status": "failed", "sources": [], "context": "", "via": "budget"}
        return failed
    if limit is not None and local_fn is lookup_web:
        result = local_fn(query, limit=limit)
    else:
        result = local_fn(query)
    if isinstance(result, dict):
        copied = dict(result)
        copied["via"] = "local"
        return copied
    return result
