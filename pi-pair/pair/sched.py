"""One decode slot for every client, plus idle background work.

Three job types share this module. Interactive decode is one global slot for
Flash and Pro. Background decode (a summary) runs only when that slot and the
line are empty, and an interactive arrival cancels it. Tool jobs go to pi2 and
pi3 through the tool registry and never take the slot.

Each client may keep two requests queued behind the one that is running. A
full line, or a wait past three minutes, is a 503. Tools for the next request
start while the current decode still holds the slot. A hot board delays the
next decode. This never starts a second model call.
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
# Past this estimated wait the line answers 503 instead of growing.
WAIT_LIMIT_S = 180
# One running request is the slot itself. Two more may wait per client.
PER_CLIENT_QUEUED = 2
HEAT_C = 80.0
HEAT_DELAY_S = 5.0


class AtCapacity(Exception):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(BUSY)


class Ticket:
    """One place in line. `started` is set when a slot is taken."""

    def __init__(self, client: str = "") -> None:
        self.started = 0.0
        self.client = client
        self.seq = 0
        self.rr = 0
        self.holding = False
        self.cooled = False


class _StopCancel:
    """Adapt the older stop callback to the cancel flag."""

    def __init__(self, stop) -> None:
        self._stop = stop

    def gone(self) -> bool:
        try:
            return bool(self._stop())
        except Exception:
            return False


class _Rates:
    """Moving averages for prefill, decode, and the last prompt size."""

    def __init__(self) -> None:
        self.prefill_tps = 0.0
        self.decode_tps = 0.0
        self.prompt_tokens = 0.0
        self.answer_tokens = 0.0

    def note(
        self,
        prefill_tps: float,
        decode_tps: float,
        prompt_tokens: float,
        answer_tokens: float,
    ) -> None:
        self.prefill_tps = _ema(self.prefill_tps, prefill_tps)
        self.decode_tps = _ema(self.decode_tps, decode_tps)
        self.prompt_tokens = _ema(self.prompt_tokens, prompt_tokens)
        self.answer_tokens = _ema(self.answer_tokens, answer_tokens)

    def ready(self) -> bool:
        return (
            self.prefill_tps > 0
            and self.decode_tps > 0
            and self.prompt_tokens > 0
            and self.answer_tokens > 0
        )

    def turn_s(self) -> float:
        return (self.prompt_tokens / self.prefill_tps) + (
            self.answer_tokens / self.decode_tps
        )


def _ema(old: float, new: float) -> float:
    if new <= 0:
        return old
    if old <= 0:
        return float(new)
    return (0.3 * float(new)) + (0.7 * old)


class InferenceGate:
    """One shared decode slot, with a round-robin wait behind it."""

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
        self._seq = 0
        self._active: dict[str, int] = {}
        self._rates = _Rates()
        self._local = threading.local()
        self._bg_cancel: threading.Event | None = None
        self._tool_jobs = 0

    def in_flight(self) -> int:
        with self._cv:
            return self._in_flight

    def waiting(self) -> int:
        with self._cv:
            return len(self._queue)

    def queued_clients(self) -> list[str]:
        with self._cv:
            return [ticket.client for ticket in self._queue]

    def position(self, ticket: Ticket) -> int:
        """Place in line. The running decode is #1, so the first waiter is #2."""
        with self._cv:
            try:
                index = self._queue.index(ticket)
            except ValueError:
                return 0
            return self._in_flight + index + 1

    def note_rates(
        self,
        prefill_tps: float = 0,
        decode_tps: float = 0,
        prompt_tokens: float = 0,
        answer_tokens: float = 0,
    ) -> None:
        self._rates.note(prefill_tps, decode_tps, prompt_tokens, answer_tokens)

    def estimate_turn_s(self) -> float:
        if not self._rates.ready():
            return float(self._avg_hold)
        return self._rates.turn_s()

    def estimate_wait_s(self, ahead: int) -> float:
        if ahead <= 0:
            return 0.0
        return self.estimate_turn_s() * ahead

    def eta_s(self, pos: int) -> int:
        """Rough seconds until a waiter at `pos` (1-based) starts."""
        if pos <= 0:
            return 0
        ahead = math.ceil(pos / self.limit)
        if self._rates.ready():
            return int(self.estimate_wait_s(ahead))
        return int(self._avg_hold * ahead)

    def retry_after_s(self) -> int:
        pos = self.in_flight() + self.waiting() + 1
        return max(1, self.eta_s(pos))

    def try_acquire(self) -> bool:
        """Return immediately. False when a slot is busy or someone is waiting."""
        with self._cv:
            self._stop_background()
            if self._queue or self._in_flight >= self.limit:
                return False
            self._in_flight += 1
            return True

    def reserve_ticket(self, client: str = "") -> tuple[str, Ticket | None]:
        """`ready` took a slot. `wait` joined the line. `full` did neither.

        A free slot is taken only when the line is empty, so a new chat cannot
        pass someone who is already waiting. Either outcome cancels a summary.
        """
        with self._cv:
            self._stop_background()
            if not self._queue and self._in_flight < self.limit:
                return "ready", self._start_locked(client)
            if self._queued_locked(client) >= PER_CLIENT_QUEUED:
                return "full", None
            if len(self._queue) >= self.queue_limit:
                return "full", None
            if self._wait_too_long_locked():
                return "full", None
            ticket = Ticket(client)
            self._place_locked(ticket)
            self._cv.notify_all()
            return "wait", ticket

    def reserve(self, client: str = "") -> str:
        """`ready` took a slot. `wait` reserved a queue spot. `full` did neither."""
        status, ticket = self.reserve_ticket(client)
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
                pos = self._in_flight + self._queue.index(ticket) + 1
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
                    delay = 0.0 if ticket.cooled else heat_delay_s()
                    if delay > 0:
                        ticket.cooled = True
                        self._cv.release()
                        try:
                            time.sleep(min(HEAT_DELAY_S, float(delay)))
                        finally:
                            self._cv.acquire()
                        continue
                    self._grant_locked(ticket)
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
            if ticket is not None and ticket.holding:
                ticket.holding = False
                self._drop_active_locked(ticket.client)
            self._cv.notify_all()
        if getattr(self._local, "ticket", None) is ticket:
            self._local.ticket = None

    def take_next(self) -> Ticket | None:
        """Grant the slot to the next waiter when it is free. Bench use."""
        with self._cv:
            if self._in_flight or not self._queue:
                return None
            ticket = self._queue[0]
            self._grant_locked(ticket)
            self._cv.notify_all()
            return ticket

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

    def _start_locked(self, client: str) -> Ticket:
        ticket = Ticket(client)
        ticket.seq = self._seq
        self._seq += 1
        ticket.started = time.monotonic()
        ticket.holding = True
        self._in_flight += 1
        self._active[client] = self._active.get(client, 0) + 1
        return ticket

    def _grant_locked(self, ticket: Ticket) -> None:
        self._queue.popleft()
        self._in_flight += 1
        ticket.started = time.monotonic()
        ticket.holding = True
        self._active[ticket.client] = self._active.get(ticket.client, 0) + 1

    def _drop_active_locked(self, client: str) -> None:
        left = self._active.get(client, 0) - 1
        if left <= 0:
            self._active.pop(client, None)
        else:
            self._active[client] = left

    def _queued_locked(self, client: str) -> int:
        return sum(1 for ticket in self._queue if ticket.client == client)

    def _place_locked(self, ticket: Ticket) -> None:
        """Round-robin. One client's tickets stay in arrival order."""
        ticket.seq = self._seq
        self._seq += 1
        ticket.rr = self._queued_locked(ticket.client)
        for index, item in enumerate(self._queue):
            if (ticket.rr, ticket.seq) < (item.rr, item.seq):
                self._queue.insert(index, ticket)
                return
        self._queue.append(ticket)

    def _wait_too_long_locked(self) -> bool:
        if not self._rates.ready():
            return False
        pos = self._in_flight + len(self._queue) + 1
        return self.eta_s(pos) > WAIT_LIMIT_S

    def _stop_background(self) -> None:
        """Ask the idle summary to stop. Safe to call while holding the lock."""
        event = self._bg_cancel
        if event is not None:
            event.set()

    def enqueue_background(self, fn) -> bool:
        """Run `fn(cancel)` when no interactive job is active or waiting.

        One background job at a time. The event is set as soon as an
        interactive reserve or try_acquire arrives. The callable must stop
        when that event is set.
        """
        with self._cv:
            if self._bg_cancel is not None:
                return False
            cancel = threading.Event()
            self._bg_cancel = cancel

        def run() -> None:
            holding = False
            try:
                while not cancel.is_set():
                    with self._cv:
                        if cancel.is_set():
                            return
                        if self._in_flight == 0 and not self._queue:
                            self._in_flight += 1
                            holding = True
                            break
                    if cancel.wait(0.02):
                        return
                if not holding or cancel.is_set():
                    return
                fn(cancel)
            finally:
                with self._cv:
                    if holding and self._in_flight > 0:
                        self._in_flight -= 1
                    if self._bg_cancel is cancel:
                        self._bg_cancel = None
                    self._cv.notify_all()

        threading.Thread(target=run, name="background-decode", daemon=True).start()
        return True

    def enqueue_tool(self, name: str, payload: dict, invoke) -> dict:
        """Send a tool job to pi2 or pi3. This does not take the decode slot."""
        from pair.tools import Dispatcher

        with self._cv:
            self._tool_jobs += 1
            busy = self._in_flight
        try:
            result = Dispatcher(invoke).call(name, payload)
        finally:
            with self._cv:
                self._tool_jobs -= 1
        if self.in_flight() != busy:
            raise RuntimeError("a tool job took the decode slot")
        return result

    def job_counts(self) -> dict:
        with self._cv:
            return {
                "interactive": self._in_flight + len(self._queue),
                "background": 1 if self._bg_cancel is not None else 0,
                "tool": self._tool_jobs,
            }


