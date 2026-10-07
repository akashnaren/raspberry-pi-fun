"""Subset loader for the train config files. Stdlib only."""

from __future__ import annotations

from pathlib import Path


def _scalar(text: str):
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    if text in ("null", "None", "~"):
        return None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def loads(text: str) -> dict:
    lines = []
    for raw in text.splitlines():
        stripped = raw.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip()))
    parsed, index = _map(lines, 0, 0)
    if index != len(lines):
        raise ValueError("train config did not parse")
    if not isinstance(parsed, dict):
        raise ValueError("train config must be a map")
    return parsed


def load_path(path: Path) -> dict:
    return loads(path.read_text(encoding="utf-8"))


def _map(lines, index: int, indent: int):
    obj: dict = {}
    while index < len(lines):
        level, text = lines[index]
        if level < indent:
            break
        if level > indent:
            raise ValueError("unexpected indent in train config")
        if text.startswith("- "):
            raise ValueError("list where a map was expected")
        key, sep, rest = text.partition(":")
        if not sep:
            raise ValueError(f"missing colon in {text}")
        key = key.strip()
        rest = rest.strip()
        index += 1
        if rest == "":
            if index >= len(lines) or lines[index][0] <= level:
                obj[key] = {}
                continue
            child_indent, child_text = lines[index]
            if child_text.startswith("- "):
                obj[key], index = _list(lines, index, child_indent)
            else:
                obj[key], index = _map(lines, index, child_indent)
        else:
            obj[key] = _scalar(rest)
    return obj, index


def _list(lines, index: int, indent: int):
    items = []
    while index < len(lines):
        level, text = lines[index]
        if level < indent:
            break
        if level != indent or not text.startswith("- "):
            break
        items.append(_scalar(text[2:].strip()))
        index += 1
    return items, index
