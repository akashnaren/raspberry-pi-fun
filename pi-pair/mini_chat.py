#!/usr/bin/env python3
"""Pi 0.2 High — multi-user chat, Ollama + llama.cpp. Port 18080. Stdlib only.

Start: python3 mini_chat.py
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pair.server import main

if __name__ == "__main__":
    main()
