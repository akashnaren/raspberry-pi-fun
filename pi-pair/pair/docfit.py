"""Fit an attached document into the pi4 context window.

The upload may hold up to MAX_TEXT_CHARS. The model sees one window scored
against the question, not the whole OCR dump.
"""

from __future__ import annotations

import re

DOC_FIT_CHARS = 1400
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


def fit_outbound(messages: list) -> list:
    """Trim document blocks and add one system hint when a document was attached."""
    changed = False
    outbound = []
    for message in messages:
        if not isinstance(message, dict):
            outbound.append(message)
            continue
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, str):
            fitted, did = fit_user_text(content)
            if did:
                changed = True
                outbound.append({**message, "content": fitted})
                continue
        outbound.append(message)
    if not changed:
        return outbound
    if any(isinstance(item, dict) and item.get("role") == "system" and item.get("content") == DOC_HINT for item in outbound):
        return outbound
    return [{"role": "system", "content": DOC_HINT}, *outbound]
