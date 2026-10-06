"""Chat abilities that stay generic: fences, citations, and one extra model call.

A turn may contain at most two fenced tools, named search, calc, plot, or doc.
The fence name is the only signal. There is no JSON function call.
"""

from __future__ import annotations

import json
import os
import re

from pair.calc import evaluate_expr
from pair.turn import ATTACH_MARK

_TOOL_LANG = "search|calc|plot|chart|bar|doc|pdf|docx|xlsx|md|markdown|txt|csv"
_FENCE = re.compile(rf"```({_TOOL_LANG})[ \t]*\n(.*?)```", re.S | re.I)
_ANY_FENCE = re.compile(r"```([^\n`]*)\n?([\s\S]*?)```")
_JSON_FENCE = re.compile(r"```json\n(.*?)```", re.S)
_CITE = re.compile(r"\[(n(?:=\d+)?|\d+)\]", re.I)
_LANG_TOKEN = re.compile(r"^[A-Za-z0-9_+-]{1,32}$")
_FILE_LANG = {"pdf", "docx", "xlsx", "md", "markdown", "txt", "csv"}
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


def _as_tool(name: str, body: str) -> tuple[str, str]:
    """Fence language to a tool name. pdf/docx/xlsx/md are documents."""
    label = (name or "").strip().lower()
    text = (body or "").strip()
    if label in {"chart", "bar"}:
        label = "plot"
    if label in _FILE_LANG:
        kind = "md" if label == "markdown" else label
        if not re.search(r"(?m)^kind\s*:", text):
            text = f"kind: {kind}\n{text}"
        label = "doc"
    return label, text


def parse_fences(text: str, limit: int = TOOL_LIMIT) -> list[dict]:
    """The first `limit` tool fences, in order. Other fences are left alone."""
    found = []
    for match in _FENCE.finditer(text or ""):
        name, body = _as_tool(match.group(1), match.group(2))
        if not body:
            continue
        found.append({"name": name, "body": body})
        if len(found) >= limit:
            break
    return found


def ground_citations(text: str, source_count: int) -> str:
    """Drop a citation that is not one of the numbered snippets.

    Literal [n] and [n=1] markers never match a source. Fenced blocks stay.
    """

    def repl(match: re.Match) -> str:
        token = match.group(1)
        if token.isdigit():
            number = int(token)
            if 1 <= number <= int(source_count):
                return match.group(0)
        return ""

    parts = re.split(r"(```[\s\S]*?```)", text or "")
    cleaned = [
        part if index % 2 else _CITE.sub(repl, part) for index, part in enumerate(parts)
    ]
    return "".join(cleaned)


def strip_fences(text: str) -> str:
    """Drop an empty fence, and a fence whose label is not a language token."""

    def repl(match: re.Match) -> str:
        label = (match.group(1) or "").strip()
        body = (match.group(2) or "").strip()
        if not body:
            return ""
        if label and not _LANG_TOKEN.fullmatch(label):
            return ""
        return match.group(0)

    return _ANY_FENCE.sub(repl, text or "")


def dedupe_fences(text: str) -> str:
    """One copy of each tool fence. A repeated doc block renders once."""
    seen: set[tuple[str, str]] = set()

    def repl(match: re.Match) -> str:
        label = (match.group(1) or "").strip().lower()
        body = " ".join((match.group(2) or "").split())
        if not body:
            return match.group(0)
        key = (label, body)
        if key in seen:
            return ""
        seen.add(key)
        return match.group(0)

    return _ANY_FENCE.sub(repl, text or "")


def _persona_lines(persona: str) -> set[str]:
    rows: set[str] = set()
    for line in (persona or "").splitlines():
        stripped = " ".join(line.split())
        if not stripped or stripped.startswith("```") or stripped.startswith("|"):
            continue
        rows.add(stripped)
        for piece in re.split(r"(?<=[.!?])\s+", stripped):
            piece = piece.strip()
            if len(piece) >= 24:
                rows.add(piece)
    return rows


_META_LINE = re.compile(r"^(kind|title|type)\s*:", re.I)


def _split_meta(body: str) -> tuple[str, str]:
    """Leading kind, title, and type lines, then the rest of the fence."""
    meta: list[str] = []
    rest: list[str] = []
    started = False
    for line in (body or "").splitlines():
        if not started and (not line.strip() or _META_LINE.match(line.strip())):
            if line.strip():
                meta.append(line.strip())
            continue
        started = True
        rest.append(line)
    return "\n".join(meta), "\n".join(rest).strip()


def _copied_from_prompt(text: str, persona: str) -> bool:
    """True when the block is a byte-for-byte span of the system prompt."""
    payload = (text or "").strip()
    source = (persona or "").strip()
    if len(payload) < 24 or not source:
        return False
    return payload in source


def _calc_line(body: str) -> str:
    """The expression in a calc fence, or empty when it does not evaluate."""
    _meta, payload = _split_meta(body)
    text = payload or (body or "").strip()
    if text and "\n" not in text and evaluate_expr(text) is not None:
        return text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and evaluate_expr(stripped) is not None:
            return stripped
    return ""


def _real_block(name: str, body: str, persona: str) -> bool:
    """A fence the model wrote, with content, that is not prompt text."""
    meta, payload = _split_meta(body)
    if _copied_from_prompt(body, persona) or _copied_from_prompt(payload, persona):
        return False
    if _copied_from_prompt(
        "\n".join(part for part in (meta, payload) if part), persona
    ):
        return False
    if name == "calc":
        return bool(_calc_line(body))
    if name == "search":
        return bool((body or "").strip())
    return bool(payload)


