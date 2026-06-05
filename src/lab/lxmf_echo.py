"""LXMF echo responder daemon.

For every incoming LXMF whose body parses as
``PING seq=<int> from=<short>``, replies ``ACK seq=<int> orig=<short>
recv_at=<iso>`` to the source's lxmf.delivery destination.

Identity: ``~/.config/meshforge/lab_echo_identity`` (auto-generated on
first run). The destination hash is logged at startup so an operator
can ssh-distribute it into peers' ``~/.config/meshforge/lab_peers``.

Wire format and short-name conventions live in ``_lab_common`` so the
echo and tracer can't drift out of sync.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

# Lazy RNS / LXMF imports so unit tests can patch around them.

logger = logging.getLogger("lab.echo")


# Re-announce cadence: peers' RNS path table evicts after the default
# announce TTL (~12h). One announce per hour keeps us reliably reachable
# without flooding the channel.
ANNOUNCE_INTERVAL_S = 3600

# Bounded wait for an on-demand path request when a requester's identity
# is not in the local cache (the routed-leaf case — see _send_ack).
# Pings arrive on a 10-min cadence, so a few seconds inside the rx
# callback is harmless.
PATH_REQUEST_WAIT_S = 5.0


def _build_client_config(tmpdir: Path) -> Path:
    """Write a share-instance client config that matches rnsd's keying.

    Mirrors ``scripts/validate_rns_to_mesh.py._build_client_config``.
    Keeping the helper local (vs importing from scripts/) avoids pulling
    a script into the package import path.
    """
    from utils.paths import ReticulumPaths

    instance_name = ReticulumPaths.get_configured_instance_name()
    lines = [
        "# MeshForge lab-echo RNS client config (auto-generated)",
        "[reticulum]",
        "share_instance = Yes",
        f"instance_name = {instance_name}",
        "shared_instance_port = 37428",
        "instance_control_port = 37429",
    ]
    rpc_key = ReticulumPaths.get_shared_rpc_key()
    if rpc_key:
        lines.append(f"rpc_key = {rpc_key}")
    cfg = tmpdir / "config"
    cfg.write_text("\n".join(lines) + "\n")
    return cfg


class _EchoState:
    """Container the receive callback closes over.

    Holds the LXMRouter, source destination, and a counter — keeping
    them on an object (vs module globals) makes the daemon testable.
    """

    def __init__(self):
        self.router = None  # LXMRouter
        self.source = None  # delivery destination (RNS.Destination)
        self.identity = None  # RNS.Identity
        self.rx_count = 0
        self.tx_count = 0
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def wait(self, seconds: float) -> bool:
        """Sleep interruptibly; returns True if stop was signaled."""
        return self._stop.wait(seconds)


def make_on_receive(state: _EchoState):
    """Build the LXMRouter delivery callback.

    Pulled out as a factory so unit tests can drive it without spinning
    up RNS — the callback only depends on the message object's
    ``content`` and ``source_hash``, plus the LXMF builder.
    """
    from lab._lab_common import make_ack_body, parse_ping

    def _on_receive(message):
        # Decode body. message.content is bytes in newer LXMF builds,
        # str in older ones (per Issue #40 — same defensive pattern).
        body = message.content
        if isinstance(body, bytes):
            try:
                body = body.decode("utf-8")
            except UnicodeDecodeError:
                body = body.decode("utf-8", errors="replace")

        ping = parse_ping(body)
        if not ping:
            # Not a PING — log and skip. Could be a stray LXMF, a chat
            # message, anything. The echo destination is shared via the
            # lab_peers file; we don't refuse non-PING input, just ignore.
            logger.debug(
                "echo: rx ignored (not PING) from=%s len=%d",
                message.source_hash.hex(), len(body),
            )
            return

        state.rx_count += 1
        logger.info(
            "echo: rx seq=%d from=%s source=%s",
            ping.seq, ping.sender, message.source_hash.hex(),
        )

        try:
            _send_ack(state, message.source_hash, ping)
        except Exception as exc:
            # One failed ACK must not kill the daemon — soak runs for
            # weeks. Bridge errors / transient path loss happen.
            # exc_info=True puts the traceback in the journal so we
            # can diagnose without re-running; some RNS exceptions have
            # empty str() and would otherwise produce a stub line.
            logger.warning(
                "echo: tx FAILED seq=%d to=%s: %s: %s",
                ping.seq, message.source_hash.hex(),
                type(exc).__name__, exc or "(no message)",
                exc_info=True,
            )

    return _on_receive


def _send_ack(state: _EchoState, dest_hash: bytes, ping):
    """Resolve dest, build LXMessage, hand to router."""
    import RNS
    import LXMF
    from lab._lab_common import make_ack_body

    dest_identity = RNS.Identity.recall(dest_hash)
    if dest_identity is None:
        # Flat-LAN boxes hear every announce, so recall always hit and a
        # None looked like "path forgotten". A ROUTED LEAF (moc5 behind
        # the AREDN hAP, 2026-06-04) receives pings from tracers whose
        # announces never propagated to it — recall misses and every
        # reply died here while the inbound ping arrived fine. Cure:
        # fetch the identity on demand with a bounded path request (the
        # path response carries the announcing identity), exactly like
        # the tracer's outbound leg already does.
        logger.info(
            "echo: identity for %s not cached — requesting path",
            dest_hash.hex(),
        )
        RNS.Transport.request_path(dest_hash)
        deadline = time.time() + PATH_REQUEST_WAIT_S
        while dest_identity is None and time.time() < deadline:
            time.sleep(0.25)
            dest_identity = RNS.Identity.recall(dest_hash)
    if dest_identity is None:
        logger.warning(
            "echo: tx skipped seq=%d to=%s (Identity.recall returned None "
            "after %.0fs path request)",
            ping.seq, dest_hash.hex(), PATH_REQUEST_WAIT_S,
        )
        return

    destination = RNS.Destination(
        dest_identity,
        RNS.Destination.OUT,
        RNS.Destination.SINGLE,
        "lxmf",
        "delivery",
    )
    body = make_ack_body(ping.seq, ping.sender)
    lxm = LXMF.LXMessage(destination, state.source, body, "lab echo ACK")
    state.router.handle_outbound(lxm)
    state.tx_count += 1
    logger.info(
        "echo: tx seq=%d to=%s bytes=%d",
        ping.seq, dest_hash.hex(), len(body),
    )


def run_daemon(loglevel: str = "INFO", announce_interval_s: int = ANNOUNCE_INTERVAL_S) -> int:
    """Main entry. Returns process exit code."""
    logging.basicConfig(
        level=getattr(logging, loglevel.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    try:
        import RNS
        import LXMF
    except ImportError as exc:
        logger.error("RNS/LXMF not installed: %s", exc)
        return 3

    from lab._lab_common import (
        init_reticulum_with_watchdog, load_or_create_identity, short_name,
    )

    state = _EchoState()

    def _on_signal(signum, _frame):
        logger.info("echo: received signal %d — shutting down", signum)
        state.stop()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    with tempfile.TemporaryDirectory(prefix="meshforge_lab_echo_") as tmp:
        tmpdir = Path(tmp)
        _build_client_config(tmpdir)

        logger.info("echo: initialising RNS (configdir=%s)", tmpdir)
        try:
            reticulum = init_reticulum_with_watchdog(str(tmpdir))
        except Exception as exc:
            logger.error("echo: RNS init failed: %s", exc)
            return 4

        if not getattr(reticulum, "is_connected_to_shared_instance", False):
            logger.warning(
                "echo: NOT connected to rnsd shared instance — peer-reach "
                "will be limited to direct interfaces"
            )

        identity, identity_path = load_or_create_identity("lab_echo")
        state.identity = identity
        logger.info("echo: identity loaded from %s", identity_path)

        # LXMF storage in tmpdir keeps state ephemeral — echo is stateless,
        # we don't want stale outbound queues across restarts.
        lxmf_storage = tmpdir / "lxmf"
        lxmf_storage.mkdir(parents=True, exist_ok=True)
        state.router = LXMF.LXMRouter(storagepath=str(lxmf_storage))
        display_name = f"lab-echo ({short_name()})"
        state.source = state.router.register_delivery_identity(
            identity, display_name=display_name,
        )
        logger.info(
            "echo: lab-echo destination hash: %s (%s)",
            state.source.hash.hex(), display_name,
        )

        # Register inbound callback.
        state.router.register_delivery_callback(make_on_receive(state))

        # Initial announce + periodic re-announce.
        state.router.announce(state.source.hash)
        last_announce = time.monotonic()

        # Main loop. We don't poll anything — RNS' transport thread
        # drives delivery callbacks. Just sleep until shutdown, with
        # periodic re-announce to keep peers' path tables warm.
        while not state.stopped:
            if state.wait(min(60.0, announce_interval_s)):
                break
            if time.monotonic() - last_announce >= announce_interval_s:
                try:
                    state.router.announce(state.source.hash)
                    last_announce = time.monotonic()
                    logger.debug("echo: re-announced")
                except Exception as exc:
                    logger.warning("echo: re-announce failed: %s", exc)

        logger.info(
            "echo: shutdown — rx=%d tx=%d", state.rx_count, state.tx_count,
        )
        return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--init",
        action="store_true",
        help="Generate the identity file (idempotent) and print the "
             "destination hash to stdout, then exit. Use for fleet "
             "bootstrap before the systemd service is enabled.",
    )
    parser.add_argument(
        "--loglevel", default="INFO",
        help="Python logging level (default INFO)",
    )
    parser.add_argument(
        "--announce-interval", type=int, default=ANNOUNCE_INTERVAL_S,
        help=f"Re-announce cadence in seconds (default {ANNOUNCE_INTERVAL_S})",
    )
    args = parser.parse_args(argv)

    if args.init:
        return _init_only()

    return run_daemon(
        loglevel=args.loglevel,
        announce_interval_s=args.announce_interval,
    )


def _init_only() -> int:
    """`--init` path: just generate the identity, print the hash, exit.

    Lets the operator do `python3 -m lab.lxmf_echo --init` on every
    fleet box, scp the hashes into a central lab_peers file, then start
    the service. No RNS transport runtime needed — Identity is local.
    """
    try:
        import RNS  # noqa: F401  — required for Identity.from_file/Identity()
    except ImportError as exc:
        print(f"error: RNS not installed: {exc}", file=sys.stderr)
        return 3

    from lab._lab_common import load_or_create_identity, short_name

    identity, path = load_or_create_identity("lab_echo")

    # Compute the LXMF delivery destination hash without bringing up
    # RNS.Reticulum. RNS.Destination.hash() is a static helper that
    # derives the destination hash from an identity + aspects — no
    # Transport / Reticulum instance required, which is exactly the
    # shape we want for a one-shot bootstrap helper.
    import RNS

    dest_hash = RNS.Destination.hash(identity, "lxmf", "delivery")
    print(f"{short_name()}={dest_hash.hex()}")
    print(f"# identity at {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
