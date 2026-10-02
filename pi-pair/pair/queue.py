"""Bounded miss queue. Raw rows stay off pi4 and pi2."""
from __future__ import annotations

import json
import os
import threading
import urllib.request
from pathlib import Path

from pair.config import data_root
from pair import runtime

_LOCK = threading.Lock()
QUEUE_BOUND = 128


def node_role() -> str:
    role = os.environ.get("PI_PAIR_ROLE", "").strip().lower()
    if role in ("brain", "health", "dataset"):
        return role
    name = os.environ.get("PI_PAIR_NAME", "").strip().lower()
    if "pi2" in name:
        return "health"
    if "pi4" in name:
        return "brain"
    if "pi3" in name:
        return "dataset"
    return "dataset"


def queue_path(root: Path | None = None) -> Path:
    return (root or data_root()) / "train" / "pending" / "queue.jsonl"


def metrics_path(root: Path | None = None) -> Path:
    return (root or data_root()) / "metrics.json"


def _bump(root: Path, field: str) -> None:
    path = metrics_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = {"hits": 0, "misses": 0}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            current.update({key: int(loaded.get(key) or 0) for key in ("hits", "misses")})
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    current[field] = int(current.get(field) or 0) + 1
    path.write_text(json.dumps(current) + "\n", encoding="utf-8")


def append_row(row: dict, root: Path | None = None, bound: int = QUEUE_BOUND) -> int:
    path = queue_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        lines = []
        if path.exists():
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        lines.append(json.dumps(row, ensure_ascii=False))
        if len(lines) > bound:
            lines = lines[-bound:]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _bump(root or data_root(), "misses")
        return len(lines)


def note_hit(root: Path | None = None) -> None:
    if node_role() != "dataset":
        return
    with _LOCK:
        _bump(root or data_root(), "hits")


def _dataset_peer() -> dict | None:
    for peer in runtime.PEERS:
        if peer.get("role") == "dataset" or peer.get("name") == "pi3":
            return peer
    return None


def forward_row(row: dict, opener=None, timeout: float = 1.5) -> bool:
    peer = _dataset_peer()
    if not peer:
        return False
    port = int(os.environ.get("PI_PAIR_DATASET_PORT", str(peer.get("port") or 18080)))
    url = f"http://{peer['host']}:{port}/v1/flywheel/enqueue"
    request = urllib.request.Request(
        url,
        data=json.dumps(row).encode(),
        headers={"content-type": "application/json"},
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout):
            return True
    except Exception:
        return False


def note_exchange(prompt: str, answer: str, *, chip: str, peer: str, train: bool) -> None:
    try:
        if not train:
            note_hit()
            return
        text = (prompt or "").strip()
        reply = (answer or "").strip()
        if not text or not reply:
            return
        row = {"prompt": text, "answer": reply, "chip": chip, "peer": peer}
        if node_role() == "dataset":
            append_row(row)
            return
        forward_row(row)
    except Exception:
        return
