"""Chat abilities that stay generic: fences, citations, and one extra model call.

A turn may contain at most two fenced tools, named search, calc, plot, or doc.
The fence name is the only signal. There is no JSON function call.
"""

from __future__ import annotations

import json
import os
import re

from pair.calc import evaluate_expr, notes_for
from pair.turn import ATTACH_MARK

_FENCE = re.compile(r"```(search|calc|plot|doc)\n(.*?)```", re.S)
_JSON_FENCE = re.compile(r"```json\n(.*?)```", re.S)
_CITE = re.compile(r"\[(\d+)\]")
_TASK = re.compile(r"^\s*(summarize|rewrite|translate)\b", re.I)
_LANG = re.compile(r"^[A-Za-z0-9_+-]{1,16}$")
TOOL_LIMIT = 2
JSON_RETRY = (
    "The JSON did not match the requested schema. "
    "Reply with one ```json fence that includes the required keys."
)


def task_note(prompt: str) -> str:
    """One tail note for a summarize, rewrite, or translate request."""
    if not _TASK.match(prompt or ""):
        return ""
    return "Do only the requested task. A summary uses far fewer words than the source."


def attachment_note(prompt: str) -> str:
    """File text is untrusted and images are OCR, for any attachment."""
    if ATTACH_MARK not in (prompt or ""):
        return ""
    return "File text is an excerpt. Image text is OCR only, so say that."


def tail_hints(prompt: str, hint: str = "") -> str:
    parts = []
    base = (hint or "").strip()
    if base:
        parts.append(base)
    for extra in (task_note(prompt), attachment_note(prompt)):
        if extra:
            parts.append(extra)
    return "\n\n".join(parts)


def parse_fences(text: str, limit: int = TOOL_LIMIT) -> list[dict]:
    """The first `limit` tool fences, in order. Other fences are left alone."""
    found = []
    for match in _FENCE.finditer(text or ""):
        found.append({"name": match.group(1), "body": match.group(2).strip()})
        if len(found) >= limit:
            break
    return found


def ground_citations(text: str, source_count: int) -> str:
    """Drop a [n] citation that is not one of the numbered snippets."""

    def repl(match: re.Match) -> str:
        number = int(match.group(1))
        if 1 <= number <= int(source_count):
            return match.group(0)
        return ""

    return _CITE.sub(repl, text or "")


def user_schema(prompt: str) -> dict | None:
    match = _JSON_FENCE.search(prompt or "")
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if isinstance(value, dict) and ("properties" in value or "required" in value):
        return value
    return None


def reply_json(reply: str):
    match = _JSON_FENCE.search(reply or "")
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def json_satisfies(schema: dict, value) -> bool:
    if not isinstance(schema, dict) or not isinstance(value, dict):
        return False
    required = schema.get("required") or []
    if not isinstance(required, list):
        return False
    return all(isinstance(key, str) and key in value for key in required)


def needs_json_retry(prompt: str, reply: str) -> bool:
    schema = user_schema(prompt)
    if schema is None:
        return False
    value = reply_json(reply)
    if value is None:
        return True
    return not json_satisfies(schema, value)


def language_class(lang: str) -> str:
    """A safe language tag for a code fence, or an empty string."""
    token = (lang or "").strip()
    if not _LANG.fullmatch(token):
        return ""
    return token


def _calc_note(body: str) -> str:
    line = ""
    for raw in (body or "").splitlines():
        if raw.strip():
            line = raw.strip()
            break
    shown = evaluate_expr(line) if line else None
    if shown is not None:
        return f"Calculator: {line} = {shown}."
    fallback = notes_for(body)
    if fallback:
        return fallback
    return "Calculator: no result."


def _search_note(body: str, search) -> str:
    query = " ".join((body or "").split())
    if not query:
        return "Search returned nothing."
    try:
        found = search(query)
    except Exception:
        return "Search returned nothing."
    if not isinstance(found, dict):
        return "Search returned nothing."
    context = str(found.get("context") or "").strip()
    return context or "Search returned nothing."


def _plot_note(body: str) -> str:
    from pair.charts import render_chart

    result = render_chart(plot=body)
    if not result.get("ok"):
        return "Chart: could not be drawn. The source stays in the reply."
    return f"Chart: {result.get('type') or 'line'}."


def _doc_note(body: str) -> str:
    from pair.docs import render_document, save_document

    kind = "txt"
    title = "document"
    lines = []
    for line in (body or "").splitlines():
        match = re.match(r"^(kind|title)\s*:\s*(\S.*?)\s*$", line.strip(), re.I)
        if match and not lines:
            if match.group(1).lower() == "kind":
                kind = match.group(2).strip().lower()
            else:
                title = match.group(2).strip()
            continue
        lines.append(line)
    data, ext = render_document("\n".join(lines).strip(), kind)
    meta = save_document(data, ext, title)
    return f"Document: {meta['name']}."


