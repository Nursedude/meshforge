#!/usr/bin/env python3
"""Send an LXMF message to a gateway's LXMF destination for RNS→Mesh validation.

Purpose: close the Issue #40 acceptance leg that otherwise requires an operator
at the NomadNet TUI. Runs from any fleet box as the operator user, connects to
the local rnsd as a shared-instance client (using Issue #41's pinned rpc_key if
present), sends an LXMF text message to the target destination hash, and waits
for delivery confirmation. The gateway then bridges to Meshtastic — verify by
tailing ``journalctl -u meshforge-gateway`` on the gateway host for a matching
``Message bridged`` line.

The target gateway hash is your gateway's LXMF source hash. Find it via:
    journalctl -u meshforge-gateway | grep 'Gateway LXMF destination'
Or set ``MESHFORGE_GATEWAY_HASH`` in the environment to skip ``--to``.

Usage:
    # Default text, hash from $MESHFORGE_GATEWAY_HASH or --to
    python3 scripts/validate_rns_to_mesh.py --to <gateway_lxmf_hash>

    # Custom text + longer path-request window + delivery wait
    python3 scripts/validate_rns_to_mesh.py \\
        --to <gateway_lxmf_hash> \\
        --text "@!ffffffff ping from validator" \\
        --path-timeout 10 --delivery-timeout 20

Exits 0 on successful handoff to rnsd (or delivery confirmation if waited for),
nonzero on missing path, init failure, or timeout.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Set by main() so __main__ can remove the temp configdir as the LAST thing
# before os._exit(). _teardown()'s own rmtree runs first and is usually
# enough; this is the pass that closes the window in which RNS's threads
# re-persist state into the tree (measured: ~1 run in 3 still leaked an
# 80 KB dir with only _teardown's removal).
_TMPDIR: Path | None = None

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


def _teardown(RNS, reticulum, router, tmpdir: Path) -> None:
    """Stop RNS/LXMF, THEN remove the temp configdir — in that order.

    Why the order is the whole point (2026-09-17): this script used to run
    inside ``with tempfile.TemporaryDirectory(...)`` and ``return`` from the
    body, so the tree was deleted while RNS's daemon threads were still
    live. The next path-response announce called ``rotate_ratchets()`` ->
    ``_persist_ratchets()`` and raised ``FileNotFoundError`` from
    ``Thread-N (job)`` on EVERY run — after a successful send, so the
    script still exited 0 and the exit code could not see it.

    ⚠️ The traceback's path (``.../lxmf/lxmf/ratchets/``) reads like a
    directory that was never created, and the first diagnosis in this
    session said exactly that. It is wrong: ``LXMRouter.register_delivery_
    identity`` makes that directory itself. The directory existed and was
    then DELETED out from under a running thread. Read the lifetime, not
    just the path.

    LXMF's ``exit_handler`` is idempotent (``exit_handler_running``), so the
    ``atexit`` copy it registers is a no-op after this one — and under
    ``__main__`` nothing registered with ``atexit`` runs at all, because the
    entrypoint leaves via ``os._exit()``. That is deliberate: those handlers
    write into this same tree and were re-creating it behind us.

    Best-effort throughout: teardown must never turn a successful validation
    into a nonzero exit, so every step is caught and only warns.
    """
    # Flush FIRST. Everything below can replace or close the streams, and a
    # redirected stdout is block-buffered — an unflushed buffer that gets
    # swapped out is silently discarded, taking the entire verdict with it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass

    try:
        if router is not None:
            router.exit_handler()
    except Exception as e:  # noqa: BLE001 - teardown must not mask the verdict
        print(f"warn: LXMF teardown: {e}", file=sys.stderr)

    # ⚠️ detach_interfaces(), deliberately NOT RNS.Reticulum.exit_handler().
    # The full handler ends with RNS._detach_stdout(), which rebinds
    # sys.stdout/sys.stderr to os.devnull WITHOUT flushing them — calling it
    # here ate every line this script had printed and left a successful run
    # looking like it produced nothing (measured 2026-09-17, in the first cut
    # of this very fix). The handler's other work is persisting state into a
    # configdir we are about to delete, which is worth nothing to us. All we
    # actually want is the network going quiet.
    if reticulum is not None:
        try:
            RNS.Transport.detach_interfaces()
        except Exception as e:  # noqa: BLE001
            print(f"warn: RNS interface detach: {e}", file=sys.stderr)

    # Settle, then remove. This is the last word only because __main__ takes
    # the process down with os._exit() immediately afterwards — otherwise
    # RNS's atexit handler runs LATER, persists Transport/Identity state and
    # RECREATES the tree we just deleted (measured: every run of the original
    # script left an 80 KB dir in /tmp, and /tmp is tmpfs on this fleet).
    time.sleep(0.25)
    shutil.rmtree(tmpdir, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--to",
        default=os.environ.get("MESHFORGE_GATEWAY_HASH"),
        help="Target LXMF destination hash (32 hex chars / 16 bytes). "
             "Defaults to $MESHFORGE_GATEWAY_HASH if set.",
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

    if not args.to:
        print(
            "error: --to is required (or set $MESHFORGE_GATEWAY_HASH). "
            "Find your gateway hash via: "
            "journalctl -u meshforge-gateway | grep 'Gateway LXMF destination'",
            file=sys.stderr,
        )
        return 2

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

    global _TMPDIR
    tmpdir = Path(tempfile.mkdtemp(prefix="meshforge_rns_validator_"))
    _TMPDIR = tmpdir
    router = None
    reticulum = None
    # try/finally rather than `with TemporaryDirectory`: every `return` below
    # is a success path, and the directory must outlive the RNS threads that
    # write into it. See _teardown().
    try:
        _build_client_config(tmpdir)

        print(f"initialising RNS (configdir={tmpdir})...")
        # MF019: through the guarded chokepoint, never RNS.Reticulum() raw.
        # A validator is exactly the caller that must not hang on a wedged
        # rnsd — it exists to return a verdict. open_reticulum() returns None
        # instead of blocking the thread (#68) and raises loud on a foreign
        # @rns owner (#69). ⚠️ The raw construction here PRE-DATED this fix
        # and lint never saw it: `lint.py --all` walks src/ only, so nothing
        # in scripts/ is covered by the documented pre-push check — it
        # surfaced only when the pre-commit hook ran --staged on this file.
        from utils.rns_init import open_reticulum
        try:
            reticulum = open_reticulum(str(tmpdir), loglevel=2)
        except Exception as e:
            print(f"error: RNS init failed: {e}", file=sys.stderr)
            return 4
        if reticulum is None:
            print(
                "error: RNS unavailable or degraded (no listener, or a wedged "
                "rnsd that did not accept within the probe window) — "
                "check `systemctl status rnsd` and `timeout 8 rnstatus`",
                file=sys.stderr,
            )
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
    finally:
        _teardown(RNS, reticulum, router, tmpdir)


if __name__ == "__main__":
    _code = main()
    # ⚠️ NOT sys.exit(). RNS registers an atexit handler that ends in
    # RNS.exit() -> os._exit(0), so a SystemExit carrying our code is
    # discarded during interpreter shutdown and the caller reads 0. Measured
    # 2026-09-17 on the PRE-CHANGE script: "no path to <gateway>" — the
    # script's documented exit 5 — reached the shell as **0**. A cron or
    # drill gating on $? would have read a total failure to find the gateway
    # as success, which is this repo's whole defect class in miniature.
    #
    # Taking the exit ourselves makes the documented codes authoritative, and
    # incidentally stops RNS re-creating the temp configdir after _teardown
    # removed it. Flush first — os._exit() does not.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.flush()
        except Exception:  # noqa: BLE001
            pass
    if _TMPDIR is not None:
        shutil.rmtree(_TMPDIR, ignore_errors=True)
    os._exit(_code)
