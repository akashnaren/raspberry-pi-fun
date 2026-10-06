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

_TOOL_LANG = "search|calc|plot|chart|doc|pdf|docx|xlsx|md|markdown|txt|csv"
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
    if label == "chart":
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


_CHART_ASK = re.compile(r"\b(?:charts?|plots?|graphs?)\b", re.I)
_FILE_ASK = re.compile(
    r"\b(?:pdf|docx|xlsx|csv|markdown|txt|spreadsheets?|downloads?|files?)\b|\.md\b",
    re.I,
)
_DOC_KINDS = {"pdf", "docx", "xlsx", "md", "txt", "csv"}
_META_LINE = re.compile(r"^(kind|title|type)\s*:", re.I)


def _leading_meta(body: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in (body or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _META_LINE.match(stripped)
        if not match:
            break
        value = stripped.split(":", 1)[1].strip()
        found[match.group(1).lower()] = value
    return found


def _kind_token(value: str) -> str:
    token = (value or "").strip().lower()
    if token == "markdown":
        return "md"
    if token in _DOC_KINDS:
        return token
    return ""


def _format_echo(payload: str, persona_words: set[str]) -> bool:
    """True when the payload only repeats the prompt's format wording."""
    from pair.nodes.compact_plan import concrete_tokens
    from pair.turn import _content_words

    words = _content_words(payload)
    if not words or not words <= persona_words:
        return False
    return not concrete_tokens(payload)


def _prepare_payload(body: str, persona: str, question: str) -> str:
    """Drop format lines and stand-in tables. Real prose stays."""
    from pair.charts import without_placeholders
    from pair.turn import PERSONA, _content_words

    banned = _persona_lines(persona or PERSONA)
    raw_lines = (body or "").splitlines()
    index = 0
    while index < len(raw_lines):
        stripped = raw_lines[index].strip()
        if not stripped or _META_LINE.match(stripped):
            index += 1
            continue
        break
    kept = []
    for line in raw_lines[index:]:
        flat = " ".join(line.split())
        if flat in banned:
            continue
        kept.append(line)
    rest = "\n".join(kept).strip()
    cleaned = without_placeholders(rest).strip()
    had_placeholder = cleaned != rest
    payload = cleaned
    if _format_echo(payload, _content_words(persona or PERSONA)):
        payload = ""
    user_words = _content_words(question)
    if had_placeholder and payload and not (_content_words(payload) & user_words):
        payload = ""
    return payload.strip()


def _doc_fence(kind: str, title: str, payload: str) -> str:
    lines = [f"kind: {kind}"]
    if title:
        lines.append(f"title: {title}")
    body = payload.strip()
    if body:
        lines.append(body)
    return "```doc\n" + "\n".join(lines) + "\n```"


def _plot_fence(payload: str) -> str:
    label = "chart" if "|" in payload and "---" in payload else "plot"
    return f"```{label}\n{payload.strip()}\n```"


def _markdown_prose(original: str, body: str, persona: str, question: str) -> str:
    """A markdown fence with no kind line is an answer, not a download."""
    head = original.lstrip()[:12].lower()
    if not head.startswith("```md"):
        return ""
    if re.search(r"(?mi)^kind\s*:", original):
        return ""
    return _prepare_payload(body, persona, question)


def _series_numbers(table: str) -> list[str]:
    from pair.charts import parse_markdown_table

    parsed = parse_markdown_table(table)
    if not parsed:
        return re.findall(r"\b\d+(?:\.\d+)?\b", table or "")
    _headers, rows = parsed
    found = []
    for row in rows:
        for cell in row[1:]:
            if re.fullmatch(r"\d+(?:\.\d+)?", cell.strip()):
                found.append(cell.strip())
    return found


def _doc_fence_present(text: str) -> bool:
    for match in _FENCE.finditer(text or ""):
        name, _body = _as_tool(match.group(1), match.group(2))
        if name == "doc":
            return True
    return False


def _covers(text: str, numbers: list[str], kind: str) -> bool:
    if not numbers:
        return False
    found: set[str] = set()
    for match in _FENCE.finditer(text or ""):
        name, body = _as_tool(match.group(1), match.group(2))
        if name != kind:
            continue
        found.update(re.findall(r"\b\d+(?:\.\d+)?\b", body))
    need = set(numbers)
    return len(need & found) * 2 >= len(need)


def settle_blocks(
    text: str, prompt: str = "", persona: str = "", context: str = ""
) -> str:
    """Drop copied format blocks. Chart and file fences need a real ask.

    A stand-in table is replaced with numbers from the user's turn when they
    asked for a chart or a spreadsheet. Anything rejected here is not a tool
    call: it does not search, draw, or write a file.
    """
    from pair.charts import series_markdown
    from pair.docs import kind_from_request
    from pair.turn import PERSONA, notes_for, user_question

    question = user_question(prompt or "")
    asked_chart = bool(_CHART_ASK.search(question))
    asked_file = bool(_FILE_ASK.search(question))
    wanted = kind_from_request(prompt or "")
    series = ""
    if asked_chart or wanted in {"xlsx", "csv"}:
        series = series_markdown(question)
    pieces: list[str] = []
    last = 0
    source = text or ""
    for match in _FENCE.finditer(source):
        pieces.append(source[last : match.start()])
        name, body = _as_tool(match.group(1), match.group(2))
        pieces.append(
            _settle_fence(
                match.group(0),
                name,
                body,
                prompt,
                persona or PERSONA,
                context,
                question,
                asked_chart,
                asked_file,
                wanted,
                series,
            )
        )
        last = match.end()
    pieces.append(source[last:])
    out = "".join(pieces)
    numbers = _series_numbers(series)
    if asked_chart and series and not _covers(out, numbers, "plot"):
        block = _plot_fence(series)
        out = f"{out.rstrip()}\n\n{block}" if out.strip() else block
    if wanted in {"xlsx", "csv"} and series and not _covers(out, numbers, "doc"):
        block = _doc_fence(wanted, "", series)
        out = f"{out.rstrip()}\n\n{block}" if out.strip() else block
    if wanted and not _doc_fence_present(out):
        prose = _prepare_payload(out, persona or PERSONA, question)
        if prose:
            out = _doc_fence(wanted, "", prose)
    if not out.strip():
        note = notes_for(question)
        if note:
            return note
    return out


def _settle_fence(
    original: str,
    name: str,
    body: str,
    prompt: str,
    persona: str,
    context: str,
    question: str,
    asked_chart: bool,
    asked_file: bool,
    wanted: str,
    series: str,
) -> str:
    from pair.turn import answered_locally

    if name == "search":
        if answered_locally(prompt or "", context):
            return ""
        return original
    if name == "calc":
        return original if body.strip() else ""
    if name == "plot" and not asked_chart:
        return ""
    if name == "doc" and not asked_file:
        return _markdown_prose(original, body, persona, question)
    payload = _prepare_payload(body, persona, question)
    if not payload:
        if series and (name == "plot" or wanted in {"xlsx", "csv"}):
            payload = series
        else:
            return ""
    if name == "plot":
        return _plot_fence(payload)
    meta = _leading_meta(body)
    kind = wanted or _kind_token(meta.get("kind", "")) or "md"
    return _doc_fence(kind, meta.get("title", ""), payload)


def clean_reply(
    text: str,
    prompt: str = "",
    source_count: int = 0,
    persona: str = "",
    context: str = "",
) -> str:
    """Drop empty fences, repeated fences, echoed prompt lines, and stray [n]."""
    from pair.turn import PERSONA, user_question

    cleaned = settle_blocks(text or "", prompt, persona, context)
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


def tool_notes(text: str, search=None, *, prompt: str = "", context: str = "") -> str:
    """Run at most two fences. A rejected block is not a tool call."""
    text = settle_blocks(text, prompt=prompt, context=context)
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
            from pair.turn import answered_locally

            if answered_locally(prompt, context):
                continue
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
