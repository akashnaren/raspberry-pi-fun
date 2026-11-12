"""23:00-07:00 Pacific downtime, including the midnight crossing."""

from __future__ import annotations

import datetime
import importlib.util
import json
import subprocess
import unittest
from zoneinfo import ZoneInfo

from tests.support.paths import ROOT

OPS = ROOT / "ops" / "downtime"
PT = ZoneInfo("America/Los_Angeles")
SCRIPT = OPS / "openpi-downtime.sh"

_BANNER_STATE = r"""
const fs = require("fs");
const html = fs.readFileSync(process.argv[1], "utf8");
const match = html.match(/<script id="openpi-downtime-logic">([\s\S]*?)<\/script>/);
if (!match) throw new Error("missing downtime logic");
eval(match[1]);
const now = Number(process.argv[2]);
const target = next7am(now);
const state = stateAt(now, target);
process.stdout.write(JSON.stringify({
  mode: state.mode,
  text: state.text,
  count: state.count,
  left: target - now,
}));
"""


def _hour_in_window(hour: int) -> bool:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; hour_in_window "$2"',
            "bash",
            str(SCRIPT),
            str(hour),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _banner_server():
    spec = importlib.util.spec_from_file_location(
        "openpi_banner_server", OPS / "banner_server.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("banner_server.py could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _at(year: int, month: int, day: int, hour: int, minute: int) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, tzinfo=PT)


def _banner_state(when: datetime.datetime) -> dict:
    result = subprocess.run(
        [
            "node",
            "-e",
            _BANNER_STATE,
            "--",
            str(OPS / "banner.html"),
            str(int(when.timestamp() * 1000)),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)


class DowntimeWindow(unittest.TestCase):
    def test_hours_cross_midnight(self):
        for hour in (23, 0, 1, 6):
            self.assertTrue(_hour_in_window(hour), hour)
        for hour in (7, 8, 12, 22):
            self.assertFalse(_hour_in_window(hour), hour)

    def test_units_start_at_23_and_end_at_07(self):
        start = (OPS / "openpi-downtime-start.timer").read_text(encoding="utf-8")
        end = (OPS / "openpi-downtime-end.timer").read_text(encoding="utf-8")
        apply = (OPS / "openpi-downtime-apply.service").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=*-*-* 23:00:00 America/Los_Angeles", start)
        self.assertNotIn("01:00", start)
        self.assertIn("OnCalendar=*-*-* 07:00:00 America/Los_Angeles", end)
        self.assertIn("openpi-downtime.sh auto", apply)
        self.assertIn("23:00-07:00", apply)

    def test_retry_after_crosses_midnight(self):
        banner = _banner_server()
        self.assertEqual(
            banner.retry_after(_at(2026, 1, 15, 23, 30)), 7 * 3600 + 30 * 60
        )
        self.assertEqual(
            banner.retry_after(_at(2026, 1, 16, 0, 30)), 6 * 3600 + 30 * 60
        )
        self.assertEqual(banner.retry_after(_at(2026, 1, 16, 6, 50)), 10 * 60)
        self.assertEqual(
            banner.retry_after(_at(2026, 7, 15, 23, 30)), 7 * 3600 + 30 * 60
        )

    def test_banner_counts_down_to_7am_and_says_up_soon_at_650(self):
        late = _banner_state(_at(2026, 1, 15, 23, 30))
        self.assertEqual(late["mode"], "down")
        self.assertIn("7:00 AM Pacific", late["text"])
        self.assertEqual(late["count"], "07:30:00")

        after_midnight = _banner_state(_at(2026, 1, 16, 0, 10))
        self.assertEqual(after_midnight["mode"], "down")
        self.assertEqual(after_midnight["count"], "06:50:00")

        still_counting = _banner_state(_at(2026, 1, 16, 6, 49))
        self.assertEqual(still_counting["mode"], "down")
        self.assertEqual(still_counting["count"], "00:11:00")

        soon = _banner_state(_at(2026, 1, 16, 6, 50))
        self.assertEqual(soon["mode"], "soon")
        self.assertEqual(soon["text"], "OpenPi will be up soon")
        self.assertEqual(soon["count"], "")

        summer = _banner_state(_at(2026, 7, 15, 23, 0))
        self.assertEqual(summer["mode"], "down")
        self.assertEqual(summer["count"], "08:00:00")
        self.assertIn("7:00 AM Pacific", summer["text"])
