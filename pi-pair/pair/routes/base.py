from __future__ import annotations

import ipaddress
import json
import os
from urllib.parse import urlparse

from pair.core.errors import TOO_BIG, friendly_error
from pair.routes.limit import admit

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
# Tailscale mesh. Inbound peers on these ranges are LAN. Funnel and
# Cloudflare headers still force public, and outbound fetches still block them.
_TAILNET = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)
_PUBLIC_HIDDEN = {"x-pi-peer", "x-pi-chip"}
_CLOSED_EXACT = {
    "/docs",
    "/docs/",
    "/swagger",
    "/swagger/",
    "/openapi.json",
    "/swagger.json",
}
_PAGE_HEADERS = (
    "Content-Type, X-Pi-Mode, X-Pi-Target, X-Pi-Mesh, X-Pi-Think, "
    "X-Pi-Client, X-Pi-Chat, X-Pi-Request-Id"
)
_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "script-src 'self' 'unsafe-inline' https://unpkg.com; "
    "style-src 'self' 'unsafe-inline' https://unpkg.com; "
    "img-src 'self' data: blob: https://upload.wikimedia.org https://thumb.wikimedia.org; "
    "font-src 'self' data:; "
    "connect-src 'self'"
)
_SECURITY = (
    ("Content-Security-Policy", _CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
)


def _ip(host: str):
    try:
        ip = ipaddress.ip_address((host or "").strip())
    except ValueError:
        return None
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped if mapped is not None else ip


def _lan_address(host: str) -> bool:
    """True for loopback, RFC 1918, and the Tailscale mesh ranges.

    100.64.0.0/10 and fd7a:115c:a1e0::/48 are trusted peers. A request that
    carries Tailscale-Funnel-Request or a Cloudflare header is still public.
    """
    ip = _ip(host)
    if ip is None:
        return False
    if ip.is_loopback:
        return True
    return any(ip.version == net.version and ip in net for net in _RFC1918 + _TAILNET)


def _loopback(host: str) -> bool:
    ip = _ip(host)
    return bool(ip is not None and ip.is_loopback)


def _ip_token(value: str) -> str:
    """The address in one header value, or "" when it is not an IP."""
    token = (value or "").strip().strip('"')
    if token.startswith("[") and "]" in token:
        token = token[1 : token.index("]")]
    elif token.count(":") == 1:
        token = token.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(token))
    except ValueError:
        return ""


def _cloudflare_request(headers) -> bool:
    """cloudflared. Cf-Connecting-Ip is not a marker: Funnel forwards it.

    Recognized by Cf-Ray, or by Cdn-Loop containing "cloudflare".
    """
    if headers.get("Cf-Ray"):
        return True
    return "cloudflare" in str(headers.get("Cdn-Loop") or "").lower()


def _funnel_request(headers) -> bool:
    """Funnel sets Tailscale-Funnel-Request and Tailscale-Ingress-Src.

    Tailscale-Funnel-Request is the marker, including when the client
    also sent Cloudflare headers. Tailscale-Ingress-Src marks Funnel
    on its own only when those Cloudflare markers are absent, so a
    cloudflared client cannot rotate buckets by inventing it.
    """
    if headers.get("Tailscale-Funnel-Request") is not None:
        return True
    if _cloudflare_request(headers):
        return False
    return headers.get("Tailscale-Ingress-Src") is not None


def trusted_client_ip(peer: str, forwarded_for: str, headers=None) -> str:
    """Socket peer, unless that peer is the local tunnel proxy.

    Headers are read only for a loopback peer (127.0.0.1 or ::1).
    One ingress supplies the client key. Every other client-IP header
    is ignored. X-Forwarded-For is never a key.

    A Funnel request uses only Tailscale-Ingress-Src (an address or
    address:port). A cloudflared request uses only Cf-Connecting-Ip,
    and only when Tailscale-Funnel-Request is absent. A missing or
    invalid value uses the loopback peer, so those requests share one
    bucket. Any other peer keeps its socket address. The forwarded_for
    argument is ignored.
    """
    peer = (peer or "").strip()
    if not _loopback(peer):
        return peer
    if headers is None or not hasattr(headers, "get"):
        return peer
    if _funnel_request(headers):
        return _ip_token(str(headers.get("Tailscale-Ingress-Src") or "")) or peer
    if _cloudflare_request(headers):
        return _ip_token(str(headers.get("Cf-Connecting-Ip") or "")) or peer
    return peer


