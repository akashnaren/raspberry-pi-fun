"""Search when the button is on, or when the line asks for it.

The composer control is the primary path. The list below is the backup for
someone who forgot the button. It is an explicit ask to search, a news
shape, or "who is" followed by two capitalised words. A name already written
in an earlier user turn or in stored facts is not searched again.
A bare news, latest, current, or today is not a cue. A pasted URL is not a cue.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from pair.turn.shape import _clean_untrusted, clip_words, user_question

# One list. Whole words, case insensitive. A multi-word cue allows any space.
# "latest <word> news" and "what's the latest" are the two shapes below, not
# extra list rows. "the latest <noun>" is not an ask.
CUES = (
    "web search",
    "search the web",
    "search for",
    "look up",
    "look it up",
    "lookup",
    "use your tools",
    "use the web",
    "use tools",
    "google",
    "latest news",
    "news about",
    "news on",
    "latest on",
    "in the news",
)

_CUE = re.compile(
    "|".join(
        r"\b" + r"\s+".join(re.escape(word) for word in cue.split()) + r"\b"
        for cue in CUES
    )
    + r"|\blatest\s+\w+\s+news\b"
    + r"|\bwhat(?:'s| is)\s+the\s+latest\b(?:\s+(?:on|about)\b|(?=\s*[?.!]|$))",
    re.I,
)
_URL = re.compile(r"https?://\S+", re.I)
_WHO_TAIL = re.compile(
    r"^(?:(?:hello|hi|hey)\b[\s,!.?]*)?"
    r"who(?:'s|’s|\s+is)\s+"
    r"(.+)$",
    re.I,
)
_EDGE = re.compile(r"^[^\w]+|[^\w]+$")
WHO_NOTE_LINES = 3
WHO_SNIPPET_CHARS = 220


def _cue_search(prompt: str) -> bool:
    """True for an explicit search or news ask. A pasted URL is not one."""
    return _CUE.search(_URL.sub(" ", user_question(prompt or ""))) is not None


def _who_words(question: str) -> list[str]:
    match = _WHO_TAIL.match(user_question(question or "").strip())
    if match is None:
        return []
    return match.group(1).split()


def _capitalised(word: str) -> bool:
    core = _EDGE.sub("", word or "")
    return bool(core) and core[0].isupper()


def _name_words(question: str) -> list[str]:
    """The first run of two or more capitalised words, as typed."""
    parts: list[str] = []
    for word in _who_words(question):
        if _capitalised(word):
            parts.append(_EDGE.sub("", word))
            continue
        if len(parts) >= 2:
            return parts
        parts = []
    return parts if len(parts) >= 2 else []


def _person_lookup(question: str) -> bool:
    """True when "who is" or "who's" is followed by two capitalised words."""
    return bool(_name_words(question))


def _already_named(question: str, prior: str) -> bool:
    """True when the exact name phrase is already in the prior text."""
    phrase = " ".join(_name_words(question))
    if not phrase or not (prior or "").strip():
        return False
    pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
    return re.search(pattern, prior) is not None


def _host(url: str) -> str:
    host = urlparse(url or "").hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _plain(text: str) -> str:
    return " ".join(_clean_untrusted(text or "").split())


def who_note_text(sources: list) -> str:
    """Up to three labelled hits, in source order. The host comes from the URL."""
    lines = []
    for item in sources or []:
        if len(lines) >= WHO_NOTE_LINES:
            break
        if not isinstance(item, dict):
            continue
        host = _host(str(item.get("url") or ""))
        if not host:
            continue
        title = _plain(str(item.get("title") or ""))
        snippet = _plain(str(item.get("snippet") or ""))
        if len(snippet) > WHO_SNIPPET_CHARS:
            snippet = clip_words(snippet, WHO_SNIPPET_CHARS)
        if not title and not snippet:
            continue
        if title and snippet:
            body = f"{title}: {snippet}"
        else:
            body = title or snippet
        lines.append(f"Result {len(lines) + 1} ({host}): {body}")
    return "\n".join(lines)


def explicit_search(prompt: str, prior: str = "") -> bool:
    """True when the line asks to search, asks for the news, or names a person."""
    if _cue_search(prompt):
        return True
    question = user_question(prompt or "")
    return _person_lookup(question) and not _already_named(question, prior)


def web_search_request(data: dict) -> bool:
    """True forces a lookup. False, or a missing field, leaves the keyword backup.

    Anything that is not a JSON boolean is rejected.
    """
    if "web_search" not in data:
        return False
    value = data.pop("web_search")
    if isinstance(value, bool):
        return value
    raise ValueError("web_search must be true or false.")
