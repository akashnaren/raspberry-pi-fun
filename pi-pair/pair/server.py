"""HTTP UI, OpenAI-compatible /v1/chat/completions, and the keyed public API."""

from __future__ import annotations

import gzip
import ipaddress
import json
import os
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pair.abilities import (
    JSON_RETRY,
    clean_reply,
    needs_json_retry,
    settle_blocks,
    tail_hints,
    tool_notes,
)
from pair.assist import (
    is_harmful,
    scrub_reply,
    settle_reply,
    stream_release,
)
from pair.moderate import moderate
from pair.cancel import Cancel, ClientGone, peer_closed
from pair.chat import (
    chat_llamacpp,
    chat_ollama,
    llamacpp_model,
    start_model_warm,
    warm_in_flight,
)
from pair.config import STATIC_DIR
from pair.docfit import fit_outbound
from pair.errors import (
    ASK_FIRST,
    BUSY,
    FLASH_WARMING,
    GENERIC,
    MODEL_MISSING,
    TOO_BIG,
    WAITING,
    friendly_body,
    friendly_error,
)
from pair.guard import PI4_MISS_DOWN, may_generate, weak_brain_error
from pair.health import COOLING_NOTE, board_thermal, snapshot_peers
from pair.thermal import sample as thermal_sample
from pair.knobs import decode_effort, inference_knobs, mode_limits, search_note_limit
from pair.modes import (
    mode_table,
    mode_tips,
    pull_needed,
    resolve_auto,
    resolve_mode,
    tag_ready,
)
from pair.peers import pick
from pair.preload import (
    resident_models,
    schedule_pro_warm,
    start_pro_warm,
    wait_for_resident,
)
from pair.public_api import (
    FLASH_MODE,
    apply_mode,
    authorize,
    flash_checkpoint,
    openapi_bytes,
    stamp,
    swagger_html,
)
from pair.queue import append_row, apply_label, node_role, note_exchange
from pair.mesh import lookup_for_brain
from pair.search import lookup_web
from pair import runtime
from pair.stream import iter_ollama_channels, stream_llamacpp, stream_ollama
from pair.think import decode_plan, peel_think
from pair.timing import assemble, present
from pair.turn import (
    is_structured_request,
    needs_web,
    prepare_search_note,
    public_failure,
    shape_messages,
    structure_hint,
    turns_for_memory,
)
from pair.upload import UploadRejected, ingest, read_limited

FLASH_SOURCE_CAP = 3
PRO_SOURCE_CAP = 8
SEARCH_BUDGET_S = 4.0
KEEPALIVE_S = 5.0
SEARCH_BODY_CAP = 4096
CHAT_BODY_CAP = 1_000_000
_DRAIN_CAP = 8 * 1024 * 1024
_CHAT_ROLES = {"system", "user", "assistant"}


class DecodeFailed(Exception):
    """The model did not return an answer. The message is safe to show."""


_LAST_LOCK = threading.Lock()
_LAST = {"prompt": "", "answer": "", "chip": "", "peer": ""}


def remember_completion(prompt: str, answer: str, chip: str, peer: str) -> None:
    text = (prompt or "").strip()
    reply = (answer or "").strip()
    if not text or not reply:
        return
    with _LAST_LOCK:
        _LAST["prompt"] = text
        _LAST["answer"] = reply
        _LAST["chip"] = chip
        _LAST["peer"] = peer


def last_completion() -> dict:
    with _LAST_LOCK:
        return dict(_LAST)


_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".txt": "text/plain; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
}


def encoded_body(handler, body: bytes) -> tuple[bytes, str | None]:
    """Gzip only when the browser asks. Tests that omit the header stay plain."""
    accept = handler.headers.get("Accept-Encoding") or ""
    if "gzip" not in accept.lower() or len(body) < 800:
        return body, None
    packed = gzip.compress(body, compresslevel=6)
    if len(packed) >= len(body):
        return body, None
    return packed, "gzip"


def safe_write(handler, body: bytes, flush: bool = False) -> bool:
    lock = getattr(handler, "_sse_lock", None)

    def _write() -> bool:
        try:
            handler.wfile.write(body)
            if flush:
                handler.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return False

    if lock is None:
        ok = _write()
    else:
        with lock:
            ok = _write()
    if not ok:
        cancel = getattr(handler, "_cancel", None)
        if cancel is not None:
            cancel.set()
    return ok


def static_file(url_path: str) -> Path | None:
    if not url_path.startswith("/static/"):
        return None
    name = url_path[len("/static/") :]
    parts = name.split("/")
    if not parts or any(part in ("", ".", "..") or "\\" in part for part in parts):
        return None
    if len(parts) == 2 and parts[0] == "fonts":
        rel = parts
    elif len(parts) == 1:
        rel = parts
    else:
        return None
    root = STATIC_DIR.resolve()
    candidate = root.joinpath(*rel).resolve()
    allowed = {root, (root / "fonts").resolve()}
    if candidate.parent not in allowed or not candidate.is_file():
        return None
    return candidate


def _forward_tool(path: str, payload: dict) -> tuple[int, dict] | None:
    """Send a render call to pi2 or pi3. Off unless PI_PAIR_TOOL_FORWARD=1."""
    if os.environ.get("PI_PAIR_TOOL_FORWARD") != "1":
        return None
    from pair.tools import Dispatcher, ToolError, registry

    names = {"/tools/render_doc": "render_doc", "/tools/render_chart": "render_chart"}
    tool_name = names.get(path)
    if tool_name is None or tool_name not in registry():
        return None
    peers = {
        str(peer.get("name") or ""): peer
        for peer in runtime.PEERS
        if str(peer.get("name") or "") in ("pi2", "pi3")
    }
    if not peers:
        return None

    def invoke(node: str, tool, body: dict) -> dict:
        peer = peers.get(node)
        if not peer:
            raise ToolError(node)
        url = f"http://{peer['host']}:{int(peer['port'])}{tool.path}"
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=tool.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        return 200, Dispatcher(invoke).call(tool_name, payload)
    except (
        ToolError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        OSError,
    ):
        return None


def message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "\n".join(parts)
    return str(content or "")


def _named_can_generate(target: str) -> bool:
    named = next((peer for peer in runtime.PEERS if peer["name"] == target), None)
    return named is not None and may_generate(named)


def one_user_turn(messages) -> bool:
    """The canned map is for a single new line. A session with history stays on pi4."""
    users = 0
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role") or "user"
        if role == "assistant":
            return False
        if role == "user":
            users += 1
            if users > 1:
                return False
    return True


def last_user_text(messages) -> str:
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        if message.get("role", "user") == "user":
            return message_text(message.get("content"))
    return ""


def _block(text: str) -> str:
    """Replacement from `moderate`, or empty when that span is allowed."""
    verdict = moderate(text or "")
    return verdict.replacement if verdict.refused else ""


def brain_chat_url() -> str:
    """pi4's page, not its Ollama port. Search and decode both happen there."""
    peer = next((item for item in runtime.PEERS if item.get("name") == "pi4"), None)
    if peer is None or not may_generate(peer):
        raise RuntimeError(PI4_MISS_DOWN)
    port = int(os.environ.get("PI_PAIR_BRAIN_PORT", "18080"))
    return f"http://{peer['host']}:{port}/v1/chat/completions"


