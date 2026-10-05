"""How many generations may run at once.

The cap is a permit, not a queue. A caller either gets a slot immediately or
is told the board is full. Waiting here is what stacked every chat behind one
slow decode.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager


def capacity_message(limit: int) -> str:
    return (
        f"pi4 is at capacity ({limit} generations in flight). "
        "Try again in a moment."
    )


class AtCapacity(Exception):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(capacity_message(limit))


class InferenceGate:
    """Bounded in-flight generations against one already-loaded model."""

    def __init__(self, limit: int):
        if limit < 1:
            raise ValueError("inference cap must be at least 1")
        self.limit = int(limit)
        self._sem = threading.BoundedSemaphore(self.limit)
        self._mu = threading.Lock()
        self._in_flight = 0

    def in_flight(self) -> int:
        with self._mu:
            return self._in_flight

    def try_acquire(self) -> bool:
        """Return immediately. False means every slot is already decoding."""
        if not self._sem.acquire(blocking=False):
            return False
        with self._mu:
            self._in_flight += 1
        return True

    def release(self) -> None:
        with self._mu:
            if self._in_flight <= 0:
                return
            self._in_flight -= 1
        self._sem.release()

    @contextmanager
    def generation(self):
        if not self.try_acquire():
            raise AtCapacity(self.limit)
        try:
            yield
        finally:
            self.release()
