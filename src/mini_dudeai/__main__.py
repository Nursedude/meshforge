"""Enable `python3 -m mini_dudeai --preset meshforge_fleet ...`."""
import sys

from .daemon import main

if __name__ == "__main__":
    sys.exit(main())
