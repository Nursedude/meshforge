"""RNS attach + discovery loop for UnifiedNodeTracker (split from node_tracker.py, MF025).

The tracker is a pure RNS *consumer*: it joins rnsd's shared instance and
never becomes the @rns host. Attach can DEGRADE at start — rnsd not running
yet, or open_reticulum() returning None (wedged rnsd, #68; a join-only
refusal mid-restart, #69). Until 2026-10-09 that ended node discovery for the
whole run ("will retry on next start"), silently. Now a degraded attach hands
off to a retry thread that rejoins on a backoff without a restart, and
``get_rns_attach_state()`` says what it is doing (honest_failure_modes #9).
"""

import logging
import threading
import time

from .network_topology import path_entry_hops
from .node_models import UnifiedNode
from ._signal_guard import suppress_signal_off_main
from utils.boundary_timing import timed_boundary
from gateway.bounded_rpc import bounded_call
from utils.safe_import import safe_import

# Same logger name as node_tracker.py so journal greps keep working.
logger = logging.getLogger("gateway.node_tracker")

# RNS aspects the tracker registers announce handlers for.
#
# IMPORTANT: do NOT also register a None/catch-all handler. RNS aspect
# filters are NOT exclusive — a None handler receives every announce already
# covered by the aspect-specific ones, so each LXMF announce was being parsed
# twice (once with the correct aspect → LXMF_DELIVERY, once via catch-all
# without aspect → UNKNOWN). The symptom was duplicated `Parsed announce ...`
# log lines and the node's service_type flapping between LXMF_DELIVERY and
# UNKNOWN as the second handler overwrote the first. Adding a new aspect to
# this list is the correct way to broaden coverage; the catch-all is
# intentionally absent.
known_aspects = [
    "lxmf.delivery",       # LXMF messaging (Sideband, NomadNet)
    "lxmf.propagation",    # LXMF propagation nodes
    "nomadnetwork.node",   # Nomad Network pages
]


class AspectAnnounceHandler:
    """Announce handler that passes aspect info to tracker"""
    def __init__(self, tracker, aspect: str = None):
        self.tracker = tracker
        self.aspect_filter = aspect  # None = catch all

    def received_announce(self, destination_hash, announced_identity, app_data):
        try:
            self.tracker._on_rns_announce(
                destination_hash, announced_identity, app_data,
                aspect=self.aspect_filter
            )
        except Exception as e:
            logger.error(f"Error handling RNS announce: {e}")


