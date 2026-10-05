"""Serve path: exact canned map, then Arctic embed near-match.

normalize_key still answers an exact line with no model call. A miss can
return a stored answer when snowflake-arctic-embed:m cosine is at least
0.85. Pull that model on pi4 with `ollama pull snowflake-arctic-embed:m`
(see pair/embed.py). Under the bar, the caller goes on to stock Qwen.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pair.config import data_root
from pair.embed import forget_vectors, semantic_lookup


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


def lookup(text: str, path: Path | None = None, *, embedder=None) -> str | None:
    """Exact normalized key, then an Arctic embed near-match.

    embedder, when set, replaces the Ollama /api/embed call. A failure to
    embed is a miss so the caller can still ask Qwen.
    """
    key = normalize_key(text)
    if not key:
        return None
    table = load_map(path)
    if key in table:
        return table[key]
    folded = {normalize_key(item): answer for item, answer in table.items()}
    exact = folded.get(key)
    if exact is not None:
        return exact
    try:
        return semantic_lookup(key, folded, map_file=path or map_path(), embedder=embedder)
    except Exception:
        return None


def write_map(table: dict[str, str], path: Path | None = None) -> None:
    target = path or map_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, target)
    forget_vectors(target)
