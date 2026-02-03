"""
MQTT Mixin - MQTT monitoring control for MeshForge TUI.

Provides:
- Start/stop MQTT subscriber
- Configure MQTT broker settings
- View MQTT node statistics
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


class MQTTMixin:
    """MQTT monitoring control for mesh networks."""

    # Class-level subscriber instance (shared across menu calls)
    _mqtt_subscriber: Optional[Any] = None
    _mqtt_thread: Optional[threading.Thread] = None

    def _mqtt_menu(self):
        """MQTT monitoring menu - nodeless mesh observation."""
        while True:
            # Get current status and mode
            status = self._get_mqtt_status()
            config = self._load_mqtt_config()
            broker = config.get('broker', 'mqtt.meshtastic.org')
            mode = "Local" if broker == "localhost" else "Public"

            choices = [
                ("status", f"Status              {status}"),
                ("start", "Start Subscriber    Connect to MQTT broker"),
                ("stop", "Stop Subscriber     Disconnect from broker"),
                ("config", f"Configure           Mode: {mode}"),
                ("nodes", "View Nodes          Show discovered nodes"),
                ("stats", "Statistics          Node counts, activity"),
                ("export", "Export Data         Save nodes to file"),
                ("back", "Back"),
            ]

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
            elif choice == "export":
                self._export_mqtt_data()

    def _get_mqtt_status(self) -> str:
        """Get current MQTT subscriber status."""
        if not _HAS_MQTT:
            return "Module unavailable"
        if self._mqtt_subscriber and self._mqtt_subscriber.is_connected():
            return "Connected"
        return "Not running"

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

            # Get subscriber stats
            node_count = len(self._mqtt_subscriber.nodes) if hasattr(self._mqtt_subscriber, 'nodes') else 0
            lines.append(f"Nodes discovered: {node_count}")

            # Get config info
            config = self._load_mqtt_config()
            if config:
                lines.append("")
                lines.append("CONFIGURATION:")
                lines.append(f"  Broker: {config.get('broker', 'mqtt.meshtastic.org')}")
                lines.append(f"  Port: {config.get('port', 8883)}")
                lines.append(f"  Topic: {config.get('topic', 'msh/US/2/e/LongFast/#')}")
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
            self._mqtt_subscriber = MQTTNodelessSubscriber(
                broker=config.get('broker', 'mqtt.meshtastic.org'),
                port=config.get('port', 8883),
                topic=config.get('topic', 'msh/US/2/e/LongFast/#'),
                username=config.get('username'),
                password=config.get('password')
            )

            # Start in background thread
            self._mqtt_thread = threading.Thread(
                target=self._mqtt_subscriber.run,
                daemon=True
            )
            self._mqtt_thread.start()

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

        if self.dialog.yesno(
            "Stop MQTT",
            "Stop the MQTT subscriber?\n\n"
            "Node data will be preserved in cache."
        ):
            try:
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

            choices = [
                ("local", f"Use Local Broker    Quick: localhost:1883"),
                ("public", f"Use Public Broker   Quick: mqtt.meshtastic.org"),
                ("broker", f"Broker              {broker}"),
                ("port", f"Port                {port}"),
                ("topic", f"Topic               {topic[:30]}..."),
                ("auth", "Authentication      Username/password"),
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
        if not self._mqtt_subscriber or not hasattr(self._mqtt_subscriber, 'nodes'):
            # Try to load from cache
            nodes = self._load_mqtt_cache()
            if not nodes:
                self.dialog.msgbox(
                    "No Nodes",
                    "No MQTT nodes discovered yet.\n\n"
                    "Start the subscriber and wait for network activity."
                )
                return
        else:
            nodes = list(self._mqtt_subscriber.nodes.values())

        if not nodes:
            self.dialog.msgbox("No Nodes", "No nodes discovered yet.")
            return

        # Build node list for menu
        choices = []
        for i, node in enumerate(nodes[:50]):  # Limit to 50 for display
            name = getattr(node, 'name', None) or getattr(node, 'id', f'Node {i}')
            network = getattr(node, 'network', 'mqtt')
            last_seen = getattr(node, 'last_seen', 'unknown')
            choices.append((str(i), f"{name[:20]:<20} {network} ({last_seen})"))

        if len(nodes) > 50:
            choices.append(("more", f"... and {len(nodes) - 50} more nodes"))

        self.dialog.menu(
            f"MQTT Nodes ({len(nodes)})",
            "Nodes discovered via MQTT monitoring:",
            choices
        )

    def _show_mqtt_stats(self):
        """Show MQTT statistics."""
        lines = ["MQTT STATISTICS", "=" * 40, ""]

        if self._mqtt_subscriber and hasattr(self._mqtt_subscriber, 'nodes'):
            nodes = self._mqtt_subscriber.nodes

            # Count by network type
            meshtastic = sum(1 for n in nodes.values() if getattr(n, 'network', '') == 'meshtastic')
            online = sum(1 for n in nodes.values() if getattr(n, 'is_online', False))

            lines.append(f"Total nodes: {len(nodes)}")
            lines.append(f"Online: {online}")
            lines.append(f"Meshtastic: {meshtastic}")
            lines.append("")

            # Recent activity
            now = time.time()
            recent_1h = sum(1 for n in nodes.values()
                          if hasattr(n, 'last_heard') and (now - n.last_heard) < 3600)
            lines.append(f"Active in last hour: {recent_1h}")
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
            if self._mqtt_subscriber and hasattr(self._mqtt_subscriber, 'nodes'):
                # Export from live data
                nodes_data = []
                for node in self._mqtt_subscriber.nodes.values():
                    nodes_data.append({
                        'id': getattr(node, 'id', ''),
                        'name': getattr(node, 'name', ''),
                        'network': getattr(node, 'network', 'mqtt'),
                        'lat': getattr(node, 'lat', None),
                        'lon': getattr(node, 'lon', None),
                        'last_seen': getattr(node, 'last_seen', ''),
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
