"""Per-chat token ledger. Real prompt_eval_count wins; estimates cover the next turn."""

from __future__ import annotations

import threading

from pair.turn.shape import estimate_tokens

VERBATIM_FRACTION = 0.40


class Ledger:
    """used / num_ctx for one chat. Compaction reads this and does not run inline."""

    def __init__(self, num_ctx: int = 2048):
        self.num_ctx = max(256, int(num_ctx))
        self.prompt_eval = 0
        self.estimated = 0
        self._lock = threading.Lock()

    def observe(self, prompt_eval_count: int) -> None:
        with self._lock:
            self.prompt_eval = max(0, int(prompt_eval_count))

    def note_estimate(self, text: str) -> int:
        count = estimate_tokens(text)
        with self._lock:
            self.estimated = count
        return count

    def used(self) -> int:
        with self._lock:
            if self.prompt_eval:
                return self.prompt_eval
            return self.estimated

    def ratio(self) -> float:
        return self.used() / self.num_ctx

    def within(self, other: int, tolerance: float = 0.05) -> bool:
        """True when `used` is within `tolerance` of an Ollama prompt_eval_count."""
        if other <= 0:
            return self.used() == 0
        return abs(self.used() - int(other)) / int(other) <= tolerance


_LEDGERS: dict[str, Ledger] = {}
_LOCK = threading.Lock()


def ledger_for(chat_id: str, num_ctx: int = 2048) -> Ledger:
    key = (chat_id or "").strip() or "default"
    with _LOCK:
        row = _LEDGERS.get(key)
        if row is None or row.num_ctx != int(num_ctx):
            row = Ledger(num_ctx)
            _LEDGERS[key] = row
        return row


def reset() -> None:
    with _LOCK:
        _LEDGERS.clear()


def verbatim_budget(num_ctx: int) -> int:
    """Recent turns stay verbatim up to 40% of the context."""
    return max(1, int(int(num_ctx) * VERBATIM_FRACTION))
