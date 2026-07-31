#!/usr/bin/env python3
"""Annotate ledger verdicts whose evidence came from the unguarded bare-rc path.

WHY (2026-07-31): calibration_reverify.sh ran the full suite, trusted
`pyrc=$?` alone, and minted held/broke verdicts from it. On this fleet that
exit code flaps — pytest computes TESTS_FAILED:1 while the interpreter exits 0
on ~50% of full-suite runs (measured 2026-07-28), and the flap only loses
failures TOWARD zero. So those verdicts rest on a signal that cannot
distinguish "suite green" from "suite red with the exit code lost". Fixed
forward in 1cc613d3; this annotates what was already recorded.

WHAT THIS DOES NOT CLAIM: the annotated verdicts are NOT shown to be wrong.
The suite is usually green, and when it is, rc=0 was correct. The defect is in
the strength of the evidence, not in the conclusion. Re-deriving them is a
separate act with a separate cost; this makes the weakness visible so the
headline ratio is never quoted without it.

WHY ANNOTATIONS AND NOT VERDICTS: verdicts belong to the re-derivation
machinery and are never hand-written (calibrated_claims rule 6). An annotation
can only reduce confidence in a recorded verdict, never manufacture it.

HOW THE SET IS DERIVED: the ledger cannot answer this directly — rederive_open
hardcoded detail="honest_status green on <sha>" for every marker, including
reverify's own, so provenance is not recorded in the verdict (fixed in the same
commit, going forward). It is therefore derived from LANDING TIME: the reverify
cron fires at 04:45 local and its verdicts land in a tight 04:47-04:49 cluster,
while warm-start verdicts land at arbitrary session times. That is a BOUND, not
an exact attribution, and the note says so.

Usage:
    python3 scripts/annotate_ledger_provenance.py           # dry-run
    python3 scripts/annotate_ledger_provenance.py --apply   # write (backs up)
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from mini_dudeai import calibration_ledger as cl  # noqa: E402

TOPIC = "evidence_provenance"

# The cron reverify window, local time. Generous around the measured
# 04:47-04:49 cluster (cron fires 04:45; the suite takes ~4 min).
WIN_START_H, WIN_END_H = 4.6, 6.0

# calibration_reverify.sh started classifying its run in this commit. Verdicts
# at or after its commit time came from the guarded path and are not annotated.
FIX_COMMIT = "1cc613d3"
FIX_TS = 1785525689.0

NOTE = (
    "Verdict minted by calibration_reverify.sh before {fix}, when that job "
    "trusted pytest's bare exit code. On this fleet that code flaps to 0 on a "
    "failed full-suite run (~50%, thread-join race at interpreter shutdown, "
    "measured 2026-07-28), and only ever toward 0 — so this verdict's evidence "
    "cannot distinguish a green suite from a red one whose exit code was lost. "
    "The verdict is NOT shown to be wrong and still stands; its evidence is "
    "weaker than recorded. Provenance derived from landing time (the 04:45 "
    "cron window), not from the ledger, because the verdict detail hardcoded "
    "honest_status as its source regardless of marker — also fixed in {fix}."
).format(fix=FIX_COMMIT)


def in_cron_window(ts: float) -> bool:
    lt = time.localtime(ts)
    hour = lt.tm_hour + lt.tm_min / 60.0
    return WIN_START_H <= hour <= WIN_END_H


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="append the annotations (default: dry-run)")
    ap.add_argument("--path", default=None, help="ledger path override")
    args = ap.parse_args()

    path = args.path or cl.ledger_path()
    events = cl.load_events(path)
    if not events:
        print(f"ledger {path}: no events — nothing to annotate")
        return 0

    claims = {e["id"]: e for e in events if e.get("kind") == "claim" and e.get("id")}
    verdicts = [e for e in events if e.get("kind") == "verdict"]
    already = {
        e.get("claim_id") for e in events
        if e.get("kind") == "annotation" and e.get("topic") == TOPIC
    }

    targets = []
    for v in verdicts:
        ts = v.get("ts")
        cid = v.get("claim_id")
        if not isinstance(ts, (int, float)) or not isinstance(cid, str):
            continue
        if ts >= FIX_TS:
            continue                      # guarded path
        if not in_cron_window(ts):
            continue                      # warm-start / manual
        if cid in already:
            continue                      # idempotent
        targets.append(v)

    print(f"ledger: {path}")
    print(f"  {len(claims)} claims · {len(verdicts)} verdicts · "
          f"{len(already)} already annotated for '{TOPIC}'")
    print(f"  {len(targets)} verdict(s) to annotate\n")
    for v in targets:
        c = claims.get(v["claim_id"], {})
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(v["ts"]))
        print(f"  {when}  {v.get('outcome'):5s}  claim={v['claim_id']}  "
              f"head={str(c.get('head_full', '?'))[:10]}")

    if not targets:
        print("\nnothing to do.")
        return 0
    if not args.apply:
        print("\nDry-run — re-run with --apply to write.")
        return 0

    # The ledger IS the track record; back it up before appending to it.
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = f"{path}.bak-{stamp}"
    shutil.copy2(path, backup)
    print(f"\nbackup: {backup}")

    for v in targets:
        cl.record_annotation(
            v["claim_id"], TOPIC, NOTE, path=path,
            extra={"verdict_ts": v["ts"], "verdict_outcome": v.get("outcome"),
                   "source": "manual", "derived_by": os.path.basename(__file__)})

    # Re-read from disk and re-derive rather than trusting the writes returned
    # cleanly — the ledger's own discipline applied to a change of the ledger.
    after = cl.load_events(path)
    state = cl.fold(after)
    n_annot_events = sum(1 for e in after
                         if e.get("kind") == "annotation" and e.get("topic") == TOPIC)
    print(f"applied — {n_annot_events} '{TOPIC}' annotation(s) now in the ledger")
    print(f"re-derived: held={state['n_held']} broke={state['n_broke']} "
          f"open={state['n_open']} annotated={state['n_annotated']} "
          f"ratio={'n/a' if state['ratio'] is None else round(state['ratio'], 3)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
