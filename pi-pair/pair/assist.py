"""Keep ordinary replies helpful and free of product internals.

A harmful ask is one fixed short refusal. Self-harm mentions 988. The router
sends that line before search, canned text, or generation. A reply that uses
the same harmful wording is replaced with that refusal.

A soft refusal is retried only when the user prompt is a harmless shape and
the harmful check does not match. One nudge retry comes first. A second soft
refusal on a list or real-world question can be grounded by the caller (search
notes or Pro). The last resort is one honest sentence, never a canned item list.
"""

from __future__ import annotations

import re

from pair.charts import is_structured_request
from pair.lists import list_count, placeholder_only

HELPFUL_NUDGE = "Answer helpfully if the request is safe."

LIST_MISS = "I couldn't get a good list for that, try Pro or rephrase."
FACT_MISS = "I couldn't get a good answer for that, try Pro or rephrase."

HARM_REFUSAL = "I can't help with that."
CRISIS_REFUSAL = "I can't help with that. If you are in crisis, call or text 988."

ANSWER_HINT = (
    "Answer the user's question directly. "
    "Lists, greetings, and ordinary factual questions are welcome when they are safe. "
    "Answer helpfully if the request is safe. "
    "Do not mention the thinking control, model choice, routing, or private mesh."
)

LIST_HINT = (
    "The user wants a numbered list. Reply with one item per line, numbered from 1. "
    "Answer helpfully if the request is safe. "
    "Do not mention the thinking control, model choice, or routing."
)

_GREETING = re.compile(
    r"^(?:(?:hi|hello|hey|hiya|howdy|yo|sup)(?:\s+there)?|"
    r"good\s+(?:morning|afternoon|evening|night)|"
    r"what'?s\s+up|how\s+are\s+you|how'?s\s+it\s+going)"
    r"[!?.\s]*$",
    re.I,
)

_SELF_HARM = re.compile(
    r"\b(?:suicid\w*|self[-\s]?harm|kill myself|killing myself|end my life|"
    r"hang myself|hurt myself|cut myself)\b",
    re.I,
)

# Actionable harm. A false positive refuses; a how-to must not reach the model.
_HARMFUL = re.compile(
    "|".join(
        (
            _SELF_HARM.pattern,
            r"\b(?:pipe[\s-]?bombs?|pipebombs?|ieds?|detonators?|explosives?|"
            r"grenades?|molotovs?|bombs?)\b",
            r"\b(?:how to|make|build|manufacture)\b.{0,40}\b(?:gun|firearm|rifle|weapon)\b",
            r"\b(?:malware|ransomware|keylogger|rootkit|credential theft)\b",
            r"\b(?:hack(?:ing)? into|how to hack|steal (?:a |the |their |his |her )?"
            r"(?:password|credentials)|phishing)\b",
            r"\b(?:stalk(?:ing|ed|er)?|how to stalk)\b",
            r"\b(?:track(?:ing)?|follow(?:ing)?|tail(?:ing)?|locat(?:e|ing))\b.{0,48}"
            r"\b(?:someone|somebody|a person|people|him|her|them)\b",
            r"\b(?:find|locate|track|get|obtain)\b.{0,40}"
            r"\b(?:someone(?:'s|s)?|somebody(?:'s|s)?|their|his|her)\s+"
            r"(?:home|house|address|location|whereabouts)\b",
            r"\b(?:home|house|address|location|whereabouts)\s+of\s+"
            r"(?:someone|somebody|a person|him|her|them)\b",
            r"\b(?:kill|murder|assassinate|shoot|stab)\b.{0,32}"
            r"\b(?:someone|a person|him|her|them|people)\b",
            r"\b(?:meth(?:amphetamine)?|fentanyl|heroin|mdma)\b.{0,40}"
            r"\b(?:synth\w*|cook|recipe|steps?|make)\b",
            r"\b(?:synth\w*|cook)\b.{0,40}\b(?:meth(?:amphetamine)?|fentanyl|heroin|mdma)\b",
            r"\b(?:csam|child[\s-]?porn(?:ography)?|child[\s-]?sexual)\b",
            r"\b(?:child|children|kid|kids|minor|minors|underage)\b.{0,48}"
            r"\b(?:porn(?:ography)?|sexual(?:ly)?|nude|nudes|exploitation)\b",
            r"\b(?:porn(?:ography)?|nude|nudes|sexual)\b.{0,40}"
            r"\b(?:child|children|kid|kids|minor|minors|underage)\b",
            r"\b(?:obtain|download|get|find|buy)\b.{0,70}"
            r"\b(?:csam|child[\s-]?porn(?:ography)?)\b",
            r"\b(?:obtain|download|get|find|buy)\b.{0,50}"
            r"\b(?:images?|videos?|photos?|pictures?|nudes?|content|material)\b.{0,40}"
            r"\b(?:of\s+)?(?:a\s+|an\s+)?(?:minors?|children|kids|child|underage)\b",
            r"\b(?:images?|videos?|photos?|pictures?|nudes?)\b.{0,30}"
            r"\b(?:of\s+)?(?:minors?|children|a child|kids|underage)\b",
            r"\b(?:poison(?:ing|s)?|ricin|cyanide|novichok|sarin|anthrax)\b",
        )
    ),
    re.I,
)

