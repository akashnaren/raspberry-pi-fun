"""Numbered asks get enough tokens and a continuation when the list stops early.

A real-world Top-N prefers titles that already appear in the search notes.
One continuation still fills a short list. Items the notes contradict are dropped.
"""

from __future__ import annotations

import re

_LIST = re.compile(
    r"\b(?:top|list|name|give|rank|number)\b(?:\s+\w+){0,4}\s+(\d{1,2})\b|"
    r"\b(\d{1,2})\s+(?:movies|films|books|songs|items|things|reasons|ways|"
    r"examples|ideas|tips|points|steps|cars|places|products|shows)\b",
    re.I,
)
_LINE = re.compile(r"(?m)^\s*(\d{1,2})[\.\)]\s+(\S.*)$")
_CATEGORY = re.compile(
    r"\b(?:cars?|automobiles?|movies?|films?|books?|songs?|phones?|cities|"
    r"foods?|fruits?|games?|shows?|restaurants?|albums?)\b",
    re.I,
)
_COUNT_LEAD = re.compile(
    r"^(?:please\s+)?(?:top|best|list|name|give|rank|number)\s+"
    r"(?:these\s+|me\s+|the\s+)?\d{1,2}\s+",
    re.I,
)

_CAP = 1024
_PER_ITEM = 40
_HEADROOM = 48


def list_count(prompt: str) -> int | None:
    """The N in 'top 10 movies', between 2 and 20. Other lines are None."""
    match = _LIST.search(prompt or "")
    if not match:
        return None
    raw = next(group for group in match.groups() if group)
    count = int(raw)
    if count < 2 or count > 20:
        return None
    return count


_RECOMMEND = re.compile(
    r"\b(?:best|top)\b|\bto\s+(?:watch|read|try)\b",
    re.I,
)


def is_recommendation(prompt: str) -> bool:
    """'best X', 'top N', and 'X to watch/read/try' are recommendation asks."""
    return bool(_RECOMMEND.search(prompt or ""))


def is_real_world_list(prompt: str) -> bool:
    """Counted lists of cars, films, and similar categories. Primes are not."""
    from pair.assist import is_harmful

    text = prompt or ""
    if is_harmful(text) or not list_count(text):
        return False
    return bool(_CATEGORY.search(text))


def is_grounded_list(prompt: str) -> bool:
    """Real-world Top-N and recommendation asks that should search for names."""
    from pair.assist import is_harmful

    text = prompt or ""
    if is_harmful(text):
        return False
    if is_real_world_list(text):
        return True
    return bool(is_recommendation(text) and _CATEGORY.search(text))


def answer_count(prompt: str) -> int | None:
    """N for a counted list. An uncounted recommendation asks for five names."""
    count = list_count(prompt)
    if count:
        return count
    if is_grounded_list(prompt):
        return 5
    return None


def category_query(prompt: str) -> str:
    """Ranking query. 'top 5 movies' becomes 'best movies of all time list'."""
    text = " ".join((prompt or "").split()).strip(" ?.!")
    trimmed = _COUNT_LEAD.sub("", text, count=1).strip(" ?.!")
    subject = re.sub(r"^(?:best|top)\s+", "", trimmed, count=1, flags=re.I).strip()
    subject = " ".join(subject.split()) or text
    return f"best {subject} of all time list"


def list_budget(prompt: str, base: int) -> int:
    """Raise num_predict so N short items fit. Unlisted prompts keep `base`."""
    count = list_count(prompt)
    if not count:
        return int(base)
    need = _PER_ITEM * count + _HEADROOM
    return max(int(base), min(need, _CAP))


def _placeholder(num: int, body: str) -> bool:
    """'1. 1' is not an item. A prime such as '2. 3' still is."""
    token = re.sub(r"[^\w]+", "", (body or "").strip(), flags=re.UNICODE)
    if not token:
        return True
    return bool(re.fullmatch(r"\d{1,2}", token) and token == str(num))


def _matches(text: str) -> list[tuple[int, str, bool]]:
    found: list[tuple[int, str, bool]] = []
    for match in _LINE.finditer(text or ""):
        num = int(match.group(1))
        body = match.group(2).strip()
        found.append((num, match.group(0).strip(), _placeholder(num, body)))
    return found


def numbered_lines(text: str) -> list[tuple[int, str]]:
    """Real numbered lines. Placeholder rows such as '1. 1' are ignored."""
    return [(num, line) for num, line, placeholder in _matches(text) if not placeholder]


def placeholder_only(text: str) -> bool:
    """True when every numbered line is a placeholder and at least one exists."""
    rows = _matches(text)
    return bool(rows) and all(placeholder for _num, _line, placeholder in rows)


def list_complete(text: str, count: int) -> bool:
    nums = [num for num, _line in numbered_lines(text)]
    return count in nums and len(nums) >= count


