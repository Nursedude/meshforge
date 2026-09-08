#!/usr/bin/env python3
"""link_test_beacon.py — make the FAR end radiate, on demand, for a link test.

The deliberate complement to ``link_test_capture.py``. That tool captures and
refuses to transmit, for a good reason stated in its own docstring: keying a PA
from a script can destroy an amplifier if the antenna is disconnected. So it
leaves generating test traffic to "an RNS link, or a radio already beaconing" —
and on 2026-09-08 that turned out to be the missing half. Ambient RNS traffic on
this path is far too sparse to measure with: ``id_interval = 600`` emits a
callsign ID six times an HOUR, and ``--summarize`` needs 20 packets before it
will quote a median. Waiting for ambient traffic means a capture that ends in
"only 3 packet(s); below 20 the median is noise".

How it stays on the safe side of that line
------------------------------------------
This does **not** touch the radio. It hands announces to the already-running
rnsd, which owns the RNode and transmits at the txpower in the RNS config. The
PA is keyed by the same code path that keys it all day; nothing here can key it
in a way normal operation would not. If rnsd is not running, this exits rather
than constructing its own RNS — a pure consumer that becomes the ``@rns`` host
is the 2026-05-28 fleet outage (see ``utils.rns_init``).

Why announces, and not a probe or an LXMF message
-------------------------------------------------
These two boxes are ALSO linked over TCP/LAN, and RNS routes addressed traffic
over the fastest path — which is the LAN, not the LoRa. An ``rnprobe`` between
them can complete perfectly while the radio never transmits a byte, and the
capture at the far end would sit empty with nothing to explain it. An announce
is a broadcast: it goes out EVERY interface, including the RNode, regardless of
routing. That is the property this needs.

The witness — because ``announce()`` returning is not evidence of a transmission
-------------------------------------------------------------------------------
Calling ``announce()`` proves a function returned. It does not prove a packet
left the antenna: the RNode could be down, the interface disabled, the shared
instance connected to a different rnsd than the one holding the radio. Verifying
the WIRING and calling it a transmission is the proxy-verification failure this
project keeps paying for (calibrated_claims rule 7). So the consumer of record
here is the RF interface's own TX byte counter, read from ``rnstatus`` before
and after. A run whose counter did not move reports FAILED, loudly, even though
every announce "succeeded".

Usage
-----
    # on the box that must TRANSMIT (its rnsd stays running)
    python3 scripts/link_test_beacon.py --count 40 --interval 3 \
        --rf-interface "<RNode interface name from rnstatus>"

    --rf-interface is matched against the rnstatus display name; omit it and
    the beacon witnesses the total across every RNodeInterface it finds.
"""

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.paths import ReticulumPaths, get_real_user_home  # noqa: E402
from utils.rns_init import open_reticulum  # noqa: E402
from utils.rns_status_parser import run_rnstatus  # noqa: E402
from utils.safe_import import safe_import  # noqa: E402

RNS, _HAS_RNS = safe_import("RNS")

_UNIT_SCALE = {"B": 1.0, "KB": 1e3, "KIB": 1024.0, "MB": 1e6, "MIB": 1024.0 ** 2,
               "GB": 1e9, "GIB": 1024.0 ** 3}


def _to_bytes(total: float, unit: str) -> float:
    """Normalise an rnstatus traffic figure to bytes.

    An unknown unit returns 0.0 rather than guessing a scale: a wrong scale
    would silently turn a real transmission into a bogus delta, and this
    number's whole job is to be believable.
    """
    return total * _UNIT_SCALE.get((unit or "B").strip().upper(), 0.0)


