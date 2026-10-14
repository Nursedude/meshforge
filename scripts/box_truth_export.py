#!/usr/bin/env python3
"""box_truth_export — this box's box_truth/v1 document, for a granted center.

Invoked as the FORCED COMMAND of a NOC read grant (never by hand in normal
operation):

    restrict,command="/usr/bin/python3 -I /opt/meshforge/scripts/box_truth_export.py
      --grant SHA256:<fp> </dev/null" ssh-ed25519 AAAA... noc:<center>

and by the legacy spool path over an interactive key (no --grant).

What it will NOT do, by design (security pass B1): read stdin, or treat
SSH_ORIGINAL_COMMAND as anything but data recorded in the read witness —
whatever the client asked to run, it gets this document and nothing else.
Run under ``python3 -I`` (B2): PYTHON* variables and the user site are
ignored, so the src path is derived from this file, never from the env.

Spec: .claude/plans/noc_read_grant_2026_10_13.md
"""
import argparse
import os
import sys
from pathlib import Path

_SRC = Path(os.path.realpath(__file__)).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.box_truth_collect import Sources, run_export  # noqa: E402
from utils.paths import get_real_user_home  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--grant", default=None,
                    help="the authenticating grant's fingerprint (set by the "
                         "authorized_keys line, never by the client)")
    args, _unknown = ap.parse_known_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    sys.stdout.write(run_export(args.grant, Sources(home=get_real_user_home())))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
