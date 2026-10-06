"""Stop a chat when the client leaves, or when a newer turn replaces it."""

from __future__ import annotations

import select
import socket
import threading


class ClientGone(Exception):
    """The client disconnected, or a retry cancelled this turn."""


class Cancel:
    """A flag plus closers that unblock a socket read from another thread."""

    def __init__(self) -> None:
        self._ev = threading.Event()
        self._closers: dict[int, object] = {}
        self._mu = threading.Lock()
        self._next = 1

    def set(self) -> None:
        self._ev.set()
        for closer in self._pop_closers():
            try:
                closer()
            except Exception:
                pass

    def gone(self) -> bool:
        return self._ev.is_set()

    def check(self) -> None:
        if self._ev.is_set():
            raise ClientGone()

    def attach(self, closer) -> int:
        """Run `closer` when the turn is cancelled. An already-set flag runs it now."""
        if closer is None:
            return 0
        with self._mu:
            token = self._next
            self._next += 1
            if self._ev.is_set():
                pending = closer
            else:
                self._closers[token] = closer
                pending = None
        if pending is not None:
            try:
                pending()
            except Exception:
                pass
        return token

    def detach(self, token: int) -> None:
        with self._mu:
            self._closers.pop(int(token), None)

    def wait(self, seconds: float) -> bool:
        return self._ev.wait(seconds)

    def _pop_closers(self) -> list:
        with self._mu:
            closers = list(self._closers.values())
            self._closers.clear()
            return closers


def peer_closed(sock) -> bool:
    """True when the client has hung up. A quiet open socket stays False."""
    if sock is None:
        return True
    readable, _, _ = select.select([sock], [], [], 0)
    if not readable:
        return False
    try:
        return sock.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
    except BlockingIOError:
        return False
    except OSError:
        return True
