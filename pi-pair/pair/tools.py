"""Tool registry and dispatch for pi2 and pi3.

A tool names the nodes that may run it, a timeout, and whether a second try
is safe. Dispatch picks the least loaded node, hedges to the next node, and
opens a breaker after repeated failures. No tool path is a generation route.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass

from pair.guard import WEAK_NAMES

GENERATION_PATHS = (
    "/api/chat",
    "/api/generate",
    "/v1/chat/completions",
)
HEDGE_S = 1.2
BREAKER_FAILS = 3
BREAKER_HOLD_S = 30.0
SEARCH_P95_TARGET_S = 3.0
SEARCH_MEDIAN_TARGET_S = 1.5


class ToolError(Exception):
    """Every node failed or the breaker left none to try."""


@dataclass(frozen=True)
class Tool:
    name: str
    path: str
    nodes: tuple[str, ...]
    timeout: float
    retry_safe: bool

    def __post_init__(self) -> None:
        if self.path in GENERATION_PATHS or self.path.startswith("/api/"):
            raise ValueError(f"{self.name} cannot target {self.path}")
        for node in self.nodes:
            if node not in WEAK_NAMES and node != "local":
                raise ValueError(f"{self.name} node {node} is not a tool host")


def registry() -> dict[str, Tool]:
    """The mesh tools. pi4 is not in this list."""
    return {
        "extract": Tool("extract", "/tools/extract", ("pi3", "pi2"), 20.0, True),
        "search": Tool("search", "/tools/search", ("pi2", "pi3"), 3.0, True),
        "images": Tool("images", "/tools/images", ("pi2", "pi3"), 5.0, True),
        "render_doc": Tool(
            "render_doc", "/tools/render_doc", ("pi3", "pi2"), 15.0, True
        ),
        "render_chart": Tool(
            "render_chart", "/tools/render_chart", ("pi3", "pi2"), 10.0, True
        ),
        "tokenize": Tool("tokenize", "/tools/tokenize", ("pi3",), 5.0, True),
        "compact_plan": Tool(
            "compact_plan", "/tools/compact_plan", ("pi3",), 5.0, True
        ),
        "memory": Tool("memory", "/tools/memory", ("pi3",), 5.0, True),
        "embed": Tool("embed", "/tools/embed", ("pi3",), 3.0, True),
    }


def generation_routes(tools: dict[str, Tool] | None = None) -> list[str]:
    """Paths that would send a tool at a chat model. Empty when the registry is safe."""
    found = []
    for tool in (tools or registry()).values():
        if tool.path in GENERATION_PATHS or any(
            node not in WEAK_NAMES and node != "local" for node in tool.nodes
        ):
            found.append(tool.path)
    return found


class _Breaker:
    def __init__(self) -> None:
        self.fails = 0
        self.open_until = 0.0

    def open(self, now: float) -> bool:
        return now < self.open_until

    def fail(self, now: float) -> None:
        self.fails += 1
        if self.fails >= BREAKER_FAILS:
            self.open_until = now + BREAKER_HOLD_S

    def ok(self) -> None:
        self.fails = 0
        self.open_until = 0.0


class Dispatcher:
    """Least-loaded call with a hedge and a per-node breaker."""

    def __init__(
        self,
        invoke,
        tools: dict[str, Tool] | None = None,
        load=None,
        hedge_s: float = HEDGE_S,
        clock=None,
    ):
        self.invoke = invoke
        self.tools = tools or registry()
        self.load = load or (lambda _names: {})
        self.hedge_s = float(hedge_s)
        self.clock = clock or time.monotonic
        self._breakers: dict[str, _Breaker] = {}
        self._samples: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def p95(self, name: str) -> float | None:
        with self._lock:
            rows = list(self._samples.get(name) or [])
        if len(rows) < 4:
            return None
        rows.sort()
        index = max(0, math.ceil(0.95 * len(rows)) - 1)
        return rows[index]

    def _breaker(self, node: str) -> _Breaker:
        with self._lock:
            row = self._breakers.get(node)
            if row is None:
                row = _Breaker()
                self._breakers[node] = row
            return row

    def _rank(self, nodes: tuple[str, ...]) -> list[str]:
        now = self.clock()
        loads = self.load(list(nodes)) or {}
        ranked = []
        for node in nodes:
            breaker = self._breaker(node)
            if breaker.open(now):
                continue
            row = loads.get(node) or {}
            score = (
                float(row.get("load") or 0),
                float(row.get("queue") or 0),
                float(row.get("temp_c") or 0),
                node,
            )
            ranked.append((score, node))
        ranked.sort()
        return [node for _score, node in ranked]

    def _record(self, name: str, elapsed: float) -> None:
        with self._lock:
            bucket = self._samples.setdefault(name, deque(maxlen=32))
            bucket.append(float(elapsed))

    def call(self, name: str, payload: dict) -> dict:
        tool = self.tools[name]
        order = self._rank(tool.nodes)
        if not order:
            raise ToolError(f"{name} has no open node")
        hedge = self.hedge_s
        seen = self.p95(name)
        if seen is not None and seen < hedge:
            hedge = seen
        started = self.clock()
        primary = order[0]
        box: dict = {}
        errors: list[str] = []

        def run(node: str) -> None:
            try:
                result = self.invoke(node, tool, payload)
            except Exception as exc:
                self._breaker(node).fail(self.clock())
                errors.append(f"{node}: {exc}")
                return
            if not isinstance(result, dict):
                self._breaker(node).fail(self.clock())
                errors.append(f"{node}: bad result")
                return
            self._breaker(node).ok()
            box.setdefault("result", result)
            box.setdefault("node", node)

        first = threading.Thread(target=run, args=(primary,), daemon=True)
        first.start()
        first.join(hedge)
        if "result" in box:
            self._record(name, self.clock() - started)
            return box["result"]
        backups = order[1:]
        if not tool.retry_safe:
            backups = []
        threads = [first]
        for node in backups:
            thread = threading.Thread(target=run, args=(node,), daemon=True)
            thread.start()
            threads.append(thread)
        deadline = started + tool.timeout
        while self.clock() < deadline and "result" not in box:
            if all(not thread.is_alive() for thread in threads):
                break
            time.sleep(0.01)
        for thread in threads:
            thread.join(0.05)
        if "result" not in box:
            raise ToolError(errors[-1] if errors else f"{name} timed out")
        self._record(name, self.clock() - started)
        return box["result"]


def hedged_latency(
    primary_s: float, backup_s: float, hedge_s: float = HEDGE_S
) -> float:
    """Wall time when the backup starts at the hedge and the first success wins."""
    if primary_s <= hedge_s:
        return primary_s
    return min(primary_s, hedge_s + backup_s)


def simulated_search_latencies(n: int = 200) -> list[float]:
    """VM stand-in. Most searches are short. A slow pi2 is hedged to pi3."""
    rows = []
    for index in range(n):
        primary = 2.4 if index % 10 == 0 else 0.35
        rows.append(hedged_latency(primary, 0.45, HEDGE_S))
    return rows


def latency_stats(samples: list[float]) -> dict:
    ordered = sorted(float(item) for item in samples)
    if not ordered:
        return {"n": 0, "median_s": 0.0, "p95_s": 0.0}

    def pick(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
        return ordered[index]

    return {"n": len(ordered), "median_s": pick(0.5), "p95_s": pick(0.95)}


def schedule_prefix_prime(messages: list[dict]) -> bool:
    """Warm the cached persona while tools run. Idle gate only. Never a second decode."""
    from pair import runtime
    from pair.chat import warm_chat_model
    from pair.modes import mode_table

    peers = list(runtime.PEERS)
    if not any(str(peer.get("name") or "") in WEAK_NAMES for peer in peers):
        return False
    brain = next(
        (
            peer
            for peer in peers
            if peer.get("name") == "pi4" and peer.get("generative")
        ),
        None,
    )
    if brain is None or runtime.gate.in_flight() or runtime.gate.waiting():
        return False
    model = str(mode_table().get("flash") or "")
    if not model:
        return False

    def run() -> None:
        if runtime.gate.in_flight():
            return
        warm_chat_model(brain, model, timeout=5)

    threading.Thread(target=run, name="prefix-prime", daemon=True).start()
    return True
