"""Keep-alive HTTP for the local Ollama. Stdlib only.

Chat, stream, and the startup warm share one pool per host. A patched
`urllib.request.urlopen` (the unit tests) is called as-is so those fakes still
see the request. Ollama speaks HTTP/1.1 and leaves the socket open; the pool
hands that socket to the next call. A closed or failed socket is dropped.
"""

from __future__ import annotations

import http.client
import threading
import urllib.request
from urllib.parse import urlparse

_STDLIB_URLOPEN = urllib.request.urlopen
_MAX_IDLE = 4


class _Pool:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle: dict[tuple[str, int], list[http.client.HTTPConnection]] = {}

    def checkout(
        self, host: str, port: int, timeout: float
    ) -> http.client.HTTPConnection:
        key = (host, port)
        with self._lock:
            bucket = self._idle.get(key)
            if bucket:
                return bucket.pop()
        return http.client.HTTPConnection(host, port, timeout=timeout)

    def release(
        self, host: str, port: int, conn: http.client.HTTPConnection, reuse: bool
    ) -> None:
        if not reuse:
            self.discard(conn)
            return
        key = (host, port)
        with self._lock:
            bucket = self._idle.setdefault(key, [])
            if len(bucket) >= _MAX_IDLE:
                self._close(conn)
                return
            bucket.append(conn)

    def discard(self, conn: http.client.HTTPConnection) -> None:
        self._close(conn)

    @staticmethod
    def _close(conn: http.client.HTTPConnection) -> None:
        try:
            conn.close()
        except Exception:
            pass


_POOL = _Pool()


class _Body:
    """The slice of a urlopen result that chat and the startup warm use."""

    def __init__(self, response: http.client.HTTPResponse, release) -> None:
        self.status = response.status
        self.headers = response.headers
        self._response = response
        self._release = release
        self._closed = False

    def read(self, amt: int = -1) -> bytes:
        return self._response.read(amt)

    def readline(self, amt: int = -1) -> bytes:
        return self._response.readline(amt)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        reuse = False
        try:
            response = self._response
            if not response.isclosed():
                response.read()
            reuse = not response.will_close
        except Exception:
            reuse = False
        self._release(reuse)

    def __enter__(self) -> _Body:
        return self

    def __exit__(self, *_args) -> bool:
        self.close()
        return False


def _pool_post(url: str, body: bytes, timeout: float):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", ""):
        request = urllib.request.Request(
            url,
            data=body,
            headers={"content-type": "application/json"},
        )
        return _STDLIB_URLOPEN(request, timeout=timeout)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "Connection": "keep-alive",
    }
    caught: Exception | None = None
    for _attempt in (1, 2):
        conn = _POOL.checkout(host, port, timeout)
        try:
            conn.timeout = timeout
            if conn.sock is not None:
                conn.sock.settimeout(timeout)
            conn.request("POST", path, body=body, headers=headers)
            response = conn.getresponse()

            def release(reuse: bool, connection=conn, origin=host, number=port) -> None:
                _POOL.release(origin, number, connection, reuse)

            return _Body(response, release)
        except Exception as exc:
            _POOL.discard(conn)
            caught = exc
    assert caught is not None
    raise caught


def open_json_request(request, timeout: float):
    """POST JSON. Tests that replace urlopen receive the urllib request."""
    current = urllib.request.urlopen
    if current is not _STDLIB_URLOPEN:
        return current(request, timeout=timeout)
    url = getattr(request, "full_url", None) or request.get_full_url()
    body = request.data or b""
    if isinstance(body, str):
        body = body.encode()
    return _pool_post(url, body, timeout)
