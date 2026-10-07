from __future__ import annotations
import json
import threading
import time
from urllib.parse import urlparse
from pair.model.chat_once import warm_in_flight
from pair.model.knobs import search_note_limit
from pair.model.modes import mode_table
from pair.flywheel.miss_queue import node_role
from pair.mesh.offload import lookup_for_brain
from pair.search.lookup import lookup_web
from pair.turn.shape import prepare_search_note
from pair.routes.base import safe_write


FLASH_SOURCE_CAP = 3
PRO_SOURCE_CAP = 8
SEARCH_BUDGET_S = 4.0
SEARCH_BODY_CAP = 4096


def _source_cap(model: str) -> int:
    """Flash reads fewer pages. Pro keeps the wider set."""
    pro = str(mode_table().get("pro") or "")
    if pro and str(model or "") == pro:
        return PRO_SOURCE_CAP
    return FLASH_SOURCE_CAP


def _join_cancel(thread, timeout: float | None, cancel=None) -> None:
    """Wait for a side thread, and notice a disconnect about four times a second."""
    if thread is None:
        return
    if cancel is None:
        thread.join(timeout)
        return
    deadline = None if timeout is None else time.monotonic() + timeout
    while thread.is_alive():
        cancel.check()
        if deadline is not None and time.monotonic() >= deadline:
            return
        thread.join(0.25)


def _begin_lookup(prompt: str, model: str) -> dict:
    """Start the lookup before the decode slot, so it overlaps the queue wait."""
    cap = _source_cap(model)
    holder: dict = {}
    started = time.perf_counter()
    deadline = time.monotonic() + SEARCH_BUDGET_S

    def _lookup() -> None:
        try:
            holder["found"] = lookup_for_brain(
                prompt, local=lookup_web, limit=cap, deadline=deadline
            )
        except Exception:
            holder["found"] = None

    lookup = threading.Thread(target=_lookup, name="search-lookup", daemon=True)
    lookup.start()
    return {
        "thread": lookup,
        "holder": holder,
        "deadline": deadline,
        "started": started,
        "cap": cap,
    }


def _with_search(
    messages,
    prompt: str,
    model: str = "",
    cancel=None,
    job: dict | None = None,
):
    """On pi4, attach public notes when a lookup already ran. Failures stay local.

    The lookup is capped so a slow search cannot hold the first token. An
    in-flight model warm runs beside the join.
    """
    warm = warm_in_flight()
    if not (prompt or "").strip():
        _join_cancel(warm, 40, cancel)
        return messages, None
    if job is None:
        job = _begin_lookup(prompt, model)
    lookup = job["thread"]
    holder = job["holder"]
    cap = int(job["cap"])
    remain = max(0.0, float(job["deadline"]) - time.monotonic())
    _join_cancel(lookup, remain, cancel)
    found = holder.get("found") if not lookup.is_alive() else None
    if not isinstance(found, dict):
        found = {"status": "failed", "sources": [], "context": ""}
    status = found.get("status")
    if status not in ("ok", "failed"):
        status = "failed"
    sources = []
    for item in found.get("sources") or []:
        if len(sources) >= cap:
            break
        if not isinstance(item, dict):
            continue
        url = _source_url(str(item.get("url") or ""))
        if not url:
            continue
        title = str(item.get("title") or url).strip() or url
        sources.append({"title": title[:120], "url": url})
    full = str(found.get("context") or "").strip()
    shown = prepare_search_note(full, search_note_limit())
    note = {"status": status, "sources": sources, "context": full}
    if status == "ok" and shown:
        note["prompt_note"] = shown
    _join_cancel(warm, 40, cancel)
    return messages, note


def _searched(search_note) -> bool:
    """True when this turn already received a search result."""
    return isinstance(search_note, dict) and search_note.get("status") == "ok"


def _source_count(search_note) -> int:
    if not isinstance(search_note, dict):
        return 0
    sources = search_note.get("sources")
    return len(sources) if isinstance(sources, list) else 0


def _source_url(url: str) -> str:
    """http(s) link with no userinfo. Anything else is dropped before the page."""
    text = (url or "").strip()
    if not text or text.startswith("//") or any(ch in text for ch in "\r\n\t "):
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        return ""
    if not parsed.hostname or parsed.username or parsed.password:
        return ""
    return text


class SearchRoutes:
    def _timed_search(self, *args, job=None, **kwargs):
        started = time.perf_counter()
        try:
            return _with_search(*args, job=job, **kwargs)
        finally:
            origin = job["started"] if isinstance(job, dict) else started
            self._search_ms = int((time.perf_counter() - origin) * 1000)

    def _search(self) -> None:
        """DuckDuckGo lookup on the health host. This route does not decode."""
        if node_role() != "health":
            self._error("search is served on the health host", status=403)
            return
        row = self._read_json(
            cap=SEARCH_BODY_CAP,
            label="search body",
            oversize="search query is too long",
            empty_ok=True,
        )
        if row is None:
            return
        query = str(row.get("q") or row.get("query") or "")
        found = lookup_web(query)
        body = json.dumps(
            {
                "status": found.get("status") if isinstance(found, dict) else "failed",
                "sources": (found.get("sources") if isinstance(found, dict) else [])
                or [],
                "context": (found.get("context") if isinstance(found, dict) else "")
                or "",
            }
        ).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)
