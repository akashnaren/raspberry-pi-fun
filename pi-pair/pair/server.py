"""HTTP UI, OpenAI-compatible /v1/chat/completions, and the keyed public API."""
from __future__ import annotations

import gzip
import json
import os
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pair.assist import (
    HELPFUL_NUDGE,
    is_harmful,
    is_honest_miss,
    is_soft_refusal,
    may_retry_refusal,
    scrub_reply,
    settle_reply,
    visible_canned,
    withhold_partial,
)
from pair.canned import lookup, start_canned_warm, warm_status
from pair.charts import (
    CHART_NUDGE,
    is_chart_request,
    is_structured_request,
    parabola_chart,
    repair_chart_reply,
    structure_hint,
)
from pair.chat import chat_llamacpp, chat_ollama, llamacpp_model, start_model_warm
from pair.config import STATIC_DIR
from pair.docfit import fit_outbound
from pair.gate import capacity_message
from pair.ground import answer_from_search
from pair.guard import PI4_MISS_DOWN, may_generate, weak_brain_error
from pair.health import snapshot_peers
from pair.images import cards_for_answer, lookup_images, sanitize_card, visual_mode
from pair.knobs import decode_effort, inference_knobs, search_note_limit
from pair.lists import (
    continuation_messages,
    finish_numbered,
    list_budget,
    list_count,
    placeholder_only,
)
from pair.modes import mode_table, pull_needed, resolve_auto, resolve_mode, tag_ready
from pair.peers import pick
from pair.preload import start_pro_warm
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
from pair.stream import stream_llamacpp, stream_ollama
from pair.turn import (
    asks_continuation,
    continuation_messages as plain_continuation,
    degraded_answer,
    join_continuation,
    needs_web,
    prepare_search_note,
    public_failure,
    shape_messages,
)
from pair.upload import UploadRejected, ingest, read_limited

SOURCE_CAP = 8
SEARCH_BODY_CAP = 4096

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
    try:
        handler.wfile.write(body)
        if flush:
            handler.wfile.flush()
        return True
    except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
        return False


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


def brain_chat_url() -> str:
    """pi4's page, not its Ollama port. Search and decode both happen there."""
    peer = next((item for item in runtime.PEERS if item.get("name") == "pi4"), None)
    if peer is None or not may_generate(peer):
        raise RuntimeError(PI4_MISS_DOWN)
    port = int(os.environ.get("PI_PAIR_BRAIN_PORT", "18080"))
    return f"http://{peer['host']}:{port}/v1/chat/completions"


def relay_chat(payload: bytes, target: str, mesh: str, mode: str = "") -> tuple[int, dict[str, str], bytes]:
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
                "content-type": response.headers.get("content-type", "application/json"),
            }
            for name in ("X-Pi-Peer", "X-Pi-Chip", "X-Pi-Think", "X-Pi-Search", "X-Pi-Mode", "X-Pi-Route"):
                value = response.headers.get(name)
                if value:
                    headers[name] = value
            return response.status, headers, raw
    except urllib.error.HTTPError as error:
        raw = error.read()
        kind = error.headers.get("content-type", "application/json")
        return error.code, {"content-type": kind}, raw
    except Exception:
        raise RuntimeError(PI4_MISS_DOWN) from None


def _with_search(messages, prompt: str):
    """On pi4, after a miss, attach public notes. Failures stay on the local model."""
    if not (prompt or "").strip():
        return messages, None
    try:
        found = lookup_for_brain(prompt, local=lookup_web)
    except Exception:
        found = None
    if not isinstance(found, dict):
        found = {"status": "failed", "sources": [], "context": ""}
    status = found.get("status")
    if status not in ("ok", "failed"):
        status = "failed"
    sources = []
    for item in found.get("sources") or []:
        if len(sources) >= SOURCE_CAP:
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
    if status == "ok" and shown:
        messages = [{"role": "system", "content": shown}, *messages]
    return messages, {"status": status, "sources": sources, "context": full}


