"""Fit an attached document into the pi4 context window.

The upload may hold up to MAX_TEXT_CHARS. The model sees one window scored
against the question. That window is sized from num_ctx after the question,
the document hint, earlier turns, and the reply have a reserved share.
"""

from __future__ import annotations

import re

from pair.ingest.upload import MAX_TEXT_CHARS

# Four characters per token keeps typical OCR inside this model's window.
CHARS_PER_TOKEN = 4
# Role tags and the chat template around the excerpt.
TEMPLATE_TOKENS = 32
# A crowded thread still keeps a short paragraph of the document.
MIN_EXCERPT_CHARS = 240

DOC_MARK = "\n---\n"

DOC_HINT = (
    "The user attached a document. After a --- line is an excerpt that fits the context window. "
    "Answer their question from that excerpt. Do not paste the excerpt back and do not dump the document."
)

_STOP = {
    "that",
    "this",
    "with",
    "from",
    "what",
    "when",
    "where",
    "which",
    "about",
    "would",
    "could",
    "should",
    "there",
    "their",
    "have",
    "been",
    "were",
    "your",
    "please",
    "document",
    "attached",
}


def _tokens(text: str) -> int:
    size = len(text or "")
    if size <= 0:
        return 0
    return (size + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def excerpt_limit(
    num_ctx: int = 2048, reserved: str = "", reply_tokens: int = 256
) -> int:
    """Characters of document text that still leave the thread and reply in num_ctx."""
    try:
        ctx = int(num_ctx)
    except (TypeError, ValueError):
        ctx = 2048
    if ctx < 256:
        ctx = 256
    try:
        reply = int(reply_tokens)
    except (TypeError, ValueError):
        reply = 256
    if reply < 64:
        reply = 64
    held = _tokens(reserved) + _tokens(DOC_HINT) + reply + TEMPLATE_TOKENS
    room = ctx - held
    floor = MIN_EXCERPT_CHARS // CHARS_PER_TOKEN
    if room < floor:
        room = floor
    return min(MAX_TEXT_CHARS, room * CHARS_PER_TOKEN)


# Default window: num_ctx 2048, no earlier turns, a medium reply.
DOC_FIT_CHARS = excerpt_limit(2048)


def _terms(text: str) -> list[str]:
    seen: list[str] = []
    for word in re.findall(r"[a-z0-9]{4,}", (text or "").lower()):
        if word in _STOP or word in seen:
            continue
        seen.append(word)
    return seen


def fit_document(text: str, question: str, limit: int = DOC_FIT_CHARS) -> str:
    """One window of `text`. A short document is returned whole."""
    body = " ".join((text or "").split())
    if limit < 1 or len(body) <= limit:
        return body
    step = max(200, limit // 2)
    windows: list[str] = []
    start = 0
    while start < len(body):
        windows.append(body[start : start + limit])
        if start + limit >= len(body):
            break
        start += step
    terms = _terms(question)
    if not terms:
        return windows[0]

    def score(window: str) -> int:
        low = window.lower()
        return sum(1 for term in terms if term in low)

    return max(windows, key=score)


def fit_user_text(content: str, limit: int = DOC_FIT_CHARS) -> tuple[str, bool]:
    """Replace a --- document block with a fitted excerpt. The bool says it changed."""
    if DOC_MARK not in (content or ""):
        return content, False
    question, document = content.split(DOC_MARK, 1)
    fitted = fit_document(document, question, limit)
    question = question.strip()
    if question:
        return question + "\n\n---\n" + fitted, True
    return fitted, True


def _thread_reserve(messages: list) -> tuple[str, int]:
    """Non-document text already in the thread, and how many documents it holds."""
    parts: list[str] = []
    docs = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if message.get("role") == "user" and DOC_MARK in content:
            question, _document = content.split(DOC_MARK, 1)
            docs += 1
            if question.strip():
                parts.append(question)
            continue
        parts.append(content)
    return "\n".join(parts), docs


def fit_outbound(messages: list, num_ctx: int = 2048, reply_tokens: int = 256) -> list:
    """Trim document blocks and add one system hint when a document was attached."""
    reserved, docs = _thread_reserve(messages)
    limit = excerpt_limit(num_ctx, reserved, reply_tokens)
    if docs > 1:
        limit = max(MIN_EXCERPT_CHARS, limit // docs)
    changed = False
    outbound = []
    for message in messages:
        if not isinstance(message, dict):
            outbound.append(message)
            continue
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, str):
            fitted, did = fit_user_text(content, limit)
            if did:
                changed = True
                outbound.append({**message, "content": fitted})
                continue
        outbound.append(message)
    if not changed:
        return outbound
    if any(
        isinstance(item, dict)
        and item.get("role") == "system"
        and item.get("content") == DOC_HINT
        for item in outbound
    ):
        return outbound
    return [{"role": "system", "content": DOC_HINT}, *outbound]
