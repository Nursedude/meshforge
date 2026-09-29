#!/usr/bin/env python3
"""Activate a meshtasticd HAT/USB template into config.d/ — SANITIZED.

    sudo python3 scripts/sanitize_overlay.py SRC.yaml /etc/meshtasticd/config.d/

A config.d/ overlay OVERRIDES config.yaml, so a template that carries
``Webserver:``/``TCP:``/``Logging:``/``MQTT:``/``Bluetooth:``/``General:``
moves the daemon's ports and logging out from under the box (#58: moc3's
``Webserver: Port: 443`` rode in on a raw ``cp`` of an upstream template).
Every Python activation path already goes through ``sanitize_hat_overlay``;
this is the same function for shell callers — ``install_noc.sh`` and the
operator at a terminal — so there is ONE rule, not a bash re-implementation.

Exit codes: 0 written (stdout says what was stripped, or ``clean``);
2 SRC unreadable / DST unwritable; 3 PyYAML missing (``apt install
python3-yaml``). A non-zero exit writes NOTHING — the caller must not fall
back to a raw copy.
"""
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


def main(argv) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    src = Path(argv[1])
    dst = Path(argv[2])
    try:
        import yaml  # noqa: F401  — the sanitizer needs it; say so plainly
    except ImportError:
        print("sanitize_overlay: PyYAML missing — sudo apt install python3-yaml "
              "(nothing written)", file=sys.stderr)
        return 3
    from core.meshtasticd_templates import sanitize_hat_overlay

    if dst.is_dir() or str(argv[2]).endswith("/"):
        dst = dst / src.name
    try:
        content = src.read_text()
    except OSError as e:
        print(f"sanitize_overlay: cannot read {src}: {e}", file=sys.stderr)
        return 2
    sanitized, stripped = sanitize_hat_overlay(content)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(sanitized)
    except OSError as e:
        print(f"sanitize_overlay: cannot write {dst}: {e}", file=sys.stderr)
        return 2
    if stripped:
        print(f"{dst}: stripped {', '.join(stripped)} — those belong in "
              f"/etc/meshtasticd/config.yaml, not an overlay")
    else:
        print(f"{dst}: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
