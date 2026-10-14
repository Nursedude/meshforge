#!/usr/bin/env python3
"""Operator login for the map's web writes (BIC-3).

Run ON the box, as the operator (the user the map runs as — not sudo):

    python3 scripts/meshforge_web.py pair        # print a one-time code (5 min)
    python3 scripts/meshforge_web.py revoke-all  # log out every browser
    python3 scripts/meshforge_web.py status      # sessions, last use, pending code

Then open http://<box>:5000/login in the browser and type the code. Viewing
the map never needs this; sending and changing settings do.

Exit codes: 0 done · 1 refused or failed · 2 the store could not be read.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import web_auth  # noqa: E402


def _ago(ts) -> str:
    if ts is None:
        return "never"
    s = max(0, int(time.time() - ts))
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return f"{s}s ago"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["pair", "revoke-all", "status"])
    args = ap.parse_args(argv)

    if os.geteuid() == 0:
        print("Refused: run this as the operator (the user meshforge-map runs as), "
              "not root — a root-owned session file is one the map cannot read.\n"
              "  e.g.  sudo -u <operator> python3 scripts/meshforge_web.py "
              f"{args.action}", file=sys.stderr)
        return 1

    store = web_auth.SessionStore()
    try:
        if args.action == "pair":
            code = store.start_pairing()
            print(f"Pairing code:  {code[:4]}-{code[4:]}")
            print(f"Valid for {web_auth.PAIRING_TTL_S // 60} minutes, once; "
                  f"{web_auth.PAIRING_MAX_TRIES} wrong tries burn it.")
            print("Open http://<this-box>:5000/login on the box's LAN and type it.")
        elif args.action == "revoke-all":
            n = store.revoke_all()
            print(f"Revoked {n} operator session(s). Every browser must pair again.")
        else:
            st = store.status()
            print(f"store:            {store.path}")
            print(f"live sessions:    {st['sessions']}")
            print(f"last used:        {_ago(st['last_used'])}")
            print(f"code pending:     {'yes' if st['pairing_pending'] else 'no'}")
    except web_auth.StoreUnavailable as e:
        print(f"UNKNOWN: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
