"""Plot, table, and diagram asks skip web search and get a structure hint.

A simple parabola is drawn here so the reply is a chart fence even when the
small model would talk about Desmos. Other plots stay on the model.
"""

from __future__ import annotations

import json
import math
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

CHART_FALLBACK = "I could not draw that chart."
CHART_NUDGE = (
    "That chart fence was not strict JSON. Reply again with one ```chart fence only. "
    '{"title":"Title","data":[{"type":"bar","x":["a","b"],"y":[1,2]}]}. '
    "type is bar, scatter, line, or pie. "
    "Each series needs y or values as a non-empty array of finite numbers. "
    "No trailing commas and no ```json fence."
)
_CHART_TYPES = frozenset({"bar", "scatter", "line", "pie"})
_CHART_LANG = frozenset({"", "chart", "plotly", "json"})
_FENCE = re.compile(r"```([^\n`]*)\n([\s\S]*?)```")
_LOOSE_FENCE = re.compile(r"```([^\n`]*)\n?([\s\S]*?)```")

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


def _finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _points(value: object) -> bool:
    if not isinstance(value, list) or not 1 <= len(value) <= 240:
        return False
    return all(_finite(item) for item in value)


def _labels(value: object, count: int) -> bool:
    if not isinstance(value, list) or len(value) != count:
        return False
    for item in value:
        if isinstance(item, bool) or isinstance(item, (int, float)):
            if not _finite(item):
                return False
            continue
        if not isinstance(item, str):
            return False
        text = item.strip()
        if not text or len(text) > 80 or "<" in text or ">" in text:
            return False
    return True


def chart_json_ok(raw: str) -> bool:
    """True when one fence body is a bar, scatter, line, or pie spec.

    Required: a data array, and each series needs type plus y or values.
    """
    try:
        data = json.loads((raw or "").strip())
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    rows = data.get("data")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 6:
        return False
    for item in rows:
        if not _series_ok(item):
            return False
    return True


def _series_ok(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    kind = item.get("type")
    if kind not in _CHART_TYPES:
        return False
    y = item.get("y")
    values = item.get("values")
    if y is None and values is None:
        return False
    if y is not None and not _points(y):
        return False
    if values is not None and not _points(values):
        return False
    if isinstance(y, list) and isinstance(values, list) and list(y) != list(values):
        return False
    points = y if isinstance(y, list) else values
    if not isinstance(points, list):
        return False
    labels = item.get("x")
    if labels is None and "labels" in item:
        labels = item.get("labels")
    if labels is not None and not _labels(labels, len(points)):
        return False
    return True


def _chart_attempts(text: str, prompt: str) -> list[str]:
    """Bodies of chart fences. ```json counts when the user asked for a plot."""
    raw = (text or "").replace("\r\n", "\n")
    want_json = is_chart_request(prompt)
    bodies: list[str] = []
    for match in _FENCE.finditer(raw):
        lang = (match.group(1) or "").strip().lower().split()
        name = lang[0] if lang else ""
        if name in {"chart", "plotly"} or (name == "json" and want_json):
            bodies.append(match.group(2) or "")
    if bodies:
        return bodies
    lowered = raw.lower()
    if raw.count("```") % 2 == 1 and (
        "```chart" in lowered or "```plotly" in lowered or (want_json and "```json" in lowered)
    ):
        return ["{"]
    return []


def repair_chart_reply(text: str, retry, prompt: str = "") -> str:
    """Keep a valid chart fence. One retry, then a single sentence.

    `retry` is called at most once and should return the next model reply.
    """
    attempts = _chart_attempts(text, prompt)
    if not attempts:
        return text or ""
    if all(chart_json_ok(body) for body in attempts):
        return text
    second = ""
    try:
        second = retry() or ""
    except Exception:
        second = ""
    again = _chart_attempts(second, prompt)
    if again and all(chart_json_ok(body) for body in again):
        return second
    return CHART_FALLBACK


def _chart_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    rounded = round(float(value), 4)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def _chart_spec(raw: str) -> dict | None:
    """One chart object, or None when the text is not the chart schema."""
    text = (raw or "").strip()
    text = re.sub(r"^json\s*", "", text, count=1, flags=re.I).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    blob = re.sub(r",(\s*[}\]])", r"\1", text[start : end + 1])
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return None
    rows = data["data"]
    if not rows or len(rows) > 6:
        return None
    cleaned: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            return None
        kind = str(item.get("type") or "").strip().lower()
        if kind not in _CHART_TYPES:
            return None
        points_raw = item.get("y")
        if points_raw is None:
            points_raw = item.get("values")
        if not isinstance(points_raw, list) or not points_raw or len(points_raw) > 240:
            return None
        points = []
        for value in points_raw:
            number = _chart_number(value)
            if number is None:
                return None
            points.append(number)
        row: dict = {"type": kind, "y": points}
        labels = item.get("x")
        if labels is None:
            labels = item.get("labels")
        if isinstance(labels, list) and len(labels) == len(points):
            kept = []
            for label in labels:
                if isinstance(label, bool) or not isinstance(label, (int, float, str)):
                    return None
                if isinstance(label, str):
                    label = " ".join(label.split())
                    if not label or len(label) > 80 or "<" in label or ">" in label:
                        return None
                elif isinstance(label, float):
                    label = _chart_number(label)
                kept.append(label)
            row["x"] = kept
        name = item.get("name")
        if isinstance(name, str):
            name = " ".join(name.split())
            if name and len(name) <= 80 and "<" not in name and ">" not in name:
                row["name"] = name
        mode = str(item.get("mode") or "").strip()
        if kind == "scatter" and mode in {"lines", "markers", "lines+markers"}:
            row["mode"] = mode
        cleaned.append(row)
    spec: dict = {"data": cleaned}
    title = data.get("title")
    if isinstance(title, dict):
        title = title.get("text")
    if isinstance(title, str):
        title = " ".join(title.split())
        if title and len(title) <= 120 and "<" not in title and ">" not in title:
            spec["title"] = title
    return spec


def _chart_fence(spec: dict) -> str:
    body = json.dumps(spec, separators=(",", ":"))
    return "```chart\n" + body + "\n```"


def normalize_chart_reply(text: str) -> str:
    """Rewrite a chart-shaped fence as one compact ```chart block.

    Other fences stay as written. A strict ```chart fence stays as written so
    repair_chart_reply can keep it. A reply that is only a chart object is wrapped.
    A fence this cannot read is left for that retry.
    """
    raw = text or ""
    if "```" not in raw:
        spec = _chart_spec(raw) if raw.strip().startswith("{") else None
        return _chart_fence(spec) if spec else raw

    def repl(match: re.Match) -> str:
        lang = (match.group(1) or "").strip().lower().split()
        name = lang[0] if lang else ""
        if name not in _CHART_LANG:
            return match.group(0)
        body = match.group(2) or ""
        if name == "chart" and chart_json_ok(body):
            return match.group(0)
        spec = _chart_spec(body)
        if spec is None:
            return match.group(0)
        return _chart_fence(spec)

    return _LOOSE_FENCE.sub(repl, raw)


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
