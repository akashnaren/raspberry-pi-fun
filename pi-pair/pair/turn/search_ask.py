"""Search when the button is on, or when the line asks for it.

The composer control is the primary path. The list below is the backup for
someone who forgot the button. It is an explicit ask to search, a news
shape, or "who is <Name>". "who is my …" and "who is in …" are not names.
A name already in the thread or in stored facts is not searched again.
A bare news, latest, current, or today is not a cue. A pasted URL is not a cue.
"""

from __future__ import annotations

import re

from pair.turn.shape import clip_words, search_result_text, user_question

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
# The first word after "who is" is a name. These words are not.
_NOT_A_NAME = frozenset(
    {
        "a",
        "an",
        "at",
        "best",
        "better",
        "going",
        "he",
        "her",
        "him",
        "i",
        "in",
        "it",
        "me",
        "my",
        "on",
        "our",
        "responsible",
        "she",
        "that",
        "the",
        "their",
        "them",
        "they",
        "this",
        "we",
        "worst",
        "worse",
        "you",
        "your",
    }
)
_WHO = re.compile(
    r"^(?:(?:hello|hi|hey)\b[\s,!.?]*)?"
    r"who(?:'s|’s|\s+is)\s+"
    r"([a-z][\w'.-]*)",
    re.I,
)
# A company or place after at/from/with/of, or the last word of a longer
# "who is <name> <context>", breaks ties. It does not outrank the full name.
_CONTEXT_AT = frozenset({"at", "from", "with", "of"})
_CONTEXT_WORD = frozenset({"dating", "married"})
_CLAUSE = re.compile(r"[,;:]|\s+[-–—]\s+|\band\b", re.I)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9'.-]*", re.I)
WHO_NOTE_LINES = 3
WHO_SNIPPET_CHARS = 160
WHO_NOTE_CHARS = 500


def _person_lookup(question: str) -> bool:
    """True for "who is <Name>" after an optional hello, hi, or hey."""
    match = _WHO.match(question.strip())
    if match is None:
        return False
    return match.group(1).lower() not in _NOT_A_NAME


def _already_named(question: str, prior: str) -> bool:
    """True when that name is already in the thread or in stored facts."""
    match = _WHO.match((question or "").strip())
    if match is None or not (prior or "").strip():
        return False
    return re.search(r"\b" + re.escape(match.group(1)) + r"\b", prior, re.I) is not None


def _who_tail(question: str) -> str:
    """The name and whatever follows it, cut at a comma, a dash, or "and"."""
    text = _URL.sub(" ", user_question(question or "")).strip()
    match = _WHO.match(text)
    if match is None or match.group(1).lower() in _NOT_A_NAME:
        return ""
    tail = text[match.start(1) :]
    cut = _CLAUSE.search(tail)
    if cut is not None:
        tail = tail[: cut.start()]
    return tail


def _name_and_context(question: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Name words, then a company or context when the question names one."""
    raw = _TOKEN.findall(_who_tail(question))
    words = [word.lower() for word in raw]
    if not words:
        return (), ()
    for index, word in enumerate(words):
        if index == 0:
            continue
        if word in _CONTEXT_AT:
            names = tuple(item for item in words[:index] if item not in _CONTEXT_AT)
            ctx = tuple(item for item in words[index + 1 :] if item not in _CONTEXT_AT)
            return names, ctx
        if word in _CONTEXT_WORD:
            return tuple(words[:index]), tuple(words[index:])
    if len(words) >= 3:
        return tuple(words[:-1]), (words[-1],)
    return tuple(words), ()


def _asked_name(question: str, names: tuple[str, ...]) -> str:
    """The name as written, without the company or context that follows it."""
    raw = _TOKEN.findall(_who_tail(question))
    return " ".join(raw[: len(names)])


def _mentions(text: str, tokens: tuple[str, ...]) -> bool:
    if not tokens:
        return False
    return all(
        re.search(r"\b" + re.escape(token) + r"\b", text, re.I) is not None
        for token in tokens
    )


def _rank_lines(
    lines: list[str], names: tuple[str, ...], ctx: tuple[str, ...]
) -> list[str]:
    """Full name first. A context word only breaks ties. Nothing is dropped."""
    if not ctx:
        return lines

    def key(line: str) -> tuple[bool, bool]:
        return (_mentions(line, names), _mentions(line, ctx))

    return sorted(lines, key=key, reverse=True)


def _snippet_body(lines: list[str]) -> str:
    """Top snippets, each trimmed, then the whole note held to its budget."""
    parts = []
    for line in lines[:WHO_NOTE_LINES]:
        shown = search_result_text(line, WHO_SNIPPET_CHARS)
        if shown:
            parts.append(shown)
    body = "\n".join(parts)
    if len(body) <= WHO_NOTE_CHARS:
        return body
    return clip_words(body, WHO_NOTE_CHARS)


def who_note_text(prompt: str, full: str) -> str | None:
    """Labelled who-is note: three trimmed snippets. None otherwise."""
    names, ctx = _name_and_context(prompt)
    if not names:
        return None
    lines = []
    for raw in (full or "").splitlines():
        text = raw.strip()
        if text and not text.lower().startswith("web search notes"):
            lines.append(text)
    if not lines:
        return ""
    body = _snippet_body(_rank_lines(lines, names, ctx))
    gap = ""
    if not any(_mentions(line, names) for line in lines):
        label = _asked_name(prompt, names)
        if label:
            gap = f"No result mentions {label}."
    if not body and not gap:
        return ""
    head = "Web search:"
    if gap:
        head = head + "\n" + gap
    if not body:
        return head
    return head + "\n" + body


def explicit_search(prompt: str, prior: str = "") -> bool:
    """True when the line asks to search, asks for the news, or names a person."""
    question = _URL.sub(" ", user_question(prompt or "")).strip()
    if _CUE.search(question) is not None:
        return True
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
