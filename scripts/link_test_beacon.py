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
here is the RF interface's own TX byte counter — the RAW integer ``txb`` from
``Reticulum.get_interface_stats()`` over the handle this tool already holds,
read before and after. Not the rnstatus text: that renders counters through
``prettysize`` ("%.2f MB", a 10,000 B quantum above 1 MB), so a default run's
≤8 kB of announces on an interface past 1 MB computed delta 0 and printed
"transmitted NOTHING" for a radio that radiated — or printed a display step of
exactly 10,000 B as a measurement (review 2026-09-09, finding 5). A run whose
counter did not move reports FAILED, loudly, even though every announce
"succeeded".

What --count and --interval are NOT: the on-air cadence (finding 6)
-------------------------------------------------------------------
The 2026-09-08 field note recorded "every announce on an exact cadence, then a
hard cliff at 30/47/81 s, after which nothing for minutes". The chain that
produces that is in rnsd, not on the air (fork RNS 1.3.8+mf.0):

* this tool is a shared-instance CLIENT, so its announce reaches rnsd's
  Transport with ``hops = 1`` — and outbound announces with ``hops > 0`` are
  subject to the interface's ANNOUNCE_CAP (2 % of airtime);
* over the cap, announces go to a per-interface queue, ONE per ``wait_time``
  (announce airtime / 0.02: ~7 s at 10.94 kbps, ~24 s at SF8/125k) from a
  ``threading.Timer``;
* the queue is de-duplicated PER DESTINATION — a newer announce for the same
  destination REPLACES the queued one;
* ``RNodeInterface`` bumps ``txb`` only when the frame is actually written.

So with ``--interval 3`` most of announces 2..N are replaced in the queue, one
per ``wait_time`` radiates, and the last one may leave AFTER this tool and the
far-end capture have both exited. That is why the counter is polled until it
stops moving before the delta is read, why the delta is reported against
``sent × ~200 B`` as an ESTIMATED number of frames, and why "OK" means "the
radio radiated", never "it radiated what you asked for". Size a capture by
PACKETS RECEIVED at the far end, and expect roughly one frame per ``wait_time``
whatever cadence you choose. The docstring's earlier "27 consecutive packets
at 3 s" observation is inconsistent with this chain; settle it with rnsd at
LOG_EXTREME ("Added announce to queue") before quoting either.

Usage
-----
    # on the box that must TRANSMIT (its rnsd stays running)
    python3 scripts/link_test_beacon.py --count 40 --interval 3 \
        --rf-interface "<RNode interface name from rnstatus>"

    --rf-interface is matched against the interface name; omit it and the
    beacon witnesses the total across every RNodeInterface it finds.
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.paths import ReticulumPaths, get_real_user_home  # noqa: E402
from utils.rns_init import open_reticulum  # noqa: E402
from utils.safe_import import safe_import  # noqa: E402

RNS, _HAS_RNS = safe_import("RNS")

#: A single-destination announce on the air: 148-180 B payload + 35 B HEADER_2.
#: Used only to turn a byte delta into an ESTIMATED frame count for the report.
ANNOUNCE_BYTES_APPROX = 200

#: Counter settle: poll every SETTLE_POLL_S until it has not moved for
#: SETTLE_QUIET_POLLS consecutive polls (30 s — longer than the ~24 s
#: wait_time at SF8/125k, so a frame still queued cannot hide inside the
#: quiet window), giving up after SETTLE_MAX_S.
SETTLE_POLL_S = 5.0
SETTLE_QUIET_POLLS = 6
SETTLE_MAX_S = 120.0


