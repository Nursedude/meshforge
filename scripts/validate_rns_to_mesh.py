#!/usr/bin/env python3
"""Send an LXMF message to a gateway's LXMF destination for RNS→Mesh validation.

Purpose: close the Issue #40 acceptance leg that otherwise requires an operator
at the NomadNet TUI. Runs from any fleet box as ``wh6gxz``, connects to the
local rnsd as a shared-instance client (using Issue #41's pinned rpc_key if
present), sends an LXMF text message to the target destination hash, and waits
for delivery confirmation. The gateway then bridges to Meshtastic — verify by
tailing ``journalctl -u meshforge-gateway`` on the gateway host for a matching
``Message bridged`` line.

Usage:
    # Send to fleet-host-3's gateway LXMF source hash, default text
    python3 scripts/validate_rns_to_mesh.py --to 0123456789abcdef0123456789abcdef

    # Custom text + longer path-request window + delivery wait
    python3 scripts/validate_rns_to_mesh.py \\
        --to 0123456789abcdef0123456789abcdef \\
        --text "@!ffffffff ping from validator" \\
        --path-timeout 10 --delivery-timeout 20

Exits 0 on successful handoff to rnsd (or delivery confirmation if waited for),
nonzero on missing path, init failure, or timeout.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR.parent / "src"))


def _read_rnsd_instance_name() -> str:
    """Return the ``instance_name`` from the active RNS config (or 'default').

    Thin wrapper kept for the script's local call-site; the shared helper
    is ``ReticulumPaths.get_configured_instance_name()`` and that is the
    one every other MeshForge client-config writer should use.
    """
    from utils.paths import ReticulumPaths

    return ReticulumPaths.get_configured_instance_name()


def _build_client_config(tmpdir: Path) -> Path:
    """Write a share-instance client config that matches rnsd's rpc_key
    and instance_name.

    Mirrors the pattern in ``gateway.node_tracker`` — no interfaces, just
    the fields that bind the multiprocessing RPC authkey and the shared
    socket namespace to rnsd's.
    """
    from utils.paths import ReticulumPaths

    instance_name = _read_rnsd_instance_name()

    cfg_lines = [
        "# MeshForge RNS validator client config (auto-generated)",
        "[reticulum]",
        "share_instance = Yes",
        f"instance_name = {instance_name}",
        "shared_instance_port = 37428",
        "instance_control_port = 37429",
    ]
    rpc_key = ReticulumPaths.get_shared_rpc_key()
    if rpc_key:
        cfg_lines.append(f"rpc_key = {rpc_key}")
    else:
        print(
            "warn: no rpc_key pinned in the active RNS config — this will "
            "only work if the validator and rnsd share a transport_identity",
            file=sys.stderr,
        )

    cfg_path = tmpdir / "config"
    cfg_path.write_text("\n".join(cfg_lines) + "\n")
    return cfg_path


def _resolve_destination(RNS, dest_hash: bytes, path_timeout: float):
    """Block until RNS has a path to ``dest_hash`` or timeout.

    Requests a path if none is known. Returns the recalled ``Identity`` on
    success, None on timeout.
    """
    if not RNS.Transport.has_path(dest_hash):
        print(f"path unknown for {dest_hash.hex()} — requesting...")
        RNS.Transport.request_path(dest_hash)

    deadline = time.monotonic() + path_timeout
    while time.monotonic() < deadline:
        if RNS.Transport.has_path(dest_hash):
            break
        time.sleep(0.1)

    if not RNS.Transport.has_path(dest_hash):
        return None
    return RNS.Identity.recall(dest_hash)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--to",
        required=True,
        help="Target LXMF destination hash (32 hex chars / 16 bytes)",
    )
    parser.add_argument(
        "--text",
        default=f"rns-to-mesh validator ping {int(time.time())}",
        help="LXMF message body",
    )
    parser.add_argument(
        "--title",
        default="RNS→Mesh validator",
        help="LXMF title (subject)",
    )
    parser.add_argument(
        "--path-timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for path resolution (default 5)",
    )
    parser.add_argument(
        "--delivery-timeout",
        type=float,
        default=0.0,
        help="Seconds to wait for delivery confirmation (0 = don't wait, default)",
    )
    parser.add_argument(
        "--identity-path",
        default=None,
        help="Where to persist the validator's LXMF identity "
             "(default ~/.config/meshforge/lxmf_validator_identity)",
    )
    args = parser.parse_args(argv)

    target_hex = args.to.strip().lower()
    try:
        dest_hash = bytes.fromhex(target_hex)
    except ValueError:
        print(f"error: --to must be hex, got {args.to!r}", file=sys.stderr)
        return 2
    if len(dest_hash) != 16:
        print(
            f"error: LXMF destination hash must be 16 bytes (32 hex chars), "
            f"got {len(dest_hash)} bytes",
            file=sys.stderr,
        )
        return 2

    try:
        import RNS
        import LXMF
    except ImportError as e:
        print(
            f"error: {e}. Install: pip3 install --user --break-system-packages "
            f"rns lxmf",
            file=sys.stderr,
        )
        return 3

    from utils.paths import get_real_user_home

    with tempfile.TemporaryDirectory(prefix="meshforge_rns_validator_") as tmp:
        tmpdir = Path(tmp)
        _build_client_config(tmpdir)

        print(f"initialising RNS (configdir={tmpdir})...")
        try:
            reticulum = RNS.Reticulum(configdir=str(tmpdir), loglevel=2)
        except Exception as e:
            print(f"error: RNS init failed: {e}", file=sys.stderr)
            return 4

        if getattr(reticulum, "is_connected_to_shared_instance", False):
            print("connected to local rnsd as shared-instance client")
        elif getattr(reticulum, "is_shared_instance", False):
            print("this process IS the shared rnsd (unexpected)")
        else:
            print(
                "warn: running STANDALONE (not sharing rnsd) — pathfinder "
                "starts cold and may not find 2-hop destinations without "
                "announces",
                file=sys.stderr,
            )

        if args.identity_path:
            identity_path = Path(args.identity_path)
        else:
            identity_path = (
                get_real_user_home() / ".config" / "meshforge"
                / "lxmf_validator_identity"
            )
        identity_path.parent.mkdir(parents=True, exist_ok=True)

        if identity_path.exists():
            identity = RNS.Identity.from_file(str(identity_path))
            print(f"loaded validator identity from {identity_path}")
        else:
            identity = RNS.Identity()
            identity.to_file(str(identity_path))
            print(f"generated validator identity at {identity_path}")

        lxmf_storage = tmpdir / "lxmf"
        lxmf_storage.mkdir(parents=True, exist_ok=True)
        router = LXMF.LXMRouter(storagepath=str(lxmf_storage))
        source = router.register_delivery_identity(identity, display_name="validator")
        print(f"validator LXMF source hash: {source.hash.hex()}")

        print(f"resolving path to {dest_hash.hex()} "
              f"(timeout={args.path_timeout}s)...")
        dest_identity = _resolve_destination(RNS, dest_hash, args.path_timeout)
        if dest_identity is None:
            print(
                f"error: no path to {dest_hash.hex()} after "
                f"{args.path_timeout}s — is the gateway announced?",
                file=sys.stderr,
            )
            return 5

        destination = RNS.Destination(
            dest_identity,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            "lxmf",
            "delivery",
        )

        lxm = LXMF.LXMessage(
            destination,
            source,
            args.text,
            args.title,
        )

        delivered = {"ok": False, "failed": None}
        if args.delivery_timeout > 0:
            def _on_delivered(receipt):
                delivered["ok"] = True

            def _on_failed(receipt):
                reason = "delivery_failed"
                if hasattr(receipt, "failure_reason"):
                    reason = str(receipt.failure_reason)
                delivered["failed"] = reason

            try:
                lxm.register_delivery_callback(_on_delivered)
                lxm.register_failed_callback(_on_failed)
            except (AttributeError, TypeError):
                print(
                    "warn: this LXMF build does not expose delivery callbacks; "
                    "will not wait for confirmation",
                    file=sys.stderr,
                )
                args.delivery_timeout = 0

        print(f"sending LXMF: {args.text!r}")
        router.handle_outbound(lxm)

        if args.delivery_timeout <= 0:
            print(
                "handed to LXMRouter — check gateway log for "
                "'Message bridged' to confirm RNS→Mesh round-trip"
            )
            return 0

        print(f"waiting up to {args.delivery_timeout}s for delivery...")
        deadline = time.monotonic() + args.delivery_timeout
        while time.monotonic() < deadline:
            if delivered["ok"]:
                print("delivery CONFIRMED")
                return 0
            if delivered["failed"]:
                print(
                    f"error: delivery FAILED ({delivered['failed']})",
                    file=sys.stderr,
                )
                return 6
            time.sleep(0.1)

        print(
            f"warn: no confirmation within {args.delivery_timeout}s — "
            f"gateway may still have processed it; check its log",
            file=sys.stderr,
        )
        return 7


if __name__ == "__main__":
    sys.exit(main())
