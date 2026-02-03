#!/usr/bin/env python3
"""
Test script for meshtasticd multi-consumer architecture.

Tests both data paths:
  1. TCP:4403 → Gateway Bridge → RNS transport
  2. MQTT → mosquitto → MeshForge MQTT Subscriber

Run on Pi where meshtasticd and mosquitto are running:
  python3 scripts/test_meshtasticd_architecture.py

Requirements:
  - meshtasticd running (systemctl status meshtasticd)
  - mosquitto running (systemctl status mosquitto)
  - MQTT configured in meshtasticd (mqtt.enabled true)
"""

import socket
import subprocess
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

# ANSI colors
GREEN = '\033[0;32m'
RED = '\033[0;31m'
YELLOW = '\033[0;33m'
BLUE = '\033[0;34m'
RESET = '\033[0m'


def check_mark(success: bool) -> str:
    return f"{GREEN}✓{RESET}" if success else f"{RED}✗{RESET}"


def test_service_running(service_name: str) -> bool:
    """Check if a systemd service is running."""
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', service_name],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() == 'active'
    except Exception:
        return False


def test_port_open(host: str, port: int) -> bool:
    """Check if a TCP port is open."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            result = s.connect_ex((host, port))
            return result == 0
    except Exception:
        return False


def test_mqtt_topics() -> tuple[bool, list[str]]:
    """Subscribe to MQTT briefly and check for messages.

    Returns (success, topics_seen).
    """
    topics = []
    try:
        result = subprocess.run(
            ['timeout', '3', 'mosquitto_sub', '-h', 'localhost', '-t', 'msh/#', '-v', '-C', '5'],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout:
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split(' ', 1)
                    if parts:
                        topics.append(parts[0])
        return len(topics) > 0, topics
    except subprocess.TimeoutExpired:
        return False, []
    except Exception as e:
        return False, []


def test_mqtt_subscriber_import() -> bool:
    """Test that MQTT subscriber module can be imported."""
    try:
        from monitoring.mqtt_subscriber import MQTTNodelessSubscriber, create_local_subscriber
        return True
    except ImportError:
        return False


def test_mqtt_subscriber_connect() -> tuple[bool, str]:
    """Test MQTT subscriber connection to local broker."""
    try:
        from monitoring.mqtt_subscriber import create_local_subscriber

        subscriber = create_local_subscriber()
        success = subscriber.start()

        # Wait for connection
        time.sleep(2)

        connected = subscriber.is_connected()
        stats = subscriber.get_stats()

        subscriber.stop()

        return connected, f"nodes={stats.get('node_count', 0)}, msgs={stats.get('messages_received', 0)}"
    except Exception as e:
        return False, str(e)


def test_gateway_bridge_import() -> bool:
    """Test that Gateway Bridge can be imported."""
    try:
        from gateway.rns_bridge import RNSMeshtasticBridge
        return True
    except ImportError:
        return False


def test_websocket_bridge_import() -> bool:
    """Test that MQTT→WebSocket bridge can be imported."""
    try:
        from utils.mqtt_websocket_bridge import MQTTWebSocketBridge, is_bridge_available
        return is_bridge_available()
    except ImportError:
        return False


def main():
    print(f"\n{BLUE}═══════════════════════════════════════════════════════════════{RESET}")
    print(f"{BLUE}    MeshForge meshtasticd Architecture Test{RESET}")
    print(f"{BLUE}═══════════════════════════════════════════════════════════════{RESET}\n")

    all_passed = True

    # =========================================================================
    # Section 1: Services
    # =========================================================================
    print(f"{YELLOW}[1] Service Status{RESET}")
    print("-" * 40)

    meshtasticd_running = test_service_running('meshtasticd')
    print(f"  {check_mark(meshtasticd_running)} meshtasticd service")
    if not meshtasticd_running:
        all_passed = False

    mosquitto_running = test_service_running('mosquitto')
    print(f"  {check_mark(mosquitto_running)} mosquitto service")
    if not mosquitto_running:
        all_passed = False

    print()

    # =========================================================================
    # Section 2: Port Connectivity
    # =========================================================================
    print(f"{YELLOW}[2] Port Connectivity{RESET}")
    print("-" * 40)

    tcp_4403 = test_port_open('localhost', 4403)
    print(f"  {check_mark(tcp_4403)} TCP:4403 (meshtasticd)")

    mqtt_1883 = test_port_open('localhost', 1883)
    print(f"  {check_mark(mqtt_1883)} TCP:1883 (mosquitto)")

    print()

    # =========================================================================
    # Section 3: MQTT Topic Activity
    # =========================================================================
    print(f"{YELLOW}[3] MQTT Topic Activity{RESET}")
    print("-" * 40)

    if mosquitto_running:
        has_messages, topics = test_mqtt_topics()
        print(f"  {check_mark(has_messages)} Messages flowing on msh/#")
        if topics:
            unique_topics = list(set(topics))[:3]
            for topic in unique_topics:
                print(f"      └─ {topic}")
        else:
            print(f"      └─ (no messages in 3s - normal if mesh is quiet)")
    else:
        print(f"  {check_mark(False)} MQTT topics (mosquitto not running)")

    print()

    # =========================================================================
    # Section 4: MeshForge Module Imports
    # =========================================================================
    print(f"{YELLOW}[4] MeshForge Modules{RESET}")
    print("-" * 40)

    mqtt_import = test_mqtt_subscriber_import()
    print(f"  {check_mark(mqtt_import)} MQTT Subscriber (monitoring.mqtt_subscriber)")

    bridge_import = test_gateway_bridge_import()
    print(f"  {check_mark(bridge_import)} Gateway Bridge (gateway.rns_bridge)")

    ws_bridge_import = test_websocket_bridge_import()
    print(f"  {check_mark(ws_bridge_import)} WebSocket Bridge (utils.mqtt_websocket_bridge)")

    print()

    # =========================================================================
    # Section 5: MQTT Subscriber Connection Test
    # =========================================================================
    print(f"{YELLOW}[5] MQTT Subscriber Connection{RESET}")
    print("-" * 40)

    if mqtt_import and mqtt_1883:
        connected, info = test_mqtt_subscriber_connect()
        print(f"  {check_mark(connected)} Local broker connection")
        print(f"      └─ {info}")
    else:
        print(f"  {check_mark(False)} Skipped (missing dependencies)")

    print()

    # =========================================================================
    # Summary
    # =========================================================================
    print(f"{BLUE}═══════════════════════════════════════════════════════════════{RESET}")

    if meshtasticd_running and mosquitto_running and mqtt_import:
        print(f"\n{GREEN}Architecture Status: READY{RESET}")
        ws_note = "  • WebSocket Bridge: MQTT Monitor → WebSocket Bridge → Enable\n" if ws_bridge_import else ""
        print(f"""
Both data paths are available:
  • TCP:4403 → Gateway Bridge (exclusive, one client)
  • MQTT → mosquitto → multiple consumers

TUI Quick Start:
  • MQTT Monitor: Mesh Networks → MQTT Monitor → Configure → Use Local Broker
  • Gateway Bridge: Mesh Networks → Gateway Bridge
{ws_note}""")
    else:
        print(f"\n{YELLOW}Architecture Status: PARTIAL{RESET}")
        print("\nMissing components:")
        if not meshtasticd_running:
            print("  • meshtasticd: sudo systemctl start meshtasticd")
        if not mosquitto_running:
            print("  • mosquitto: sudo systemctl start mosquitto")
        if not mqtt_import:
            print("  • paho-mqtt: pip install paho-mqtt")

    print()
    return 0 if all_passed else 1


if __name__ == '__main__':
    sys.exit(main())
