"""Alias for daemon.main — supports both `python3 -m mini_dudeai.daemon` and
`python3 -m mini_dudeai.cli`.
"""
from .daemon import main

if __name__ == "__main__":
    import sys
    sys.exit(main())
