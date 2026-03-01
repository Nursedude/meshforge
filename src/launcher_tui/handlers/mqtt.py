"""
MQTT Handler — MQTT monitoring control for the TUI.

Converted from mqtt_mixin.py as part of the mixin-to-registry migration.
Provides MQTT subscriber start/stop, configuration, node viewing, statistics,
telemetry polling, and WebSocket bridge toggle.

Implements LifecycleHandler for auto-start on TUI launch.

Module-level load_mqtt_config() and save_mqtt_config() are shared with
BrokerHandler for cross-handler config access.
"""

import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any

from handler_protocol import BaseHandler
from utils.safe_import import safe_import
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Try to import the MQTT subscriber
MQTTNodelessSubscriber, _HAS_MQTT = safe_import(
    'monitoring.mqtt_subscriber', 'MQTTNodelessSubscriber'
)

# Telemetry poller for silent node detection & batch polling
_get_telemetry_poller, _TelemetryPoller, _HAS_TELEMETRY_POLLER = safe_import(
    'utils.telemetry_poller', 'get_telemetry_poller', 'TelemetryPoller'
)

# Try to import the MQTT-WebSocket bridge
MQTTWebSocketBridge, is_bridge_available, _HAS_WS_BRIDGE_MOD = safe_import(
    'utils.mqtt_websocket_bridge', 'MQTTWebSocketBridge', 'is_bridge_available'
)
_HAS_WS_BRIDGE = is_bridge_available() if _HAS_WS_BRIDGE_MOD and is_bridge_available else False

# Try to import TelemetryPoller for auto-start and telemetry requests
TelemetryPoller, get_telemetry_poller, _HAS_TELEMETRY_POLLER = safe_import(
    'utils.telemetry_poller', 'TelemetryPoller', 'get_telemetry_poller'
)


# ---------------------------------------------------------------------------
# Module-level config helpers — shared with BrokerHandler
# ---------------------------------------------------------------------------

def load_mqtt_config() -> Dict[str, Any]:
    """Load MQTT configuration from file."""
    config_path = get_real_user_home() / ".config" / "meshforge" / "mqtt_nodeless.json"
    try:
        if config_path.exists():
            with open(config_path) as f:
                return json.load(f)
    except Exception as e:
        logger.debug("Error loading MQTT config: %s", e)

    return {
        'broker': 'mqtt.meshtastic.org',
        'port': 8883,
        'topic': 'msh/US/2/e/LongFast/#',
        'username': None,
        'password': None
    }


def save_mqtt_config(config: Dict[str, Any]):
    """Save MQTT configuration to file."""
    config_path = get_real_user_home() / ".config" / "meshforge" / "mqtt_nodeless.json"
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.error("Error saving MQTT config: %s", e)