def heat_delay_s(temp_c: float | None = None) -> float:
    """Seconds to wait before the next decode. Zero when the board is cool."""
    if temp_c is None:
        from pair.thermal import sample

        temp_c = sample().get("temp_c")
    if temp_c is None or float(temp_c) <= HEAT_C:
        return 0.0
    return min(HEAT_DELAY_S, float(temp_c) - HEAT_C)


def prepare_prefix(messages: list) -> int:
    """Touch the prompt text. This does not call a model."""
    total = 0
    for row in messages:
        if isinstance(row, dict):
            total += len(str(row.get("content") or ""))
    return total


def overlap(fn) -> bool:
    """Run non-decode work while a decode holds the slot."""
    from pair import runtime

    if runtime.gate.in_flight() <= 0:
        return False

    def run() -> None:
        try:
            fn()
        except Exception:
            return

    threading.Thread(target=run, name="handoff", daemon=True).start()
    return True


def observe_usage(usage: dict | None, answer_cap: int = 0) -> None:
    """Fold one finished decode into the wait estimate."""
    from pair import runtime

    row = usage if isinstance(usage, dict) else {}
    prefill_tokens = _num(row.get("prefill_tokens"))
    prefill_ms = _num(row.get("prefill_ms"))
    eval_tokens = _num(row.get("eval_tokens"))
    eval_ms = _num(row.get("eval_ms"))
    prefill_tps = (prefill_tokens * 1000 / prefill_ms) if prefill_ms > 0 else 0
    decode_tps = (eval_tokens * 1000 / eval_ms) if eval_ms > 0 else 0
    answer = answer_cap or eval_tokens
    runtime.gate.note_rates(prefill_tps, decode_tps, prefill_tokens, answer)


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def fairness_report() -> dict:
    """Six clients, four turns each. The gap is the most one client is ahead."""
    clients = [f"c{i}" for i in range(6)]
    order_gate = InferenceGate(1, queue_limit=8)
    order_gate.note_rates(1_000_000, 1_000_000, 1, 1)
    order_gate.reserve_ticket("boot")
    for name in clients:
        order_gate.reserve_ticket(name)
    order_gate.reserve_ticket("c0")
    order_gate.reserve_ticket("c1")
    order = order_gate.queued_clients()

    gate = InferenceGate(1, queue_limit=8)
    gate.note_rates(1_000_000, 1_000_000, 1, 1)
    _status, boot = gate.reserve_ticket("boot")
    for name in clients:
        gate.reserve_ticket(name)
    gate.release(boot)
    done = {name: 0 for name in clients}
    max_gap = 0
    steps = 0
    while min(done.values()) < 4 and steps < 40:
        ticket = gate.take_next()
        if ticket is None or ticket.client not in done:
            break
        done[ticket.client] += 1
        max_gap = max(max_gap, max(done.values()) - min(done.values()))
        gate.release(ticket)
        if done[ticket.client] < 4:
            status, _ticket = gate.reserve_ticket(ticket.client)
            if status == "full":
                break
        steps += 1
    return {
        "clients": 6,
        "turns": 4,
        "order": order,
        "max_gap": max_gap,
        "ok": order == ["c0", "c1", "c2", "c3", "c4", "c5", "c0", "c1"]
        and max_gap <= 2
        and min(done.values()) == 4,
    }


