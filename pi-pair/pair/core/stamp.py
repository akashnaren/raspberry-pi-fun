"""The commit this process started with. Stdlib only."""

from __future__ import annotations

import re
from pathlib import Path

from pair.core.config import ROOT

_HEX = re.compile(r"^[0-9a-f]{7,40}$")


def parse_commit(text: str) -> str:
    if text.endswith("\n"):
        text = text[:-1]
    match = _HEX.fullmatch(text)
    if match is None:
        return "unknown"
    return match.group(0)[:12]


def read_commit(directory: Path) -> str:
    path = directory / "DEPLOYED_SHA"
    try:
        with path.open("rb") as handle:
            raw = handle.read(64)
    except OSError:
        return "unknown"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "unknown"
    return parse_commit(text)


_DEPLOYED = read_commit(ROOT)


def deployed_commit() -> str:
    return _DEPLOYED
