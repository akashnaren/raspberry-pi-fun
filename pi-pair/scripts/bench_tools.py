"""VM stand-in for mesh search latency. No Pi and no network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.tools import (
    SEARCH_MEDIAN_TARGET_S,
    SEARCH_P95_TARGET_S,
    latency_stats,
    simulated_search_latencies,
)


def main() -> None:
    stats = latency_stats(simulated_search_latencies())
    stats["median_target_s"] = SEARCH_MEDIAN_TARGET_S
    stats["p95_target_s"] = SEARCH_P95_TARGET_S
    stats["ok"] = (
        stats["median_s"] < SEARCH_MEDIAN_TARGET_S
        and stats["p95_s"] < SEARCH_P95_TARGET_S
    )
    out = ROOT / "data" / "bench"
    out.mkdir(parents=True, exist_ok=True)
    (out / "tools.json").write_text(
        json.dumps(stats, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "| metric | value |",
        "| --- | --- |",
        f"| n | {stats['n']} |",
        f"| median s | {stats['median_s']} |",
        f"| p95 s | {stats['p95_s']} |",
        f"| ok | {stats['ok']} |",
        "",
    ]
    (out / "tools.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    if not stats["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
