from __future__ import annotations
import json
import time
from pair.core import runtime
from pair.routes.base import safe_write

_COMPACT_GAP_S = 10.0
_COMPACT_BODY_CAP = 64 * 1024
_COMPACT_LAST: dict[str, float] = {}


def _memory_prompt(scope: str) -> tuple[str, str]:
    if not scope:
        return "", ""
    try:
        from pair.memory import store as memory

        return memory.facts_block(scope), memory.summary_text(scope)
    except Exception:
        return "", ""


_tuned_knobs = None


class MemoryRoutes:
    def _touch_memory(self, messages) -> None:
        """Record the real prompt size and enqueue a summary for when the slot is free."""
        from pair.memory.compact import schedule, should_compact
        from pair.memory.ledger import ledger_for

        ctx = 2048
        try:
            ctx = int((_tuned_knobs("") or {}).get("num_ctx") or 2048)
        except Exception:
            ctx = 2048
        scope = self._memory_scope()
        book = ledger_for(scope or "anon", ctx)
        count = int((getattr(self, "_usage", {}) or {}).get("prompt_eval_count") or 0)
        if count:
            book.observe(count)
        else:
            text = "\n".join(
                str(row.get("content") or "")
                for row in messages or []
                if isinstance(row, dict)
            )
            book.note_estimate(text)
        if not scope or not should_compact(
            book.used(), book.num_ctx, runtime.gate.waiting() > 0
        ):
            return
        schedule(
            [row for row in messages or [] if isinstance(row, dict)],
            book.num_ctx,
            idle=lambda: True,
            scope=scope,
        )

    def _memory_get(self) -> None:
        from pair.memory import store as memory
        from pair.memory.compact import BUSY_RATIO, IDLE_RATIO

        scope = self._memory_scope()
        if scope:
            usage = memory.stats(scope)
        else:
            usage = {
                "used": 0,
                "num_ctx": self._memory_num_ctx(),
                "compactions": 0,
                "last_compact_ms": 0,
            }
        self._write_json(
            {
                "facts": memory.list_facts(scope) if scope else [],
                "summary": memory.summary_text(scope) if scope else "",
                "usage": usage,
                "compact_at": IDLE_RATIO,
                "compact_busy_at": BUSY_RATIO,
            }
        )

    def _memory_num_ctx(self) -> int:
        """Same context knob the idle compactor uses."""
        try:
            return int((_tuned_knobs("") or {}).get("num_ctx") or 2048)
        except Exception:
            return 2048

    def _memory_compact(self) -> None:
        """Queue one compact in the existing idle slot. Never preempts a decode."""
        scope = self._memory_scope()
        data = self._read_json(cap=_COMPACT_BODY_CAP, label="compact body")
        if data is None:
            return
        if not scope:
            self._write_json({"ok": False, "reason": "no_session"}, status=400)
            return
        rows: list[dict] = []
        messages = data.get("messages")
        if isinstance(messages, list):
            for row in messages:
                if len(rows) >= 64:
                    break
                if not isinstance(row, dict):
                    continue
                role = row.get("role")
                content = row.get("content")
                if role not in {"user", "assistant"} or not isinstance(content, str):
                    continue
                rows.append({"role": role, "content": content})
        if runtime.gate.in_flight() or runtime.gate.waiting():
            self._write_json({"ok": False, "reason": "busy"}, status=409)
            return
        now = time.monotonic()
        previous = _COMPACT_LAST.get(scope, 0.0)
        if now - previous < _COMPACT_GAP_S:
            self._write_json({"ok": False, "reason": "rate"}, status=429)
            return
        _COMPACT_LAST[scope] = now
        from pair.memory.compact import schedule

        queued = schedule(
            rows,
            self._memory_num_ctx(),
            idle=lambda: not runtime.gate.in_flight() and not runtime.gate.waiting(),
            scope=scope,
        )
        self._write_json({"ok": True, "queued": bool(queued)})

    def _memory_delete(self, fact_id: str) -> None:
        from pair.memory import store as memory

        scope = self._memory_scope()
        if scope and fact_id:
            memory.delete_fact(fact_id, scope=scope)
        elif scope:
            memory.clear_facts(scope)
        body = json.dumps(
            {"ok": True, "facts": memory.list_facts(scope) if scope else []}
        ).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _memory_scope(self) -> str:
        """Chat plus client. Missing both reads and writes nothing."""
        from pair.memory.store import scope_key

        headers = getattr(self, "headers", None)
        chat = client = ""
        if headers is not None:
            chat = str(headers.get("X-Pi-Chat") or "")
            client = str(headers.get("X-Pi-Client") or "")
        return scope_key(chat, client)
