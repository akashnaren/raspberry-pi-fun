#!/usr/bin/env python3
"""Delete active shards and prepared artifacts. The pending queue is not touched."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.lifecycle import sweep_ephemeral


def main() -> int:
    removed = sweep_ephemeral()
    print(json.dumps({"deleted": removed}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