def estimate_report() -> dict:
    """Predict the next turn from earlier samples. Error must stay within 30%."""
    gate = InferenceGate(1)
    samples = (
        (100, 2.0, 40, 8.0),
        (80, 1.5, 50, 9.0),
        (120, 2.5, 30, 7.0),
        (90, 1.8, 45, 8.5),
    )
    for prefill_tokens, prefill_s, answer_tokens, answer_s in samples:
        gate.note_rates(
            prefill_tokens / prefill_s,
            answer_tokens / answer_s,
            prefill_tokens,
            answer_tokens,
        )
    predicted = gate.estimate_turn_s()
    actual = 11.0
    error = abs(predicted - actual) / actual
    ahead = gate.estimate_wait_s(2)
    ahead_error = abs(ahead - (actual * 2)) / (actual * 2)
    return {
        "predicted_s": round(predicted, 3),
        "actual_s": actual,
        "error": round(error, 3),
        "ahead_error": round(ahead_error, 3),
        "ok": error <= 0.30 and ahead_error <= 0.30,
    }


def admission_report() -> dict:
    """503 when eight are waiting, and when the estimate passes 180 s."""
    gate = InferenceGate(1, queue_limit=8)
    gate.note_rates(1000, 1000, 1, 1)
    gate.try_acquire()
    queued = 0
    for index in range(8):
        status, _ticket = gate.reserve_ticket(f"u{index}")
        if status == "wait":
            queued += 1
    overflow, _ticket = gate.reserve_ticket("u8")
    slow = InferenceGate(1, queue_limit=8)
    slow.note_rates(1, 1, 200, 200)
    slow.try_acquire()
    late, _ticket = slow.reserve_ticket("late")
    retry = slow.retry_after_s()
    return {
        "queued": queued,
        "ninth": overflow,
        "long_wait": late,
        "long_retry_after_s": retry,
        "ok": queued == 8 and overflow == "full" and late == "full" and retry > 180,
    }


