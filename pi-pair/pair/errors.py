"""Plain sentences for people. The raw cause is logged, not shown."""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("pi-pair.errors")
log.addHandler(logging.NullHandler())

BUSY = "Too many chats are going at once. Try again in a moment."
WAITING = "Waiting for a free slot…"
ASK_FIRST = "Ask a question first."


def queue_status(position: int) -> str:
    """The line the page shows while a chat waits for a generation slot."""
    slot = max(1, int(position))
    return f"Waiting for a free slot (#{slot})"


GENERIC = "Something went wrong. Try again."
TIMEOUT = "That took too long. Try again."
UNREACHABLE = "The chat service is not reachable. Try again."
NO_CHAT = "That machine cannot answer chats."
MODEL_MISSING = "The larger model is not ready yet."
FLASH_WARMING = "The model is warming up. Ask again in a moment."
BAD_MESSAGE = "That message could not be read. Try again."
FILE_TYPE = "That file type is not supported."
FILE_SEND = "That file could not be sent. Try again."
FILE_EMPTY = "That file is empty."
OCR_MISSING = "This device cannot read pictures or scanned pages."
OCR_BUSY = "Reading a file is busy. Try again in a moment."
PDF_SCAN = "That PDF could not be read. Try a text file or a photo."
SEARCH_WHERE = "Search is not available from here."
NOT_HERE = "That is not available from here."
TOO_BIG = "That is too big to send. Try a shorter message."

_BANNED = re.compile(
    r"\b(pi[234]|ollama|traceback|generations in flight)\b|\bHTTP\b|\{|\[",
    re.I,
)
_STATUS = re.compile(r"\b[45]\d\d\b")


def friendly_error(raw: str) -> str:
    """One short line. Never empty, and never a board name, trace, or status code."""
    text = " ".join(str(raw or "").split())
    shown = _map(text)
    log.warning("user failure: %s -> %s", text or "(empty)", shown)
    return shown


def friendly_body(raw: bytes) -> bytes:
    """Rewrite an error body so a relay cannot forward a trace or a board name."""
    try:
        parsed = json.loads(raw.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
        parsed["error"] = friendly_error(parsed["error"])
        return json.dumps(parsed).encode()
    return json.dumps({"error": GENERIC}).encode()


def _map(text: str) -> str:
    if not text or text.lower().startswith("traceback") or text[0] in "{[":
        return GENERIC
    if len(text) > 240:
        return GENERIC
    low = text.lower()
    if "at capacity" in low or "generations in flight" in low:
        return BUSY
    if "took too long" in low or "timed out" in low or "timeout" in low:
        return TIMEOUT
    if "too long" in low or "too large" in low or "over 4 mb" in low or "413" in low:
        return TOO_BIG
    if "jpeg-scanned" in low or "no readable text" in low:
        return PDF_SCAN
    if "not installed" in low or "this pi" in low:
        return OCR_MISSING
    if "ocr is busy" in low:
        return OCR_BUSY
    if "unsupported file" in low:
        return FILE_TYPE
    if "attachment is empty" in low or low == "that file is empty.":
        return FILE_EMPTY
    if "multipart" in low or "content-length" in low or "must be a file" in low:
        return FILE_SEND
    if (
        "unreachable" in low
        or "offline" in low
        or "connection refused" in low
        or "no usable model" in low
        or low.startswith("unknown peer")
    ):
        return UNREACHABLE
    if "cannot be the brain" in low or "does not run a chat model" in low:
        return NO_CHAT
    if "does not pull" in low or "ollama pull" in low or "not on pi" in low:
        return MODEL_MISSING
    if "health host" in low:
        return SEARCH_WHERE
    if "dataset host" in low or "train queue" in low:
        return NOT_HERE
    if (
        "must be json" in low
        or "must be an object" in low
        or "needs a user message" in low
        or "mode must be" in low
        or "unknown mode" in low
    ):
        return BAD_MESSAGE
    if _STATUS.search(text) or _BANNED.search(text):
        return GENERIC
    return text
