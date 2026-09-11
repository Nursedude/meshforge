#!/usr/bin/env python3
"""fleet_posture_stamp.py — stamp the manager's posture document as a MIRROR.

    fleet_posture_stamp.py <source> <dest> <manager-hostname>

Called by fleet_posture_sync.sh before the fan-out. Two jobs, and the first
matters more than the second:

1. **REFUSE to distribute a document that does not validate.** The manager is
   the SSOT, but "SSOT" is a statement about authority, not about correctness.
   Fanning a malformed declaration to nine boxes turns one broken file into
   nine, on exactly the boxes that are about to lose their operator. The
   reader treats an invalid file as "watch everything", so a refusal here
   costs nothing and an un-refused bad copy costs the whole fleet.

2. Add ``mirror: {from}``. The manager's own file never carries it, so the
   stamp is what lets a reader tell a COPY from the ORIGINAL — and hold the
   copy to the stricter rule (utils.fleet_posture.read_posture: a mirror whose
   reader cannot confirm its own clock silences nothing).

   ⚠️ THE STAMP MUST BE DETERMINISTIC. The first cut also wrote ``at: <now>``,
   which made every run produce a different document — so the sync's md5
   comparison found "drift" on every invocation forever. Drilled against moc4
   2026-09-10: three consecutive no-change runs each reported `healed`, each
   left a `.bak`, and `--check` returned 3 (drift) with nothing changed. Three
   harms in one: the drift signal carried NO information (a churning box and a
   stable one read identically — the exact failure fleet_registry_sync.sh's
   header warns about, arrived at from the other side), `.bak` files grew
   without bound, and `fleet_power.py down` would re-scp the whole fleet at
   the worst possible moment. The field was also never READ: a writer with no
   reader (hfm #4), which is what made it pure cost. The document's own
   ``declared_at`` already says WHICH declaration this is; a copy's age, if it
   is ever wanted, belongs to a reader that asks for it.

Writes ``dest`` byte-stably (sorted keys, indent 2) so the md5 the sync
compares is a property of the CONTENT, not of dict ordering.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from utils import fleet_posture as fp  # noqa: E402


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 3:
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    src, dest, manager = argv
    try:
        doc = fp.load_doc(src)
    except ValueError as exc:
        print(f"REFUSING to distribute: source is not usable JSON: {exc}",
              file=sys.stderr)
        return 2
    # skip_past: an EXPIRED window is a valid document whose effect has ended,
    # not a refusal — same rule read_posture uses, for the same reason.
    errs = fp.validate(doc, skip_past=True)
    if errs:
        print("REFUSING to distribute an invalid declaration:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 2
    doc["mirror"] = {"from": manager}
    tmp = f"{dest}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, dest)
    n = len(doc.get("boxes") or {})
    print(f"stamped mirror from {manager} ({n} box(es) declared)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
