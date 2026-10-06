"""pi3 memory tool. The file itself lives in pair.memory."""

from __future__ import annotations


def apply(payload: dict) -> dict:
    """list, put, delete, or clear. No model. Scoped when the caller names one."""
    from pair import memory

    raw = payload.get("scope")
    scope = None if raw is None else str(raw)
    op = str(payload.get("op") or "list")
    if op == "put":
        text = " ".join(str(payload.get("text") or "").split())
        if text:
            memory.remember_user([text], scope=scope)
    elif op == "delete":
        memory.delete_fact(str(payload.get("id") or ""), scope=scope)
    elif op == "clear":
        memory.clear_facts(scope)
    return {"ok": True, "facts": memory.list_facts(scope), "model": False}
