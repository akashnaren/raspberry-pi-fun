"""Numbered asks get enough tokens and a continuation when the list stops early."""

from __future__ import annotations

import re

_LIST = re.compile(
    r"\b(?:top|list|name|give|rank|number)\b(?:\s+\w+){0,4}\s+(\d{1,2})\b|"
    r"\b(\d{1,2})\s+(?:movies|films|books|songs|items|things|reasons|ways|"
    r"examples|ideas|tips|points|steps|cars|places|products|shows)\b",
    re.I,
)
_LINE = re.compile(r"(?m)^\s*(\d{1,2})[\.\)]\s+(\S.*)$")

_CAP = 1024
_PER_ITEM = 40
_HEADROOM = 48


def list_count(prompt: str) -> int | None:
    """The N in 'top 10 movies', between 2 and 20. Other lines are None."""
    match = _LIST.search(prompt or "")
    if not match:
        return None
    raw = next(group for group in match.groups() if group)
    count = int(raw)
    if count < 2 or count > 20:
        return None
    return count


def list_budget(prompt: str, base: int) -> int:
    """Raise num_predict so N short items fit. Unlisted prompts keep `base`."""
    count = list_count(prompt)
    if not count:
        return int(base)
    need = _PER_ITEM * count + _HEADROOM
    return max(int(base), min(need, _CAP))


def numbered_lines(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for match in _LINE.finditer(text or ""):
        found.append((int(match.group(1)), match.group(0).strip()))
    return found


def list_complete(text: str, count: int) -> bool:
    nums = [num for num, _line in numbered_lines(text)]
    return count in nums and len(nums) >= count


def reply_truncated(prompt: str, text: str, reason: str = "") -> bool:
    """True when a numbered list is still short of N, or a reply hit the token cap."""
    count = list_count(prompt)
    if count:
        return not list_complete(text or "", count)
    return str(reason or "").strip().lower() in {"length", "max_tokens"}


def merge_list(base: str, more: str, count: int) -> str:
    """Append continuation lines that continue past the last number already present."""
    del count
    have = numbered_lines(base)
    last = have[-1][0] if have else 0
    extra = [line for num, line in numbered_lines(more) if num > last]
    if not extra:
        return base
    block = "\n".join(extra)
    if not (base or "").strip():
        return block
    return base.rstrip() + "\n" + block


def continuation_messages(messages: list, partial: str, count: int) -> list:
    note = (
        f"Continue the numbered list until item {count}. "
        "Start at the next missing number. Do not repeat earlier items. Do not stop early."
    )
    return [
        *messages,
        {"role": "assistant", "content": partial},
        {"role": "user", "content": note},
    ]


def finish_numbered(prompt: str, text: str, more) -> str:
    """Call `more(partial, n)` at most twice when the list is short of N."""
    count = list_count(prompt)
    current = text or ""
    if not count or list_complete(current, count):
        return current
    for _ in range(2):
        if list_complete(current, count):
            break
        extra = more(current, count)
        current = merge_list(current, extra or "", count)
    return current
