"""Keep-alive HTTP for the local Ollama. Stdlib only.

Chat, stream, and the startup warm share one pool per host. A patched
`urllib.request.urlopen` (the unit tests) is called as-is so those fakes still
see the request. Ollama speaks HTTP/1.1 and leaves the socket open; the pool
hands that socket to the next call. A closed or failed socket is dropped.
"""

from __future__ import annotations

import http.client
import socket
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

    def __init__(
        self, response: http.client.HTTPResponse, release, connection=None
    ) -> None:
        self.status = response.status
        self.headers = response.headers
        self._response = response
        self._release = release
        self._connection = connection
        self._closed = False

    def read(self, amt: int = -1) -> bytes:
        return self._response.read(amt)

    def readline(self, amt: int = -1) -> bytes:
        return self._response.readline(amt)

    def _sock(self):
        conn = self._connection
        if conn is not None and getattr(conn, "sock", None) is not None:
            return conn.sock
        return None

    def abort(self) -> None:
        """Unblock a readline on this body. The socket is not reused."""
        sock = self._sock()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.close()

    def close(self) -> None:
        """Return or drop the socket. An unfinished body is never drained."""
        if self._closed:
            return
        self._closed = True
        reuse = False
        try:
            response = self._response
            if response.isclosed():
                reuse = not response.will_close
            else:
                sock = self._sock()
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                try:
                    response.close()
                except Exception:
                    pass
                reuse = False
        except Exception:
            reuse = False
        self._release(reuse)

    def __enter__(self) -> _Body:
        return self

    def __exit__(self, *_args) -> bool:
        self.close()
        return False


def _abort_conn(conn: http.client.HTTPConnection) -> None:
    sock = getattr(conn, "sock", None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    try:
        conn.close()
    except Exception:
        pass


def _pool_post(url: str, body: bytes, timeout: float, cancel=None):
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
            token = None
            if cancel is not None:
                token = cancel.attach(lambda connection=conn: _abort_conn(connection))
            try:
                response = conn.getresponse()
            except Exception:
                if cancel is not None and cancel.gone():
                    from pair.core.cancel import ClientGone

                    raise ClientGone() from None
                raise
            finally:
                if cancel is not None and token:
                    cancel.detach(token)

            def release(reuse: bool, connection=conn, origin=host, number=port) -> None:
                _POOL.release(origin, number, connection, reuse)

            body_out = _Body(response, release, connection=conn)
            if cancel is not None and cancel.gone():
                body_out.abort()
                from pair.core.cancel import ClientGone

                raise ClientGone()
            return body_out
        except Exception as exc:
            _POOL.discard(conn)
            caught = exc
    assert caught is not None
    raise caught


def open_json_request(request, timeout: float, cancel=None):
    """POST JSON. Tests that replace urlopen receive the urllib request."""
    current = urllib.request.urlopen
    if current is not _STDLIB_URLOPEN:
        opened = current(request, timeout=timeout)
        if cancel is not None and cancel.gone():
            abort = getattr(opened, "abort", None)
            if abort is not None:
                abort()
            from pair.core.cancel import ClientGone

            raise ClientGone()
        return opened
    url = getattr(request, "full_url", None) or request.get_full_url()
    body = request.data or b""
    if isinstance(body, str):
        body = body.encode()
    return _pool_post(url, body, timeout, cancel=cancel)
