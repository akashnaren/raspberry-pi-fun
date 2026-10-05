"""Flash and Pro. An unspecified mode is always Flash."""
from __future__ import annotations

from pair.knobs import inference_knobs

FLASH = "flash"
PRO = "pro"
FLASH_MODEL = "qwen2.5:0.5b"
PRO_MODEL = "qwen2.5:1.5b"


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
