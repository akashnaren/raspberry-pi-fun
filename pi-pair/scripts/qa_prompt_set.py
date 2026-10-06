#!/usr/bin/env python3
"""Short prompt set for the QA fixes. No model and no network.

Checks the persona cannot be quoted back, personal chats do not search,
a chart table draws, and pdf, xlsx, and md fences become files.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.abilities import clean_reply, parse_fences  # noqa: E402
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


def _rendered(body: str) -> tuple[bytes, str]:
    kind = "txt"
    lines = []
    for line in body.splitlines():
        if not lines and line.lower().startswith("kind:"):
            kind = line.split(":", 1)[1].strip().lower()
            continue
        lines.append(line)
    return render_document("\n".join(lines).strip(), kind)


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
        "doc_example": "```doc" in persona,
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
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