def _kept_fence(original: str, label: str, name: str, body: str) -> str:
    """The model's fence. A headerless table gains label/value headers."""
    if name not in {"plot", "doc"}:
        return original
    from pair.charts import with_headers

    meta, payload = _split_meta(body)
    table = with_headers(payload)
    if table == payload:
        return original
    parts = [part for part in (meta, table) if part]
    return f"```{label}\n" + "\n".join(parts) + "\n```"


def _close_trailing_fence(text: str) -> str:
    """Keep a tool fence the model left open at the end of the reply."""
    source = text or ""
    closed = list(_FENCE.finditer(source))
    cursor = closed[-1].end() if closed else 0
    tail = source[cursor:]
    opener = None
    for match in re.finditer(rf"```({_TOOL_LANG})[ \t]*\n", tail, re.I):
        opener = match
    if opener is None:
        return source
    rest = tail[opener.end() :]
    if "```" in rest or not rest.strip():
        return source
    return source.rstrip() + "\n```"


def settle_blocks(
    text: str,
    prompt: str = "",
    persona: str = "",
    context: str = "",
    have_tools: bool = False,
) -> str:
    """Keep a chart or file only when the model wrote a block with content.

    Nothing here wraps a plain reply as a download, and nothing here builds
    a table from the user's wording. A search fence runs at most once, and
    not when this turn already has a tool result. A block copied from the
    system prompt is dropped. A rejected block is not a tool call.
    """
    from pair.turn import PERSONA, notes_for, sample_turns, user_question

    del context
    question = user_question(prompt or "")
    samples = "\n".join(row["content"] for row in sample_turns())
    source_persona = "\n".join(part for part in ((persona or PERSONA), samples) if part)
    source = _close_trailing_fence(text or "")
    found = []
    for match in _FENCE.finditer(source):
        raw = match.group(2)
        name, _body = _as_tool(match.group(1), raw)
        found.append((match, name, raw))
    other = any(
        name != "search" and _real_block(name, raw, source_persona)
        for _match, name, raw in found
    )
    block_search = bool(have_tools or other or notes_for(question))
    pieces: list[str] = []
    last = 0
    saw_search = False
    for match, name, raw in found:
        pieces.append(source[last : match.start()])
        keep = ""
        if name == "search":
            if (
                not saw_search
                and not block_search
                and _real_block(name, raw, source_persona)
            ):
                keep = match.group(0)
                saw_search = True
        elif _real_block(name, raw, source_persona):
            keep = _kept_fence(match.group(0), match.group(1), name, raw)
        pieces.append(keep)
        last = match.end()
    pieces.append(source[last:])
    out = "".join(pieces)
    if not out.strip():
        note = notes_for(question)
        if note:
            return note
    return out


def clean_reply(
    text: str,
    prompt: str = "",
    source_count: int = 0,
    persona: str = "",
    context: str = "",
    have_tools: bool = False,
) -> str:
    """Drop empty fences, repeated fences, echoed prompt lines, and stray [n]."""
    from pair.turn import PERSONA, user_question

    cleaned = settle_blocks(text or "", prompt, persona, context, have_tools=have_tools)
    cleaned = dedupe_fences(strip_fences(cleaned))
    question = " ".join(user_question(prompt or "").split())
    banned = _persona_lines(persona or PERSONA)
    lines = []
    for line in cleaned.splitlines():
        flat = " ".join(line.split())
        if question and len(question) >= 8 and flat == question:
            continue
        if flat in banned:
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    cleaned = ground_citations(cleaned, source_count)
    return "\n".join(line for line in cleaned.splitlines() if line.strip()).strip()


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
    line = _calc_line(body)
    shown = evaluate_expr(line) if line else None
    if shown is not None:
        return f"Calculator: {line} = {shown}."
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

    kind = "md"
    title = "document"
    lines = []
    for line in (body or "").splitlines():
        match = re.match(r"^(kind|title)\s*:\s*(\S.*?)\s*$", line.strip(), re.I)
        if match and not lines:
            if match.group(1).lower() == "kind":
                kind = match.group(2).strip().lower()
                if kind == "markdown":
                    kind = "md"
            else:
                title = match.group(2).strip()
            continue
        lines.append(line)
    data, ext = render_document("\n".join(lines).strip(), kind)
    meta = save_document(data, ext, title)
    return f"Document: {meta['name']}."


def tool_notes(
    text: str,
    search=None,
    *,
    prompt: str = "",
    context: str = "",
    have_tools: bool = False,
) -> str:
    """Run at most two fences. A rejected block is not a tool call."""
    text = settle_blocks(text, prompt=prompt, context=context, have_tools=have_tools)
    fences = parse_fences(text)
    if not fences:
        return ""
    if search is None:
        from pair.nodes.websearch import search as web_search

        search = web_search

    lines = []
    saw_search = False
    for fence in fences:
        name = fence["name"]
        body = fence["body"]
        if name == "calc":
            lines.append(_calc_note(body))
        elif name == "search":
            if saw_search:
                continue
            saw_search = True
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
        lowered = PERSONA.lower()
        stock = ("you do not know", "clarifying question", "calc plot doc")
        return (
            40 <= tokens <= 220
            and "```" not in PERSONA
            and "you are not the user" in lowered
            and "calc" in lowered
            and "plot" in lowered
            and not any(phrase in lowered for phrase in stock)
        )
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
        lowered = PERSONA.lower()
        return "do not know" not in lowered and "clarifying question" not in lowered
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
