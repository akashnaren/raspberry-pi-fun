"""HTTP UI, OpenAI-compatible /v1/chat/completions, and the keyed public API."""
from __future__ import annotations

import gzip
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pair.canned import lookup, start_canned_warm, warm_status
from pair.chat import chat_llamacpp, chat_ollama, llamacpp_model, start_model_warm
from pair.config import STATIC_DIR
from pair.gate import capacity_message
from pair.ground import answer_from_search
from pair.guard import PI4_MISS_DOWN, may_generate, weak_brain_error
from pair.health import snapshot_peers
from pair.knobs import decode_effort, search_note_limit
from pair.modes import mode_table, pull_needed, resolve_auto, resolve_mode, tag_ready
from pair.peers import pick
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
from pair.images import lookup_images, sanitize_card, suits_visuals
from pair.mesh import lookup_for_brain
from pair.search import lookup_web
from pair import runtime
from pair.stream import stream_llamacpp, stream_ollama
from pair.turn import (
    asks_continuation,
    continuation_messages,
    degraded_answer,
    is_plot,
    join_continuation,
    needs_web,
    neutralize,
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
        url = str(item.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        title = str(item.get("title") or url).strip() or url
        sources.append({"title": title[:120], "url": url})
    full = str(found.get("context") or "").strip()
    shown = neutralize(full)
    limit = search_note_limit()
    if limit and len(shown) > limit:
        shown = shown[:limit].rstrip()
    if status == "ok" and shown:
        messages = [{"role": "system", "content": shown}, *messages]
    return messages, {"status": status, "sources": sources, "context": full}


def _image_cards(prompt: str) -> list[dict]:
    """Public cards for this turn. Empty when the line is not visual or the lookup fails."""
    try:
        found = lookup_images(prompt)
    except Exception:
        return []
    if not isinstance(found, list):
        return []
    cards = []
    for item in found:
        clean = sanitize_card(item)
        if not clean:
            continue
        cards.append(clean)
        if len(cards) >= 3:
            break
    return cards


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


def health_document() -> dict:
    peers = snapshot_peers()
    return {
        "ok": True,
        "model": runtime.MODEL,
        "mode": "flash",
        "modes": mode_table(),
        "peers_up": sum(1 for peer in peers if peer["ok"]),
        "peers": peers,
        "slots": runtime.INFER_SLOTS,
        "in_flight": runtime.gate.in_flight(),
        "cache_ttl": runtime.HEALTH_CACHE_TTL,
        "warm": warm_status(),
    }


def index_body() -> bytes:
    """Same substitution the single-file chat used: replace __MODEL__ in the page."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return html.replace("__MODEL__", runtime.MODEL).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _cors(self) -> None:
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
            do_search = bool(mesh and node_role() == "brain" and needs_web(prompt))
            show_images = bool(mesh and node_role() == "brain" and not is_plot(prompt))
        except Exception as error:
            self._error(str(error))
            return
        if route_name == "pro" and kind != "llamacpp":
            if not tag_ready(peer.get("models") or [], "pro", model):
                self._error(pull_needed(model))
                return
        # Streaming opens the event stream before search so the page is not
        # blank for the whole lookup. The gate is taken inside _stream, and
        # only when the model is actually called.
        if want_stream:
            try:
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
                    show_images=show_images,
                )
            except Exception as error:
                self._error(str(error))
            return
        search_note = None
        images: list[dict] = []
        grounded = None
        try:
            if do_search:
                outbound, search_note = _with_search(outbound, prompt)
            if show_images and (do_search or suits_visuals(prompt)):
                images = _image_cards(prompt)
            if search_note is not None:
                grounded = answer_from_search(prompt, str(search_note.get("context") or ""))
        except Exception as error:
            self._error(str(error))
            return
        # Map hits already returned. A page answer never enters the model, so it
        # does not take a generation slot either. Flash and Pro share this gate.
        if grounded is None and not runtime.gate.try_acquire():
            self._error(capacity_message(runtime.gate.limit), status=503)
            return
        try:
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

    def _write_delta(self, text: str) -> bool:
        if not text:
            return True
        chunk = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        }
        return safe_write(self, f"data: {json.dumps(chunk)}\n\n".encode(), flush=True)

    def _reply(
        self,
        peer,
        kind,
        model,
        messages,
        temperature,
        max_tokens,
        prompt: str,
        search_note,
    ):
        """One completion, plus a single list continuation. Network failures are a sentence."""
        meta: dict = {}
        try:
            if kind == "llamacpp":
                content, used = chat_llamacpp(peer, model, messages, temperature, max_tokens, meta=meta)
            else:
                content, used = chat_ollama(peer, model, messages, temperature, max_tokens, meta=meta)
        except (OSError, json.JSONDecodeError) as error:
            print(f"generation degraded: {type(error).__name__}", flush=True)
            return degraded_answer(search_note, error), model, False
        content = content or ""
        if asks_continuation(prompt, content, str(meta.get("done_reason") or "")):
            follow = shape_messages(continuation_messages(messages, content), prompt)
            more = ""
            try:
                if kind == "llamacpp":
                    more, used = chat_llamacpp(peer, model, follow, temperature, max_tokens)
                else:
                    more, used = chat_ollama(peer, model, follow, temperature, max_tokens)
            except (OSError, json.JSONDecodeError) as error:
                print(f"generation degraded: {type(error).__name__}", flush=True)
                more = ""
            content = join_continuation(content, more or "")
        if not str(content).strip():
            return degraded_answer(search_note, None), used, False
        return content, used, True

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
        show_images: bool = False,
    ) -> None:
        """Open the event stream. A full gate with no lookup stays a JSON 503."""
        acquired = False
        opened = False
        try:
            if not do_search:
                acquired = runtime.gate.try_acquire()
                if not acquired:
                    self._error(capacity_message(runtime.gate.limit), status=503)
                    return
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
            opened = True
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
                    if show_images:
                        images = _image_cards(prompt)
                elif show_images and not images:
                    images = _image_cards(prompt)
                found = {"pi_tool": "search"}
                if search_note:
                    found["pi_search"] = search_note["status"]
                    found["pi_sources"] = search_note["sources"]
                _put_images(found, images)
                if not emit_status("searching", found):
                    return
            elif show_images and suits_visuals(prompt):
                images = _image_cards(prompt)
            grounded = None
            if search_note is not None:
                grounded = answer_from_search(prompt, str(search_note.get("context") or ""))
            answer_extra = dict(think_extra or {})
            if search_note:
                answer_extra["pi_search"] = search_note["status"]
                answer_extra["pi_sources"] = search_note["sources"]
            _put_images(answer_extra, images)
            if not emit_status("answering", answer_extra or None):
                return
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
            messages = shape_messages(messages, prompt)
            parts: list[str] = []
            train_answer = True

            def pump(gen) -> bool:
                for delta in gen:
                    parts.append(delta)
                    if not self._write_delta(delta):
                        return True
                return False

            def seal(answer: str, train: bool) -> None:
                chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
                if train and answer.strip():
                    note_exchange(prompt, answer, chip=chip, peer=peer["name"], train=True)
                if answer.strip():
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
                _put_images(final, images)
                final.update(note)
                apply_tier(self, final)
                safe_write(self, f"data: {json.dumps(final)}\n\n".encode(), flush=True)
                safe_write(self, b"data: [DONE]\n\n", flush=True)

            try:
                if not acquired:
                    acquired = runtime.gate.try_acquire()
                if not acquired:
                    self._emit_ready_answer(
                        peer,
                        kind,
                        used,
                        prompt,
                        capacity_message(runtime.gate.limit),
                        started,
                        think_name,
                        search_note,
                        stages,
                        images,
                        mode_name,
                        route_name,
                        resident_name,
                        train=False,
                    )
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
                if kind == "llamacpp":
                    gen = stream_llamacpp(peer, model, messages, temperature, max_tokens)
                else:
                    gen = stream_ollama(peer, model, messages, temperature, max_tokens)
                if pump(gen):
                    answer = "".join(parts).strip()
                    if answer:
                        chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
                        note_exchange(prompt, answer, chip=chip, peer=peer["name"], train=True)
                        remember_completion(prompt, answer, chip, peer["name"])
                    return
                if asks_continuation(prompt, "".join(parts), getattr(gen, "done_reason", "")):
                    follow = shape_messages(continuation_messages(messages, "".join(parts)), prompt)
                    if kind == "llamacpp":
                        more = stream_llamacpp(peer, model, follow, temperature, max_tokens)
                    else:
                        more = stream_ollama(peer, model, follow, temperature, max_tokens)
                    if not "".join(parts).endswith("\n"):
                        parts.append("\n")
                        if not self._write_delta("\n"):
                            answer = "".join(parts).strip()
                            if answer:
                                chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
                                note_exchange(prompt, answer, chip=chip, peer=peer["name"], train=True)
                                remember_completion(prompt, answer, chip, peer["name"])
                            return
                    if pump(more):
                        answer = "".join(parts).strip()
                        if answer:
                            chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
                            note_exchange(prompt, answer, chip=chip, peer=peer["name"], train=True)
                            remember_completion(prompt, answer, chip, peer["name"])
                        return
                answer = "".join(parts)
                if not answer.strip():
                    sentence = degraded_answer(search_note, None)
                    parts.append(sentence)
                    self._write_delta(sentence)
                    train_answer = False
                    answer = sentence
                seal(answer, train_answer)
            except Exception as error:
                if not opened:
                    raise
                print(f"generation degraded: {type(error).__name__}", flush=True)
                if not "".join(parts).strip():
                    sentence = public_failure(error, search_note)
                    parts.append(sentence)
                    self._write_delta(sentence)
                    train_answer = False
                seal("".join(parts), train_answer and bool("".join(parts).strip()))
        finally:
            if acquired:
                runtime.gate.release()


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
        train: bool = True,
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
        if train:
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
    ) -> None:
        grounded = None
        if search_note is not None:
            grounded = answer_from_search(prompt, str(search_note.get("context") or ""))
        train = True
        if grounded is not None:
            content, used = grounded, model
        else:
            messages = shape_messages(messages, prompt)
            content, used, train = self._reply(
                peer,
                kind,
                model,
                messages,
                temperature,
                max_tokens,
                prompt,
                search_note,
            )
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
model_warm_thread: threading.Thread | None = None


def main() -> None:
    global warm_thread, model_warm_thread
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
    model_warm_thread = start_model_warm(warm_thread)
    server.serve_forever()