def relay_chat(
    payload: bytes, target: str, mesh: str, mode: str = ""
) -> tuple[int, dict[str, str], bytes]:
    """Hand the chat to pi4. This relay does not search and does not generate."""
    headers = {
        "content-type": "application/json",
        "X-Pi-Target": target or "auto",
        "X-Pi-Mesh": mesh or "on",
    }
    if mode:
        headers["X-Pi-Mode"] = mode
    request = urllib.request.Request(
        brain_chat_url(),
        data=payload,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
            headers = {
                "content-type": response.headers.get(
                    "content-type", "application/json"
                ),
            }
            for name in (
                "X-Pi-Peer",
                "X-Pi-Chip",
                "X-Pi-Think",
                "X-Pi-Search",
                "X-Pi-Mode",
                "X-Pi-Route",
            ):
                value = response.headers.get(name)
                if value:
                    headers[name] = value
            return response.status, headers, raw
    except urllib.error.HTTPError as error:
        raw = friendly_body(error.read())
        return error.code, {"content-type": "application/json"}, raw
    except Exception:
        raise RuntimeError(PI4_MISS_DOWN) from None


def _tuned_knobs(model: str) -> dict:
    """File knobs with this mode's context, threads, and batch."""
    row = inference_knobs()
    tuned = dict(row)
    tuned.update(mode_limits(str(model or ""), row))
    return tuned


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


def _prompt_note(search_note) -> str:
    if not isinstance(search_note, dict):
        return ""
    return str(search_note.get("prompt_note") or "")


def _apply_model_sample(handler, model: str, temperature: float, max_tokens: int):
    """Flash and Pro use different temperatures once the tag is final."""
    plan = getattr(handler, "_decode_plan", None)
    if plan is None:
        return temperature, max_tokens
    table = mode_table()
    pro_tag = str(table.get("pro") or "")
    flash_tag = str(table.get("flash") or "")
    name = str(model or "")
    pro = bool(pro_tag) and name == pro_tag and name != flash_tag
    tuned = decode_plan(getattr(plan, "name", ""), pro=pro)
    if tuned is None:
        return temperature, max_tokens
    handler._decode_plan = tuned
    return tuned.temperature, tuned.num_predict


def listed_chat_models() -> list[str] | None:
    """Tags Ollama reported for pi4. None when the board is down or the list is empty."""
    for snap in snapshot_peers():
        if snap.get("name") != "pi4" or not snap.get("ok"):
            continue
        names = [str(item) for item in (snap.get("models") or []) if item]
        return names or None
    return None


def mode_fields(mode: str, route: str, resident: str = "") -> dict:
    """Route fields for the page. Residency is never a swap plan."""
    del resident
    extra: dict[str, str] = {}
    if mode:
        extra["pi_mode"] = mode
    if route:
        extra["pi_route"] = route
    return extra


def status_event(stage: str, extra: dict | None = None) -> dict:
    """One SSE object for a stage the server has actually entered."""
    payload = {
        "id": "pi-pair",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
        "pi_status": stage,
    }
    if extra:
        payload.update(extra)
    return payload


def apply_tier(handler, payload: dict) -> dict:
    """Keyed API keeps its public mode stamp. The LAN page adds pi_mode only."""
    public = getattr(handler, "public_mode", "") or ""
    stamp(payload, public)
    tier = getattr(handler, "pi_mode", "") or ""
    if tier and not public and isinstance(payload, dict):
        payload["pi_mode"] = tier
    return payload


def write_event(handler, payload: dict) -> bool:
    apply_tier(handler, payload)
    return safe_write(handler, f"data: {json.dumps(payload)}\n\n".encode(), flush=True)


_BOOTED = time.monotonic()


def _service_row(peers: list, role: str, name: str) -> dict:
    match = None
    for peer in peers:
        if not isinstance(peer, dict):
            continue
        if peer.get("role") == role or peer.get("name") == name:
            match = peer
            break
    if match is None:
        return {"name": name, "ok": False, "latency_ms": None}
    return {
        "name": name,
        "ok": bool(match.get("ok")),
        "latency_ms": match.get("latency_ms"),
    }


def _claim_wait(slot: dict, cancel=None, on_tick=None) -> bool:
    """Turn a queue reservation into a generation slot. False means the wait ran out."""
    slot["waiting"] = False
    if runtime.gate.acquire_reserved(cancel=cancel, on_tick=on_tick):
        slot["held"] = True
        return True
    return False


_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_PUBLIC_HIDDEN = {"x-pi-peer", "x-pi-chip"}


def _lan_address(host: str) -> bool:
    """True for loopback and RFC 1918. Anything else is treated as public."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    return any(ip.version == net.version and ip in net for net in _RFC1918)


def health_document() -> dict:
    peers = snapshot_peers()
    up = sum(1 for peer in peers if isinstance(peer, dict) and peer.get("ok"))
    table = mode_table()
    pro_model = str(table.get("pro") or "").strip()
    doc = {
        "ok": True,
        "model": runtime.MODEL,
        "pro_model": pro_model,
        "mode": "flash",
        "modes": table,
        "peers_up": up,
        "peers": peers,
        "slots": runtime.INFER_SLOTS,
        "in_flight": runtime.gate.in_flight(),
        "waiting": runtime.gate.waiting(),
        "cache_ttl": runtime.HEALTH_CACHE_TTL,
        "uptime_s": max(0, int(time.monotonic() - _BOOTED)),
        "cooling": COOLING_NOTE,
        "memory": _memory_stats(),
        "services": {
            "brain": _service_row(peers, "brain", "pi4"),
            "search": _service_row(peers, "health", "pi2"),
            "peers_up": up,
            "peers": len(peers),
        },
    }
    doc.update(board_thermal())
    temp = thermal_sample().get("temp_c")
    if temp is not None:
        doc["temp_c"] = temp
    return doc


def _memory_stats() -> dict:
    try:
        from pair import memory

        return memory.stats("")
    except Exception:
        return {"used": 0, "num_ctx": 0, "compactions": 0, "last_compact_ms": 0}


def _memory_prompt(scope: str) -> tuple[str, str]:
    if not scope:
        return "", ""
    try:
        from pair import memory

        return memory.facts_block(scope), memory.summary_text(scope)
    except Exception:
        return "", ""


def _recall_context(handler, messages, prompt: str) -> str:
    """Facts, the summary, and earlier turns. The current question is not a source."""
    facts, summary = _memory_prompt(handler._memory_scope())
    question = " ".join((prompt or "").split())
    parts = [facts, summary]
    for row in messages or []:
        if not isinstance(row, dict) or row.get("role") == "system":
            continue
        text = " ".join(str(row.get("content") or "").split())
        if not text or text == question:
            continue
        parts.append(text)
    return "\n".join(part for part in parts if part)


def _searched(search_note) -> bool:
    """True when this turn already received a search result."""
    return isinstance(search_note, dict) and search_note.get("status") == "ok"


def _source_count(search_note) -> int:
    if not isinstance(search_note, dict):
        return 0
    sources = search_note.get("sources")
    return len(sources) if isinstance(sources, list) else 0


def public_health(doc: dict) -> dict:
    """What a tunnel may show. Names, hosts, ports, roles, and latency stay off."""
    services: dict = {}
    for key, value in (doc.get("services") or {}).items():
        if isinstance(value, dict) and "ok" in value:
            services[key] = bool(value.get("ok"))
        elif isinstance(value, bool):
            services[key] = value
    peers = []
    for peer in doc.get("peers") or []:
        if not isinstance(peer, dict):
            continue
        peers.append({"ok": bool(peer.get("ok"))})
    shown = {
        "ok": bool(doc.get("ok")),
        "slots": doc.get("slots"),
        "in_flight": doc.get("in_flight"),
        "waiting": doc.get("waiting", runtime.gate.waiting()),
        "uptime_s": doc.get("uptime_s"),
        "cooling": COOLING_NOTE,
        "peers_up": doc.get("peers_up"),
        "services": services,
        "peers": peers,
    }
    return shown


def index_body() -> bytes:
    """Fill the page from config: Flash and Pro tip text. Model tags stay off it."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    tips = mode_tips()
    html = html.replace("__FLASH_TIP__", tips["flash"])
    html = html.replace("__PRO_TIP__", tips["pro"])
    return html.encode("utf-8")


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


def _canonical_origin(value: str) -> str:
    raw = (value or "").strip()
    if not raw or raw.lower() == "null" or any(ch in raw for ch in "\r\n\x00"):
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return ""
    if parsed.username or parsed.password:
        return ""
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return ""
    if parsed.path not in ("", "/") or parsed.query or parsed.params or parsed.fragment:
        return ""
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
    return f"{parsed.scheme}://{netloc}"


def allowed_api_origin(origin: str, host: str, allowlist: str = "") -> str:
    """Origin to echo on /api/*, or empty when the browser must not be allowed.

    The page's own host matches without a setting. PI_PAIR_CORS_ORIGINS adds
    more, comma-separated. There is no wildcard on these routes.
    """
    echo = _canonical_origin(origin)
    if not echo:
        return ""
    for item in (allowlist or "").split(","):
        if item.strip() and _canonical_origin(item) == echo:
            return echo
    request_host = (host or "").strip().lower()
    if request_host and urlparse(echo).netloc.lower() == request_host:
        return echo
    return ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_response(self, code: int, message: str | None = None) -> None:
        self._sent_pi: set[str] = set()
        super().send_response(code, message)

    def send_header(self, keyword: str, value: str) -> None:
        name = str(keyword)
        if name.lower().startswith("x-pi-"):
            key = name.lower()
            sent = getattr(self, "_sent_pi", None)
            if sent is None:
                sent = set()
                self._sent_pi = sent
            if key in sent:
                return
            if self._public() and key in _PUBLIC_HIDDEN:
                return
            sent.add(key)
        super().send_header(keyword, value)

    def _public(self) -> bool:
        """True on the Cloudflare tunnel, or when the client is not on the LAN."""
        if self.headers.get("Cf-Ray") or self.headers.get("Cf-Connecting-Ip"):
            return True
        loop = (self.headers.get("Cdn-Loop") or "").lower()
        if "cloudflare" in loop:
            return True
        host = str(self.client_address[0]) if self.client_address else ""
        return not _lan_address(host)

    def _api_route(self) -> bool:
        path = (self.path or "").split("?", 1)[0]
        return path == "/api" or path.startswith("/api/")

    def _cors(self) -> None:
        if self._api_route():
            origin = allowed_api_origin(
                self.headers.get("Origin", ""),
                self.headers.get("Host", ""),
                os.environ.get("PI_PAIR_CORS_ORIGINS", ""),
            )
            self.send_header("Vary", "Origin")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, X-API-Key",
            )
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        public = getattr(self, "public_mode", "") or ""
        tier = getattr(self, "pi_mode", "") or ""
        if public:
            self.send_header("X-Pi-Mode", public)
            self.send_header("X-Pi-Model", FLASH_MODE)
        elif tier:
            self.send_header("X-Pi-Mode", tier)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            body, encoding = encoded_body(self, index_body())
            self.send_response(200)
            self._cors()
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            if encoding:
                self.send_header("Content-Encoding", encoding)
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            safe_write(self, body)
            return
        asset = static_file(path)
        if asset is not None:
            body, encoding = encoded_body(self, asset.read_bytes())
            self.send_response(200)
            self._cors()
            self.send_header(
                "content-type", _TYPES.get(asset.suffix, "application/octet-stream")
            )
            self.send_header("Cache-Control", "no-cache")
            if encoding:
                self.send_header("Content-Encoding", encoding)
                self.send_header("Vary", "Accept-Encoding")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            safe_write(self, body)
            return
        if path in ("/openapi.json", "/swagger.json"):
            self._openapi()
            return
        if path in ("/docs", "/docs/", "/swagger", "/swagger/"):
            self._docs()
            return
        if path == "/tools/health":
            self._tool_health()
            return
        if path == "/v1/memory":
            self._memory_get()
            return
        if path.startswith("/v1/files/"):
            self._file_get(path[len("/v1/files/") :])
            return
        if path == "/api/health":
            self._api_health()
            return
        if path.startswith("/health") or path.startswith("/peers"):
            doc = health_document()
            if path.startswith("/health") and self._public():
                doc = public_health(doc)
            body = json.dumps(doc).encode()
            self.send_response(200)
            self._cors()
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            safe_write(self, body)
            return
        self.send_response(404)
        self.end_headers()

    def _drain_body(self, length: int) -> None:
        """Read a rejected body so the client is not left hanging. Over 8 MB, skip it."""
        if length <= 0 or length > _DRAIN_CAP:
            return
        try:
            self.connection.settimeout(10)
        except OSError:
            pass
        left = length
        while left > 0:
            try:
                chunk = self.rfile.read(min(65536, left))
            except (TimeoutError, OSError):
                return
            if not chunk:
                return
            left -= len(chunk)

    def _read_json(
        self,
        cap: int = CHAT_BODY_CAP,
        label: str = "chat body",
        oversize: str = "",
        empty_ok: bool = False,
    ) -> dict | None:
        """Read one JSON object, or send 400/413 and return None."""
        raw_length = self.headers.get("content-length")
        if raw_length is None or str(raw_length).strip() == "":
            length = 0
        else:
            try:
                length = int(str(raw_length).strip())
            except ValueError:
                self._error(f"{label} must be JSON", status=400)
                return None
        if length < 0:
            self._error(f"{label} must be JSON", status=400)
            return None
        if length > cap:
            self._drain_body(length)
            self._error(oversize or TOO_BIG, status=413)
            return None
        if length == 0 and not empty_ok:
            self._error(f"{label} must be JSON", status=400)
            return None
        try:
            blob = self.rfile.read(length) if length else b""
            text = blob.decode("utf-8")
            data = json.loads(text or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(f"{label} must be JSON", status=400)
            return None
        if not isinstance(data, dict):
            self._error(f"{label} must be an object", status=400)
            return None
        return data

    def _validate_chat(self, data: dict) -> bool:
        """False after a 400. A chat needs one non-empty user message."""
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            self._error(ASK_FIRST, status=400)
            return False
        for message in messages:
            if not isinstance(message, dict):
                self._error(ASK_FIRST, status=400)
                return False
            role = message.get("role", "user")
            if role not in _CHAT_ROLES:
                self._error(ASK_FIRST, status=400)
                return False
            content = message.get("content")
            if not isinstance(content, (str, list)):
                self._error(ASK_FIRST, status=400)
                return False
        if not last_user_text(messages).strip():
            self._error(ASK_FIRST, status=400)
            return False
        return True

    def do_POST(self) -> None:
        try:
            self._dispatch_post()
        except Exception:
            self._error(GENERIC, status=500)

    def _dispatch_post(self) -> None:
        path = self.path.split("?")[0]
        if path == "/v1/search":
            self._search()
            return
        if path.startswith("/tools/"):
            self._tool(path)
            return
        if path == "/v1/attachments":
            self._attachment()
            return
        if path == "/v1/flywheel/enqueue":
            self._enqueue()
            return
        if path == "/v1/flywheel/feedback":
            self._feedback()
            return
        if path == "/api/chat":
            self._api_chat()
            return
        if path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        data = self._read_json()
        if data is None or not self._validate_chat(data):
            return
        self._serve_chat(data, json.dumps(data).encode())

    def _api_chat(self) -> None:
        rejected = authorize(self.headers)
        if rejected is not None:
            self._reject_api(*rejected)
            return
        data = self._read_json()
        if data is None or not self._validate_chat(data):
            return
        try:
            self.public_mode = apply_mode(data)
        except ValueError as error:
            self._error(str(error), status=400)
            return
        self._serve_chat(data, json.dumps(data).encode())

    def _api_health(self) -> None:
        rejected = authorize(self.headers)
        if rejected is not None:
            self._reject_api(*rejected)
            return
        body_obj = health_document()
        if self._public():
            body_obj = public_health(body_obj)
        body_obj["public_model"] = FLASH_MODE
        body_obj["default_mode"] = FLASH_MODE
        body_obj["checkpoint"] = flash_checkpoint()
        body = json.dumps(body_obj).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _openapi(self) -> None:
        body = openapi_bytes()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _docs(self) -> None:
        body = swagger_html()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _reject_api(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        self.send_response(status)
        self._cors()
        if status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="pi-gpt"')
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _reset_timing(self) -> None:
        self._queue_ms = 0
        self._search_ms = 0
        self._usage = {}
        self._ttft_ms = 0
        self._queue_t0 = None

    def _mark_queue(self) -> None:
        started = getattr(self, "_queue_t0", None)
        if started is None:
            return
        self._queue_ms = int((time.perf_counter() - started) * 1000)

    def _timed_search(self, *args, job=None, **kwargs):
        started = time.perf_counter()
        try:
            return _with_search(*args, job=job, **kwargs)
        finally:
            origin = job["started"] if isinstance(job, dict) else started
            self._search_ms = int((time.perf_counter() - origin) * 1000)

    def _attach_timing(self, payload: dict, started: float) -> None:
        timing = assemble(
            queue_ms=getattr(self, "_queue_ms", 0),
            search_ms=getattr(self, "_search_ms", 0),
            usage=getattr(self, "_usage", None),
            total_ms=int((time.time() - started) * 1000),
            ttft_ms=getattr(self, "_ttft_ms", 0),
        )
        payload["pi_timing"] = present(timing, self._public())

    def _effort_name(self) -> str:
        """Low, Medium, or High when the turn named one. Empty otherwise."""
        plan = getattr(self, "_decode_plan", None)
        return str(getattr(plan, "name", "") or "")

    def _chosen_mode(self, data: dict) -> str:
        """LAN mode word. Thinking levels are not model choices."""
        if getattr(self, "public_mode", ""):
            return ""
        chosen = (self.headers.get("X-Pi-Mode") or "").strip()
        if not chosen:
            body_mode = data.get("mode", None)
            if body_mode is None:
                body_mode = data.get("pi_mode", None)
            chosen = "" if body_mode is None else str(body_mode)
        picked = chosen.strip().lower()
        if picked in {"low", "medium", "high"}:
            return ""
        return picked

    def _bind_tier(self, data: dict, prompt: str, think_name: str = "") -> str:
        """LAN flash/pro/auto. The keyed API already pinned the Flash checkpoint.

        Empty and unknown modes clamp to the Flash or Pro tag from mode_table.
        Auto uses that same table. Nothing here unloads a resident tag.
        """
        picked = self._chosen_mode(data)
        data.pop("mode", None)
        data.pop("pi_mode", None)
        if getattr(self, "public_mode", ""):
            self.pi_mode = ""
            self.pi_route = ""
            return str(data.get("model") or runtime.MODEL)
        if picked == "auto":
            route, model, _reason = resolve_auto(prompt, listed_chat_models())
            # High is a longer Flash answer. Stay on Flash unless Pro was chosen.
            if think_name == "high":
                route, model = "flash", mode_table()["flash"]
            self.pi_mode = "auto"
            self.pi_route = route
            return model
        mode_name, model = resolve_mode(picked, data.get("model") or runtime.MODEL)
        self.pi_mode = mode_name
        self.pi_route = mode_name
        return model

    def _remember_canned_mode(self, data: dict) -> tuple[str, str]:
        """Name the page mode on a map hit without asking Ollama which tags exist."""
        picked = self._chosen_mode(data)
        data.pop("mode", None)
        data.pop("pi_mode", None)
        if getattr(self, "public_mode", ""):
            self.pi_mode = ""
            self.pi_route = ""
            return "", ""
        if picked == "auto":
            self.pi_mode = "auto"
            self.pi_route = "canned"
            return "auto", "canned"
        mode_name, _model = resolve_mode(picked, data.get("model") or runtime.MODEL)
        self.pi_mode = mode_name
        self.pi_route = "canned"
        return mode_name, "canned"

    def _policy_refusal(
        self, prompt: str, want_stream: bool, started: float, answer: str
    ) -> None:
        """Fixed refusal from the moderation hook. No model, search, or list hint."""
        remember_completion(prompt, answer, "policy", "policy")
        elapsed = int((time.time() - started) * 1000)
        if not want_stream:
            resp = {
                "id": "pi-pair",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "pi_peer": "policy",
                "pi_chip": "policy",
                "pi_ms": elapsed,
                "pi_model": "policy",
                "pi_kind": "policy",
                "pi_stages": ["answering"],
            }
            apply_tier(self, resp)
            body = json.dumps(resp).encode()
            self.send_response(200)
            self._cors()
            self.send_header("content-type", "application/json")
            self.send_header("X-Pi-Peer", "policy")
            self.send_header("X-Pi-Chip", "policy")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            safe_write(self, body)
            return
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Pi-Peer", "policy")
        self.send_header("X-Pi-Chip", "policy")
        self.end_headers()
        answering = {"pi_stages": ["answering"]}
        write_event(self, status_event("answering", answering))
        chunk = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": answer},
                    "finish_reason": None,
                }
            ],
            "pi_peer": "policy",
            "pi_chip": "policy",
            "pi_model": "policy",
            "pi_kind": "policy",
            "pi_stages": ["answering"],
        }
        apply_tier(self, chunk)
        safe_write(self, f"data: {json.dumps(chunk)}\n\n".encode(), flush=True)
        final = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "pi_peer": "policy",
            "pi_chip": "policy",
            "pi_ms": elapsed,
            "pi_model": "policy",
            "pi_kind": "policy",
            "pi_stages": ["answering"],
        }
        apply_tier(self, final)
        safe_write(self, f"data: {json.dumps(final)}\n\n".encode(), flush=True)
        safe_write(self, b"data: [DONE]\n\n", flush=True)

    def _begin_cancel(self) -> None:
        """Watch the client socket, and replace any earlier turn with this id."""
        self._cancel = Cancel()
        self._watch_stop = threading.Event()
        request_id = (self.headers.get("X-Pi-Request-Id") or "").strip()
        self._request_id = request_id
        if request_id:
            previous = runtime.requests.get(request_id)
            if previous is not None and previous is not self._cancel:
                previous.set()
            runtime.requests[request_id] = self._cancel

        def watch() -> None:
            while not self._watch_stop.wait(1.0):
                try:
                    gone = peer_closed(self.connection)
                except Exception:
                    gone = True
                if gone:
                    self._cancel.set()
                    return

        threading.Thread(target=watch, name="client-watch", daemon=True).start()

    def _end_cancel(self) -> None:
        stop = getattr(self, "_watch_stop", None)
        if stop is not None:
            stop.set()
        request_id = getattr(self, "_request_id", "")
        cancel = getattr(self, "_cancel", None)
        if request_id and cancel is not None:
            if runtime.requests.get(request_id) is cancel:
                runtime.requests.pop(request_id, None)

    def _serve_chat(self, data: dict, raw: bytes) -> None:
        self._begin_cancel()
        try:
            self._serve_chat_body(data, raw)
        except ClientGone:
            return
        finally:
            self._end_cancel()

    def _serve_chat_body(self, data: dict, raw: bytes) -> None:
        if not isinstance(data, dict):
            self._error("chat body must be an object", status=400)
            return
        target = (
            self.headers.get("X-Pi-Target") or data.pop("pi_target", None) or "auto"
        ).strip()
        mesh = (
            self.headers.get("X-Pi-Mesh") or data.pop("pi_mesh", None) or "on"
        ).strip().lower() not in (
            "0",
            "off",
            "false",
            "no",
        )
        messages = data.get("messages") or []
        prompt = last_user_text(messages)
        effort = decode_effort(str(data.pop("think", "") or ""), prompt)
        self._decode_plan = effort
        self._last_reasoning = ""
        if effort:
            think_name = effort.name
            temperature = effort.temperature
            max_tokens = effort.num_predict
            data.pop("temperature", None)
            data.pop("max_tokens", None)
            data.pop("max_completion_tokens", None)
        else:
            think_name = ""
            temperature = float(
                data.get("temperature") if data.get("temperature") is not None else 0.7
            )
            max_tokens = int(
                data.get("max_tokens") or data.get("max_completion_tokens") or 256
            )
        started = time.time()
        want_stream = bool(data.get("stream"))
        self._searched = False
        self._reset_timing()
        refused = _block(prompt)
        if refused:
            self._policy_refusal(prompt, want_stream, started, refused)
            return
        try:
            if target and target != "auto":
                named = next(
                    (peer for peer in runtime.PEERS if peer["name"] == target), None
                )
                if named is not None and not may_generate(named):
                    raise RuntimeError(weak_brain_error(named["name"]))
        except ClientGone:
            raise
        except Exception as error:
            self._error(str(error))
            return
        if node_role() != "brain":
            self._relay_to_brain(raw)
            return
        # Pro must already be on disk. A missing tag is not a pull, and it does
        # not take a generation slot. Auto names a mode_table tag and does not
        # plan a swap.
        try:
            model = self._bind_tier(data, prompt, think_name)
            mode_name = getattr(self, "pi_mode", "") or ""
            route_name = getattr(self, "pi_route", "") or ""
            resident_name = ""
            peer = pick(target, mesh, model)
            outbound = [
                {
                    "role": message.get("role", "user"),
                    "content": message_text(message.get("content", "")),
                }
                for message in messages
                if isinstance(message, dict)
            ]
            kind = peer.get("kind") or "ollama"
            used = llamacpp_model(peer, model) if kind == "llamacpp" else model
            structured = is_structured_request(prompt)
            self._local_context = _recall_context(self, outbound, prompt)
            do_search = (
                bool(mesh and node_role() == "brain")
                and not structured
                and needs_web(
                    prompt,
                    follow_up=not one_user_turn(messages),
                    context=self._local_context,
                )
            )
            tuned = _tuned_knobs(used if kind != "llamacpp" else model)
            ctx = int(tuned.get("num_ctx") or 2048)
            outbound = fit_outbound(outbound, num_ctx=ctx, reply_tokens=max_tokens)
            hint = tail_hints(prompt, structure_hint(prompt) or "")
            search_note = None
            search_job = _begin_lookup(prompt, model) if do_search else None
            if do_search and os.environ.get("PI_PAIR_PREFIX_PRIME") == "1":
                from pair.tools import schedule_prefix_prime

                schedule_prefix_prime(outbound)
            grounded = None
        except ClientGone:
            raise
        except Exception as error:
            self._error(str(error))
            return
        if kind != "llamacpp":
            if route_name == "pro" and not tag_ready(
                peer.get("models") or [], "pro", model
            ):
                self._error(pull_needed(model))
                return
            host = str(peer.get("host") or "127.0.0.1")
            try:
                peer_port = int(peer.get("port") or 0)
            except (TypeError, ValueError):
                peer_port = 0
            resident = resident_models(host, peer_port) if peer_port else None
            # A chat against a tag /api/ps does not list would cold-load it.
            # Warm that tag in the background. Use Flash only when it is already resident.
            if resident is not None and model not in resident:
                schedule_pro_warm(host, peer_port, model)
                flash_tag = str(mode_table().get("flash") or "")
                if flash_tag and flash_tag in resident and model != flash_tag:
                    model = flash_tag
                    used = model
                    route_name = "flash"
                    self.pi_route = "flash"
                elif route_name != "pro":
                    if not wait_for_resident(host, peer_port, model):
                        self._warming(want_stream, FLASH_WARMING, started)
                        return
                else:
                    self._error(MODEL_MISSING)
                    return
        temperature, max_tokens = _apply_model_sample(
            self, model, temperature, max_tokens
        )
        self._answer_cap = int(max_tokens or 0)
        use_model = grounded is None
        slot = {"held": False, "waiting": False}
        if use_model:
            self._queue_t0 = time.perf_counter()
            outcome = runtime.gate.reserve(self._client_key())
            if outcome == "ready":
                self._mark_queue()
            if outcome == "full":
                self._error(
                    BUSY,
                    status=503,
                    headers={"Retry-After": str(runtime.gate.retry_after_s())},
                )
                return
            if outcome == "ready":
                slot["held"] = True
            else:
                slot["waiting"] = True
                from pair.sched import overlap, prepare_prefix

                overlap(lambda: prepare_prefix(outbound))
        try:
            if want_stream:
                self._stream(
                    peer,
                    kind,
                    model,
                    used,
                    outbound,
                    temperature,
                    max_tokens,
                    started,
                    prompt,
                    think_name,
                    do_search,
                    searched=False,
                    search_note=None,
                    mode_name=mode_name,
                    route_name=route_name,
                    resident_name=resident_name,
                    ready_answer=grounded,
                    slot=slot,
                    search_job=search_job,
                )
            else:
                if slot["waiting"] and not _claim_wait(
                    slot, cancel=getattr(self, "_cancel", None)
                ):
                    self._error(
                        BUSY,
                        status=503,
                        headers={"Retry-After": str(runtime.gate.retry_after_s())},
                    )
                    return
                if slot["held"]:
                    self._mark_queue()
                if do_search:
                    self._searched = True
                    outbound, search_note = self._timed_search(
                        outbound,
                        prompt,
                        model,
                        getattr(self, "_cancel", None),
                        job=search_job,
                    )
                fact_text, summary_text = _memory_prompt(self._memory_scope())
                source_rows = list(outbound)
                outbound = shape_messages(
                    outbound,
                    prompt,
                    tuned,
                    think_name,
                    _prompt_note(search_note),
                    hints=hint,
                    facts=fact_text,
                    summary=summary_text,
                )
                memory_rows = turns_for_memory(source_rows, outbound)
                stages = ["thinking"]
                if do_search:
                    stages.append("searching")
                stages.append("answering")
                self._complete(
                    peer,
                    kind,
                    model,
                    outbound,
                    temperature,
                    max_tokens,
                    started,
                    prompt,
                    think_name,
                    search_note,
                    stages,
                    mode_name,
                    route_name,
                    resident_name,
                    ready_answer=grounded,
                    memory_turns=memory_rows,
                )
        except ClientGone:
            raise
        except Exception as error:
            self._error(str(error))
        finally:
            self._observe_rates()
            if slot["held"]:
                runtime.gate.release()
            elif slot["waiting"]:
                runtime.gate.cancel_wait()

    def _relay_to_brain(self, payload: bytes) -> None:
        try:
            requested = json.loads(payload.decode() or "{}")
        except json.JSONDecodeError:
            requested = {}
        if isinstance(requested, dict) and requested.get("stream"):
            self._relay_stream(payload)
            return
        target = (self.headers.get("X-Pi-Target") or "auto").strip()
        mesh = (self.headers.get("X-Pi-Mesh") or "on").strip()
        try:
            status, headers, body = relay_chat(
                payload, target, mesh, getattr(self, "pi_mode", "") or ""
            )
        except RuntimeError as error:
            self._error(str(error))
            return
        mode = getattr(self, "public_mode", "") or ""
        kind = (headers.get("content-type") or "").split(";")[0].strip().lower()
        if mode and kind != "text/event-stream":
            try:
                parsed = json.loads(body.decode() or "{}")
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                apply_tier(self, parsed)
                body = json.dumps(parsed).encode()
                headers["content-type"] = "application/json"
                headers.pop("content-length", None)
        self.send_response(status)
        self._cors()
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _relay_stream(self, payload: bytes) -> None:
        """Pass pi4's event stream through. This relay does not search or generate."""
        target = (self.headers.get("X-Pi-Target") or "auto").strip()
        mesh = (self.headers.get("X-Pi-Mesh") or "on").strip()
        forward = {
            "content-type": "application/json",
            "X-Pi-Target": target or "auto",
            "X-Pi-Mesh": mesh or "on",
        }
        tier = getattr(self, "pi_mode", "") or ""
        if tier:
            forward["X-Pi-Mode"] = tier
        request = urllib.request.Request(
            brain_chat_url(),
            data=payload,
            headers=forward,
        )
        try:
            response = urllib.request.urlopen(request, timeout=180)
        except urllib.error.HTTPError as error:
            raw = friendly_body(error.read())
            self.send_response(error.code)
            self._cors()
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            safe_write(self, raw)
            return
        except Exception:
            self._error(PI4_MISS_DOWN)
            return
        try:
            self.send_response(response.status)
            self._cors()
            self.send_header(
                "content-type",
                response.headers.get("content-type", "text/event-stream"),
            )
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            for name in (
                "X-Pi-Peer",
                "X-Pi-Chip",
                "X-Pi-Think",
                "X-Pi-Search",
                "X-Pi-Mode",
                "X-Pi-Route",
            ):
                value = response.headers.get(name)
                if value:
                    self.send_header(name, value)
            self.end_headers()
            while True:
                chunk = response.read(1024)
                if not chunk:
                    break
                if not safe_write(self, chunk, flush=True):
                    break
        finally:
            close = getattr(response, "close", None)
            if close:
                close()

    def _attachment(self) -> None:
        """Text files are decoded. Images and JPEG-scanned PDFs are OCR'd first."""
        try:
            raw = read_limited(self.headers.get("content-length"), self.rfile.read)
            result = ingest(
                self.headers.get("content-type") or "",
                raw,
                filename=self.headers.get("x-filename") or "",
            )
        except UploadRejected as error:
            self._error(str(error), status=error.status)
            return
        body = json.dumps(result).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _touch_memory(self, messages) -> None:
        """Record the real prompt size and enqueue a summary for when the slot is free."""
        from pair.compact import schedule, should_compact
        from pair.context import ledger_for

        ctx = 2048
        try:
            ctx = int((_tuned_knobs("") or {}).get("num_ctx") or 2048)
        except Exception:
            ctx = 2048
        scope = self._memory_scope()
        book = ledger_for(scope or "anon", ctx)
        count = int((getattr(self, "_usage", {}) or {}).get("prompt_eval_count") or 0)
        if count:
            book.observe(count)
        else:
            text = "\n".join(
                str(row.get("content") or "")
                for row in messages or []
                if isinstance(row, dict)
            )
            book.note_estimate(text)
        if not scope or not should_compact(
            book.used(), book.num_ctx, runtime.gate.waiting() > 0
        ):
            return
        schedule(
            [row for row in messages or [] if isinstance(row, dict)],
            book.num_ctx,
            idle=lambda: True,
            scope=scope,
        )

    def _memory_get(self) -> None:
        from pair import memory

        scope = self._memory_scope()
        body = json.dumps(
            {
                "facts": memory.list_facts(scope) if scope else [],
                "summary": memory.summary_text(scope) if scope else "",
            }
        ).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _memory_delete(self, fact_id: str) -> None:
        from pair import memory

        scope = self._memory_scope()
        if scope and fact_id:
            memory.delete_fact(fact_id, scope=scope)
        elif scope:
            memory.clear_facts(scope)
        body = json.dumps(
            {"ok": True, "facts": memory.list_facts(scope) if scope else []}
        ).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def do_DELETE(self) -> None:
        path = self.path.split("?")[0]
        if path == "/v1/memory":
            self._memory_delete("")
            return
        if path.startswith("/v1/memory/"):
            self._memory_delete(path.split("/")[-1])
            return
        self.send_response(404)
        self.end_headers()

    def _tool_health(self) -> None:
        from pair.nodes.worker import health_body

        body = json.dumps(health_body()).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _tool(self, path: str) -> None:
        """pi2 and pi3 tools. pi4 may extract only as a last resort. No generation."""
        from pair.nodes.worker import GENERATION_PATHS, handle

        if path in GENERATION_PATHS:
            self._error("this node does not generate", status=404)
            return
        role = node_role()
        render = path in ("/tools/render_doc", "/tools/render_chart")
        if role == "brain" and path != "/tools/extract" and not render:
            self._error("tools run on pi2 or pi3", status=403)
            return
        row = self._read_json(label="tool body", empty_ok=True)
        if row is None:
            return
        payload_in = row if isinstance(row, dict) else {}
        if role == "brain" and render:
            status, payload = self._render_tool(path, payload_in)
        else:
            status, payload = handle(path, payload_in)
        body = json.dumps(payload).encode()
        self.send_response(status)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _render_tool(self, path: str, payload: dict) -> tuple[int, dict]:
        """Prefer pi2/pi3 when forwarding is on. Otherwise render here. Never a model call."""
        from pair.nodes.worker import handle

        forwarded = _forward_tool(path, payload)
        if forwarded is not None:
            return forwarded
        return handle(path, payload)

    def _file_get(self, doc_id: str) -> None:
        from pair.docs import open_document

        found = open_document(doc_id.split("/")[0])
        if found is None:
            self._error("file not found", status=404)
            return
        data, name, mime = found
        self.send_response(200)
        self._cors()
        self.send_header("content-type", mime)
        self.send_header("content-disposition", f'attachment; filename="{name}"')
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        safe_write(self, data)

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

    def _enqueue(self) -> None:
        if node_role() != "dataset":
            self._error("train queue is accepted only on the dataset host", status=403)
            return
        row = self._read_json(label="queue row", empty_ok=True)
        if row is None:
            return
        prompt = str(row.get("prompt") or "").strip()
        answer = str(row.get("answer") or "").strip()
        if not prompt or not answer:
            self._error("queue row needs prompt and answer", status=400)
            return
        kept = append_row(
            {
                "prompt": prompt,
                "answer": answer,
                "chip": row.get("chip") or "",
                "peer": row.get("peer") or "",
            }
        )
        body = json.dumps({"ok": True, "queued": kept}).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _feedback(self) -> None:
        row = self._read_json(label="feedback", empty_ok=True)
        if row is None:
            return
        prompt = str(row.get("prompt") or "").strip()
        answer = str(row.get("answer") or "").strip()
        chip = str(row.get("chip") or "")
        peer = str(row.get("peer") or "")
        if not prompt and not answer:
            last = last_completion()
            prompt = str(last.get("prompt") or "")
            answer = str(last.get("answer") or "")
            chip = chip or str(last.get("chip") or "")
            peer = peer or str(last.get("peer") or "")
        elif not prompt or not answer:
            self._error("feedback needs both prompt and answer, or neither", status=400)
            return
        try:
            result = apply_label(
                prompt,
                answer,
                str(row.get("vote") or ""),
                str(row.get("correction") or ""),
                chip=chip,
                peer=peer,
            )
        except ValueError as error:
            self._error(str(error), status=400)
            return
        if result.get("forwarded") and not result.get("ok"):
            self._error("dataset host did not accept the label", status=502)
            return
        stored = result.get("row") if isinstance(result.get("row"), dict) else {}
        body = json.dumps(
            {
                "ok": True,
                "labeled": True,
                "queued": result.get("queued") or 0,
                "prompt": stored.get("prompt") or prompt,
                "answer": stored.get("answer") or answer,
                "vote": stored.get("vote") or "",
                "correction": stored.get("correction") or "",
            }
        ).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def finish(self) -> None:
        stop = getattr(self, "_stop_beat", None)
        if stop is not None:
            stop.set()
        self._sse_lock = None
        super().finish()

    def _warming(self, want_stream: bool, message: str, started: float) -> None:
        """A cold Flash tag is a wait, not a 502 and not a larger-model error."""
        if want_stream:
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            chunk = {
                "id": "pi-pair",
                "object": "chat.completion.chunk",
                "choices": [
                    {"index": 0, "delta": {"content": message}, "finish_reason": None}
                ],
                "pi_detail": message,
            }
            safe_write(self, f"data: {json.dumps(chunk)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)
            return
        body = json.dumps(
            {
                "id": "pi-pair",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": message},
                        "finish_reason": "stop",
                    }
                ],
                "pi_detail": message,
                "pi_ms": int((time.time() - started) * 1000),
            }
        ).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)

    def _memory_scope(self) -> str:
        """Chat plus client. Missing both reads and writes nothing."""
        from pair.memory import scope_key

        headers = getattr(self, "headers", None)
        chat = client = ""
        if headers is not None:
            chat = str(headers.get("X-Pi-Chat") or "")
            client = str(headers.get("X-Pi-Client") or "")
        return scope_key(chat, client)

    def _client_key(self) -> str:
        """Header id when the caller set one, otherwise the socket address."""
        header = ""
        headers = getattr(self, "headers", None)
        if headers is not None:
            header = str(headers.get("X-Pi-Client") or "").strip()
        if header:
            return header[:80]
        address = getattr(self, "client_address", None)
        if not address:
            return ""
        return str(address[0])[:80]

    def _observe_rates(self) -> None:
        from pair.sched import observe_usage

        observe_usage(
            getattr(self, "_usage", None) or {},
            int(getattr(self, "_answer_cap", 0) or 0),
        )

    def _error(
        self, message: str, status: int = 502, headers: dict | None = None
    ) -> None:
        body = json.dumps({"error": friendly_error(message)}).encode()
        try:
            self.send_response(status)
            self._cors()
            self.send_header("content-type", "application/json")
            if status == 413:
                self.close_connection = True
                self.send_header("connection", "close")
            for key, value in (headers or {}).items():
                self.send_header(str(key), str(value))
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            safe_write(self, body)
        except Exception:
            pass

    def _write_mode_headers(self, mode: str, route: str, resident: str = "") -> None:
        fields = mode_fields(mode, route, resident)
        if fields.get("pi_mode"):
            self.send_header("X-Pi-Mode", fields["pi_mode"])
        if fields.get("pi_route"):
            self.send_header("X-Pi-Route", fields["pi_route"])

    def _cached(
        self,
        answer: str,
        want_stream: bool,
        started: float,
        think_name: str = "",
        mode_name: str = "",
        route_name: str = "",
    ) -> None:
        elapsed = int((time.time() - started) * 1000)
        note = mode_fields(mode_name, route_name, "")
        if not want_stream:
            resp = {
                "id": "pi-pair",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "pi_peer": "cache",
                "pi_chip": "cache",
                "pi_ms": elapsed,
                "pi_model": "canned",
                "pi_kind": "dataset",
            }
            if think_name:
                resp["pi_think"] = think_name
            resp["pi_stages"] = ["answering"]
            resp.update(note)
            apply_tier(self, resp)
            body = json.dumps(resp).encode()
            self.send_response(200)
            self._cors()
            self.send_header("content-type", "application/json")
            self.send_header("X-Pi-Peer", "cache")
            self.send_header("X-Pi-Chip", "cache")
            if think_name:
                self.send_header("X-Pi-Think", think_name)
            self._write_mode_headers(mode_name, route_name, "")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            safe_write(self, body)
            return
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Pi-Peer", "cache")
        self.send_header("X-Pi-Chip", "cache")
        if think_name:
            self.send_header("X-Pi-Think", think_name)
        self._write_mode_headers(mode_name, route_name, "")
        self.end_headers()
        answering = {"pi_stages": ["answering"]}
        if think_name:
            answering["pi_think"] = think_name
        answering.update(note)
        write_event(self, status_event("answering", answering))
        first = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": answer},
                    "finish_reason": None,
                }
            ],
            "pi_peer": "cache",
            "pi_chip": "cache",
            "pi_model": "canned",
            "pi_kind": "dataset",
            "pi_stages": ["answering"],
        }
        if think_name:
            first["pi_think"] = think_name
        first.update(note)
        apply_tier(self, first)
        safe_write(self, f"data: {json.dumps(first)}\n\n".encode(), flush=True)
        final = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "pi_peer": "cache",
            "pi_chip": "cache",
            "pi_ms": elapsed,
            "pi_model": "canned",
            "pi_kind": "dataset",
        }
        if think_name:
            final["pi_think"] = think_name
        final["pi_stages"] = ["answering"]
        final.update(note)
        apply_tier(self, final)
        safe_write(self, f"data: {json.dumps(final)}\n\n".encode(), flush=True)
        safe_write(self, b"data: [DONE]\n\n", flush=True)

    def _decode_reply(
        self,
        peer,
        kind,
        model,
        messages,
        temperature,
        max_tokens,
        prompt: str,
        search_note: dict | None,
    ) -> tuple[str, str, bool]:
        """One completion. A failed decode is one sentence."""
        meta: dict = {}
        try:
            if kind == "llamacpp":
                content, used = chat_llamacpp(
                    peer,
                    model,
                    messages,
                    temperature,
                    max_tokens,
                    meta=meta,
                    cancel=self._cancel,
                )
            else:
                content, used = chat_ollama(
                    peer,
                    model,
                    messages,
                    temperature,
                    max_tokens,
                    meta=meta,
                    plan=getattr(self, "_decode_plan", None),
                    cancel=self._cancel,
                )
        except (OSError, json.JSONDecodeError) as error:
            self._last_reasoning = ""
            raise DecodeFailed(public_failure(error)) from error
        self._usage = dict(meta.get("usage") or {})
        content = content or ""
        answer, leaked = peel_think(content)
        reasoning = "\n".join(
            part for part in (str(meta.get("reasoning") or "").strip(), leaked) if part
        )
        refused = _block(reasoning) or _block(answer)
        if refused:
            self._last_reasoning = ""
            return refused, used, False
        self._last_reasoning = reasoning
        content = answer
        if not _block(prompt):
            content = settle_reply(prompt, content, lambda: "")
        refused = _block(content)
        if refused:
            self._last_reasoning = ""
            return refused, used, False
        if not str(content).strip():
            raise DecodeFailed(friendly_error(""))
        content = self._one_more_round(
            peer,
            kind,
            model,
            messages,
            temperature,
            max_tokens,
            prompt,
            search_note,
            content,
        )
        content = clean_reply(
            content,
            prompt,
            _source_count(search_note),
            context=getattr(self, "_local_context", ""),
            have_tools=_searched(search_note),
        )
        if not str(content).strip():
            raise DecodeFailed(friendly_error(""))
        return content, used, True

    def _one_more_round(
        self,
        peer,
        kind,
        model,
        messages,
        temperature,
        max_tokens,
        prompt: str,
        search_note: dict | None,
        content: str,
    ) -> str:
        """At most one extra decode: tool results, or a single JSON schema retry."""
        if getattr(self, "_extra_call", False):
            return content
        context = getattr(self, "_local_context", "")
        have = _searched(search_note)
        settled = settle_blocks(content, prompt, context=context, have_tools=have)
        notes = tool_notes(settled, prompt=prompt, context=context, have_tools=have)
        retry = needs_json_retry(prompt, settled) and not notes
        if not notes and not retry:
            return settled
        follow = list(messages or [])
        follow.append({"role": "assistant", "content": settled})
        follow.append(
            {"role": "system", "content": notes or JSON_RETRY},
        )
        self._extra_call = True
        try:
            nxt, _used, _train = self._decode_reply(
                peer,
                kind,
                model,
                follow,
                temperature,
                max_tokens,
                prompt,
                search_note,
            )
        except DecodeFailed:
            return content
        finally:
            self._extra_call = False
        nxt = (nxt or "").strip()
        if not nxt:
            return content
        if retry and not needs_json_retry(prompt, nxt):
            return nxt
        if notes:
            return settled.rstrip() + "\n\n" + nxt
        return settled

    def _stream(
        self,
        peer,
        kind,
        model,
        used,
        messages,
        temperature,
        max_tokens,
        started,
        prompt: str,
        think_name: str = "",
        do_search: bool = False,
        searched: bool = False,
        search_note: dict | None = None,
        mode_name: str = "",
        route_name: str = "",
        resident_name: str = "",
        ready_answer: str | None = None,
        slot: dict | None = None,
        search_job: dict | None = None,
    ) -> None:
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Pi-Peer", peer["name"])
        self.send_header(
            "X-Pi-Chip", "brain: pi4" if peer["name"] == "pi4" else peer["name"]
        )
        if think_name:
            self.send_header("X-Pi-Think", think_name)
        self._write_mode_headers(mode_name, route_name, resident_name)
        self.end_headers()
        self._sse_lock = threading.Lock()
        self._stop_beat = threading.Event()

        def _beat() -> None:
            while not self._stop_beat.wait(KEEPALIVE_S):
                if not safe_write(self, b": keep-alive\n\n", flush=True):
                    return

        threading.Thread(target=_beat, name="sse-keepalive", daemon=True).start()
        stages: list[str] = []
        note = mode_fields(mode_name, route_name, resident_name)

        def emit_status(stage: str, extra: dict | None = None) -> bool:
            if stage not in stages:
                stages.append(stage)
            merged = dict(note)
            if extra:
                merged.update(extra)
            return write_event(self, status_event(stage, merged or None))

        if slot and slot.get("waiting"):
            if not emit_status("waiting", {"pi_detail": WAITING}):
                return

            def on_tick(pos: int, eta: int) -> None:
                emit_status(
                    "waiting",
                    {
                        "pi_detail": WAITING,
                        "pi_queue": {"position": pos, "eta_s": eta},
                    },
                )

            if not _claim_wait(slot, cancel=self._cancel, on_tick=on_tick):
                write_event(
                    self,
                    {
                        "id": "pi-pair",
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "error": BUSY,
                    },
                )
                safe_write(self, b"data: [DONE]\n\n", flush=True)
                return
            self._mark_queue()

        think_extra = {"pi_think": think_name} if think_name else None
        if not emit_status("thinking", think_extra):
            return
        if do_search:
            self._searched = True
            if not emit_status("searching", {"pi_tool": "search"}):
                return
            if not searched:
                messages, search_note = self._timed_search(
                    messages,
                    prompt,
                    model,
                    getattr(self, "_cancel", None),
                    job=search_job,
                )
            found = {"pi_tool": "search"}
            if search_note:
                found["pi_search"] = search_note["status"]
                found["pi_sources"] = search_note["sources"]
            if not emit_status("searching", found):
                return
        grounded = ready_answer
        answer_extra = dict(think_extra or {})
        if search_note:
            answer_extra["pi_search"] = search_note["status"]
            answer_extra["pi_sources"] = search_note["sources"]
        if not emit_status("answering", answer_extra or None):
            return
        fact_text, summary_text = _memory_prompt(self._memory_scope())
        source_rows = list(messages)
        messages = shape_messages(
            messages,
            prompt,
            _tuned_knobs(model),
            think_name,
            _prompt_note(search_note),
            hints=tail_hints(prompt, structure_hint(prompt) or ""),
            facts=fact_text,
            summary=summary_text,
        )
        self._memory_rows = turns_for_memory(source_rows, messages)
        if grounded is not None:
            self._emit_ready_answer(
                peer,
                kind,
                used,
                prompt,
                grounded,
                started,
                think_name,
                search_note,
                stages,
                mode_name,
                route_name,
                resident_name,
            )
            return
        if not self._public():
            safe_write(
                self,
                f": pi-pair peer={peer['name']} kind={kind} model={used}\n\n".encode(),
                flush=True,
            )
        first = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None,
                }
            ],
            "pi_peer": peer["name"],
            "pi_chip": "brain: pi4" if peer["name"] == "pi4" else peer["name"],
            "pi_model": used,
            "pi_kind": kind,
        }
        if think_name:
            first["pi_think"] = think_name
        if search_note:
            first["pi_search"] = search_note["status"]
            first["pi_sources"] = search_note["sources"]
        first.update(note)
        apply_tier(self, first)
        safe_write(self, f"data: {json.dumps(first)}\n\n".encode(), flush=True)
        parts: list[str] = []
        try:
            plan = getattr(self, "_decode_plan", None)
            if kind == "llamacpp":
                channels = (
                    ("content", delta)
                    for delta in stream_llamacpp(
                        peer,
                        model,
                        messages,
                        temperature,
                        max_tokens,
                        cancel=self._cancel,
                    )
                )
            elif plan is not None:
                channels = iter_ollama_channels(
                    peer,
                    model,
                    messages,
                    temperature,
                    max_tokens,
                    plan=plan,
                    cancel=self._cancel,
                    usage=self._usage,
                )
            else:
                channels = (
                    ("content", delta)
                    for delta in stream_ollama(
                        peer,
                        model,
                        messages,
                        temperature,
                        max_tokens,
                        plan=plan,
                        cancel=self._cancel,
                        usage=self._usage,
                    )
                )
            closed = False
            held = True
            policy = ""
            flushed = 0
            thinking_parts: list[str] = []
            thinking_flushed = 0
            reasoning_sent = False

            def write_json(payload: dict) -> bool:
                return safe_write(
                    self, f"data: {json.dumps(payload)}\n\n".encode(), flush=True
                )

            def clear_reasoning() -> bool:
                nonlocal reasoning_sent
                if not reasoning_sent:
                    return True
                reasoning_sent = False
                return write_json(
                    {
                        "id": "pi-pair",
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
                        "pi_reasoning_clear": True,
                    }
                )

            def emit_reasoning(piece: str) -> bool:
                nonlocal reasoning_sent
                if not piece:
                    return True
                ok = write_json(
                    {
                        "id": "pi-pair",
                        "object": "chat.completion.chunk",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"reasoning_content": piece},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
                if ok:
                    reasoning_sent = True
                return ok

            for channel, delta in channels:
                if not delta:
                    continue
                if channel == "thinking":
                    nxt = "".join(thinking_parts) + delta
                    release = stream_release(nxt)
                    if release == "refuse":
                        # The span that completes the match is not written.
                        # A thought prefix that already went out is cleared.
                        policy = _block(nxt)
                        thinking_parts.clear()
                        if not clear_reasoning():
                            closed = True
                        break
                    thinking_parts.append(delta)
                    joined_thought = "".join(thinking_parts)
                    piece = joined_thought[thinking_flushed:]
                    thinking_flushed = len(joined_thought)
                    if piece and not emit_reasoning(piece):
                        closed = True
                        break
                    continue
                parts.append(delta)
                joined = "".join(parts)
                release = stream_release(joined)
                if release == "refuse":
                    policy = _block(joined)
                    parts.clear()
                    thinking_parts.clear()
                    if not clear_reasoning():
                        closed = True
                    break
                old = flushed
                if "<" in joined and "think" in joined.lower():
                    visible, leaked = peel_think(joined)
                    if leaked and stream_release(leaked) == "refuse":
                        policy = _block(leaked)
                        parts.clear()
                        thinking_parts.clear()
                        flushed = len(joined) if old else 0
                        if not clear_reasoning():
                            closed = True
                        break
                    prior, _prior_leak = peel_think(joined[:old])
                    piece = visible[len(prior) :] if visible.startswith(prior) else ""
                    flushed = len(joined)
                    if piece:
                        held = False
                else:
                    piece = joined[old:]
                    flushed = len(joined)
                    held = False
                if not piece:
                    continue
                if not getattr(self, "_ttft_ms", 0):
                    self._ttft_ms = max(0, int((time.time() - started) * 1000))
                chunk = {
                    "id": "pi-pair",
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": piece},
                            "finish_reason": None,
                        }
                    ],
                }
                if not write_json(chunk):
                    closed = True
                    break
            if not closed and not policy:
                raw_answer = "".join(parts)
                if "<" in raw_answer and "think" in raw_answer.lower():
                    visible, leaked = peel_think(raw_answer)
                    if leaked and stream_release(leaked) == "refuse":
                        policy = _block(leaked)
                        parts.clear()
                        thinking_parts.clear()
                        if not clear_reasoning():
                            closed = True
                    else:
                        parts[:] = [visible] if visible else []
                rest = "".join(thinking_parts)[thinking_flushed:]
                if rest and not emit_reasoning(rest):
                    closed = True
            self._last_reasoning = "" if policy else "".join(thinking_parts)
            if closed:
                answer = "".join(parts).strip()
                if not is_harmful(prompt):
                    answer = scrub_reply(answer) or answer
                if answer:
                    chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
                    note_exchange(
                        prompt, answer, chip=chip, peer=peer["name"], train=True
                    )
                    remember_completion(prompt, answer, chip, peer["name"])
                return
            if policy:
                answer = policy
                refused = {
                    "id": "pi-pair",
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": answer},
                            "finish_reason": None,
                        }
                    ],
                }
                # The page appends deltas. This flag drops a prefix that
                # streamed before the reply turned harmful.
                if flushed:
                    refused["pi_replace"] = True
                if not safe_write(
                    self, f"data: {json.dumps(refused)}\n\n".encode(), flush=True
                ):
                    chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
                    remember_completion(prompt, answer, chip, peer["name"])
                    return
            else:
                answer = "".join(parts)
            if not policy and not is_harmful(prompt):
                answer = scrub_reply(answer) or answer
                streamed = answer
                more = self._one_more_round(
                    peer,
                    kind,
                    model,
                    messages,
                    temperature,
                    max_tokens,
                    prompt,
                    search_note,
                    answer,
                )
                shown = clean_reply(
                    more,
                    prompt,
                    _source_count(search_note),
                    context=getattr(self, "_local_context", ""),
                    have_tools=_searched(search_note),
                )
                visible = "\n".join(
                    line for line in streamed.splitlines() if line.strip()
                ).strip()
                if shown != visible:
                    extra = {
                        "id": "pi-pair",
                        "object": "chat.completion.chunk",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": shown},
                                "finish_reason": None,
                            }
                        ],
                        "pi_replace": True,
                    }
                    safe_write(
                        self, f"data: {json.dumps(extra)}\n\n".encode(), flush=True
                    )
                answer = shown
            trainable = not policy
            if not policy and not str(answer).strip():
                err = {"error": friendly_error("")}
                safe_write(self, f"data: {json.dumps(err)}\n\n".encode(), flush=True)
                safe_write(self, b"data: [DONE]\n\n", flush=True)
                return
            elapsed = int((time.time() - started) * 1000)
            final = {
                "id": "pi-pair",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
                "pi_peer": peer["name"],
                "pi_chip": "brain: pi4" if peer["name"] == "pi4" else peer["name"],
                "pi_ms": elapsed,
                "pi_model": used,
                "pi_kind": kind,
            }
            if think_name:
                final["pi_think"] = think_name
            if search_note:
                final["pi_search"] = search_note["status"]
                final["pi_sources"] = search_note["sources"]
            final["pi_stages"] = list(stages)
            self._attach_timing(final, started)
            final.update(note)
            chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
            if not policy:
                raw_answer = "".join(parts)
                if not held and answer == raw_answer:
                    answer = (
                        scrub_reply(raw_answer) or raw_answer
                        if not is_harmful(prompt)
                        else raw_answer
                    )
                elif not held and not is_harmful(prompt):
                    answer = scrub_reply(answer) or answer
                refused = _block(answer)
                if refused:
                    answer = refused
                note_exchange(
                    prompt, answer, chip=chip, peer=peer["name"], train=trainable
                )
            remember_completion(prompt, answer, chip, peer["name"])
            remembered = getattr(self, "_memory_rows", None)
            self._touch_memory(messages if remembered is None else remembered)
            apply_tier(self, final)
            safe_write(self, f"data: {json.dumps(final)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)
        except (OSError, json.JSONDecodeError, DecodeFailed) as error:
            shown = (
                str(error) if isinstance(error, DecodeFailed) else public_failure(error)
            )
            err = {"error": shown}
            safe_write(self, f"data: {json.dumps(err)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)
        except ClientGone:
            return
        except Exception as error:
            err = {"error": public_failure(error, search_note)}
            apply_tier(self, err)
            safe_write(self, f"data: {json.dumps(err)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)

    def _emit_ready_answer(
        self,
        peer,
        kind,
        used,
        prompt: str,
        answer: str,
        started: float,
        think_name: str,
        search_note: dict | None,
        stages: list[str],
        mode_name: str = "",
        route_name: str = "",
        resident_name: str = "",
    ) -> None:
        """Send a finished answer that was taken from the pages, not the model."""
        chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
        chunk = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {"content": answer}, "finish_reason": None}
            ],
            "pi_peer": peer["name"],
            "pi_chip": chip,
            "pi_model": used,
            "pi_kind": kind,
        }
        chunk.update(mode_fields(mode_name, route_name, resident_name))
        apply_tier(self, chunk)
        if not safe_write(self, f"data: {json.dumps(chunk)}\n\n".encode(), flush=True):
            return
        note_exchange(prompt, answer, chip=chip, peer=peer["name"], train=True)
        remember_completion(prompt, answer, chip, peer["name"])
        elapsed = int((time.time() - started) * 1000)
        final = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "pi_peer": peer["name"],
            "pi_chip": chip,
            "pi_ms": elapsed,
            "pi_model": used,
            "pi_kind": kind,
            "pi_stages": list(stages),
        }
        if think_name:
            final["pi_think"] = think_name
        if search_note:
            final["pi_search"] = search_note["status"]
            final["pi_sources"] = search_note["sources"]
        self._attach_timing(final, started)
        final.update(mode_fields(mode_name, route_name, resident_name))
        apply_tier(self, final)
        safe_write(self, f"data: {json.dumps(final)}\n\n".encode(), flush=True)
        safe_write(self, b"data: [DONE]\n\n", flush=True)

    def _complete(
        self,
        peer,
        kind,
        model,
        messages,
        temperature,
        max_tokens,
        started,
        prompt: str,
        think_name: str = "",
        search_note: dict | None = None,
        stages: list[str] | None = None,
        mode_name: str = "",
        route_name: str = "",
        resident_name: str = "",
        ready_answer: str | None = None,
        memory_turns=None,
    ) -> None:
        grounded = ready_answer
        train = True
        if grounded is not None:
            content = grounded
            used = model
        else:
            try:
                content, used, train = self._decode_reply(
                    peer,
                    kind,
                    model,
                    messages,
                    temperature,
                    max_tokens,
                    prompt,
                    search_note,
                )
            except DecodeFailed as error:
                self._error(str(error))
                return
        chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
        if train:
            note_exchange(prompt, content, chip=chip, peer=peer["name"], train=True)
        remember_completion(prompt, content, chip, peer["name"])
        self._touch_memory(messages if memory_turns is None else memory_turns)
        elapsed = int((time.time() - started) * 1000)
        message = {"role": "assistant", "content": content}
        reasoning = getattr(self, "_last_reasoning", "") or ""
        if grounded is None and reasoning and not _block(reasoning):
            message["reasoning_content"] = reasoning
        resp = {
            "id": "pi-pair",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "stop",
                }
            ],
            "pi_peer": peer["name"],
            "pi_chip": chip,
            "pi_ms": elapsed,
            "pi_model": used,
            "pi_kind": kind,
        }
        self._attach_timing(resp, started)
        if think_name:
            resp["pi_think"] = think_name
        if search_note:
            resp["pi_search"] = search_note["status"]
            resp["pi_sources"] = search_note["sources"]
        if stages:
            resp["pi_stages"] = stages
        resp.update(mode_fields(mode_name, route_name, resident_name))
        apply_tier(self, resp)
        body = json.dumps(resp).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("X-Pi-Peer", peer["name"])
        self.send_header("X-Pi-Chip", chip)
        if think_name:
            self.send_header("X-Pi-Think", think_name)
        self._write_mode_headers(mode_name, route_name, resident_name)
        if search_note:
            self.send_header("X-Pi-Search", search_note["status"])
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)


def make_server(
    host: str | None = None, port: int | None = None
) -> ThreadingHTTPServer:
    bind_host = runtime.HOST if host is None else host
    bind_port = runtime.PORT if port is None else port
    return ThreadingHTTPServer((bind_host, bind_port), Handler)


def main() -> None:
    runtime.configure()
    print(
        f"OpenPi 1.0 on {runtime.HOST}:{runtime.PORT} model={runtime.MODEL} "
        f"flash={mode_table().get('flash')} pro={mode_table().get('pro')} "
        f"slots={runtime.INFER_SLOTS} cache_ttl={runtime.HEALTH_CACHE_TTL}s "
        f"brain=pi4 search=pi2 dataset=pi3",
        flush=True,
    )
    server = make_server()
    start_pro_warm()
    start_model_warm()
    server.serve_forever()
