#!/usr/bin/env python3
"""Operator-facing wrapper around utils.pcap_export.

Usage:
    python3 scripts/pcap_export.py --since '2026-04-27 14:00' \\
                                   --until '2026-04-27 14:30' \\
                                   --output ~/forensic.pcap
"""

import os
import sys
from pathlib import Path

# Make src/ importable when invoked from the repo root.
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from utils.pcap_export import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
