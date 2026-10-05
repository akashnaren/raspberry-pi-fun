"""Flash and Pro. An unspecified mode is always Flash.

Auto is a page choice. It still resolves to one of the two tags in mode_table.
"""
from __future__ import annotations

import re

from pair.ground import is_grounded_problem
from pair.knobs import inference_knobs

FLASH = "flash"
PRO = "pro"
AUTO = "auto"
FLASH_MODEL = "qwen2.5:0.5b"
PRO_MODEL = "qwen2.5:1.5b"

_CODE = re.compile(
    r"```|"
    r"(^|\n)\s*(?:def |class |function |import |from |const |let |var |fn |pub )|"
    r"\b(?:write|debug|implement|refactor)\b.{0,48}\b(?:function|script|program|class|code)\b|"
    r"\b(?:python|javascript|typescript)\b",
    re.I,
)
_SEARCH = re.compile(
    r"\b(?:search for|look up|lookup|latest news|news about|sources for|find articles|find sources)\b",
    re.I,
)
_MULTI = re.compile(r"step by step|multi-step|break it down|show your work", re.I)


def mode_table(knobs: dict | None = None) -> dict[str, str]:
    row = inference_knobs() if knobs is None else knobs
    flash = str(row.get("model") or FLASH_MODEL).strip() or FLASH_MODEL
    pro = str(row.get("pro_model") or PRO_MODEL).strip() or PRO_MODEL
    return {FLASH: flash, PRO: pro}


def resolve_mode(mode: str | None, model: object | None = None, knobs: dict | None = None) -> tuple[str, str]:
    """Return (mode, ollama tag). Only an explicit Pro choice leaves Flash."""
    table = mode_table(knobs)
    picked = str(mode or "").strip().lower()
    if picked == PRO:
        return PRO, table[PRO]
    if picked == FLASH:
        return FLASH, table[FLASH]
    raw = str(model or "").strip()
    if raw.lower() == PRO or raw.lower() == table[PRO].lower():
        return PRO, table[PRO]
    return FLASH, table[FLASH]


def task_tier(prompt: str) -> str:
    """flash for short chitchat. pro for length, math, code, multi-step, or search."""
    text = (prompt or "").strip()
    if not text:
        return FLASH
    if is_grounded_problem(text) or _CODE.search(text) or _SEARCH.search(text) or _MULTI.search(text):
        return PRO
    if len(text) >= 280 or len(text.split()) >= 48:
        return PRO
    return FLASH


def resolve_auto(prompt: str, available: list | None = None, knobs: dict | None = None) -> tuple[str, str, str]:
    """Return (route, tag, reason) for Auto. The tag is always from mode_table.

    A known model list that lacks the Pro tag stays on Flash. An unknown list
    still names Pro; the request path fails closed if that tag is not pulled.
    """
    table = mode_table(knobs)
    tier = task_tier(prompt)
    reason = "heuristic"
    known = [str(name).strip() for name in (available or []) if str(name or "").strip()]
    if tier == PRO and known and table[PRO] not in known:
        tier = FLASH
        reason = "pro-unavailable"
    return tier, table[tier], reason


def tag_ready(models: list, mode: str, model: str) -> bool:
    """Pro must be an exact pulled tag. Flash may proceed when tags are unknown."""
    names = [str(item).strip() for item in (models or []) if str(item or "").strip()]
    if mode == PRO:
        return model in names
    if not names:
        return True
    return model in names


def pull_needed(model: str) -> str:
    return (
        f"{model} is not on pi4. This router does not pull it. "
        f"On pi4, when you mean to: ollama pull {model}"
    )
