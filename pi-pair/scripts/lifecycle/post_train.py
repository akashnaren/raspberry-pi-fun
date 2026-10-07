#!/usr/bin/env python3
"""Prepare the miss queue, fold the canned map, gate, promote, delete shards."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.flywheel.lifecycle import post_train


def main() -> int:
    try:
        print(json.dumps(post_train()))
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
