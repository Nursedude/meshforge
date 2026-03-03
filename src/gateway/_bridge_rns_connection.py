"""
RNS connection management for the gateway bridge.

Extracted from rns_bridge.py for CLAUDE.md #6 compliance (<1,500 lines).
Contains RNS/LXMF initialization, connection, and receive handlers.
"""

import logging
import os
import signal as _signal_mod
import threading
from contextlib import contextmanager

from utils.paths import get_real_user_home, ReticulumPaths
from utils.service_check import check_service
from utils.config_drift import detect_rnsd_config_drift, get_rnsd_effective_config_dir


def _get_rns_flags():
    """Get RNS/LXMF module flags from the parent rns_bridge module.

    This ensures that test patches on gateway.rns_bridge._HAS_RNS etc.
    are visible to functions in this helper module.
    """
    import gateway.rns_bridge as rb
    return rb._RNS_mod, rb._HAS_RNS, rb._LXMF_mod, rb._HAS_LXMF

logger = logging.getLogger(__name__)


@contextmanager
def suppress_signal_in_thread():
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


def init_rns_main_thread(bridge):
    """Pre-initialize RNS from the main thread.

    RNS.Reticulum() registers signal handlers that only work in the
    main thread. If we defer to the background _rns_loop thread,
    initialization fails with 'signal only works in main thread'.

    When rnsd is running, we connect as a client to its shared instance.

    POLICY: Diagnose, don't fix. This method NEVER restarts services
    or modifies configs. It logs issues and lets the user fix them.
    """
    if threading.current_thread() is not threading.main_thread():
        logger.warning("RNS pre-init skipped (not main thread)")
        return

    _RNS_mod, _HAS_RNS, _LXMF_mod, _HAS_LXMF = _get_rns_flags()

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
    config_dir = bridge.config.rns.config_dir or None
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
            bridge._rns_via_rnsd = True

        bridge._reticulum = RNS.Reticulum(configdir=config_dir)
        bridge._rns_pre_initialized = True
        logger.info("RNS pre-initialized from main thread")
    except Exception as e:
        err_msg = str(e).lower()
        if "reinitialise" in err_msg or "already running" in err_msg:
            bridge._rns_pre_initialized = True
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


def connect_rns(bridge):
    """Initialize RNS and LXMF.

    If RNS was pre-initialized from the main thread (via init_rns_main_thread),
    skips Reticulum initialization and proceeds directly to LXMF setup.
    Otherwise falls back to initialization here (background thread).

    POLICY: Diagnose, don't fix. Never restart services or modify configs.
    """
    _RNS_mod, _HAS_RNS, _LXMF_mod, _HAS_LXMF = _get_rns_flags()

    if not (_HAS_RNS and _HAS_LXMF):
        logger.warning("RNS/LXMF library not installed - bridge cannot connect")
        bridge._connected_rns = False
        bridge._rns_init_failed_permanently = True
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
    with suppress_signal_in_thread():
        try:
            if bridge._rns_pre_initialized:
                logger.info("RNS pre-initialized, proceeding to LXMF setup")
            else:
                # Fallback: init RNS from background thread.
                # Works when rnsd is running (client mode, no signal handlers).
                config_dir = bridge.config.rns.config_dir or None
                if not config_dir:
                    try:
                        effective = get_rnsd_effective_config_dir()
                        config_dir = str(effective)
                    except Exception:
                        pass  # Use RNS default resolution

                try:
                    bridge._reticulum = RNS.Reticulum(configdir=config_dir)
                except Exception as e:
                    err_msg = str(e).lower()
                    if "reinitialise" in err_msg or "already running" in err_msg:
                        logger.info("RNS already initialized, proceeding to LXMF")
                    elif "signal only works in main thread" in err_msg:
                        logger.warning("RNS needs main thread init (no rnsd running?)")
                        bridge._rns_init_failed_permanently = True
                        bridge._connected_rns = False
                        return
                    elif hasattr(e, 'errno') and getattr(e, 'errno', None) == 98:
                        logger.warning(f"RNS port conflict: {e} (will retry)")
                        bridge._connected_rns = False
                        return
                    else:
                        raise

            # Set up LXMF messaging on top of the RNS instance
            setup_lxmf(bridge, RNS, LXMF)

        except Exception as e:
            logger.error(f"Failed to connect to RNS: {e}")
            try:
                from utils.gateway_diagnostic import (
                    diagnose_rnsd_connection, find_rns_processes
                )
                diagnose_rnsd_connection(find_rns_processes(), error=e)
            except Exception:
                pass  # diagnostic failure should never block bridge
            bridge._connected_rns = False


