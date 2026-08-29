#!/usr/bin/env python3
"""Live drill of the mesh oracle's RNS/LXMF leg — the sender side.

Sister of ``oracle_fire_drill.py``, which proves the PROBE's fire path against
log copies and never sends anything. This script exercises the ORACLE itself:
it sends real spaced queries to a gateway's LXMF delivery destination over the
box's shared rnsd, receives the directed replies, and reports. Every answered
(or send-failed) query is a *confirmable* record in the oracle audit log, so
this is also the sanctioned way to walk ``probe_oracle_delivery_degraded`` out
of its ``< sample_n confirmable ever`` cold-start indeterminate with genuine
end-to-end evidence (first used 2026-08-29 to clear moc's 15-day
``detector_blind_any``; the RNS leg had never fired live before it — all prior
answers were Meshtastic-transport).

Run ON the oracle box as the operator:

    PYTHONPATH=src python3 scripts/oracle_rns_drill.py [dest_hash_hex]
    PYTHONPATH=src python3 scripts/oracle_rns_drill.py --hash-only

With no destination argument the local gateway identity's own LXMF delivery
hash is used (querying this box's oracle through rnsd loopback).

Uses a dedicated drill identity at ``~/.config/meshforge/oracle_drill_identity``
(created on first run). Its source hash must be present in the gateway's
``MESHFORGE_ORACLE_RNS_ALLOWLIST`` (systemd drop-in) or every query is declined
``not_allowlisted`` — which is itself a valid leg-liveness check, but declines
never advance the confirmable count. ``--hash-only`` prints the hash and exits,
for allowlisting BEFORE the first real drill (the gateway reads the allowlist
at start, so adding it needs one gateway restart).

Spacing is 40s (> the oracle's 30s per-sender cooldown) so every query is
confirmable, never a cooldown decline. Exit 0 = at least one reply received.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import RNS  # noqa: E402
from utils.paths import get_real_user_home, ReticulumPaths  # noqa: E402

QUERIES = ["status", "wd", "whatsup", "help", "status"]
SPACING_S = 40.0   # > oracle.responder._DEFAULT_COOLDOWN_S (30s)
REPLY_WAIT_S = 30.0

home = get_real_user_home()
ident_path = home / ".config" / "meshforge" / "oracle_drill_identity"
if ident_path.exists():
    ident = RNS.Identity.from_file(str(ident_path))
    if ident is None:
        print(f"FATAL: could not load {ident_path}")
        sys.exit(2)
else:
    ident = RNS.Identity()
    ident_path.parent.mkdir(parents=True, exist_ok=True)
    ident.to_file(str(ident_path))

src_hash = RNS.Destination.hash(ident, "lxmf", "delivery")
print(f"drill source hash: {src_hash.hex()}")
if "--hash-only" in sys.argv:
    sys.exit(0)

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if args:
    dest_hash = bytes.fromhex(args[0])
else:
    gw_ident_path = home / ".config" / "meshforge" / "gateway_identity"
    gw_ident = RNS.Identity.from_file(str(gw_ident_path)) \
        if gw_ident_path.exists() else None
    if gw_ident is None:
        print(f"FATAL: no destination given and no gateway identity at "
              f"{gw_ident_path}")
        sys.exit(2)
    dest_hash = RNS.Destination.hash(gw_ident, "lxmf", "delivery")
print(f"oracle destination: {dest_hash.hex()}")

from utils.rns_init import open_reticulum  # noqa: E402
import LXMF  # noqa: E402

r = open_reticulum(ReticulumPaths.ensure_rns_client_configdir())
if r is None:
    print("FATAL: RNS unavailable/degraded (open_reticulum returned None)")
    sys.exit(2)

storage = home / ".local" / "share" / "meshforge" / "oracle_drill_lxmf"
storage.mkdir(parents=True, exist_ok=True)
router = LXMF.LXMRouter(storagepath=str(storage))

replies = []
reply_evt = threading.Event()


def on_delivery(msg):
    text = msg.content.decode("utf-8", "replace")
    replies.append(text)
    print(f"  <- reply {len(replies)}: {text!r}")
    reply_evt.set()


router.register_delivery_callback(on_delivery)
src = router.register_delivery_identity(ident, display_name="MF oracle drill")
router.announce(src.hash)
print(f"announced drill destination {src.hash.hex()}")

if not RNS.Transport.has_path(dest_hash):
    RNS.Transport.request_path(dest_hash)
    for _ in range(100):
        if RNS.Transport.has_path(dest_hash):
            break
        time.sleep(0.1)
if not RNS.Transport.has_path(dest_hash):
    print("FATAL: no path to gateway destination")
    sys.exit(2)

dest_ident = RNS.Identity.recall(dest_hash)
dest = RNS.Destination(dest_ident, RNS.Destination.OUT,
                       RNS.Destination.SINGLE, "lxmf", "delivery")

for i, q in enumerate(QUERIES):
    reply_evt.clear()
    lxm = LXMF.LXMessage(dest, src, q.encode("utf-8"),
                         desired_method=LXMF.LXMessage.DIRECT)
    t_sent = time.monotonic()
    router.handle_outbound(lxm)
    print(f"[{i + 1}/{len(QUERIES)}] sent {q!r}")
    reply_evt.wait(REPLY_WAIT_S)
    if i < len(QUERIES) - 1:
        remaining = SPACING_S - (time.monotonic() - t_sent)
        if remaining > 0:
            time.sleep(remaining)

# grace for the final reply
if len(replies) < len(QUERIES):
    time.sleep(10.0)

print(f"RESULT: sent={len(QUERIES)} replies={len(replies)}")
sys.exit(0 if replies else 1)
