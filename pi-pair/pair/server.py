"""HTTP UI and OpenAI-compatible /v1/chat/completions. Stdlib only."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pair.canned import lookup
from pair.chat import chat_llamacpp, chat_ollama, llamacpp_model
from pair.config import STATIC_DIR
from pair.guard import may_generate, weak_brain_error
from pair.health import snapshot_peers
from pair.peers import pick
from pair.queue import append_row, apply_label, node_role, note_exchange
from pair import runtime
from pair.stream import stream_llamacpp, stream_ollama

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
}


def safe_write(handler, body: bytes, flush: bool = False) -> None:
    try:
        handler.wfile.write(body)
        if flush:
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
        pass


def static_file(url_path: str) -> Path | None:
    if not url_path.startswith("/static/"):
        return None
    name = url_path[len("/static/") :]
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    root = STATIC_DIR.resolve()
    candidate = (root / name).resolve()
    if candidate.parent != root or not candidate.is_file():
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


def last_user_text(messages) -> str:
    for message in reversed(messages or []):
        if not isinstance(message, dict):
            continue
        if message.get("role", "user") == "user":
            return message_text(message.get("content"))
    return ""


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

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            body = index_body()
            self.send_response(200)
            self._cors()
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            safe_write(self, body)
            return
        asset = static_file(path)
        if asset is not None:
            body = asset.read_bytes()
            self.send_response(200)
            self._cors()
            self.send_header("content-type", _TYPES.get(asset.suffix, "application/octet-stream"))
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            safe_write(self, body)
            return
        if path.startswith("/health") or path.startswith("/peers"):
            peers = snapshot_peers()
            body = json.dumps(
                {
                    "ok": True,
                    "model": runtime.MODEL,
                    "peers_up": sum(1 for peer in peers if peer["ok"]),
                    "peers": peers,
                    "slots": runtime.INFER_SLOTS,
                    "cache_ttl": runtime.HEALTH_CACHE_TTL,
                }
            ).encode()
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
        if path == "/v1/flywheel/enqueue":
            self._enqueue()
            return
        if path == "/v1/flywheel/feedback":
            self._feedback()
            return
        if path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("content-length") or 0)
        data = json.loads(self.rfile.read(length).decode() or "{}")
        target = (self.headers.get("X-Pi-Target") or data.pop("pi_target", None) or "auto").strip()
        mesh = (self.headers.get("X-Pi-Mesh") or data.pop("pi_mesh", None) or "on").strip().lower() not in (
            "0",
            "off",
            "false",
            "no",
        )
        model = data.get("model") or runtime.MODEL
        messages = data.get("messages") or []
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
            if mesh and (not target or target == "auto" or _named_can_generate(target)):
                hit = lookup(prompt)
                if hit is not None:
                    note_exchange(prompt, hit, chip="cache", peer="cache", train=False)
                    remember_completion(prompt, hit, "cache", "cache")
                    self._cached(hit, want_stream, started)
                    return
        except Exception as error:
            self._error(str(error))
            return
        acquired = runtime._infer_sem.acquire(timeout=120)
        if not acquired:
            body = json.dumps({"error": "inference slots busy — try again"}).encode()
            self.send_response(503)
            self._cors()
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            safe_write(self, body)
            return
        try:
            peer = pick(target, mesh, model)
            outbound = [
                {"role": message.get("role", "user"), "content": message_text(message.get("content", ""))}
                for message in messages
                if isinstance(message, dict)
            ]
            kind = peer.get("kind") or "ollama"
            used = llamacpp_model(peer, model) if kind == "llamacpp" else model
            if want_stream:
                self._stream(peer, kind, model, used, outbound, temperature, max_tokens, started, prompt)
            else:
                self._complete(peer, kind, model, outbound, temperature, max_tokens, started, prompt)
        except Exception as error:
            self._error(str(error))
        finally:
            runtime._infer_sem.release()

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
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            safe_write(self, body)
        except Exception:
            pass

    def _cached(self, answer: str, want_stream: bool, started: float) -> None:
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
                "pi_peer": "cache",
                "pi_chip": "cache",
                "pi_ms": elapsed,
                "pi_model": "canned",
                "pi_kind": "dataset",
            }
            body = json.dumps(resp).encode()
            self.send_response(200)
            self._cors()
            self.send_header("content-type", "application/json")
            self.send_header("X-Pi-Peer", "cache")
            self.send_header("X-Pi-Chip", "cache")
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
        self.end_headers()
        first = {
            "id": "pi-pair",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": answer}, "finish_reason": None}],
            "pi_peer": "cache",
            "pi_chip": "cache",
            "pi_model": "canned",
            "pi_kind": "dataset",
        }
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
        safe_write(self, f"data: {json.dumps(final)}\n\n".encode(), flush=True)
        safe_write(self, b"data: [DONE]\n\n", flush=True)

    def _stream(self, peer, kind, model, used, messages, temperature, max_tokens, started, prompt: str) -> None:
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Pi-Peer", peer["name"])
        self.send_header("X-Pi-Chip", "brain: pi4" if peer["name"] == "pi4" else peer["name"])
        self.end_headers()
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
        safe_write(self, f"data: {json.dumps(first)}\n\n".encode(), flush=True)
        parts: list[str] = []
        try:
            if kind == "llamacpp":
                gen = stream_llamacpp(peer, model, messages, temperature, max_tokens)
            else:
                gen = stream_ollama(peer, model, messages, temperature, max_tokens)
            for delta in gen:
                parts.append(delta)
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
                safe_write(self, f"data: {json.dumps(chunk)}\n\n".encode(), flush=True)
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
            chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
            answer = "".join(parts)
            note_exchange(prompt, answer, chip=chip, peer=peer["name"], train=True)
            remember_completion(prompt, answer, chip, peer["name"])
            safe_write(self, f"data: {json.dumps(final)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)
        except Exception as error:
            err = {"error": str(error)}
            safe_write(self, f"data: {json.dumps(err)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)

    def _complete(self, peer, kind, model, messages, temperature, max_tokens, started, prompt: str) -> None:
        if kind == "llamacpp":
            content, used = chat_llamacpp(peer, model, messages, temperature, max_tokens)
        else:
            content, used = chat_ollama(peer, model, messages, temperature, max_tokens)
        chip = "brain: pi4" if peer["name"] == "pi4" else peer["name"]
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
        body = json.dumps(resp).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("X-Pi-Peer", peer["name"])
        self.send_header("X-Pi-Chip", chip)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        safe_write(self, body)


def make_server(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    bind_host = runtime.HOST if host is None else host
    bind_port = runtime.PORT if port is None else port
    return ThreadingHTTPServer((bind_host, bind_port), Handler)


def main() -> None:
    runtime.configure()
    print(
        f"Pi 0.2 High on {runtime.HOST}:{runtime.PORT} model={runtime.MODEL} "
        f"slots={runtime.INFER_SLOTS} cache_ttl={runtime.HEALTH_CACHE_TTL}s brain=pi4",
        flush=True,
    )
    make_server().serve_forever()
