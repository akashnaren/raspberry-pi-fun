"""Keep ordinary replies helpful and free of product internals.

The small local model sometimes refuses a harmless list, or answers a greeting
with mesh jargon. A system hint asks it to answer. If the reply is still a
canned soft refusal, one retry asks it to answer helpfully, then a short
attempt stands in. A clearly harmful request keeps the model's refusal.
"""

from __future__ import annotations

import re

from pair.charts import is_structured_request
from pair.lists import list_count

HELPFUL_NUDGE = "Answer the user's question helpfully."

ANSWER_HINT = (
    "Answer the user's question directly and helpfully. "
    "Lists, cars, movies, greetings, and ordinary questions are welcome. "
    "Do not refuse a harmless question. "
    "Do not mention the thinking control, model choice, routing, or private mesh."
)

LIST_HINT = (
    "The user wants a numbered list. Reply with one item per line, numbered from 1. "
    "Answer helpfully. Do not say you cannot assist. "
    "Do not mention the thinking control, model choice, or routing."
)

_CARS = (
    "Toyota Corolla",
    "Honda Civic",
    "Tesla Model Y",
    "Ford F-150",
    "Volkswagen Golf",
    "Hyundai Elantra",
    "Chevrolet Silverado",
    "BMW 3 Series",
    "Mercedes-Benz C-Class",
    "Subaru Outback",
)

_MOVIES = (
    "The Godfather",
    "Spirited Away",
    "The Dark Knight",
    "Parasite",
    "The Shawshank Redemption",
    "Inception",
    "Get Out",
    "Mad Max: Fury Road",
    "Whiplash",
    "Lady Bird",
)

_GREETING = re.compile(
    r"^(?:(?:hi|hello|hey|hiya|howdy|yo|sup)(?:\s+there)?|"
    r"good\s+(?:morning|afternoon|evening|night)|"
    r"what'?s\s+up|how\s+are\s+you|how'?s\s+it\s+going)"
    r"[!?.\s]*$",
    re.I,
)

_HARMFUL = re.compile(
    r"\b(?:how\s+to|how\s+do\s+i|make|build|synthesize|cook)\b.{0,48}"
    r"\b(?:bomb|explosive|methamphetamine|fentanyl|sarin|anthrax)\b|"
    r"\b(?:child|minor|underage)\b.{0,32}\b(?:porn|sexual|nude|nudes)\b",
    re.I,
)

_SOFT = re.compile(
    r"\b(?:"
    r"i(?:'m| am) sorry,? but i can(?:not|'t) (?:assist|help)|"
    r"i can(?:not|'t) (?:assist|help) with (?:that|this)|"
    r"i(?:'m| am) (?:not able|unable) to (?:assist|help)|"
    r"i(?:'m| am) sorry,? i can(?:not|'t) (?:assist|help)"
    r")\b",
    re.I,
)

_META = re.compile(
    r"\b(?:"
    r"thinking\s+(?:level|control|effort)|"
    r"(?:low|medium|high)\s+(?:thinking|effort)|"
    r"effort\s+(?:level|setting)|"
    r"flash\s+map|"
    r"(?:flash|pro)\s+(?:mode|model|route)|"
    r"auto\s+mode|"
    r"model\s+routing|"
    r"routing\s+label|"
    r"private\s+(?:pi\s+)?mesh|"
    r"pi\s+private\s+mesh|"
    r"mesh\s+assistant|"
    r"canned\s+map|"
    r"generative\s+brain"
    r")\b",
    re.I,
)

_INFRA = re.compile(
    r"\b(?:"
    r"private\s+(?:pi\s+)?mesh|"
    r"pi\s+private\s+mesh|"
    r"mesh\s+assistant|"
    r"canned\s+map|"
    r"generative\s+brain|"
    r"pi[234]\b|"
    r"cache\s+path|"
    r"on-prem|"
    r"\bfleet\b"
    r")\b",
    re.I,
)

_OPENERS = (
    "i'm sorry",
    "i am sorry",
    "sorry,",
    "sorry ",
    "i can't",
    "i cannot",
    "i can not",
    "unfortunately",
)

_FENCE = re.compile(r"(```[\s\S]*?```)")


def _fold(text: str) -> str:
    return (text or "").replace("’", "'").replace("‘", "'").replace("`", "'")


def is_harmful(prompt: str) -> bool:
    """True only for a clearly harmful request. Ordinary questions stay false."""
    return bool(_HARMFUL.search(prompt or ""))


def is_casual_greeting(prompt: str) -> bool:
    return bool(_GREETING.match(" ".join((prompt or "").split())))


