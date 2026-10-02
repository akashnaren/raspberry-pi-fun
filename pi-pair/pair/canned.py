"""Serve path: compact input → answer map. No model call."""
from __future__ import annotations

import json
import os
from pathlib import Path

from pair.config import data_root


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


def lookup(text: str, path: Path | None = None) -> str | None:
    key = normalize_key(text)
    if not key:
        return None
    table = load_map(path)
    if key in table:
        return table[key]
    folded = {normalize_key(item): answer for item, answer in table.items()}
    return folded.get(key)


def write_map(table: dict[str, str], path: Path | None = None) -> None:
    target = path or map_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, target)
