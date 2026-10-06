"""Counted math sequences are computed here, not recalled by the model.

Primes use a sieve. The other sequences are generators. Nothing in this
module is a stored list of answers. A request past the cap is shortened
and the reply says so.
"""

from __future__ import annotations

import math
import re
from itertools import islice

_CAP = 1000

_PREFIX = (
    r"^(?:please\s+)?"
    r"(?:(?:what\s+are|what're|what's|show(?:\s+me)?|give(?:\s+me)?|"
    r"list|name|tell(?:\s+me)?)\s+)?"
    r"(?:me\s+)?(?:the\s+|a\s+)?"
)
_KIND = (
    r"(?P<kind>primes?|prime\s+numbers?|"
    r"fibonacci(?:\s+numbers?|\s+sequence)?|"
    r"perfect\s+squares?|square\s+numbers?|squares|"
    r"perfect\s+cubes?|cube\s+numbers?|cubes|"
    r"even\s+numbers?|odd\s+numbers?|"
    r"factorials?|"
    r"triangular\s+numbers?|"
    r"multiples\s+of\s+(?P<step>\d{1,6})|"
    r"powers\s+of\s+(?P<base>\d{1,6}))"
)
_ORDERED = re.compile(
    _PREFIX + r"(?:first|top|next)\s+(\d{1,6})\s+" + _KIND + r"\s*[?.!]?\s*$",
    re.I,
)
_COUNTED = re.compile(
    _PREFIX + r"(\d{1,6})\s+" + _KIND + r"\s*[?.!]?\s*$",
    re.I,
)
_BETWEEN = re.compile(
    _PREFIX
    + r"(?:all\s+)?"
    + _KIND
    + r"\s+between\s+(?P<lo>\d{1,6})\s+and\s+(?P<hi>\d{1,6})\s*[?.!]?\s*$",
    re.I,
)
_PLOT = re.compile(r"\b(?:plot|chart|graph)\b", re.I)


def _normalize(kind: str) -> str:
    text = (kind or "").lower()
    if text.startswith("prime"):
        return "prime"
    if text.startswith("fibonacci"):
        return "fibonacci"
    if "square" in text:
        return "square"
    if "cube" in text:
        return "cube"
    if text.startswith("even"):
        return "even"
    if text.startswith("odd"):
        return "odd"
    if text.startswith("factorial"):
        return "factorial"
    if text.startswith("triangular"):
        return "triangular"
    if text.startswith("multiple"):
        return "multiple"
    if text.startswith("power"):
        return "power"
    return ""


def _parse(prompt: str) -> tuple[int, str, int] | None:
    """(asked count, kind, parameter) or None when this is not a counted sequence."""
    from pair.assist import is_harmful

    text = " ".join((prompt or "").split())
    if not text or is_harmful(text) or _PLOT.search(text):
        return None
    if _BETWEEN.search(text):
        return None
    match = _ORDERED.search(text) or _COUNTED.search(text)
    if not match:
        return None
    asked = int(match.group(1))
    if asked < 1:
        return None
    kind = _normalize(match.group("kind"))
    if not kind:
        return None
    raw = match.group("step") or match.group("base") or "0"
    return asked, kind, int(raw)


def _parse_range(prompt: str) -> tuple[str, int, int, int] | None:
    """(kind, low, high, parameter) for 'primes between 1 and 50'."""
    from pair.assist import is_harmful

    text = " ".join((prompt or "").split())
    if not text or is_harmful(text) or _PLOT.search(text):
        return None
    match = _BETWEEN.search(text)
    if not match:
        return None
    kind = _normalize(match.group("kind"))
    if not kind:
        return None
    low = int(match.group("lo"))
    high = int(match.group("hi"))
    if high < low:
        low, high = high, low
    raw = match.group("step") or match.group("base") or "0"
    return kind, low, high, int(raw)


def _sieve(limit: int) -> list[int]:
    if limit < 2:
        return []
    marks = bytearray(b"\x01") * (limit + 1)
    marks[0:2] = b"\x00\x00"
    root = int(limit**0.5)
    for number in range(2, root + 1):
        if not marks[number]:
            continue
        start = number * number
        count = ((limit - start) // number) + 1
        marks[start : limit + 1 : number] = b"\x00" * count
    return [number for number, flag in enumerate(marks) if flag]


def _primes(count: int) -> list[int]:
    if count < 1:
        return []
    if count < 6:
        limit = 15
    else:
        log = math.log(count)
        limit = int(count * (log + math.log(log)) + 10)
    while True:
        found = _sieve(limit)
        if len(found) >= count:
            return found[:count]
        limit *= 2


def _fibonacci():
    first, second = 0, 1
    while True:
        yield first
        first, second = second, first + second


def _squares():
    number = 1
    while True:
        yield number * number
        number += 1


def _cubes():
    number = 1
    while True:
        yield number * number * number
        number += 1


def _evens():
    number = 2
    while True:
        yield number
        number += 2


def _odds():
    number = 1
    while True:
        yield number
        number += 2


def _factorials():
    number = 1
    value = 1
    while True:
        value *= number
        yield value
        number += 1


def _triangular():
    number = 1
    while True:
        yield number * (number + 1) // 2
        number += 1


def _multiples(param: int):
    number = 1
    while True:
        yield number * param
        number += 1


def _powers(param: int):
    value = param
    while True:
        yield value
        value *= param if param else 0


def _stream(kind: str, param: int):
    streams = {
        "fibonacci": _fibonacci,
        "square": _squares,
        "cube": _cubes,
        "even": _evens,
        "odd": _odds,
        "factorial": _factorials,
        "triangular": _triangular,
    }
    plain = streams.get(kind)
    if plain is not None:
        return plain()
    if kind == "multiple":
        return _multiples(param)
    if kind == "power":
        return _powers(param)
    return iter(())


def _values(kind: str, count: int, param: int) -> list[int]:
    if kind == "prime":
        return _primes(count)
    return [int(item) for item in islice(_stream(kind, param), count)]


def _range_values(kind: str, low: int, high: int, param: int) -> list[int]:
    """Numbers of this kind inside the inclusive bounds, capped."""
    if high < low:
        return []
    if kind == "prime":
        return [number for number in _sieve(high) if number >= low][:_CAP]
    found: list[int] = []
    for value in _stream(kind, param):
        number = int(value)
        if number > high:
            break
        if number >= low:
            found.append(number)
        if len(found) >= _CAP:
            break
        if number > high + 1_000_000:
            break
    return found


def sequence_values(prompt: str) -> list[int] | None:
    """The numbers for this ask, capped, or None when it is not a sequence."""
    span = _parse_range(prompt)
    if span:
        kind, low, high, param = span
        return _range_values(kind, low, high, param)
    parsed = _parse(prompt)
    if not parsed:
        return None
    asked, kind, param = parsed
    return _values(kind, min(asked, _CAP), param)


def _numbered(values: list[int]) -> str:
    return "\n".join(f"{index}. {value}" for index, value in enumerate(values, 1))


def sequence_answer(prompt: str) -> str | None:
    """A numbered list of the sequence, or None to use the model."""
    span = _parse_range(prompt)
    if span:
        kind, low, high, param = span
        values = _range_values(kind, low, high, param)
        if not values:
            return None
        return _numbered(values)
    parsed = _parse(prompt)
    if not parsed:
        return None
    asked, kind, param = parsed
    count = min(asked, _CAP)
    values = _values(kind, count, param)
    if len(values) != count:
        return None
    body = _numbered(values)
    if asked > _CAP:
        return f"Showing {_CAP} items (capped from {asked}).\n" + body
    return body
