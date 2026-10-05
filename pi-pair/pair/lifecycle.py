"""pi3 cycle: prepare queue, fold canned map, gate, promote, delete shards."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from pair.canned import load_map, normalize_key, write_map
from pair.config import ROOT, data_root
from pair.publish import record_public_labels, sync_public_labels
from pair.queue import node_role
from pair.registry import RegistryError, require_registered
from pair.yaml_lite import load_path

REJECT_MARKERS = (
    "cannot be the brain",
    "unreachable on cache miss",
)


class GateError(RuntimeError):
    pass


def adapters_root() -> Path:
    raw = os.environ.get("PI_PAIR_ADAPTERS", "").strip()
    if raw:
        return Path(raw)
    return ROOT / "adapters"


def train_config_path() -> Path:
    raw = os.environ.get("PI_PAIR_TRAIN_CONFIG", "").strip()
    if raw:
        return Path(raw)
    return ROOT / "configs" / "train" / "sft_canned.yaml"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise RegistryError(f"{path} contains a non-object row")
        rows.append(item)
    return rows


def _heldout_keys(path: Path) -> set[str]:
    keys = set()
    for row in _read_jsonl(path):
        raw = row.get("input") or row.get("prompt") or ""
        key = normalize_key(str(raw))
        if key:
            keys.add(key)
    return keys


def _usable(prompt: str, answer: str) -> bool:
    if not prompt or not answer:
        return False
    if len(prompt) > 4000 or len(answer) > 8000:
        return False
    lowered = answer.lower()
    return not any(marker in lowered for marker in REJECT_MARKERS)


def _lock(root: Path):
    path = root / "train" / "post_train.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RuntimeError("train job already running") from error
    os.write(fd, str(os.getpid()).encode())
    return fd, path


def _unlock(fd: int, path: Path) -> None:
    try:
        os.close(fd)
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def _consume_queue(root: Path, run_id: str) -> Path | None:
    pending = root / "train" / "pending" / "queue.jsonl"
    if not pending.is_file() or pending.stat().st_size == 0:
        return None
    active = root / "train" / "active" / f"{run_id}.jsonl"
    active.parent.mkdir(parents=True, exist_ok=True)
    os.replace(pending, active)
    return active


def _restore_queue(root: Path, active: Path | None) -> None:
    if active is None or not active.is_file():
        return
    pending = root / "train" / "pending" / "queue.jsonl"
    pending.parent.mkdir(parents=True, exist_ok=True)
    if pending.exists():
        extra = active.read_text(encoding="utf-8")
        with pending.open("a", encoding="utf-8") as handle:
            if extra and not extra.endswith("\n"):
                extra += "\n"
            handle.write(extra)
        active.unlink()
        return
    os.replace(active, pending)


def _prepare(active: Path | None, root: Path, run_id: str) -> tuple[Path, list[dict]]:
    rows: list[dict] = []
    if active is not None:
        for row in _read_jsonl(active):
            prompt = str(row.get("prompt") or row.get("input") or row.get("q") or "")
            answer = str(row.get("answer") or row.get("a") or row.get("output") or "")
            prepared_row = {
                "prompt": prompt.strip(),
                "answer": answer.strip(),
                "q": normalize_key(prompt),
            }
            vote = str(row.get("vote") or "").strip().lower()
            correction = str(row.get("correction") or "").strip()
            if vote:
                prepared_row["vote"] = vote
            if correction:
                prepared_row["correction"] = correction
            rows.append(prepared_row)
    prepared = root / "prepared" / f"{run_id}.jsonl"
    prepared.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    prepared.write_text(body, encoding="utf-8")
    return prepared, rows


def _fold(table: dict[str, str], rows: list[dict], heldout: set[str]) -> tuple[dict[str, str], int, int]:
    added = 0
    rejected = 0
    for row in rows:
        key = row.get("q") or ""
        correction = str(row.get("correction") or "").strip()
        vote = str(row.get("vote") or "").strip().lower()
        if correction:
            answer = correction
        elif vote == "down":
            rejected += 1
            continue
        else:
            answer = row.get("answer") or ""
        if not _usable(key, answer) or key in heldout:
            rejected += 1
            continue
        if key in table:
            if correction and table[key] != answer:
                table[key] = answer
                added += 1
            continue
        table[key] = answer
        added += 1
    return table, added, rejected


def _gate(table: dict[str, str], heldout: set[str], before: dict[str, str]) -> None:
    leaked = sorted(key for key in heldout if key in table or normalize_key(key) in table)
    if leaked:
        raise GateError("eval gate failed: held-out input present in canned map")
    missing = [key for key in before if key not in table]
    if missing:
        raise GateError("eval gate failed: existing canned keys dropped")


def _tombstone(
    root: Path, run_id: str, added: int, rejected: int, digest: str, rows_after: int, labeled: int = 0
) -> Path:
    path = root / "train" / "done" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": run_id,
        "deleted": True,
        "added": added,
        "rejected": rejected,
        "rows_after": rows_after,
        "labeled": labeled,
        "map_sha256": digest,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _delete_consumed(active: Path | None, prepared: Path | None) -> list[str]:
    removed = []
    for path in (active, prepared):
        if path is not None and path.is_file():
            path.unlink()
            removed.append(path.name)
    return removed


def sweep_ephemeral(root: Path | None = None) -> list[str]:
    """Delete active shards and prepared artifacts. Pending queue is left intact."""
    base = root or data_root()
    removed = []
    for relative in ("train/active", "prepared"):
        folder = base / relative
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if not path.is_file() or path.name == ".gitkeep":
                continue
            if path.suffix not in {".jsonl", ".json", ".txt", ".bin", ".tmp"}:
                continue
            path.unlink()
            removed.append(path.name)
    return removed


def _public_sync(rows: list[dict], root: Path) -> dict:
    """Hash votes, then upload if HF_TOKEN is set. A hub failure does not keep the shard."""
    skipped = {"status": "skipped", "reason": "behind huggingface"}
    try:
        public_rows = record_public_labels(rows, root)
        return sync_public_labels(public_rows, root=root)
    except Exception:
        return {
            "huggingface": {"status": "error", "reason": "sync failed"},
            "kaggle": skipped,
        }


def post_train(
    root: Path | None = None,
    adapters: Path | None = None,
    config: Path | None = None,
) -> dict:
    if node_role() != "dataset":
        raise RuntimeError(
            "train-then-delete runs only on the dataset host (pi3). "
            "pi4 keeps weights. pi2 is health and search."
        )
    base = root or data_root()
    adapter_root = adapters or adapters_root()
    cfg_path = config or train_config_path()
    cfg = load_path(cfg_path)
    names = list(cfg.get("datasets") or [])
    eval_name = cfg.get("eval")
    if not eval_name:
        raise RegistryError("train config missing eval dataset")
    resolved = require_registered(names + [eval_name], base)
    for name in names + [eval_name]:
        _read_jsonl(resolved[name])
    heldout = _heldout_keys(resolved[eval_name])
    fd, lock_path = _lock(base)
    active = None
    prepared = None
    try:
        run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        active = _consume_queue(base, run_id)
        if active is None:
            table = load_map(base / "canned" / "canned_map.json")
            _gate(table, heldout, table)
            return {
                "id": run_id,
                "added": 0,
                "rejected": 0,
                "rows_before": len(table),
                "rows_after": len(table),
                "deleted": [],
                "promoted": False,
                "public_sync": _public_sync([], base),
            }
        prepared, rows = _prepare(active, base, run_id)
        labeled = sum(1 for row in rows if row.get("vote"))
        before = load_map(base / "canned" / "canned_map.json")
        candidate = dict(before)
        candidate, added, rejected = _fold(candidate, rows, heldout)
        try:
            _gate(candidate, heldout, before)
        except GateError:
            _restore_queue(base, active)
            active = None
            if prepared is not None and prepared.exists():
                prepared.unlink()
            prepared = None
            raise
        digest = _sha256(json.dumps(candidate, ensure_ascii=False, sort_keys=True))
        staging = adapter_root / "staging" / run_id
        staging.mkdir(parents=True, exist_ok=True)
        manifest = {
            "id": run_id,
            "stage": cfg.get("stage") or "sft",
            "base_model": cfg.get("base_model") or "qwen2.5:0.5b",
            "weights": "pi4-ollama",
            "canned_map": "data/canned/canned_map.json",
            "map_sha256": digest,
            "added": added,
            "rejected": rejected,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        write_map(candidate, base / "canned" / "canned_map.json")
        active_dir = adapter_root / "active"
        active_dir.mkdir(parents=True, exist_ok=True)
        os.replace(staging / "manifest.json", active_dir / "manifest.json")
        try:
            staging.rmdir()
        except OSError:
            pass
        parent = staging.parent
        try:
            parent.rmdir()
        except OSError:
            pass
        public_sync = _public_sync(rows, base)
        removed = _delete_consumed(active, prepared)
        active = None
        prepared = None
        _tombstone(base, run_id, added, rejected, digest, len(candidate), labeled)
        return {
            "id": run_id,
            "added": added,
            "rejected": rejected,
            "labeled": labeled,
            "rows_before": len(before),
            "rows_after": len(candidate),
            "deleted": removed,
            "promoted": True,
            "map_sha256": digest,
            "public_sync": public_sync,
        }
    finally:
        _unlock(fd, lock_path)