def setup_lxmf(bridge, RNS, LXMF):
    """Set up LXMF identity, router, and announce handler.

    Called after RNS is initialized (either pre-init or fallback).
    Separated from connect_rns to keep the method focused and
    allow LXMF setup to be retried independently.
    """
    # Create or load identity
    identity_path = get_real_user_home() / ".config" / "meshforge" / "gateway_identity"
    if identity_path.exists():
        bridge._identity = RNS.Identity.from_file(str(identity_path))
    else:
        bridge._identity = RNS.Identity()
        identity_path.parent.mkdir(parents=True, exist_ok=True)
        bridge._identity.to_file(str(identity_path))

    # Create LXMF router
    storage_path = get_real_user_home() / ".config" / "meshforge" / "lxmf_storage"
    storage_path.mkdir(parents=True, exist_ok=True)
    bridge._lxmf_router = LXMF.LXMRouter(storagepath=str(storage_path))

    # Register delivery callback
    bridge._lxmf_router.register_delivery_callback(bridge._on_lxmf_receive)

    # Create source identity
    bridge._lxmf_source = bridge._lxmf_router.register_delivery_identity(
        bridge._identity,
        display_name="MeshForge Gateway"
    )

    # Announce presence
    bridge._lxmf_router.announce(bridge._lxmf_source.hash)

    # Register announce handler for node discovery
    class AnnounceHandler:
        def __init__(self, bridge_ref):
            self.aspect_filter = "lxmf.delivery"
            self.bridge = bridge_ref

        def received_announce(self, dest_hash, announced_identity, app_data):
            self.bridge._on_rns_announce(dest_hash, announced_identity, app_data)

    RNS.Transport.register_announce_handler(AnnounceHandler(bridge))

    bridge._connected_rns = True
    logger.info("Connected to RNS (LXMF ready)")
    bridge._notify_status("rns_connected")


def disconnect_rns(bridge):
    """Disconnect from RNS and release ports."""
    # Properly shut down RNS to release ports
    if bridge._reticulum:
        try:
            import RNS
            # RNS.Transport.exithandler() closes all interfaces and releases ports
            RNS.Transport.exithandler()
            logger.debug("RNS Transport shut down")
        except Exception as e:
            logger.debug(f"Error shutting down RNS Transport: {e}")

    bridge._lxmf_router = None
    bridge._lxmf_source = None
    bridge._identity = None
    bridge._reticulum = None
    bridge._connected_rns = False


def handle_lxmf_receive(bridge, message):
    """Handle incoming LXMF message."""
    from .node_tracker import UnifiedNode
    from .rns_bridge import BridgedMessage
    from queue import Full

    try:
        # Import optional sniffer
        try:
            from monitoring.rns_sniffer import (
                get_rns_sniffer, RNSPacketInfo, RNSPacketType
            )
            has_sniffer = True
        except ImportError:
            has_sniffer = False

        # Update node info
        source_hash = message.source_hash
        node = UnifiedNode.from_rns(source_hash)
        bridge.node_tracker.add_node(node)

        # Capture LXMF message for traffic inspection
        if has_sniffer:
            try:
                sniffer = get_rns_sniffer()
                if sniffer and sniffer._running:
                    content_bytes = message.content.encode('utf-8') if message.content else b''
                    packet_info = RNSPacketInfo(
                        packet_type=RNSPacketType.DATA,
                        source_hash=source_hash,
                        direction="inbound",
                        payload=content_bytes,
                        payload_size=len(content_bytes),
                        announce_aspect="lxmf.delivery",
                    )
                    sniffer._store_packet(packet_info)
            except Exception as e:
                logger.debug(f"RNS sniffer LXMF capture error: {e}")

        msg = BridgedMessage(
            source_network="rns",
            source_id=source_hash.hex(),
            destination_id=None,
            content=message.content,
            title=message.title,
            metadata={
                'lxmf_stamp': message.stamp,
            }
        )

        # Store incoming message for UI/history
        try:
            from commands import messaging
            content = message.content
            if message.title:
                content = f"[{message.title}] {content}"
            messaging.store_incoming(
                from_id=source_hash.hex(),
                content=content,
                network="rns",
                to_id=None,
            )
        except Exception as e:
            logger.debug(f"Could not store incoming RNS message: {e}")

        # Queue for bridging if enabled (non-blocking to prevent deadlock)
        if bridge._router.should_bridge(msg):
            try:
                bridge._rns_to_mesh_queue.put_nowait(msg)
            except Full:
                logger.warning("RNS→Mesh queue full, dropping message")
                with bridge._stats_lock:
                    bridge.stats['errors'] += 1

        # Notify callbacks
        bridge._notify_message(msg)

    except Exception as e:
        logger.error(f"Error processing LXMF message: {e}")


def handle_rns_announce(bridge, dest_hash, announced_identity, app_data):
    """Handle RNS announce for node discovery."""
    from .node_tracker import UnifiedNode

    try:
        # Import optional sniffer
        try:
            from monitoring.rns_sniffer import (
                get_rns_sniffer, RNSPacketInfo, RNSPacketType
            )
            has_sniffer = True
        except ImportError:
            has_sniffer = False

        # Capture announce packet for traffic inspection
        if has_sniffer:
            try:
                import RNS
                sniffer = get_rns_sniffer()
                if sniffer and sniffer._running:
                    packet_info = RNSPacketInfo(
                        packet_type=RNSPacketType.ANNOUNCE,
                        destination_hash=dest_hash,
                        direction="inbound",
                        announce_app_data=app_data,
                        announce_aspect="lxmf.delivery",
                    )
                    if announced_identity:
                        try:
                            packet_info.source_hash = announced_identity.hash
                            packet_info.announce_identity = announced_identity.hash
                        except Exception:
                            pass
                    try:
                        if RNS.Transport.has_path(dest_hash):
                            hops = RNS.Transport.hops_to(dest_hash)
                            packet_info.hops = hops if hops is not None else 0
                    except Exception:
                        pass
                    sniffer._store_packet(packet_info)
            except Exception as e:
                logger.debug(f"RNS sniffer capture error: {e}")

        node = UnifiedNode.from_rns(dest_hash, app_data=app_data)
        bridge.node_tracker.add_node(node)
        logger.debug(f"Discovered RNS node: {dest_hash.hex()[:8]}")
    except Exception as e:
        logger.error(f"Error processing RNS announce: {e}")
