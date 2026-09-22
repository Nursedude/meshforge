"""
MeshCore Handler — MeshCore companion radio management.

Converted from meshcore_mixin.py as part of the mixin-to-registry migration.
"""

import json
import logging
import time
import urllib.error
import urllib.request

from backend import clear_screen
from handler_protocol import BaseHandler
from handlers._meshcore_contacts import MeshCoreContactsMixin
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


class MeshCoreHandler(MeshCoreContactsMixin, BaseHandler):
    """TUI handler for MeshCore companion radio management."""

    handler_id = "meshcore"
    menu_section = "mesh_networks"

    # The gateway PROCESS's own listener (utils.metrics_server binds
    # 127.0.0.1:9090 inside bridge_cli). ``/api/json/meshcore`` there is the
    # only surface that can answer for the radio, the oracle and the bridge
    # counters from OUTSIDE the gateway process (roadmap 1e, 2026-09-22).
    STATUS_API_BASE = "http://127.0.0.1:9090"
    STATUS_TIMEOUT = 5
    FW_BRIEF_TTL = 30.0

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
                ("contacts", "View Contacts       Radio's contact table + last heard"),
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
                "contacts": ("MeshCore Contacts", self._meshcore_contacts),
                "stats": ("MeshCore Stats", self._meshcore_stats),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
            else:
                self.ctx.notify_unwired(choice, "MeshCoreHandler._meshcore_menu")

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
            # Roadmap 1c: the firmware fact rides the landing subtitle so
            # "what is this radio running?" costs zero menus. Cached +
            # short-timeout (_meshcore_fw_brief) — the subtitle is rebuilt
            # on every redraw, so it must never block the menu.
            return (f"MeshCore: ENABLED ({conn} -> {device}) | "
                    f"{self._meshcore_fw_brief()}")
        except Exception:
            # Distinct from the no-module neutral subtitle above: a config-read
            # failure must not masquerade as "feature unavailable" (S7, #74-#77).
            return "MeshCore: status unavailable (config read failed)"

    # ── gateway status API (roadmap 1e) ─────────────────────────────────

    def _status_fetch(self, timeout=None):
        """``(payload, error)`` from ``GET /api/json/meshcore`` on the gateway.

        ``(None, reason)`` on any failure — unreachable is UNKNOWN, never
        "off": the gateway may simply not be running on this box.
        """
        try:
            req = urllib.request.Request(
                f"{self.STATUS_API_BASE}/api/json/meshcore",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(
                    req, timeout=timeout or self.STATUS_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # The listener ANSWERED. A 404 is a gateway process still on a
            # build that predates this route — "unreachable" would send the
            # operator to check the network for a process that is up.
            return None, self._older_gateway_reason(e)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
            return None, str(e)
        if not isinstance(payload, dict):
            return None, "response was not a JSON object"
        return payload, None

    @staticmethod
    def _older_gateway_reason(err) -> str:
        code = getattr(err, "code", None)
        if code == 404:
            return ("gateway is up but predates this pane (HTTP 404) - restart "
                    "it to load the current code: Service Control -> "
                    "meshforge-gateway -> Restart")
        return f"gateway answered HTTP {code}"

    # ── firmware brief (roadmap 1c) ─────────────────────────────────────
    #
    # ⚠️ What the radio actually reports (measured on a RAK, 2026-09-21) —
    # neither field is a release version:
    #   fw_build  '19-Apr-2026'  a BUILD DATE string
    #   fw_ver    11             the COMPANION PROTOCOL version byte the
    #                            library feature-gates on. Not firmware 11.
    # So this brief says "build" and "proto" and never "version": calling
    # fw_ver a version reads as "this radio runs 11" against an actual
    # release of 1.15.0 — a confident wrong label on the exact fact the
    # operator is deciding a flash against.

    def _meshcore_fw_brief(self) -> str:
        """One short clause for the menu subtitle. Never raises.

        Cached for FW_BRIEF_TTL because the subtitle is rebuilt on every
        menu redraw; without it, sitting on the menu would poll the gateway
        once per keystroke.
        """
        now = time.monotonic()
        cached = getattr(self, "_fw_brief_cache", None)
        if cached and now - cached[0] < self.FW_BRIEF_TTL:
            return cached[1]

        try:
            payload, err = self._status_fetch(timeout=1.5)
        except Exception:
            payload, err = None, "fetch failed"

        if payload is None:
            # Unreachable is UNKNOWN, never "no firmware" — an absent
            # answer must not render as an answer.
            brief = "firmware ? (gateway unreachable)"
        elif not payload.get("observable", False):
            brief = "firmware ? (no bridge in gateway)"
        else:
            dev = payload.get("device") or {}
            if not dev.get("observed"):
                brief = f"firmware ? ({dev.get('reason') or 'radio not read'})"
            else:
                model, build, ver = dev.get("model"), dev.get("fw_build"), dev.get("fw_ver")
                parts = [str(model) if model else "unknown model",
                         f"build {build}" if build else "build ?"]
                if ver is not None:
                    parts.append(f"proto v{ver}")
                if dev.get("source") == "simulator":
                    parts.insert(0, "[SIM]")
                brief = " ".join(parts)

        self._fw_brief_cache = (now, brief)
        return brief

    @staticmethod
    def _oracle_posture_line(posture) -> str:
        """One line for the oracle leg (roadmap 1d).

        Wording tracks the gateway's own build log
        (``mesh oracle (meshcore) responder built: answer_all=... ``) on
        purpose: a journal grep and this pane describe the posture in the
        SAME vocabulary. The posture dict is built by the GATEWAY
        (utils.meshcore_status_api), never from this process's env — the
        TUI's environment is not the daemon's; the fleet's allowlists ride
        systemd drop-ins.
        """
        if posture is None:
            # An older gateway, or a payload without the key. Not "off".
            return ("Oracle:      UNKNOWN (gateway did not report a posture; "
                    "older build?)")
        if not posture.get("observable", False):
            reason = posture.get("reason") or "not observable"
            return f"Oracle:      UNKNOWN ({reason}) - not the same as OFF"
        if posture.get("error"):
            # Asked for, did not come up. The loudest of the three.
            return f"Oracle:      BUILD FAILED - {posture['error']}"
        if not posture.get("enabled", False):
            return "Oracle:      OFF (default; MESHFORGE_ORACLE_ENABLED unset)"
        chans = posture.get("channels") or []
        cooldown = posture.get("cooldown_s")
        # Fields the gateway could not read off the responder are NAMED,
        # never rendered as zeros — an enabled oracle with allowlist=0 is a
        # real fail-closed posture, so a defaulted 0 would be a confident
        # wrong answer rather than an obvious blank.
        unreadable = posture.get("unreadable") or []
        suffix = f"  ! unreadable: {','.join(unreadable)}" if unreadable else ""
        return ("Oracle:      ON  answer_all={} allowlist={} channels={} "
                "cooldown={} consume={}".format(
                    posture.get("answer_all", False),
                    posture.get("allowlist", 0),
                    ",".join(str(c) for c in chans) if chans else "-",
                    f"{cooldown:g}s" if isinstance(cooldown, (int, float))
                    else "?",
                    posture.get("consume", False)) + suffix)

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

        # Roadmap 1c: what the radio reports about itself, from the gateway
        # process that holds it. Labelled for what the fields ARE — the
        # radio reports a BUILD DATE and a protocol version, never a
        # release number.
        payload, err = self._status_fetch()
        print()
        if payload is None:
            print(f"  Gateway:          unreachable ({err})")
            print("  Radio facts unavailable - start it: Service Control -> meshforge-gateway")
        elif not payload.get("observable"):
            print(f"  Gateway:          {payload.get('reason') or 'no bridge'}")
        else:
            print(f"  Gateway:          {'connected' if payload.get('connected') else 'NOT connected'}"
                  f" to the radio{' [SIM]' if payload.get('simulation') else ''}")
            dev = payload.get("device") or {}
            if not dev.get("observed"):
                print(f"  Radio:            not read ({dev.get('reason') or 'unknown'})")
            else:
                print(f"  Model:            {dev.get('model') or '?'}")
                print(f"  Build:            {dev.get('fw_build') or '?'}")
                if dev.get("fw_ver") is not None:
                    print(f"  Companion proto:  v{dev['fw_ver']}")
                print("  (build date + protocol version, never a release number -")
                print("   compare the BUILD DATE against the release you intend to flash)")

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
                    saved = config.save()
                except Exception as e:
                    self.ctx.dialog.msgbox("Save Error", f"Could not save config:\n\n{e}")
                else:
                    # GatewayConfig.save() returns False on a failed write (it
                    # never raises), so the success dialog must gate on it —
                    # "Saved" had fired even when nothing persisted (S8 M1, #74-#77).
                    if saved:
                        self.ctx.dialog.msgbox(
                            "Saved",
                            "MeshCore configuration saved.\n\n"
                            "Restart the gateway bridge for changes to take effect."
                        )
                    else:
                        self.ctx.dialog.msgbox(
                            "Save Failed",
                            "Config write returned failure — changes were NOT "
                            "persisted. Check disk space / permissions and retry."
                        )

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
            saved = config.save()
        except Exception as e:
            self.ctx.dialog.msgbox("Save Error", f"Could not save config:\n\n{e}")
        else:
            # gate the toggle confirmation on the write result (S8 M2, #74-#77)
            if saved:
                self.ctx.dialog.msgbox(
                    f"MeshCore {action.title()}",
                    f"MeshCore is now {action}.\n\n"
                    f"Restart the gateway bridge for changes to take effect."
                )
            else:
                self.ctx.dialog.msgbox(
                    "Save Failed",
                    f"Toggle to {action} was NOT persisted — config write "
                    "returned failure. Check disk space / permissions and retry."
                )

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

        # The gateway runs in ANOTHER process (meshforge-gateway.service /
        # bridge_cli.py), so the in-process handle behind
        # _is_gateway_running() is empty here and used to make this pane
        # say "not running" forever (roadmap 1e). Ask the gateway itself;
        # fall back to the in-process handle only when this process IS the
        # gateway (daemon.py embedding).
        payload, err = self._status_fetch()
        if payload is None and _HAS_GW_CLI and _is_gateway_running():
            try:
                gw = _get_gateway_stats()
            except Exception as e:
                print(f"  Error reading gateway stats: {e}")
                self.ctx.wait_for_enter()
                return
            payload = {"observable": True, "running": gw.get("running", False),
                       "connected": gw.get("meshcore_connected", False),
                       "oracle": None,
                       "counters": {"observable": True,
                                    "uptime_seconds": gw.get("uptime_seconds"),
                                    "stats": gw.get("statistics", gw)}}
        if payload is None:
            print(f"  Gateway unreachable at {self.STATUS_API_BASE}: {err}\n")
            print("  Statistics are UNKNOWN, not zero. Start the gateway in-app:")
            print("    Service Control -> meshforge-gateway -> Start")
            self.ctx.wait_for_enter()
            return
        if not payload.get("observable"):
            print(f"  Gateway answered but runs no bridge: {payload.get('reason')}")
            self.ctx.wait_for_enter()
            return

        counters = payload.get("counters") or {}
        stats = counters.get("stats") or {}
        connected = payload.get("connected", False)
        gw_stats = {"uptime_seconds": counters.get("uptime_seconds")}

        print(f"  Connection:  {'CONNECTED' if connected else 'DISCONNECTED'}")
        print(f"  Bridge:      {'Running' if payload.get('running') else 'Stopped'}")
        print(f"  {self._oracle_posture_line(payload.get('oracle'))}")
        if not counters.get("observable", True):
            print(f"  Counters:    UNKNOWN ({counters.get('reason')})")
        print()

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
