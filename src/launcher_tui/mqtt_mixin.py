"""
MQTT Mixin - MQTT monitoring control for MeshForge TUI.

Provides:
- Start/stop MQTT subscriber
- Configure MQTT broker settings
- View MQTT node statistics
- WebSocket bridge for web UI (MQTT → WebSocket:5001)
"""

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Import path utility - see persistent_issues.md Issue #1
try:
    from utils.paths import get_real_user_home
except ImportError:
    import os
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user and sudo_user != 'root' and '/' not in sudo_user and '..' not in sudo_user:
            return Path(f'/home/{sudo_user}')
        logname = os.environ.get('LOGNAME', '')
        if logname and logname != 'root' and '/' not in logname and '..' not in logname:
            return Path(f'/home/{logname}')
        return Path('/root')

# Try to import the MQTT subscriber
try:
    from monitoring.mqtt_subscriber import MQTTNodelessSubscriber
    _HAS_MQTT = True
except ImportError:
    _HAS_MQTT = False
    MQTTNodelessSubscriber = None

# Try to import the MQTT→WebSocket bridge
try:
    from utils.mqtt_websocket_bridge import MQTTWebSocketBridge, is_bridge_available
    _HAS_WS_BRIDGE = is_bridge_available()
except ImportError:
    _HAS_WS_BRIDGE = False
    MQTTWebSocketBridge = None

# Try to import TelemetryPoller for auto-start
try:
    from utils.telemetry_poller import get_telemetry_poller
    _HAS_TELEMETRY_POLLER = True
except ImportError:
    _HAS_TELEMETRY_POLLER = False
    get_telemetry_poller = None


