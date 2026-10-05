"""Math and multi-step misses are answered from the fetched notes.

The small local model does not rewrite those problems. A canned line never
comes through here.
"""

from __future__ import annotations

import re

MISS = "I could not get the result from the pages."

_MATH_PROBLEM = re.compile(
    r"\\[\[(]|\$\$|"
    r"\b(?:derivative|derivatives|integral|integrals|differentiate|equation|equations|"
    r"polynomial|quadratic|theorem|radius|radii)\b|"
    r"surface area|cubic centimeters|square centimeters|related rates|"
    r"rate at which|find the exact|solve for",
    re.I,
)
_STEPS_PROBLEM = re.compile(
    r"step by step|show your work|multi-step|break it down", re.I
)
_MATH_TOKEN = re.compile(
    r"\\(?:frac|pi|sqrt|cdot|int|sum|theta)|"
    r"\\\[|\\\(|\$\$|"
    r"π|"
    r"d[A-Za-z]/d[A-Za-z]|"
    r"\d+\s*/\s*\d+|"
    r"(?:=|≈)\s*[\d\\π]|"
    r"\br\s*=\s*\d",
    re.I,
)
_STOP = {
    "that",
    "this",
    "with",
    "from",
    "when",
    "what",
    "where",
    "which",
    "while",
    "being",
    "into",
    "about",
    "would",
    "could",
    "should",
    "there",
    "their",
    "have",
    "has",
    "been",
    "were",
    "your",
    "find",
    "exact",
    "step",
    "show",
    "work",
    "multi",
    "precise",
    "instant",
    "constant",
    "given",
    "using",
    "square",
    "cubic",
}


def is_grounded_problem(prompt: str) -> bool:
    text = prompt or ""
    return bool(_MATH_PROBLEM.search(text) or _STEPS_PROBLEM.search(text))


def answer_from_search(prompt: str, context: str) -> str | None:
    """A page passage, the miss sentence, or None when the model may answer."""
    if not is_grounded_problem(prompt):
        return None
    source = _source_text(context or "")
    if _MATH_PROBLEM.search(prompt or ""):
        passage = _math_passage(prompt, source)
    else:
        passage = _steps_passage(prompt, source)
    if not passage:
        return MISS
    return passage


def _source_text(context: str) -> str:
    page = ""
    marker = "Text from the first page:"
    if marker in context:
        page = context.split(marker, 1)[1].strip()
    snippets = []
    for line in context.splitlines():
        if line.startswith("- "):
            snippets.append(line[2:].strip())
    parts = [part for part in [page, *snippets] if part]
    return "\n".join(parts)


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text or ""))


def _content_terms(prompt: str) -> list[str]:
    seen: list[str] = []
    for word in re.findall(r"[a-z]{5,}", (prompt or "").lower()):
        if word in _STOP or word in seen:
            continue
        seen.append(word)
    return seen


def _math_passage(prompt: str, source: str) -> str:
    needed = _numbers(prompt)
    hits = list(_MATH_TOKEN.finditer(source))
    if not hits:
        return ""
    best = ""
    for hit in hits:
        left = max(0, hit.start() - 220)
        right = min(len(source), hit.end() + 380)
        chunk = source[left:right].strip()
        found = _numbers(chunk)
        if len(needed) >= 2 and len(needed & found) < 2:
            continue
        if len(needed) == 1 and not (needed & found):
            continue
        if not needed:
            terms = _content_terms(prompt)
            if sum(1 for term in terms if term in chunk.lower()) < 2:
                continue
        if len(chunk) > len(best):
            best = chunk
    return best


def _steps_passage(prompt: str, source: str) -> str:
    terms = _content_terms(prompt)
    if len(terms) < 2 or not source.strip():
        return ""
    picked: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", source):
        text = sentence.strip()
        if not text:
            continue
        hits = sum(1 for term in terms if term in text.lower())
        if hits >= 2:
            picked.append(text)
    passage = " ".join(picked).strip()
    if len(passage) < 120:
        return ""
    return passage[:900].rstrip()
