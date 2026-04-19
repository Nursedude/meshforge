"""RNS/LXMF connection lifecycle for the gateway bridge.

Handles Reticulum initialization, LXMF setup, and teardown.
Extracted from rns_bridge.py for file size compliance (CLAUDE.md #6).
"""

import logging
import os
import signal as _signal_mod
import socket
import threading
import time
from contextlib import contextmanager

from utils.paths import get_real_user_home, ReticulumPaths
from utils.safe_import import safe_import
from utils.service_check import check_service
from utils.config_drift import detect_rnsd_config_drift, get_rnsd_effective_config_dir

logger = logging.getLogger(__name__)

# RNS and LXMF modules (optional)
_RNS_mod, _HAS_RNS = safe_import('RNS')
_LXMF_mod, _HAS_LXMF = safe_import('LXMF')


class RNSConnectionMixin:
    """Mixin providing RNS/LXMF connection lifecycle methods.

    Expects the host class to provide:
        self.config          — GatewayConfig with .rns.config_dir
        self._reticulum      — RNS.Reticulum instance (or None)
        self._lxmf_router    — LXMF.LXMRouter instance (or None)
        self._lxmf_source    — LXMF source (or None)
        self._identity       — RNS.Identity (or None)
        self._connected_rns  — bool
        self._rns_via_rnsd   — bool
        self._rns_init_failed_permanently — bool
        self._rns_pre_initialized — bool
        self._notify_status(event) — status callback
        self._on_lxmf_receive(message) — LXMF delivery callback
        self._on_rns_announce(dest_hash, identity, app_data) — announce callback
        self.node_tracker    — UnifiedNodeTracker
    """

    @staticmethod
    @contextmanager
    def _suppress_signal_in_thread():
        """Suppress signal.signal() calls when not in the main thread.

        LXMF.LXMRouter() and RNS.Reticulum() internally register signal
        handlers for graceful shutdown. When called from a background
        thread, signal.signal() raises ValueError. This context manager
        temporarily replaces signal.signal with a safe wrapper that
        returns SIG_DFL instead of raising.

        On the main thread, this is a no-op passthrough.
        """
        if threading.current_thread() is threading.main_thread():
            yield
            return

        original = _signal_mod.signal

        def _safe_signal(signalnum, handler):
            # Cannot register signal handlers from non-main thread.
            # Return default disposition; bridge has its own shutdown logic.
            return _signal_mod.SIG_DFL

        _signal_mod.signal = _safe_signal
        try:
            yield
        finally:
            _signal_mod.signal = original

    def _init_rns_main_thread(self):
        """Pre-initialize RNS from the main thread.

        RNS.Reticulum() registers signal handlers that only work in the
        main thread. If we defer to the background _rns_loop thread,
        initialization fails with 'signal only works in main thread'.

        When rnsd is running, we connect as a client to its shared instance.

        POLICY: Diagnose, don't fix. This method NEVER restarts services
        or modifies configs. It logs issues and lets the user fix them.
        """
        import threading as _threading
        if _threading.current_thread() is not _threading.main_thread():
            logger.warning("RNS pre-init skipped (not main thread)")
            return

        if not _HAS_RNS:
            logger.info("RNS not installed, will be handled in _connect_rns")
            return

        RNS = _RNS_mod

        # Ensure /etc/reticulum/storage subdirs exist before RNS init.
        # RNS requires ratchets/, resources/, cache/announces/.
        # Create dirs if missing but NEVER restart services.
        if os.geteuid() == 0:
            if not ReticulumPaths.ensure_system_dirs():
                logger.warning("Could not create /etc/reticulum directories "
                             "(filesystem may be read-only)")

        # Detect rnsd process
        try:
            from utils.gateway_diagnostic import find_rns_processes
            rns_pids = find_rns_processes()
        except ImportError:
            rns_pids = []

        # Determine config directory: explicit config > rnsd's actual path > default
        config_dir = self.config.rns.config_dir or None
        if config_dir:
            logger.info(f"Using explicit RNS config dir: {config_dir}")
        else:
            # Check for config drift between gateway and rnsd
            try:
                drift = detect_rnsd_config_drift()
                if drift.drifted:
                    logger.warning("Config drift: %s", drift.message)
                    config_dir = str(drift.rnsd_config_dir)
                    logger.info("Using rnsd's config dir: %s", config_dir)
            except Exception as e:
                logger.debug("Config drift check skipped: %s", e)

        try:
            if rns_pids:
                logger.info(f"rnsd detected (PID: {rns_pids[0]}), "
                           "connecting as shared instance client")
                self._rns_via_rnsd = True

            self._reticulum = RNS.Reticulum(configdir=config_dir)
            self._rns_pre_initialized = True
            logger.info("RNS pre-initialized from main thread")
        except Exception as e:
            err_msg = str(e).lower()
            if "reinitialise" in err_msg or "already running" in err_msg:
                self._rns_pre_initialized = True
                logger.info("RNS already initialized, bridge will use existing instance")
            elif hasattr(e, 'errno') and getattr(e, 'errno', None) == 98:
                logger.warning(f"RNS port conflict: {e} (will retry in background)")
            else:
                logger.warning(f"RNS pre-init failed: {e}")
                try:
                    from utils.gateway_diagnostic import diagnose_rnsd_connection
                    diagnose_rnsd_connection(rns_pids, error=e)
                except Exception:
                    pass  # diagnostic failure should never block init

    def _connect_rns(self):
        """Initialize RNS and LXMF.

        If RNS was pre-initialized from the main thread (via _init_rns_main_thread),
        skips Reticulum initialization and proceeds directly to LXMF setup.
        Otherwise falls back to initialization here (background thread).

        POLICY: Diagnose, don't fix. Never restart services or modify configs.
        """
        if not (_HAS_RNS and _HAS_LXMF):
            missing = []
            if not _HAS_RNS:
                missing.append("rns")
            if not _HAS_LXMF:
                missing.append("lxmf")
            pkgs = " ".join(missing)
            logger.error(
                "Gateway cannot connect: Python package(s) missing: %s. "
                "Fix: pip3 install --user --break-system-packages %s "
                "(see requirements/rns.txt)", pkgs, pkgs,
            )
            self._connected_rns = False
            self._rns_init_failed_permanently = True
            return

        # Pre-flight: verify rnsd is available (advisory, not blocking)
        rnsd_status = check_service('rnsd')
        if not rnsd_status.available:
            logger.warning("rnsd not available: %s", rnsd_status.message)
            if rnsd_status.fix_hint:
                logger.info("Fix: %s", rnsd_status.fix_hint)
            # Continue anyway — RNS can init standalone without rnsd

        RNS = _RNS_mod
        LXMF = _LXMF_mod

        # Both RNS.Reticulum() and LXMF.LXMRouter() register signal
        # handlers internally. When _connect_rns is called from the
        # background _rns_loop thread, signal.signal() raises ValueError.
        # Suppress signal registration for the entire init sequence.
        with self._suppress_signal_in_thread():
            try:
                if self._rns_pre_initialized:
                    logger.info("RNS pre-initialized, proceeding to LXMF setup")
                else:
                    # Fallback: init RNS from background thread.
                    # Works when rnsd is running (client mode, no signal handlers).
                    config_dir = self.config.rns.config_dir or None
                    if not config_dir:
                        try:
                            effective = get_rnsd_effective_config_dir()
                            config_dir = str(effective)
                        except Exception:
                            pass  # Use RNS default resolution

                    try:
                        self._reticulum = RNS.Reticulum(configdir=config_dir)
                    except Exception as e:
                        err_msg = str(e).lower()
                        if "reinitialise" in err_msg or "already running" in err_msg:
                            logger.info("RNS already initialized, proceeding to LXMF")
                        elif "signal only works in main thread" in err_msg:
                            logger.warning("RNS needs main thread init (no rnsd running?)")
                            self._rns_init_failed_permanently = True
                            self._connected_rns = False
                            return
                        elif hasattr(e, 'errno') and getattr(e, 'errno', None) == 98:
                            logger.warning(f"RNS port conflict: {e} (will retry)")
                            self._connected_rns = False
                            return
                        else:
                            raise

                # Set up LXMF messaging on top of the RNS instance
                self._setup_lxmf(RNS, LXMF)

            except Exception as e:
                logger.error(f"Failed to connect to RNS: {e}")
                try:
                    from utils.gateway_diagnostic import (
                        diagnose_rnsd_connection, find_rns_processes
                    )
                    diagnose_rnsd_connection(find_rns_processes(), error=e)
                except Exception:
                    pass  # diagnostic failure should never block bridge
                self._connected_rns = False

    def _setup_lxmf(self, RNS, LXMF):
        """Set up LXMF identity, router, and announce handler.

        Called after RNS is initialized (either pre-init or fallback).
        Separated from _connect_rns to keep the method focused and
        allow LXMF setup to be retried independently.
        """
        # Create or load identity
        identity_path = get_real_user_home() / ".config" / "meshforge" / "gateway_identity"
        if identity_path.exists():
            self._identity = RNS.Identity.from_file(str(identity_path))
        else:
            self._identity = RNS.Identity()
            identity_path.parent.mkdir(parents=True, exist_ok=True)
            self._identity.to_file(str(identity_path))

        # Create LXMF router
        storage_path = get_real_user_home() / ".config" / "meshforge" / "lxmf_storage"
        storage_path.mkdir(parents=True, exist_ok=True)
        self._lxmf_router = LXMF.LXMRouter(storagepath=str(storage_path))

        # Register delivery callback
        self._lxmf_router.register_delivery_callback(self._on_lxmf_receive)

        # Resolve announced display name. Empty config = hostname-tagged default
        # so fleet Pis (fleet-host-3, fleet-host, etc.) are distinguishable in peers'
        # contact lists without operator config. Hex hash (routable address) is
        # cryptographically fixed by the identity file and unaffected by this.
        configured = self.config.rns.gateway_name.strip()
        display_name = configured or f"MeshForge Gateway ({socket.gethostname()})"

        self._lxmf_source = self._lxmf_router.register_delivery_identity(
            self._identity,
            display_name=display_name
        )

        # Configure outbound propagation node for store-and-forward
        prop_node = self.config.rns.propagation_node.strip()
        if prop_node:
            try:
                prop_node_hash = bytes.fromhex(prop_node)
                self._lxmf_router.set_outbound_propagation_node(prop_node_hash)
                logger.info("LXMF propagation node set: %s", prop_node)
            except (ValueError, TypeError) as e:
                logger.error("Invalid propagation_node hash '%s': %s", prop_node, e)

        # Announce presence and log destination so operators (and future LXMF
        # clients) can discover the hash to direct LXMF at this gateway.
        self._lxmf_router.announce(self._lxmf_source.hash)
        self._last_lxmf_announce = time.monotonic()
        logger.info(
            "Gateway LXMF destination: %s (%s)",
            self._lxmf_source.hash.hex(), display_name,
        )

        # Register announce handler for node discovery
        class AnnounceHandler:
            def __init__(self, bridge):
                self.aspect_filter = "lxmf.delivery"
                self.bridge = bridge

            def received_announce(self, dest_hash, announced_identity, app_data):
                self.bridge._on_rns_announce(dest_hash, announced_identity, app_data)

        RNS.Transport.register_announce_handler(AnnounceHandler(self))

        self._connected_rns = True
        logger.info("Connected to RNS (LXMF ready)")
        self._notify_status("rns_connected")

    def _disconnect_rns(self):
        """Disconnect from RNS and release ports"""
        # Properly shut down RNS to release ports
        if self._reticulum:
            try:
                import RNS
                # RNS.Transport.exithandler() closes all interfaces and releases ports
                RNS.Transport.exithandler()
                logger.debug("RNS Transport shut down")
            except Exception as e:
                logger.debug(f"Error shutting down RNS Transport: {e}")

        self._lxmf_router = None
        self._lxmf_source = None
        self._identity = None
        self._reticulum = None
        self._connected_rns = False
