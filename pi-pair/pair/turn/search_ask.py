"""Search when the button is on, or when the line asks for it.

The composer control is the primary path. The list below is the backup for
someone who forgot the button. It is explicit intent only: ask to search, or
ask for news or something current. A pasted URL is not a cue.
"""

from __future__ import annotations

import re

from pair.turn.shape import user_question

# One list. Whole words, case insensitive. A multi-word cue allows any space.
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
    "news",
    "latest",
    "current",
    "today",
)

_CUE = re.compile(
    "|".join(
        r"\b" + r"\s+".join(re.escape(word) for word in cue.split()) + r"\b"
        for cue in CUES
    ),
    re.I,
)
_URL = re.compile(r"https?://\S+", re.I)


def explicit_search(prompt: str) -> bool:
    """True when the latest line asks to search or asks for a current fact."""
    question = _URL.sub(" ", user_question(prompt or ""))
    return _CUE.search(question) is not None


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
