"""Numbered asks get enough tokens and a continuation when the list stops early.

A tiny Qwen tag often answers "Top 5 cars" with "I'm sorry, but I can't assist
with that". That line is not a router safety block, a chart hint, or an image
prompt. Continuing it asks for the missing numbers, and the tag then emits
placeholders such as "1. 1". A benign list gets one answer nudge instead.
A harmful ask keeps the refusal and is not continued.
"""

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
_CANNED = (
    "i'm sorry, but i can't assist with that",
    "i'm sorry, but i cannot assist with that",
    "i'm sorry, i can't assist with that",
    "i'm sorry, i cannot assist with that",
    "i am sorry, but i can't assist with that",
    "i am sorry, but i cannot assist with that",
    "sorry, but i can't assist with that",
    "sorry, but i cannot assist with that",
    "i can't assist with that",
    "i cannot assist with that",
    "i'm sorry, but i can't help with that",
    "i'm sorry, but i cannot help with that",
    "i can't help with that",
    "i cannot help with that",
    "i'm unable to assist with that",
    "i am unable to assist with that",
    "as an ai, i can't assist with that",
    "as an ai, i cannot assist with that",
)
_PLACEHOLDER = re.compile(r"(?m)^\s*(\d{1,2})[\.\)]\s+\1\s*$")
_PLACEHOLDER_PAIR = re.compile(r"\b\d{1,2}[\.\)]\s+\d{1,2}\b")
_HARM = re.compile(
    r"\b(?:bombs?|explosives?|ieds?|grenades?|firearms?|guns?|rifles?|"
    r"shootings?|malware|ransomware|phishing|keyloggers?|"
    r"meth(?:amphetamine)?|fentanyl|heroin|csam|"
    r"child(?:ren)?(?:'s)?\s+porn|child\s+sexual|"
    r"how to (?:kill|murder|poison)|suicide methods?|"
    r"nuclear weapons?|bioweapons?|"
    r"make (?:a |an )?(?:bomb|meth|explosive)|"
    r"build (?:a |an )?(?:bomb|weapon|explosive|nuclear))\b",
    re.I,
)


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


def _fold(text: str) -> str:
    folded = (text or "").lower().replace("\u2019", "'").replace("`", "'")
    return " ".join(folded.split())


def is_canned_refusal(text: str) -> bool:
    """True when the reply is only a soft refusal, plus optional placeholder numbers."""
    folded = _fold(text)
    if not folded:
        return False
    for phrase in _CANNED:
        at = folded.find(phrase)
        if at < 0:
            continue
        tail = (folded[:at] + " " + folded[at + len(phrase) :]).strip()
        tail = _PLACEHOLDER_PAIR.sub(" ", tail)
        tail = re.sub(r"\b(?:request|sorry|please|however|unfortunately)\b", " ", tail)
        if not re.sub(r"[^a-z]+", "", tail):
            return True
    return False


def refusal_holding(text: str) -> bool:
    """True while streamed text might still be only that canned refusal."""
    folded = _fold(text)
    if not folded:
        return True
    if is_canned_refusal(text):
        return True
    if len(folded) > 200:
        return False
    return any(phrase.startswith(folded) for phrase in _CANNED)


def harmful_ask(prompt: str) -> bool:
    """Harmful list asks keep a refusal. Benign top-N lines do not match."""
    from pair.turn import user_question

    return bool(_HARM.search(user_question(prompt) or ""))


def benign_list_ask(prompt: str) -> bool:
    """Top-N and plain lists, except news and harmful asks."""
    if harmful_ask(prompt):
        return False
    from pair.turn import is_list_intent

    return bool(list_count(prompt) or is_list_intent(prompt))


def strip_refusal_junk(text: str) -> str:
    """Drop a canned refusal line and placeholder rows such as ``1. 1``."""
    kept: list[str] = []
    for line in (text or "").splitlines():
        if is_canned_refusal(line) or _PLACEHOLDER.match(line):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def list_answer_messages(messages: list, prompt: str) -> list:
    """One short nudge. The refusal itself is not fed back in."""
    count = list_count(prompt)
    if count:
        note = (
            f"Answer with a numbered list of {count} real items. "
            "Do not apologize and do not say you can't assist."
        )
    else:
        note = (
            "Answer with the list the user asked for. "
            "Do not apologize and do not say you can't assist."
        )
    return [*list(messages or []), {"role": "user", "content": note}]


def recover_list_refusal(prompt: str, text: str, retry) -> str:
    """Replace one canned refusal on a benign list. Harmful asks stay refused.

    ``retry`` is called at most once. A second refusal is kept, unless the
    first reply already had real list lines under the apology.
    """
    current = text or ""
    if not benign_list_ask(prompt) or not is_canned_refusal(current):
        return current
    try:
        nxt = retry() or ""
    except Exception:
        nxt = ""
    if str(nxt).strip() and not is_canned_refusal(nxt):
        return nxt
    stripped = strip_refusal_junk(current)
    if stripped and not is_canned_refusal(stripped):
        return stripped
    return current


def finish_numbered(prompt: str, text: str, more) -> str:
    """Call `more(partial, n)` at most twice when the list is short of N."""
    count = list_count(prompt)
    current = text or ""
    if not count or list_complete(current, count) or is_canned_refusal(current):
        return current
    for _ in range(2):
        if list_complete(current, count):
            break
        extra = more(current, count)
        current = merge_list(current, extra or "", count)
    return current
