"""How many generations may run at once.

Two chats may decode at once. Further chats wait in a short queue. A full
queue, or a wait that runs out, is a busy line rather than an immediate error.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from pair.errors import BUSY

QUEUE_LIMIT = 8
# Answers often run for minutes. A one-minute wait drops the chat first.
WAIT_TIMEOUT_S = 600.0
_POLL_S = 0.4


class AtCapacity(Exception):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(BUSY)


class InferenceGate:
    """Bounded in-flight generations, plus a bounded wait for the next slot."""

    def __init__(
        self,
        limit: int,
        queue_limit: int = QUEUE_LIMIT,
        wait_timeout: float = WAIT_TIMEOUT_S,
    ):
        if limit < 1:
            raise ValueError("inference cap must be at least 1")
        self.limit = int(limit)
        self.queue_limit = max(0, int(queue_limit))
        self.wait_timeout = float(wait_timeout)
        self._sem = threading.BoundedSemaphore(self.limit)
        self._mu = threading.Lock()
        self._in_flight = 0
        self._waiting = 0

    def in_flight(self) -> int:
        with self._mu:
            return self._in_flight

    def waiting(self) -> int:
        with self._mu:
            return self._waiting

    def try_acquire(self) -> bool:
        """Return immediately. False means every slot is already decoding."""
        if not self._sem.acquire(blocking=False):
            return False
        with self._mu:
            self._in_flight += 1
        return True

    def reserve(self) -> str:
        """`ready` took a slot. `wait` reserved a queue spot. `full` did neither."""
        if self.try_acquire():
            return "ready"
        with self._mu:
            if self._waiting >= self.queue_limit:
                return "full"
            self._waiting += 1
            return "wait"

    def acquire_reserved(self, timeout: float | None = None, stop=None) -> bool:
        """Block for a slot. The caller already holds one wait reservation.

        `stop` is checked about every half second. A disconnect ends the wait
        without taking a slot. The wait itself lasts until `timeout`.
        """
        limit = self.wait_timeout if timeout is None else float(timeout)
        deadline = time.monotonic() + max(0.0, limit)
        got = False
        try:
            while True:
                if stop is not None and stop():
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if self._sem.acquire(timeout=min(_POLL_S, remaining)):
                    got = True
                    break
        finally:
            with self._mu:
                if self._waiting > 0:
                    self._waiting -= 1
        if not got:
            return False
        with self._mu:
            self._in_flight += 1
        return True

    def cancel_wait(self) -> None:
        with self._mu:
            if self._waiting > 0:
                self._waiting -= 1

    def release(self) -> None:
        with self._mu:
            if self._in_flight <= 0:
                return
            self._in_flight -= 1
        self._sem.release()

    @contextmanager
    def generation(self):
        outcome = self.reserve()
        if outcome == "full":
            raise AtCapacity(self.limit)
        if outcome == "wait" and not self.acquire_reserved():
            raise AtCapacity(self.limit)
        try:
            yield
        finally:
            self.release()
