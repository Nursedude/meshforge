#!/usr/bin/env python3
"""
Entry point for running the monitoring module directly.

Usage:
    python3 -m src.monitoring [options]

This is equivalent to running:
    python3 -m src.monitor [options]
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor import main

if __name__ == "__main__":
    main()
