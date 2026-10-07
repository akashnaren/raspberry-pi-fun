#!/usr/bin/env python3
"""Held-out inputs must stay out of the live canned map."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.core.config import data_root
from pair.core.yaml_lite import load_path
from pair.flywheel.canned import load_map
from pair.flywheel.lifecycle import _gate, _heldout_keys, train_config_path
from pair.flywheel.registry import require_registered


def main() -> int:
    root = data_root()
    cfg = load_path(train_config_path())
    names = list(cfg.get("datasets") or [])
    eval_name = cfg.get("eval")
    resolved = require_registered(names + [eval_name], root)
    table = load_map(root / "canned" / "canned_map.json")
    heldout = _heldout_keys(resolved[eval_name])
    _gate(table, heldout, table)
    print(f"gate ok rows={len(table)} heldout={len(heldout)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
