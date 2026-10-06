"""Fold old turns after a reply, only while the decode slot is idle.

pi3's part is extractive and uses no model. pi4 may rewrite that draft with
Flash when the slot stays free. An interruption keeps the draft.
"""

from __future__ import annotations

import json
import threading
import time

from pair.turn import estimate_tokens

SUMMARY_CAP = 160
BUSY_RATIO = 0.50
IDLE_RATIO = 0.70


def should_compact(used: int, num_ctx: int, busy: bool) -> bool:
    if num_ctx <= 0:
        return False
    limit = BUSY_RATIO if busy else IDLE_RATIO
    return (used / num_ctx) >= limit


def plan(turns: list[dict], num_ctx: int) -> dict:
    """Pick turns to fold. pi3's compact_plan tool is the same planner."""
    from pair.nodes.compact_plan import plan_turns

    planned = plan_turns(turns, num_ctx)
    return {
        "keep": planned["keep"],
        "fold": planned["fold"],
        "facts": planned["facts"],
        "draft": planned["draft"],
    }


def _clip_summary(text: str) -> str:
    raw = " ".join((text or "").split())
    if estimate_tokens(raw) <= SUMMARY_CAP:
        return raw
    room = int(SUMMARY_CAP * 3.2)
    return raw[:room].rstrip()


def run_compact(
    turns: list[dict],
    num_ctx: int,
    *,
    idle,
    generate=None,
    cancel=None,
) -> dict:
    """Fold, then optionally ask Flash. `idle()` false aborts the model call."""
    started = time.perf_counter()
    if not idle():
        return {"ok": False, "reason": "busy", "summary": "", "facts": []}
    planned = plan(turns, num_ctx)
    draft = planned["draft"]
    summary = draft
    if generate is not None and idle():
        try:
            written = generate(draft) or ""
        except Exception:
            written = ""
        if cancel is not None and getattr(cancel, "gone", lambda: False)():
            written = ""
        if not idle():
            written = ""
        summary = _clip_summary(written or draft)
    else:
        summary = _clip_summary(draft)
    elapsed = int((time.perf_counter() - started) * 1000)
    return {
        "ok": True,
        "summary": summary,
        "facts": planned["facts"],
        "keep": planned["keep"],
        "elapsed_ms": elapsed,
    }


def _flash_summary(cancel):
    """Rewrite the draft with Flash. An empty string keeps the draft."""

    def write(draft: str) -> str:
        if cancel.is_set() or not str(draft or "").strip():
            return ""
        from pair import runtime
        from pair.guard import may_generate
        from pair.modes import FLASH, mode_table

        peer = next((row for row in runtime.PEERS if may_generate(row)), None)
        model = str(mode_table().get(FLASH) or "")
        if peer is None or not model:
            return ""
        from pair.cancel import Cancel, ClientGone
        from pair.chat import open_json, ollama_payload

        flag = Cancel()
        stop = threading.Event()

        def watch() -> None:
            while not stop.is_set():
                if cancel.wait(0.05):
                    flag.set()
                    return

        threading.Thread(target=watch, name="summary-cancel", daemon=True).start()
        url = f"http://{peer['host']}:{peer['port']}/api/chat"
        payload = ollama_payload(
            model,
            [
                {"role": "system", "content": "Summarize the notes in fewer words."},
                {"role": "user", "content": draft},
            ],
            0.0,
            SUMMARY_CAP,
            False,
        )
        try:
            with open_json(url, payload, timeout=30, cancel=flag) as response:
                data = json.loads(response.read().decode())
        except ClientGone:
            return ""
        except Exception:
            return ""
        finally:
            stop.set()
        if cancel.is_set():
            return ""
        return str((data.get("message") or {}).get("content") or "")

    return write


def schedule(turns: list[dict], num_ctx: int, idle, generate=None) -> bool:
    """Enqueue a summary. False when the caller says a decode is already running.

    The scheduler waits until the slot is free, holds it for this job, and
    cancels the job when an interactive request arrives. The extractive draft
    is what gets stored if Flash is interrupted or absent.
    """
    if not idle():
        return False
    from pair import runtime

    def job(cancel) -> None:
        from pair import memory

        class _Gone:
            def gone(self) -> bool:
                return bool(cancel.is_set())

        def still() -> bool:
            return bool(idle()) and not cancel.is_set()

        writer = generate if generate is not None else _flash_summary(cancel)
        result = run_compact(
            turns,
            num_ctx,
            idle=still,
            generate=writer,
            cancel=_Gone(),
        )
        if not result.get("ok"):
            return
        memory.remember_user(result.get("facts") or [])
        memory.save_summary(
            result.get("summary") or "", int(result.get("elapsed_ms") or 0)
        )

    return bool(runtime.gate.enqueue_background(job))
