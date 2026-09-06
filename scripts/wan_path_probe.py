#!/usr/bin/env python3
"""CLI wrapper for utils.wan_path — measure the path out, rung by rung.

    scripts/wan_path_probe.py            print the ladder + verdict
    scripts/wan_path_probe.py --verdict  also leave the cron_verdict line

See utils/wan_path.py for the design and the crontab idiom.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from utils.wan_path import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
