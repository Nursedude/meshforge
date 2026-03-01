"""
MeshCore Handler — MeshCore companion radio management.

Converted from meshcore_mixin.py as part of the mixin-to-registry migration.
"""

import logging

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

_detect_meshcore_devices, _HAS_DETECT = safe_import(
    'gateway.meshcore_handler', 'detect_meshcore_devices'
)
_GatewayConfig, _MeshCoreConfig, _HAS_GW_CONFIG = safe_import(
    'gateway.config', 'GatewayConfig', 'MeshCoreConfig'
)
_get_node_tracker, _HAS_NODE_TRACKER = safe_import(
    'gateway.node_tracker', 'get_node_tracker'
)
_is_gateway_running, _get_gateway_stats, _HAS_GW_CLI = safe_import(
    'gateway.gateway_cli', 'is_gateway_running', 'get_gateway_stats'
)


class MeshCoreHandler(BaseHandler):
    """TUI handler for MeshCore companion radio management."""

    handler_id = "meshcore"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("meshcore", "MeshCore            Companion radio, config", "meshcore"),
        ]

    def execute(self, action):
        if action == "meshcore":
            self._meshcore_menu()

    def _meshcore_menu(self):
        """MeshCore companion radio setup and monitoring."""
        while True:
            status_line = self._meshcore_status_line()

            choices = [
                ("status", "Connection Status   MeshCore radio state"),
                ("detect", "Detect Devices      Scan for serial devices"),
                ("config", "Configure           Connection settings"),
                ("enable", "Enable/Disable      Toggle MeshCore in gateway"),
                ("nodes", "View Nodes          MeshCore network nodes"),
                ("stats", "Statistics          Message & connection stats"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "MeshCore Radio",
                status_line,
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "status": ("MeshCore Status", self._meshcore_status),
                "detect": ("Detect Devices", self._meshcore_detect),
                "config": ("MeshCore Config", self._meshcore_configure),
                "enable": ("Enable/Disable", self._meshcore_toggle),
                "nodes": ("MeshCore Nodes", self._meshcore_nodes),
                "stats": ("MeshCore Stats", self._meshcore_stats),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _meshcore_status_line(self) -> str:
        """Build status line for MeshCore menu subtitle."""
        if not _HAS_GW_CONFIG:
            return "MeshCore companion radio management"

        try:
            config = _GatewayConfig.load()
            mc = getattr(config, 'meshcore', None)
            if not mc or not mc.enabled:
                return "MeshCore: DISABLED in gateway config"
            conn = mc.connection_type
            device = mc.device_path if conn == "serial" else f"{mc.tcp_host}:{mc.tcp_port}"
            return f"MeshCore: ENABLED ({conn} -> {device})"
        except Exception:
            return "MeshCore companion radio management"

    def _meshcore_status(self):
        """Show MeshCore connection status."""
        clear_screen()
        print("=== MeshCore Connection Status ===\n")

        if not _HAS_GW_CONFIG:
            print("  Gateway config module not available.")
            self.ctx.wait_for_enter()
            return

        try:
            config = _GatewayConfig.load()
        except Exception as e:
            print(f"  Could not load gateway config: {e}")
            self.ctx.wait_for_enter()
            return

        mc = getattr(config, 'meshcore', None)
        if not mc:
            print("  MeshCore not configured.")
            print("  Use 'Configure' to set up connection.")
            self.ctx.wait_for_enter()
            return

        print(f"  Enabled:          {'Yes' if mc.enabled else 'No'}")
        print(f"  Connection Type:  {mc.connection_type}")
        if mc.connection_type == "serial":
            print(f"  Device Path:      {mc.device_path}")
            print(f"  Baud Rate:        {mc.baud_rate}")

            import os
            exists = os.path.exists(mc.device_path)
            print(f"  Device Present:   {'Yes' if exists else 'No (not plugged in?)'}")
        elif mc.connection_type == "tcp":
            print(f"  TCP Host:         {mc.tcp_host}")
            print(f"  TCP Port:         {mc.tcp_port}")
        print(f"  Bridge Channels:  {'Yes' if mc.bridge_channels else 'No'}")
        print(f"  Bridge DMs:       {'Yes' if mc.bridge_dms else 'No'}")
        print(f"  Simulation Mode:  {'Yes' if mc.simulation_mode else 'No'}")
        print(f"  Auto-Fetch Msgs:  {'Yes' if mc.auto_fetch_messages else 'No'}")

        try:
            import meshcore as _mc_check  # noqa: F401
            print(f"\n  meshcore_py:      Installed")
        except ImportError:
            print(f"\n  meshcore_py:      NOT installed")
            print(f"  Install:          pip install meshcore")

        self.ctx.wait_for_enter()

    def _meshcore_detect(self):
        """Scan for MeshCore-compatible serial devices."""
        clear_screen()
        print("=== MeshCore Device Detection ===\n")

        if not _HAS_DETECT:
            print("  Device detection module not available.")
            self.ctx.wait_for_enter()
            return

        devices = _detect_meshcore_devices()

        if not devices:
            print("  No serial devices found.")
            print("\n  Check:")
            print("  - Is the radio plugged in via USB?")
            print("  - Does it show up with: ls /dev/ttyUSB* /dev/ttyACM*")
            print("  - Is the user in the 'dialout' group?")
            self.ctx.wait_for_enter()
            return

        print(f"  Found {len(devices)} serial device(s):\n")
        for i, dev in enumerate(devices, 1):
            print(f"  {i}. {dev}")

        print("\n  Note: These are serial ports that MAY be MeshCore radios.")
        print("  Verify by connecting and checking firmware response.")

        if _HAS_GW_CONFIG and len(devices) >= 1:
            print(f"\n  Set {devices[0]} as MeshCore device? (Configure menu)")

        self.ctx.wait_for_enter()

    def _meshcore_configure(self):
        """Configure MeshCore connection settings."""
        if not _HAS_GW_CONFIG:
            self.ctx.dialog.msgbox(
                "Module Missing",
                "Gateway configuration module not found.\n\n"
                "Ensure src/gateway/config.py exists."
            )
            return

        try:
            config = _GatewayConfig.load()
        except Exception:
            config = _GatewayConfig()

        mc = getattr(config, 'meshcore', None)
        if mc is None:
            mc = _MeshCoreConfig()
            config.meshcore = mc

        while True:
            choices = [
                ("type", f"Connection Type     {mc.connection_type}"),
                ("device", f"Device Path         {mc.device_path}"),
                ("baud", f"Baud Rate           {mc.baud_rate}"),
                ("tcp_host", f"TCP Host            {mc.tcp_host or '(not set)'}"),
                ("tcp_port", f"TCP Port            {mc.tcp_port}"),
                ("channels", f"Bridge Channels     {'Yes' if mc.bridge_channels else 'No'}"),
                ("dms", f"Bridge DMs          {'Yes' if mc.bridge_dms else 'No'}"),
                ("sim", f"Simulation Mode     {'Yes' if mc.simulation_mode else 'No'}"),
                ("save", "Save Configuration"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "MeshCore Configuration",
                "Configure MeshCore companion radio connection:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "type":
                type_choice = self.ctx.dialog.menu(
                    "Connection Type",
                    "How is the MeshCore radio connected?",
                    [
                        ("serial", "USB Serial          Direct USB connection"),
                        ("tcp", "TCP                 Network connection"),
                        ("ble", "Bluetooth LE        BLE connection"),
                    ]
                )
                if type_choice:
                    mc.connection_type = type_choice

            elif choice == "device":
                devices = []
                if _HAS_DETECT:
                    devices = _detect_meshcore_devices()

                if devices:
                    dev_choices = [(d, d) for d in devices]
                    dev_choices.append(("custom", "Enter custom path"))
                    selected = self.ctx.dialog.menu(
                        "Select Device",
                        "Detected serial devices:",
                        dev_choices
                    )
                    if selected and selected != "custom":
                        mc.device_path = selected
                    elif selected == "custom":
                        path = self.ctx.dialog.inputbox(
                            "Device Path",
                            "Enter serial device path:",
                            mc.device_path
                        )
                        if path:
                            mc.device_path = path
                else:
                    path = self.ctx.dialog.inputbox(
                        "Device Path",
                        "No devices detected. Enter path manually:",
                        mc.device_path
                    )
                    if path:
                        mc.device_path = path

            elif choice == "baud":
                baud = self.ctx.dialog.inputbox(
                    "Baud Rate",
                    "Enter baud rate (typically 115200):",
                    str(mc.baud_rate)
                )
                if baud:
                    try:
                        mc.baud_rate = int(baud)
                    except ValueError:
                        self.ctx.dialog.msgbox("Invalid Input", "Baud rate must be a number.")

            elif choice == "tcp_host":
                host = self.ctx.dialog.inputbox(
                    "TCP Host",
                    "Enter TCP host for MeshCore connection:",
                    mc.tcp_host or "localhost"
                )
                if host and self.ctx.validate_hostname(host):
                    mc.tcp_host = host
                elif host:
                    self.ctx.dialog.msgbox("Invalid Host", "Invalid hostname or IP address.")

            elif choice == "tcp_port":
                port = self.ctx.dialog.inputbox(
                    "TCP Port",
                    "Enter TCP port (default 4000):",
                    str(mc.tcp_port)
                )
                if port and self.ctx.validate_port(port):
                    mc.tcp_port = int(port)
                elif port:
                    self.ctx.dialog.msgbox("Invalid Port", "Port must be 1-65535.")

            elif choice == "channels":
                mc.bridge_channels = not mc.bridge_channels

            elif choice == "dms":
                mc.bridge_dms = not mc.bridge_dms

            elif choice == "sim":
                mc.simulation_mode = not mc.simulation_mode

            elif choice == "save":
                try:
                    config.save()
                    self.ctx.dialog.msgbox(
                        "Saved",
                        "MeshCore configuration saved.\n\n"
                        "Restart the gateway bridge for changes to take effect."
                    )
                except Exception as e:
                    self.ctx.dialog.msgbox("Save Error", f"Could not save config:\n\n{e}")

    def _meshcore_toggle(self):
        """Enable or disable MeshCore in gateway config."""
        if not _HAS_GW_CONFIG:
            self.ctx.dialog.msgbox(
                "Module Missing",
                "Gateway configuration module not found."
            )
            return

        try:
            config = _GatewayConfig.load()
        except Exception:
            config = _GatewayConfig()

        mc = getattr(config, 'meshcore', None)
        if mc is None:
            mc = _MeshCoreConfig()
            config.meshcore = mc

        mc.enabled = not mc.enabled
        action = "enabled" if mc.enabled else "disabled"

        try:
            config.save()
            self.ctx.dialog.msgbox(
                f"MeshCore {action.title()}",
                f"MeshCore is now {action}.\n\n"
                f"Restart the gateway bridge for changes to take effect."
            )
        except Exception as e:
            self.ctx.dialog.msgbox("Save Error", f"Could not save config:\n\n{e}")

    def _meshcore_nodes(self):
        """Show MeshCore nodes from the live node tracker."""
        clear_screen()
        print("=== MeshCore Nodes ===\n")

        if not _HAS_NODE_TRACKER:
            print("  Node tracker module not available.")
            self.ctx.wait_for_enter()
            return

        try:
            tracker = _get_node_tracker()
            nodes = tracker.get_meshcore_nodes()
        except Exception as e:
            print(f"  Error reading node tracker: {e}")
            self.ctx.wait_for_enter()
            return

        if not nodes:
            print("  No MeshCore nodes discovered yet.\n")
            print("  Nodes appear when the gateway bridge is running")
            print("  with MeshCore enabled and a radio connected.")
            self.ctx.wait_for_enter()
            return

        print(f"  {len(nodes)} node(s) discovered:\n")
        for node in sorted(nodes, key=lambda n: n.name or n.id):
            name = node.name or node.short_name or "(unnamed)"
            status = "ONLINE" if node.is_online else "offline"
            role = node.meshcore_role or ""
            hops = f"hops:{node.meshcore_hops}" if node.meshcore_hops is not None else ""

            signal = ""
            if node.rssi is not None:
                signal = f"RSSI:{node.rssi}"
            if node.snr is not None:
                signal += f" SNR:{node.snr:.1f}"

            last = ""
            if node.last_seen:
                delta = (__import__('datetime').datetime.now() - node.last_seen).total_seconds()
                if delta < 60:
                    last = f"{int(delta)}s ago"
                elif delta < 3600:
                    last = f"{int(delta / 60)}m ago"
                else:
                    last = f"{delta / 3600:.1f}h ago"

            detail = "  ".join(filter(None, [role, hops, signal, last]))
            print(f"  {name:<20s} [{status}]  {detail}")
            if node.meshcore_pubkey:
                print(f"    pubkey: {node.meshcore_pubkey}")

        self.ctx.wait_for_enter()

    def _meshcore_stats(self):
        """Show MeshCore statistics from the live bridge."""
        clear_screen()
        print("=== MeshCore Statistics ===\n")

        if not _HAS_GW_CLI:
            print("  Gateway CLI module not available.")
            self.ctx.wait_for_enter()
            return

        if not _is_gateway_running():
            print("  Gateway bridge is not running.\n")
            print("  Start the bridge to collect MeshCore statistics.")
            self.ctx.wait_for_enter()
            return

        try:
            gw_stats = _get_gateway_stats()
        except Exception as e:
            print(f"  Error reading gateway stats: {e}")
            self.ctx.wait_for_enter()
            return

        stats = gw_stats.get('statistics', gw_stats)
        connected = gw_stats.get('meshcore_connected', False)

        print(f"  Connection:  {'CONNECTED' if connected else 'DISCONNECTED'}")
        print(f"  Bridge:      {gw_stats.get('status', 'unknown')}\n")

        print(f"  Messages RX:    {stats.get('meshcore_rx', 0)}")
        print(f"  Messages TX:    {stats.get('meshcore_tx', 0)}")
        print(f"  Delivery ACKs:  {stats.get('meshcore_acks', 0)}")

        mc_to_mesh = stats.get('messages_meshcore_to_mesh', 0)
        mc_to_rns = stats.get('messages_meshcore_to_rns', 0)
        mesh_to_mc = stats.get('messages_mesh_to_meshcore', 0)
        rns_to_mc = stats.get('messages_rns_to_meshcore', 0)
        if any([mc_to_mesh, mc_to_rns, mesh_to_mc, rns_to_mc]):
            print(f"\n  Bridged:")
            print(f"    MeshCore -> Meshtastic:  {mc_to_mesh}")
            print(f"    MeshCore -> RNS:         {mc_to_rns}")
            print(f"    Meshtastic -> MeshCore:  {mesh_to_mc}")
            print(f"    RNS -> MeshCore:         {rns_to_mc}")

        errors = stats.get('errors', 0)
        bounced = stats.get('bounced', 0)
        if errors or bounced:
            print(f"\n  Errors:   {errors}")
            print(f"  Bounced:  {bounced}")

        uptime = gw_stats.get('uptime_seconds')
        if uptime:
            h, rem = divmod(int(uptime), 3600)
            m, s = divmod(rem, 60)
            print(f"\n  Uptime: {h}h {m}m {s}s")

        self.ctx.wait_for_enter()
