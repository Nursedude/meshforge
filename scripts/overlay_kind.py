#!/usr/bin/env python3
"""Print what a meshtasticd overlay IS — for shell callers (install_noc.sh).

    python3 scripts/overlay_kind.py FILE.yaml   ->  ch341 | spi | aux | ignored

The same classify_overlay() the TUI uses, so the installer's USB/SPI menus
cannot drift from it (one rule, not a bash re-implementation). Judged on
CONTENT, never the filename: `*-usb.yaml` once held `Serial:` files that
meshtasticd ignores (it has no `Serial:` key) while upstream `lora-usb-*`
held real CH341 configs (B7, 2026-09-30).

Exit codes: 0 kind printed; 2 unreadable / bad usage; 3 PyYAML missing
(`apt install python3-yaml`). On a non-zero exit the caller must treat the
file as NOT a radio config.
"""
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("overlay_kind: PyYAML missing — sudo apt install python3-yaml", file=sys.stderr)
        return 3
    from core.meshtasticd_templates import classify_overlay
    try:
        content = Path(argv[1]).read_text(errors="replace")
    except OSError as e:
        print(f"overlay_kind: cannot read {argv[1]}: {e}", file=sys.stderr)
        return 2
    print(classify_overlay(content))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
