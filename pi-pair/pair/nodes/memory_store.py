"""Fact file for the pi3 memory tool. pi4 keeps its own copy in pair.memory later."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from pair.queue import data_root

_LOCK = threading.Lock()


def _path() -> Path:
    folder = data_root() / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "facts.json"


def _read() -> dict:
    path = _path()
    if not path.exists():
        return {"facts": [], "summary": "", "compactions": 0, "last_compact_ms": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("facts", [])
    data.setdefault("summary", "")
    return data


def _write(data: dict) -> None:
    _path().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def apply(payload: dict) -> dict:
    """list, put, delete, or clear. No model."""
    op = str(payload.get("op") or "list")
    with _LOCK:
        data = _read()
        rows = [row for row in data.get("facts") or [] if isinstance(row, dict)]
        if op == "put":
            text = " ".join(str(payload.get("text") or "").split())
            if text and not text.lower().startswith("user said"):
                text = f"user said {text}"
            if text and text not in {str(row.get("text") or "") for row in rows}:
                rows.append({"id": uuid.uuid4().hex[:12], "text": text})
            data["facts"] = rows
            _write(data)
        elif op == "delete":
            fact_id = str(payload.get("id") or "")
            data["facts"] = [row for row in rows if str(row.get("id") or "") != fact_id]
            _write(data)
        elif op == "clear":
            data["facts"] = []
            _write(data)
        else:
            data["facts"] = rows
    return {"ok": True, "facts": data.get("facts") or [], "model": False}