def is_soft_refusal(text: str) -> bool:
    return bool(_SOFT.search(_fold(text)))


def friendly_greeting(prompt: str) -> str:
    folded = " ".join((prompt or "").lower().split())
    if folded.startswith("good morning"):
        return "Good morning! How can I help?"
    if folded.startswith("good afternoon"):
        return "Good afternoon! How can I help?"
    if folded.startswith("good evening") or folded.startswith("good night"):
        return "Good evening! How can I help?"
    if folded.startswith("hey"):
        return "Hey! How can I help?"
    return "Hi! How can I help?"


def answer_hint_for(prompt: str) -> str | None:
    """A short system hint for a harmless question. Plots already have one."""
    if is_harmful(prompt) or is_structured_request(prompt):
        return None
    if list_count(prompt):
        return LIST_HINT
    return ANSWER_HINT


def leaks_infra(text: str) -> bool:
    return bool(_INFRA.search(text or "") or _META.search(text or ""))


def _drop_meta(prose: str) -> str:
    if not _META.search(prose):
        return prose
    kept = []
    for line in prose.splitlines():
        if re.match(r"\s*\d+\.\s+\S", line) and not _META.search(line):
            kept.append(line)
            continue
        pieces = re.split(r"(?<=[.!?])\s+", line)
        good = [piece for piece in pieces if piece.strip() and not _META.search(piece)]
        if good:
            kept.append(" ".join(good))
    return "\n".join(kept)


def scrub_reply(text: str) -> str:
    """Drop sentences that name effort, routing, or mesh internals.

    Fenced charts and tables stay intact. Text with none of those phrases
    is returned unchanged.
    """
    raw = text or ""
    if not _META.search(raw):
        return raw
    parts = _FENCE.split(raw)
    cleaned = [_drop_meta(part) if index % 2 == 0 else part for index, part in enumerate(parts)]
    return "\n".join(line for line in "".join(cleaned).splitlines() if line.strip()).strip()


def withhold_partial(text: str) -> bool:
    """Hold a stream that still looks like a soft refusal or an infra leak."""
    sample = " ".join(_fold(text).split())
    if not sample:
        return False
    if is_soft_refusal(sample) or _META.search(sample):
        return True
    folded = sample.lower()
    if len(folded) > 180:
        return False
    return any(folded.startswith(opener) or opener.startswith(folded) for opener in _OPENERS)


def visible_canned(prompt: str, answer: str) -> str:
    """A stored greeting that talks about the mesh becomes a normal hello."""
    text = answer or ""
    if is_casual_greeting(prompt) and (leaks_infra(text) or is_soft_refusal(text)):
        return friendly_greeting(prompt)
    return text


def _numbered(items: tuple[str, ...], count: int) -> str:
    n = max(2, min(int(count), len(items)))
    return "\n".join(f"{index}. {name}" for index, name in enumerate(items[:n], start=1))


def short_attempt(prompt: str) -> str:
    """A real answer when the model will only refuse a harmless question."""
    if is_casual_greeting(prompt):
        return friendly_greeting(prompt)
    count = list_count(prompt) or 0
    folded = prompt or ""
    if count and re.search(r"\bcars?\b", folded, re.I):
        return _numbered(_CARS, count)
    if count and re.search(r"\b(?:movies|films)\b", folded, re.I):
        return _numbered(_MOVIES, count)
    if count:
        return f"Here are {count} to start with. Tell me the kind you care about and I will narrow it."
    return "I can help with that. Ask for the detail you want and I will answer it."


def settle_reply(prompt: str, text: str, retry) -> str:
    """Scrub a harmless reply. One helpful retry, then a short attempt.

    `retry` is called at most once. A harmful prompt is returned unchanged.
    """
    if is_harmful(prompt):
        return text or ""
    if is_casual_greeting(prompt):
        cleaned = scrub_reply(text or "")
        if cleaned and not is_soft_refusal(cleaned) and not leaks_infra(cleaned):
            return cleaned
        return friendly_greeting(prompt)
    if not is_soft_refusal(text or ""):
        cleaned = scrub_reply(text or "")
        if cleaned:
            return cleaned
        if _META.search(text or ""):
            return short_attempt(prompt)
        return text or ""
    second = ""
    try:
        second = retry() or ""
    except Exception:
        second = ""
    again = scrub_reply(second)
    if again and not is_soft_refusal(again) and not (is_casual_greeting(prompt) and leaks_infra(again)):
        return again
    return short_attempt(prompt)