class MQTTMixin:
    """MQTT monitoring control for mesh networks."""

    # Class-level subscriber instance (shared across menu calls)
    _mqtt_subscriber: Optional[Any] = None
    _mqtt_thread: Optional[threading.Thread] = None
    _mqtt_ws_bridge: Optional[Any] = None  # MQTT→WebSocket bridge

    def _maybe_auto_start_mqtt_and_telemetry(self):
        """Auto-start MQTT subscriber and TelemetryPoller if configured.

        Called at TUI startup. Follows the same pattern as _maybe_auto_start_map().
        Silent operation - no dialogs, all errors suppressed to prevent TUI corruption.
        """
        if not _HAS_MQTT:
            return

        config = self._load_mqtt_config()

        # Check if auto-start is enabled
        if not config.get('auto_start', False):
            return

        # Check if already connected
        if self._mqtt_subscriber and self._mqtt_subscriber.is_connected():
            return

        # Suppress output to prevent TUI corruption during startup
        try:
            import logging
            from contextlib import redirect_stdout, redirect_stderr
            from io import StringIO

            root_logger = logging.getLogger()
            old_level = root_logger.level
            root_logger.setLevel(logging.CRITICAL + 1)

            try:
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self._auto_start_mqtt_quiet(config)
            finally:
                root_logger.setLevel(old_level)

        except Exception:
            pass  # Non-fatal, don't interrupt startup

    def _auto_start_mqtt_quiet(self, config: Dict[str, Any]):
        """Quietly start MQTT subscriber without any UI feedback.

        Args:
            config: MQTT configuration dictionary
        """
        broker = config.get('broker', 'mqtt.meshtastic.org')
        port = config.get('port', 8883)
        topic = config.get('topic', 'msh/US/2/e/LongFast/#')

        # Parse root_topic and channel from full topic string
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
            "key": "AQ==",  # Default Meshtastic key
            "use_tls": config.get('use_tls', port == 8883),
            "auto_reconnect": True,
            "reconnect_delay": 2 if broker == 'localhost' else 5,
            "max_reconnect_delay": 30 if broker == 'localhost' else 60,
        }

        self._mqtt_subscriber = MQTTNodelessSubscriber(config=subscriber_config)
        self._mqtt_subscriber.start()

        # Also start TelemetryPoller if available and auto_start_telemetry is enabled
        if _HAS_TELEMETRY_POLLER and config.get('auto_start_telemetry', True):
            # Get the singleton poller with auto_start=True
            # Default to 30 minute poll interval
            get_telemetry_poller(
                poll_interval_minutes=config.get('telemetry_poll_minutes', 30),
                auto_start=True
            )

    def _mqtt_menu(self):
        """MQTT monitoring menu - nodeless mesh observation."""
        while True:
            # Get current status and mode
            status = self._get_mqtt_status()
            config = self._load_mqtt_config()
            broker = config.get('broker', 'mqtt.meshtastic.org')
            mode = "Local" if broker == "localhost" else "Public"

            # Check WebSocket bridge status
            ws_status = self._get_ws_bridge_status()

            choices = [
                ("status", f"Status              {status}"),
                ("start", "Start Subscriber    Connect to MQTT broker"),
                ("stop", "Stop Subscriber     Disconnect from broker"),
                ("config", f"Configure           Mode: {mode}"),
                ("nodes", "View Nodes          Show discovered nodes"),
                ("stats", "Statistics          Node counts, activity"),
                ("telemetry", "Request Telemetry   Poll silent 2.7+ nodes"),
                ("export", "Export Data         Save nodes to file"),
            ]

            # Add WebSocket bridge option if available
            if _HAS_WS_BRIDGE:
                choices.append(("websocket", f"WebSocket Bridge    {ws_status}"))

            choices.append(("back", "Back"))

            subtitle = f"MQTT Mode: {mode} ({broker})\n"
            if mode == "Local":
                subtitle += "Multi-consumer: shares messages with other apps"
            else:
                subtitle += "Nodeless monitoring without local radio"

            choice = self.dialog.menu(
                "MQTT Monitoring",
                subtitle,
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "status":
                self._show_mqtt_status()
            elif choice == "start":
                self._start_mqtt_subscriber()
            elif choice == "stop":
                self._stop_mqtt_subscriber()
            elif choice == "config":
                self._configure_mqtt()
            elif choice == "nodes":
                self._show_mqtt_nodes()
            elif choice == "stats":
                self._show_mqtt_stats()
            elif choice == "telemetry":
                self._request_telemetry_menu()
            elif choice == "export":
                self._export_mqtt_data()
            elif choice == "websocket":
                self._toggle_ws_bridge()

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
        """Toggle the MQTT→WebSocket bridge for web UI access."""
        if not _HAS_WS_BRIDGE:
            self.dialog.msgbox(
                "WebSocket Unavailable",
                "WebSocket bridge not available.\n\n"
                "Install websockets: pip install websockets"
            )
            return

        # Check if MQTT subscriber is running
        if not self._mqtt_subscriber or not self._mqtt_subscriber.is_connected():
            self.dialog.msgbox(
                "MQTT Not Running",
                "Start the MQTT subscriber first.\n\n"
                "The WebSocket bridge forwards MQTT data to web clients."
            )
            return

        # Toggle bridge state
        if self._mqtt_ws_bridge and self._mqtt_ws_bridge.is_running:
            # Stop the bridge
            if self.dialog.yesno(
                "Stop WebSocket Bridge",
                "Stop the WebSocket bridge?\n\n"
                "Web UI clients will disconnect."
            ):
                self._mqtt_ws_bridge.stop()
                self._mqtt_ws_bridge = None
                self.dialog.msgbox("Stopped", "WebSocket bridge stopped.")
        else:
            # Start the bridge
            self.dialog.infobox("Starting", "Starting WebSocket bridge...")

            try:
                from utils.mqtt_websocket_bridge import MQTTWebSocketBridge
                self._mqtt_ws_bridge = MQTTWebSocketBridge(self._mqtt_subscriber)

                if self._mqtt_ws_bridge.start():
                    self.dialog.msgbox(
                        "WebSocket Bridge Started",
                        "MQTT→WebSocket bridge is now running!\n\n"
                        "Web UI can connect to: ws://localhost:5001\n\n"
                        "This enables the web map and dashboard to\n"
                        "receive mesh data via MQTT monitoring."
                    )
                else:
                    self._mqtt_ws_bridge = None
                    self.dialog.msgbox("Error", "Failed to start WebSocket bridge.")
            except Exception as e:
                logger.error(f"WebSocket bridge error: {e}")
                self.dialog.msgbox("Error", f"WebSocket bridge error:\n{e}")

    def _show_mqtt_status(self):
        """Show detailed MQTT status."""
        if not _HAS_MQTT:
            self.dialog.msgbox(
                "MQTT Unavailable",
                "MQTT subscriber module not found.\n\n"
                "Make sure monitoring/mqtt_subscriber.py exists."
            )
            return

        lines = ["MQTT SUBSCRIBER STATUS", "=" * 40, ""]

        if self._mqtt_subscriber:
            connected = self._mqtt_subscriber.is_connected()
            lines.append(f"Status: {'Connected' if connected else 'Disconnected'}")

            # Get subscriber stats using proper API
            stats = self._mqtt_subscriber.get_stats()
            lines.append(f"Nodes discovered: {stats.get('node_count', 0)}")
            lines.append(f"Messages received: {stats.get('messages_received', 0)}")

            # Get config info
            config = self._load_mqtt_config()
            if config:
                lines.append("")
                lines.append("CONFIGURATION:")
                lines.append(f"  Broker: {config.get('broker', 'mqtt.meshtastic.org')}")
                lines.append(f"  Port: {config.get('port', 8883)}")
                lines.append(f"  Topic: {config.get('topic', 'msh/US/2/e/LongFast/#')}")

            # WebSocket bridge status
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

        self.dialog.msgbox("MQTT Status", "\n".join(lines), width=50)

    def _start_mqtt_subscriber(self):
        """Start the MQTT subscriber."""
        if not _HAS_MQTT:
            self.dialog.msgbox(
                "MQTT Unavailable",
                "MQTT subscriber module not available."
            )
            return

        if self._mqtt_subscriber and self._mqtt_subscriber.is_connected():
            self.dialog.msgbox("Already Running", "MQTT subscriber is already connected.")
            return

        # Load configuration
        config = self._load_mqtt_config()

        self.dialog.infobox("Starting MQTT", "Connecting to MQTT broker...")

        try:
            # Convert TUI config format to subscriber config format
            # The subscriber expects: broker, port, root_topic, channel, key, use_tls, etc.
            broker = config.get('broker', 'mqtt.meshtastic.org')
            port = config.get('port', 8883)
            topic = config.get('topic', 'msh/US/2/e/LongFast/#')

            # Parse root_topic and channel from full topic string
            # Topic format: msh/2/json/{channel}/# or msh/{region}/2/e/{channel}/#
            parts = topic.rstrip('#').rstrip('/').split('/')
            if len(parts) >= 4:
                # Extract channel (last non-empty part before #)
                channel = parts[-1] if parts[-1] else 'LongFast'
                # Build root_topic from parts before channel
                # For msh/2/json/HawaiiNet -> root = msh/2/e (standard encrypted path)
                # For msh/US/2/e/LongFast -> root = msh/US/2/e
                if '/json/' in topic:
                    # Local broker format: msh/2/json/{channel}/#
                    root_topic = '/'.join(parts[:-1]).replace('/json', '/e')
                else:
                    # Public broker format: msh/{region}/2/e/{channel}/#
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
                "key": "AQ==",  # Default Meshtastic key
                "use_tls": config.get('use_tls', port == 8883),
                "auto_reconnect": True,
                "reconnect_delay": 2 if broker == 'localhost' else 5,
                "max_reconnect_delay": 30 if broker == 'localhost' else 60,
            }

            self._mqtt_subscriber = MQTTNodelessSubscriber(config=subscriber_config)

            # Start the subscriber (it manages its own loop thread)
            success = self._mqtt_subscriber.start()

            # Wait a moment for connection
            time.sleep(2)

            if self._mqtt_subscriber.is_connected():
                self.dialog.msgbox(
                    "MQTT Started",
                    "MQTT subscriber is now connected!\n\n"
                    "Nodes will be discovered as messages are received.\n"
                    "Data is automatically cached for map display."
                )
            else:
                self.dialog.msgbox(
                    "Connection Issue",
                    "MQTT subscriber started but connection may be pending.\n\n"
                    "Check your network and broker settings."
                )

        except Exception as e:
            logger.error(f"Failed to start MQTT subscriber: {e}")
            self.dialog.msgbox("Error", f"Failed to start MQTT subscriber:\n{e}")

    def _stop_mqtt_subscriber(self):
        """Stop the MQTT subscriber."""
        if not self._mqtt_subscriber:
            self.dialog.msgbox("Not Running", "MQTT subscriber is not running.")
            return

        # Note if WebSocket bridge is running
        ws_running = self._mqtt_ws_bridge and self._mqtt_ws_bridge.is_running
        ws_note = "\n\nWebSocket bridge will also be stopped." if ws_running else ""

        if self.dialog.yesno(
            "Stop MQTT",
            f"Stop the MQTT subscriber?\n\n"
            f"Node data will be preserved in cache.{ws_note}"
        ):
            try:
                # Stop WebSocket bridge first if running
                if self._mqtt_ws_bridge:
                    self._mqtt_ws_bridge.stop()
                    self._mqtt_ws_bridge = None

                self._mqtt_subscriber.stop()
                self._mqtt_subscriber = None
                self.dialog.msgbox("Stopped", "MQTT subscriber stopped.")
            except Exception as e:
                self.dialog.msgbox("Error", f"Error stopping subscriber:\n{e}")

    def _configure_mqtt(self):
        """Configure MQTT broker settings."""
        config = self._load_mqtt_config()

        while True:
            broker = config.get('broker', 'mqtt.meshtastic.org')
            port = config.get('port', 8883)
            topic = config.get('topic', 'msh/US/2/e/LongFast/#')

            # Determine current mode
            mode = "Local" if broker == "localhost" else "Public"

            # Auto-start settings
            auto_start = config.get('auto_start', False)
            auto_telem = config.get('auto_start_telemetry', True)
            auto_status = "ON" if auto_start else "OFF"
            telem_status = "ON" if auto_telem else "OFF"

            choices = [
                ("local", f"Use Local Broker    Quick: localhost:1883"),
                ("public", f"Use Public Broker   Quick: mqtt.meshtastic.org"),
                ("broker", f"Broker              {broker}"),
                ("port", f"Port                {port}"),
                ("topic", f"Topic               {topic[:30]}..."),
                ("auth", "Authentication      Username/password"),
                ("autostart", f"Auto-Start          [{auto_status}] Start on TUI launch"),
                ("autotelem", f"Auto Telemetry      [{telem_status}] Poll silent nodes"),
                ("save", "Save & Exit"),
                ("back", "Cancel"),
            ]

            choice = self.dialog.menu(
                "MQTT Configuration",
                "Configure MQTT broker connection:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "local":
                # Quick setup for local mosquitto broker
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
                self._save_mqtt_config(config)
                self.dialog.msgbox(
                    "Local Mode Set",
                    f"Configured for local mosquitto broker:\n\n"
                    f"  Broker: localhost:1883\n"
                    f"  Topic: {topic}\n"
                    f"  TLS: disabled\n\n"
                    "Make sure:\n"
                    "  1. Mosquitto is running (systemctl status mosquitto)\n"
                    "  2. Meshtasticd MQTT is configured\n\n"
                    "Use Service Config → MQTT Setup for full setup."
                )
                break

            elif choice == "public":
                # Quick setup for public Meshtastic broker
                config = {
                    'broker': 'mqtt.meshtastic.org',
                    'port': 8883,
                    'topic': 'msh/US/2/e/LongFast/#',
                    'username': 'meshdev',
                    'password': 'large4cats',
                    'use_tls': True
                }
                self._save_mqtt_config(config)
                self.dialog.msgbox(
                    "Public Mode Set",
                    "Configured for public Meshtastic broker:\n\n"
                    "  Broker: mqtt.meshtastic.org:8883\n"
                    "  Topic: msh/US/2/e/LongFast/#\n"
                    "  TLS: enabled\n\n"
                    "This is nodeless monitoring - no local radio needed."
                )
                break

            elif choice == "broker":
                new_broker = self.dialog.inputbox(
                    "MQTT Broker",
                    "Enter MQTT broker hostname:",
                    init=broker
                )
                if new_broker:
                    config['broker'] = new_broker

            elif choice == "port":
                new_port = self.dialog.inputbox(
                    "MQTT Port",
                    "Enter MQTT port (8883 for TLS, 1883 for plain):",
                    init=str(port)
                )
                if new_port and new_port.isdigit():
                    config['port'] = int(new_port)

            elif choice == "topic":
                new_topic = self.dialog.inputbox(
                    "MQTT Topic",
                    "Enter MQTT topic filter:\n"
                    "(Default: msh/US/2/e/LongFast/#)",
                    init=topic
                )
                if new_topic:
                    config['topic'] = new_topic

            elif choice == "auth":
                username = self.dialog.inputbox(
                    "Username",
                    "Enter MQTT username (blank for anonymous):",
                    init=config.get('username', '')
                )
                if username is not None:
                    config['username'] = username if username else None

                password = self.dialog.inputbox(
                    "Password",
                    "Enter MQTT password (blank for none):",
                    init=''  # Don't show existing password
                )
                if password is not None:
                    config['password'] = password if password else None

            elif choice == "autostart":
                # Toggle auto-start on TUI launch
                current = config.get('auto_start', False)
                config['auto_start'] = not current
                new_state = "ENABLED" if config['auto_start'] else "DISABLED"
                self.dialog.msgbox(
                    "Auto-Start",
                    f"MQTT auto-start: {new_state}\n\n"
                    "When enabled, MQTT subscriber will start\n"
                    "automatically when the TUI launches.\n\n"
                    "Save configuration to apply."
                )

            elif choice == "autotelem":
                # Toggle TelemetryPoller auto-start
                current = config.get('auto_start_telemetry', True)
                config['auto_start_telemetry'] = not current
                new_state = "ENABLED" if config['auto_start_telemetry'] else "DISABLED"
                self.dialog.msgbox(
                    "Auto Telemetry",
                    f"TelemetryPoller auto-start: {new_state}\n\n"
                    "When enabled (and MQTT auto-start is on),\n"
                    "the TelemetryPoller will poll silent 2.7+\n"
                    "nodes in the background.\n\n"
                    "Save configuration to apply."
                )

            elif choice == "save":
                self._save_mqtt_config(config)
                self.dialog.msgbox(
                    "Saved",
                    "MQTT configuration saved.\n\n"
                    "Restart the subscriber for changes to take effect."
                )
                break

    def _show_mqtt_nodes(self):
        """Show nodes discovered via MQTT."""
        nodes = []
        if self._mqtt_subscriber:
            # Use proper API to get nodes
            nodes = self._mqtt_subscriber.get_nodes()

        if not nodes:
            # Try to load from cache
            cache_data = self._load_mqtt_cache()
            if cache_data:
                # Cache is GeoJSON features, convert to display format
                nodes = cache_data
            else:
                self.dialog.msgbox(
                    "No Nodes",
                    "No MQTT nodes discovered yet.\n\n"
                    "Start the subscriber and wait for network activity."
                )
                return

        if not nodes:
            self.dialog.msgbox("No Nodes", "No nodes discovered yet.")
            return

        # Build node list for menu
        choices = []
        node_list = nodes[:50]  # Limit to 50 for display
        for i, node in enumerate(node_list):
            # Handle both MQTTNode objects and GeoJSON feature dicts
            if hasattr(node, 'long_name'):
                # MQTTNode object
                name = node.long_name or node.short_name or node.node_id
                last_seen = node.get_age_string()
                # Add health indicator if present
                health_ind = ""
                if hasattr(node, 'heart_bpm') and node.heart_bpm:
                    health_ind = " [H]"
            elif isinstance(node, dict):
                # GeoJSON feature from cache
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
            selected = self.dialog.menu(
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
            # MQTTNode object
            lines.append(f"NODE: {node.node_id}")
            lines.append("=" * 50)
            lines.append("")

            # Identity
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

            # Health Metrics (Meshtastic 2.7+)
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

            # Device Telemetry
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

            # Environment Metrics
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

            # Air Quality
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

            # Signal Quality
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

            # Position
            if node.latitude and node.longitude:
                lines.append("POSITION:")
                lines.append("-" * 50)
                lines.append(f"  Latitude:   {node.latitude:.6f}")
                lines.append(f"  Longitude:  {node.longitude:.6f}")
                if node.altitude:
                    lines.append(f"  Altitude:   {node.altitude}m")
                lines.append("")

            # Relay info (Meshtastic 2.6+)
            if hasattr(node, 'relay_node') and node.relay_node:
                lines.append("RELAY INFO:")
                lines.append("-" * 50)
                lines.append(f"  Relay Node: !...{node.relay_node:02x}")
                if hasattr(node, 'next_hop') and node.next_hop:
                    lines.append(f"  Next Hop:   !...{node.next_hop:02x}")
                lines.append("")

        else:
            # GeoJSON feature or other dict format
            props = node.get('properties', node)
            lines.append(f"NODE: {props.get('id', 'Unknown')}")
            lines.append("=" * 50)
            lines.append(f"Name: {props.get('name', 'Unknown')}")
            lines.append(f"Last Seen: {props.get('last_seen', 'Unknown')}")

        self.dialog.msgbox("Node Details", "\n".join(lines))

    def _show_mqtt_stats(self):
        """Show MQTT statistics."""
        lines = ["MQTT STATISTICS", "=" * 40, ""]

        if self._mqtt_subscriber:
            # Use proper API to get stats
            stats = self._mqtt_subscriber.get_stats()

            lines.append("NODE COUNTS:")
            lines.append(f"  Total nodes:      {stats.get('node_count', 0)}")
            lines.append(f"  Online (15 min):  {stats.get('online_count', 0)}")
            lines.append(f"  With position:    {stats.get('with_position', 0)}")
            lines.append("")

            # Sensor stats (Meshtastic 2.7+)
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

            # Mesh health warnings
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

            # Relay discovery stats (Meshtastic 2.6+)
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
            # Load from cache
            cache = self._load_mqtt_cache()
            if cache:
                lines.append(f"Cached nodes: {len(cache)}")
            else:
                lines.append("No data available.")
                lines.append("Start the MQTT subscriber to collect data.")

        self.dialog.msgbox("MQTT Statistics", "\n".join(lines))

    def _export_mqtt_data(self):
        """Export MQTT node data to file."""
        if not self._mqtt_subscriber and not self._load_mqtt_cache():
            self.dialog.msgbox("No Data", "No MQTT data to export.")
            return

        export_path = get_real_user_home() / ".local" / "share" / "meshforge" / "mqtt_export.json"

        try:
            if self._mqtt_subscriber:
                # Export from live data using proper API
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

            self.dialog.msgbox(
                "Export Complete",
                f"MQTT data exported to:\n{export_path}\n\n"
                f"Nodes exported: {len(nodes_data)}"
            )
        except Exception as e:
            self.dialog.msgbox("Export Error", f"Failed to export:\n{e}")

    def _load_mqtt_config(self) -> Dict[str, Any]:
        """Load MQTT configuration from file."""
        config_path = get_real_user_home() / ".config" / "meshforge" / "mqtt_nodeless.json"
        try:
            if config_path.exists():
                with open(config_path) as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"Error loading MQTT config: {e}")

        # Return defaults
        return {
            'broker': 'mqtt.meshtastic.org',
            'port': 8883,
            'topic': 'msh/US/2/e/LongFast/#',
            'username': None,
            'password': None
        }

    def _save_mqtt_config(self, config: Dict[str, Any]):
        """Save MQTT configuration to file."""
        config_path = get_real_user_home() / ".config" / "meshforge" / "mqtt_nodeless.json"
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving MQTT config: {e}")

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
            logger.debug(f"Error loading MQTT cache: {e}")
        return []

    def _detect_local_channel(self) -> Optional[str]:
        """Detect primary channel name from local meshtasticd.

        Returns channel name or None if detection fails.
        Used to construct correct MQTT topic for local broker.
        """
        import shutil
        try:
            cli = shutil.which('meshtastic') or 'meshtastic'
            result = subprocess.run(
                [cli, '--host', 'localhost', '--ch-index', '0', '--info'],
                capture_output=True, text=True, timeout=15
            )

            if result.returncode == 0:
                # Parse channel name from output
                for line in result.stdout.split('\n'):
                    if 'name' in line.lower():
                        parts = line.split(':')
                        if len(parts) >= 2:
                            name = parts[1].strip().strip('"\'')
                            if name and name.lower() not in ('none', ''):
                                logger.debug(f"Detected channel: {name}")
                                return name
        except Exception as e:
            logger.debug(f"Could not detect channel: {e}")

        return None

    def _request_telemetry_menu(self):
        """Request telemetry from silent Meshtastic 2.7+ nodes.

        Meshtastic 2.7.13+ no longer sends telemetry by default to reduce
        mesh congestion. This menu allows requesting telemetry from specific
        nodes or identifying silent nodes that haven't reported recently.
        """
        try:
            from utils.telemetry_poller import TelemetryPoller, get_telemetry_poller
            _HAS_POLLER = True
        except ImportError:
            _HAS_POLLER = False

        choices = [
            ("single", "Request from Node    Enter node ID manually"),
            ("silent", "Find Silent Nodes    Nodes with stale telemetry"),
            ("batch", "Poll Silent Nodes    Request from all silent"),
        ]

        if _HAS_POLLER:
            poller = get_telemetry_poller()
            stats = poller.get_stats()
            choices.append(("stats", f"Poller Statistics    {stats.get('total_requests', 0)} requests"))

        choices.append(("back", "Back"))

        while True:
            choice = self.dialog.menu(
                "Telemetry Requests",
                "Request telemetry from silent Meshtastic 2.7+ nodes.\n\n"
                "These nodes don't broadcast telemetry by default to reduce\n"
                "mesh congestion. Use this to poll them explicitly.",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "single":
                self._request_single_telemetry()
            elif choice == "silent":
                self._show_silent_nodes()
            elif choice == "batch":
                self._batch_telemetry_request()
            elif choice == "stats":
                self._show_poller_stats()

    def _request_single_telemetry(self):
        """Request telemetry from a single node by ID."""
        node_id = self.dialog.inputbox(
            "Request Telemetry",
            "Enter the Meshtastic node ID (e.g., !ba4bf9d0):",
            init="!"
        )

        if not node_id or node_id == "!":
            return

        # Validate format
        if not node_id.startswith('!'):
            node_id = f"!{node_id}"

        self.dialog.infobox("Requesting", f"Sending telemetry request to {node_id}...")

        try:
            from utils.telemetry_poller import get_telemetry_poller
            poller = get_telemetry_poller()
            success = poller.poll_node_now(node_id)

            if success:
                self.dialog.msgbox(
                    "Request Sent",
                    f"Telemetry request sent to {node_id}.\n\n"
                    "The node should respond within a few seconds.\n"
                    "Check MQTT Nodes view for updated data."
                )
            else:
                self.dialog.msgbox(
                    "Request Failed",
                    f"Failed to send telemetry request to {node_id}.\n\n"
                    "Possible reasons:\n"
                    "- meshtastic CLI not found\n"
                    "- Rate limited (max 4 requests/minute)\n"
                    "- meshtasticd not running"
                )
        except ImportError:
            self._fallback_telemetry_request(node_id)

    def _fallback_telemetry_request(self, node_id: str):
        """Fallback telemetry request using direct CLI call."""
        import shutil

        cli = shutil.which('meshtastic')
        if not cli:
            self.dialog.msgbox(
                "CLI Not Found",
                "meshtastic CLI not found.\n\n"
                "Install it with: pipx install meshtastic"
            )
            return

        try:
            result = subprocess.run(
                [cli, '--host', 'localhost', '--request-telemetry', '--dest', node_id],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                self.dialog.msgbox(
                    "Request Sent",
                    f"Telemetry request sent to {node_id}.\n\n"
                    f"Output:\n{result.stdout[:500] if result.stdout else 'No output'}"
                )
            else:
                self.dialog.msgbox(
                    "Request Failed",
                    f"Failed to request telemetry:\n{result.stderr[:500] if result.stderr else 'Unknown error'}"
                )
        except subprocess.TimeoutExpired:
            self.dialog.msgbox("Timeout", "Telemetry request timed out after 30 seconds.")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to request telemetry:\n{e}")

    def _show_silent_nodes(self):
        """Show nodes with stale or missing telemetry."""
        if not self._mqtt_subscriber:
            self.dialog.msgbox(
                "MQTT Not Running",
                "Start the MQTT subscriber first to discover nodes."
            )
            return

        nodes = self._mqtt_subscriber.get_nodes()
        if not nodes:
            self.dialog.msgbox("No Nodes", "No nodes discovered yet.")
            return

        try:
            from utils.telemetry_poller import TelemetryPoller
            poller = TelemetryPoller()

            # Build node list for the poller
            node_list = []
            for node in nodes:
                node_list.append({
                    'id': node.node_id,
                    'is_online': node.is_online(),
                    'telemetry_timestamp': node.last_seen  # Use last_seen as proxy
                })

            silent = poller.identify_silent_nodes(node_list, telemetry_age_threshold=1800)  # 30 min

            if not silent:
                self.dialog.msgbox(
                    "No Silent Nodes",
                    "All online nodes have recent telemetry.\n\n"
                    "Threshold: 30 minutes"
                )
                return

            # Display silent nodes
            lines = [
                "SILENT NODES (>30 min without telemetry)",
                "=" * 50,
                "",
            ]

            for node_id in silent[:20]:
                # Find the node to show more info
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

            self.dialog.msgbox("Silent Nodes", "\n".join(lines))

        except ImportError:
            self.dialog.msgbox(
                "Module Not Found",
                "TelemetryPoller module not available."
            )

    def _batch_telemetry_request(self):
        """Request telemetry from all silent nodes."""
        if not self._mqtt_subscriber:
            self.dialog.msgbox(
                "MQTT Not Running",
                "Start the MQTT subscriber first to discover nodes."
            )
            return

        if not self.dialog.yesno(
            "Confirm Batch Request",
            "This will send telemetry requests to all silent nodes.\n\n"
            "Requests are rate-limited to 4/minute to avoid\n"
            "congesting the mesh.\n\n"
            "Continue?"
        ):
            return

        nodes = self._mqtt_subscriber.get_nodes()
        if not nodes:
            self.dialog.msgbox("No Nodes", "No nodes discovered yet.")
            return

        try:
            from utils.telemetry_poller import get_telemetry_poller, TelemetryPoller

            poller = get_telemetry_poller()

            # Identify silent nodes
            node_list = []
            for node in nodes:
                node_list.append({
                    'id': node.node_id,
                    'is_online': node.is_online(),
                    'telemetry_timestamp': node.last_seen
                })

            silent = poller.identify_silent_nodes(node_list, telemetry_age_threshold=1800)

            if not silent:
                self.dialog.msgbox("No Silent Nodes", "No silent nodes to poll.")
                return

            # Poll nodes (rate limited)
            self.dialog.infobox("Polling", f"Sending requests to {min(5, len(silent))} nodes...")

            success_count = 0
            for node_id in silent[:5]:  # Max 5 per batch
                if poller.poll_node_now(node_id):
                    success_count += 1
                time.sleep(0.5)  # Brief pause between attempts

            self.dialog.msgbox(
                "Batch Complete",
                f"Telemetry requests sent: {success_count}/{min(5, len(silent))}\n\n"
                f"Total silent nodes: {len(silent)}\n"
                f"Rate limit: 4 requests/minute\n\n"
                "Run again to poll more nodes."
            )

        except ImportError:
            self.dialog.msgbox(
                "Module Not Found",
                "TelemetryPoller module not available."
            )

    def _show_poller_stats(self):
        """Show telemetry poller statistics."""
        try:
            from utils.telemetry_poller import get_telemetry_poller
            poller = get_telemetry_poller()
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

            self.dialog.msgbox("Poller Statistics", "\n".join(lines))

        except ImportError:
            self.dialog.msgbox(
                "Module Not Found",
                "TelemetryPoller module not available."
            )