_LIST_SHAPE = re.compile(
    r"\b(?:top\s+\d{1,2}|\d{1,2}\s+best|rank(?:ing)?|bullet list|checklist|enumerate|list)\b",
    re.I,
)

_FACTUAL = re.compile(
    r"(?:what(?:'s| is| are| was| were)|who(?:'s| is| are| was| were)|"
    r"when(?:'s| did| was| were| is)|where(?:'s| is| are| was| were)|"
    r"why(?:'s| is| are| did| do| does| was| were)|which|"
    r"how (?:many|much|old|tall|long|far|big|often|high)|"
    r"define|definition of|capital of)\b",
    re.I,
)

_SOFT = re.compile(
    r"\b(?:"
    r"i(?:'m| am) sorry,? but i can(?:not|'t) (?:assist|help)|"
    r"i can(?:not|'t) (?:assist|help) with (?:that|this)|"
    r"i(?:'m| am) (?:not able|unable) to (?:assist|help)|"
    r"i(?:'m| am) sorry,? i can(?:not|'t) (?:assist|help)"
    r")\b",
    re.I,
)

_META = re.compile(
    r"\b(?:"
    r"thinking\s+(?:level|control|effort)|"
    r"(?:low|medium|high)\s+(?:thinking|effort)|"
    r"effort\s+(?:level|setting)|"
    r"flash\s+map|"
    r"(?:flash|pro)\s+(?:mode|model|route)|"
    r"auto\s+mode|"
    r"model\s+routing|"
    r"routing\s+label|"
    r"private\s+(?:pi\s+)?mesh|"
    r"pi\s+private\s+mesh|"
    r"mesh\s+assistant|"
    r"canned\s+map|"
    r"generative\s+brain"
    r")\b",
    re.I,
)

_INFRA = re.compile(
    r"\b(?:"
    r"private\s+(?:pi\s+)?mesh|"
    r"pi\s+private\s+mesh|"
    r"mesh\s+assistant|"
    r"canned\s+map|"
    r"generative\s+brain|"
    r"pi[234]\b|"
    r"cache\s+path|"
    r"on-prem|"
    r"\bfleet\b"
    r")\b",
    re.I,
)

_OPENERS = (
    "i'm sorry",
    "i am sorry",
    "sorry,",
    "sorry ",
    "i can't",
    "i cannot",
    "i can not",
    "unfortunately",
)

_FENCE = re.compile(r"(```[\s\S]*?```)")


def _fold(text: str) -> str:
    return (text or "").replace("’", "'").replace("‘", "'").replace("`", "'")


def _line(prompt: str) -> str:
    return " ".join((prompt or "").split())


def is_harmful(prompt: str) -> bool:
    """True for self-harm, weapons, malware, stalking, drug synthesis, or CSAM."""
    return bool(_HARMFUL.search(prompt or ""))


def refusal_for(text: str) -> str:
    """One short refusal. Self-harm includes the 988 line. No how-to, no jargon."""
    if _SELF_HARM.search(text or ""):
        return CRISIS_REFUSAL
    return HARM_REFUSAL


def is_casual_greeting(prompt: str) -> bool:
    return bool(_GREETING.match(_line(prompt)))


def is_list_shape(prompt: str) -> bool:
    text = prompt or ""
    return bool(list_count(text) or _LIST_SHAPE.search(text))


def is_plain_factual(prompt: str) -> bool:
    text = _line(prompt)
    if not text or len(text) > 240 or is_harmful(text):
        return False
    return bool(_FACTUAL.match(text))


def is_harmless_shape(prompt: str) -> bool:
    """Top-N, list, rank, greeting, or a short factual question."""
    if is_harmful(prompt):
        return False
    if is_casual_greeting(prompt) or is_list_shape(prompt):
        return True
    return is_plain_factual(prompt)


def may_retry_refusal(prompt: str) -> bool:
    """Retry a soft refusal only for a harmless shape that also passes the check."""
    return is_harmless_shape(prompt) and not is_harmful(prompt)


def wants_grounded_retry(prompt: str) -> bool:
    """A list or real-world question can try search notes or Pro. A greeting does not."""
    return may_retry_refusal(prompt) and not is_casual_greeting(prompt)


