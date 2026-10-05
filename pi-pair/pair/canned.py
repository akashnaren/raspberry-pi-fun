"""Serve path: compact input → answer map.

Exact normalized keys hit on every board. On pi4, a miss can still hit when
the line is close to a key in embedding space. The brain preloads those key
vectors when the server starts. Generation stays outside this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pair.config import data_root
from pair.embed import on_pi4, semantic_lookup, warm_canned_embeddings


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
    """Preload canned-key embeddings before the server accepts chats.

    No-op off the brain. A failure here does not stop the process, and the
    next paraphrase still fills the cache lazily.
    """
    if not on_pi4():
        return
    try:
        warm_canned_embeddings(_semantic_table(load_map(path)))
    except Exception as exc:
        print(f"canned embed warm skipped: {exc}", flush=True)


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
