"""Plot, table, and diagram asks skip web search and get a structure hint.

A simple parabola is drawn here so the reply is a chart fence even when the
small model would talk about Desmos. Other plots stay on the model.
"""

from __future__ import annotations

import json
import re

CHART_HINT = (
    "The user wants a plot. Reply with one ```chart fence and at most one short sentence. "
    'JSON shape: {"title":"y = x^2","data":[{"type":"scatter","mode":"lines",'
    '"name":"y = x^2","x":[-2,-1,0,1,2],"y":[4,1,0,1,4]}]}. '
    "type is bar, scatter, line, or pie. Include a non-empty y or values array of finite numbers. "
    "Do not mention Desmos or another site, and do not say you could not get the result."
)

TABLE_HINT = (
    "The user wants a table. Reply with one ```table fence and at most one short sentence. "
    'JSON shape: {"title":"Comparison","columns":["Name","Value"],"rows":[["A","1"],["B","2"]]}. '
    "Use the columns and rows from the question. Do not dump an unformatted list."
)

FLOW_HINT = (
    "The user wants a flowchart or diagram. Reply with one ```mermaid fence and at most one short sentence. "
    "Use flowchart TD. Example:\n```mermaid\nflowchart TD\n"
    "A[Start] --> B{Choice}\nB -->|yes| C[Done]\nB -->|no| D[Stop]\n```\n"
    "Do not send the user to another site."
)

_CHART = re.compile(
    r"\b(?:plot|graph|chart)\b|"
    r"\b(?:parabola|parabolic)\b|"
    r"\b(?:draw|sketch|show)\b.{0,40}\b(?:curve|parabola|graph|chart)\b",
    re.I,
)
_TABLE = re.compile(
    r"\b(?:table|spreadsheet|columns?)\b|"
    r"\bcompare\b.{0,48}\b(?:side by side|in a table|as a table)\b",
    re.I,
)
_FLOW = re.compile(
    r"\b(?:flowchart|flow chart|diagram|sequence diagram)\b|"
    r"\b(?:draw|sketch|show)\b.{0,40}\b(?:flow|diagram|steps)\b",
    re.I,
)
_PARABOLA = re.compile(
    r"\b(?:parabola|parabolic)\b|"
    r"y\s*=\s*[^.\n]{0,48}x\s*(?:\^|\*\*)\s*2|"
    r"y\s*=\s*[^.\n]{0,24}x²",
    re.I,
)
_EQ = re.compile(
    r"y\s*=\s*([+-]?\s*\d*\.?\d*)\s*\*?\s*x\s*(?:\^|\*\*)\s*2"
    r"(?:\s*([+-])\s*(\d*\.?\d*)\s*\*?\s*x)?"
    r"(?:\s*([+-])\s*(\d+\.?\d*))?",
    re.I,
)


def is_chart_request(prompt: str) -> bool:
    return bool(_CHART.search(prompt or ""))


def is_table_request(prompt: str) -> bool:
    return bool(_TABLE.search(prompt or ""))


def is_flow_request(prompt: str) -> bool:
    return bool(_FLOW.search(prompt or ""))


def is_structured_request(prompt: str) -> bool:
    """A plot, table, or diagram. These skip grounded web search."""
    return is_chart_request(prompt) or is_table_request(prompt) or is_flow_request(prompt)


def structure_hint(prompt: str) -> str | None:
    """One short system hint. A parabola that we can draw needs none."""
    if parabola_chart(prompt):
        return None
    if is_chart_request(prompt):
        return CHART_HINT
    if is_table_request(prompt):
        return TABLE_HINT
    if is_flow_request(prompt):
        return FLOW_HINT
    return None


def _coeff(raw: str | None, default: float) -> float:
    text = (raw or "").replace(" ", "")
    if text in {"", "+", "-"}:
        return default if text != "-" else -default
    return float(text)


def _parabola_coeffs(prompt: str) -> tuple[float, float, float] | None:
    folded = (prompt or "").replace("²", "^2")
    match = _EQ.search(folded)
    if match:
        leading = _coeff(match.group(1), 1.0)
        linear = 0.0
        if match.group(2):
            sign = -1.0 if match.group(2).strip() == "-" else 1.0
            linear = sign * _coeff(match.group(3), 1.0)
        constant = 0.0
        if match.group(4):
            sign = -1.0 if match.group(4).strip() == "-" else 1.0
            constant = sign * float(match.group(5))
        return leading, linear, constant
    if _PARABOLA.search(prompt or ""):
        return 1.0, 0.0, 0.0
    return None


def _point(value: float) -> int | float:
    rounded = round(value, 4)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def _format_coeff(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)


def _parabola_title(a: float, b: float, c: float) -> str:
    parts = [f"{_format_coeff(a)}x²" if a != 1 else "x²"]
    if a == -1:
        parts = ["-x²"]
    elif a != 1:
        parts = [f"{_format_coeff(a)}x²"]
    if b:
        sign = "+" if b > 0 else "-"
        mag = _format_coeff(abs(b))
        parts.append(f" {sign} {mag if mag != '1' else ''}x".replace("  ", " "))
    if c:
        sign = "+" if c > 0 else "-"
        parts.append(f" {sign} {_format_coeff(abs(c))}")
    body = "".join(parts).replace("x²", "x^2")
    title = "y = " + body.replace("x^2", "x²")
    return title[:120]


def parabola_chart(prompt: str) -> str | None:
    """A ```chart fence for a simple parabola, or None when the model should try."""
    if not is_chart_request(prompt):
        return None
    coeffs = _parabola_coeffs(prompt)
    if coeffs is None:
        return None
    a, b, c = coeffs
    xs = list(range(-5, 6))
    ys = [_point(a * x * x + b * x + c) for x in xs]
    title = _parabola_title(a, b, c)
    spec = {
        "title": title,
        "data": [
            {
                "type": "scatter",
                "mode": "lines",
                "name": title[:80],
                "x": xs,
                "y": ys,
            }
        ],
    }
    return "```chart\n" + json.dumps(spec, separators=(",", ":")) + "\n```"