def _bounded(lines: list[tuple[int, str]], count: int) -> str:
    kept: list[str] = []
    seen: set[int] = set()
    for num, line in lines:
        if num < 1 or num > count or num in seen:
            continue
        seen.add(num)
        kept.append(line)
    return "\n".join(kept)


def merge_list(base: str, more: str, count: int) -> str:
    """Append real lines past the last number, and never past N."""
    have = numbered_lines(base)
    last = have[-1][0] if have else 0
    extra = [line for num, line in numbered_lines(more) if last < num <= count]
    if not extra:
        return base
    block = "\n".join(extra)
    if not (base or "").strip():
        return block
    return base.rstrip() + "\n" + block


def _may_continue(prompt: str) -> bool:
    """Only a clearly harmless list is continued. Harmful subjects stay put."""
    from pair.assist import is_harmful, may_retry_refusal

    return bool(
        may_retry_refusal(prompt) and not is_harmful(prompt) and list_count(prompt)
    )


def needs_exact_n(prompt: str, text: str) -> bool:
    """True when a harmless counted list does not yet have N real items.

    Canned hits and Flash replies both use this. Nothing is invented here.
    """
    count = list_count(prompt)
    if not count or not _may_continue(prompt):
        return False
    return not list_complete(text or "", count)


def continuation_messages(messages: list, partial: str, count: int) -> list:
    """A refusal or a placeholder list is a fresh ask, not a replay."""
    if not numbered_lines(partial):
        note = (
            f"Reply with exactly {count} items, one per line, numbered from 1 to {count}. "
            "Use a real name or fact on each line, not the item number. "
            "Answer helpfully if the request is safe. "
            f"Stop at item {count}."
        )
        return [*list(messages or []), {"role": "user", "content": note}]
    note = (
        f"Continue the numbered list until item {count}. "
        f"The list must contain exactly {count} items. "
        "Start at the next missing number. Do not repeat earlier items. "
        f"Stop at item {count}."
    )
    return [
        *list(messages or []),
        {"role": "assistant", "content": partial},
        {"role": "user", "content": note},
    ]


_TITLE_LINE = re.compile(r"(?m)^\s*\d{1,3}[\.\)]\s+(\S.*)$")
_BULLET = re.compile(r"(?m)^\s*[-*]\s+(\S.*)$")
_QUOTED = re.compile(r"[\"“]([^\"”\n]{2,80})[\"”]")
_YEAR_TITLE = re.compile(
    r"\b([A-Z][A-Za-z0-9'’:.-]*(?:\s+[A-Z][A-Za-z0-9'’:.-]*){0,6})\s+\((?:19|20)\d{2}\)"
)
_SKIP_TITLE = re.compile(
    r"\b(?:best|top|list|ranking|ranked|review|reviews|guide|why|how|what)\b",
    re.I,
)
_GENERIC_TITLE = frozenset(
    {
        "movie",
        "movies",
        "film",
        "films",
        "horror",
        "book",
        "books",
        "song",
        "songs",
        "show",
        "shows",
        "car",
        "cars",
    }
)


_JUNK_TITLE = re.compile(
    r"\||\b(?:showtimes?|tickets?|fandango|imdb|rotten tomatoes|wikipedia|"
    r"reddit|youtube|netflix|cinemark|google)\b|"
    r"\bnear\s+\w+|\bwatch\b.+\bonline\b|"
    r"^movies\s*&\s*tv$|\b(?:amc|regal|xd)\b",
    re.I,
)
_MARK = re.compile(
    r"\*\*(.+?)\*\*|\*(.+?)\*|__(.+?)__|(?<!\w)_(.+?)_(?!\w)",
)


def _strip_marks(text: str) -> str:
    """Drop qwen3 italic and bold wrappers. *The Shining* is The Shining."""
    body = (text or "").strip()
    while True:
        match = _MARK.search(body)
        if not match:
            break
        inner = next(group for group in match.groups() if group)
        body = body[: match.start()] + inner + body[match.end() :]
    return " ".join(body.strip("*_ ").split())


def _junk_title(text: str) -> bool:
    """Site, page, and nav titles are not entity names."""
    if not text or _JUNK_TITLE.search(text):
        return True
    return text.casefold() in _GENERIC_TITLE


def _clean_title(raw: str) -> str:
    text = _strip_marks(raw or "")
    text = re.split(r"\s+[—–]\s+|\s+-\s+|:\s+|\s+\(", text, maxsplit=1)[0]
    text = " ".join(text.strip(" .*\"'_").split())
    if not text or len(text) < 2 or len(text) > 80:
        return ""
    if len(text.split()) > 8:
        return ""
    lowered = text.casefold()
    if "http" in lowered or "www." in lowered or _junk_title(text):
        return ""
    if _SKIP_TITLE.search(text) and len(text.split()) > 3:
        return ""
    return text


