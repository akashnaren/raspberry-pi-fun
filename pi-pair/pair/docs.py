"""Markdown to a downloadable file. Office XML and PDF use the standard library.

Files live under the pair data directory and are removed after 24 hours.
Nothing here generates text.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
import uuid
import zipfile
from pathlib import Path

from pair.charts import parse_markdown_table
from pair.config import data_root

MAX_AGE_S = 24 * 60 * 60
MAX_FILES = 32
_KINDS = {"md", "txt", "csv", "docx", "xlsx", "pdf"}
_ID = re.compile(r"[0-9a-f]{32}")
_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_KIND_ASK = re.compile(r"\b(pdf|docx|xlsx|csv|markdown|txt)\b|\.md\b", re.I)


def _xml(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _plain(markdown: str) -> str:
    lines = []
    for line in (markdown or "").replace("\r\n", "\n").split("\n"):
        if line.strip().startswith("```"):
            continue
        lines.append(line)
    return "\n".join(lines).strip() + "\n"


def _split_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [part.strip() for part in text.split("|")]


def _blocks(markdown: str) -> list:
    """Prose and tables. A row may be wider than the header; cells stay."""
    lines = _plain(markdown).split("\n")
    blocks: list = []
    text: list[str] = []

    def flush() -> None:
        if any(line.strip() for line in text):
            blocks.append(("text", "\n".join(text).strip("\n")))
        text.clear()

    index = 0
    while index < len(lines):
        nxt = index + 1
        if nxt < len(lines) and "|" in lines[index] and _RULE.match(lines[nxt]):
            headers = _split_row(lines[index])
            rows: list[list[str]] = []
            cursor = nxt + 1
            while (
                cursor < len(lines)
                and lines[cursor].strip()
                and "|" in lines[cursor]
                and not _RULE.match(lines[cursor])
            ):
                rows.append(_split_row(lines[cursor]))
                cursor += 1
            if rows and len(headers) >= 2:
                width = max(len(headers), *(len(row) for row in rows))
                headers = headers + [""] * (width - len(headers))
                rows = [row + [""] * (width - len(row)) for row in rows]
                flush()
                blocks.append(("table", headers, rows))
                index = cursor
                continue
        text.append(lines[index])
        index += 1
    flush()
    return blocks


def _first_table(markdown: str) -> tuple[list[str], list[list[str]]] | None:
    for block in _blocks(markdown):
        if block[0] == "table":
            return block[1], block[2]
    parsed = parse_markdown_table(markdown)
    if not parsed:
        return None
    return parsed


def _csv(markdown: str) -> str:
    parsed = _first_table(markdown)
    buf = io.StringIO()
    writer = csv.writer(buf)
    if parsed:
        headers, rows = parsed
        writer.writerow(headers)
        writer.writerows(rows)
    else:
        for line in _plain(markdown).splitlines():
            writer.writerow([line])
    return buf.getvalue()


def _docx_table(headers: list[str], rows: list[list[str]]) -> str:
    edges = ("top", "left", "bottom", "right", "insideH", "insideV")
    borders = "".join(
        f'<w:{edge} w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        for edge in edges
    )
    grid = "".join('<w:gridCol w:w="2400"/>' for _ in headers)

    def cell(value: str) -> str:
        return (
            '<w:tc><w:tcPr><w:tcW w:w="2400" w:type="dxa"/></w:tcPr>'
            f'<w:p><w:r><w:t xml:space="preserve">{_xml(value)}</w:t></w:r></w:p>'
            "</w:tc>"
        )

    def row(values: list[str]) -> str:
        return "<w:tr>" + "".join(cell(value) for value in values) + "</w:tr>"

    body = row(headers) + "".join(row(values) for values in rows)
    return (
        '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>'
        f"<w:tblBorders>{borders}</w:tblBorders></w:tblPr>"
        f"<w:tblGrid>{grid}</w:tblGrid>{body}</w:tbl>"
    )


def _zip(parts: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
    return buf.getvalue()


def _docx(markdown: str) -> bytes:
    paragraphs = []
    for block in _blocks(markdown):
        if block[0] == "text":
            for line in str(block[1]).split("\n"):
                paragraphs.append(
                    '<w:p><w:r><w:t xml:space="preserve">'
                    f"{_xml(line)}</w:t></w:r></w:p>"
                )
        else:
            paragraphs.append(_docx_table(block[1], block[2]))
    if not paragraphs:
        paragraphs.append("<w:p/>")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(paragraphs)}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
        "</w:body></w:document>"
    )
    return _zip(
        {
            "[Content_Types].xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/word/document.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                "</Types>"
            ),
            "_rels/.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="word/document.xml"/>'
                "</Relationships>"
            ),
            "word/document.xml": document,
            "word/_rels/document.xml.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
            ),
        }
    )


def _cell(col: int, row: int, value: str) -> str:
    ref = f"{chr(ord('A') + col)}{row}"
    return f'<c r="{ref}" t="inlineStr"><is><t>{_xml(value)}</t></is></c>'


def _sheet_rows(markdown: str) -> list[list[str]]:
    parsed = _first_table(markdown)
    if parsed:
        headers, rows = parsed
        return [headers, *rows]
    return [[line] for line in _plain(markdown).splitlines()] or [[""]]


def _xlsx(markdown: str) -> bytes:
    rows_xml = []
    for index, row in enumerate(_sheet_rows(markdown), start=1):
        cells = "".join(_cell(col, index, value) for col, value in enumerate(row[:26]))
        rows_xml.append(f'<row r="{index}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(rows_xml)}</sheetData></worksheet>"
    )
    return _zip(
        {
            "[Content_Types].xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/worksheets/sheet1.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                "</Types>"
            ),
            "_rels/.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="xl/workbook.xml"/>'
                "</Relationships>"
            ),
            "xl/workbook.xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
            ),
            "xl/_rels/workbook.xml.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                'Target="worksheets/sheet1.xml"/>'
                "</Relationships>"
            ),
            "xl/worksheets/sheet1.xml": sheet,
        }
    )


def _pdf_escape(text: str) -> str:
    raw = (text or "").encode("latin-1", "replace").decode("latin-1")
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap_line(raw: str) -> list[str]:
    line = (raw or "").replace("\t", "    ")
    if not line:
        return [""]
    lines: list[str] = []
    while len(line) > 90:
        lines.append(line[:90])
        line = line[90:]
    lines.append(line)
    return lines


def _pdf_items(markdown: str) -> list[tuple]:
    """Text lines and horizontal rules. Table cells are drawn, not piped."""
    items: list[tuple] = []
    for block in _blocks(markdown):
        if block[0] == "text":
            for raw in str(block[1]).split("\n"):
                for line in _wrap_line(raw):
                    items.append(("text", line))
            continue
        headers, rows = block[1], block[2]
        items.append(("rule",))
        items.append(("text", "  ".join(headers)))
        items.append(("rule",))
        for row in rows:
            items.append(("text", "  ".join(row)))
            items.append(("rule",))
    return items or [("text", "")]


def _pdf_commands(items: list[tuple]) -> bytes:
    commands: list[str] = []
    y = 750
    text_open = False

    def close_text() -> None:
        nonlocal text_open
        if text_open:
            commands.append("ET")
            text_open = False

    def open_text() -> None:
        nonlocal text_open
        if text_open:
            return
        commands.append("BT")
        commands.append("/F1 11 Tf")
        commands.append(f"1 0 0 1 72 {y} Tm")
        commands.append("14 TL")
        text_open = True

    for item in items:
        kind = item[0]
        if kind == "rule":
            close_text()
            y -= 4
            commands.append(f"72 {y} m 540 {y} l S")
            y -= 6
            continue
        open_text()
        commands.append(f"({_pdf_escape(item[1])}) Tj")
        commands.append("T*")
        y -= 14
    close_text()
    return "\n".join(commands).encode("latin-1", "replace")


def _pdf(markdown: str) -> bytes:
    items = _pdf_items(markdown)
    chunks = [items[index : index + 40] for index in range(0, len(items), 40)]
    objects: dict[int, bytes] = {}
    page_ids = []
    next_id = 4
    for chunk in chunks:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_ids.append(page_id)
        stream = _pdf_commands(chunk)
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
        ).encode()
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(chunks)} >>".encode()
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index in range(1, next_id):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode())
        out.extend(objects[index])
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {next_id}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for index in range(1, next_id):
        out.extend(f"{offsets[index]:010d} 00000 n \n".encode())
    out.extend(
        f"trailer\n<< /Size {next_id} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def render_document(markdown: str, kind: str) -> tuple[bytes, str]:
    """Bytes and the file extension. Unknown kinds become markdown."""
    chosen = (kind or "md").strip().lower()
    if chosen not in _KINDS:
        chosen = "md"
    text = markdown or ""
    if chosen == "md":
        return text.encode("utf-8"), "md"
    if chosen == "txt":
        return _plain(text).encode("utf-8"), "txt"
    if chosen == "csv":
        return _csv(text).encode("utf-8"), "csv"
    if chosen == "docx":
        return _docx(text), "docx"
    if chosen == "xlsx":
        return _xlsx(text), "xlsx"
    return _pdf(text), "pdf"


def _dir() -> Path:
    root = data_root() / "docs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _unlink_doc(root: Path, meta_path: Path, meta: dict) -> None:
    ext = str(meta.get("ext") or "")
    blob = root / f"{meta_path.stem}.{ext}" if ext else None
    if blob is not None and blob.is_file():
        blob.unlink()
    meta_path.unlink(missing_ok=True)


def purge_documents(max_age: float = MAX_AGE_S, now: float | None = None) -> int:
    """Delete files older than `max_age`, then the oldest past MAX_FILES."""
    clock = time.time() if now is None else float(now)
    removed = 0
    root = data_root() / "docs"
    if not root.is_dir():
        return 0
    kept: list[tuple[float, Path, dict]] = []
    for meta_path in list(root.glob("*.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            created = float(meta.get("created") or 0)
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(meta, dict):
            continue
        if clock - created >= max_age:
            _unlink_doc(root, meta_path, meta)
            removed += 1
            continue
        kept.append((created, meta_path, meta))
    kept.sort(key=lambda item: (item[0], item[1].name))
    overflow = len(kept) - MAX_FILES
    for _created, meta_path, meta in kept[: max(0, overflow)]:
        _unlink_doc(root, meta_path, meta)
        removed += 1
    return removed


def kind_from_request(prompt: str) -> str:
    """The file type the user named, or empty. Never guesses docx."""
    from pair.turn import user_question

    match = _KIND_ASK.search(user_question(prompt or ""))
    if not match:
        return ""
    token = (match.group(1) or "md").lower()
    if token == "markdown":
        return "md"
    return token


def save_document(data: bytes, ext: str, name: str = "") -> dict:
    """Store one file and drop anything older than a day."""
    purge_documents()
    doc_id = uuid.uuid4().hex
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "document"
    if not safe.lower().endswith("." + ext):
        safe = f"{safe}.{ext}"
    root = _dir()
    (root / f"{doc_id}.{ext}").write_bytes(data)
    meta = {
        "id": doc_id,
        "name": safe,
        "ext": ext,
        "bytes": len(data),
        "created": time.time(),
    }
    (root / f"{doc_id}.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


def open_document(
    doc_id: str, now: float | None = None
) -> tuple[bytes, str, str] | None:
    """`(bytes, filename, content type)` or None when missing or expired."""
    if not _ID.fullmatch(doc_id or ""):
        return None
    purge_documents(now=now)
    root = data_root() / "docs"
    meta_path = root / f"{doc_id}.json"
    blob_guess = list(root.glob(f"{doc_id}.*"))
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    clock = time.time() if now is None else float(now)
    if clock - float(meta.get("created") or 0) >= MAX_AGE_S:
        return None
    ext = str(meta.get("ext") or "")
    blob = root / f"{doc_id}.{ext}"
    if not blob.is_file():
        for candidate in blob_guess:
            if candidate.suffix != ".json" and candidate.is_file():
                blob = candidate
                break
        else:
            return None
    types = {
        "md": "text/markdown; charset=utf-8",
        "txt": "text/plain; charset=utf-8",
        "csv": "text/csv; charset=utf-8",
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    return (
        blob.read_bytes(),
        str(meta.get("name") or blob.name),
        types.get(ext, "application/octet-stream"),
    )
