"""Cross-chat facts taken only from explicit user statements."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from pair.queue import data_root
from pair.turn import estimate_tokens

FACT_TOKEN_CAP = 150
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
    data.setdefault("compactions", 0)
    data.setdefault("last_compact_ms", 0)
    return data


def _write(data: dict) -> None:
    path = _path()
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def list_facts() -> list[dict]:
    with _LOCK:
        rows = _read().get("facts") or []
    return [dict(row) for row in rows if isinstance(row, dict)]


def facts_block() -> str:
    """At most 150 tokens, placed after the persona."""
    lines = []
    used = 0
    for row in list_facts():
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        cost = estimate_tokens(text)
        if used + cost > FACT_TOKEN_CAP:
            break
        lines.append(text)
        used += cost
    return "\n".join(lines)


def summary_text() -> str:
    with _LOCK:
        return str(_read().get("summary") or "")


def stats() -> dict:
    with _LOCK:
        data = _read()
    from pair.context import ledger_for
    from pair.knobs import inference_knobs

    ctx = int(inference_knobs().get("num_ctx") or 2048)
    book = ledger_for("default", ctx)
    return {
        "used": book.used(),
        "num_ctx": book.num_ctx,
        "compactions": int(data.get("compactions") or 0),
        "last_compact_ms": int(data.get("last_compact_ms") or 0),
    }


def remember_user(statements: list[str]) -> list[dict]:
    """Store 'user said X' lines. Nothing is inferred from topics."""
    kept = []
    with _LOCK:
        data = _read()
        rows = [row for row in data.get("facts") or [] if isinstance(row, dict)]
        seen = {str(row.get("text") or "") for row in rows}
        for raw in statements:
            text = " ".join(str(raw or "").split())
            if not text:
                continue
            line = text if text.lower().startswith("user said") else f"user said {text}"
            if line in seen:
                continue
            if estimate_tokens("\n".join([*seen, line])) > FACT_TOKEN_CAP:
                break
            item = {"id": uuid.uuid4().hex[:12], "text": line}
            rows.append(item)
            seen.add(line)
            kept.append(item)
        data["facts"] = rows
        _write(data)
    return kept


def delete_fact(fact_id: str) -> bool:
    with _LOCK:
        data = _read()
        rows = [row for row in data.get("facts") or [] if isinstance(row, dict)]
        kept = [row for row in rows if str(row.get("id") or "") != fact_id]
        if len(kept) == len(rows):
            return False
        data["facts"] = kept
        _write(data)
    return True


def clear_facts() -> None:
    with _LOCK:
        data = _read()
        data["facts"] = []
        _write(data)


def save_summary(text: str, elapsed_ms: int) -> None:
    with _LOCK:
        data = _read()
        data["summary"] = str(text or "").strip()
        data["compactions"] = int(data.get("compactions") or 0) + 1
        data["last_compact_ms"] = int(elapsed_ms)
        _write(data)
