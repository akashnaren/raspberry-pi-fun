"""Arithmetic notes from a standard-library ast whitelist.

Spans of numbers, operators, and parentheses are evaluated. "N% of M" is
rewritten to a product first. Other word operators stay out of scope. A bad
span is skipped, never raised.
"""

from __future__ import annotations

import ast
import math
import re

_MAX_EXPRESSIONS = 3
_MAX_OPERAND = 1e15
_MAX_EXPONENT = 10
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PHONE = re.compile(r"\d{2,4}(?:-\d{2,4})+")
_PERCENT_OF = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s+of\s+(\d+(?:\.\d+)?)",
    re.I,
)
_ALLOWED = set("0123456789+-*/%^×÷()., \t")
_OPS = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.FloorDiv: lambda left, right: left // right,
    ast.Mod: lambda left, right: left % right,
}
_CALLS = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "sqrt": math.sqrt,
    "abs": abs,
    "log": math.log,
    "exp": math.exp,
}
_CONST = {"pi": math.pi, "e": math.e}


def _spans(text: str) -> list[str]:
    found: list[str] = []
    buf: list[str] = []
    for char in text or "":
        if char in _ALLOWED:
            buf.append(char)
            continue
        if buf:
            found.append("".join(buf))
            buf = []
    if buf:
        found.append("".join(buf))
    return found


def _normalize(span: str) -> str:
    compact = re.sub(r"\s+", "", span)
    compact = re.sub(r"(?<=\d),(?=\d)", "", compact)
    compact = compact.replace("×", "*").replace("÷", "/")
    return compact.replace("^", "**")


def _has_operator(expr: str) -> bool:
    body = expr[1:] if expr.startswith("-") else expr
    return bool(re.search(r"[+\-*/%]", body))


def _phone_like(span: str) -> bool:
    compact = re.sub(r"\s+", "", span)
    if not _PHONE.fullmatch(compact):
        return False
    return "+" not in compact and "*" not in compact and "/" not in compact


def _operand(value: float | int) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a number")
    if abs(value) > _MAX_OPERAND:
        raise ValueError("operand")
    return value


def _walk(node: ast.AST, names: dict | None = None) -> float | int:
    bound = dict(_CONST)
    for key, value in (names or {}).items():
        if key in ("x", "pi", "e"):
            bound[key] = value
    if isinstance(node, ast.Constant):
        return _operand(node.value)
    if isinstance(node, ast.Name):
        if node.id not in bound:
            raise ValueError("name")
        return _operand(bound[node.id])
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return _operand(-_walk(node.operand, names))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left = _walk(node.left, names)
        right = _walk(node.right, names)
        _operand(left)
        _operand(right)
        return _operand(_OPS[type(node.op)](left, right))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        left = _walk(node.left, names)
        right = _walk(node.right, names)
        if isinstance(right, float) or right < 0 or right > _MAX_EXPONENT:
            raise ValueError("exponent")
        return _operand(left**right)
    if isinstance(node, ast.Call):
        func = node.func
        if (
            isinstance(func, ast.Name)
            and func.id in _CALLS
            and len(node.args) == 1
            and not node.keywords
        ):
            return _operand(_CALLS[func.id](_walk(node.args[0], names)))
    raise ValueError("unsupported")


def evaluate_expr(expr: str, variables: dict | None = None) -> str | None:
    """One expression, same whitelist as the calculator, plus `x` and basic calls."""
    raw = (expr or "").strip()
    if not raw or len(raw) > 240 or "\n" in raw:
        return None
    return _evaluate(raw.replace("^", "**"), variables or {})


def _evaluate(expr: str, names: dict | None = None) -> str | None:
    try:
        tree = ast.parse(expr, mode="eval")
        value = _walk(tree.body, names)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        if value.is_integer() and abs(value) < _MAX_OPERAND:
            return str(int(value))
        return format(value, ".12g")
    return str(value)


def _display(span: str) -> str:
    compact = re.sub(r"\s+", "", span.strip())
    return re.sub(r"(?<=\d),(?=\d)", "", compact)


def fully_answers(text: str) -> bool:
    """True when the whole message is arithmetic this module can finish."""
    raw = (text or "").strip()
    if not raw or notes_for(raw) is None:
        return False
    return all(char in _ALLOWED for char in raw)


def _percent_product(text: str) -> str:
    """Rewrite 'N% of M' so the whitelist can multiply. Other words stay."""

    def repl(match: re.Match) -> str:
        return f"({match.group(1)}/100)*{match.group(2)}"

    return _PERCENT_OF.sub(repl, text or "")


def notes_for(text: str) -> str | None:
    """Calculator lines for arithmetic spans, or None when nothing is safe."""
    lines: list[str] = []
    for span in _spans(_percent_product(text)):
        if _DATE.search(span) or _phone_like(span):
            continue
        expr = _normalize(span)
        if not expr or not _has_operator(expr):
            continue
        shown = _evaluate(expr)
        display = _display(span)
        if shown is None or shown == display or shown == expr:
            continue
        lines.append(f"Calculator: {display} = {shown}. Use this result.")
        if len(lines) >= _MAX_EXPRESSIONS:
            break
    if not lines:
        return None
    return "\n".join(lines)
