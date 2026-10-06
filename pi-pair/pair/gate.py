"""How many generations may run at once.

The scheduler lives in `pair.sched`. This module keeps the old import path.
"""

from pair.sched import (
    QUEUE_LIMIT,
    WAIT_TIMEOUT_S,
    AtCapacity,
    InferenceGate,
    Ticket,
)

__all__ = [
    "QUEUE_LIMIT",
    "WAIT_TIMEOUT_S",
    "AtCapacity",
    "InferenceGate",
    "Ticket",
]