def rf_tx_bytes(match: str):
    """Total TX bytes across the RNode interfaces, or None if unobservable.

    None means "could not look" (rnsd unreachable, rnstatus wedged, no RNode
    interface found) and is deliberately distinct from 0.0, which means "looked,
    and nothing has been transmitted". Collapsing those two is the error class
    this repo names honest_failure_modes #1.
    """
    status = run_rnstatus(timeout_s=15.0)
    if status.parse_error or status.timed_out:
        return None, (status.parse_error or "rnstatus timed out")
    hits = [i for i in status.interfaces
            if "rnode" in i.type_name.lower()
            and (not match or match.lower() in i.display_name.lower())]
    if not hits:
        return None, (f"no RNodeInterface matching {match!r} in rnstatus"
                      if match else "no RNodeInterface in rnstatus")
    total = sum(_to_bytes(i.tx.bytes_total, i.tx.bytes_unit) for i in hits)
    return total, ", ".join(i.full_name for i in hits)


def load_identity():
    """Load or create the beacon identity, keeping the private key at 0600.

    RNS.Identity.to_file writes with the default umask (644). A private key
    readable by every local account is a real finding — 33 of them were found
    loose across the fleet on 2026-08-29 — so the chmod happens here, in the
    same breath as the write, not in a follow-up sweep.
    """
    path = get_real_user_home() / ".config" / "meshforge" / "link_test_beacon_identity"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return RNS.Identity.from_file(str(path)), False
    ident = RNS.Identity()
    ident.to_file(str(path))
    os.chmod(path, 0o600)
    return ident, True


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Announce on an interval so a far-end capture has packets to measure.")
    ap.add_argument("--count", type=int, default=40, help="announces to send")
    ap.add_argument("--interval", type=float, default=3.0, help="seconds between announces")
    ap.add_argument("--rf-interface", default="",
                    help="substring of the rnstatus display name of the RF interface "
                         "to witness (default: every RNodeInterface)")
    args = ap.parse_args()

    if not _HAS_RNS:
        print("FAIL: RNS is not importable in this interpreter.")
        return 1

    reticulum = open_reticulum(ReticulumPaths.ensure_rns_client_configdir(),
                               require_listener=True)
    if reticulum is None:
        print("FAIL: no shared RNS instance to announce through.")
        print("      Refusing to construct one — a consumer that becomes the @rns")
        print("      host takes the box's real rnsd offline. Start rnsd and retry.")
        return 1

    ident, created = load_identity()
    dest = RNS.Destination(ident, RNS.Destination.IN, RNS.Destination.SINGLE,
                           "meshforge", "linktest")
    print(f"beacon destination : {dest.hash.hex()}"
          + ("  (identity created)" if created else ""))

    before, witness = rf_tx_bytes(args.rf_interface)
    if before is None:
        print(f"WARNING: cannot read RF TX counter ({witness}).")
        print("         The beacon will still announce, but this run will not be able")
        print("         to show that anything was radiated. Treat the far-end capture")
        print("         as the only evidence.")
    else:
        print(f"RF witness         : {witness}")
        print(f"TX bytes before    : {before:,.0f}")

    print(f"announcing {args.count}x every {args.interval}s "
          f"(~{args.count * args.interval:.0f}s). Ctrl-C to stop early.\n")
    sent = 0
    try:
        for n in range(1, args.count + 1):
            dest.announce()
            sent = n
            print(f"[{n:3d}/{args.count}] announce sent", flush=True)
            if n < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped by operator")

    # Let the last announce clear the interface queue before reading the counter.
    time.sleep(2.0)
    after, _ = rf_tx_bytes(args.rf_interface)
    print()
    if before is None or after is None:
        print(f"announces sent: {sent}   RF transmission: UNKNOWN (counter unreadable)")
        print("UNKNOWN is not a pass — confirm at the far-end capture before quoting it.")
        return 1
    delta = after - before
    print(f"announces sent : {sent}")
    print(f"TX bytes after : {after:,.0f}   (delta {delta:+,.0f} B)")
    if delta <= 0:
        print("\nFAILED: the RF interface transmitted NOTHING while this ran.")
        print("        Every announce() returned and not one reached the antenna —")
        print("        check that rnsd holds the RNode and the interface is enabled.")
        return 1
    print(f"\nOK: the radio transmitted {delta:,.0f} B while beaconing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