def _candidates(blob: str) -> list[str]:
    """Entity-shaped strings in one source. Page chrome is not a candidate."""
    found: list[str] = []
    text = blob or ""
    for match in _TITLE_LINE.finditer(text):
        found.append(match.group(1))
    for match in _QUOTED.finditer(text):
        found.append(match.group(1))
    for match in _YEAR_TITLE.finditer(text):
        found.append(match.group(1))
    for match in _MARK.finditer(text):
        inner = next(group for group in match.groups() if group)
        found.append(inner)
    return found


def _source_blobs(context: str) -> list[str]:
    """Snippet text per result, plus the fetched page. Result titles are dropped."""
    blobs: list[str] = []
    page = ""
    body = context or ""
    marker = "Text from the first page:"
    if marker in body:
        body, page = body.split(marker, 1)
    for match in _BULLET.finditer(body):
        line = match.group(1).strip()
        _head, sep, snippet = line.partition("): ")
        if sep:
            blobs.append(snippet)
            continue
        if " (http" in line:
            continue
        blobs.append(line)
    if page.strip():
        blobs.append(page)
    if not blobs and body.strip():
        blobs.append(body)
    return blobs


def ranked_entities(context: str) -> list[str]:
    """Clean names from snippets. Names seen in more sources come first."""
    counts: dict[str, int] = {}
    order: dict[str, int] = {}
    display: dict[str, str] = {}
    seq = 0
    for blob in _source_blobs(context or ""):
        seen_here: set[str] = set()
        for raw in _candidates(blob):
            title = _clean_title(raw)
            if not title:
                continue
            key = title.casefold()
            if key in seen_here:
                continue
            seen_here.add(key)
            counts[key] = counts.get(key, 0) + 1
            if key not in order:
                order[key] = seq
                seq += 1
                display[key] = title
    keys = sorted(counts, key=lambda key: (-counts[key], order[key]))
    return [display[key] for key in keys]


def source_titles(context: str) -> list[str]:
    """Clean entity names from search notes, frequent names first."""
    return ranked_entities(context)


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))


def _item_name(line: str) -> str:
    body = re.sub(r"^\s*\d{1,3}[\.\)]\s*", "", line or "").strip()
    body = re.split(r"\s+[—–]\s+|\s+-\s+|:\s+", body, maxsplit=1)[0]
    body = _strip_marks(body)
    return body.strip(" .\"'*_")


def clip_repeat(text: str) -> tuple[str, bool]:
    """Cut a stream at the first repeated line or list item."""
    raw = text or ""
    lines = raw.splitlines(keepends=True)
    seen: set[str] = set()
    kept: list[str] = []
    for index, line in enumerate(lines):
        finished = line.endswith(("\n", "\r")) or index < len(lines) - 1
        key = (_item_name(line) or "").casefold()
        if key and key in seen:
            return "".join(kept), True
        if key and finished:
            seen.add(key)
        kept.append(line)
    return raw, False


def dedupe_lines(text: str) -> str:
    """One row per title. Markdown wrappers and later copies are dropped.

    A prose sentence stays as written. Only a numbered list is renamed.
    """
    rows = [line for line in (text or "").splitlines() if line.strip()]
    if not rows:
        return text or ""
    numbered = all(re.match(r"\s*\d{1,3}[\.\)]\s+\S", line) for line in rows)
    seen: set[str] = set()
    names: list[str] = []
    for line in rows:
        shown = _item_name(line) if numbered else line.strip()
        key = (_item_name(line) or line.strip()).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        names.append(shown)
    if not names:
        return text or ""
    if numbered:
        return _numbered(names)
    return "\n".join(names)


def distinct_items(text: str) -> int:
    cleaned = dedupe_lines(text or "")
    return len([line for line in cleaned.splitlines() if line.strip()])


def ground_category_list(prompt: str, text: str, context: str) -> str:
    """N clean entity names when the notes have them.

    Names that show up in more sources come first. Site and nav titles are
    not names. Fewer than N clean names leaves the model's own list, deduped,
    instead of padding with page titles.
    """
    cleaned = dedupe_lines(text or "")
    count = answer_count(prompt)
    if not count or not is_grounded_list(prompt):
        return cleaned
    titles = ranked_entities(context or "")
    if len(titles) >= count:
        return _numbered(titles[:count])
    return cleaned


def finish_numbered(prompt: str, text: str, more) -> str:
    """Call `more(partial, n)` once when a harmless list stops before N.

    The retry asks for exactly N. Nothing is invented here. Zero real items
    get one fresh ask, and that refusal or placeholder is not replayed as the
    list. A harmful subject is not continued.
    """
    count = list_count(prompt)
    current = text or ""
    if not count or not _may_continue(prompt) or list_complete(current, count):
        return current
    nxt = more(current, count) or ""
    if not numbered_lines(current):
        fresh = _bounded(numbered_lines(nxt), count)
        return fresh or current
    return merge_list(current, nxt, count)
