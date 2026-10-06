"""Turn a markdown table or a plot fence into a Plotly figure.

The shape of the table picks the chart. A date or an ordered first column
is a line. Two unordered number columns are a scatter. One text column plus
numbers is a bar. Optional `type` and `title` lines override that. There is
no JSON chart spec. Nothing here generates text.
"""

from __future__ import annotations

import re

from pair.calc import evaluate_expr

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_KV = re.compile(r"^(type|title)\s*:\s*(\S.*?)\s*$", re.I)
_KINDS = {"bar", "line", "scatter"}
_POINTS = 81


def _split(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [part.strip() for part in text.split("|")]


def _is_date(cell: str) -> bool:
    return bool(_DATE.fullmatch((cell or "").strip()))


def _is_number(cell: str) -> bool:
    text = (cell or "").strip().replace(",", "")
    if not text or _is_date(text):
        return False
    return bool(_NUMBER.fullmatch(text))


def _float(cell: str) -> float:
    return float(cell.strip().replace(",", ""))


def parse_markdown_table(text: str) -> tuple[list[str], list[list[str]]] | None:
    """The first markdown table in `text`, or None."""
    lines = (text or "").splitlines()
    for index, line in enumerate(lines[:-1]):
        if "|" not in line or not _RULE.match(lines[index + 1]):
            continue
        headers = _split(line)
        if len(headers) < 2 or any(not item for item in headers):
            continue
        rows: list[list[str]] = []
        for raw in lines[index + 2 :]:
            if not raw.strip() or "|" not in raw:
                break
            cells = _split(raw)
            if len(cells) < len(headers):
                cells = cells + [""] * (len(headers) - len(cells))
            rows.append(cells[: len(headers)])
        if rows:
            return headers, rows
    return None


def _column_kind(cells: list[str]) -> str:
    filled = [cell for cell in cells if cell]
    if not filled:
        return "empty"
    if all(_is_date(cell) for cell in filled):
        return "date"
    if all(_is_number(cell) for cell in filled):
        return "number"
    return "text"


def _ordered(cells: list[str]) -> bool:
    if len(cells) < 2 or any(not _is_number(cell) for cell in cells):
        return False
    nums = [_float(cell) for cell in cells]
    return nums == sorted(nums) or nums == sorted(nums, reverse=True)


def chart_type(headers: list[str], rows: list[list[str]]) -> str:
    """bar, line, scatter, or an empty string when the shape is not a chart."""
    if len(headers) < 2 or not rows:
        return ""
    kinds = [
        _column_kind([row[index].strip() for row in rows])
        for index in range(len(headers))
    ]
    first = [row[0].strip() for row in rows]
    if kinds[0] == "date" or (kinds[0] == "number" and _ordered(first)):
        if any(kind == "number" for kind in kinds[1:]):
            return "line"
    if (
        kinds[0] == "number"
        and kinds.count("number") >= 2
        and "text" not in kinds
        and "date" not in kinds
    ):
        return "scatter"
    if any(kind == "number" for kind in kinds) and "text" in kinds:
        return "bar"
    if any(kind == "number" for kind in kinds[1:]):
        return "bar"
    return ""


def key_values(text: str) -> dict[str, str]:
    """`type` and `title` lines. Other lines are data, not settings."""
    found: dict[str, str] = {}
    for line in (text or "").splitlines():
        match = _KV.match(line.strip())
        if match:
            found[match.group(1).lower()] = match.group(2).strip()
    return found


def _expression(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or _KV.match(stripped) or stripped.startswith("|"):
            continue
        if _RULE.match(stripped):
            continue
        return stripped
    return ""


def _series(kind: str, headers: list[str], rows: list[list[str]]) -> list[dict]:
    labels = [row[0] for row in rows]
    if kind == "scatter":
        return [
            {
                "type": "scatter",
                "mode": "markers",
                "name": headers[1],
                "x": [_float(row[0]) for row in rows],
                "y": [_float(row[1]) for row in rows],
            }
        ]
    mode = "lines" if kind == "line" else ""
    data = []
    for index in range(1, len(headers)):
        cells = [row[index] for row in rows]
        if not cells or any(not _is_number(cell) for cell in cells if cell.strip()):
            continue
        if any(not cell.strip() for cell in cells):
            continue
        series = {
            "name": headers[index],
            "x": labels,
            "y": [_float(cell) for cell in cells],
        }
        if kind == "line":
            series["type"] = "scatter"
            series["mode"] = mode
        else:
            series["type"] = "bar"
        data.append(series)
    return data


def _figure(kind: str, data: list[dict], title: str) -> dict | None:
    if kind not in _KINDS or not data:
        return None
    layout: dict = {"margin": {"t": 48, "r": 16, "b": 48, "l": 48}}
    if title:
        layout["title"] = title
    return {"data": data, "layout": layout}


def figure_from_table(text: str, title: str = "", kind: str = "") -> dict | None:
    parsed = parse_markdown_table(text)
    if not parsed:
        return None
    headers, rows = parsed
    chosen = kind if kind in _KINDS else chart_type(headers, rows)
    if not chosen:
        return None
    try:
        data = _series(chosen, headers, rows)
    except ValueError:
        return None
    return _figure(chosen, data, title)


def figure_from_plot(text: str, title: str = "") -> dict | None:
    expr = _expression(text)
    if not expr or parse_markdown_table(text):
        return None
    xs: list[float] = []
    ys: list[float] = []
    for index in range(_POINTS):
        x = -10 + (20 * index / (_POINTS - 1))
        shown = evaluate_expr(expr, {"x": x})
        if shown is None:
            continue
        xs.append(round(x, 6))
        ys.append(float(shown))
    if len(xs) < 2:
        return None
    return _figure(
        "line",
        [{"type": "scatter", "mode": "lines", "name": expr, "x": xs, "y": ys}],
        title,
    )


def chart_samples(n: int = 50) -> list[dict]:
    """Generic table shapes. The last four are not tables, so they stay text."""
    rows = []
    for index in range(n):
        if index >= n - 4:
            rows.append({"table": f"notes {index}", "expect": ""})
            continue
        kind = index % 4
        if kind == 0:
            table = "| item | value |\n| --- | --- |\n| a | 1 |\n| b | 3 |\n"
            expect = "bar"
        elif kind == 1:
            table = (
                "| day | value |\n| --- | --- |\n"
                "| 2024-01-01 | 1 |\n| 2024-01-02 | 2 |\n"
            )
            expect = "line"
        elif kind == 2:
            table = "| x | y |\n| --- | --- |\n| 1 | 4 |\n| 2 | 1 |\n"
            expect = "line"
        else:
            table = "| x | y |\n| --- | --- |\n| 1 | 2 |\n| 3 | 1 |\n| 2 | 4 |\n"
            expect = "scatter"
        rows.append({"table": table, "expect": expect})
    return rows


def render_chart(table: str = "", plot: str = "") -> dict:
    """A figure plus the original table text, so a failed chart still shows the table."""
    source = plot or table or ""
    settings = key_values(source)
    title = settings.get("title") or ""
    forced = settings.get("type") or ""
    if forced not in _KINDS:
        forced = ""
    parsed = parse_markdown_table(source)
    if parsed:
        headers, rows = parsed
        kind = forced or chart_type(headers, rows)
        figure = figure_from_table(source, title, kind)
        return {
            "ok": figure is not None,
            "type": kind if figure else "",
            "figure": figure,
            "table": source,
        }
    if plot or _expression(source):
        figure = figure_from_plot(source, title)
        return {
            "ok": figure is not None,
            "type": "line" if figure else "",
            "figure": figure,
            "table": source,
        }
    return {"ok": False, "type": "", "figure": None, "table": source}
