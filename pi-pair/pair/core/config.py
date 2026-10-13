"""Listen settings and the peer list. Stdlib only."""

from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import socket
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "static"

DEFAULT_MODEL = "qwen3:0.6b"
# Legacy llama.cpp probe, only if a peer is still kind llamacpp and named pi2.
PI2_ALT_PORTS = [8080, 11434]
WEAK_NAMES = frozenset({"pi2", "pi3"})

# Fleet map. Generative completion is pi4 only; normalize_peer enforces that.
DEFAULT_PEERS = [
    {
        "name": "pi2",
        "host": "10.0.0.180",
        "port": 18080,
        "kind": "health",
        "note": "armv7 ~1GB; health, web search, and read-only canned mirror. No decode.",
        "generative": False,
        "role": "health",
    },
    {
        "name": "pi3",
        "host": "10.0.0.228",
        "port": 18080,
        "kind": "health",
        "note": "arm64 ~1GB; dataset, queue, train-then-delete, and public hashed votes. No decode.",
        "generative": False,
        "role": "dataset",
    },
    {
        "name": "pi4",
        "host": "10.0.0.166",
        "port": 11434,
        "kind": "ollama",
        "note": "",
        "generative": True,
        "role": "brain",
    },
]


# Tailscale's IPv4 pool. Python does not mark this range private.
_TAILNET = ipaddress.ip_network("100.64.0.0/10")
_SIOCGIFADDR = 0x8915


def _iface_ipv4(name: str) -> str:
    """Primary IPv4 on one interface. Empty when the interface has none."""
    raw = name.encode("utf-8")
    if not raw or len(raw) > 15:
        return ""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, struct.pack("256s", raw))
    except OSError:
        return ""
    finally:
        sock.close()
    try:
        return socket.inet_ntoa(packed[20:24])
    except OSError:
        return ""


def local_ipv4() -> list[tuple[str, str]]:
    """IPv4 addresses on this machine, as (interface, address)."""
    try:
        names = os.listdir("/sys/class/net")
    except OSError:
        return []
    found: list[tuple[str, str]] = []
    for name in names:
        if name == "lo":
            continue
        addr = _iface_ipv4(name)
        if addr:
            found.append((name, addr))
    return found


def _on_tailnet(name: str, addr: str) -> bool:
    if name.startswith("tailscale"):
        return True
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.version == 4 and ip in _TAILNET


def tailnet_addrs() -> list[str]:
    """Addresses pi2 and pi3 can still dial. Not the LAN NIC."""
    found: list[str] = []
    for name, addr in local_ipv4():
        if _on_tailnet(name, addr) and addr not in found:
            found.append(addr)
    return found


def listen_hosts() -> list[str]:
    """Bind list. Unset is loopback plus the tailnet, not every interface.

    PI_PAIR_HOST overrides that. Commas separate addresses. 0.0.0.0 still
    means every interface when an operator sets it on purpose.
    """
    raw = os.environ.get("PI_PAIR_HOST", "").strip()
    if raw:
        hosts: list[str] = []
        for item in raw.split(","):
            host = item.strip()
            if host and host not in hosts:
                hosts.append(host)
        return hosts or ["127.0.0.1"]
    hosts = ["127.0.0.1"]
    for addr in tailnet_addrs():
        if addr not in hosts:
            hosts.append(addr)
    return hosts


def listen_host() -> str:
    return listen_hosts()[0]


def listen_port() -> int:
    return int(os.environ.get("PI_PAIR_PORT", "18080"))


def default_model() -> str:
    return os.environ.get("MESH_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL


def infer_slots() -> int:
    """Generations allowed at once. PI_PAIR_SLOTS overrides the runtime file.

    The override is clamped to 1..4. Unset means the `ollama_num_parallel`
    knob, which is the same number install.sh prints for Ollama.
    """
    from pair.model.knobs import clamp_parallel, parallel_limit

    raw = os.environ.get("PI_PAIR_SLOTS", "").strip()
    if not raw:
        return parallel_limit()
    try:
        return clamp_parallel(int(raw))
    except ValueError:
        return parallel_limit()


def health_ttl() -> float:
    return float(os.environ.get("PI_PAIR_HEALTH_TTL", "2.5"))


def data_root() -> Path:
    raw = os.environ.get("PI_PAIR_DATA", "").strip()
    if raw:
        return Path(raw)
    return ROOT / "data"


def normalize_peer(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("peer entry must be an object")
    name = str(raw.get("name") or "").strip()
    host = str(raw.get("host") or "").strip()
    if not name or not host:
        raise ValueError("peer needs name and host")
    kind = str(raw.get("kind") or "ollama").strip() or "ollama"
    if kind not in ("ollama", "llamacpp", "health"):
        raise ValueError(f"unknown peer kind {kind}")
    if "port" not in raw or raw.get("port") in ("", None):
        raise ValueError(f"{name} needs port")
    if "generative" in raw:
        generative = bool(raw.get("generative"))
    else:
        generative = name == "pi4"
    if name in WEAK_NAMES:
        generative = False
    role = str(raw.get("role") or "").strip()
    if not role:
        role = {"pi2": "health", "pi3": "dataset", "pi4": "brain"}.get(name, "")
    if name in WEAK_NAMES:
        role = "health" if name == "pi2" else "dataset"
    return {
        "name": name,
        "host": host,
        "port": int(raw["port"]),
        "kind": kind,
        "note": str(raw.get("note") or ""),
        "generative": generative,
        "role": role,
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


def on_pi4() -> bool:
    """True only for the brain role. That role is pi4."""
    role = os.environ.get("PI_PAIR_ROLE", "").strip().lower()
    if role in ("brain", "health", "dataset"):
        return role == "brain"
    name = os.environ.get("PI_PAIR_NAME", "").strip().lower()
    return "pi4" in name


def ollama_base() -> str:
    """Origin of the local Ollama used by the Pro preload. Not the chat peer."""
    raw = os.environ.get("PI_PAIR_OLLAMA", "").strip()
    if not raw:
        raw = os.environ.get("OLLAMA_HOST", "").strip()
    if not raw:
        return "http://127.0.0.1:11434"
    if raw.startswith("http://") or raw.startswith("https://"):
        base = raw
    else:
        base = "http://" + raw
    # 0.0.0.0 is a bind address. The client has to use the loopback.
    base = base.replace("://0.0.0.0", "://127.0.0.1", 1)
    return base.rstrip("/")
