#!/usr/bin/env python3
"""Validate the train config against dataset_info.json and count registered rows."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.core.config import data_root
from pair.flywheel.lifecycle import _read_jsonl, train_config_path
from pair.flywheel.registry import require_registered
from pair.core.yaml_lite import load_path


def main() -> int:
    root = data_root()
    cfg = load_path(train_config_path())
    names = list(cfg.get("datasets") or [])
    eval_name = cfg.get("eval")
    resolved = require_registered(names + [eval_name], root)
    counts = {name: len(_read_jsonl(path)) for name, path in resolved.items()}
    pending = root / "train" / "pending" / "queue.jsonl"
    queued = len(_read_jsonl(pending)) if pending.is_file() else 0
    print(json.dumps({"registered": counts, "queued": queued}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
