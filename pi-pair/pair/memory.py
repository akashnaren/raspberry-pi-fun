"""Facts and compaction summaries, one file per chat session.

A caller with no chat or client id reads and writes nothing. Two sessions
never share a summary, and clearing one does not touch the other.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from pair.queue import data_root
from pair.turn import estimate_tokens

FACT_TOKEN_CAP = 150
MEMORY_TTL_S = 30 * 24 * 60 * 60
_LOCK = threading.Lock()
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_NUMBER = re.compile(r"\b\d{3,}(?:[.,]\d+)?\b")
_CODE = re.compile(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]{3,}\b")
_NAME = re.compile(r"\b[A-Z][a-z]{2,}\b")
_WORD = re.compile(r"[a-z]{4,}")
_STOP = {
    "the",
    "this",
    "that",
    "there",
    "then",
    "they",
    "them",
    "with",
    "from",
    "have",
    "were",
    "been",
    "your",
    "what",
    "when",
    "where",
    "which",
    "their",
    "about",
    "user",
    "said",
}


def scope_key(chat: str, client: str) -> str:
    """A filename-safe id. Empty when the caller did not name a session."""
    chat_id = _safe(chat)
    client_id = _safe(client)
    if client_id and chat_id:
        return f"{client_id}--{chat_id}"[:120]
    return (client_id or chat_id)[:120]


def _safe(value: str) -> str:
    return _SAFE.sub("", (value or "").strip())[:80]


def _bucket(scope: str | None) -> str:
    """None is the local tool bucket. An empty string stores nothing."""
    if scope is None:
        return "default"
    return _safe(scope)[:120]


def _path(scope: str) -> Path:
    folder = data_root() / "memory"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{scope}.json"


def _empty() -> dict:
    return {"facts": [], "summary": "", "compactions": 0, "last_compact_ms": 0}


def purge_memory(max_age: float = MEMORY_TTL_S, now: float | None = None) -> int:
    """Drop idle chat files and the old global facts.json. Active chats stay."""
    folder = data_root() / "memory"
    if not folder.is_dir():
        return 0
    clock = time.time() if now is None else float(now)
    removed = 0
    legacy = folder / "facts.json"
    if legacy.is_file():
        legacy.unlink()
        removed += 1
    for path in list(folder.glob("*.json")):
        try:
            stale = path.stat().st_mtime <= clock - float(max_age)
        except OSError:
            continue
        if stale:
            path.unlink()
            removed += 1
    return removed


def _read(scope: str) -> dict:
    path = _path(scope)
    if not path.exists():
        return _empty()
    try:
        os.utime(path, None)
    except OSError:
        pass
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


def _write(scope: str, data: dict) -> None:
    _path(scope).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def list_facts(scope: str | None = None) -> list[dict]:
    key = _bucket(scope)
    if not key:
        return []
    purge_memory()
    with _LOCK:
        rows = _read(key).get("facts") or []
    return [dict(row) for row in rows if isinstance(row, dict)]


def facts_block(scope: str | None = None) -> str:
    """At most 150 tokens. The latest facts are kept, then shown in order."""
    chosen: list[str] = []
    used = 0
    for row in reversed(list_facts(scope)):
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        cost = estimate_tokens(text)
        if used + cost > FACT_TOKEN_CAP:
            continue
        chosen.append(text)
        used += cost
    chosen.reverse()
    return "\n".join(chosen)


def summary_text(scope: str | None = None) -> str:
    key = _bucket(scope)
    if not key:
        return ""
    with _LOCK:
        return str(_read(key).get("summary") or "")


def stats(scope: str | None = None) -> dict:
    key = _bucket(scope)
    data = _empty()
    if key:
        with _LOCK:
            data = _read(key)
    from pair.context import ledger_for
    from pair.knobs import inference_knobs

    ctx = int(inference_knobs().get("num_ctx") or 2048)
    book = ledger_for(key or "default", ctx)
    return {
        "used": book.used(),
        "num_ctx": book.num_ctx,
        "compactions": int(data.get("compactions") or 0),
        "last_compact_ms": int(data.get("last_compact_ms") or 0),
    }


def _numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(text or ""))


def _words(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def _priority(text: str) -> int:
    score = 1
    if _numbers(text):
        score += 4
    if _CODE.search(text or ""):
        score += 4
    names = {item.lower() for item in _NAME.findall(text or "")} - _STOP
    if names:
        score += 2
    return score


def _supersedes(new: str, old: str) -> bool:
    """A later statement that changes a number and shares its wording."""
    new_nums = _numbers(new)
    old_nums = _numbers(old)
    if not new_nums or not old_nums or new_nums == old_nums:
        return False
    return len(_words(new) & _words(old)) >= 2


def _trim(rows: list[dict]) -> None:
    def weight() -> int:
        return estimate_tokens("\n".join(str(row.get("text") or "") for row in rows))

    while rows and weight() > FACT_TOKEN_CAP:
        index = min(range(len(rows)), key=lambda i: (_priority(rows[i]["text"]), i))
        rows.pop(index)


def _as_user_fact(text: str) -> str:
    """Memory is quoted as the user, so the model does not answer as them."""
    flat = " ".join(str(text or "").split())
    lower = flat.lower()
    if lower.startswith("the user said:"):
        return flat
    if lower.startswith("user said"):
        parts = flat.split(None, 2)
        body = parts[2] if len(parts) > 2 else ""
        return f"The user said: {body}".strip()
    return f"The user said: {flat}"


def remember_user(statements: list[str], scope: str | None = None) -> list[dict]:
    """Store 'The user said: …' lines. The latest wording wins. Nothing is inferred."""
    key = _bucket(scope)
    if not key:
        return []
    purge_memory()
    kept = []
    with _LOCK:
        data = _read(key)
        rows = [row for row in data.get("facts") or [] if isinstance(row, dict)]
        seen = {str(row.get("text") or "") for row in rows}
        for raw in statements:
            text = " ".join(str(raw or "").split())
            if not text:
                continue
            line = _as_user_fact(text)
            rows = [
                row for row in rows if not _supersedes(line, str(row.get("text") or ""))
            ]
            seen = {str(row.get("text") or "") for row in rows}
            if line in seen:
                rows = [row for row in rows if str(row.get("text") or "") != line]
            item = {"id": uuid.uuid4().hex[:12], "text": line}
            rows.append(item)
            seen.add(line)
            kept.append(item)
        _trim(rows)
        data["facts"] = rows
        _write(key, data)
    kept_ids = {row["id"] for row in rows}
    return [item for item in kept if item["id"] in kept_ids]


def delete_fact(fact_id: str, scope: str | None = None) -> bool:
    key = _bucket(scope)
    if not key:
        return False
    with _LOCK:
        data = _read(key)
        rows = [row for row in data.get("facts") or [] if isinstance(row, dict)]
        kept = [row for row in rows if str(row.get("id") or "") != fact_id]
        if len(kept) == len(rows):
            return False
        data["facts"] = kept
        _write(key, data)
    return True


def clear_facts(scope: str | None = None) -> None:
    key = _bucket(scope)
    if not key:
        return
    with _LOCK:
        data = _read(key)
        data["facts"] = []
        data["summary"] = ""
        _write(key, data)


def save_summary(text: str, elapsed_ms: int, scope: str | None = None) -> None:
    key = _bucket(scope)
    if not key:
        return
    purge_memory()
    with _LOCK:
        data = _read(key)
        data["summary"] = str(text or "").strip()
        data["compactions"] = int(data.get("compactions") or 0) + 1
        data["last_compact_ms"] = int(elapsed_ms)
        _write(key, data)
