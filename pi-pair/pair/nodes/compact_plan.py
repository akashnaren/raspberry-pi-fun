"""Extractive compaction plan. No model call.

Folded user sentences that name a person, a number, a code, or a decision
stay verbatim. Later wording replaces an earlier line that changed a number.
"""

from __future__ import annotations

import re

from pair.turn import estimate_tokens

_NUMBER = re.compile(r"\b\d{3,}(?:[.,]\d+)?\b")
_CODE = re.compile(r"\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9-]{3,}\b")
_NAME = re.compile(r"\b[A-Z][a-z]{2,}\b")
_DECISION = re.compile(r"\b(?:decided|decision|agreed|choose|chose)\b", re.I)
_QUOTE = re.compile(r'"([^"]{1,160})"')
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


def concrete_tokens(text: str) -> set[str]:
    """Numbers, codes, names, and quotes. Stopwords are not names."""
    found = {item.lower() for item in _NUMBER.findall(text or "")}
    found.update(item.lower() for item in _CODE.findall(text or ""))
    found.update(
        item.lower() for item in _NAME.findall(text or "") if item.lower() not in _STOP
    )
    found.update(
        item.strip().lower() for item in _QUOTE.findall(text or "") if item.strip()
    )
    return found


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", (text or "").strip())
    return [" ".join(part.split()) for part in parts if part.strip()]


def _concrete(text: str) -> bool:
    return bool(concrete_tokens(text) or _DECISION.search(text or ""))


def _words(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def _supersedes(new: str, old: str) -> bool:
    new_nums = set(_NUMBER.findall(new))
    old_nums = set(_NUMBER.findall(old))
    if not new_nums or not old_nums or new_nums == old_nums:
        return False
    return len(_words(new) & _words(old)) >= 2


def _dedupe(lines: list[str]) -> list[str]:
    """Keep the latest copy, and drop an older line whose number changed."""
    kept: list[str] = []
    for line in lines:
        kept = [old for old in kept if old != line and not _supersedes(line, old)]
        kept.append(line)
    return kept


def preserve_draft(draft: str, written: str) -> str:
    """Keep every concrete token from the draft when a rewrite drops it."""
    base = (draft or "").strip()
    short = " ".join((written or "").split())
    if not short:
        return base
    if not base:
        return short
    have = concrete_tokens(short)
    extra = []
    for line in base.splitlines():
        need = concrete_tokens(line)
        if need and not need <= have:
            extra.append(line.strip())
            have |= need
    if not extra:
        return short
    return short + "\n" + "\n".join(extra)


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
    facts: list[str] = []
    latest = ""
    for row in folded:
        if str(row.get("role") or "") != "user":
            continue
        text = " ".join(str(row.get("content") or "").split())
        if not text:
            continue
        latest = text
        for sentence in _sentences(text):
            if _concrete(sentence):
                facts.append(sentence[:240])
    facts = _dedupe(facts)
    if not facts and latest:
        facts = [latest[:240]]
    return {
        "keep": keep,
        "fold": folded,
        "facts": facts,
        "draft": "\n".join(facts).strip(),
        "model": False,
    }
