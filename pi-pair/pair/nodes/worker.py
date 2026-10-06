"""Plain HTTP tools for pi2 and pi3. None of these routes generate text."""

from __future__ import annotations

import base64

from pair.nodes import websearch
from pair.tools import GENERATION_PATHS, registry

ROUTES = {tool.path: tool.name for tool in registry().values()}


def forbidden_routes() -> list[str]:
    """Generation paths that this worker also serves. Must stay empty."""
    return [path for path in ROUTES if path in GENERATION_PATHS]


def _extract(payload: dict) -> dict:
    """Text files pass through. Images and PDFs use the local OCR binaries."""
    name = str(payload.get("filename") or "file")
    raw = payload.get("data") or ""
    try:
        blob = base64.b64decode(raw) if isinstance(raw, str) and raw else b""
    except Exception:
        blob = b""
    kind = str(payload.get("kind") or "")
    if kind == "text" or name.lower().endswith((".txt", ".md", ".csv")):
        text = blob.decode("utf-8", "replace").strip()
        return {"ok": bool(text), "text": text, "filename": name}
    from pair.ocr import OcrFailed, OcrNotInstalled, recognize_image, recognize_pdf

    try:
        if name.lower().endswith(".pdf") or kind == "pdf":
            text = recognize_pdf(blob)
        else:
            text = recognize_image(blob)
    except (OcrFailed, OcrNotInstalled) as exc:
        return {"ok": False, "text": "", "error": str(exc), "filename": name}
    return {"ok": bool(text), "text": text, "filename": name}


def _search(payload: dict) -> dict:
    query = str(payload.get("q") or payload.get("query") or "")
    return websearch.search(query)


def _render_doc(payload: dict) -> dict:
    """Placeholder until PR4 writes the file. The route still does not generate."""
    markdown = str(payload.get("markdown") or "")
    kind = str(payload.get("kind") or "md")
    return {"ok": True, "kind": kind, "bytes": len(markdown.encode("utf-8"))}


def _render_chart(payload: dict) -> dict:
    table = str(payload.get("table") or "")
    return {"ok": bool(table.strip()), "table": table}


def _tokenize(payload: dict) -> dict:
    """Character estimate only. Tokenizer files are optional and no weights are loaded."""
    text = str(payload.get("text") or "")
    tokens = 0 if not text else max(1, int(len(text) / 3.2))
    return {"ok": True, "tokens": tokens}


def _compact_plan(payload: dict) -> dict:
    from pair.nodes.compact_plan import plan_turns

    turns = payload.get("turns") if isinstance(payload.get("turns"), list) else []
    try:
        num_ctx = int(payload.get("num_ctx") or 2048)
    except (TypeError, ValueError):
        num_ctx = 2048
    planned = plan_turns(turns, num_ctx)
    planned["ok"] = True
    return planned


def _memory(payload: dict) -> dict:
    from pair.nodes.memory_store import apply

    return apply(payload)


_HANDLERS = {
    "/tools/extract": _extract,
    "/tools/search": _search,
    "/tools/render_doc": _render_doc,
    "/tools/render_chart": _render_chart,
    "/tools/tokenize": _tokenize,
    "/tools/compact_plan": _compact_plan,
    "/tools/memory": _memory,
}


def handle(path: str, payload: dict | None) -> tuple[int, dict]:
    if path in GENERATION_PATHS:
        return 404, {"error": "this node does not generate"}
    handler = _HANDLERS.get(path)
    if handler is None:
        return 404, {"error": "unknown tool"}
    try:
        body = handler(payload or {})
    except Exception as exc:
        return 502, {"ok": False, "error": type(exc).__name__}
    return 200, body


def health_body() -> dict:
    from pair import runtime
    from pair.thermal import sample

    temp = sample()
    return {
        "ok": True,
        "role": "tools",
        "load": 0.0,
        "queue": runtime.gate.waiting() if runtime.gate else 0,
        "temp_c": None if temp is None else temp.get("temp_c"),
        "routes": sorted(ROUTES),
    }
