"""Serve path: compact input → answer map.

Exact normalized keys hit on every board. On pi4, a miss can still hit when
the line is close to a key in embedding space. The brain preloads those key
vectors on a daemon thread after the socket is listening, so health and chat
accept during the preload. A miss in that window stays on the exact map until
the key cache is filled, then embeds the line only. Generation stays outside
this module.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from pair.config import data_root
from pair.embed import (
    on_pi4,
    reset_warm_state,
    semantic_lookup,
    set_warm_status,
    warm_canned_embeddings,
    warm_status,
)

__all__ = [
    "load_map",
    "lookup",
    "map_path",
    "normalize_key",
    "reset_warm_state",
    "start_canned_warm",
    "warm_at_start",
    "warm_status",
    "write_map",
]


def normalize_key(text: str) -> str:
    collapsed = " ".join((text or "").strip().lower().split())
    return collapsed.strip(" \t\r\n?!.,;:\"'")


def map_path() -> Path:
    override = os.environ.get("PI_PAIR_CANNED", "").strip()
    if override:
        return Path(override)
    return data_root() / "canned" / "canned_map.json"


def load_map(path: Path | None = None) -> dict[str, str]:
    target = path or map_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and key and value:
            out[key] = value
    return out


def _semantic_table(table: dict[str, str]) -> dict[str, str]:
    """Normalized keys the paraphrase match scores. Empty keys drop out."""
    folded = {normalize_key(item): answer for item, answer in table.items()}
    return {item: answer for item, answer in folded.items() if item}


def warm_at_start(path: Path | None = None) -> None:
    """Preload canned-key embeddings. Safe to run off the accept path.

    No-op off the brain. A failure here does not stop the process, and the
    next paraphrase still fills the cache lazily. The server runs this on a
    daemon thread after listen and does not wait for the batch.
    """
    if not on_pi4():
        return
    try:
        warm_canned_embeddings(_semantic_table(load_map(path)))
    except Exception as exc:
        print(f"canned embed warm skipped: {exc}", flush=True)


def start_canned_warm(path: Path | None = None) -> threading.Thread:
    """Start the key preload without blocking the caller.

    Call this after the listening socket exists. On the brain, status is
    `warming` before the thread is scheduled, then `ready` when the batch
    returns or fails. Other roles stay `ready` and the thread is a no-op.
    """
    brain = on_pi4()
    set_warm_status("warming" if brain else "ready")

    def run() -> None:
        try:
            warm_at_start(path)
        finally:
            set_warm_status("ready")

    thread = threading.Thread(target=run, name="canned-embed-warm", daemon=True)
    thread.start()
    return thread


def lookup(text: str, path: Path | None = None) -> str | None:
    key = normalize_key(text)
    if not key:
        return None
    table = load_map(path)
    if key in table:
        return table[key]
    folded = _semantic_table(table)
    exact = folded.get(key)
    if exact is not None:
        return exact
    # Semantic match is the pi4 brain only. A failure stays a miss.
    if not on_pi4():
        return None
    try:
        return semantic_lookup(key, folded)
    except Exception:
        return None


def write_map(table: dict[str, str], path: Path | None = None) -> None:
    target = path or map_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, target)
