"""Rest hysteresis on the cached thermal zone, and the kill switch."""

from __future__ import annotations

import json
import os
import time
import unittest
from unittest.mock import patch

from pair.core import thermal
from pair.model.knobs import inference_knobs
from pair.model.rest import defer_nonurgent, enabled
from tests.support.paths import ROOT


def _cache(temp: float | None) -> None:
    with thermal._LOCK:
        thermal._SAMPLE["temp_c"] = temp
        thermal._SAMPLE["read_at"] = time.monotonic()


def _reset_latch() -> None:
    with thermal._LOCK:
        thermal._HOT = False
        thermal._SAMPLE["temp_c"] = None
        thermal._SAMPLE["read_at"] = 0.0


class ThermalRest(unittest.TestCase):
    def setUp(self) -> None:
        self._rest = os.environ.get("PI_PAIR_REST")
        self._role = os.environ.get("PI_PAIR_ROLE")
        os.environ.pop("PI_PAIR_REST", None)
        os.environ["PI_PAIR_ROLE"] = "brain"
        _reset_latch()

    def tearDown(self) -> None:
        _reset_latch()
        if self._rest is None:
            os.environ.pop("PI_PAIR_REST", None)
        else:
            os.environ["PI_PAIR_REST"] = self._rest
        if self._role is None:
            os.environ.pop("PI_PAIR_ROLE", None)
        else:
            os.environ["PI_PAIR_ROLE"] = self._role

    def test_latch_holds_between_hot_and_cool(self) -> None:
        for temp, want in ((74, False), (76, True), (70, True), (67, False)):
            _cache(temp)
            self.assertIs(thermal.hot(), want, temp)
        _cache(75)
        self.assertTrue(thermal.hot())
        _cache(68)
        self.assertFalse(thermal.hot())

    def test_missing_reading_is_not_hot(self) -> None:
        _cache(None)
        self.assertFalse(thermal.hot())
        _cache(76)
        self.assertTrue(thermal.hot())
        _cache(None)
        self.assertFalse(thermal.hot())

    def test_missing_reading_clears_the_latch(self) -> None:
        _cache(80)
        self.assertTrue(thermal.hot())
        _cache(None)
        self.assertFalse(thermal.hot())
        _cache(72)
        self.assertFalse(thermal.hot())

    def test_kill_switch_via_config_and_env(self) -> None:
        shipped = json.loads(
            (ROOT / "configs" / "runtime" / "inference_pi4.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(shipped["rest"], {"enabled": False, "hot_c": 75, "cool_c": 68})
        self.assertFalse(enabled())
        base = inference_knobs()
        on = dict(base)
        on["rest"] = {"enabled": True, "hot_c": 75, "cool_c": 68}
        off = dict(base)
        off["rest"] = {"enabled": False, "hot_c": 75, "cool_c": 68}
        malformed = dict(base)
        malformed["rest"] = {"enabled": True, "hot_c": "hot", "cool_c": 68}
        missing = dict(base)
        missing.pop("rest", None)
        inverted = dict(base)
        inverted["rest"] = {"enabled": True, "hot_c": 60, "cool_c": 70}
        _cache(80)
        with patch("pair.model.rest.inference_knobs", return_value=on):
            self.assertTrue(enabled())
            self.assertTrue(defer_nonurgent())
            for flag in ("0", "false", "off", "no", "FALSE", "Off", "NO", " false "):
                os.environ["PI_PAIR_REST"] = flag
                self.assertFalse(enabled(), flag)
                self.assertFalse(defer_nonurgent(), flag)
            os.environ["PI_PAIR_REST"] = "1"
            self.assertTrue(enabled())
            os.environ.pop("PI_PAIR_REST", None)
        for row in (off, malformed, missing, inverted):
            with patch("pair.model.rest.inference_knobs", return_value=row):
                self.assertFalse(enabled(), row.get("rest"))
                self.assertFalse(defer_nonurgent(), row.get("rest"))
        self.assertTrue(thermal.hot())


if __name__ == "__main__":
    unittest.main()
