"""Search when the button is on, or when the line asks for it.

The composer control is the primary path. The list below is the backup for
someone who forgot the button. It is an explicit ask to search, a news
shape, or a person lookup. News shapes are latest news, news about, news on,
latest on, in the news, what's the latest, or latest <word> news. A person
lookup is who is <name>, who's <name>, or who is <name> <org>, with an
optional leading hello, hi, or hey. A bare news, latest, current, or today
is not a cue. A pasted URL is not a cue. "who is better", "who is the best",
and "who are you" are not cues.
"""

from __future__ import annotations

import re

from pair.turn.shape import user_question

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

# The first word after "who is" or "who's" is a name. These words are not.
_GENERIC_WHO = frozenset(
    {
        "a",
        "an",
        "anybody",
        "anyone",
        "best",
        "better",
        "going",
        "he",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "i",
        "it",
        "its",
        "me",
        "mine",
        "my",
        "not",
        "our",
        "ours",
        "responsible",
        "she",
        "that",
        "the",
        "their",
        "them",
        "there",
        "they",
        "this",
        "us",
        "we",
        "worse",
        "worst",
        "you",
        "your",
        "yours",
    }
)
_WHO_NAME = re.compile(
    r"^(?:(?:hello|hi|hey)\b[\s,!.?]*)?"
    r"who(?:'s|’s|\s+is)\s+"
    r"([a-z][\w'.-]*)",
    re.I,
)


def _person_lookup(question: str) -> bool:
    """True for "who is <name>" after an optional hello, hi, or hey."""
    match = _WHO_NAME.match(question)
    if match is None:
        return False
    return match.group(1).lower() not in _GENERIC_WHO


# Words that sit between a name and an organization. They are not tokens.
_NOT_NAME = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "at",
        "for",
        "from",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)
_NAME_WORD = re.compile(r"[A-Za-z0-9]+(?:[-'’][A-Za-z0-9]+)*")


# The first of these starts the organization or other context, when one is named.
_CONTEXT_PREP = frozenset({"about", "at", "for", "from", "in", "of", "on", "with"})


def _who_tail(question: str) -> str:
    """Words after who-is, stopping at a spaced dash."""
    match = _WHO_NAME.match(question)
    if match is None:
        return ""
    tail = question[match.start(1) :]
    return re.split(r"\s+[-–—]\s+", tail, maxsplit=1)[0]


def _name_query(question: str) -> str:
    """Name and org tokens after who-is. Function words drop out."""
    tokens = [
        word
        for word in _NAME_WORD.findall(_who_tail(question))
        if word.lower() not in _NOT_NAME
    ]
    return " ".join(tokens)


def name_context(prompt: str) -> tuple[list[str], list[str]]:
    """Name tokens and org tokens for an automatic who-is. Empty when it is not one.

    A preposition such as "at" or "from" starts the context. With no preposition,
    three or more tokens treat the last one as context ("sid nair etched").
    An explicit search cue is not this path.
    """
    question = user_question(prompt or "").strip()
    cleaned = _URL.sub(" ", question).strip()
    if not _person_lookup(cleaned) or _CUE.search(cleaned) is not None:
        return [], []
    words = _NAME_WORD.findall(_who_tail(cleaned))
    prep_at = next(
        (index for index, word in enumerate(words) if word.lower() in _CONTEXT_PREP),
        None,
    )
    if prep_at is None:
        kept = [word for word in words if word.lower() not in _NOT_NAME]
        if len(kept) >= 3:
            return kept[:-1], kept[-1:]
        return kept, []
    names = [word for word in words[:prep_at] if word.lower() not in _NOT_NAME]
    context = [word for word in words[prep_at + 1 :] if word.lower() not in _NOT_NAME]
    return names, context


def search_query(prompt: str) -> str:
    """Text sent to the web. A who-is name keeps every name and org token."""
    question = user_question(prompt or "").strip()
    cleaned = _URL.sub(" ", question).strip()
    if _person_lookup(cleaned) and _CUE.search(cleaned) is None:
        built = _name_query(cleaned)
        if built:
            return built
    return question


def explicit_search(prompt: str) -> bool:
    """True when the line asks to search, asks for the news, or names a person."""
    question = _URL.sub(" ", user_question(prompt or "")).strip()
    return _CUE.search(question) is not None or _person_lookup(question)


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
