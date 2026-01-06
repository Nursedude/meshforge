#!/usr/bin/env python3
"""
Meshtastic Node Monitor - Standalone Entry Point

A lightweight, sudo-free monitoring tool for Meshtastic nodes.
Connects to meshtasticd via TCP and displays real-time node information.

Usage:
    python3 -m src.monitor [options]

Options:
    --host HOST     meshtasticd hostname (default: localhost)
    --port PORT     meshtasticd port (default: 4403)
    --json          Output as JSON
    --watch         Continuous monitoring mode
    --interval N    Update interval in seconds (default: 5)

Examples:
    # Quick node list
    python3 -m src.monitor

    # Continuous monitoring
    python3 -m src.monitor --watch

    # JSON output for scripting
    python3 -m src.monitor --json

    # Connect to remote node
    python3 -m src.monitor --host 192.168.1.100
"""

import argparse
import json
import os
import sys
import time
import signal
from datetime import datetime
from pathlib import Path
from typing import Optional

# Import centralized path utility for sudo compatibility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

# Config file location
CONFIG_DIR = get_real_user_home() / '.config' / 'meshtastic-monitor'
CONFIG_FILE = CONFIG_DIR / 'config.json'

def load_config() -> dict:
    """Load saved configuration"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(config: dict):
    """Save configuration"""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Warning: Could not save config: {e}")

def setup_interactive() -> dict:
    """Interactive setup for host configuration"""
    print("\n=== Meshtastic Monitor Setup ===\n")

    config = load_config()
    current_host = config.get('host', 'localhost')
    current_port = config.get('port', 4403)

    print(f"Current host: {current_host}")
    print(f"Current port: {current_port}\n")

    # Get new host
    host_input = input(f"Enter meshtasticd host [{current_host}]: ").strip()
    host = host_input if host_input else current_host

    # Get new port
    port_input = input(f"Enter port [{current_port}]: ").strip()
    try:
        port = int(port_input) if port_input else current_port
    except ValueError:
        print("Invalid port, using default")
        port = current_port

    # Save config
    config['host'] = host
    config['port'] = port
    save_config(config)

    print(f"\nConfiguration saved to {CONFIG_FILE}")
    print(f"  Host: {host}")
    print(f"  Port: {port}\n")

    return config

# Handle graceful shutdown
_running = True

def signal_handler(sig, frame):
    global _running
    _running = False
    print("\nShutting down...")

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def format_time_ago(dt: Optional[datetime]) -> str:
    """Format datetime as 'X ago' string"""
    if not dt:
        return "never"

    delta = datetime.now() - dt
    seconds = delta.total_seconds()

    if seconds < 60:
        return f"{int(seconds)}s ago"
    elif seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    elif seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    else:
        return f"{int(seconds / 86400)}d ago"


def print_banner():
    """Print application banner"""
    print("""
