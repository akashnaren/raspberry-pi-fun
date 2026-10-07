#!/usr/bin/env python3
"""VM bench for the multi-user decode slot. Heat on the Pi is for Akash."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.model.sched import (  # noqa: E402
    admission_report,
    background_cancel_report,
    client_report,
    estimate_report,
    fairness_report,
    handoff_report,
    heat_report,
    tool_job_report,
)
from pair.mesh.tools import generation_routes  # noqa: E402


def main() -> int:
    fairness = fairness_report()
    estimate = estimate_report()
    admission = admission_report()
    clients = client_report()
    heat = heat_report()
    handoff = handoff_report()
    background = background_cancel_report()
    tools = tool_job_report()
    routes = generation_routes()
    report = {
        "fairness": fairness,
        "estimate": estimate,
        "admission": admission,
        "clients": clients,
        "heat": heat,
        "handoff": handoff,
        "background": background,
        "tool": tools,
        "generation_routes": routes,
        "ok": all(
            row["ok"]
            for row in (
                fairness,
                estimate,
                admission,
                clients,
                heat,
                handoff,
                background,
                tools,
            )
        )
        and routes == [],
    }
    out = ROOT / "data" / "bench"
    out.mkdir(parents=True, exist_ok=True)
    (out / "sched.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Scheduler bench",
        "",
        f"- clients: {fairness['clients']}",
        f"- max gap: {fairness['max_gap']}",
        f"- estimate error: {estimate['error']}",
        f"- two-ahead error: {estimate['ahead_error']}",
        f"- queued before 503: {admission['queued']}",
        f"- long wait retry-after s: {admission['long_retry_after_s']}",
        f"- hot delay s: {heat['hot_delay_s']}",
        f"- sensor delay s: {heat['sensor_delay_s']}",
        f"- handoff ran: {handoff['ran']}",
        f"- background cancel s: {background['elapsed_s']}",
        f"- tool node: {tools['node']}",
        f"- generation routes: {len(routes)}",
        f"- ok: {report['ok']}",
        "",
    ]
    (out / "sched.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
