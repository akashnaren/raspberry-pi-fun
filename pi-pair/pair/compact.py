"""Fold old turns after a reply, only while the decode slot is idle.

pi3's part is extractive and uses no model. pi4 may rewrite that draft with
Flash when the slot stays free. An interruption keeps the draft.
"""

from __future__ import annotations

import threading
import time

from pair.context import verbatim_budget
from pair.turn import estimate_tokens

SUMMARY_CAP = 160
BUSY_RATIO = 0.50
IDLE_RATIO = 0.70


def should_compact(used: int, num_ctx: int, busy: bool) -> bool:
    if num_ctx <= 0:
        return False
    limit = BUSY_RATIO if busy else IDLE_RATIO
    return (used / num_ctx) >= limit


def _numbers_and_quotes(text: str) -> list[str]:
    import re

    found = re.findall(
        r'"([^"]{1,160})"|\'([^\']{1,160})\'|\b\d[\d,]*(?:\.\d+)?\b', text or ""
    )
    rows = []
    for groups in found:
        if isinstance(groups, str):
            piece = groups
        else:
            piece = next((part for part in groups if part), "")
        piece = piece.strip()
        if piece:
            rows.append(piece)
    return rows


def plan(turns: list[dict], num_ctx: int) -> dict:
    """Pick turns to fold. Recent turns that fit in 40% of the context stay verbatim."""
    budget = verbatim_budget(num_ctx)
    keep: list[dict] = []
    spent = 0
    for row in reversed(turns):
        if not isinstance(row, dict):
            continue
        cost = estimate_tokens(str(row.get("content") or ""))
        if keep and spent + cost > budget:
            break
        keep.append(row)
        spent += cost
    keep.reverse()
    folded = turns[: max(0, len(turns) - len(keep))]
    facts = []
    for row in folded:
        if str(row.get("role") or "") != "user":
            continue
        text = " ".join(str(row.get("content") or "").split())
        if text:
            facts.append(text)
    draft_bits = []
    for row in folded:
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        bits = _numbers_and_quotes(content)
        if bits:
            draft_bits.append(" ".join(bits))
        elif str(row.get("role") or "") == "user":
            draft_bits.append(content[:240])
    draft = "\n".join(draft_bits).strip()
    return {"keep": keep, "fold": folded, "facts": facts, "draft": draft}


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


def schedule(turns: list[dict], num_ctx: int, idle, generate=None) -> bool:
    """Start compaction off the request. False when a decode is already running."""
    if not idle():
        return False

    def work() -> None:
        from pair import memory

        result = run_compact(turns, num_ctx, idle=idle, generate=generate)
        if not result.get("ok"):
            return
        memory.remember_user(result.get("facts") or [])
        memory.save_summary(
            result.get("summary") or "", int(result.get("elapsed_ms") or 0)
        )

    threading.Thread(target=work, name="compact", daemon=True).start()
    return True
