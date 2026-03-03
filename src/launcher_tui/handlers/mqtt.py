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
import threading
import time
from typing import Optional, Dict, Any

from handler_protocol import BaseHandler, PRIVILEGE_ADMIN
from utils.safe_import import safe_import
from utils.paths import get_real_user_home
from . import _mqtt_telemetry

logger = logging.getLogger(__name__)

# Try to import the MQTT subscriber
MQTTNodelessSubscriber, _HAS_MQTT = safe_import(
    'monitoring.mqtt_subscriber', 'MQTTNodelessSubscriber'
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
    admin_tags = frozenset({"broker"})

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
        def _mqtt_choices():
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
            items = [
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
                items.append(("websocket", f"WebSocket Bridge    {ws_status}"))
            return items

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
        self.run_menu_loop("MQTT Monitoring", "MQTT mesh monitoring:", _mqtt_choices, dispatch)

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

    # -- Thin wrappers delegating to _mqtt_telemetry helpers --

    def _show_mqtt_nodes(self):
        """Show nodes discovered via MQTT."""
        _mqtt_telemetry.show_mqtt_nodes(self)

    def _show_mqtt_node_details(self, node):
        """Show detailed information for an MQTT-discovered node."""
        _mqtt_telemetry.show_mqtt_node_details(self, node)

    def _show_mqtt_stats(self):
        """Show MQTT statistics."""
        _mqtt_telemetry.show_mqtt_stats(self)

    def _export_mqtt_data(self):
        """Export MQTT node data to file."""
        _mqtt_telemetry.export_mqtt_data(self)

    def _load_mqtt_cache(self) -> list:
        """Load cached MQTT nodes from file."""
        return _mqtt_telemetry.load_mqtt_cache()

    def _detect_local_channel(self) -> Optional[str]:
        """Detect primary channel name from local meshtasticd."""
        return _mqtt_telemetry.detect_local_channel()

    def _configure_private_broker(self, config: Dict[str, Any]):
        """Guided setup for a private MQTT broker."""
        _mqtt_telemetry.configure_private_broker(self, config)

    def _request_telemetry_menu(self):
        """Request telemetry from silent Meshtastic 2.7+ nodes."""
        _mqtt_telemetry.request_telemetry_menu(self)

    def _request_single_telemetry(self):
        """Request telemetry from a single node by ID."""
        _mqtt_telemetry.request_single_telemetry(self)

    def _fallback_telemetry_request(self, node_id: str):
        """Fallback telemetry request using direct CLI call."""
        _mqtt_telemetry.fallback_telemetry_request(self, node_id)

    def _show_silent_nodes(self):
        """Show nodes with stale or missing telemetry."""
        _mqtt_telemetry.show_silent_nodes(self)

    def _batch_telemetry_request(self):
        """Request telemetry from all silent nodes."""
        _mqtt_telemetry.batch_telemetry_request(self)

    def _show_poller_stats(self):
        """Show telemetry poller statistics."""
        _mqtt_telemetry.show_poller_stats(self)
