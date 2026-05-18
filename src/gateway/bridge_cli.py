#!/usr/bin/env python3
"""
Gateway Bridge CLI

Simple command-line interface to run the RNS-Meshtastic bridge.
Used by launcher_tui.py and can be run directly.
"""

import sys
import signal
import logging
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from gateway import (
    RNSMeshtasticBridge,
    GatewayConfig,
    MeshtasticPresetBridge,
    create_mesh_bridge,
    RNSMeshtasticTransport,
    create_rns_transport,
)
from utils.service_check import check_service, check_port
from utils.sandbox_check import assert_writable_or_exit

# Setup logging — use canonical logging_config (avoids basicConfig conflicts)
from utils.logging_config import setup_logging, get_logger
setup_logging(level=logging.INFO)
logger = get_logger('gateway.cli')

# Metrics server instance (auto-started with gateway)
_metrics_server = None


def migrate_legacy_bridge_mode(config: GatewayConfig) -> list:
    """Reconcile a legacy bridge_mode-as-gate config to the composable-bridges model.

    Pre-refactor, ``bridge_mode`` was a single-choice selector that the CLI
    used to pick which bridge to instantiate. The post-refactor model gates
    each bridge on its own section (``mesh_bridge.enabled`` etc.) plus
    ``rns_bridge_enabled`` for the default RNS<->Meshtastic bridge.

    Existing deployments have e.g. ``bridge_mode="mesh_bridge"`` with
    ``mesh_bridge.enabled=False`` — previously the CLI auto-corrected at
    runtime. This helper migrates forward: if the user's stated mode
    implies a section they haven't explicitly enabled, enable it in-place
    and return a human-readable warning so they see the migration happened.

    Returns a list of warning strings (empty if nothing to migrate).
    """
    warnings_out = []
    mode = (config.bridge_mode or "").lower()

    # Legacy "mesh_bridge" mode: the user meant to run the Meshtastic preset
    # bridge. If they haven't flipped mesh_bridge.enabled, do it for them.
    if mode == "mesh_bridge" and not config.mesh_bridge.enabled:
        config.mesh_bridge.enabled = True
        warnings_out.append(
            "bridge_mode='mesh_bridge' but mesh_bridge.enabled=false — "
            "auto-enabled. (Composable-bridges migration: prefer setting "
            "mesh_bridge.enabled=true explicitly in gateway.json.)"
        )

    # Legacy "rns_transport" mode: same pattern.
    if mode == "rns_transport" and not config.rns_transport.enabled:
        config.rns_transport.enabled = True
        warnings_out.append(
            "bridge_mode='rns_transport' but rns_transport.enabled=false — "
            "auto-enabled. (Set rns_transport.enabled=true explicitly in "
            "gateway.json to silence this warning.)"
        )

    # Pure mesh_bridge deployments that want to run WITHOUT the RNS bridge
    # have to opt out explicitly via rns_bridge_enabled=false. We don't
    # infer that from bridge_mode because "mesh_bridge" alone doesn't say
    # whether the user also wants RNS — keep both enabled by default.

    return warnings_out


def resolve_bridges(config: GatewayConfig) -> list:
    """Return the ordered list of bridges this gateway should run.

    Each entry is a dict ``{name, label, builder}`` where ``builder`` is a
    zero-arg callable returning a started-ready bridge instance. The order
    matters for startup (we start earlier entries first) and for shutdown
    (we stop in reverse).
    """
    bridges = []

    if config.rns_bridge_enabled:
        bridges.append({
            "name": "rns_bridge",
            "label": "RNS <-> Meshtastic Message Bridge",
            "builder": lambda cfg=config: RNSMeshtasticBridge(cfg),
        })

    if config.mesh_bridge.enabled:
        bridges.append({
            "name": "mesh_bridge",
            "label": "Meshtastic Preset Bridge (cross-preset)",
            "builder": lambda cfg=config: create_mesh_bridge(cfg),
        })

    if config.rns_transport.enabled:
        bridges.append({
            "name": "rns_transport",
            "label": "RNS Over Meshtastic Transport",
            "builder": lambda cfg=config: create_rns_transport(cfg.rns_transport),
        })

    return bridges


