#!/usr/bin/env python3
"""R→M (RNS to Meshtastic) gateway validation harness.

Sends a single LXMF message from a transient sender identity to the
configured gateway destination hash. The expected end-to-end flow is:

  this script  ──LXMF──>  rnsd  ──>  meshforge-gateway  ──MQTT──>
  mosquitto  ──>  meshtasticd  ──RF──>  meshforge channel listeners

Usage (on a Pi running rnsd + meshforge-gateway):

  # In one terminal: tail the gateway log
  journalctl -u meshforge-gateway -f | grep -E 'bridge|publish|RX|TX'

  # In another terminal: subscribe to the MQTT bridge topic
  mosquitto_sub -h 127.0.0.1 -t 'msh/US/2/json/meshforge/#' -v

  # Then run this script
  python3 scripts/test_r2m_validation.py f68c2f56cb61527b6c9ad603b9a5009a

Pass/fail criteria:
  - Script exits 0 if LXMF dispatch reports DELIVERED within 30s
  - Gateway log shows "Message bridged: rns -> meshtastic" or similar
  - mosquitto_sub shows a publish on msh/US/2/json/meshforge/<topic>
  - meshtasticd journal shows TX of the payload (LoRa airtime consumed)

WARNING: This emits real RF on the meshforge bridge channel via the
operator's HAM-licensed station. Confirm you are operating within your
license and channel allocation before running.

Per Issue #12: this script ALWAYS passes configdir= to RNS so it never
binds ports — it joins rnsd's shared instance instead.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

# A non-binding RNS client config — same shape MeshForge uses internally
# (see src/gateway/node_tracker.py::_init_rns_main_thread).
CLIENT_CONFIG = """\
[reticulum]
enable_transport = No
share_instance = Yes
shared_instance_port = 37428
instance_control_port = 37429

[logging]
loglevel = 4
"""


def _make_client_configdir() -> str:
    """Materialize a throwaway client-only RNS configdir."""
    d = tempfile.mkdtemp(prefix="meshforge_r2m_test_")
    (Path(d) / "config").write_text(CLIENT_CONFIG)
    return d


def _resolve_path(rns, dest_hash_bytes, timeout_s: float = 15.0) -> bool:
    """Block until RNS Transport has a path to the destination, or timeout."""
    if rns.Transport.has_path(dest_hash_bytes):
        return True
    rns.Transport.request_path(dest_hash_bytes)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if rns.Transport.has_path(dest_hash_bytes):
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dest_hash",
        help="Gateway LXMF destination hash (32 hex chars). Find via "
             "`journalctl -u meshforge-gateway | grep 'Gateway LXMF destination'`",
    )
    parser.add_argument(
        "--message",
        default=f"R2M validation {int(time.time())}",
        help="Body of the test LXMF (default: timestamped)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for delivery confirmation (default: 30)",
    )
    args = parser.parse_args()

    if len(args.dest_hash) != 32:
        print(f"ERROR: dest hash must be 32 hex chars, got {len(args.dest_hash)}",
              file=sys.stderr)
        return 2

    try:
        dest_hash_bytes = bytes.fromhex(args.dest_hash)
    except ValueError as e:
        print(f"ERROR: dest hash not valid hex: {e}", file=sys.stderr)
        return 2

    try:
        import RNS
        import LXMF
    except ImportError as e:
        print(f"ERROR: RNS/LXMF not importable: {e}", file=sys.stderr)
        print("Install: sudo pip3 install --break-system-packages rns lxmf",
              file=sys.stderr)
        return 3

    configdir = _make_client_configdir()
    print(f"[+] RNS client configdir: {configdir}")
    rns = RNS.Reticulum(configdir=configdir)

    print("[+] Resolving path to gateway destination...")
    if not _resolve_path(rns, dest_hash_bytes, timeout_s=15.0):
        print("ERROR: no path to gateway after 15s. Is rnsd up and is the "
              "gateway announcing? Check `rnstatus` and "
              "`rnpath {0}`.".format(args.dest_hash), file=sys.stderr)
        return 4

    print("[+] Path found. Setting up LXMF router...")

    storage = Path(configdir) / "lxmf"
    storage.mkdir(exist_ok=True)
    router = LXMF.LXMRouter(storagepath=str(storage))

    sender_identity = RNS.Identity()
    sender_dest = router.register_delivery_identity(
        sender_identity, display_name="r2m_test"
    )
    print(f"[+] Sender LXMF hash: {sender_dest.hash.hex()}")

    gateway_identity = RNS.Identity.recall(dest_hash_bytes)
    if not gateway_identity:
        print(f"ERROR: Identity.recall returned None for {args.dest_hash}",
              file=sys.stderr)
        return 5

    gateway_dest = RNS.Destination(
        gateway_identity,
        RNS.Destination.OUT,
        RNS.Destination.SINGLE,
        "lxmf",
        "delivery",
    )

    msg = LXMF.LXMessage(
        gateway_dest,
        sender_dest,
        args.message,
        title="r2m-test",
        desired_method=LXMF.LXMessage.DIRECT,
    )

    print(f"[+] Dispatching LXMF: {args.message!r}")
    router.handle_outbound(msg)

    deadline = time.monotonic() + args.timeout
    last_state = None
    while time.monotonic() < deadline:
        state = msg.state
        if state != last_state:
            state_name = {
                getattr(LXMF.LXMessage, n): n
                for n in dir(LXMF.LXMessage)
                if n.isupper() and isinstance(getattr(LXMF.LXMessage, n), int)
            }.get(state, str(state))
            print(f"[.] state={state_name}")
            last_state = state
        if state == LXMF.LXMessage.DELIVERED:
            print("[+] DELIVERED. Now check the gateway log + mosquitto.")
            return 0
        if state == LXMF.LXMessage.FAILED:
            print("ERROR: LXMessage state FAILED", file=sys.stderr)
            return 6
        time.sleep(0.5)

    print(f"ERROR: timed out after {args.timeout}s. Final state={msg.state}",
          file=sys.stderr)
    return 7


if __name__ == "__main__":
    sys.exit(main())