def rf_tx_bytes(reticulum, match: str) -> Tuple[Optional[int], str]:
    """Total RAW TX bytes across the RNode interfaces, or None if unobservable.

    None means "could not look" (RPC failed, no RNode interface found, the
    counter is not an integer) and is deliberately distinct from 0, which
    means "looked, and nothing has been transmitted". Collapsing those two is
    the error class this repo names honest_failure_modes #1.
    """
    try:
        stats = reticulum.get_interface_stats()
    except Exception as e:  # noqa: BLE001 - unobservable, surfaced with the cause
        return None, f"get_interface_stats failed: {e}"
    ifaces = stats.get("interfaces") if isinstance(stats, dict) else None
    if not isinstance(ifaces, list):
        return None, "interface stats carried no interfaces list"
    hits = []
    for i in ifaces:
        if not isinstance(i, dict):
            continue
        kind = str(i.get("type") or i.get("name") or "")
        name = str(i.get("name") or "")
        if "rnode" in kind.lower() and (not match or match.lower() in name.lower()):
            hits.append(i)
    if not hits:
        return None, (f"no RNodeInterface matching {match!r} in interface stats"
                      if match else "no RNodeInterface in interface stats")
    total = 0
    for i in hits:
        txb = i.get("txb")
        if isinstance(txb, bool) or not isinstance(txb, int):
            return None, f"{i.get('name')} carried no integer txb ({txb!r})"
        total += txb
    return total, ", ".join(str(i.get("name")) for i in hits)


def wait_for_tx_to_settle(read_fn: Callable[[], Tuple[Optional[int], str]], *,
                          poll_s: float = SETTLE_POLL_S,
                          quiet_polls: int = SETTLE_QUIET_POLLS,
                          max_wait_s: float = SETTLE_MAX_S,
                          sleep: Callable[[float], None] = time.sleep,
                          log: Callable[[str], None] = print
                          ) -> Tuple[Optional[int], bool, float]:
    """Poll the TX counter until it stops moving. Returns (value, settled, waited_s).

    ``settled`` False means the counter was still moving at ``max_wait_s``
    (frames still queued — the delta is a LOWER bound) or became unreadable.
    """
    last, _ = read_fn()
    if last is None:
        return None, False, 0.0
    quiet = 0
    waited = 0.0
    while waited < max_wait_s:
        sleep(poll_s)
        waited += poll_s
        cur, _ = read_fn()
        if cur is None:
            return last, False, waited
        if cur == last:
            quiet += 1
        else:
            quiet = 0
            log(f"  ... TX counter moved to {cur:,} B ({waited:.0f}s after the last "
                "announce — a queued frame just left)")
        last = cur
        if quiet >= quiet_polls:
            return last, True, waited
    return last, False, waited


def _tighten(path: Path) -> None:
    """A private key must be 0600, whichever branch produced it."""
    try:
        if path.stat().st_mode & 0o077:
            os.chmod(path, 0o600)
    except OSError as e:
        print(f"WARNING: could not chmod 600 {path}: {e}")