def tool_notes(text: str, search=None) -> str:
    """Run at most two fences. Empty when the reply has none."""
    fences = parse_fences(text)
    if not fences:
        return ""
    if search is None:
        from pair.nodes.websearch import search as web_search

        search = web_search

    lines = []
    for fence in fences:
        name = fence["name"]
        body = fence["body"]
        if name == "calc":
            lines.append(_calc_note(body))
        elif name == "search":
            lines.append(_search_note(body, search))
        elif name == "plot":
            lines.append(_plot_note(body))
        elif name == "doc":
            lines.append(_doc_note(body))
    if not lines:
        return ""
    return "Tool results:\n" + "\n".join(lines)


def forward_enabled() -> bool:
    return os.environ.get("PI_PAIR_TOOL_FORWARD") == "1"


CATEGORIES = (
    "persona",
    "calc",
    "citations",
    "files",
    "task",
    "code",
    "json",
    "tools",
    "effort",
    "honest",
)

_EXPRS = (
    "2+2",
    "3*4",
    "10-1",
    "8/2",
    "2^3",
    "7+1",
    "9-4",
    "6*6",
    "100/4",
    "1+2+3",
    "5*5",
    "12-7",
    "4/2",
    "3^2",
    "11+8",
    "20-6",
    "2*9",
    "15/3",
    "6+6",
    "1^4",
)
_LANGS = (
    "python",
    "js",
    "ts",
    "bash",
    "json",
    "css",
    "html",
    "sql",
    "go",
    "rust",
    "java",
    "ruby",
    "php",
    "c",
    "cpp",
    "sh",
    "yaml",
    "toml",
    "text",
    "md",
)
_TASKS = ("summarize", "rewrite", "translate")


def eval_rows() -> list[dict]:
    """200 structural checks, 20 for each ability. Not model answers."""
    rows = []
    for index, expr in enumerate(_EXPRS):
        rows.append({"id": f"persona-{index}", "category": "persona"})
        rows.append({"id": f"calc-{index}", "category": "calc", "expr": expr})
        rows.append(
            {
                "id": f"citations-{index}",
                "category": "citations",
                "text": "See [1] and [9].",
            }
        )
        rows.append(
            {
                "id": f"files-{index}",
                "category": "files",
                "text": f"read this{ATTACH_MARK}page" if index % 2 == 0 else "hello",
            }
        )
        verb = _TASKS[index % len(_TASKS)]
        rows.append(
            {
                "id": f"task-{index}",
                "category": "task",
                "text": f"{verb} the note {index}",
            }
        )
        rows.append({"id": f"code-{index}", "category": "code", "lang": _LANGS[index]})
        rows.append({"id": f"json-{index}", "category": "json", "key": "name"})
        rows.append(
            {
                "id": f"tools-{index}",
                "category": "tools",
                "text": "```calc\n2+2\n```\n```search\nquery\n```\n```plot\nx\n```",
            }
        )
        rows.append({"id": f"effort-{index}", "category": "effort"})
        rows.append({"id": f"honest-{index}", "category": "honest"})
    return rows


def _passes(row: dict) -> bool:
    from pair.turn import EFFORT_HINT, PERSONA, estimate_tokens

    category = row["category"]
    if category == "persona":
        tokens = estimate_tokens(PERSONA)
        return 160 <= tokens <= 200
    if category == "calc":
        note = tool_notes(f"```calc\n{row['expr']}\n```", search=lambda _q: {})
        return str(evaluate_expr(row["expr"])) in note
    if category == "citations":
        grounded = ground_citations(row["text"], 2)
        return "[1]" in grounded and "[9]" not in grounded
    if category == "files":
        note = attachment_note(row["text"])
        if ATTACH_MARK in row["text"]:
            return "OCR" in note
        return note == ""
    if category == "task":
        return "only the requested task" in task_note(row["text"])
    if category == "code":
        return language_class(row["lang"]) == row["lang"]
    if category == "json":
        schema = {"required": [row["key"]]}
        prompt = "```json\n" + json.dumps(schema) + "\n```"
        bad = "```json\n{}\n```"
        good = {row["key"]: "value"}
        return json_satisfies(schema, good) and needs_json_retry(prompt, bad)
    if category == "tools":
        found = parse_fences(row["text"])
        return len(found) == 2 and [item["name"] for item in found] == [
            "calc",
            "search",
        ]
    if category == "effort":
        hint = EFFORT_HINT["high"]
        return "step-by-step" in hint and "Thinking mode stays off" in hint
    if category == "honest":
        return "do not know" in PERSONA and "clarifying question" in PERSONA
    return False


def category_rates(enabled: set[str] | None = None) -> dict[str, float]:
    """Pass rate per ability. A category that is not enabled scores 0."""
    chosen = set(CATEGORIES if enabled is None else enabled)
    grouped: dict[str, list[bool]] = {name: [] for name in CATEGORIES}
    for row in eval_rows():
        ok = _passes(row) if row["category"] in chosen else False
        grouped[row["category"]].append(ok)
    return {
        name: (sum(flags) / len(flags) if flags else 0.0)
        for name, flags in grouped.items()
    }