def _image_cards(prompt: str, answer: str = "") -> list[dict]:
    """Public cards for this turn. A list of visual items waits for the answer."""
    mode = visual_mode(prompt)
    if mode == "none":
        return []
    try:
        if mode == "each":
            if not (answer or "").strip():
                return []
            found = cards_for_answer(prompt, answer)
        else:
            found = lookup_images(prompt)
    except Exception:
        return []
    if not isinstance(found, list):
        return []
    cap = 8 if mode == "each" else 3
    cards = []
    for item in found:
        clean = sanitize_card(item)
        if not clean:
            continue
        cards.append(clean)
        if len(cards) >= cap:
            break
    return cards


def _cards_after(prompt: str, answer: str, images: list[dict]) -> list[dict]:
    """One card per listed car, movie, product, or place. Other turns keep theirs."""
    if visual_mode(prompt) != "each":
        return images
    fresh = _image_cards(prompt, answer)
    return fresh or images


def _list_suffix(shown: str, finished: str) -> str:
    """Text the stream has not already sent. Empty when the list did not grow."""
    if not finished or finished == shown:
        return ""
    trimmed = shown.rstrip()
    if finished.startswith(trimmed):
        return finished[len(trimmed) :]
    return ""


def _put_images(payload: dict, images: list[dict]) -> None:
    if images:
        payload["pi_images"] = images


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


def health_document() -> dict:
    peers = snapshot_peers()
    up = sum(1 for peer in peers if isinstance(peer, dict) and peer.get("ok"))
    return {
        "ok": True,
        "model": runtime.MODEL,
        "mode": "flash",
        "modes": mode_table(),
        "peers_up": up,
        "peers": peers,
        "slots": runtime.INFER_SLOTS,
        "in_flight": runtime.gate.in_flight(),
        "cache_ttl": runtime.HEALTH_CACHE_TTL,
        "warm": warm_status(),
        "uptime_s": max(0, int(time.monotonic() - _BOOTED)),
        "services": {
            "brain": _service_row(peers, "brain", "pi4"),
            "search": _service_row(peers, "health", "pi2"),
            "peers_up": up,
            "peers": len(peers),
        },
    }