class MQTTHandler(BaseHandler):
    """TUI handler for MQTT monitoring and subscriber control."""

    handler_id = "mqtt"
    menu_section = "mesh_networks"

    def __init__(self):
        super().__init__()
        self._mqtt_subscriber: Optional[Any] = None
        self._mqtt_thread: Optional[threading.Thread] = None
        self._mqtt_ws_bridge: Optional[Any] = None

    def menu_items(self):
        return [
            ("mqtt", "MQTT Monitor        Nodeless mesh observation", "mqtt"),
        ]

    def execute(self, action):
        if action == "mqtt":
            self._mqtt_menu()

    # -- Lifecycle hooks (LifecycleHandler protocol) --

    def on_startup(self):
        """Auto-start MQTT subscriber and TelemetryPoller if configured.

        Called at TUI startup via registry.startup_all().
        Silent operation — no dialogs, all errors suppressed.
        """
        if self.ctx.daemon_active:
            return

        try:
            config = load_mqtt_config()
            if not config.get('auto_start', False):
                return

            if not _HAS_MQTT:
                return

            broker = config.get('broker', 'mqtt.meshtastic.org')
            port = config.get('port', 8883)
            topic = config.get('topic', 'msh/US/2/e/LongFast/#')

            parts = topic.rstrip('#').rstrip('/').split('/')
            if len(parts) >= 4:
                channel = parts[-1] if parts[-1] else 'LongFast'
                if '/json/' in topic:
                    root_topic = '/'.join(parts[:-1]).replace('/json', '/e')
                else:
                    root_topic = '/'.join(parts[:-1])
            else:
                root_topic = 'msh/US/2/e'
                channel = 'LongFast'

            subscriber_config = {
                "broker": broker,
                "port": port,
                "username": config.get('username') or "",
                "password": config.get('password') or "",
                "root_topic": root_topic,
                "channel": channel,
                "key": "AQ==",
                "use_tls": config.get('use_tls', port == 8883),
                "auto_reconnect": True,
                "reconnect_delay": 2 if broker == 'localhost' else 5,
                "max_reconnect_delay": 30 if broker == 'localhost' else 60,
            }

            self._mqtt_subscriber = MQTTNodelessSubscriber(config=subscriber_config)
            self._mqtt_subscriber.start()
            logger.info("MQTT subscriber auto-started (broker=%s)", broker)

        except Exception as e:
            logger.debug("MQTT auto-start failed (non-fatal): %s", e)
            self._mqtt_subscriber = None

        # Auto-start TelemetryPoller if configured
        try:
            config = load_mqtt_config()
            if (config.get('auto_start', False) and
                    config.get('auto_start_telemetry', True) and
                    _HAS_TELEMETRY_POLLER and get_telemetry_poller):
                get_telemetry_poller(
                    poll_interval_minutes=config.get('telemetry_poll_minutes', 30),
                    auto_start=True
                )
        except Exception as e:
            logger.debug("TelemetryPoller auto-start failed (non-fatal): %s", e)

    def on_shutdown(self):
        """Stop MQTT subscriber and WebSocket bridge on TUI exit."""
        try:
            if self._mqtt_ws_bridge:
                self._mqtt_ws_bridge.stop()
                self._mqtt_ws_bridge = None
        except Exception as e:
            logger.debug("WebSocket bridge shutdown error: %s", e)

        try:
            if self._mqtt_subscriber:
                self._mqtt_subscriber.stop()
                self._mqtt_subscriber = None
        except Exception as e:
            logger.debug("MQTT subscriber shutdown error: %s", e)

    # -- Menu methods --

    def _mqtt_menu(self):
        """MQTT monitoring menu — nodeless mesh observation."""
        while True:
            try:
                status = self._get_mqtt_status()
            except Exception as e:
                logger.debug("MQTT status check failed: %s", e)
                status = "Unknown"
            try:
                config = load_mqtt_config()
            except Exception as e:
                logger.debug("MQTT config load failed: %s", e)
                config = {}
            broker = config.get('broker', 'mqtt.meshtastic.org')

            if broker in ("localhost", "127.0.0.1"):
                mode = "Private"
            elif broker == "mqtt.meshtastic.org":
                mode = "Public"
            else:
                mode = "Custom"

            try:
                ws_status = self._get_ws_bridge_status()
            except Exception as e:
                logger.debug("WebSocket bridge status check failed: %s", e)
                ws_status = "Unknown"

            choices = [
                ("status", f"Status              {status}"),
                ("start", "Start Subscriber    Connect to MQTT broker"),
                ("stop", "Stop Subscriber     Disconnect from broker"),
                ("broker", f"Broker Manager      Mode: {mode}"),
                ("config", "Configure           Advanced settings"),
                ("nodes", "View Nodes          Show discovered nodes"),
                ("stats", "Statistics          Node counts, activity"),
                ("telemetry", "Request Telemetry   Poll silent 2.7+ nodes"),
                ("export", "Export Data         Save nodes to file"),
            ]

            if _HAS_WS_BRIDGE:
                choices.append(("websocket", f"WebSocket Bridge    {ws_status}"))

            choices.append(("back", "Back"))

            subtitle = f"MQTT Broker: {mode} ({broker})\n"
            if mode == "Private":
                subtitle += "MeshForge private broker (multi-consumer)"
            elif mode == "Public":
                subtitle += "Nodeless monitoring without local radio"
            else:
                subtitle += f"Custom broker: {broker}"

            choice = self.ctx.dialog.menu(
                "MQTT Monitoring",
                subtitle,
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "status": ("MQTT Status", self._show_mqtt_status),
                "start": ("Start MQTT Subscriber", self._start_mqtt_subscriber),
                "stop": ("Stop MQTT Subscriber", self._stop_mqtt_subscriber),
                "broker": ("Broker Manager", self._dispatch_broker),
                "config": ("MQTT Configuration", self._configure_mqtt),
                "nodes": ("MQTT Nodes", self._show_mqtt_nodes),
                "stats": ("MQTT Statistics", self._show_mqtt_stats),
                "telemetry": ("Telemetry Requests", self._request_telemetry_menu),
                "export": ("Export MQTT Data", self._export_mqtt_data),
                "websocket": ("WebSocket Bridge", self._toggle_ws_bridge),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _dispatch_broker(self):
        """Dispatch to BrokerHandler's broker menu."""
        broker_handler = self.ctx.registry.get_handler("broker")
        if broker_handler:
            broker_handler._broker_menu()
        else:
            self.ctx.dialog.msgbox("Not Available", "Broker handler not loaded.")

    def _get_mqtt_status(self) -> str:
        """Get current MQTT subscriber status."""
        if not _HAS_MQTT:
            return "Module unavailable"
        if self._mqtt_subscriber and self._mqtt_subscriber.is_connected():
            return "Connected"
        return "Not running"

    def _get_ws_bridge_status(self) -> str:
        """Get WebSocket bridge status."""
        if not _HAS_WS_BRIDGE:
            return "Unavailable"
        if self._mqtt_ws_bridge and self._mqtt_ws_bridge.is_running:
            clients = self._mqtt_ws_bridge.connected_clients
            return f"Running ({clients} clients)"
        return "Stopped"

    def _toggle_ws_bridge(self):
        """Toggle the MQTT->WebSocket bridge for web UI access."""
        if not _HAS_WS_BRIDGE:
            self.ctx.dialog.msgbox(
                "WebSocket Unavailable",
                "WebSocket bridge not available.\n\n"
                "Install websockets: pip install websockets"
            )
            return

        if not self._mqtt_subscriber or not self._mqtt_subscriber.is_connected():
            self.ctx.dialog.msgbox(
                "MQTT Not Running",
                "Start the MQTT subscriber first.\n\n"
                "The WebSocket bridge forwards MQTT data to web clients."
            )
            return

        if self._mqtt_ws_bridge and self._mqtt_ws_bridge.is_running:
            if self.ctx.dialog.yesno(
                "Stop WebSocket Bridge",
                "Stop the WebSocket bridge?\n\n"
                "Web UI clients will disconnect."
            ):
                self._mqtt_ws_bridge.stop()
                self._mqtt_ws_bridge = None
                self.ctx.dialog.msgbox("Stopped", "WebSocket bridge stopped.")
        else:
            self.ctx.dialog.infobox("Starting", "Starting WebSocket bridge...")

            try:
                from utils.mqtt_websocket_bridge import MQTTWebSocketBridge
                self._mqtt_ws_bridge = MQTTWebSocketBridge(self._mqtt_subscriber)

                if self._mqtt_ws_bridge.start():
                    self.ctx.dialog.msgbox(
                        "WebSocket Bridge Started",
                        "MQTT->WebSocket bridge is now running!\n\n"
                        "Web UI can connect to: ws://localhost:5001\n\n"
                        "This enables the web map and dashboard to\n"
                        "receive mesh data via MQTT monitoring."
                    )
                else:
                    self._mqtt_ws_bridge = None
                    self.ctx.dialog.msgbox("Error", "Failed to start WebSocket bridge.")
            except Exception as e:
                logger.error("WebSocket bridge error: %s", e)
                self.ctx.dialog.msgbox("Error", f"WebSocket bridge error:\n{e}")

    def _show_mqtt_status(self):
        """Show detailed MQTT status."""
        if not _HAS_MQTT:
            self.ctx.dialog.msgbox(
                "MQTT Unavailable",
                "MQTT subscriber module not found.\n\n"
                "Make sure monitoring/mqtt_subscriber.py exists."
            )
            return

        lines = ["MQTT SUBSCRIBER STATUS", "=" * 40, ""]

        if self._mqtt_subscriber:
            connected = self._mqtt_subscriber.is_connected()
            lines.append(f"Status: {'Connected' if connected else 'Disconnected'}")

            stats = self._mqtt_subscriber.get_stats()
            lines.append(f"Nodes discovered: {stats.get('node_count', 0)}")
            lines.append(f"Messages received: {stats.get('messages_received', 0)}")

            config = load_mqtt_config()
            if config:
                lines.append("")
                lines.append("CONFIGURATION:")
                lines.append(f"  Broker: {config.get('broker', 'mqtt.meshtastic.org')}")
                lines.append(f"  Port: {config.get('port', 8883)}")
                lines.append(f"  Topic: {config.get('topic', 'msh/US/2/e/LongFast/#')}")

            if _HAS_WS_BRIDGE:
                lines.append("")
                lines.append("WEBSOCKET BRIDGE:")
                if self._mqtt_ws_bridge and self._mqtt_ws_bridge.is_running:
                    ws_stats = self._mqtt_ws_bridge.get_stats()
                    lines.append(f"  Status: Running")
                    lines.append(f"  Port: ws://0.0.0.0:{ws_stats.get('websocket_port', 5001)}")
                    lines.append(f"  Clients: {ws_stats.get('websocket_clients', 0)}")
                    lines.append(f"  Messages bridged: {ws_stats.get('messages_bridged', 0)}")
                else:
                    lines.append(f"  Status: Stopped")
                    lines.append(f"  Enable for web UI access")
        else:
            lines.append("Status: Not running")
            lines.append("")
            lines.append("Use 'Start Subscriber' to begin monitoring.")

        self.ctx.dialog.msgbox("MQTT Status", "\n".join(lines), width=50)

    def _start_mqtt_subscriber(self):
        """Start the MQTT subscriber."""
        if not _HAS_MQTT:
            self.ctx.dialog.msgbox(
                "MQTT Unavailable",
                "MQTT subscriber module not available."
            )
            return

        if self._mqtt_subscriber and self._mqtt_subscriber.is_connected():
            self.ctx.dialog.msgbox("Already Running", "MQTT subscriber is already connected.")
            return

        config = load_mqtt_config()
        self.ctx.dialog.infobox("Starting MQTT", "Connecting to MQTT broker...")

        try:
            broker = config.get('broker', 'mqtt.meshtastic.org')
            port = config.get('port', 8883)
            topic = config.get('topic', 'msh/US/2/e/LongFast/#')

            parts = topic.rstrip('#').rstrip('/').split('/')
            if len(parts) >= 4:
                channel = parts[-1] if parts[-1] else 'LongFast'
                if '/json/' in topic:
                    root_topic = '/'.join(parts[:-1]).replace('/json', '/e')
                else:
                    root_topic = '/'.join(parts[:-1])
            else:
                root_topic = 'msh/US/2/e'
                channel = 'LongFast'

            subscriber_config = {
                "broker": broker,
                "port": port,
                "username": config.get('username') or "",
                "password": config.get('password') or "",
                "root_topic": root_topic,
                "channel": channel,
                "key": "AQ==",
                "use_tls": config.get('use_tls', port == 8883),
                "auto_reconnect": True,
                "reconnect_delay": 2 if broker == 'localhost' else 5,
                "max_reconnect_delay": 30 if broker == 'localhost' else 60,
            }

            self._mqtt_subscriber = MQTTNodelessSubscriber(config=subscriber_config)
            self._mqtt_subscriber.start()
            time.sleep(2)

            if self._mqtt_subscriber.is_connected():
                self.ctx.dialog.msgbox(
                    "MQTT Started",
                    "MQTT subscriber is now connected!\n\n"
                    "Nodes will be discovered as messages are received.\n"
                    "Data is automatically cached for map display."
                )
            else:
                self.ctx.dialog.msgbox(
                    "Connection Issue",
                    "MQTT subscriber started but connection may be pending.\n\n"
                    "Check your network and broker settings."
                )

        except Exception as e:
            logger.error("Failed to start MQTT subscriber: %s", e)
            self.ctx.dialog.msgbox("Error", f"Failed to start MQTT subscriber:\n{e}")

    def _stop_mqtt_subscriber(self):
        """Stop the MQTT subscriber."""
        if not self._mqtt_subscriber:
            self.ctx.dialog.msgbox("Not Running", "MQTT subscriber is not running.")
            return

        ws_running = self._mqtt_ws_bridge and self._mqtt_ws_bridge.is_running
        ws_note = "\n\nWebSocket bridge will also be stopped." if ws_running else ""

        if self.ctx.dialog.yesno(
            "Stop MQTT",
            f"Stop the MQTT subscriber?\n\n"
            f"Node data will be preserved in cache.{ws_note}"
        ):
            try:
                if self._mqtt_ws_bridge:
                    self._mqtt_ws_bridge.stop()
                    self._mqtt_ws_bridge = None

                self._mqtt_subscriber.stop()
                self._mqtt_subscriber = None
                self.ctx.dialog.msgbox("Stopped", "MQTT subscriber stopped.")
            except Exception as e:
                self.ctx.dialog.msgbox("Error", f"Error stopping subscriber:\n{e}")

    def _configure_mqtt(self):
        """Configure MQTT broker settings."""
        config = load_mqtt_config()

        while True:
            broker = config.get('broker', 'mqtt.meshtastic.org')
            port = config.get('port', 8883)
            topic = config.get('topic', 'msh/US/2/e/LongFast/#')

            mode = "Local" if broker == "localhost" else "Public"

            auto_start = config.get('auto_start', False)
            auto_telem = config.get('auto_start_telemetry', True)
            auto_status = "ON" if auto_start else "OFF"
            telem_status = "ON" if auto_telem else "OFF"

            choices = [
                ("local", f"Use Local Broker    Quick: localhost:1883"),
                ("public", f"Use Public Broker   Quick: mqtt.meshtastic.org"),
                ("private", "Use Private Broker  Custom: your own broker"),
                ("broker", f"Broker              {broker}"),
                ("port", f"Port                {port}"),
                ("topic", f"Topic               {topic[:30]}..."),
                ("auth", "Authentication      Username/password"),
                ("autostart", f"Auto-Start          [{auto_status}] Start on TUI launch"),
                ("autotelem", f"Auto Telemetry      [{telem_status}] Poll silent nodes"),
                ("save", "Save & Exit"),
                ("back", "Cancel"),
            ]

            choice = self.ctx.dialog.menu(
                "MQTT Configuration",
                "Configure MQTT broker connection:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "local":
                channel = self._detect_local_channel()
                topic = f"msh/2/json/{channel}/#" if channel else "msh/2/json/+/#"
                config = {
                    'broker': 'localhost',
                    'port': 1883,
                    'topic': topic,
                    'username': None,
                    'password': None,
                    'use_tls': False
                }
                save_mqtt_config(config)
                self.ctx.dialog.msgbox(
                    "Local Mode Set",
                    f"Configured for local mosquitto broker:\n\n"
                    f"  Broker: localhost:1883\n"
                    f"  Topic: {topic}\n"
                    f"  TLS: disabled\n\n"
                    "Make sure:\n"
                    "  1. Mosquitto is running (systemctl status mosquitto)\n"
                    "  2. Meshtasticd MQTT is configured\n\n"
                    "Use Service Config -> MQTT Setup for full setup."
                )
                break

            elif choice == "public":
                config = {
                    'broker': 'mqtt.meshtastic.org',
                    'port': 8883,
                    'topic': 'msh/US/2/e/LongFast/#',
                    'username': 'meshdev',
                    'password': 'large4cats',
                    'use_tls': True
                }
                save_mqtt_config(config)
                self.ctx.dialog.msgbox(
                    "Public Mode Set",
                    "Configured for public Meshtastic broker:\n\n"
                    "  Broker: mqtt.meshtastic.org:8883\n"
                    "  Topic: msh/US/2/e/LongFast/#\n"
                    "  TLS: enabled\n\n"
                    "This is nodeless monitoring - no local radio needed."
                )
                break

            elif choice == "private":
                self._configure_private_broker(config)
                break

            elif choice == "broker":
                new_broker = self.ctx.dialog.inputbox(
                    "MQTT Broker", "Enter MQTT broker hostname:", init=broker)
                if new_broker:
                    config['broker'] = new_broker

            elif choice == "port":
                new_port = self.ctx.dialog.inputbox(
                    "MQTT Port", "Enter MQTT port (8883 for TLS, 1883 for plain):",
                    init=str(port))
                if new_port and new_port.isdigit():
                    config['port'] = int(new_port)

            elif choice == "topic":
                new_topic = self.ctx.dialog.inputbox(
                    "MQTT Topic",
                    "Enter MQTT topic filter:\n(Default: msh/US/2/e/LongFast/#)",
                    init=topic)
                if new_topic:
                    config['topic'] = new_topic

            elif choice == "auth":
                username = self.ctx.dialog.inputbox(
                    "Username", "Enter MQTT username (blank for anonymous):",
                    init=config.get('username', ''))
                if username is not None:
                    config['username'] = username if username else None

                password = self.ctx.dialog.inputbox(
                    "Password", "Enter MQTT password (blank for none):", init='')
                if password is not None:
                    config['password'] = password if password else None

            elif choice == "autostart":
                current = config.get('auto_start', False)
                config['auto_start'] = not current
                new_state = "ENABLED" if config['auto_start'] else "DISABLED"
                self.ctx.dialog.msgbox(
                    "Auto-Start",
                    f"MQTT auto-start: {new_state}\n\n"
                    "When enabled, MQTT subscriber will start\n"
                    "automatically when the TUI launches.\n\n"
                    "Save configuration to apply."
                )

            elif choice == "autotelem":
                current = config.get('auto_start_telemetry', True)
                config['auto_start_telemetry'] = not current
                new_state = "ENABLED" if config['auto_start_telemetry'] else "DISABLED"
                self.ctx.dialog.msgbox(
                    "Auto Telemetry",
                    f"TelemetryPoller auto-start: {new_state}\n\n"
                    "When enabled (and MQTT auto-start is on),\n"
                    "the TelemetryPoller will poll silent 2.7+\n"
                    "nodes in the background.\n\n"
                    "Save configuration to apply."
                )

            elif choice == "save":
                save_mqtt_config(config)
                self.ctx.dialog.msgbox(
                    "Saved",
                    "MQTT configuration saved.\n\n"
                    "Restart the subscriber for changes to take effect."
                )
                break

    def _show_mqtt_nodes(self):
        """Show nodes discovered via MQTT."""
        nodes = []
        if self._mqtt_subscriber:
            nodes = self._mqtt_subscriber.get_nodes()

        if not nodes:
            cache_data = self._load_mqtt_cache()
            if cache_data:
                nodes = cache_data
            else:
                self.ctx.dialog.msgbox(
                    "No Nodes",
                    "No MQTT nodes discovered yet.\n\n"
                    "Start the subscriber and wait for network activity."
                )
                return

        if not nodes:
            self.ctx.dialog.msgbox("No Nodes", "No nodes discovered yet.")
            return

        choices = []
        node_list = nodes[:50]
        for i, node in enumerate(node_list):
            if hasattr(node, 'long_name'):
                name = node.long_name or node.short_name or node.node_id
                last_seen = node.get_age_string()
                health_ind = ""
                if hasattr(node, 'heart_bpm') and node.heart_bpm:
                    health_ind = " [H]"
            elif isinstance(node, dict):
                props = node.get('properties', node)
                name = props.get('name', props.get('id', f'Node {i}'))
                last_seen = props.get('last_seen', 'cached')
                health_ind = ""
            else:
                name = f'Node {i}'
                last_seen = 'unknown'
                health_ind = ""
            choices.append((str(i), f"{str(name)[:18]:<18}{health_ind} ({last_seen})"))

        if len(nodes) > 50:
            choices.append(("more", f"... and {len(nodes) - 50} more nodes"))
        choices.append(("back", "Back"))

        while True:
            selected = self.ctx.dialog.menu(
                f"MQTT Nodes ({len(nodes)})",
                "Select a node for details, or Back to exit:",
                choices
            )

            if selected is None or selected == "back" or selected == "more":
                break

            try:
                idx = int(selected)
                if 0 <= idx < len(node_list):
                    self._show_mqtt_node_details(node_list[idx])
            except (ValueError, IndexError):
                pass

    def _show_mqtt_node_details(self, node):
        """Show detailed information for an MQTT-discovered node."""
        lines = []

        if hasattr(node, 'node_id'):
            lines.append(f"NODE: {node.node_id}")
            lines.append("=" * 50)
            lines.append("")

            lines.append("IDENTITY:")
            lines.append("-" * 50)
            if node.long_name:
                lines.append(f"  Long Name:  {node.long_name}")
            if node.short_name:
                lines.append(f"  Short Name: {node.short_name}")
            if node.hardware_model:
                lines.append(f"  Hardware:   {node.hardware_model}")
            if node.role:
                lines.append(f"  Role:       {node.role}")
            lines.append(f"  Via MQTT:   Yes")
            lines.append(f"  Last Seen:  {node.get_age_string()}")
            lines.append("")

            has_health = (
                (hasattr(node, 'heart_bpm') and node.heart_bpm) or
                (hasattr(node, 'spo2') and node.spo2) or
                (hasattr(node, 'body_temperature') and node.body_temperature)
            )
            if has_health:
                lines.append("HEALTH METRICS:")
                lines.append("-" * 50)
                if hasattr(node, 'heart_bpm') and node.heart_bpm:
                    lines.append(f"  Heart Rate: {node.heart_bpm} BPM")
                if hasattr(node, 'spo2') and node.spo2:
                    lines.append(f"  SpO2:       {node.spo2}%")
                if hasattr(node, 'body_temperature') and node.body_temperature:
                    lines.append(f"  Body Temp:  {node.body_temperature:.1f}C")
                lines.append("")

            has_device = node.battery_level or node.voltage
            has_channel = node.channel_utilization or node.air_util_tx
            if has_device or has_channel:
                lines.append("DEVICE TELEMETRY:")
                lines.append("-" * 50)
                if node.battery_level:
                    lines.append(f"  Battery:    {node.battery_level}%")
                if node.voltage:
                    lines.append(f"  Voltage:    {node.voltage:.2f}V")
                if node.channel_utilization:
                    chutil = node.channel_utilization
                    warn = " [!]" if chutil > 25 else ""
                    lines.append(f"  ChUtil:     {chutil:.1f}%{warn}")
                if node.air_util_tx:
                    airutil = node.air_util_tx
                    warn = " [!]" if airutil > 7 else ""
                    lines.append(f"  AirUtilTX:  {airutil:.1f}%{warn}")
                lines.append("")

            has_env = node.temperature or node.humidity or node.pressure
            if has_env:
                lines.append("ENVIRONMENT:")
                lines.append("-" * 50)
                if node.temperature:
                    lines.append(f"  Temperature: {node.temperature:.1f}C")
                if node.humidity:
                    lines.append(f"  Humidity:    {node.humidity:.0f}%")
                if node.pressure:
                    lines.append(f"  Pressure:    {node.pressure:.0f} hPa")
                lines.append("")

            has_aq = node.pm25_standard or node.co2 or node.iaq
            if has_aq:
                lines.append("AIR QUALITY:")
                lines.append("-" * 50)
                if node.pm25_standard:
                    lines.append(f"  PM2.5:      {node.pm25_standard} ug/m3")
                if node.pm10_standard:
                    lines.append(f"  PM10:       {node.pm10_standard} ug/m3")
                if node.co2:
                    lines.append(f"  CO2:        {node.co2} ppm")
                if node.iaq:
                    lines.append(f"  IAQ Index:  {node.iaq}")
                lines.append("")

            if node.snr or node.rssi:
                lines.append("SIGNAL QUALITY:")
                lines.append("-" * 50)
                if node.snr:
                    lines.append(f"  SNR:        {node.snr:.1f} dB")
                if node.rssi:
                    lines.append(f"  RSSI:       {node.rssi} dBm")
                if node.hops_away is not None:
                    lines.append(f"  Hops:       {node.hops_away}")
                lines.append("")

            if node.latitude and node.longitude:
                lines.append("POSITION:")
                lines.append("-" * 50)
                lines.append(f"  Latitude:   {node.latitude:.6f}")
                lines.append(f"  Longitude:  {node.longitude:.6f}")
                if node.altitude:
                    lines.append(f"  Altitude:   {node.altitude}m")
                lines.append("")

            if hasattr(node, 'relay_node') and node.relay_node:
                lines.append("RELAY INFO:")
                lines.append("-" * 50)
                lines.append(f"  Relay Node: !...{node.relay_node:02x}")
                if hasattr(node, 'next_hop') and node.next_hop:
                    lines.append(f"  Next Hop:   !...{node.next_hop:02x}")
                lines.append("")

        else:
            props = node.get('properties', node)
            lines.append(f"NODE: {props.get('id', 'Unknown')}")
            lines.append("=" * 50)
            lines.append(f"Name: {props.get('name', 'Unknown')}")
            lines.append(f"Last Seen: {props.get('last_seen', 'Unknown')}")

        self.ctx.dialog.msgbox("Node Details", "\n".join(lines))

    def _show_mqtt_stats(self):
        """Show MQTT statistics."""
        lines = ["MQTT STATISTICS", "=" * 40, ""]

        if self._mqtt_subscriber:
            stats = self._mqtt_subscriber.get_stats()

            lines.append("NODE COUNTS:")
            lines.append(f"  Total nodes:      {stats.get('node_count', 0)}")
            lines.append(f"  Online (15 min):  {stats.get('online_count', 0)}")
            lines.append(f"  With position:    {stats.get('with_position', 0)}")
            lines.append("")

            env_count = stats.get('nodes_with_env_metrics', 0)
            aq_count = stats.get('nodes_with_aq_metrics', 0)
            health_count = stats.get('nodes_with_health_metrics', 0)
            if env_count or aq_count or health_count:
                lines.append("SENSOR NODES:")
                if env_count:
                    lines.append(f"  Environment:      {env_count}")
                if aq_count:
                    lines.append(f"  Air Quality:      {aq_count}")
                if health_count:
                    lines.append(f"  Health Metrics:   {health_count}")
                lines.append("")

            chutil_warn = stats.get('nodes_chutil_warning', 0)
            chutil_crit = stats.get('nodes_chutil_critical', 0)
            airutil_warn = stats.get('nodes_airutiltx_warning', 0)
            airutil_crit = stats.get('nodes_airutiltx_critical', 0)
            if chutil_warn or chutil_crit or airutil_warn or airutil_crit:
                lines.append("MESH HEALTH:")
                if chutil_warn:
                    lines.append(f"  ChUtil >25%:      {chutil_warn} nodes")
                if chutil_crit:
                    lines.append(f"  ChUtil >40%:      {chutil_crit} nodes [!]")
                if airutil_warn:
                    lines.append(f"  AirUtil >7%:      {airutil_warn} nodes")
                if airutil_crit:
                    lines.append(f"  AirUtil >10%:     {airutil_crit} nodes [!]")
                lines.append("")

            relay_discovered = stats.get('nodes_discovered_via_relay', 0)
            relay_merged = stats.get('relay_nodes_merged', 0)
            if relay_discovered or relay_merged:
                lines.append("RELAY DISCOVERY:")
                if relay_discovered:
                    lines.append(f"  Via relay:        {relay_discovered}")
                if relay_merged:
                    lines.append(f"  Merged nodes:     {relay_merged}")
                lines.append("")

            lines.append("TRAFFIC:")
            lines.append(f"  Messages recv:    {stats.get('messages_received', 0)}")
            lines.append(f"  Messages rejected:{stats.get('messages_rejected', 0)}")
            lines.append(f"  Reconnect tries:  {stats.get('reconnect_attempts', 0)}")

            if stats.get('connect_time'):
                lines.append("")
                lines.append(f"Connected since: {stats['connect_time']}")
        else:
            cache = self._load_mqtt_cache()
            if cache:
                lines.append(f"Cached nodes: {len(cache)}")
            else:
                lines.append("No data available.")
                lines.append("Start the MQTT subscriber to collect data.")

        self.ctx.dialog.msgbox("MQTT Statistics", "\n".join(lines))

    def _export_mqtt_data(self):
        """Export MQTT node data to file."""
        if not self._mqtt_subscriber and not self._load_mqtt_cache():
            self.ctx.dialog.msgbox("No Data", "No MQTT data to export.")
            return

        export_path = get_real_user_home() / ".local" / "share" / "meshforge" / "mqtt_export.json"

        try:
            if self._mqtt_subscriber:
                nodes = self._mqtt_subscriber.get_nodes()
                nodes_data = []
                for node in nodes:
                    nodes_data.append({
                        'id': node.node_id,
                        'name': node.long_name or node.short_name or node.node_id,
                        'network': 'meshtastic',
                        'lat': node.latitude,
                        'lon': node.longitude,
                        'last_seen': node.get_age_string(),
                        'battery': node.battery_level,
                        'snr': node.snr,
                        'rssi': node.rssi,
                        'hardware': node.hardware_model,
                    })
            else:
                nodes_data = self._load_mqtt_cache()

            export_path.parent.mkdir(parents=True, exist_ok=True)
            with open(export_path, 'w') as f:
                json.dump({'nodes': nodes_data, 'exported_at': time.time()}, f, indent=2)

            self.ctx.dialog.msgbox(
                "Export Complete",
                f"MQTT data exported to:\n{export_path}\n\n"
                f"Nodes exported: {len(nodes_data)}"
            )
        except Exception as e:
            self.ctx.dialog.msgbox("Export Error", f"Failed to export:\n{e}")

    def _load_mqtt_cache(self) -> list:
        """Load cached MQTT nodes from file."""
        cache_path = get_real_user_home() / ".local" / "share" / "meshforge" / "mqtt_nodes.json"
        try:
            if cache_path.exists():
                with open(cache_path) as f:
                    data = json.load(f)
                    if data.get('type') == 'FeatureCollection':
                        return data.get('features', [])
                    return data.get('nodes', [])
        except Exception as e:
            logger.debug("Error loading MQTT cache: %s", e)
        return []

    def _detect_local_channel(self) -> Optional[str]:
        """Detect primary channel name from local meshtasticd."""
        try:
            cli = shutil.which('meshtastic') or 'meshtastic'
            result = subprocess.run(
                [cli, '--host', 'localhost', '--ch-index', '0', '--info'],
                capture_output=True, text=True, timeout=15
            )

            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'name' in line.lower():
                        parts = line.split(':')
                        if len(parts) >= 2:
                            name = parts[1].strip().strip('"\'')
                            if name and name.lower() not in ('none', ''):
                                logger.debug("Detected channel: %s", name)
                                return name
        except Exception as e:
            logger.debug("Could not detect channel: %s", e)

        return None

    def _configure_private_broker(self, config: Dict[str, Any]):
        """Guided setup for a private MQTT broker."""
        broker = self.ctx.dialog.inputbox(
            "Broker Address",
            "Enter your private MQTT broker hostname or IP:\n\n"
            "Examples:\n"
            "  gt.wildc.net\n"
            "  192.168.1.100\n"
            "  mqtt.local",
            init=config.get('broker', '')
        )
        if not broker:
            return

        port = self.ctx.dialog.inputbox(
            "Broker Port",
            "Enter MQTT port:\n\n"
            "  1883 = Plain TCP\n"
            "  1884 = Alternative plain TCP\n"
            "  8883 = TLS encrypted",
            init=str(config.get('port', 1883))
        )
        if not port or not port.isdigit():
            return

        username = self.ctx.dialog.inputbox(
            "Username", "MQTT username (blank for anonymous):",
            init=config.get('username', ''))

        password = self.ctx.dialog.inputbox(
            "Password", "MQTT password (blank for none):", init='')

        root_topic = self.ctx.dialog.inputbox(
            "Root Topic",
            "MQTT root topic -- controls which nodes you see:\n\n"
            "  msh           = ALL nodes (can be 5000+)\n"
            "  msh/US        = US region only\n"
            "  msh/US/2/e    = US encrypted channel\n"
            "  msh/HI        = Hawaii only (if broker supports)\n\n"
            "Your meshtasticd MQTT module must use the same root topic.",
            init=config.get('root_topic', 'msh/US/2/e')
        )
        if not root_topic:
            root_topic = "msh/US/2/e"

        channel = self.ctx.dialog.inputbox(
            "Channel Name",
            "Meshtastic channel to subscribe to:\n\n"
            "  LongFast   = Default Meshtastic channel\n"
            "  HawaiiNet  = Regional channel\n"
            "  meshforge  = Private MeshForge channel\n\n"
            "Must match your radio's channel configuration.",
            init=config.get('channel', 'LongFast')
        )
        if not channel:
            channel = "LongFast"

        topic = f"{root_topic}/{channel}/#"
        use_tls = int(port) == 8883

        new_config = {
            'broker': broker,
            'port': int(port),
            'topic': topic,
            'root_topic': root_topic,
            'channel': channel,
            'username': username if username else None,
            'password': password if password else None,
            'use_tls': use_tls,
        }

        new_config['auto_start'] = config.get('auto_start', False)
        new_config['auto_start_telemetry'] = config.get('auto_start_telemetry', True)

        save_mqtt_config(new_config)
        self.ctx.dialog.msgbox(
            "Private Broker Configured",
            f"Saved configuration:\n\n"
            f"  Broker:   {broker}:{port}\n"
            f"  Topic:    {topic}\n"
            f"  Channel:  {channel}\n"
            f"  Username: {username or '(anonymous)'}\n"
            f"  TLS:      {'Yes' if use_tls else 'No'}\n\n"
            f"Root topic '{root_topic}' determines node scope.\n"
            f"Restart MQTT subscriber to apply."
        )

    def _request_telemetry_menu(self):
        """Request telemetry from silent Meshtastic 2.7+ nodes."""
        choices = [
            ("single", "Request from Node    Enter node ID manually"),
            ("silent", "Find Silent Nodes    Nodes with stale telemetry"),
            ("batch", "Poll Silent Nodes    Request from all silent"),
        ]

        if _HAS_TELEMETRY_POLLER:
            poller = get_telemetry_poller()
            stats = poller.get_stats()
            choices.append(("stats", f"Poller Statistics    {stats.get('total_requests', 0)} requests"))

        choices.append(("back", "Back"))

        while True:
            choice = self.ctx.dialog.menu(
                "Telemetry Requests",
                "Request telemetry from silent Meshtastic 2.7+ nodes.\n\n"
                "These nodes don't broadcast telemetry by default to reduce\n"
                "mesh congestion. Use this to poll them explicitly.",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "single": ("Request Single Telemetry", self._request_single_telemetry),
                "silent": ("Show Silent Nodes", self._show_silent_nodes),
                "batch": ("Batch Telemetry Request", self._batch_telemetry_request),
                "stats": ("Poller Statistics", self._show_poller_stats),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _request_single_telemetry(self):
        """Request telemetry from a single node by ID."""
        node_id = self.ctx.dialog.inputbox(
            "Request Telemetry",
            "Enter the Meshtastic node ID (e.g., !ba4bf9d0):",
            init="!"
        )

        if not node_id or node_id == "!":
            return

        if not node_id.startswith('!'):
            node_id = f"!{node_id}"

        self.ctx.dialog.infobox("Requesting", f"Sending telemetry request to {node_id}...")

        if _HAS_TELEMETRY_POLLER:
            poller = _get_telemetry_poller()
            success = poller.poll_node_now(node_id)

            if success:
                self.ctx.dialog.msgbox(
                    "Request Sent",
                    f"Telemetry request sent to {node_id}.\n\n"
                    "The node should respond within a few seconds.\n"
                    "Check MQTT Nodes view for updated data."
                )
            else:
                self.ctx.dialog.msgbox(
                    "Request Failed",
                    f"Failed to send telemetry request to {node_id}.\n\n"
                    "Possible reasons:\n"
                    "- meshtastic CLI not found\n"
                    "- Rate limited (max 4 requests/minute)\n"
                    "- meshtasticd not running"
                )
        else:
            self._fallback_telemetry_request(node_id)

    def _fallback_telemetry_request(self, node_id: str):
        """Fallback telemetry request using direct CLI call."""
        cli = shutil.which('meshtastic')
        if not cli:
            self.ctx.dialog.msgbox(
                "CLI Not Found",
                "meshtastic CLI not found.\n\n"
                "Install it with: pipx install meshtastic"
            )
            return

        try:
            result = subprocess.run(
                [cli, '--host', 'localhost', '--request-telemetry', '--dest', node_id],
                capture_output=True, text=True, timeout=30
            )

            if result.returncode == 0:
                self.ctx.dialog.msgbox(
                    "Request Sent",
                    f"Telemetry request sent to {node_id}.\n\n"
                    f"Output:\n{result.stdout[:500] if result.stdout else 'No output'}"
                )
            else:
                self.ctx.dialog.msgbox(
                    "Request Failed",
                    f"Failed to request telemetry:\n{result.stderr[:500] if result.stderr else 'Unknown error'}"
                )
        except subprocess.TimeoutExpired:
            self.ctx.dialog.msgbox("Timeout", "Telemetry request timed out after 30 seconds.")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to request telemetry:\n{e}")

    def _show_silent_nodes(self):
        """Show nodes with stale or missing telemetry."""
        if not self._mqtt_subscriber:
            self.ctx.dialog.msgbox(
                "MQTT Not Running",
                "Start the MQTT subscriber first to discover nodes."
            )
            return

        nodes = self._mqtt_subscriber.get_nodes()
        if not nodes:
            self.ctx.dialog.msgbox("No Nodes", "No nodes discovered yet.")
            return

        if not _HAS_TELEMETRY_POLLER:
            self.ctx.dialog.msgbox("Module Not Found", "TelemetryPoller module not available.")
            return

        poller = _TelemetryPoller()

        node_list = []
        for node in nodes:
            node_list.append({
                'id': node.node_id,
                'is_online': node.is_online(),
                'telemetry_timestamp': node.last_seen
            })

        silent = poller.identify_silent_nodes(node_list, telemetry_age_threshold=1800)

        if not silent:
            self.ctx.dialog.msgbox(
                "No Silent Nodes",
                "All online nodes have recent telemetry.\n\nThreshold: 30 minutes"
            )
            return

        lines = ["SILENT NODES (>30 min without telemetry)", "=" * 50, ""]

        for node_id in silent[:20]:
            for node in nodes:
                if node.node_id == node_id:
                    name = node.long_name or node.short_name or node_id
                    age = node.get_age_string()
                    lines.append(f"  {node_id}  {name[:15]:<15} ({age})")
                    break
            else:
                lines.append(f"  {node_id}")

        if len(silent) > 20:
            lines.append(f"\n  ... and {len(silent) - 20} more")

        lines.append("")
        lines.append("Use 'Poll Silent Nodes' to request telemetry from all.")

        self.ctx.dialog.msgbox("Silent Nodes", "\n".join(lines))

    def _batch_telemetry_request(self):
        """Request telemetry from all silent nodes."""
        if not self._mqtt_subscriber:
            self.ctx.dialog.msgbox(
                "MQTT Not Running",
                "Start the MQTT subscriber first to discover nodes."
            )
            return

        if not self.ctx.dialog.yesno(
            "Confirm Batch Request",
            "This will send telemetry requests to all silent nodes.\n\n"
            "Requests are rate-limited to 4/minute to avoid\n"
            "congesting the mesh.\n\nContinue?"
        ):
            return

        nodes = self._mqtt_subscriber.get_nodes()
        if not nodes:
            self.ctx.dialog.msgbox("No Nodes", "No nodes discovered yet.")
            return

        if not _HAS_TELEMETRY_POLLER:
            self.ctx.dialog.msgbox("Module Not Found", "TelemetryPoller module not available.")
            return

        poller = _get_telemetry_poller()

        node_list = []
        for node in nodes:
            node_list.append({
                'id': node.node_id,
                'is_online': node.is_online(),
                'telemetry_timestamp': node.last_seen
            })

        silent = poller.identify_silent_nodes(node_list, telemetry_age_threshold=1800)

        if not silent:
            self.ctx.dialog.msgbox("No Silent Nodes", "No silent nodes to poll.")
            return

        self.ctx.dialog.infobox("Polling", f"Sending requests to {min(5, len(silent))} nodes...")

        success_count = 0
        for node_id in silent[:5]:
            if poller.poll_node_now(node_id):
                success_count += 1
            time.sleep(0.5)

        self.ctx.dialog.msgbox(
            "Batch Complete",
            f"Telemetry requests sent: {success_count}/{min(5, len(silent))}\n\n"
            f"Total silent nodes: {len(silent)}\n"
            f"Rate limit: 4 requests/minute\n\n"
            "Run again to poll more nodes."
        )

    def _show_poller_stats(self):
        """Show telemetry poller statistics."""
        if not _HAS_TELEMETRY_POLLER:
            self.ctx.dialog.msgbox("Module Not Found", "TelemetryPoller module not available.")
            return

        poller = _get_telemetry_poller()
        stats = poller.get_stats()

        lines = [
            "TELEMETRY POLLER STATISTICS",
            "=" * 40,
            "",
            f"Total requests:      {stats.get('total_requests', 0)}",
            f"Successful:          {stats.get('successful_requests', 0)}",
            f"Failed:              {stats.get('failed_requests', 0)}",
            f"Rate limited:        {stats.get('rate_limited', 0)}",
            "",
            f"Nodes polled:        {stats.get('nodes_polled', 0)}",
        ]

        if stats.get('last_poll_cycle'):
            lines.append(f"Last poll cycle:     {stats['last_poll_cycle']}")

        self.ctx.dialog.msgbox("Poller Statistics", "\n".join(lines))
