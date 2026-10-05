"""Flash and Arctic stay resident. Pro is an extra load, not a swap.

OLLAMA_MAX_LOADED_MODELS is at least 3, so Flash, Arctic, and an opted-in Pro
fit together. This module does not call Ollama. A keep_alive of 0 for a tag
that is not loaded can make Ollama fetch it, and a Flash request must not
name Pro.
"""
from __future__ import annotations

import re

from pair.embed import EMBED_MODEL
from pair.modes import FLASH, mode_table

MIN_LOADED_MODELS = 3
_CAP = re.compile(r"OLLAMA_MAX_LOADED_MODELS=(\d+)")


def protected_tags(knobs: dict | None = None) -> tuple[str, str]:
    """Tags a mode switch must not unload: Flash, then the map embedder."""
    table = mode_table(knobs)
    return table[FLASH], EMBED_MODEL


def eviction_targets(running: list[str] | None, requested: str, knobs: dict | None = None) -> list[str]:
    """Names this process would unload before `requested` runs.

    Always empty. Flash and Arctic are never eligible, and switching back to
    Flash does not evict Pro. Ollama keeps all three while the loaded-model
    cap is at least MIN_LOADED_MODELS.
    """
    del running, requested, knobs
    return []


def loaded_caps(text: str) -> list[int]:
    return [int(match) for match in _CAP.findall(text or "")]


def cap_fits_three(text: str) -> bool:
    found = loaded_caps(text)
    return bool(found) and all(value >= MIN_LOADED_MODELS for value in found)