def validate_bridge_conflicts(config: GatewayConfig, bridges: list) -> list:
    """Return list of human-readable config-error strings — empty list means OK.

    Design goal (per operator request): the gateway refuses to start if the
    config is internally inconsistent. No silent fallback, no "auto-correct
    to message_bridge" surprises — the operator either fixes the config or
    the service exits.
    """
    errs = []

    if not bridges:
        errs.append(
            "No bridges enabled. At least one of: rns_bridge_enabled=true, "
            "mesh_bridge.enabled=true, or rns_transport.enabled=true must "
            "be set in gateway.json."
        )
        return errs  # nothing else to validate

    # Self-conflict inside mesh_bridge: two serial interfaces on the same
    # device path would try to open /dev/ttyUSB0 twice and the second fails.
    if config.mesh_bridge.enabled:
        p = config.mesh_bridge.primary
        s = config.mesh_bridge.secondary
        p_ct = (p.connection_type or "").lower()
        s_ct = (s.connection_type or "").lower()
        if (p_ct == "serial" and s_ct == "serial"
                and p.serial_device and p.serial_device == s.serial_device):
            errs.append(
                f"mesh_bridge primary and secondary both point to "
                f"serial_device={p.serial_device}. Each radio must have a "
                "distinct device path."
            )

    # mesh_bridge and rns_transport both expect to own the meshtasticd
    # radio's data path — running both concurrently is ambiguous and untested.
    if config.mesh_bridge.enabled and config.rns_transport.enabled:
        errs.append(
            "mesh_bridge.enabled and rns_transport.enabled are both true. "
            "These two bridges both claim the Meshtastic radio's data "
            "path and cannot run concurrently — enable at most one."
        )

    # Secondary serial device that does not exist: caught here as a hard
    # refusal rather than at runtime.
    if config.mesh_bridge.enabled:
        import os
        s = config.mesh_bridge.secondary
        s_ct = (s.connection_type or "").lower()
        if s_ct == "serial" and s.serial_device and not os.path.exists(s.serial_device):
            errs.append(
                f"mesh_bridge.secondary.serial_device={s.serial_device} does "
                "not exist. Attach the USB Meshtastic device or fix the path."
            )

    return errs


def preflight_checks(config: GatewayConfig) -> bool:
    """
    Run pre-flight service checks before starting the bridge.
    Returns True if all required services are available.
    """
    print("\n--- Pre-flight Checks ---")
    all_ok = True

    # Check Meshtastic daemon
    mesh_host = config.meshtastic.host if config else "localhost"
    mesh_port = config.meshtastic.port if config else 4403

    print(f"Checking meshtasticd ({mesh_host}:{mesh_port})...", end=" ")
    if check_port(mesh_port, mesh_host, timeout=2.0):
        print("✓ Available")
    else:
        print("✗ NOT AVAILABLE")
        status = check_service('meshtasticd')
        print(f"  {status.message}")
        print(f"  Fix: {status.fix_hint}")
        all_ok = False

    # Check RNS daemon (if RNS mode enabled)
    bridge_mode = config.bridge_mode if config else "message_bridge"
    if bridge_mode in ("message_bridge", "rns_transport"):
        print("Checking rnsd...", end=" ")
        rns_status = check_service('rnsd')
        if rns_status.available:
            print("✓ Available")
        else:
            print("✗ NOT AVAILABLE")
            print(f"  {rns_status.message}")
            print(f"  Fix: {rns_status.fix_hint}")
            all_ok = False

    # Check second Meshtastic if mesh_bridge mode
    if bridge_mode == "mesh_bridge" and config and config.mesh_bridge.enabled:
        sec = config.mesh_bridge.secondary
        if (sec.connection_type or "").lower() == "serial":
            device = sec.serial_device or "(auto-detect)"
            print(f"Checking secondary Meshtastic (serial {device})...", end=" ")
            import os
            if not sec.serial_device or os.path.exists(sec.serial_device):
                print("✓ Available")
            else:
                print("✗ NOT AVAILABLE")
                print(f"  Device path missing: {sec.serial_device}")
                print(f"  Fix: attach USB Meshtastic device; confirm /dev path")
                all_ok = False
        else:
            print(f"Checking secondary meshtasticd ({sec.host}:{sec.port})...", end=" ")
            if check_port(sec.port, sec.host, timeout=2.0):
                print("✓ Available")
            else:
                print("✗ NOT AVAILABLE")
                print(f"  Secondary Meshtastic daemon not reachable")
                print(f"  Fix: Start second meshtasticd on port {sec.port}")
                all_ok = False

    print("-------------------------\n")
    return all_ok