class NodeTrackerRNSMixin:
    """RNS attach, attach retry, and the path_table discovery loop."""

    # Retry backoff for a degraded attach: first retry after INITIAL, doubling
    # to MAX. 30 s matches the discovery loop's own cadence; 300 s caps the
    # cost of a box that genuinely runs without rnsd.
    RNS_RETRY_INITIAL_S = 30.0
    RNS_RETRY_MAX_S = 300.0

    def _rns_attach_init(self):
        self._rns_thread = None
        self._rns_retry_thread = None
        self._reticulum = None
        self._rns_connected = False
        self._rns_module = None
        self._rns_attach_attempts = 0
        self._rns_attach_last_error = None
        self._rns_handlers_registered = False

    def get_rns_attach_state(self) -> dict:
        """What RNS discovery is doing right now — for status surfaces."""
        retry = self._rns_retry_thread
        return {
            "connected": self._rns_connected,
            "retrying": bool(retry is not None and retry.is_alive()),
            "attempts": self._rns_attach_attempts,
            "last_error": self._rns_attach_last_error,
        }

    def _init_rns_main_thread(self):
        """Initialize RNS from main thread, then start background listener.

        IMPORTANT: MeshForge operates as a CLIENT ONLY - it connects to existing
        rnsd/NomadNet instances but never creates its own RNS instance that would
        bind interfaces and conflict with NomadNet or other RNS services.

        NOTE: RNS.Reticulum() uses signal handlers which ONLY work in the main
        thread. If called from a background thread, it will fail with:
        "signal only works in main thread of the main interpreter"
        (the retry thread suppresses them — see _signal_guard).
        """
        current = threading.current_thread()
        main = threading.main_thread()
        is_main = current is main
        logger.info(f"Thread check: current={current.name}, main={main.name}, is_main={is_main}")

        if not is_main:
            logger.warning("RNS initialization must be in main thread - skipping node discovery")
            logger.info("RNS node discovery disabled (call start() from main thread to enable)")
            self._rns_connected = False
            return

        # Deferred: importing RNS costs ~180 ms and only this discovery path
        # needs it — at module level it taxed every status_bar/TUI startup.
        RNS, _has_rns = safe_import('RNS')
        if not _has_rns:
            logger.info("RNS module not installed. To enable RNS node discovery:")
            logger.info("  1. Install RNS: pipx install rns")
            logger.info("  2. Start rnsd: sudo systemctl start rnsd")
            logger.info("  3. Restart MeshForge")
            return

        if self._attach_rns(RNS):
            return
        if not self._running:
            return
        live = self._rns_retry_thread
        if live is not None and live.is_alive():
            return  # a retry from an earlier start() is still on it
        logger.warning(
            "RNS node discovery degraded (%s) — retrying every %.0f-%.0fs "
            "until rnsd answers; no restart needed.",
            self._rns_attach_last_error, self.RNS_RETRY_INITIAL_S,
            self.RNS_RETRY_MAX_S)
        self._rns_retry_thread = threading.Thread(
            target=self._rns_retry_loop, args=(RNS,),
            daemon=True, name="NodeTrackerRNSRetry")
        self._rns_retry_thread.start()

    def _rns_retry_loop(self, RNS):
        """Re-attempt a degraded attach on a backoff until connected or stopped."""
        delay = self.RNS_RETRY_INITIAL_S
        while self._running and threading.current_thread() is self._rns_retry_thread:
            if self._stop_event.wait(delay):
                return
            with suppress_signal_off_main():
                ok = self._attach_rns(RNS)
            if ok:
                logger.info(
                    "RNS node discovery recovered after %d attach attempt(s)",
                    self._rns_attach_attempts)
                return
            logger.info("RNS attach retry %d failed (%s); next in %.0fs",
                        self._rns_attach_attempts, self._rns_attach_last_error,
                        min(delay * 2, self.RNS_RETRY_MAX_S))
            delay = min(delay * 2, self.RNS_RETRY_MAX_S)

    def _attach_rns(self, RNS) -> bool:
        """One attach attempt. True = connected and discovery running.

        Every False leaves ``_rns_attach_last_error`` naming why, so the
        degraded state is legible rather than silent.
        """
        self._rns_attach_attempts += 1
        try:
            logger.info("Checking for existing RNS service...")

            # Check if rnsd is already running
            from utils.gateway_diagnostic import find_rns_processes
            rns_pids = find_rns_processes()

            if not rns_pids:
                # No rnsd running - DO NOT initialize our own RNS instance
                # This would bind AutoInterface port and block NomadNet from starting
                logger.info("No rnsd detected - RNS node discovery waits for rnsd")
                self._rns_attach_last_error = "no rnsd process"
                self._rns_connected = False
                return False

            # rnsd is running - connect to existing instance as CLIENT ONLY
            logger.info(f"rnsd detected (PID: {rns_pids[0]}), connecting as client...")
            try:
                # Canonical clean-client config (NO interfaces — avoids binding
                # ports rnsd owns) at a FIXED location, SHARED with the bridge
                # connection so the gateway process's RNS singleton resourcepath
                # is deterministic (gw-resourcepath-determinism, 2026-06-27). The
                # helper propagates the box instance_name + rnsd's rpc_key
                # (Issue #37/#40/#41). instance_name is still needed below for
                # the shared-instance preflight + log lines.
                from utils.paths import ReticulumPaths
                client_config_dir = ReticulumPaths.ensure_rns_client_configdir()
                instance_name = ReticulumPaths.get_configured_instance_name()

                # Pre-flight: check if shared instance socket is listening
                try:
                    from utils.service_check import check_rns_shared_instance
                    if not check_rns_shared_instance(instance_name=instance_name):
                        logger.warning(
                            "rnsd PID %d found but shared instance @rns/%s not available "
                            "(may be initializing or hung)", rns_pids[0], instance_name
                        )
                except ImportError:
                    pass  # service_check not available, proceed anyway

                # Connect using client-only config via the guarded chokepoint.
                # require_listener=True keeps node_tracker a pure RNS *consumer*
                # (never becomes the @rns host); the #68 connect probe degrades
                # instead of hanging this thread on a wedged rnsd. Cold-start
                # RNS attach is genuinely slow (identity load + shared-instance
                # socket open + state sync), so timed_boundary still measures
                # the now-bounded attach time at a higher threshold.
                from utils.rns_init import open_reticulum
                with timed_boundary("rnsd.attach", threshold_s=10.0):
                    self._reticulum = open_reticulum(
                        str(client_config_dir), require_listener=True,
                    )
                if not self._running:
                    # stop() ran while we were attaching (it waits only its
                    # timeout): a stopped tracker must not go "connected",
                    # register handlers, or add nodes after its final save.
                    self._rns_attach_last_error = "stopped during attach"
                    return False
                if self._reticulum is None:
                    logger.warning(
                        "RNS attach degraded: shared instance @rns/%s absent or "
                        "wedged (#68 fail-open).", instance_name)
                    self._rns_attach_last_error = (
                        f"@rns/{instance_name} absent or wedged")
                    self._rns_connected = False
                    return False
                self._rns_connected = True
                self._rns_attach_last_error = None
                logger.info("Connected to existing rnsd instance")

                # Register announce handlers for node discovery — once per
                # process: RNS.Transport keeps them across our attach attempts.
                if not self._rns_handlers_registered:
                    for aspect in known_aspects:
                        RNS.Transport.register_announce_handler(
                            AspectAnnounceHandler(self, aspect))
                        logger.debug(f"Registered announce handler for aspect: {aspect}")
                    self._rns_handlers_registered = True
                    logger.info(f"Registered {len(known_aspects)} aspect-scoped announce handlers with rnsd")

                # Load known destinations from rnsd (may be empty initially)
                self._load_known_rns_destinations(RNS)

                # Store RNS module reference for background loop
                self._rns_module = RNS

                # Start background loop (will re-check path_table periodically)
                self._rns_thread = threading.Thread(target=self._rns_loop, daemon=True)
                self._rns_thread.start()

                # Schedule delayed re-check after 5 seconds for sync'd data
                def delayed_check():
                    if self._stop_event.wait(5):
                        return
                    if self._running and self._rns_connected:
                        logger.debug("Running delayed RNS destination check...")
                        self._load_known_rns_destinations(RNS)

                threading.Thread(target=delayed_check, daemon=True).start()
                return True

            except Exception as e:
                logger.warning(f"Could not connect to rnsd: {e}")
                try:
                    from utils.gateway_diagnostic import diagnose_rnsd_connection
                    diagnose_rnsd_connection(rns_pids, error=e)
                except Exception:
                    pass  # diagnostic failure should never block startup
                self._rns_attach_last_error = f"connect failed: {e}"
                self._rns_connected = False
                return False

        except Exception as e:
            logger.warning(f"Failed to initialize RNS discovery: {e}")
            self._rns_attach_last_error = f"init failed: {e}"
            self._rns_connected = False
            return False

    def _rns_loop(self):
        """Background loop for RNS - periodically check for new destinations.

        When connected as a shared instance client, the path_table may not
        be populated immediately. This loop periodically checks for new
        destinations that rnsd has discovered.
        """
        RNS = self._rns_module

        check_interval = 30  # Check every 30 seconds
        last_check = 0

        while self._running:
            if self._stop_event.wait(1):
                break

            # Periodic check for new RNS destinations
            current_time = time.time()
            if current_time - last_check >= check_interval:
                last_check = current_time
                try:
                    # Re-check path_table for newly discovered routes.
                    # `path_table` is a property — under a wedged rnsd
                    # RPC listener, accessing it has been observed to
                    # block. Snapshot under a hard timeout so a slow
                    # rnsd can't freeze the node-tracker scan thread.
                    new_count = 0
                    path_table_snapshot = bounded_call(
                        "rnsd.path_table",
                        lambda: (
                            dict(RNS.Transport.path_table)
                            if hasattr(RNS.Transport, 'path_table')
                            and RNS.Transport.path_table
                            else {}
                        ),
                        timeout_s=2.0,
                    )
                    if path_table_snapshot:
                        for dest_hash, path_data in path_table_snapshot.items():
                            try:
                                if isinstance(dest_hash, bytes) and len(dest_hash) == 16:
                                    node_id = f"rns_{dest_hash.hex()[:16]}"
                                    if node_id not in self._nodes:
                                        hops = path_entry_hops(path_data)
                                        node = UnifiedNode.from_rns(dest_hash, name="", app_data=None)
                                        node.hops = hops  # None when unreadable — never a 0 default
                                        self.add_node(node)
                                        new_count += 1
                                        logger.debug(f"Discovered RNS destination: {dest_hash.hex()[:8]} ({hops} hops)")
                            except Exception as e:
                                logger.debug(f"Error processing path_table entry: {e}")

                    if new_count > 0:
                        logger.info(f"Discovered {new_count} new RNS destinations from path_table")

                except Exception as e:
                    logger.debug(f"Error checking path_table: {e}")