def client_report() -> dict:
    """One client keeps two queued spots. A second client can still join."""
    gate = InferenceGate(1, queue_limit=8)
    gate.note_rates(1000, 1000, 1, 1)
    first, _ticket = gate.reserve_ticket("A")
    second, _ticket = gate.reserve_ticket("A")
    third, _ticket = gate.reserve_ticket("A")
    fourth, _ticket = gate.reserve_ticket("A")
    other, _ticket = gate.reserve_ticket("B")
    return {
        "first": first,
        "second": second,
        "third": third,
        "blocked": fourth,
        "other": other,
        "ok": first == "ready"
        and second == "wait"
        and third == "wait"
        and fourth == "full"
        and other == "wait",
    }


def heat_report() -> dict:
    sensor = heat_delay_s(None)
    warm = heat_delay_s(70)
    hot = heat_delay_s(85)
    return {
        "sensor_delay_s": sensor,
        "warm_delay_s": warm,
        "hot_delay_s": hot,
        "ok": warm == 0 and hot == HEAT_DELAY_S and sensor <= HEAT_DELAY_S,
    }


def background_cancel_report() -> dict:
    """An interactive arrival stops a running summary within 200 ms."""
    gate = InferenceGate(1, queue_limit=4)
    started = threading.Event()
    seen = threading.Event()

    def job(cancel: threading.Event) -> None:
        started.set()
        if cancel.wait(5):
            seen.set()

    enqueued = gate.enqueue_background(job)
    if not started.wait(1):
        return {
            "enqueued": enqueued,
            "cancelled": False,
            "elapsed_s": None,
            "ok": False,
        }
    second = gate.enqueue_background(lambda _cancel: None)
    began = time.perf_counter()
    status, ticket = gate.reserve_ticket("user")
    cancelled = seen.wait(0.2)
    elapsed = time.perf_counter() - began
    if status == "ready":
        gate.release(ticket)
    return {
        "enqueued": enqueued,
        "cancelled": cancelled,
        "elapsed_s": round(elapsed, 4),
        "second_while_busy": second,
        "ok": enqueued and cancelled and elapsed < 0.2 and second is False,
    }


def tool_job_report() -> dict:
    """A tool call stays on pi2 and does not take the decode slot."""
    from pair.tools import generation_routes

    gate = InferenceGate(1)
    seen: dict[str, str] = {}

    def invoke(node: str, tool, _payload: dict) -> dict:
        seen["node"] = node
        seen["path"] = tool.path
        return {"ok": True, "model": False}

    before = gate.in_flight()
    result = gate.enqueue_tool("search", {"q": "pi"}, invoke)
    return {
        "node": seen.get("node"),
        "path": seen.get("path"),
        "model": result.get("model"),
        "in_flight": gate.in_flight(),
        "routes": generation_routes(),
        "ok": seen.get("node") == "pi2"
        and result.get("model") is False
        and gate.in_flight() == before
        and generation_routes() == [],
    }


def handoff_report() -> dict:
    """A tool callable runs before the busy slot is released."""
    from pair import runtime

    previous = runtime.gate
    runtime.gate = InferenceGate(1)
    seen: list[int] = []
    try:
        runtime.gate.try_acquire()
        started = overlap(lambda: seen.append(1))
        for _ in range(50):
            if seen:
                break
            time.sleep(0.01)
        running = runtime.gate.in_flight()
        return {
            "started": started,
            "ran": len(seen) == 1,
            "in_flight": running,
            "ok": bool(started) and len(seen) == 1 and running == 1,
        }
    finally:
        runtime.gate.release()
        runtime.gate = previous
