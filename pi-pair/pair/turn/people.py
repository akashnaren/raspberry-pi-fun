"""Choose one person from search hits before the note is built.

Small models blend every row they are shown. When the question names a person
and an organization, only a hit that matches both goes into the note. A short
form of a given name counts (Sid and Siddhartha), and a single initial counts
as a family name (N. and Nair). The note stays short so prefill can finish.
"""

from __future__ import annotations

import re

from pair.turn.search_ask import name_context
from pair.turn.shape import clip_words

BRIEF_CAP = 160
_RULE = "Use only this. If the organization is missing, say so."
_SPLIT = "Results are different people. Do not merge them."
_WORD = re.compile(r"[A-Za-z0-9]+(?:[-'’][A-Za-z0-9]+)*")
_HIT = re.compile(r"^- (?P<title>.*?) \((?P<url>https?://[^)\s]+)\): (?P<snippet>.*)$")
_URL = re.compile(r"https?://\S+", re.I)


def hits_from_note(context: str) -> list[dict]:
    """Hits packed as ``- title (url): snippet``. The address is not a fact."""
    found = []
    for raw in (context or "").splitlines():
        match = _HIT.match(raw.strip())
        if match is None:
            continue
        found.append(
            {
                "title": match.group("title").strip(),
                "url": match.group("url"),
                "snippet": match.group("snippet").strip(),
            }
        )
    return found


def person_note(prompt: str, hits: list[dict]) -> dict | None:
    """A short note for one automatic who-is, or None when that path does not apply.

    ``brief`` tells the caller not to append the long grounding rule. ``hits``
    is the one person the note is allowed to describe.
    """
    names, context = name_context(prompt)
    if not names:
        return None
    scored = _scored(names, context, hits)
    if not scored:
        return {"brief": True, "prompt_note": _fit(_miss(context)), "hits": []}
    best = max(scored, key=lambda row: (row[0], -row[1]))
    key = _surname(str(best[2].get("title") or ""), names)
    split = any(
        _surname(str(hit.get("title") or ""), names) != key
        for _score, _index, hit in scored
    )
    warning = _SPLIT if split else ""
    return {
        "brief": True,
        "prompt_note": _fit(_sentence(best[2], context), warning),
        "hits": [best[2]],
    }


def _blob(hit: dict) -> str:
    title = str(hit.get("title") or "")
    snippet = str(hit.get("snippet") or "")
    return f"{title} {snippet}"


def _scored(
    names: list[str], context: list[str], hits: list[dict]
) -> list[tuple[int, int, dict]]:
    scored = []
    for index, hit in enumerate(hits or []):
        if not isinstance(hit, dict):
            continue
        text = _blob(hit)
        score = _name_score(names, text)
        if score <= 0:
            continue
        if context and not _has_org(context, text):
            continue
        scored.append((score, index, hit))
    return scored


def _miss(context: list[str]) -> str:
    if context:
        return f"No result mentions {' '.join(context)}."
    return "No result matches that name."


def _words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def _given_match(query: str, word: str) -> bool:
    """Exact given name, or a short form of at least three letters."""
    left = query.lower().rstrip(".")
    right = word.lower().rstrip(".")
    if not left or not right:
        return False
    if left == right:
        return True
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    return len(short) >= 3 and long.startswith(short)


def _family_match(query: str, word: str) -> bool:
    """Exact family name, or a one-letter initial of it."""
    left = query.lower().rstrip(".")
    right = word.lower().rstrip(".")
    if not left or not right:
        return False
    if left == right:
        return True
    if len(left) == 1 and right.startswith(left):
        return True
    if len(right) == 1 and left.startswith(right):
        return True
    return False


def _name_score(names: list[str], text: str) -> int:
    tokens = _words(text)
    if not names or not tokens:
        return 0
    given = names[0]
    if not any(_given_match(given, token) for token in tokens):
        return 0
    score = 2 if any(token.lower() == given.lower() for token in tokens) else 1
    if len(names) == 1:
        return score
    family = names[-1]
    if not any(_family_match(family, token) for token in tokens):
        return 0
    exact = any(token.lower().rstrip(".") == family.lower() for token in tokens)
    return score + (3 if exact else 1)


def _has_org(context: list[str], text: str) -> bool:
    lowered = text.lower()
    for token in context:
        if re.search(r"\b" + re.escape(token.lower()) + r"\b", lowered) is None:
            return False
    return True


def _surname(title: str, names: list[str]) -> str:
    """Family key. An initial collapses onto the asked-for family name."""
    family = names[-1] if len(names) > 1 else ""
    tokens = _words(title)
    if family:
        folded = family.lower()
        if any(token.lower().rstrip(".") == folded for token in tokens):
            return folded
        initial = folded[0]
        for token in tokens:
            letter = token.lower().rstrip(".")
            if len(letter) == 1 and letter == initial:
                return folded
    head = re.split(r"\s+[-–—|]\s+", title or "", maxsplit=1)[0]
    full = [word for word in _words(head) if len(word) > 1]
    if full:
        return full[-1].lower()
    if tokens:
        return tokens[-1].lower().rstrip(".")
    return ""


def _sentence(hit: dict, context: list[str]) -> str:
    title = " ".join(str(hit.get("title") or "").split())
    title = re.sub(r"\s+[-–—]\s+", ", ", title)
    snippet = " ".join(_URL.sub("", str(hit.get("snippet") or "")).split())
    # The title already names the organization. A second sentence is prefill.
    title_has_context = bool(context) and _has_org(context, title)
    if snippet and snippet.lower() not in title.lower() and not title_has_context:
        if len(snippet) > 48:
            snippet = clip_words(snippet, 48).rstrip("…").strip()
        body = f"{title}. {snippet}" if snippet else title
    else:
        body = title
    body = body.strip(" .")
    if not body:
        return ""
    return body + "."


def _fit(fact: str, extra: str = "") -> str:
    """Fact, optional warning, and the one-line rule, within BRIEF_CAP."""

    def assemble(body: str, warn: str) -> str:
        return " ".join(part for part in (warn.strip(), body.strip(), _RULE) if part)

    for warn in (extra, ""):
        text = assemble(fact, warn)
        if len(text) <= BRIEF_CAP:
            return text
        room = BRIEF_CAP - len(assemble("", warn)) - 1
        if room < 16:
            continue
        clipped = clip_words(fact, room)
        text = assemble(clipped, warn)
        if len(text) <= BRIEF_CAP:
            return text
    return clip_words(_RULE, BRIEF_CAP)
