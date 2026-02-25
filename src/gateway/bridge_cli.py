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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('gateway.cli')

# Metrics server instance (auto-started with gateway)
_metrics_server = None


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

    node_stats = status.get('node_stats', {})
    if node_stats:
        print(f"Nodes tracked: {node_stats.get('total', 0)}")

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

    # Load config
    try:
        config = GatewayConfig.load()
        print(f"\nConfig loaded from: {GatewayConfig.get_config_path()}")
    except Exception as e:
        print(f"\nWarning: Could not load config, using defaults: {e}")
        config = GatewayConfig()  # Use default config, not None

    bridge_mode = config.bridge_mode

    # Auto-fix: validate bridge_mode against available resources
    if bridge_mode == "mesh_bridge":
        if not config.mesh_bridge.enabled:
            logger.warning("bridge_mode is 'mesh_bridge' but mesh_bridge.enabled is False")
            logger.warning("Auto-correcting to 'message_bridge'")
            print("\nWARNING: bridge_mode='mesh_bridge' but mesh_bridge is not enabled.")
            print("         Falling back to 'message_bridge' mode.\n")
            bridge_mode = "message_bridge"
        else:
            sec = config.mesh_bridge.secondary
            if not check_port(sec.port, sec.host, timeout=2.0):
                logger.warning(
                    "bridge_mode is 'mesh_bridge' but secondary meshtasticd "
                    "(%s:%d) is not reachable", sec.host, sec.port
                )
                logger.warning("Auto-correcting to 'message_bridge'")
                print(f"\nWARNING: bridge_mode='mesh_bridge' but secondary meshtasticd")
                print(f"         ({sec.host}:{sec.port}) is not reachable.")
                print(f"         Falling back to 'message_bridge' mode.\n")
                bridge_mode = "message_bridge"

    mode_labels = {
        "message_bridge": "RNS <-> Meshtastic Message Bridge",
        "rns_transport": "RNS Over Meshtastic Transport",
        "mesh_bridge": "Meshtastic Preset Bridge",
    }
    print(f"  Mode: {mode_labels.get(bridge_mode, bridge_mode)}")

    # Pre-flight service checks (use resolved bridge_mode)
    # Persist the auto-corrected mode so downstream code sees the right value
    config.bridge_mode = bridge_mode
    if not preflight_checks(config):
        print("Pre-flight checks FAILED")
        print("Please start required services and try again.")
        sys.exit(1)

    # Create bridge based on resolved mode
    if bridge_mode == "mesh_bridge":
        bridge = create_mesh_bridge(config)
        logger.info("Created MeshtasticPresetBridge (mesh_bridge mode)")
    elif bridge_mode == "rns_transport":
        bridge = create_rns_transport(config.rns_transport)
        logger.info("Created RNSMeshtasticTransport (rns_transport mode)")
    else:
        bridge = RNSMeshtasticBridge(config)
        logger.info("Created RNSMeshtasticBridge (message_bridge mode)")

    # Register message callback (only for bridges that support it)
    if hasattr(bridge, 'register_message_callback'):
        bridge.register_message_callback(on_message)

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

    # Start bridge
    print("Starting gateway bridge...")
    bridge_started = False
    try:
        success = bridge.start()
        if not success:
            print("Failed to start gateway bridge")
            print("Pre-flight passed but bridge failed - check logs for details")
            sys.exit(1)

        bridge_started = True
        print("Gateway started successfully!")

        # Auto-start metrics server for Grafana integration
        try:
            from utils.metrics_export import start_metrics_server
            _metrics_server = start_metrics_server(port=9090)
            print("Metrics server started on http://localhost:9090/metrics")
            print("  Grafana JSON API: http://localhost:9090/api/json/metrics")
        except Exception as e:
            logger.debug(f"Metrics server not started: {e}")

        print("Press Ctrl+C to stop\n")

        # Wait for connections before showing initial status
        print("Waiting for connections...", end="", flush=True)
        for _ in range(10):
            if not running:
                break
            status = bridge.get_status()
            mesh_ok = status.get('meshtastic_connected')
            rns_ok = status.get('rns_connected') or status.get('rns_via_rnsd')
            if mesh_ok and rns_ok:
                break
            time.sleep(1)
            print(".", end="", flush=True)
        print()

        # Print initial status
        print_status(bridge.get_status())

        # Main loop - print status every 30 seconds
        last_status = time.time()
        while running:
            _stop_event.wait(1)
            if _stop_event.is_set():
                break

            # Print status periodically
            if time.time() - last_status > 30:
                print_status(bridge.get_status())
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

        # Only stop if we successfully started
        if bridge_started:
            print("Stopping gateway...")
            bridge.stop()
            print("Gateway stopped.")
        else:
            print("Gateway was not started.")


if __name__ == '__main__':
    main()
