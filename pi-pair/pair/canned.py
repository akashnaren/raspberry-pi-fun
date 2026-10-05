"""Serve path: compact input → answer map.

A hit is an exact key or the same line after whitespace and punctuation are
normalized. A paraphrase is a miss on every board. Generation stays outside
this module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pair.config import data_root

__all__ = [
    "load_map",
    "lookup",
    "map_path",
    "normalize_key",
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


_MAPS: dict = {}
_MAP_GEN = 0


def load_map(path: Path | None = None) -> dict[str, str]:
    """The canned map. A repeat read with the same mtime skips the disk."""
    global _MAP_GEN
    target = path or map_path()
    try:
        stat = target.stat()
        key = (str(target), stat.st_mtime_ns, stat.st_size, _MAP_GEN)
    except OSError:
        key = (str(target), None, None, _MAP_GEN)
    cached = _MAPS.get(key)
    if isinstance(cached, dict):
        return dict(cached)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        raw = None
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for item, value in raw.items():
            if isinstance(item, str) and isinstance(value, str) and item and value:
                out[item] = value
    _MAPS.clear()
    _MAPS[key] = out
    return dict(out)


def _folded_table(table: dict[str, str]) -> dict[str, str]:
    """Normalized keys. The first key wins a fold."""
    folded: dict[str, str] = {}
    for item, answer in table.items():
        key = normalize_key(item)
        if key and key not in folded:
            folded[key] = answer
    return folded


def lookup(text: str, path: Path | None = None) -> str | None:
    key = normalize_key(text)
    if not key:
        return None
    table = load_map(path)
    if key in table:
        return table[key]
    return _folded_table(table).get(key)


def write_map(table: dict[str, str], path: Path | None = None) -> None:
    global _MAP_GEN
    _MAP_GEN += 1
    _MAPS.clear()
    target = path or map_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, target)
