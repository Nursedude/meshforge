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

from gateway import RNSMeshtasticBridge, GatewayConfig

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('gateway.cli')


def print_status(status: dict):
    """Print bridge status."""
    running = status.get('running', False)
    mesh = "connected" if status.get('meshtastic_connected') else "disconnected"
    rns = "connected" if status.get('rns_connected') else "disconnected"

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

    print(f"{'='*50}\n")


def on_message(msg):
    """Callback for bridged messages."""
    source = msg.source_network
    dest = msg.destination_id or "broadcast"
    content = msg.content or ""
    preview = content[:50] + "..." if len(content) > 50 else content
    logger.info(f"Message bridged: {source} -> {dest}: {preview}")


def main():
    """Main entry point."""
    print("\n" + "="*50)
    print("  MeshForge Gateway Bridge")
    print("  RNS <-> Meshtastic Message Bridge")
    print("="*50)

    # Load config
    try:
        config = GatewayConfig.load()
        print(f"\nConfig loaded from: {GatewayConfig.get_config_path()}")
    except Exception as e:
        print(f"\nWarning: Could not load config, using defaults: {e}")
        config = None

    # Create bridge
    bridge = RNSMeshtasticBridge(config)

    # Register message callback
    bridge.register_message_callback(on_message)

    # Handle Ctrl+C
    running = True

    def signal_handler(sig, frame):
        nonlocal running
        print("\n\nShutting down gateway...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start bridge
    print("\nStarting gateway bridge...")
    bridge_started = False
    try:
        success = bridge.start()
        if not success:
            print("Failed to start gateway bridge")
            print("Check that meshtasticd and rnsd are running")
            sys.exit(1)

        bridge_started = True
        print("Gateway started successfully!")
        print("Press Ctrl+C to stop\n")

        # Print initial status
        print_status(bridge.get_status())

        # Main loop - print status every 30 seconds
        last_status = time.time()
        while running:
            time.sleep(1)

            # Print status periodically
            if time.time() - last_status > 30:
                print_status(bridge.get_status())
                last_status = time.time()

    except Exception as e:
        logger.error(f"Bridge error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # Only stop if we successfully started
        if bridge_started:
            print("Stopping gateway...")
            bridge.stop()
            print("Gateway stopped.")
        else:
            print("Gateway was not started.")


if __name__ == '__main__':
    main()