def print_status(status: dict):
    """Print bridge status."""
    running = status.get('running', False)
    mesh = "connected" if status.get('meshtastic_connected') else "disconnected"
    if status.get('rns_connected'):
        rns = "connected"
    elif status.get('rns_via_rnsd'):
        rns = "via rnsd (transport handled by rnsd)"
    else:
        rns = "disconnected"

    print(f"\n{'='*50}")
    print(f"Gateway Status: {'RUNNING' if running else 'STOPPED'}")
    print(f"Meshtastic: {mesh}")
    print(f"RNS: {rns}")

    stats = status.get('statistics', {})
    if stats:
        mesh_to_rns = stats.get('messages_mesh_to_rns', 0)
        rns_to_mesh = stats.get('messages_rns_to_mesh', 0)
        print(f"Messages bridged: {mesh_to_rns + rns_to_mesh} (M->R: {mesh_to_rns}, R->M: {rns_to_mesh})")
        # Hardening D triplet — surfaces "tried but didn't deliver" so a
        # silent stall (queue full / channel deployment gap) doesn't hide
        # behind the legacy single delivered counter.
        m2r_a = stats.get('mesh_to_rns_attempted', 0)
        m2r_d = stats.get('mesh_to_rns_dropped', 0)
        r2m_a = stats.get('rns_to_mesh_attempted', 0)
        r2m_d = stats.get('rns_to_mesh_dropped', 0)
        if m2r_a or r2m_a:
            print(
                f"  attempted/delivered/dropped — "
                f"M->R: {m2r_a}/{mesh_to_rns}/{m2r_d}  "
                f"R->M: {r2m_a}/{rns_to_mesh}/{r2m_d}"
            )

    node_stats = status.get('node_stats', {})
    if node_stats:
        print(f"Nodes tracked: {node_stats.get('total', 0)}")

    print(f"{'='*50}")
    print("Press Ctrl+C to stop and return to menu\n")


def _bridge_is_ready(instance) -> bool:
    """Return True when an instance reports both meshtastic + RNS sides up.

    Bridges that don't expose a status dict with these keys (e.g. pure
    MeshtasticPresetBridge without an RNS side) count as "ready" as soon
    as their meshtastic flag flips, so startup doesn't wait for an RNS
    connection that bridge doesn't need.
    """
    if not hasattr(instance, "get_status"):
        return True
    status = instance.get_status() or {}
    mesh_ok = status.get("meshtastic_connected")
    if "rns_connected" in status or "rns_via_rnsd" in status:
        rns_ok = status.get("rns_connected") or status.get("rns_via_rnsd")
        return bool(mesh_ok and rns_ok)
    return bool(mesh_ok)


