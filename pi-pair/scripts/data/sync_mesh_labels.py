#!/usr/bin/env python3
"""Upload hashed votes from pi3. Refuses every other role. Prints no raw chat."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pair.flywheel.publish import sync_from_disk
from pair.flywheel.miss_queue import node_role


def main() -> int:
    if node_role() != "dataset":
        print(
            "public label sync runs only on pi3. pi4 generates. pi2 searches.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(sync_from_disk()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
