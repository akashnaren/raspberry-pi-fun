"""HTTP UI and OpenAI-compatible /v1/chat/completions. Stdlib only."""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pair.chat import chat_llamacpp, chat_ollama, llamacpp_model
from pair.config import STATIC_DIR
from pair.health import snapshot_peers
from pair.peers import pick
from pair import runtime
from pair.stream import stream_llamacpp, stream_ollama

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
        if self.path.split("?")[0] != "/v1/chat/completions":
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
        want_stream = bool(data.get("stream"))
        try:
            peer = pick(target, mesh, model)
            outbound = [
                {"role": message.get("role", "user"), "content": message.get("content", "")}
                for message in messages
            ]
            kind = peer.get("kind") or "ollama"
            used = llamacpp_model(peer, model) if kind == "llamacpp" else model
            if want_stream:
                self._stream(peer, kind, model, used, outbound, temperature, max_tokens, started)
            else:
                self._complete(peer, kind, model, outbound, temperature, max_tokens, started)
        except Exception as error:
            body = json.dumps({"error": str(error)}).encode()
            try:
                self.send_response(502)
                self._cors()
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                safe_write(self, body)
            except Exception:
                pass
        finally:
            runtime._infer_sem.release()

    def _stream(self, peer, kind, model, used, messages, temperature, max_tokens, started) -> None:
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Pi-Peer", peer["name"])
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
            "pi_model": used,
            "pi_kind": kind,
        }
        safe_write(self, f"data: {json.dumps(first)}\n\n".encode(), flush=True)
        try:
            if kind == "llamacpp":
                gen = stream_llamacpp(peer, model, messages, temperature, max_tokens)
            else:
                gen = stream_ollama(peer, model, messages, temperature, max_tokens)
            for delta in gen:
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
                "pi_ms": elapsed,
                "pi_model": used,
                "pi_kind": kind,
            }
            safe_write(self, f"data: {json.dumps(final)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)
        except Exception as error:
            err = {"error": str(error)}
            safe_write(self, f"data: {json.dumps(err)}\n\n".encode(), flush=True)
            safe_write(self, b"data: [DONE]\n\n", flush=True)

    def _complete(self, peer, kind, model, messages, temperature, max_tokens, started) -> None:
        if kind == "llamacpp":
            content, used = chat_llamacpp(peer, model, messages, temperature, max_tokens)
        else:
            content, used = chat_ollama(peer, model, messages, temperature, max_tokens)
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
            "pi_ms": elapsed,
            "pi_model": used,
            "pi_kind": kind,
        }
        body = json.dumps(resp).encode()
        self.send_response(200)
        self._cors()
        self.send_header("content-type", "application/json")
        self.send_header("X-Pi-Peer", peer["name"])
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
        f"slots={runtime.INFER_SLOTS} cache_ttl={runtime.HEALTH_CACHE_TTL}s (ollama+llamacpp)",
        flush=True,
    )
    make_server().serve_forever()
