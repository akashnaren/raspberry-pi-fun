"""VM stand-in for compaction recall. No model."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair import memory
from pair.compact import plan, run_compact


def main() -> None:
    tmp = tempfile.TemporaryDirectory()
    os.environ["PI_PAIR_DATA"] = tmp.name
    needle = "the locker code is 4182"
    turns = [{"role": "user", "content": needle}]
    for index in range(39):
        turns.append(
            {"role": "user", "content": f"turn {index} talks about the weather"}
        )
    baseline = 1 if any("4182" in fact for fact in plan(turns, 512)["facts"]) else 0
    for _ in range(2):
        result = run_compact(turns, 512, idle=lambda: True)
        memory.remember_user(result["facts"])
        memory.save_summary(result["summary"], int(result.get("elapsed_ms") or 0))
        turns = result["keep"]
    recalled = 1 if any("4182" in row["text"] for row in memory.list_facts()) else 0
    rate = (recalled / baseline) if baseline else 0
    stats = {
        "baseline": baseline,
        "recalled": recalled,
        "rate": rate,
        "ok": rate >= 0.9,
    }
    out = ROOT / "data" / "bench"
    out.mkdir(parents=True, exist_ok=True)
    (out / "memory.json").write_text(
        json.dumps(stats, indent=2) + "\n", encoding="utf-8"
    )
    text = (
        "| metric | value |\n| --- | --- |\n"
        f"| needle recall | {rate} |\n| ok | {stats['ok']} |\n"
    )
    (out / "memory.md").write_text(text, encoding="utf-8")
    print(text)
    tmp.cleanup()
    if not stats["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
