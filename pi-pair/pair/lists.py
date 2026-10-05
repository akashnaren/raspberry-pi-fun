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


def _placeholder(num: int, body: str) -> bool:
    """'1. 1' is not an item. A prime such as '2. 3' still is."""
    token = re.sub(r"[^\w]+", "", (body or "").strip(), flags=re.UNICODE)
    if not token:
        return True
    return bool(re.fullmatch(r"\d{1,2}", token) and token == str(num))


def _matches(text: str) -> list[tuple[int, str, bool]]:
    found: list[tuple[int, str, bool]] = []
    for match in _LINE.finditer(text or ""):
        num = int(match.group(1))
        body = match.group(2).strip()
        found.append((num, match.group(0).strip(), _placeholder(num, body)))
    return found


def numbered_lines(text: str) -> list[tuple[int, str]]:
    """Real numbered lines. Placeholder rows such as '1. 1' are ignored."""
    return [(num, line) for num, line, placeholder in _matches(text) if not placeholder]


def placeholder_only(text: str) -> bool:
    """True when every numbered line is a placeholder and at least one exists."""
    rows = _matches(text)
    return bool(rows) and all(placeholder for _num, _line, placeholder in rows)


def list_complete(text: str, count: int) -> bool:
    nums = [num for num, _line in numbered_lines(text)]
    return count in nums and len(nums) >= count


def _bounded(lines: list[tuple[int, str]], count: int) -> str:
    kept: list[str] = []
    seen: set[int] = set()
    for num, line in lines:
        if num < 1 or num > count or num in seen:
            continue
        seen.add(num)
        kept.append(line)
    return "\n".join(kept)


def merge_list(base: str, more: str, count: int) -> str:
    """Append real lines past the last number, and never past N."""
    have = numbered_lines(base)
    last = have[-1][0] if have else 0
    extra = [line for num, line in numbered_lines(more) if last < num <= count]
    if not extra:
        return base
    block = "\n".join(extra)
    if not (base or "").strip():
        return block
    return base.rstrip() + "\n" + block


def _may_continue(prompt: str) -> bool:
    """Only a clearly harmless list is continued. Harmful subjects stay put."""
    from pair.assist import is_harmful, may_retry_refusal

    return bool(may_retry_refusal(prompt) and not is_harmful(prompt) and list_count(prompt))


def continuation_messages(messages: list, partial: str, count: int) -> list:
    """A refusal or a placeholder list is a fresh ask, not a replay."""
    if not numbered_lines(partial):
        note = (
            f"Reply with a numbered list of {count} items, one per line, from 1 to {count}. "
            "Use a real name or fact on each line, not the item number. "
            "Answer helpfully if the request is safe. Stop at item "
            f"{count}."
        )
        return [*list(messages or []), {"role": "user", "content": note}]
    note = (
        f"Continue the numbered list until item {count}. "
        "Start at the next missing number. Do not repeat earlier items. "
        f"Stop at item {count}."
    )
    return [
        *list(messages or []),
        {"role": "assistant", "content": partial},
        {"role": "user", "content": note},
    ]


def finish_numbered(prompt: str, text: str, more) -> str:
    """Call `more(partial, n)` at most twice when a harmless list stops before N.

    Zero real items get one fresh ask. The refusal or placeholder text is not
    replayed as the list. A harmful subject is not continued.
    """
    count = list_count(prompt)
    current = text or ""
    if not count or not _may_continue(prompt) or list_complete(current, count):
        return current
    if not numbered_lines(current):
        fresh = _bounded(numbered_lines(more(current, count) or ""), count)
        return fresh or current
    for _ in range(2):
        if list_complete(current, count):
            break
        current = merge_list(current, more(current, count) or "", count)
    return current