╔═══════════════════════════════════════════════════════════╗
║         Meshtastic Node Monitor (No Sudo Required)        ║
╚═══════════════════════════════════════════════════════════╝
""")


def print_node_table(nodes: list, my_node_id: Optional[str] = None):
    """Print nodes in a formatted table"""
    if not nodes:
        print("  No nodes found")
        return

    # Header
    print(f"  {'ID':<12} {'Name':<16} {'Short':<6} {'Hardware':<18} {'Battery':<8} {'Last Heard':<12}")
    print(f"  {'-'*12} {'-'*16} {'-'*6} {'-'*18} {'-'*8} {'-'*12}")

    for node in sorted(nodes, key=lambda n: n.last_heard or datetime.min, reverse=True):
        node_id = node.node_id[-8:] if len(node.node_id) > 8 else node.node_id
        is_me = " *" if node.node_id == my_node_id else ""

        battery = f"{node.metrics.battery_level}%" if node.metrics and node.metrics.battery_level else "--"
        last_heard = format_time_ago(node.last_heard)

        print(f"  {node_id:<12} {node.long_name[:16]:<16} {node.short_name[:6]:<6} "
              f"{node.hardware_model[:18]:<18} {battery:<8} {last_heard}{is_me}")


def run_monitor(host: str, port: int, json_output: bool, watch: bool, interval: int):
    """Main monitoring function"""
    global _running

    try:
        from src.monitoring import NodeMonitor
    except ImportError:
        try:
            # Handle running from different directories
            from monitoring import NodeMonitor
        except ImportError:
            print("Error: Could not import NodeMonitor. Make sure you're running from the project root.")
            print("Usage: python3 -m src.monitor")
            sys.exit(1)

    if not json_output:
        print_banner()
        print(f"Connecting to {host}:{port}...")

    monitor = NodeMonitor(host=host, port=port)

    # Set up callbacks for watch mode
    def on_node_update(node):
        if not json_output:
            print(f"  [UPDATE] {node.short_name} ({node.node_id[-8:]})")

    def on_node_added(node):
        if not json_output:
            print(f"  [NEW] {node.short_name} ({node.node_id[-8:]})")

    if watch:
        monitor.on_node_update = on_node_update
        monitor.on_node_added = on_node_added

    if not monitor.connect(timeout=15):
        if json_output:
            print(json.dumps({"error": "Connection failed", "host": host, "port": port}))
        else:
            print(f"\nError: Could not connect to meshtasticd at {host}:{port}")
            print("\nTroubleshooting:")
            print("  1. Make sure meshtasticd is running: systemctl status meshtasticd")
            print("  2. Check if TCP is enabled in meshtasticd config")
            print("  3. Verify the host and port are correct")
        sys.exit(1)

    try:
        if json_output:
            # JSON output mode
            if watch:
                while _running:
                    data = monitor.to_dict()
                    data['timestamp'] = datetime.now().isoformat()
                    print(json.dumps(data))
                    sys.stdout.flush()
                    time.sleep(interval)
            else:
                print(json.dumps(monitor.to_dict(), indent=2))
        else:
            # Human-readable output
            print(f"\nConnected! My node: {monitor.my_node_id}")
            print(f"Node count: {monitor.get_node_count()}\n")

            if watch:
                print("Watching for updates (Ctrl+C to stop)...\n")
                while _running:
                    print(f"\n--- {datetime.now().strftime('%H:%M:%S')} ---")
                    print_node_table(monitor.get_nodes(), monitor.my_node_id)
                    time.sleep(interval)
            else:
                print_node_table(monitor.get_nodes(), monitor.my_node_id)

                # Show my node details
                my_node = monitor.get_my_node()
                if my_node:
                    print(f"\n  * = This node ({my_node.long_name})")
                    if my_node.position and my_node.position.latitude:
                        print(f"  Position: {my_node.position.latitude:.6f}, {my_node.position.longitude:.6f}")

    finally:
        monitor.disconnect()
        if not json_output:
            print("\nDisconnected.")


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Meshtastic Node Monitor - Lightweight, sudo-free node monitoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 -m src.monitor              # Quick node list (uses saved config)
  python3 -m src.monitor --setup      # Configure host interactively
  python3 -m src.monitor --watch      # Continuous monitoring
  python3 -m src.monitor --json       # JSON output
  python3 -m src.monitor --host pi4   # Connect to specific host
        """
    )

    parser.add_argument('--host',
                        help='meshtasticd hostname (default: from config or localhost)')
    parser.add_argument('--port', type=int,
                        help='meshtasticd port (default: from config or 4403)')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')
    parser.add_argument('--watch', '-w', action='store_true',
                        help='Continuous monitoring mode')
    parser.add_argument('--interval', '-i', type=int, default=5,
                        help='Update interval in seconds (default: 5)')
    parser.add_argument('--setup', '-s', action='store_true',
                        help='Interactive setup to configure host')
    parser.add_argument('--show-config', action='store_true',
                        help='Show current configuration')

    args = parser.parse_args()

    # Handle --setup
    if args.setup:
        config = setup_interactive()
        response = input("Connect now? [Y/n]: ").strip().lower()
        if response == 'n':
            return
        host = config['host']
        port = config['port']
    elif args.show_config:
        config = load_config()
        if config:
            print(f"Config file: {CONFIG_FILE}")
            print(f"  Host: {config.get('host', 'localhost')}")
            print(f"  Port: {config.get('port', 4403)}")
        else:
            print(f"No config found. Run with --setup to configure.")
        return
    else:
        # Load from config or use defaults
        config = load_config()
        host = args.host or config.get('host', 'localhost')
        port = args.port or config.get('port', 4403)

    run_monitor(
        host=host,
        port=port,
        json_output=args.json,
        watch=args.watch,
        interval=args.interval
    )


if __name__ == "__main__":
    main()
