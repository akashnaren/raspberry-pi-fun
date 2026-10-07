#!/usr/bin/env python3
"""VM bench for the chat-ability checks. A live 200-item model run is for the Pi."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.turn.abilities import CATEGORIES, category_rates, eval_rows  # noqa: E402
from pair.turn.shape import PERSONA, estimate_tokens  # noqa: E402


def main() -> int:
    rates = category_rates()
    rows = eval_rows()
    report = {
        "n": len(rows),
        "persona_tokens": estimate_tokens(PERSONA),
        "rates": {name: round(rates[name], 3) for name in CATEGORIES},
        "ok": len(rows) == 200 and all(rates[name] >= 0.9 for name in CATEGORIES),
        "model": "not run (0.6b is not on this VM)",
    }
    out = ROOT / "data" / "bench"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "abilities.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (out / "abilities.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Ability bench",
        "",
        f"- items: {report['n']}",
        f"- persona tokens: {report['persona_tokens']}",
        f"- model: {report['model']}",
        "",
    ]
    for name in CATEGORIES:
        lines.append(f"- {name}: {report['rates'][name]}")
    lines.append("")
    (out / "abilities.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
