"""Flash and Pro both stay resident. Neither tag is unloaded for the other.

OLLAMA_MAX_LOADED_MODELS is at least 2, so both tags fit together. This
module does not call Ollama. A keep_alive of 0 can make Ollama drop a tag,
and a chat does not send that.
"""

from __future__ import annotations

import re

from pair.modes import FLASH, PRO, mode_table

MIN_LOADED_MODELS = 2
_CAP = re.compile(r"OLLAMA_MAX_LOADED_MODELS=(\d+)")


def protected_tags(knobs: dict | None = None) -> tuple[str, ...]:
    """Tags a mode switch must not unload. Flash and Pro."""
    table = mode_table(knobs)
    return (table[FLASH], table[PRO])


def eviction_targets(
    running: list[str] | None, requested: str, knobs: dict | None = None
) -> list[str]:
    """Names this process would unload before `requested` runs.

    Always empty. Flash and Pro are both protected. Ollama keeps both while
    the loaded-model cap is at least MIN_LOADED_MODELS.
    """
    del running, requested, knobs
    return []


def loaded_caps(text: str) -> list[int]:
    return [int(match) for match in _CAP.findall(text or "")]


def cap_fits_residents(text: str) -> bool:
    found = loaded_caps(text)
    return bool(found) and all(value >= MIN_LOADED_MODELS for value in found)
