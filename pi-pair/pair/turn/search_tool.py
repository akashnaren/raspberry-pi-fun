"""One web_search tool on the answer call. No separate decide pass.

The schema is part of the cached prefix. A tool call runs one lookup on
pi2 and a second decode. A turn with no tool call is one decode.
"""

from __future__ import annotations

import json
import re

from pair.turn.shape import clip_words, neutralize, user_question

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web. Use it when the user asks you to search or look something up, "
            "asks for news or other recent information, or asks about a specific person, "
            "organization, or event you are not sure about."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A short web search query.",
                }
            },
            "required": ["query"],
        },
    },
}

RESULT_RULE = (
    "Answer the user from these results, in your own words and in the format they asked for. "
    "Put the site name in parentheses after a fact you took from it. If the results do not answer "
    "the question, say the search did not find it. Never guess a name, job title, role, date, or number."
)
NO_RESULT = (
    "The web search found nothing useful. Tell the user in one sentence that you could not find "
    "reliable results and offer to try other wording. Never guess a name, job title, role, date, or number."
)
SEARCH_OFF = "Web search is off for this chat. Answer from what you know, and say you could not check the web."

_QUERY_CAP = 120
_JSON_HEAD = '{"name"'


def web_search_request(data: dict) -> bool:
    """True forces a lookup before the first decode.

    False, or a missing field, lets the model call web_search.
    Anything that is not a JSON boolean is rejected.
    """
    if "web_search" not in data:
        return False
    value = data.pop("web_search")
    if isinstance(value, bool):
        return value
    raise ValueError("web_search must be true or false.")


def pending_tool_text(text: str) -> bool:
    """True while streamed text can still be a web_search tool call.

    A normal answer releases as soon as its first character is known.
    """
    stripped = (text or "").lstrip()
    if not stripped:
        return True
    if "<tool_call>".startswith(stripped) or stripped.startswith("<tool_call>"):
        return True
    compact = re.sub(r"\s+", "", stripped)
    return _JSON_HEAD.startswith(compact) or compact.startswith(_JSON_HEAD)


def _clip_query(text: str) -> str:
    cleaned = " ".join(neutralize(text or "").split())
    if not cleaned:
        return ""
    return clip_words(cleaned, _QUERY_CAP)


def _query_from_arguments(arguments) -> str | None:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    if not isinstance(arguments, dict) or "query" not in arguments:
        return None
    return str(arguments.get("query") or "")


def _query_from_object(obj) -> str | None:
    if not isinstance(obj, dict):
        return None
    if str(obj.get("name") or "") != "web_search":
        return None
    found = _query_from_arguments(obj.get("arguments"))
    if found is not None:
        return found
    if "query" in obj:
        return str(obj.get("query") or "")
    return ""


def _load_object(text: str):
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None


def _from_tool_calls(message: dict) -> str | None:
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not isinstance(calls, list):
        return None
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function")
        if not isinstance(function, dict):
            function = call
        if str(function.get("name") or "") != "web_search":
            continue
        found = _query_from_arguments(function.get("arguments"))
        return "" if found is None else found
    return None


def _from_text(content: str) -> str | None:
    text = (content or "").strip()
    if not text:
        return None
    if text.startswith("<tool_call>"):
        inner = text[len("<tool_call>") :]
        end = inner.find("</tool_call>")
        if end >= 0:
            inner = inner[:end]
        return _query_from_object(_load_object(inner))
    if text.startswith("{"):
        return _query_from_object(_load_object(text))
    return None


def tool_query(message: dict, content: str, prompt: str = "") -> str | None:
    """The web_search query, or None when this reply is not a tool call.

    Ollama's tool_calls field wins. Text that starts with ``<tool_call>`` or
    a bare ``{"name": "web_search", ...}`` object is the same call when
    Ollama did not parse it. The query is neutralized and clipped. An empty
    query uses the user's line.
    """
    raw = _from_tool_calls(message if isinstance(message, dict) else {})
    if raw is None:
        raw = _from_text(content)
    if raw is None:
        return None
    cleaned = _clip_query(raw)
    if cleaned:
        return cleaned
    return _clip_query(user_question(prompt))


def _result_body(note) -> str:
    if note == SEARCH_OFF:
        return SEARCH_OFF
    if isinstance(note, str):
        text = note.strip()
        if text == SEARCH_OFF:
            return SEARCH_OFF
        if text:
            return text + "\n" + RESULT_RULE
        return NO_RESULT
    if isinstance(note, dict):
        if note.get("status") == "off":
            return SEARCH_OFF
        text = str(note.get("prompt_note") or "").strip()
        if note.get("status") == "ok" and text:
            return text + "\n" + RESULT_RULE
    return NO_RESULT


def tool_round(messages, query, note) -> list:
    """The answer messages plus one assistant tool call and its result."""
    rows = [row for row in list(messages or []) if isinstance(row, dict)]
    q = " ".join(str(query or "").split())
    rows.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "web_search", "arguments": {"query": q}}}
            ],
        }
    )
    rows.append({"role": "tool", "content": _result_body(note)})
    return rows
