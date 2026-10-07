"""pi3 cycle: prepare queue, fold canned map, gate, promote, delete shards."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from pair.flywheel.canned import load_map, normalize_key, write_map
from pair.core.config import ROOT, data_root
from pair.flywheel.publish import record_public_labels, sync_public_labels
from pair.flywheel.miss_queue import node_role
from pair.flywheel.registry import RegistryError, require_registered
from pair.core.yaml_lite import load_path

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


_EMBED_URL = "http://127.0.0.1:18080/tools/embed"
_EMBED_BATCH = 16
_PARAPHRASE_MIN = 0.92


def _paraphrase_min() -> float:
    path = ROOT / "configs" / "runtime" / "embed_pi3.json"
    try:
        value = float(
            json.loads(path.read_text(encoding="utf-8")).get("paraphrase_min")
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return _PARAPHRASE_MIN
    if not math.isfinite(value):
        return _PARAPHRASE_MIN
    return value


def _cosine(left: list[float], right: list[float]) -> float:
    from pair.nodes.embedder import cosine

    return cosine(left, right)


def _vector_id(key: str, model: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest() + model


def _load_vector_cache(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _cached_model(cache: dict) -> str:
    found: set[str] = set()
    for key in cache:
        if not isinstance(key, str) or len(key) <= 64:
            continue
        digest, model = key[:64], key[64:]
        if (
            len(digest) == 64
            and all(ch in "0123456789abcdef" for ch in digest)
            and model
        ):
            found.add(model)
    if len(found) == 1:
        return next(iter(found))
    return ""


def _cached_vector(cache: dict, key: str, model: str) -> list[float] | None:
    row = cache.get(_vector_id(key, model))
    if not isinstance(row, list) or not row:
        return None
    numbers: list[float] = []
    for value in row:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        numbers.append(number)
    return numbers


def _embed_remote(texts: list[str]) -> tuple[str, list[list[float]]] | None:
    """Ask this board's /tools/embed. None means the fold stays exact-only."""
    if not texts:
        return "", []
    request = urllib.request.Request(
        _EMBED_URL,
        data=json.dumps({"texts": texts}).encode(),
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3.5) as response:
            payload = json.loads(response.read().decode() or "{}")
    except (
        OSError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        ValueError,
    ):
        return None
    if not isinstance(payload, dict) or payload.get("skipped"):
        return None
    rows = payload.get("vectors")
    model = str(payload.get("model") or "")
    if not model or not isinstance(rows, list) or len(rows) != len(texts):
        return None
    parsed: list[list[float]] = []
    width: int | None = None
    for row in rows:
        if not isinstance(row, list) or not row:
            return None
        numbers: list[float] = []
        for value in row:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            number = float(value)
            if not math.isfinite(number):
                return None
            numbers.append(number)
        if width is None:
            width = len(numbers)
        elif len(numbers) != width:
            return None
        parsed.append(numbers)
    return model, parsed


def _embed_all(texts: list[str]) -> tuple[str, list[list[float]]] | None:
    model = ""
    parsed: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH):
        chunk = texts[start : start + _EMBED_BATCH]
        got = _embed_remote(chunk)
        if got is None:
            return None
        chunk_model, rows = got
        if model and chunk_model != model:
            return None
        model = chunk_model
        parsed.extend(rows)
    return model, parsed


def _write_vector_cache(
    path: Path, table: dict[str, str], vectors: dict[str, list[float]], model: str
) -> None:
    payload = {}
    for key in table:
        vector = vectors.get(key)
        if vector:
            payload[_vector_id(key, model)] = vector
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _lookup_vectors(
    table: dict[str, str], fresh: list[str], root: Path | None
) -> tuple[dict[str, list[float]], str] | None:
    path = None if root is None else Path(root) / "canned" / "key_vectors.json"
    cache = _load_vector_cache(path) if path is not None else {}
    model = _cached_model(cache)
    keys = list(dict.fromkeys([*table.keys(), *fresh]))
    vectors: dict[str, list[float]] = {}
    missing: list[str] = []
    if model:
        for key in keys:
            hit = _cached_vector(cache, key, model)
            if hit is None:
                missing.append(key)
            else:
                vectors[key] = hit
    else:
        missing = list(keys)
    if missing:
        got = _embed_all(missing)
        if got is None:
            return None
        remote_model, rows = got
        if model and remote_model != model:
            got = _embed_all(keys)
            if got is None:
                return None
            remote_model, rows = got
            vectors = dict(zip(keys, rows))
            model = remote_model
        else:
            model = remote_model
            for key, row in zip(missing, rows):
                vectors[key] = row
        if path is not None and model:
            _write_vector_cache(path, table, vectors, model)
    if not model:
        return None
    return vectors, model


def _best_paraphrase(
    key: str,
    table: dict[str, str],
    vectors: dict[str, list[float]],
    heldout: set[str],
) -> tuple[str | None, float]:
    left = vectors.get(key)
    if not left:
        return None, 0.0
    best_key = None
    best = -1.0
    for existing in table:
        if existing == key or existing in heldout:
            continue
        right = vectors.get(existing)
        if not right:
            continue
        score = _cosine(left, right)
        if score > best:
            best = score
            best_key = existing
    return best_key, best


def _fold(
    table: dict[str, str],
    rows: list[dict],
    heldout: set[str],
    root: Path | None = None,
) -> tuple[dict[str, str], int, int, int]:
    added = 0
    rejected = 0
    merged = 0
    fresh: list[tuple[str, str, bool]] = []
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
        fresh.append((key, answer, bool(correction)))
    if not fresh:
        return table, added, rejected, merged
    looked = _lookup_vectors(table, [key for key, _answer, _correction in fresh], root)
    if looked is None:
        for key, answer, _correction in fresh:
            table[key] = answer
            added += 1
        return table, added, rejected, merged
    vectors, model = looked
    threshold = _paraphrase_min()
    for key, answer, correction in fresh:
        best_key, best = _best_paraphrase(key, table, vectors, heldout)
        if best_key is not None and best >= threshold:
            merged += 1
            if correction and table[best_key] != answer:
                table[best_key] = answer
                added += 1
            continue
        table[key] = answer
        added += 1
    if root is not None and model:
        _write_vector_cache(
            Path(root) / "canned" / "key_vectors.json", table, vectors, model
        )
    return table, added, rejected, merged


def _gate(table: dict[str, str], heldout: set[str], before: dict[str, str]) -> None:
    leaked = sorted(
        key for key in heldout if key in table or normalize_key(key) in table
    )
    if leaked:
        raise GateError("eval gate failed: held-out input present in canned map")
    missing = [key for key in before if key not in table]
    if missing:
        raise GateError("eval gate failed: existing canned keys dropped")


def _tombstone(
    root: Path,
    run_id: str,
    added: int,
    rejected: int,
    digest: str,
    rows_after: int,
    labeled: int = 0,
    paraphrase_merged: int = 0,
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
        "paraphrase_merged": paraphrase_merged,
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
                "paraphrase_merged": 0,
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
        candidate, added, rejected, merged = _fold(candidate, rows, heldout, root=base)
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
            "base_model": cfg.get("base_model") or "qwen3:0.6b",
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
        _tombstone(
            base, run_id, added, rejected, digest, len(candidate), labeled, merged
        )
        return {
            "id": run_id,
            "added": added,
            "rejected": rejected,
            "paraphrase_merged": merged,
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
