"""pi3 memory tool. The file itself lives in pair.memory."""

from __future__ import annotations


def apply(payload: dict) -> dict:
    """list, put, delete, or clear. No model."""
    from pair import memory

    op = str(payload.get("op") or "list")
    if op == "put":
        text = " ".join(str(payload.get("text") or "").split())
        if text:
            memory.remember_user([text])
    elif op == "delete":
        memory.delete_fact(str(payload.get("id") or ""))
    elif op == "clear":
        memory.clear_facts()
    return {"ok": True, "facts": memory.list_facts(), "model": False}