def print_multi_status(instances):
    """Print per-bridge status block(s). Falls back to the legacy
    print_status() when only one bridge is running to keep existing
    log consumers/field-testing tooling working."""
    if len(instances) == 1:
        print_status(instances[0].get_status())
        return

    print(f"\n{'='*50}")
    print(f"Gateway Status: RUNNING ({len(instances)} bridges)")
    for inst in instances:
        status = inst.get_status() if hasattr(inst, "get_status") else {}
        label = getattr(inst, "_bridge_label", getattr(inst, "_bridge_name", "bridge"))
        print(f"  [{label}]")
        mesh = "connected" if status.get("meshtastic_connected") else "disconnected"
        if status.get("rns_connected"):
            rns = "connected"
        elif status.get("rns_via_rnsd"):
            rns = "via rnsd"
        elif "rns_connected" in status or "rns_via_rnsd" in status:
            rns = "disconnected"
        else:
            rns = "n/a"
        print(f"    Meshtastic: {mesh}   RNS: {rns}")
        stats = status.get("statistics", {}) or {}
        if stats:
            m2r = stats.get("messages_mesh_to_rns", 0)
            r2m = stats.get("messages_rns_to_mesh", 0)
            print(f"    Messages bridged: {m2r + r2m} (M->R: {m2r}, R->M: {r2m})")
            m2r_a = stats.get("mesh_to_rns_attempted", 0)
            m2r_d = stats.get("mesh_to_rns_dropped", 0)
            r2m_a = stats.get("rns_to_mesh_attempted", 0)
            r2m_d = stats.get("rns_to_mesh_dropped", 0)
            if m2r_a or r2m_a:
                print(
                    f"      att/del/drop — "
                    f"M->R: {m2r_a}/{m2r}/{m2r_d}  "
                    f"R->M: {r2m_a}/{r2m}/{r2m_d}"
                )
    print(f"{'='*50}")
    print("Press Ctrl+C to stop and return to menu\n")


def on_message(msg):
    """Callback for bridged messages."""
    source = msg.source_network
    dest = msg.destination_id or "broadcast"
    content = msg.content or ""
    preview = content[:50] + "..." if len(content) > 50 else content
    logger.info(f"Message bridged: {source} -> {dest}: {preview}")


