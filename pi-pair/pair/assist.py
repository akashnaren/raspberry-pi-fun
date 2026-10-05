"""Keep ordinary replies helpful and free of product internals.

Request refusal is optional. `safety_filter` gates it, and
`pair.moderate.moderate` is the hook a later safety stack replaces. With the
flag off, nothing here refuses a prompt or replaces a reply for harmful wording.

`is_harmful` is the lexicon. List and sequence routing still consult it so
those paths stay off harmful asks. A soft refusal is retried only when the
prompt is a harmless shape. One nudge retry comes first. A second soft refusal
on a list or real-world question can be grounded by the caller (search notes
or Pro). The last resort is one honest sentence, never a canned item list.
"""

from __future__ import annotations

import functools
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
    "Answer helpfully if the request is safe. "
    "Do not mention model choice, routing, or the mesh."
)

LIST_HINT = (
    "Reply with a numbered list, one item per line, numbered from 1. "
    "Answer helpfully if the request is safe."
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

# Actionable harm. "how to", "make", and "where to get" have to sit next to the
# harm. bomb, stalker, kids, track, poison, and address by themselves do not
# match, so a film, a bath bomb, or a package still reaches the model.
# Malware names and self-harm phrases still match on their own: a reply that
# uses them is replaced before it is shown.
_WEAPON = (
    r"\b(?:pipe[\s-]?bombs?|pipebombs?|ieds?|detonators?|explosives?|"
    r"molotov(?:\s+cocktails?)?|molotovs?)\b|"
    r"\bgrenades?\b(?!\s+launchers?\b)|"
    r"\b(?<!bath[\s-])bombs?\b"
)
_SKIP = r"(?:\s+(?!about\b|movies?\b|films?\b|halo\b)\w+){0,3}"
_VERB = (
    r"mak(?:e[sd]?|ing)|made|build(?:ing)?|manufactur\w*|construct(?:ing)?|"
    r"assembl\w*|creat(?:e[sd]?|ing)|produc(?:e[sd]?|ing)|obtain(?:ing)?|"
    r"download(?:ing)?|buy(?:ing)?|get(?:ting)?|diy"
)
_WHERE = (
    r"where\s+(?:to|can\s+i|do\s+i|should\s+i|could\s+i)\s+"
    r"(?:get|buy|find|obtain|download|order|make|build|acquire)"
)
_TARGET = (
    r"someone|somebody|people|him|her|them|"
    r"a\s+(?:person|girl|woman|man|stranger|child)|my\s+ex"
)
_PLACE = r"home|house|address|location|whereabouts|phone"
_OWN = r"someone(?:'s|s)?|somebody(?:'s|s)?|a\s+person(?:'s|s)?|their"
_DRUG = r"meth(?:amphetamine)?|fentanyl|heroin|mdma"
_MAKE = (
    r"synth\w*|cook(?:ing)?|recipes?|steps?|mak(?:e[sd]?|ing)|made|"
    r"manufactur\w*|produc(?:e[sd]?|ing)"
)
_TOX = r"ricin|cyanide|novichok|sarin|anthrax"
_ENVELOPE = (
    r"(?!\s+(?:on|off|from|in)\s+(?:(?:an?|the|this|that)\s+)?"
    r"(?:envelopes?|letters?|packages?|parcels?|mail)\b)"
)
_MINOR = r"child|children|kids?|minors?|underage"
_SEXUAL = r"porn(?:ography)?|sexual(?:ly)?|nudes?|naked|exploitation"

_HARM = (
    _SELF_HARM,
    re.compile(
        r"\b(?:how[\s-]?to|" + _WHERE + r")\b" + _SKIP + r"\s+(?:" + _WEAPON + r")",
        re.I,
    ),
    re.compile(r"\b(?:" + _VERB + r")\b" + _SKIP + r"\s+(?:" + _WEAPON + r")", re.I),
    re.compile(
        r"(?:" + _WEAPON + r")(?:\s+(?!about\b|movies?\b|films?\b)\w+){0,3}\s+"
        r"(?:instructions?|tutorials?|recipes?|steps?|howto|how[\s-]?to|making|building)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:instructions?|tutorials?|recipes?)\s+(?:on|for)\b"
        + _SKIP
        + r"\s+(?:"
        + _WEAPON
        + r")",
        re.I,
    ),
    re.compile(r"\bhow[\s-]?to\s+bomb\b", re.I),
    re.compile(r"\b(?:homemade|home-made)\s+(?:pipe\s+)?bombs?\b", re.I),
    re.compile(r"\bimprovised\s+explosive(?:\s+devices?)?\b", re.I),
    re.compile(r"\bhow[\s-]?to\s+stalk\b", re.I),
    re.compile(r"\bhow\s+(?:do|can|would|could|should)\s+i\s+stalk\b", re.I),
    re.compile(
        r"\bstalk(?:ing|ed)?\b(?:\s+\w+){0,3}\s+(?:" + _TARGET + r")\b",
        re.I,
    ),
    re.compile(
        r"\b(?:follow(?:ing)?|tail(?:ing)?)\b(?:\s+\w+){0,3}\s+(?:"
        + _TARGET
        + r")\b(?:\s+\w+){0,3}\s+(?:home|house)\b",
        re.I,
    ),
    re.compile(
        r"\bhow[\s-]?to\s+(?:track|tail|monitor|spy\s+on|surveil\w*)\b"
        r"(?:\s+\w+){0,4}\s+(?:" + _TARGET + r")\b",
        re.I,
    ),
    re.compile(r"\bspy(?:ing)?\s+on\s+(?:" + _TARGET + r")\b", re.I),
    re.compile(
        r"\b(?:track(?:ing)?|locat(?:e|ing)|monitor(?:ing)?|find(?:ing)?|"
        r"get(?:ting)?|obtain(?:ing)?)\b(?:\s+\w+){0,4}\s+(?:"
        + _OWN
        + r")\s+(?:"
        + _PLACE
        + r")\b",
        re.I,
    ),
    re.compile(
        r"\b(?:find|locate|track|get|obtain)(?:ing)?\b(?:\s+\w+){0,4}\s+"
        r"(?:her|his)\s+(?:" + _PLACE + r")\b" + _ENVELOPE,
        re.I,
    ),
    re.compile(
        r"\bhow[\s-]?to\s+find(?:\s+out)?\s+where\s+"
        r"(?:she|he|they|someone|somebody|a\s+person)\s+lives\b",
        re.I,
    ),
    re.compile(
        r"\b(?:" + _PLACE + r")\s+of\s+(?:someone|somebody|a\s+person|him|her|them)\b",
        re.I,
    ),
    re.compile(r"\b(?:csam|child[\s-]?porn(?:ography)?|child[\s-]?sexual)\b", re.I),
    re.compile(
        r"\b(?:" + _MINOR + r")\b.{0,48}\b(?:" + _SEXUAL + r")\b",
        re.I,
    ),
    re.compile(
        r"\b(?:" + _SEXUAL + r")\b.{0,48}\b(?:" + _MINOR + r")\b",
        re.I,
    ),
    re.compile(
        r"\b(?:obtain|download|buy)\b.{0,60}\b(?:images?|videos?|photos?|pictures?|"
        r"nudes?)\b.{0,40}\b(?:minors?|children|kids?|child|underage)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:" + _DRUG + r")\b(?:\s+\w+){0,5}\s+(?:" + _MAKE + r")\b",
        re.I,
    ),
    re.compile(
        r"\b(?:" + _MAKE + r")\b(?:\s+\w+){0,5}\s+(?:" + _DRUG + r")\b",
        re.I,
    ),
    re.compile(
        r"\bwhere\s+(?:to|can\s+i|do\s+i|should\s+i)\s+"
        r"(?:get|buy|obtain|find|download|order)\b(?:\s+\w+){0,3}\s+(?:"
        + _DRUG
        + r")\b",
        re.I,
    ),
    re.compile(
        r"\bhow[\s-]?to\s+(?:make|cook|synth\w*|manufactur\w*|produc(?:e|ing)|"
        r"obtain|get|buy)\b(?:\s+\w+){0,3}\s+(?:" + _DRUG + r")\b",
        re.I,
    ),
    re.compile(r"\bhow[\s-]?to\s+poison\b", re.I),
    re.compile(
        r"\bpoison(?:ing)?\b(?!\s+ivy\b)(?:\s+\w+){0,4}\s+"
        r"(?:someone|somebody|a\s+person|him|her|them|people)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:mak(?:e[sd]?|ing)|made|produc(?:e[sd]?|ing)|synth\w*|"
        r"cook(?:ing)?|manufactur\w*)\b(?:\s+\w+){0,4}\s+poisons?\b(?!\s+ivy\b)",
        re.I,
    ),
    re.compile(
        r"\b(?:" + _TOX + r")\b(?:\s+\w+){0,5}\s+(?:" + _MAKE + r")\b",
        re.I,
    ),
    re.compile(
        r"\b(?:synth\w*|cook(?:ing)?|mak(?:e[sd]?|ing)|made|manufactur\w*|"
        r"produc(?:e[sd]?|ing))\b(?:\s+\w+){0,5}\s+(?:" + _TOX + r")\b",
        re.I,
    ),
    re.compile(
        r"\b(?:malware|ransomware|keylogger|rootkit|credential theft)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:hack(?:ing)? into|how to hack|steal (?:a |the |their |his |her )?"
        r"(?:password|credentials)|phishing)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:kill|murder|assassinate|shoot|stab)\b.{0,32}"
        r"\b(?:someone|a person|him|her|them|people)\b",
        re.I,
    ),
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


@functools.lru_cache(maxsize=256)
def is_harmful(prompt: str) -> bool:
    """True for actionable harm, not a bare bomb, stalker, child, or address.

    Self-harm and malware names still match when they stand alone. This is the
    lexicon. Request refusal goes through `pair.moderate.moderate`.
    """
    text = _fold(prompt or "")
    return any(pattern.search(text) for pattern in _HARM)


def refusal_for(text: str) -> str:
    """One short refusal. Self-harm includes the 988 line. No how-to, no jargon."""
    if _SELF_HARM.search(text or ""):
        return CRISIS_REFUSAL
    return HARM_REFUSAL


@functools.lru_cache(maxsize=256)
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
    return is_harmless_shape(prompt)


def wants_grounded_retry(prompt: str) -> bool:
    """A list or real-world question can try search notes or Pro. A greeting does not."""
    return may_retry_refusal(prompt) and not is_casual_greeting(prompt)


@functools.lru_cache(maxsize=256)
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
    if (
        is_harmful(prompt)
        or is_structured_request(prompt)
        or not is_harmless_shape(prompt)
    ):
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
    cleaned = [
        _drop_meta(part) if index % 2 == 0 else part for index, part in enumerate(parts)
    ]
    return "\n".join(
        line for line in "".join(cleaned).splitlines() if line.strip()
    ).strip()


def stream_release(text: str) -> str:
    """`emit`, `hold`, or `refuse` for one growing reply.

    A buffer the moderation hook refuses is not written. A soft-refusal or
    infra-leak prefix is held until it resolves. Anything else can stream.
    """
    from pair.moderate import moderate

    if moderate(text or "").refused:
        return "refuse"
    if withhold_partial(text or ""):
        return "hold"
    return "emit"


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
    return any(
        folded.startswith(opener) or opener.startswith(folded) for opener in _OPENERS
    )


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

    `retry` and `ground` are each called at most once. A prompt or reply the
    moderation hook refuses becomes that verdict's replacement. Any other
    prompt that is not a harmless shape keeps a soft refusal as the model wrote it.
    """
    from pair.moderate import moderate

    raw = text or ""
    verdict = moderate(prompt)
    if verdict.refused:
        return verdict.replacement
    verdict = moderate(raw)
    if verdict.refused:
        return verdict.replacement
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
