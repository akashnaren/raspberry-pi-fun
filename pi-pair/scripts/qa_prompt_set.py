#!/usr/bin/env python3
"""Short prompt set for the QA fixes.

The default path uses no model and no network. ``--live`` asks a local
Ollama qwen3:0.6b, with thinking off, and checks the cleaned replies.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.abilities import clean_reply, parse_fences, settle_blocks  # noqa: E402
from pair.charts import render_chart  # noqa: E402
from pair.docs import render_document  # noqa: E402
from pair.turn import PERSONA, needs_web, persona_text  # noqa: E402

PROMPTS = (
    "My dog Biscuit likes the park.",
    "What is the boiling point of water?",
    "What is the latest news?",
    "Make a chart of apples 3 and pears 5.",
    "Write this up as a pdf.",
)

STOCK = ("you do not know", "clarifying question", "calc plot doc")
_EXAMPLE_ROW = re.compile(r"\|\s*a\s*\|\s*1\s*\|", re.I)
PLAIN = (
    "What is the capital of Australia?",
    "Who wrote Pride and Prejudice and when was it published?",
    "When did the Berlin Wall fall?",
    "What is the largest planet in the solar system?",
    "What is the boiling point of water at sea level?",
    "Who painted the Mona Lisa?",
    "¿Cuál es la capital de Francia?",
    "What is your name and who made you?",
    "Hi!",
    "Tell me a short joke.",
    "Rewrite this more formally: hey can u send me the file asap",
    "Translate 'good morning, my friend' into Spanish.",
    "What is 847 * 23?",
    "What is 1234 + 5678 - 999?",
    "What is 15% of 2340?",
    "What is the latest news about NASA today?",
    "Who won the most recent Super Bowl?",
    "What is the weather in Tokyo today?",
    "What is the current price of bitcoin?",
    "List five novels by Stephen King.",
    "Explain why the sky is blue in a short paragraph.",
    "Explain the difference between TCP and UDP.",
    "How far is the Moon from Earth?",
    "Who wrote Hamlet?",
    "What does HTTP stand for?",
    "Name a primary color.",
    "How many days are in a week?",
    "Say hello in French.",
    "What is 2+2?",
    "My dog Biscuit likes the park.",
    "What is the boiling point of water?",
    "What is your favorite season?",
)
CHART = "Make a bar chart of monthly sales: Jan 12, Feb 18, Mar 9, Apr 15."
XLSX = (
    "Make an XLSX spreadsheet I can download with columns Month and Sales: "
    "Jan 12, Feb 18, Mar 9."
)
PDF = (
    "Make a PDF file I can download: a one-page note titled Seattle Weekend "
    "with three things to do."
)
DOCX = (
    "Make a DOCX file I can download: a packing list for a beach trip with five items."
)
MD = "Make a Markdown .md file I can download with a short recipe for pancakes."


def _rendered(body: str) -> tuple[bytes, str]:
    kind = "txt"
    lines = []
    for line in body.splitlines():
        if not lines and line.lower().startswith("kind:"):
            kind = line.split(":", 1)[1].strip().lower()
            continue
        lines.append(line)
    return render_document("\n".join(lines).strip(), kind)


def _echo(text: str) -> bool:
    cleaned = text or ""
    if _EXAMPLE_ROW.search(cleaned):
        return True
    return "title: Note" in cleaned and "A short paragraph." in cleaned


def _ask(prompt: str) -> str:
    payload = {
        "model": "qwen3:0.6b",
        "stream": False,
        "think": False,
        "messages": [
            {"role": "system", "content": PERSONA},
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": 0.2, "num_predict": 256},
    }
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        body = json.loads(response.read().decode())
    message = body.get("message") or {}
    return str(message.get("content") or "")


def _fence_body(text: str, name: str) -> str:
    for fence in parse_fences(text):
        if fence["name"] != name:
            continue
        lines = []
        for line in fence["body"].splitlines():
            if not lines and line.lower().startswith(("kind:", "title:", "type:")):
                continue
            lines.append(line)
        return "\n".join(lines).strip()
    return ""


def live() -> bool:
    """Cleaned qwen3:0.6b replies: no example rows, and the repro files match."""
    if len(PLAIN) < 30:
        print(json.dumps({"live": False, "reason": "fewer than 30 plain prompts"}))
        return False
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3).read()
    except (OSError, urllib.error.URLError) as error:
        print(json.dumps({"live": False, "reason": f"ollama unavailable: {error}"}))
        return False
    echoes = []
    for prompt in PLAIN:
        try:
            raw = _ask(prompt)
        except (
            OSError,
            urllib.error.URLError,
            json.JSONDecodeError,
            TimeoutError,
        ) as error:
            print(json.dumps({"live": False, "reason": str(error), "prompt": prompt}))
            return False
        cleaned = clean_reply(raw, prompt)
        if _echo(cleaned):
            echoes.append(prompt)
    chart = settle_blocks(_ask(CHART), CHART)
    sheet = settle_blocks(_ask(XLSX), XLSX)
    pdf = settle_blocks(_ask(PDF), PDF)
    docx = settle_blocks(_ask(DOCX), DOCX)
    markdown = settle_blocks(_ask(MD), MD)
    chart_body = _fence_body(chart, "plot")
    drawn = (
        render_chart(table=chart_body) if chart_body else {"ok": False, "figure": None}
    )
    series = []
    figure = drawn.get("figure") or {}
    data = figure.get("data") or []
    if data:
        series = list(data[0].get("y") or [])
    sheet_body = _fence_body(sheet, "doc")
    report = {
        "live": True,
        "plain_prompts": len(PLAIN),
        "example_echoes": echoes,
        "chart_y": series,
        "xlsx_has_months": "Month" in sheet
        and "Jan" in sheet
        and "| a | 1 |" not in sheet,
        "pdf_kind": "kind: pdf" in pdf and "kind: docx" not in pdf,
        "docx_kept": "kind: docx" in docx and "| a | 1 |" not in docx,
        "md_kind": "kind: md" in markdown and "kind: docx" not in markdown,
        "sheet_preview": sheet_body[:180],
    }
    print(json.dumps(report, indent=2))
    return (
        not echoes
        and series == [12, 18, 9, 15]
        and report["xlsx_has_months"]
        and report["pdf_kind"]
        and report["md_kind"]
    )


def main() -> int:
    persona = persona_text()
    lowered = persona.lower()
    echoed = next(
        line
        for line in PERSONA.splitlines()
        if line and not line.startswith("```") and not line.startswith("|")
    )
    question = "What is the capital of Australia?"
    dirty = "\n".join(
        [
            "Canberra.",
            echoed,
            question,
            "```calc",
            "```",
            "```not a language",
            "junk",
            "```",
            "See [n] and [53].",
        ]
    )
    cleaned = clean_reply(dirty, question, source_count=0)
    pdf_fence = parse_fences("```pdf\ntitle: Note\nQuarter notes for the file.\n```")
    xlsx_fence = parse_fences(
        "```xlsx\n| item | n |\n| --- | --- |\n| apples | 3 |\n```"
    )
    md_fence = parse_fences("```md\n# Note\nA short paragraph.\n```")
    chart = render_chart(
        table="| item | n |\n| --- | --- |\n| apples | 3 |\n| pears | 5 |\n"
    )
    pdf_bytes, pdf_ext = _rendered(pdf_fence[0]["body"])
    xlsx_bytes, xlsx_ext = _rendered(xlsx_fence[0]["body"])
    md_bytes, md_ext = _rendered(md_fence[0]["body"])
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / f"note.{pdf_ext}").write_bytes(pdf_bytes)
        (folder / f"note.{xlsx_ext}").write_bytes(xlsx_bytes)
        (folder / f"note.{md_ext}").write_bytes(md_bytes)
        written = sorted(path.name for path in folder.iterdir())
    same_persona = all(persona_text() == persona for _prompt in PROMPTS)
    report = {
        "persona_identical": same_persona,
        "persona_tokens_ok": 40 <= len(persona) / 3.2 <= 220,
        "stock_absent": not any(phrase in lowered for phrase in STOCK),
        "no_example_fence": "```" not in persona,
        "personal_skips_search": not needs_web(PROMPTS[0])
        and not needs_web(PROMPTS[1]),
        "fresh_searches": needs_web(PROMPTS[2]),
        "no_persona_echo": echoed not in cleaned and question not in cleaned,
        "empty_fence_gone": "```" not in cleaned and "junk" not in cleaned,
        "stray_markers_gone": "[n]" not in cleaned and "[53]" not in cleaned,
        "answer_kept": "Canberra." in cleaned,
        "chart_ok": bool(chart.get("ok")),
        "pdf_ok": pdf_bytes.startswith(b"%PDF") and pdf_ext == "pdf",
        "xlsx_ok": xlsx_bytes.startswith(b"PK") and xlsx_ext == "xlsx",
        "md_ok": b"A short paragraph." in md_bytes and md_ext == "md",
        "files": written,
    }
    print(json.dumps(report, indent=2))
    ok = all(value is True for key, value in report.items() if key != "files")
    if "--live" not in sys.argv:
        return 0 if ok else 1
    return 0 if ok and live() else 1


if __name__ == "__main__":
    raise SystemExit(main())