def main():
    """Main entry point."""
    global _metrics_server

    print("\n" + "="*50)
    print("  MeshForge Gateway Bridge")
    print("="*50)

    # Sandbox preflight (Issue #58 class). When systemd's
    # ProtectHome=read-only + ReadWritePaths= drift from the data
    # directories the gateway actually writes, we want to fail at
    # startup with a precise operator-actionable error rather than
    # hours later in an LXMF callback exception. See
    # utils/sandbox_check.py and persistent_issues #58/#60.
    assert_writable_or_exit("meshforge-gateway")

    # Load config
    try:
        config = GatewayConfig.load()
        print(f"\nConfig loaded from: {GatewayConfig.get_config_path()}")
    except Exception as e:
        print(f"\nWarning: Could not load config, using defaults: {e}")
        config = GatewayConfig()  # Use default config, not None

    # Migrate legacy bridge_mode-as-gate configs to the composable-bridges
    # model in-place, announcing any rewrites so operators see them.
    for warn_msg in migrate_legacy_bridge_mode(config):
        logger.warning(warn_msg)
        print(f"\nMIGRATION: {warn_msg}")

    # Resolve which bridges to start, validate conflicts before spinning
    # up any threads. Per operator request, refuse to start on any
    # inconsistency — no silent fallback.
    bridge_specs = resolve_bridges(config)
    conflicts = validate_bridge_conflicts(config, bridge_specs)
    if conflicts:
        print("\nCONFIG ERRORS — gateway will not start:")
        for c in conflicts:
            print(f"  - {c}")
        print("\nEdit ~/.config/meshforge/gateway.json and restart.")
        sys.exit(2)

    bridge_names = [b["name"] for b in bridge_specs]
    print(f"  Bridges enabled: {', '.join(bridge_names)}")
    for spec in bridge_specs:
        print(f"    - {spec['label']}")

    # Pre-flight service checks (honors all enabled sections)
    if not preflight_checks(config):
        print("Pre-flight checks FAILED")
        print("Please start required services and try again.")
        sys.exit(1)

    # Instantiate all bridges. Short-circuit on any builder failure so we
    # don't end up with a half-built gateway where one bridge starts and
    # another crashed.
    instances = []
    for spec in bridge_specs:
        try:
            inst = spec["builder"]()
            inst._bridge_name = spec["name"]
            inst._bridge_label = spec["label"]
            instances.append(inst)
            logger.info(f"Created bridge: {spec['name']} ({spec['label']})")
        except Exception as e:
            logger.error(f"Failed to construct bridge {spec['name']}: {e}")
            print(f"\nERROR: could not construct bridge '{spec['name']}': {e}")
            sys.exit(1)

    # Register message callback on any bridge that supports it
    for inst in instances:
        if hasattr(inst, 'register_message_callback'):
            inst.register_message_callback(on_message)

    # Alias so the rest of main() (status / shutdown loops) can still
    # reference a single "bridge" when operating on the primary.
    bridge = instances[0]

    # Handle Ctrl+C
    import threading
    _stop_event = threading.Event()
    running = True

    def signal_handler(sig, frame):
        nonlocal running
        print("\n\nShutting down gateway...")
        running = False
        _stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start every enabled bridge. If any fails, unwind and exit — we never
    # leave the process running with a partial bridge set.
    print("Starting gateway bridges...")
    started = []
    try:
        for inst in instances:
            if inst.start():
                started.append(inst)
                logger.info(f"Bridge started: {inst._bridge_name}")
            else:
                logger.error(f"Bridge failed to start: {inst._bridge_name}")
                print(f"Failed to start bridge: {inst._bridge_name}")
                print("Pre-flight passed but bridge failed - check logs for details")
                # Unwind the already-started ones before exiting
                for prev in reversed(started):
                    try:
                        prev.stop()
                    except Exception:
                        pass
                sys.exit(1)

        print(f"Gateway started successfully! ({len(started)} bridge(s) running)")

        # Auto-start metrics server for Grafana integration
        try:
            from utils.metrics_export import start_metrics_server
            _metrics_server = start_metrics_server(port=9090)
            print("Metrics server started on http://localhost:9090/metrics")
            print("  Grafana JSON API: http://localhost:9090/api/json/metrics")
        except Exception as e:
            logger.debug(f"Metrics server not started: {e}")

        print("Press Ctrl+C to stop\n")

        # Wait for connections before showing initial status. We're ready
        # as soon as ANY bridge reports both its meshtastic and RNS sides
        # up — waiting for every bridge can hang startup on a slow radio.
        print("Waiting for connections...", end="", flush=True)
        for _ in range(10):
            if not running:
                break
            if any(_bridge_is_ready(inst) for inst in started):
                break
            time.sleep(1)
            print(".", end="", flush=True)
        print()

        # Print initial status for every bridge
        print_multi_status(started)

        # Main loop - print status every 30 seconds
        last_status = time.time()
        while running:
            _stop_event.wait(1)
            if _stop_event.is_set():
                break

            if time.time() - last_status > 30:
                print_multi_status(started)
                last_status = time.time()

    except Exception as e:
        logger.error(f"Bridge error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Stop metrics server if running
        if _metrics_server:
            try:
                _metrics_server.stop()
                logger.debug("Metrics server stopped")
            except Exception:
                pass
            _metrics_server = None

        # Stop all bridges that successfully started, in reverse order.
        if started:
            print("Stopping gateway bridges...")
            for inst in reversed(started):
                try:
                    inst.stop()
                    logger.info(f"Bridge stopped: {inst._bridge_name}")
                except Exception as e:
                    logger.debug(f"stop({inst._bridge_name}) error: {e}")
            print("Gateway stopped.")
        else:
            print("Gateway was not started.")


if __name__ == '__main__':
    main()
