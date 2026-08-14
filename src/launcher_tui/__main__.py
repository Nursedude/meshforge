"""``python -m launcher_tui`` — routes through the REAL entry point.

Q5 (audit W18): this used to call a shadow ``main()`` defined in
__init__.py, skipping main.py's entire ``main()`` — argparse, logging
setup, stderr redirect, crash hook, and the terminal-restore cleanup.
The package __init__ has already injected the flat import paths.
"""

from main import main

if __name__ == "__main__":
    main()
