"""Listen settings and the peer list. Stdlib only."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"

DEFAULT_MODEL = "qwen2.5:0.5b"
# pi2 is llama.cpp on :8080 and sometimes answers Ollama-shaped probes on :11434.
PI2_ALT_PORTS = [8080, 11434]

# Fleet the Pi chat used when peers were hardcoded. peers.json overrides this.
DEFAULT_PEERS = [
    {
        "name": "pi2",
        "host": "10.0.0.180",
        "port": 8080,
        "kind": "llamacpp",
        "note": "llama.cpp",
    },
    {
        "name": "pi3",
        "host": "10.0.0.228",
        "port": 11434,
        "kind": "ollama",
        "note": "",
    },
    {
        "name": "pi4",
        "host": "10.0.0.166",
        "port": 11434,
        "kind": "ollama",
        "note": "",
    },
]


def listen_host() -> str:
    return os.environ.get("PI_PAIR_HOST", "0.0.0.0") or "0.0.0.0"


def listen_port() -> int:
    return int(os.environ.get("PI_PAIR_PORT", "18080"))


def default_model() -> str:
    return os.environ.get("MESH_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL


def infer_slots() -> int:
    return int(os.environ.get("PI_PAIR_SLOTS", "3"))


def health_ttl() -> float:
    return float(os.environ.get("PI_PAIR_HEALTH_TTL", "2.5"))


def normalize_peer(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("peer entry must be an object")
    name = str(raw.get("name") or "").strip()
    host = str(raw.get("host") or "").strip()
    if not name or not host:
        raise ValueError("peer needs name and host")
    kind = str(raw.get("kind") or "ollama").strip() or "ollama"
    if kind not in ("ollama", "llamacpp"):
        raise ValueError(f"unknown peer kind {kind}")
    if "port" not in raw or raw.get("port") in ("", None):
        raise ValueError(f"{name} needs port")
    return {
        "name": name,
        "host": host,
        "port": int(raw["port"]),
        "kind": kind,
        "note": str(raw.get("note") or ""),
    }


def load_peers(path: Path | None = None) -> list[dict]:
    """peers.json if present, else the built-in fleet. PI_PAIR_PEERS wins."""
    if path is None:
        raw = os.environ.get("PI_PAIR_PEERS", "").strip()
        if raw:
            path = Path(raw)
        else:
            candidate = ROOT / "peers.json"
            path = candidate if candidate.exists() else None
    if path is None:
        return [dict(peer) for peer in DEFAULT_PEERS]
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "peers" in data:
        data = data["peers"]
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a list of peers")
    return [normalize_peer(peer) for peer in data]
