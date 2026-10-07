#!/usr/bin/env python3
"""VM bench: table shape to a chart. A live 0.6b run is for the Pi, not this script."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.render.charts import chart_samples, render_chart


def main() -> int:
    samples = chart_samples()
    rendered = 0
    fallback = 0
    for sample in samples:
        result = render_chart(table=sample["table"])
        if result["ok"] and result["type"] == sample["expect"]:
            rendered += 1
        elif sample["table"] in (result.get("table") or ""):
            fallback += 1
    rate = rendered / len(samples) if samples else 0
    report = {
        "n": len(samples),
        "rendered": rendered,
        "fallback": fallback,
        "rate": round(rate, 3),
        "ok": rate >= 0.9 and rendered + fallback == len(samples),
        "model": "not run (0.6b is not on this VM)",
    }
    out = ROOT / "data" / "bench"
    out.mkdir(parents=True, exist_ok=True)
    (out / "charts.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Chart bench",
        "",
        f"- samples: {report['n']}",
        f"- rendered: {report['rendered']}",
        f"- fallback to the table text: {report['fallback']}",
        f"- rate: {report['rate']}",
        f"- model: {report['model']}",
        "",
    ]
    (out / "charts.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
