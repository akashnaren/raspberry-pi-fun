"""Ask the turn's model whether to search. Nothing here is cached.

The check reuses the answer prompt's prefix and the same context size, so
Ollama does not reload the weights. A caller skips it for a diagram, an
attachment, arithmetic the calculator can finish, or mesh off. An explicit
web_search flag also skips it and searches.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from pair.core.cancel import ClientGone
from pair.flywheel.miss_queue import node_role
from pair.mesh.health import snapshot_peers
from pair.mesh.offload import remote_search_enabled
from pair.model.chat_once import classify_once
from pair.model.think import peel_think
from pair.turn.calc import notes_for
from pair.turn.shape import (
    attachment_tail,
    is_structured_request,
    shape_messages,
    user_question,
)

DECIDE_PROMPT = (
    "Do not answer my last message yet. Should you search the web before answering it?\n"
    "SEARCH: a named person, company, product, place, or event; news or anything that "
    "changes over time; a fact you could get wrong that a web page can check; or I ask "
    "you to search, look something up, or use your tools or the web.\n"
    "SKIP: greetings and small talk, math, code, writing or rewriting, poems and stories, "
    "translation, general explanations, or questions about an attached file, this "
    "conversation, or something I told you.\n"
    "If unsure, SEARCH. Reply with one word: SEARCH or SKIP."
)

_WORD = re.compile(r"\W*([A-Za-z]+)")


@dataclass(frozen=True)
class Decision:
    search: bool
    source: str
    ms: int


def web_search_request(data: dict) -> bool:
    """True forces a lookup. False, or a missing field, leaves the model check.

    Anything that is not a JSON boolean is rejected.
    """
    if "web_search" not in data:
        return False
    value = data.pop("web_search")
    if isinstance(value, bool):
        return value
    raise ValueError("web_search must be true or false.")


def search_candidate(prompt: str, mesh: bool) -> bool:
    """True when this turn may ask the model whether to search.

    The rules are the shape of the request. Wording is the model's job.
    """
    if not mesh or node_role() != "brain":
        return False
    if is_structured_request(prompt):
        return False
    if attachment_tail(prompt):
        return False
    if notes_for(user_question(prompt)) is not None:
        return False
    return True


def decide_messages(
    outbound, prompt: str, knobs: dict, facts: str, summary: str
) -> list:
    """Answer prefix, then one question. The shorter context keeps that question."""
    fitted = {**knobs, "num_ctx": int(knobs["num_ctx"]) - 128}
    rows = shape_messages(
        outbound,
        prompt,
        fitted,
        effort="",
        notes="",
        hints="",
        facts=facts,
        summary=summary,
    )
    rows.append({"role": "user", "content": DECIDE_PROMPT})
    return rows


def parse_decision(text: str) -> bool | None:
    """True for SEARCH, False for SKIP, None when the word is neither.

    A cut at the three-token limit still counts when the stem is there.
    """
    visible, _reasoning = peel_think(text or "")
    match = _WORD.match(visible or "")
    if match is None:
        return None
    word = match.group(1).upper()
    if word.startswith("SEA"):
        return True
    if word.startswith("SK"):
        return False
    return None


def search_ready() -> bool:
    """Whether a fallback may search. Reads the health cache and does not probe."""
    if not remote_search_enabled():
        return True
    return any(
        isinstance(peer, dict) and peer.get("name") == "pi2" and bool(peer.get("ok"))
        for peer in snapshot_peers() or []
    )


def _timeout(knobs: dict) -> float:
    try:
        value = float(knobs.get("search_decide_timeout_s", 3.0))
    except (TypeError, ValueError):
        return 3.0
    if value <= 0:
        return 3.0
    return value


def _check(cancel) -> None:
    if cancel is not None:
        cancel.check()


def decide_search(
    peer,
    kind,
    model,
    outbound,
    prompt,
    knobs,
    facts,
    summary,
    cancel,
) -> Decision:
    """One short decode. A bad reply searches only when the lookup path is up."""
    started = time.perf_counter()

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    _check(cancel)
    messages = decide_messages(outbound, prompt, knobs, facts, summary)
    try:
        text = classify_once(peer, kind, model, messages, knobs, _timeout(knobs))
        _check(cancel)
        parsed = parse_decision(text)
    except ClientGone:
        raise
    except Exception:
        parsed = None
    if parsed is None:
        return Decision(search_ready(), "fallback", elapsed())
    return Decision(bool(parsed), "model", elapsed())
