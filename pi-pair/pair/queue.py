"""Bounded miss queue. Raw rows stay off pi4 and pi2."""
from __future__ import annotations

import json
import os
import shutil
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


def label_high_water_bytes() -> int:
    raw = os.environ.get("PI_PAIR_LABEL_HIGH_WATER_BYTES", "").strip()
    if raw:
        return max(0, int(raw))
    return 1_048_576


def disk_high_water() -> float:
    raw = os.environ.get("PI_PAIR_DISK_HIGH_WATER", "").strip()
    if raw:
        return float(raw)
    return 0.85


def _pending_jsonl(root: Path) -> list[Path]:
    pending = root / "train" / "pending"
    if not pending.is_dir():
        return []
    files = [path for path in pending.iterdir() if path.is_file() and path.suffix == ".jsonl"]
    files.sort(key=lambda path: (path.stat().st_mtime, path.name))
    return files


def _label_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in _pending_jsonl(root))


def _disk_over(root: Path) -> bool:
    pending = root / "train" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(pending)
    if usage.total <= 0:
        return False
    return (usage.used / usage.total) >= disk_high_water()


def shed_raw_labels(root: Path) -> list[str]:
    """Drop the oldest raw queue text when the card or the label files are past the mark.

    The canned map is not in this directory and is not deleted. Caller holds _LOCK.
    """
    removed: list[str] = []
    for _ in range(10000):
        over_bytes = _label_bytes(root) > label_high_water_bytes()
        over_disk = _disk_over(root)
        if not over_bytes and not over_disk:
            break
        files = _pending_jsonl(root)
        siblings = [path for path in files if path.name != "queue.jsonl"]
        if siblings:
            siblings[0].unlink()
            removed.append(siblings[0].name)
            continue
        queue = queue_path(root)
        if not queue.is_file():
            break
        lines = [line for line in queue.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            queue.unlink()
            removed.append(queue.name)
            break
        lines = lines[1:]
        if lines:
            queue.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            queue.unlink()
        removed.append("oldest-row")
        if over_disk and not over_bytes:
            # One row will not free a full card. Stop once the raw queue is gone.
            if not lines:
                break
            if _label_bytes(root) == 0:
                break
    return removed


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
        shed_raw_labels(root or data_root())
        if not path.exists():
            return 0
        kept = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return len(kept)


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


def _post_dataset(path: str, payload: dict, opener=None, timeout: float = 1.5) -> bool:
    peer = _dataset_peer()
    if not peer:
        return False
    port = int(os.environ.get("PI_PAIR_DATASET_PORT", str(peer.get("port") or 18080)))
    url = f"http://{peer['host']}:{port}{path}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout):
            return True
    except Exception:
        return False


def forward_row(row: dict, opener=None, timeout: float = 1.5) -> bool:
    return _post_dataset("/v1/flywheel/enqueue", row, opener, timeout)


def forward_feedback(payload: dict, opener=None, timeout: float = 1.5) -> bool:
    return _post_dataset("/v1/flywheel/feedback", payload, opener, timeout)


def apply_label(
    prompt: str,
    answer: str,
    vote: str,
    correction: str = "",
    *,
    chip: str = "",
    peer: str = "",
    root: Path | None = None,
    bound: int = QUEUE_BOUND,
) -> dict:
    """Attach a human vote to a queue row post_train already reads.

    A down vote with no correction stays on the row and is dropped at fold time.
    A correction is stored beside the original answer. The dataset role writes
    the file. Any other role forwards the same object to pi3.
    """
    choice = str(vote or "").strip().lower()
    if choice not in ("up", "down"):
        raise ValueError("vote must be up or down")
    text = (prompt or "").strip()
    reply = (answer or "").strip()
    fixed = str(correction or "").strip()
    if not text or not reply:
        raise ValueError("label needs the prompt and the answer")
    if len(text) > 4000 or len(reply) > 8000 or len(fixed) > 8000:
        raise ValueError("label is too long")
    payload = {
        "prompt": text,
        "answer": reply,
        "vote": choice,
        "chip": chip or "",
        "peer": peer or "",
    }
    if fixed:
        payload["correction"] = fixed
    if node_role() != "dataset":
        return {"ok": forward_feedback(payload), "forwarded": True, "queued": 0, "row": payload}
    path = queue_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        raw_lines = []
        if path.exists():
            raw_lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        updated = False
        for index in range(len(raw_lines) - 1, -1, -1):
            try:
                item = json.loads(raw_lines[index])
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            if str(item.get("prompt") or "").strip() != text:
                continue
            if str(item.get("answer") or "").strip() != reply:
                continue
            item["vote"] = choice
            if fixed:
                item["correction"] = fixed
            else:
                item.pop("correction", None)
            if chip:
                item["chip"] = chip
            if peer:
                item["peer"] = peer
            raw_lines[index] = json.dumps(item, ensure_ascii=False)
            payload = item
            updated = True
            break
        if not updated:
            raw_lines.append(json.dumps(payload, ensure_ascii=False))
            if len(raw_lines) > bound:
                raw_lines = raw_lines[-bound:]
        path.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")
        shed_raw_labels(root or data_root())
        return {
            "ok": True,
            "forwarded": False,
            "queued": len(raw_lines),
            "updated": updated,
            "row": payload,
        }


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
