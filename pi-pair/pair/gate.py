"""How many generations may run at once.

Two chats may decode at once. Further chats wait in line. A full queue, or a
wait that runs out, is a busy line rather than an immediate error.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from contextlib import contextmanager

from pair.cancel import ClientGone
from pair.errors import BUSY

QUEUE_LIMIT = 8
# Answers often run for minutes. A waiter keeps its place for 15 minutes.
WAIT_TIMEOUT_S = 900.0


class AtCapacity(Exception):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(BUSY)


class Ticket:
    """One place in line. `started` is set when a slot is taken."""

    def __init__(self) -> None:
        self.started = 0.0


class _StopCancel:
    """Adapt the older stop callback to the cancel flag."""

    def __init__(self, stop) -> None:
        self._stop = stop

    def gone(self) -> bool:
        try:
            return bool(self._stop())
        except Exception:
            return False


class InferenceGate:
    """Bounded in-flight generations, plus a FIFO wait for the next slot."""

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
        self._cv = threading.Condition()
        self._queue: deque[Ticket] = deque()
        self._in_flight = 0
        self._avg_hold = 60.0
        self._local = threading.local()

    def in_flight(self) -> int:
        with self._cv:
            return self._in_flight

    def waiting(self) -> int:
        with self._cv:
            return len(self._queue)

    def position(self, ticket: Ticket) -> int:
        with self._cv:
            try:
                return self._queue.index(ticket) + 1
            except ValueError:
                return 0

    def eta_s(self, pos: int) -> int:
        """Rough seconds until a waiter at `pos` (1-based) starts."""
        if pos <= 0:
            return 0
        return int(self._avg_hold * math.ceil(pos / self.limit))

    def try_acquire(self) -> bool:
        """Return immediately. False when a slot is busy or someone is waiting."""
        with self._cv:
            if self._queue or self._in_flight >= self.limit:
                return False
            self._in_flight += 1
            return True

    def reserve_ticket(self) -> tuple[str, Ticket | None]:
        """`ready` took a slot. `wait` joined the line. `full` did neither.

        A free slot is taken only when the line is empty, so a new chat cannot
        pass someone who is already waiting.
        """
        with self._cv:
            if not self._queue and self._in_flight < self.limit:
                ticket = Ticket()
                ticket.started = time.monotonic()
                self._in_flight += 1
                return "ready", ticket
            if len(self._queue) >= self.queue_limit:
                return "full", None
            ticket = Ticket()
            self._queue.append(ticket)
            self._cv.notify_all()
            return "wait", ticket

    def reserve(self) -> str:
        """`ready` took a slot. `wait` reserved a queue spot. `full` did neither."""
        status, ticket = self.reserve_ticket()
        if status != "full":
            self._local.ticket = ticket
        return status

    def wait(
        self,
        ticket: Ticket | None,
        cancel=None,
        on_tick=None,
        timeout: float | None = None,
    ) -> bool:
        """Block until `ticket` is first in line and a slot is free."""
        if ticket is None:
            return False
        limit = self.wait_timeout if timeout is None else float(timeout)
        deadline = time.monotonic() + max(0.0, limit)
        last = None
        last_t = 0.0
        with self._cv:
            while True:
                if ticket not in self._queue:
                    return False
                gone = cancel is not None and cancel.gone()
                if gone or time.monotonic() >= deadline:
                    try:
                        self._queue.remove(ticket)
                    except ValueError:
                        pass
                    self._cv.notify_all()
                    if gone:
                        raise ClientGone()
                    return False
                pos = self._queue.index(ticket) + 1
                now = time.monotonic()
                if on_tick is not None and (pos != last or now - last_t >= 5):
                    last = pos
                    last_t = now
                    eta = self.eta_s(pos)
                    self._cv.release()
                    try:
                        on_tick(pos, eta)
                    finally:
                        self._cv.acquire()
                    continue
                if (
                    self._queue
                    and self._queue[0] is ticket
                    and self._in_flight < self.limit
                ):
                    self._queue.popleft()
                    self._in_flight += 1
                    ticket.started = time.monotonic()
                    self._cv.notify_all()
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    continue
                self._cv.wait(min(1.0, remaining))

    def acquire_reserved(
        self,
        timeout: float | None = None,
        stop=None,
        cancel=None,
        on_tick=None,
    ) -> bool:
        """Block for a slot. The caller already holds one wait reservation."""
        ticket = getattr(self._local, "ticket", None)
        if cancel is None and stop is not None:
            cancel = _StopCancel(stop)
        try:
            return self.wait(ticket, cancel=cancel, on_tick=on_tick, timeout=timeout)
        except ClientGone:
            if (
                stop is None
                and cancel is not None
                and not isinstance(cancel, _StopCancel)
            ):
                raise
            return False

    def cancel_wait(self) -> None:
        ticket = getattr(self._local, "ticket", None)
        self._local.ticket = None
        if ticket is None:
            return
        with self._cv:
            try:
                self._queue.remove(ticket)
            except ValueError:
                pass
            self._cv.notify_all()

    def release(self, ticket: Ticket | None = None) -> None:
        if ticket is None:
            ticket = getattr(self._local, "ticket", None)
        with self._cv:
            if self._in_flight <= 0:
                return
            self._in_flight -= 1
            if ticket is not None and ticket.started:
                held = max(0.0, time.monotonic() - ticket.started)
                self._avg_hold = (0.3 * held) + (0.7 * self._avg_hold)
                ticket.started = 0.0
            self._cv.notify_all()
        if getattr(self._local, "ticket", None) is ticket:
            self._local.ticket = None

    @contextmanager
    def generation(self):
        outcome = self.reserve()
        ticket = getattr(self._local, "ticket", None)
        if outcome == "full":
            raise AtCapacity(self.limit)
        if outcome == "wait" and not self.acquire_reserved():
            raise AtCapacity(self.limit)
        try:
            yield
        finally:
            self.release(ticket)
