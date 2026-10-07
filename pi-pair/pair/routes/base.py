from __future__ import annotations

import ipaddress
import json
import os
from urllib.parse import urlparse

from pair.core.errors import TOO_BIG, friendly_error

CHAT_BODY_CAP = 1_000_000
_DRAIN_CAP = 8 * 1024 * 1024


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


def write_event(handler, payload: dict) -> bool:
    apply_tier(handler, payload)
    return safe_write(handler, f"data: {json.dumps(payload)}\n\n".encode(), flush=True)


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


FLASH_MODE = None
apply_tier = None
mode_fields = None


class BaseRoutes:
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

    def _write_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
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
        from pair.model.sched import observe_usage

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
