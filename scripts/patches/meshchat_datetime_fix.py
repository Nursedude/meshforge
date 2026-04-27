#!/usr/bin/env python3
"""
MeshChat datetime.strptime() Fix

Patches reticulum-meshchat's meshchat.py to handle Peewee DateTimeField values
that are already datetime objects (not strings).

Bug: meshchat.py calls datetime.strptime() on values from Peewee DateTimeField
columns (last_read_at, created_at, updated_at). Peewee returns these as
datetime.datetime objects, causing:

    TypeError: strptime() argument 1 must be str, not datetime.datetime

Fix: Wrap strptime calls with isinstance checks so both str and datetime
inputs are handled correctly.

Usage:
    sudo python3 scripts/patches/meshchat_datetime_fix.py
    sudo python3 scripts/patches/meshchat_datetime_fix.py --path /custom/path/meshchat.py
    sudo python3 scripts/patches/meshchat_datetime_fix.py --dry-run

Upstream: https://github.com/liamcottle/reticulum-meshchat
"""

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# scripts/patches/ → repo root → src/utils/paths.py
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from utils.paths import get_real_user_home  # noqa: E402

DEFAULT_MESHCHAT_PATH = get_real_user_home() / "reticulum-meshchat" / "meshchat.py"

# Pattern: datetime.strptime(some_obj.some_field, "format_string")
# Captures: (indent)(varname) = datetime.strptime((obj.field), (format))
STRPTIME_ASSIGNMENT_RE = re.compile(
    r'^( +)(\w+) = datetime\.strptime\((\w+\.\w+), (".*?")\)$',
    re.MULTILINE,
)


def build_replacement(match: re.Match) -> str:
    """Build isinstance-guarded replacement for a strptime assignment."""
    indent = match.group(1)
    var = match.group(2)
    field_expr = match.group(3)
    fmt = match.group(4)
    return (
        f"{indent}{var} = {field_expr} if isinstance({field_expr}, datetime) "
        f"else datetime.strptime({field_expr}, {fmt})"
    )


def patch_file(path: Path, dry_run: bool = False) -> int:
    """Apply the strptime fix to meshchat.py.

    Returns the number of replacements made.
    """
    if not path.is_file():
        print(f"ERROR: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    original = path.read_text(encoding="utf-8")

    # Find all matches before replacing
    matches = list(STRPTIME_ASSIGNMENT_RE.finditer(original))
    if not matches:
        print("No strptime assignments found to patch.")
        return 0

    print(f"Found {len(matches)} strptime assignment(s) to patch:")
    for m in matches:
        line_num = original[: m.start()].count("\n") + 1
        print(f"  Line {line_num}: {m.group(0).strip()}")

    if dry_run:
        print("\n[DRY RUN] No changes written.")
        return len(matches)

    # Create backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(f".py.bak.{timestamp}")
    shutil.copy2(path, backup_path)
    print(f"\nBackup created: {backup_path}")

    # Apply replacements
    patched = STRPTIME_ASSIGNMENT_RE.sub(build_replacement, original)
    path.write_text(patched, encoding="utf-8")

    count = len(matches)
    print(f"Patched {count} strptime call(s) in {path}")
    print("\nNext steps:")
    print(f"  sudo systemctl restart reticulum-meshchat")
    print(f"  journalctl -u reticulum-meshchat -f --no-pager")
    return count


def main():
    parser = argparse.ArgumentParser(
        description="Patch meshchat.py strptime TypeError bug"
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_MESHCHAT_PATH,
        help=f"Path to meshchat.py (default: {DEFAULT_MESHCHAT_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without modifying the file",
    )
    args = parser.parse_args()

    print(f"MeshChat strptime fix — targeting: {args.path}")
    print()

    patch_file(args.path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
