"""Extractive compaction plan. No model call."""

from __future__ import annotations

from pair.turn import estimate_tokens


def plan_turns(turns: list[dict], num_ctx: int) -> dict:
    """Keep recent turns that fit in 40% of the context. Fold the rest."""
    budget = max(1, int(int(num_ctx) * 0.40))
    keep: list[dict] = []
    spent = 0
    rows = [row for row in turns if isinstance(row, dict)]
    for row in reversed(rows):
        cost = estimate_tokens(str(row.get("content") or ""))
        if keep and spent + cost > budget:
            break
        keep.append(row)
        spent += cost
    keep.reverse()
    folded = rows[: max(0, len(rows) - len(keep))]
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
        if str(row.get("role") or "") == "user":
            draft_bits.append(content[:240])
    return {
        "keep": keep,
        "fold": folded,
        "facts": facts,
        "draft": "\n".join(draft_bits).strip(),
        "model": False,
    }
