"""Fail closed: pi2 and pi3 never generate."""

from __future__ import annotations

WEAK_NAMES = frozenset({"pi2", "pi3"})

PI4_MISS_DOWN = "pi4 unreachable on cache miss. Refusing to answer from pi2 or pi3."


def weak_brain_error(name: str) -> str:
    return (
        f"{name} cannot be the brain. Generative inference runs only on pi4. "
        "This peer does not run a chat model."
    )


def may_generate(peer: dict) -> bool:
    name = str(peer.get("name") or "")
    if name in WEAK_NAMES:
        return False
    if peer.get("role") in ("health", "dataset"):
        return False
    if "generative" in peer:
        return bool(peer["generative"])
    return name == "pi4"


def require_generative(peer: dict) -> None:
    if not may_generate(peer):
        raise RuntimeError(weak_brain_error(str(peer.get("name") or "peer")))