def index_body() -> bytes:
    """Same substitution the single-file chat used: replace __MODEL__ in the page."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace("__MODEL__", runtime.MODEL).encode("utf-8")


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
            self.send_header("content-type", _TYPES.get(asset.suffix, "application/octet-stream"))
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
        if path == "/api/health":
            self._api_health()
            return
        if path.startswith("/health") or path.startswith("/peers"):
            body = json.dumps(health_document()).encode()
            self.send_response(200)
            self._cors()
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            safe_write(self, body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        if path == "/v1/search":
            self._search()
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
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        data = json.loads(raw.decode() or "{}")
        self._serve_chat(data, raw)

    def _api_chat(self) -> None:
        rejected = authorize(self.headers)
        if rejected is not None:
            self._reject_api(*rejected)
            return
        length = int(self.headers.get("content-length") or 0)
        if length > 1_000_000:
            self._error("chat body is too large", status=413)
            return
        try:
            data = json.loads(self.rfile.read(length).decode() or "{}")
        except json.JSONDecodeError:
            self._error("chat body must be JSON", status=400)
            return
        if not isinstance(data, dict):
            self._error("chat body must be an object", status=400)
            return
        messages = data.get("messages")
        if not isinstance(messages, list) or not last_user_text(messages).strip():
            self._error("chat needs a user message", status=400)
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

    def _bind_tier(self, data: dict, prompt: str) -> str:
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

    def _serve_chat(self, data: dict, raw: bytes) -> None:
        if not isinstance(data, dict):
            self._error("chat body must be an object", status=400)
            return
        target = (self.headers.get("X-Pi-Target") or data.pop("pi_target", None) or "auto").strip()
        mesh = (self.headers.get("X-Pi-Mesh") or data.pop("pi_mesh", None) or "on").strip().lower() not in (
            "0",
            "off",
            "false",
            "no",
        )
        messages = data.get("messages") or []
        effort = decode_effort(str(data.pop("think", "") or ""))
        if effort:
            think_name, temperature, max_tokens = effort
            data.pop("temperature", None)
            data.pop("max_tokens", None)
            data.pop("max_completion_tokens", None)
        else:
            think_name = ""
            temperature = float(data.get("temperature") if data.get("temperature") is not None else 0.7)
            max_tokens = int(data.get("max_tokens") or data.get("max_completion_tokens") or 256)
        started = time.time()
        want_stream = bool(data.get("stream"))
        prompt = last_user_text(messages)
        try:
            if target and target != "auto":
                named = next((peer for peer in runtime.PEERS if peer["name"] == target), None)
                if named is not None and not may_generate(named):
                    raise RuntimeError(weak_brain_error(named["name"]))
            if (
                mesh
                and one_user_turn(messages)
                and (not target or target == "auto" or _named_can_generate(target))
            ):
                hit = lookup(prompt)
                if hit is not None:
                    hit = visible_canned(prompt, hit)
                    note_exchange(prompt, hit, chip="cache", peer="cache", train=False)
                    remember_completion(prompt, hit, "cache", "cache")
                    cached_mode, cached_route = self._remember_canned_mode(data)
                    self._cached(
                        hit,
                        want_stream,
                        started,
                        think_name,
                        cached_mode,
                        cached_route,
                    )
                    return
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
            model = self._bind_tier(data, prompt)
            mode_name = getattr(self, "pi_mode", "") or ""
            route_name = getattr(self, "pi_route", "") or ""
            resident_name = ""
            peer = pick(target, mesh, model)
            outbound = [
                {"role": message.get("role", "user"), "content": message_text(message.get("content", ""))}
                for message in messages
                if isinstance(message, dict)
            ]
            kind = peer.get("kind") or "ollama"
            used = llamacpp_model(peer, model) if kind == "llamacpp" else model
            structured = is_structured_request(prompt)
            do_search = bool(mesh and node_role() == "brain") and not structured and needs_web(prompt)
            max_tokens = list_budget(prompt, max_tokens)
            ctx = int(inference_knobs().get("num_ctx") or 2048)
            outbound = fit_outbound(outbound, num_ctx=ctx, reply_tokens=max_tokens)
            ready = parabola_chart(prompt)
            hint = None if ready else structure_hint(prompt)
            if hint:
                outbound = [{"role": "system", "content": hint}, *outbound]
            search_note = None
            images: list[dict] = []
            if do_search and not want_stream:
                outbound, search_note = _with_search(outbound, prompt)
                images = _image_cards(prompt)
            if not want_stream:
                outbound = shape_messages(outbound, prompt)
            grounded = ready
            if grounded is None and search_note is not None:
                grounded = answer_from_search(prompt, str(search_note.get("context") or ""))
        except Exception as error:
            self._error(str(error))
            return
        if route_name == "pro" and kind != "llamacpp":
            if not tag_ready(peer.get("models") or [], "pro", model):
                self._error(pull_needed(model))
                return
        # Map hits already returned. A page answer never enters the model, so it
        # does not take a generation slot either. Flash and Pro share this gate.
        if grounded is None and not runtime.gate.try_acquire():
            self._error(capacity_message(runtime.gate.limit), status=503)
            return
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
                    images=[],
                    mode_name=mode_name,
                    route_name=route_name,
                    resident_name=resident_name,
                    ready_answer=grounded,
                )
            else:
                stages = ["loading"] if route_name == "pro" else []
                stages.append("thinking")
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
                    images,
                    mode_name,
                    route_name,
                    resident_name,
                    ready_answer=grounded,
                )
        except Exception as error:
            self._error(str(error))
        finally:
            if grounded is None:
                runtime.gate.release()

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
            status, headers, body = relay_chat(payload, target, mesh, getattr(self, "pi_mode", "") or "")
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
            raw = error.read()
            kind = error.headers.get("content-type", "application/json")
            self.send_response(error.code)
            self._cors()
            self.send_header("content-type", kind)
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
            for name in ("X-Pi-Peer", "X-Pi-Chip", "X-Pi-Think", "X-Pi-Search", "X-Pi-Mode", "X-Pi-Route"):
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

    def _search(self) -> None:
        """DuckDuckGo lookup on the health host. This route does not decode."""
        if node_role() != "health":
            self._error("search is served on the health host", status=403)
            return
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            self._error("search body must be JSON", status=400)
            return
        if length < 0 or length > SEARCH_BODY_CAP:
            self._error("search query is too long", status=413)
            return
        try:
            row = json.loads(self.rfile.read(length).decode() or "{}")
        except json.JSONDecodeError:
            self._error("search body must be JSON", status=400)
            return
        if not isinstance(row, dict):
            self._error("search body must be an object", status=400)
            return
        query = str(row.get("q") or row.get("query") or "")
        found = lookup_web(query)
        body = json.dumps(
            {
                "status": found.get("status") if isinstance(found, dict) else "failed",
                "sources": (found.get("sources") if isinstance(found, dict) else []) or [],
                "context": (found.get("context") if isinstance(found, dict) else "") or "",
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
        length = int(self.headers.get("content-length") or 0)
        try:
            row = json.loads(self.rfile.read(length).decode() or "{}")
        except json.JSONDecodeError:
            self._error("queue row must be JSON", status=400)
            return
        if not isinstance(row, dict):
            self._error("queue row must be an object", status=400)
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
        length = int(self.headers.get("content-length") or 0)
        try:
            row = json.loads(self.rfile.read(length).decode() or "{}")
        except json.JSONDecodeError:
            self._error("feedback must be JSON", status=400)
            return
        if not isinstance(row, dict):
            self._error("feedback must be an object", status=400)
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

    def _error(self, message: str, status: int = 502) -> None:
        body = json.dumps({"error": message}).encode()
        try:
            self.send_response(status)
            self._cors()
            self.send_header("content-type", "application/json")
            if status == 413:
                self.close_connection = True
                self.send_header("connection", "close")
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
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": answer}, "finish_reason": None}],
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

    def _extend_list(
        self,
        peer,
        kind,
        model,
        messages,
        temperature,
        max_tokens,
        prompt: str,
        text: str,
    ) -> str:
        """Ask once or twice more when a numbered list stops before N."""

        def more(partial: str, count: int) -> str:
            follow = continuation_messages(messages, partial, count)
            if kind == "llamacpp":
                nxt, _used = chat_llamacpp(peer, model, follow, temperature, max_tokens)
            else:
                nxt, _used = chat_ollama(peer, model, follow, temperature, max_tokens)
            return nxt

        try:
            extended = finish_numbered(prompt, text, more)
        except Exception:
            return text
        if extended != text or list_count(prompt):
            return extended
        return text

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
        """One completion. A plain list may continue once. A failed decode is one sentence."""
        meta: dict = {}
        try:
            if kind == "llamacpp":
                content, used = chat_llamacpp(peer, model, messages, temperature, max_tokens, meta=meta)
            else:
                content, used = chat_ollama(peer, model, messages, temperature, max_tokens, meta=meta)
        except (OSError, json.JSONDecodeError) as error:
            return degraded_answer(search_note, error), model, False
        content = content or ""
        if may_retry_refusal(prompt) and is_soft_refusal(content):
            content = self._guard_reply(
                peer, kind, model, messages, temperature, max_tokens, prompt, content
            )
        elif not list_count(prompt) and asks_continuation(prompt, content, str(meta.get("done_reason") or "")):
            follow = shape_messages(plain_continuation(messages, content), prompt)
            more = ""
            try:
                if kind == "llamacpp":
                    more, used = chat_llamacpp(peer, model, follow, temperature, max_tokens)
                else:
                    more, used = chat_ollama(peer, model, follow, temperature, max_tokens)
            except (OSError, json.JSONDecodeError):
                more = ""
            content = join_continuation(content, more or "")
        else:
            content = self._extend_list(
                peer, kind, model, messages, temperature, max_tokens, prompt, content
            )
        content = self._repair_chart(
            peer, kind, model, messages, temperature, max_tokens, prompt, content
        )
        if not is_harmful(prompt):
            content = self._guard_reply(
                peer, kind, model, messages, temperature, max_tokens, prompt, content
            )
        if not str(content).strip():
            return degraded_answer(search_note, None), used, False
        return content, used, True

    def _ask(self, peer, kind, model, messages, temperature, max_tokens) -> str:
        try:
            if kind == "llamacpp":
                more, _used = chat_llamacpp(peer, model, messages, temperature, max_tokens)
            else:
                more, _used = chat_ollama(peer, model, messages, temperature, max_tokens)
        except (OSError, json.JSONDecodeError):
            return ""
        return more or ""

    def _guard_reply(
        self,
        peer,
        kind,
        model,
        messages,
        temperature,
        max_tokens,
        prompt: str,
        content: str,
    ) -> str:
        """Nudge once. A second soft refusal may use search notes or Pro, not a canned list."""

        def again() -> str:
            follow = shape_messages(
                [
                    *list(messages or []),
                    {"role": "assistant", "content": content or ""},
                    {"role": "user", "content": HELPFUL_NUDGE},
                ],
                prompt,
            )
            return self._ask(peer, kind, model, follow, temperature, max_tokens)

        def ground() -> str:
            return self._recover_refusal(
                peer, kind, model, messages, temperature, max_tokens, prompt
            )

        return settle_reply(prompt, content, again, ground)

    def _mesh_search_on(self) -> bool:
        mesh = (self.headers.get("X-Pi-Mesh") or "on").strip().lower()
        return mesh != "off" and node_role() == "brain"

    def _recover_refusal(
        self,
        peer,
        kind,
        model,
        messages,
        temperature,
        max_tokens,
        prompt: str,
    ) -> str:
        """One recovery after the nudge: pi2/local search notes, or a Pro tag.

        Search wins when it returns notes. Pro is the other path, used when
        search is off, already ran, or came back empty. Neither invents items.
        """
        rows = list(messages or [])
        already = any(
            isinstance(row, dict) and str(row.get("content") or "").startswith("Web search notes")
            for row in rows
        )
        if not already and self._mesh_search_on() and not is_structured_request(prompt):
            outbound, note = _with_search(rows, prompt)
            context = ""
            if isinstance(note, dict) and note.get("status") == "ok":
                context = str(note.get("context") or "").strip()
            if context:
                follow = shape_messages(
                    [*outbound, {"role": "user", "content": HELPFUL_NUDGE}],
                    prompt,
                )
                return self._ask(peer, kind, model, follow, temperature, max_tokens)
        pro_tag = mode_table().get("pro") or ""
        if not pro_tag or pro_tag == model:
            return ""
        if not tag_ready(peer.get("models") or [], "pro", pro_tag):
            return ""
        follow = shape_messages(
            [*rows, {"role": "user", "content": HELPFUL_NUDGE}],
            prompt,
        )
        return self._ask(peer, kind, pro_tag, follow, temperature, max_tokens)

    def _repair_chart(
        self,
        peer,
        kind,
        model,
        messages,
        temperature,
        max_tokens,
        prompt: str,
        content: str,
    ) -> str:
        """One strict-JSON retry when a chart fence is invalid, else one sentence."""

        def again() -> str:
            follow = shape_messages(
                [
                    *list(messages or []),
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": CHART_NUDGE},
                ],
                prompt,
            )
            try:
                if kind == "llamacpp":
                    more, _used = chat_llamacpp(peer, model, follow, temperature, max_tokens)
                else:
                    more, _used = chat_ollama(peer, model, follow, temperature, max_tokens)
            except (OSError, json.JSONDecodeError):
                return ""
            return more or ""

        return repair_chart_reply(content, again, prompt=prompt)

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
        images: list[dict] | None = None,
        mode_name: str = "",
        route_name: str = "",
        resident_name: str = "",
        ready_answer: str | None = None,
    ) -> None:
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Pi-Peer", peer["name"])
        self.send_header("X-Pi-Chip", "brain: pi4" if peer["name"] == "pi4" else peer["name"])
        if think_name:
            self.send_header("X-Pi-Think", think_name)
        self._write_mode_headers(mode_name, route_name, resident_name)
        self.end_headers()
        stages: list[str] = []
        note = mode_fields(mode_name, route_name, resident_name)

        def emit_status(stage: str, extra: dict | None = None) -> bool:
            if stage not in stages:
                stages.append(stage)
            merged = dict(note)
            if extra:
                merged.update(extra)
            return write_event(self, status_event(stage, merged or None))

        think_extra = {"pi_think": think_name} if think_name else None
        if route_name == "pro":
            if not emit_status("loading", {"pi_loading": "Loading Pro"}):
                return
        if not emit_status("thinking", think_extra):
            return
        images = list(images or [])
        if do_search:
            if not emit_status("searching", {"pi_tool": "search"}):
                return
            if not searched:
                messages, search_note = _with_search(messages, prompt)
                images = _image_cards(prompt)
            found = {"pi_tool": "search"}
            if search_note:
                found["pi_search"] = search_note["status"]
                found["pi_sources"] = search_note["sources"]
            _put_images(found, images)
            if not emit_status("searching", found):
                return
        grounded = ready_answer
        if grounded is None and search_note is not None:
            grounded = answer_from_search(prompt, str(search_note.get("context") or ""))
        answer_extra = dict(think_extra or {})
        if search_note:
            answer_extra["pi_search"] = search_note["status"]
            answer_extra["pi_sources"] = search_note["sources"]
        _put_images(answer_extra, images)
        if not emit_status("answering", answer_extra or None):
            return
        messages = shape_messages(messages, prompt)
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
                images,
                mode_name,
                route_name,
                resident_name,
            )
            return
        if is_chart_request(prompt):
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
            if train:
                self._emit_ready_answer(
                    peer,
                    kind,
                    used,
                    prompt,
                    content,
                    started,
                    think_name,
                    search_note,
                    stages,
                    images,
                    mode_name,
                    route_name,
                    resident_name,
                )
                return
            chunk = {
                "id": "pi-pair",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
            }
            safe_write(self, f"data: {json.dumps(chunk)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)
            return
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
        _put_images(first, images)
        first.update(note)
        apply_tier(self, first)
        safe_write(self, f"data: {json.dumps(first)}\n\n".encode(), flush=True)
        parts: list[str] = []
        try:
            if kind == "llamacpp":
                gen = stream_llamacpp(peer, model, messages, temperature, max_tokens)
            else:
                gen = stream_ollama(peer, model, messages, temperature, max_tokens)
            closed = False
            held = True
            for delta in gen:
                parts.append(delta)
                if held and not is_harmful(prompt) and withhold_partial("".join(parts)):
                    continue
                if held:
                    delta = "".join(parts)
                    held = False
                chunk = {
                    "id": "pi-pair",
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": delta},
                            "finish_reason": None,
                        }
                    ],
                }
                if not safe_write(self, f"data: {json.dumps(chunk)}\n\n".encode(), flush=True):
                    closed = True
                    break
            if closed:
                answer = "".join(parts).strip()
                if not is_harmful(prompt):
                    answer = scrub_reply(answer) or answer
                if answer:
                    chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
                    note_exchange(prompt, answer, chip=chip, peer=peer["name"], train=True)
                    remember_completion(prompt, answer, chip, peer["name"])
                return
            answer = "".join(parts)
            if held and may_retry_refusal(prompt) and (
                is_soft_refusal(answer) or placeholder_only(answer)
            ):
                if placeholder_only(answer) and not is_soft_refusal(answer):
                    answer = self._extend_list(
                        peer, kind, model, messages, temperature, max_tokens, prompt, answer
                    )
                else:
                    answer = self._guard_reply(
                        peer, kind, model, messages, temperature, max_tokens, prompt, answer
                    )
                if answer and not safe_write(
                    self,
                    (
                        "data: "
                        + json.dumps(
                            {
                                "id": "pi-pair",
                                "object": "chat.completion.chunk",
                                "choices": [
                                    {"index": 0, "delta": {"content": answer}, "finish_reason": None}
                                ],
                            }
                        )
                        + "\n\n"
                    ).encode(),
                    flush=True,
                ):
                    chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
                    note_exchange(prompt, answer, chip=chip, peer=peer["name"], train=True)
                    remember_completion(prompt, answer, chip, peer["name"])
                    return
            elif not is_harmful(prompt):
                answer = scrub_reply(answer) or answer
            skip_extend = (
                is_soft_refusal(answer) or is_honest_miss(answer) or placeholder_only(answer)
            )
            finished = answer if skip_extend else self._extend_list(
                peer, kind, model, messages, temperature, max_tokens, prompt, answer
            )
            extra = _list_suffix(answer, finished)
            if extra:
                more = {
                    "id": "pi-pair",
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": extra},
                            "finish_reason": None,
                        }
                    ],
                }
                if not safe_write(self, f"data: {json.dumps(more)}\n\n".encode(), flush=True):
                    chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
                    note_exchange(prompt, answer, chip=chip, peer=peer["name"], train=True)
                    remember_completion(prompt, answer, chip, peer["name"])
                    return
                answer = finished
            images = _cards_after(prompt, answer, images)
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
            _put_images(final, images)
            final["pi_stages"] = list(stages)
            final.update(note)
            chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
            raw_answer = "".join(parts)
            if not held:
                answer = scrub_reply(raw_answer) or raw_answer if not is_harmful(prompt) else raw_answer
            note_exchange(prompt, answer, chip=chip, peer=peer["name"], train=True)
            remember_completion(prompt, answer, chip, peer["name"])
            apply_tier(self, final)
            safe_write(self, f"data: {json.dumps(final)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)
        except (OSError, json.JSONDecodeError) as error:
            sentence = degraded_answer(search_note, error)
            chunk = {
                "id": "pi-pair",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": sentence}, "finish_reason": None}],
            }
            safe_write(self, f"data: {json.dumps(chunk)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)
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
        images: list[dict] | None = None,
        mode_name: str = "",
        route_name: str = "",
        resident_name: str = "",
    ) -> None:
        """Send a finished answer that was taken from the pages, not the model."""
        chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
        chunk = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": None}],
            "pi_peer": peer["name"],
            "pi_chip": chip,
            "pi_model": used,
            "pi_kind": kind,
        }
        _put_images(chunk, images or [])
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
        _put_images(final, images or [])
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
        images: list[dict] | None = None,
        mode_name: str = "",
        route_name: str = "",
        resident_name: str = "",
        ready_answer: str | None = None,
    ) -> None:
        grounded = ready_answer
        if grounded is None and search_note is not None:
            grounded = answer_from_search(prompt, str(search_note.get("context") or ""))
        train = True
        if grounded is not None:
            content, used = grounded, model
        else:
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
            images = _cards_after(prompt, content, list(images or []))
        chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
        if train:
            note_exchange(prompt, content, chip=chip, peer=peer["name"], train=True)
        remember_completion(prompt, content, chip, peer["name"])
        elapsed = int((time.time() - started) * 1000)
        resp = {
            "id": "pi-pair",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "pi_peer": peer["name"],
            "pi_chip": chip,
            "pi_ms": elapsed,
            "pi_model": used,
            "pi_kind": kind,
        }
        if think_name:
            resp["pi_think"] = think_name
        if search_note:
            resp["pi_search"] = search_note["status"]
            resp["pi_sources"] = search_note["sources"]
        _put_images(resp, images or [])
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


def make_server(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    bind_host = runtime.HOST if host is None else host
    bind_port = runtime.PORT if port is None else port
    return ThreadingHTTPServer((bind_host, bind_port), Handler)


warm_thread: threading.Thread | None = None


def main() -> None:
    global warm_thread
    runtime.configure()
    print(
        f"Pi GPT 1.0 on {runtime.HOST}:{runtime.PORT} model={runtime.MODEL} "
        f"flash={mode_table().get('flash')} pro={mode_table().get('pro')} "
        f"slots={runtime.INFER_SLOTS} cache_ttl={runtime.HEALTH_CACHE_TTL}s "
        f"brain=pi4 search=pi2 dataset=pi3",
        flush=True,
    )
    # TCPServer.__init__ binds and listens. Accept starts below. The Arctic
    # key batch runs after that listen so /health and chat are not refused
    # for the duration of the preload.
    server = make_server()
    warm_thread = start_canned_warm()
    start_pro_warm()
    start_model_warm(warm_thread)
    server.serve_forever()