def is_soft_refusal(text: str) -> bool:
    return bool(_SOFT.search(_fold(text)))


def friendly_greeting(prompt: str) -> str:
    folded = " ".join((prompt or "").lower().split())
    if folded.startswith("good morning"):
        return "Good morning! How can I help?"
    if folded.startswith("good afternoon"):
        return "Good afternoon! How can I help?"
    if folded.startswith("good evening") or folded.startswith("good night"):
        return "Good evening! How can I help?"
    if folded.startswith("hey"):
        return "Hey! How can I help?"
    return "Hi! How can I help?"


def answer_hint_for(prompt: str) -> str | None:
    """A short system hint for a harmless question. Plots already have one."""
    if is_harmful(prompt) or is_structured_request(prompt) or not is_harmless_shape(prompt):
        return None
    if is_list_shape(prompt):
        return LIST_HINT
    return ANSWER_HINT


def leaks_infra(text: str) -> bool:
    return bool(_INFRA.search(text or "") or _META.search(text or ""))


def _drop_meta(prose: str) -> str:
    if not _META.search(prose):
        return prose
    kept = []
    for line in prose.splitlines():
        if re.match(r"\s*\d+\.\s+\S", line) and not _META.search(line):
            kept.append(line)
            continue
        pieces = re.split(r"(?<=[.!?])\s+", line)
        good = [piece for piece in pieces if piece.strip() and not _META.search(piece)]
        if good:
            kept.append(" ".join(good))
    return "\n".join(kept)


def scrub_reply(text: str) -> str:
    """Drop sentences that name effort, routing, or mesh internals.

    Fenced charts and tables stay intact. Text with none of those phrases
    is returned unchanged.
    """
    raw = text or ""
    if not _META.search(raw):
        return raw
    parts = _FENCE.split(raw)
    cleaned = [_drop_meta(part) if index % 2 == 0 else part for index, part in enumerate(parts)]
    return "\n".join(line for line in "".join(cleaned).splitlines() if line.strip()).strip()


def withhold_partial(text: str) -> bool:
    """Hold a stream that still looks like a soft refusal or an infra leak."""
    sample = " ".join(_fold(text).split())
    if not sample:
        return False
    if is_soft_refusal(sample) or _META.search(sample):
        return True
    if placeholder_only(text or ""):
        return True
    folded = sample.lower()
    if len(folded) > 180:
        return False
    return any(folded.startswith(opener) or opener.startswith(folded) for opener in _OPENERS)


def visible_canned(prompt: str, answer: str) -> str:
    """A stored greeting that talks about the mesh becomes a normal hello."""
    text = answer or ""
    if is_casual_greeting(prompt) and (leaks_infra(text) or is_soft_refusal(text)):
        return friendly_greeting(prompt)
    return text


def honest_fallback(prompt: str) -> str:
    """One sentence. No invented cars, movies, or other items."""
    if is_casual_greeting(prompt):
        return friendly_greeting(prompt)
    if is_list_shape(prompt):
        return LIST_MISS
    return FACT_MISS


def is_honest_miss(text: str) -> bool:
    folded = " ".join((text or "").split())
    return folded in {LIST_MISS, FACT_MISS}


def _usable(prompt: str, text: str) -> str:
    cleaned = scrub_reply(text or "")
    if not cleaned or is_soft_refusal(cleaned):
        return ""
    if is_casual_greeting(prompt) and leaks_infra(cleaned):
        return ""
    return cleaned


def settle_reply(prompt: str, text: str, retry, ground=None) -> str:
    """Scrub a harmless reply. One nudge, then one grounded retry, then one line.

    `retry` and `ground` are each called at most once. A harmful prompt, or a
    reply that uses the harmful lexicon, becomes the fixed refusal. Any other
    prompt that is not a harmless shape keeps a soft refusal as the model wrote it.
    """
    raw = text or ""
    if is_harmful(prompt):
        return refusal_for(prompt)
    if is_harmful(raw):
        return refusal_for(raw)
    if not is_harmless_shape(prompt):
        if is_soft_refusal(raw):
            return raw
        cleaned = scrub_reply(raw)
        return cleaned or raw
    if is_casual_greeting(prompt):
        return _usable(prompt, raw) or friendly_greeting(prompt)
    kept = _usable(prompt, raw)
    if kept:
        return kept
    if not is_soft_refusal(raw) and not _META.search(raw):
        return raw
    second = ""
    try:
        second = retry() or ""
    except Exception:
        second = ""
    kept = _usable(prompt, second)
    if kept:
        return kept
    if wants_grounded_retry(prompt) and ground is not None:
        try:
            third = ground() or ""
        except Exception:
            third = ""
        kept = _usable(prompt, third)
        if kept:
            return kept
    return honest_fallback(prompt)