def load_identity(rns=None):
    """Load or create the beacon identity, keeping the private key at 0600.

    RNS.Identity.to_file writes with the default umask (644). A private key
    readable by every local account is a real finding — 33 of them were found
    loose across the fleet on 2026-08-29 — so the chmod happens here, in the
    same breath as the write, not in a follow-up sweep. And on the load branch
    too: a key that was ALREADY loose is tightened, not inherited.

    ``RNS.Identity.from_file`` returns None (not an exception) for a zero-byte
    or corrupt file — the 2026-08-27 power-loss class (finding 13). Handing
    that None to ``RNS.Destination`` died with an AttributeError naming
    NoneType and never the corpse. The corpse is moved aside, named, and a
    fresh identity generated; the beacon's identity is not precious.
    """
    R = rns if rns is not None else RNS
    path = get_real_user_home() / ".config" / "meshforge" / "link_test_beacon_identity"
    path.parent.mkdir(parents=True, exist_ok=True)
    ident, created = None, False
    if path.exists():
        ident = R.Identity.from_file(str(path))
        if ident is None:
            corpse = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
            try:
                size = path.stat().st_size
            except OSError:
                size = -1
            path.rename(corpse)
            print(f"WARNING: {path} ({size} bytes) did not load as an identity — "
                  f"zero-byte/corrupt, the power-loss class. Moved to {corpse}; "
                  "generating a fresh one.")
    if ident is None:
        ident = R.Identity()
        ident.to_file(str(path))
        created = True
    _tighten(path)
    return ident, created


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Announce on an interval so a far-end capture has packets to measure.")
    ap.add_argument("--count", type=int, default=40, help="announces to hand to rnsd")
    ap.add_argument("--interval", type=float, default=3.0, help="seconds between announces")
    ap.add_argument("--rf-interface", default="",
                    help="substring of the interface name of the RF interface "
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

    def read_counter():
        return rf_tx_bytes(reticulum, args.rf_interface)

    before, witness = read_counter()
    if before is None:
        print(f"WARNING: cannot read RF TX counter ({witness}).")
        print("         The beacon will still announce, but this run will not be able")
        print("         to show that anything was radiated. Treat the far-end capture")
        print("         as the only evidence.")
    else:
        print(f"RF witness         : {witness}")
        print(f"TX bytes before    : {before:,}")

    print(f"handing rnsd {args.count} announces, one every {args.interval}s "
          f"(~{args.count * args.interval:.0f}s). Ctrl-C to stop early.")
    print("NOTE: rnsd rate-caps (2% airtime) and de-duplicates queued announces per")
    print("      destination — roughly one frame per announce-airtime/0.02 radiates,")
    print("      whatever the interval. The counter below is the truth.\n")
    sent = 0
    try:
        for n in range(1, args.count + 1):
            dest.announce()
            sent = n
            print(f"[{n:3d}/{args.count}] announce handed to rnsd", flush=True)
            if n < args.count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped by operator")

    print()
    if before is None:
        print(f"announces handed to rnsd: {sent}   RF transmission: UNKNOWN (counter unreadable)")
        print("UNKNOWN is not a pass — confirm at the far-end capture before quoting it.")
        return 1

    # Queued announces leave one per wait_time (up to ~24 s at SF8/125k) AFTER
    # the last announce() returns. Read the counter only once it has stopped
    # moving, so the delta is the run's, not a snapshot mid-drain.
    print(f"waiting for the TX counter to settle (quiet for "
          f"{SETTLE_POLL_S * SETTLE_QUIET_POLLS:.0f}s, max {SETTLE_MAX_S:.0f}s)...")
    after, settled, waited = wait_for_tx_to_settle(read_counter)
    if after is None:
        print(f"announces handed to rnsd: {sent}   RF transmission: UNKNOWN (counter "
              "became unreadable while settling)")
        return 1

    delta = after - before
    frames_est = delta / ANNOUNCE_BYTES_APPROX
    print()
    print(f"announces handed to rnsd : {sent}")
    print(f"TX bytes after           : {after:,}   (delta {delta:+,} B ≈ "
          f"{frames_est:.1f} announce-sized frames of ~{ANNOUNCE_BYTES_APPROX} B)")
    if not settled:
        print(f"NOTE: the counter was still moving after {waited:.0f}s — frames are still")
        print("      queued in rnsd; the delta above is a LOWER bound for this run.")
    if delta <= 0:
        print("\nFAILED: the RF interface transmitted NOTHING while this ran.")
        print("        Every announce() returned and not one reached the antenna —")
        print("        check that rnsd holds the RNode and the interface is enabled.")
        return 1
    print(f"\nOK: the radio transmitted {delta:,} B (≈{frames_est:.0f} frame(s)) "
          f"while beaconing.")
    if frames_est < sent:
        print(f"    Fewer frames than the {sent} announces handed to rnsd is EXPECTED:")
        print("    the announce cap and per-destination de-duplication collapse them.")
        print("    Judge the run by packets received at the far end, not by --count.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
