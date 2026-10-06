"""Arithmetic notes from a standard-library ast whitelist.

Spans of numbers, operators, and parentheses are evaluated. Word operators
and topic rules are out of scope. A bad span is skipped, never raised.
"""

from __future__ import annotations

import ast
import re

_MAX_EXPRESSIONS = 3
_MAX_OPERAND = 1e15
_MAX_EXPONENT = 10
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PHONE = re.compile(r"\d{2,4}(?:-\d{2,4})+")
_ALLOWED = set("0123456789+-*/%^×÷()., \t")
_OPS = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.FloorDiv: lambda left, right: left // right,
    ast.Mod: lambda left, right: left % right,
}


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


def _walk(node: ast.AST) -> float | int:
    if isinstance(node, ast.Constant):
        return _operand(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return _operand(-_walk(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left = _walk(node.left)
        right = _walk(node.right)
        _operand(left)
        _operand(right)
        return _operand(_OPS[type(node.op)](left, right))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        left = _walk(node.left)
        right = _walk(node.right)
        if isinstance(right, float) or right < 0 or right > _MAX_EXPONENT:
            raise ValueError("exponent")
        return _operand(left**right)
    raise ValueError("unsupported")


def _evaluate(expr: str) -> str | None:
    try:
        tree = ast.parse(expr, mode="eval")
        value = _walk(tree.body)
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


def notes_for(text: str) -> str | None:
    """Calculator lines for arithmetic spans, or None when nothing is safe."""
    lines: list[str] = []
    for span in _spans(text):
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