def _marked_public(headers) -> bool:
    """True when a tunnel header says this request did not come from the LAN."""
    if headers is None or not hasattr(headers, "get"):
        return False
    if headers.get("Tailscale-Funnel-Request") is not None:
        return True
    if headers.get("Cf-Ray") or headers.get("Cf-Connecting-Ip"):
        return True
    loop = str(headers.get("Cdn-Loop") or "").lower()
    return "cloudflare" in loop


def public_closed(path: str) -> bool:
    """Docs and tool routes stay off the public tunnel."""
    path = (path or "").split("?", 1)[0]
    if path == "/tools" or path.startswith("/tools/"):
        return True
    return path in _CLOSED_EXACT


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
    """Origin to echo, or empty when the browser must not be allowed.

    The page's own host matches without a setting. PI_PAIR_CORS_ORIGINS adds
    more, comma-separated, and only the /api routes pass that list.
    There is no wildcard.
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
        self._security_sent = False
        super().send_response(code, message)

    def end_headers(self) -> None:
        if not getattr(self, "_security_sent", False):
            self._security_sent = True
            for name, value in _SECURITY:
                super().send_header(name, value)
        super().end_headers()

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

    def _peer(self) -> str:
        address = getattr(self, "client_address", None)
        if not address:
            return ""
        return str(address[0] or "")

    def _client_ip(self) -> str:
        """Address for the rate limit. Tunnel headers count only on loopback."""
        return trusted_client_ip(self._peer(), "", getattr(self, "headers", None))

    def _public(self) -> bool:
        """True on Funnel or Cloudflare, or when the socket peer is not on the LAN.

        Loopback, RFC 1918, and the tailnet (100.64.0.0/10, fd7a:115c:a1e0::/48)
        are the mesh. Funnel arrives from 127.0.0.1, so Tailscale-Funnel-Request
        marks those visitors public even when the peer address is a mesh address.
        X-Forwarded-For never turns a public visitor back into a LAN client.
        """
        if _marked_public(getattr(self, "headers", None)):
            return True
        return not _lan_address(self._peer())

    def _request_length(self) -> int:
        headers = getattr(self, "headers", None)
        if headers is None or not hasattr(headers, "get"):
            return 0
        raw = headers.get("content-length")
        if raw is None or str(raw).strip() == "":
            return 0
        try:
            length = int(str(raw).strip())
        except ValueError:
            return 0
        return length if length > 0 else 0

    def _drain_request_body(self) -> None:
        length = self._request_length()
        if length > _DRAIN_CAP:
            self.close_connection = True
            return
        self._drain_body(length)

    def _limited(self) -> bool:
        """429 when this public client is out of tokens. LAN traffic is open."""
        if not self._public():
            return False
        path = (getattr(self, "path", "") or "").split("?", 1)[0]
        allowed, retry = admit(self._client_ip(), path)
        if allowed:
            return False
        self._drain_request_body()
        self._error(
            "Too many requests. Try again in a moment.",
            status=429,
            headers={"Retry-After": str(retry)},
        )
        return True

    def _guard(self) -> bool:
        """Answer a public request that is too fast or pointed at a closed route."""
        if self._limited():
            return True
        path = (getattr(self, "path", "") or "").split("?", 1)[0]
        if self._public() and public_closed(path):
            self._drain_request_body()
            self.send_response(404)
            self.end_headers()
            return True
        return False

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
            origin = allowed_api_origin(
                self.headers.get("Origin", ""),
                self.headers.get("Host", ""),
                "",
            )
            self.send_header("Vary", "Origin")
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", _PAGE_HEADERS)
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
