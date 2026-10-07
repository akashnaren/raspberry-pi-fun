"""HTTP UI, OpenAI-compatible /v1/chat/completions, and the keyed public API."""

from __future__ import annotations

import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pair.routes.base as _base
import pair.routes.chat as _chat
import pair.routes.memory as _memory
import pair.routes.public_api as _public
import pair.routes.status as _status
from pair.core import runtime
from pair.core.config import STATIC_DIR
from pair.core.errors import GENERIC
from pair.model.chat_once import start_model_warm
from pair.model.modes import mode_table, mode_tips
from pair.model.preload import start_pro_warm
from pair.routes.base import BaseRoutes, safe_write
from pair.routes.chat import ChatRoutes
from pair.routes.files import FileRoutes
from pair.routes.flywheel import FlywheelRoutes
from pair.routes.images import ImageRoutes
from pair.routes.memory import MemoryRoutes
from pair.routes.public_api import PublicApiRoutes
from pair.routes.relay import RelayRoutes
from pair.routes.reply import ReplyRoutes
from pair.routes.search import SearchRoutes
from pair.routes.static import _TYPES, StaticRoutes, index_body, static_file
from pair.routes.status import StatusRoutes, health_document, public_health
from pair.routes.tools import ToolRoutes

_base.apply_tier = _chat.apply_tier
_base.mode_fields = _status.mode_fields
_base.FLASH_MODE = _public.FLASH_MODE
_memory._tuned_knobs = _chat._tuned_knobs


class Handler(
    ChatRoutes,
    ReplyRoutes,
    RelayRoutes,
    SearchRoutes,
    FileRoutes,
    ImageRoutes,
    ToolRoutes,
    MemoryRoutes,
    FlywheelRoutes,
    PublicApiRoutes,
    StatusRoutes,
    StaticRoutes,
    BaseRoutes,
    BaseHTTPRequestHandler,
):
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            page = STATIC_DIR / "index.html"
            stat = page.stat()
            tips = json.dumps(mode_tips(), sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha1(tips.encode()).hexdigest()[:16]
            key = (
                f"index:{digest}",
                stat.st_mtime_ns,
                stat.st_size,
                self._wants_gzip(),
            )
            self._send_cached("text/html; charset=utf-8", key, index_body)
            return
        asset = static_file(path)
        if asset is not None:
            stat = asset.stat()
            key = (path, stat.st_mtime_ns, stat.st_size, self._wants_gzip())
            kind = _TYPES.get(asset.suffix, "application/octet-stream")
            self._send_cached(kind, key, asset.read_bytes)
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
        if path == "/v1/images":
            self._images()
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
        if path == "/v1/memory/compact":
            self._memory_compact()
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


class PiServer(ThreadingHTTPServer):
    """The page reload opens many connections. The stdlib backlog of 5 overflows."""

    request_queue_size = 128


def make_server(
    host: str | None = None, port: int | None = None
) -> ThreadingHTTPServer:
    bind_host = runtime.HOST if host is None else host
    bind_port = runtime.PORT if port is None else port
    return PiServer((bind_host, bind_port), Handler)


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
