"""Joined page source, so a moved function keeps its text assertion."""

from __future__ import annotations

from tests.support.paths import ROOT


def web_source() -> str:
    root = ROOT / "web" / "src"
    parts = [path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.ts"))]
    return "\n".join(parts)
