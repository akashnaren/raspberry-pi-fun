"""Qwen3 thinking levels for Ollama.

Low and Medium are direct answers (`think` false). Medium has a longer
answer budget than Low. High thinks, then stops at about 192 tokens or
25 seconds, whichever comes first, and asks for the answer in a second
call so `num_predict` cannot be spent entirely on reasoning. The answer
text never keeps a `<think>` block. Sampling follows the Qwen3 card:
non-thinking temperature 0.7, top_p 0.8, top_k 20, presence_penalty 1.5;
thinking temperature 0.6, top_p 0.95, top_k 20.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NON_THINK_TEMPERATURE = 0.7
NON_THINK_TOP_P = 0.8
THINK_TEMPERATURE = 0.6
THINK_TOP_P = 0.95
TOP_K = 20
PRESENCE_PENALTY = 1.5

LOW_PREDICT = 64
# The old medium answer was 256. Thinking no longer spends that budget,
# so the direct reply gets the extra room.
MEDIUM_PREDICT = 384
HIGH_PREDICT = 768
HIGH_THINK_BUDGET = 192
HIGH_THINK_SECONDS = 25.0

DIRECT_FALLBACK = "I didn't finish an answer. Ask again with a shorter question."
_THINK_OPEN = re.compile(r"<think(?:ing)?\b[^>]*>", re.I)
_THINK_CLOSE = re.compile(r"</think(?:ing)?\s*>", re.I)

FORCE_NOTE = "Answer now. Give only the final answer. Do not include reasoning."


@dataclass(frozen=True)
class DecodePlan:
    """One Low / Medium / High choice."""

    name: str
    think: bool
    temperature: float
    top_p: float
    top_k: int
    num_predict: int
    think_budget: int
    think_seconds: float = 0.0
    presence_penalty: float = 0.0

    def ollama_predict(self, answer_tokens: int | None = None) -> int:
        """Tokens Ollama may emit. `num_predict` counts thinking and the answer."""
        answer = self.num_predict if answer_tokens is None else int(answer_tokens)
        if self.think and self.think_budget:
            return answer + self.think_budget
        return answer


def _direct(name: str, num_predict: int) -> DecodePlan:
    return DecodePlan(
        name,
        False,
        NON_THINK_TEMPERATURE,
        NON_THINK_TOP_P,
        TOP_K,
        num_predict,
        0,
        presence_penalty=PRESENCE_PENALTY,
    )


def _thinking(name: str, num_predict: int, budget: int, seconds: float) -> DecodePlan:
    return DecodePlan(
        name,
        True,
        THINK_TEMPERATURE,
        THINK_TOP_P,
        TOP_K,
        num_predict,
        budget,
        seconds,
    )


def sample_knobs(plan: DecodePlan | None) -> tuple[float, int, float | None]:
    """Qwen3 sample. No plan uses the non-thinking card, including presence_penalty.

    A thinking plan leaves presence_penalty unset. Ollama's default is enough
    while the model is reasoning; the answer call sets the penalty itself.
    """
    if plan is None:
        return NON_THINK_TOP_P, TOP_K, PRESENCE_PENALTY
    penalty = float(plan.presence_penalty) if plan.presence_penalty else None
    return plan.top_p, plan.top_k, penalty


def decode_plan(name: str | None, prompt: str = "") -> DecodePlan | None:
    """Map a think level to Ollama's `think` flag and Qwen3 sampling.

    Unknown or blank names return None so a caller-supplied temperature stays.
    Low and Medium are direct answers. High is the only level that thinks.
    `prompt` is accepted so callers can pass the line without a second lookup.
    """
    del prompt
    key = (name or "").strip().lower()
    if key == "low":
        return _direct(key, LOW_PREDICT)
    if key == "medium":
        return _direct(key, MEDIUM_PREDICT)
    if key == "high":
        return _thinking(key, HIGH_PREDICT, HIGH_THINK_BUDGET, HIGH_THINK_SECONDS)
    return None


def stop_thinking(
    tokens: int,
    started: float,
    now: float,
    budget: int = HIGH_THINK_BUDGET,
    seconds: float = HIGH_THINK_SECONDS,
) -> bool:
    """True when the thinking cap or the wall clock has been reached."""
    if budget and tokens >= budget:
        return True
    if seconds and now - started >= seconds:
        return True
    return False


def reasoning_tokens(text: str) -> int:
    """A word-or-character count. Ollama does not report a mid-stream total."""
    raw = text or ""
    if not raw:
        return 0
    return max(len(raw.split()), (len(raw) + 3) // 4)


def clip_reasoning(text: str, budget: int) -> str:
    """Keep a prefix that fits the thinking budget. Zero budget keeps nothing."""
    raw = text or ""
    if budget <= 0:
        return ""
    if reasoning_tokens(raw) <= budget:
        return raw
    lo = 0
    hi = len(raw)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        piece = raw[:mid]
        if reasoning_tokens(piece) <= budget:
            best = piece
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def peel_think(text: str) -> tuple[str, str]:
    """Split answer text from `<think>` / `<thinking>` blocks.

    An unclosed block is reasoning, not answer text. Stray close tags are
    dropped. The answer is stripped so a tag cannot sit in saved history.
    """
    raw = text or ""
    thinking: list[str] = []
    answer: list[str] = []
    index = 0
    while index < len(raw):
        opened = _THINK_OPEN.search(raw, index)
        closed = _THINK_CLOSE.search(raw, index)
        if opened and (closed is None or opened.start() <= closed.start()):
            answer.append(raw[index : opened.start()])
            end = opened.end()
            closer = _THINK_CLOSE.search(raw, end)
            if closer is None:
                thinking.append(raw[end:].strip())
                index = len(raw)
                break
            thinking.append(raw[end : closer.start()].strip())
            index = closer.end()
            continue
        if closed:
            answer.append(raw[index : closed.start()])
            index = closed.end()
            continue
        answer.append(raw[index:])
        break
    kept = [part for part in thinking if part]
    return "".join(answer).strip(), "\n".join(kept).strip()


def split_ollama_message(message: dict | None) -> tuple[str, str]:
    """Separate Ollama's `thinking` channel from `content`.

    Tags that still land in content are peeled out of the answer.
    """
    if not isinstance(message, dict):
        return "", ""
    content = message.get("content") or ""
    native = message.get("thinking") or ""
    if not isinstance(content, str):
        content = str(content)
    if not isinstance(native, str):
        native = str(native)
    answer, leaked = peel_think(content)
    parts = [part for part in (native.strip(), leaked) if part]
    return answer, "\n".join(parts)


def with_force(messages: list, thought: str = "") -> list:
    """Ask for the answer after the thinking cap. This turn is not history.

    A partial thought is context for that one call. It is not saved.
    """
    note = FORCE_NOTE
    clipped = (thought or "").strip()
    if clipped:
        note = "Partial reasoning, for context only:\n" + clipped + "\n\n" + FORCE_NOTE
    return [*list(messages or []), {"role": "user", "content": note}]
