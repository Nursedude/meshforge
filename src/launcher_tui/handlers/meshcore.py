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
_test_meshcore_connection, _send_meshcore_message, _get_meshcore_status, _HAS_MC_OPS = safe_import(
    'gateway.gateway_cli',
    'test_meshcore_connection', 'send_meshcore_message', 'get_meshcore_status'
)
_DiagnosticEngine, _HAS_DIAG_ENGINE = safe_import(
    'core.diagnostics.engine', 'DiagnosticEngine'
)
_CheckCategory, _HAS_CHECK_CATEGORY = safe_import(
    'core.diagnostics.models', 'CheckCategory'
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
        choices = [
            ("status", "Connection Status   MeshCore radio state"),
            ("test", "Test Connection     Probe MeshCore device"),
            ("detect", "Detect Devices      Scan for serial devices"),
            ("config", "Configure           Connection settings"),
            ("enable", "Enable/Disable      Toggle MeshCore in gateway"),
            ("nodes", "View Nodes          MeshCore network nodes"),
            ("send", "Send Message        Send via MeshCore bridge"),
            ("stats", "Statistics          Message & connection stats"),
            ("live", "Live Monitor        Auto-refresh bridge stats"),
            ("diag", "Run Diagnostics     MeshCore health checks"),
        ]
        dispatch = {
            "status": ("MeshCore Status", self._meshcore_status),
            "test": ("Test Connection", self._meshcore_test_connection),
            "detect": ("Detect Devices", self._meshcore_detect),
            "config": ("MeshCore Config", self._meshcore_configure),
            "enable": ("Enable/Disable", self._meshcore_toggle),
            "nodes": ("MeshCore Nodes", self._meshcore_nodes),
            "send": ("Send Message", self._meshcore_send_message),
            "stats": ("MeshCore Stats", self._meshcore_stats),
            "live": ("Live Monitor", self._meshcore_live_monitor),
            "diag": ("MeshCore Diagnostics", self._meshcore_run_diagnostics),
        }
        self.run_menu_loop(
            "MeshCore Radio", "MeshCore companion radio management:",
            choices, dispatch,
        )

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
            version = getattr(_mc_check, '__version__', 'unknown')
            print(f"\n  meshcore_py:      Installed (v{version})")
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

    # ------------------------------------------------------------------
    # B1: Live operations (wired to gateway_cli APIs)
    # ------------------------------------------------------------------

    def _meshcore_test_connection(self):
        """Test MeshCore device connectivity without starting the bridge."""
        clear_screen()
        print("=== MeshCore Connection Test ===\n")

        if not _HAS_MC_OPS:
            print("  Gateway CLI module not available.")
            self.ctx.wait_for_enter()
            return

        print("  Testing device connection...\n")
        result = _test_meshcore_connection()

        conn = result.get('connection_type', 'unknown')
        print(f"  Connection Type:  {conn}")

        if result.get('reachable'):
            print(f"  Status:           REACHABLE")
            print(f"  Detail:           {result.get('detail', '')}")
        else:
            print(f"  Status:           UNREACHABLE")
            error = result.get('error', 'Unknown error')
            print(f"  Error:            {error}")

            if 'not found' in str(error).lower():
                print("\n  Suggestions:")
                print("  - Check USB cable connection")
                print("  - Verify device path in MeshCore config")
                print("  - Run 'Detect Devices' to scan for serial ports")
            elif 'refused' in str(error).lower():
                print("\n  Suggestions:")
                print("  - Check that the radio firmware supports TCP")
                print("  - Verify host and port in MeshCore config")

        self.ctx.wait_for_enter()

    def _meshcore_send_message(self):
        """Send a text message through MeshCore via the running bridge."""
        if not _HAS_MC_OPS:
            self.ctx.dialog.msgbox(
                "Module Missing",
                "Gateway CLI module not available."
            )
            return

        if not _HAS_GW_CLI or not _is_gateway_running():
            self.ctx.dialog.msgbox(
                "Bridge Not Running",
                "The gateway bridge must be running to send messages.\n\n"
                "Start the bridge from the Gateway menu first."
            )
            return

        # Get message text
        text = self.ctx.dialog.inputbox(
            "Send MeshCore Message",
            "Enter message text:",
            ""
        )
        if not text:
            return

        # Optional destination
        dest = self.ctx.dialog.inputbox(
            "Destination",
            "Enter destination node ID (leave empty for broadcast):",
            ""
        )
        if dest == "":
            dest = None

        self.ctx.dialog.infobox("Sending", "Sending message via MeshCore...")

        result = _send_meshcore_message(text, destination=dest)

        if result.get('sent'):
            target = dest or "broadcast"
            self.ctx.dialog.msgbox(
                "Message Sent",
                f"Message sent to {target} via MeshCore.\n\n"
                f"Text: {text[:80]}"
            )
        else:
            error = result.get('error', 'Unknown error')
            self.ctx.dialog.msgbox(
                "Send Failed",
                f"Failed to send message:\n\n{error}"
            )

    def _meshcore_live_monitor(self):
        """Auto-refreshing MeshCore bridge statistics display."""
        import threading

        if not _HAS_GW_CLI:
            self.ctx.dialog.msgbox("Error", "Gateway CLI module not available.")
            return

        clear_screen()
        print("=== MeshCore Live Monitor ===")
        print("  (Press Ctrl+C to return)\n")

        _stop_event = threading.Event()

        try:
            while not _stop_event.is_set():
                stats = _get_gateway_stats() if _is_gateway_running() else {}
                mc_status = _get_meshcore_status() if _HAS_MC_OPS else {}

                # Move cursor to line 4 for refresh (keep header)
                print("\033[4;1H\033[J", end="")  # ANSI: move to row 4, clear below

                if not stats.get('running', False):
                    print("  Bridge:       NOT RUNNING")
                    print("\n  Start the gateway bridge to see live stats.")
                else:
                    connected = mc_status.get('connected', False)
                    print(f"  Bridge:       RUNNING")
                    print(f"  MeshCore:     {'CONNECTED' if connected else 'DISCONNECTED'}")
                    print(f"  Device:       {mc_status.get('device', 'N/A')}")
                    print(f"  Nodes:        {mc_status.get('nodes_discovered', 0)}")

                    inner = stats.get('statistics', stats)
                    print(f"\n  Messages RX:  {inner.get('meshcore_rx', mc_status.get('rx', 0))}")
                    print(f"  Messages TX:  {inner.get('meshcore_tx', mc_status.get('tx', 0))}")

                    mesh_ok = stats.get('meshtastic_connected', False)
                    rns_ok = stats.get('rns_connected', False)
                    print(f"\n  Meshtastic:   {'OK' if mesh_ok else 'DOWN'}")
                    print(f"  RNS:          {'OK' if rns_ok else 'DOWN'}")

                    errors = inner.get('errors', 0)
                    if errors:
                        print(f"\n  Errors:       {errors}")

                    uptime = stats.get('uptime_seconds')
                    if uptime:
                        h, rem = divmod(int(uptime), 3600)
                        m, s = divmod(rem, 60)
                        print(f"  Uptime:       {h}h {m}m {s}s")

                print(f"\n  Last refresh: {__import__('datetime').datetime.now().strftime('%H:%M:%S')}")

                _stop_event.wait(2)
        except KeyboardInterrupt:
            _stop_event.set()

    def _meshcore_run_diagnostics(self):
        """Run MeshCore diagnostic checks and display results."""
        clear_screen()
        print("=== MeshCore Diagnostics ===\n")

        if not _HAS_DIAG_ENGINE or not _HAS_CHECK_CATEGORY:
            print("  Diagnostic engine not available.")
            self.ctx.wait_for_enter()
            return

        print("  Running checks...\n")

        try:
            engine = _DiagnosticEngine.get_instance()
            results = engine.run_category(_CheckCategory.MESHCORE)
        except Exception as e:
            print(f"  Error running diagnostics: {e}")
            self.ctx.wait_for_enter()
            return

        if not results:
            print("  No MeshCore checks available.")
            self.ctx.wait_for_enter()
            return

        # Status symbols
        symbols = {
            'pass': '[PASS]',
            'fail': '[FAIL]',
            'warn': '[WARN]',
            'skip': '[SKIP]',
        }

        passed = 0
        failed = 0
        for r in results:
            sym = symbols.get(r.status.value, '[????]')
            print(f"  {sym}  {r.name:<25s} {r.message}")
            if r.fix_hint:
                print(f"          Fix: {r.fix_hint}")
            if r.status.value == 'pass':
                passed += 1
            elif r.status.value == 'fail':
                failed += 1

        print(f"\n  Summary: {passed} passed, {failed} failed, "
              f"{len(results) - passed - failed} warnings/skipped")

        self.ctx.wait_for_enter()
