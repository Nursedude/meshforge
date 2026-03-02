"""
MeshChat Handler — MeshChat client installation, management, and monitoring.

Provides TUI handlers to install, manage, and monitor MeshChat --
an LXMF messaging client with HTTP API and web UI.

MeshChat runs as an external service (systemd or manual) and exposes
a REST API on port 8000. This handler wraps the existing MeshChat plugin
(src/plugins/meshchat/) with TUI menus.

Data flow:
  Meshtastic (Short Turbo) <> meshtasticd <> MeshForge Gateway
  <> LXMF <> rnsd <> LXMF <> MeshChat

Install:  Automated via TUI (git clone + npm + pip + systemd service)
          Or manually: see plugins/meshchat/service.py INSTALL_HINT

LXMF exclusivity:
  MeshChat and NomadNet are both LXMF clients. Only one should run
  at a time to avoid port 37428 conflicts. The _ensure_lxmf_exclusive()
  helper delegates to handlers/_lxmf_utils.py for this check.

Converted from meshchat_client_mixin.py as part of the mixin-to-registry migration (Batch 8).

Split into two files for size compliance (CLAUDE.md #6):
  meshchat.py            — This file: menu, detection, status, start/stop, peers/messages/logs
  _meshchat_install.py   — Install, uninstall, RNS preflight, cross-handler helpers
"""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from backend import clear_screen
from handler_protocol import BaseHandler
from handlers._lxmf_utils import ensure_lxmf_exclusive

from utils.paths import get_real_user_home
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Import centralized service checking
check_process_running, start_service, stop_service, _HAS_SERVICE_CHECK = safe_import(
    'utils.service_check', 'check_process_running', 'start_service', 'stop_service'
)

check_rns_shared_instance, _HAS_RNS_CHECK = safe_import(
    'utils.service_check', 'check_rns_shared_instance'
)

# Import MeshChat plugin components (optional external dependency)
MeshChatService, ServiceState, _HAS_MESHCHAT_SERVICE = safe_import(
    'plugins.meshchat.service', 'MeshChatService', 'ServiceState'
)

MeshChatClient, MeshChatError, _HAS_MESHCHAT_CLIENT = safe_import(
    'plugins.meshchat.client', 'MeshChatClient', 'MeshChatError'
)


class MeshChatHandler(BaseHandler):
    """TUI handler for MeshChat client management."""

    handler_id = "meshchat"
    menu_section = "mesh_networks"

    MESHCHAT_REPO = "https://github.com/liamcottle/reticulum-meshchat"
    MESHCHAT_SERVICE_NAME = "reticulum-meshchat"

    def menu_items(self):
        return [
            ("meshchat", "MeshChat Client     RNS messaging", "rns"),
        ]

    def execute(self, action):
        if action == "meshchat":
            self._meshchat_menu()

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------

    def _is_meshchat_installed(self) -> bool:
        """Check if MeshChat is installed (binary, service, or process)."""
        # Check for meshchat.py or reticulum-meshchat in PATH
        if shutil.which('meshchat') or shutil.which('meshchat.py'):
            return True

        # Check user local bin
        user_home = get_real_user_home()
        for candidate in [
            user_home / 'reticulum-meshchat' / 'meshchat.py',
            user_home / '.local' / 'bin' / 'meshchat',
        ]:
            if candidate.exists():
                return True

        # Check via service detection if plugin available
        if _HAS_MESHCHAT_SERVICE:
            try:
                svc = MeshChatService()
                status = svc.check_status(blocking=True)
                return status.installed
            except Exception:
                pass

        return False

    def _is_meshchat_running(self) -> bool:
        """Check if MeshChat process is running."""
        # Try unified check first
        if _HAS_SERVICE_CHECK and check_process_running:
            if check_process_running('meshchat'):
                return True

        # Try plugin service check
        if _HAS_MESHCHAT_SERVICE:
            try:
                svc = MeshChatService()
                status = svc.check_status(blocking=True)
                return status.running
            except Exception:
                pass

        # Fallback to pgrep
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'meshchat.py'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                for pid in result.stdout.strip().split('\n'):
                    if pid.strip() and pid.strip() != str(os.getpid()):
                        return True
        except (subprocess.SubprocessError, OSError):
            pass

        return False

    # ------------------------------------------------------------------
    # LXMF exclusivity (delegates to shared utility)
    # ------------------------------------------------------------------

    def _ensure_lxmf_exclusive(self, starting_app: str) -> bool:
        """Ensure only one LXMF app runs at a time.

        Delegates to handlers/_lxmf_utils.ensure_lxmf_exclusive().
        """
        return ensure_lxmf_exclusive(
            self.ctx.dialog, starting_app,
            is_meshchat_running_fn=self._is_meshchat_running,
        )

    # ------------------------------------------------------------------
    # Top-level submenu
    # ------------------------------------------------------------------

    def _meshchat_menu(self):
        """MeshChat LXMF client -- install, manage, monitor."""
        def _meshchat_choices():
            running = self._is_meshchat_running()
            installed = self._is_meshchat_installed()
            items = [("status", "MeshChat Status")]
            if installed:
                if running:
                    items.append(("stop", "Stop MeshChat"))
                    items.append(("peers", "View LXMF Peers"))
                    items.append(("messages", "Recent Messages"))
                    items.append(("announce", "Send LXMF Announce"))
                    items.append(("web", "Web UI (show URL)"))
                else:
                    items.append(("start", "Start MeshChat"))
                items.append(("logs", "View Logs"))
                items.append(("uninstall", "Disable MeshChat"))
            else:
                items.append(("install", "Install MeshChat"))
            return items

        dispatch = {
            "status": ("MeshChat Status", self._meshchat_status),
            "start": ("Start MeshChat", self._launch_meshchat),
            "stop": ("Stop MeshChat", self._stop_meshchat),
            "peers": ("View LXMF Peers", self._meshchat_peers),
            "messages": ("Recent Messages", self._meshchat_messages),
            "announce": ("Send LXMF Announce", self._meshchat_announce),
            "web": ("MeshChat Web UI", self._meshchat_web_ui),
            "logs": ("View MeshChat Logs", self._meshchat_logs),
            "install": ("Install MeshChat", self._install_meshchat),
            "uninstall": ("Disable MeshChat", self._uninstall_meshchat),
        }
        self.run_menu_loop(
            "MeshChat Client", "LXMF messaging with HTTP API & web UI:",
            _meshchat_choices, dispatch,
        )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _meshchat_status(self):
        """Show comprehensive MeshChat status."""
        clear_screen()
        print("=== MeshChat Status ===\n")

        # Installation
        installed = self._is_meshchat_installed()
        running = self._is_meshchat_running()

        if not installed:
            print("  Installed:  No")
            print(f"\n  Install from: https://github.com/liamcottle/reticulum-meshchat")
            self.ctx.wait_for_enter()
            return

        print(f"  Installed:  Yes")
        print(f"  Running:    {'Yes' if running else 'No'}")

        # Service details via plugin
        if _HAS_MESHCHAT_SERVICE:
            try:
                svc = MeshChatService()
                status = svc.check_status(blocking=True)
                if status.service_name:
                    print(f"  Service:    {status.service_name}")
                if status.pid:
                    print(f"  PID:        {status.pid}")
                print(f"  Port 8000:  {'Open' if status.port_open else 'Closed'}")
            except Exception as e:
                logger.debug("MeshChat service check failed: %s", e)

        # API details if running
        if running and _HAS_MESHCHAT_CLIENT:
            try:
                client = MeshChatClient()
                mc_status = client.get_status()
                print()
                if mc_status.version:
                    print(f"  Version:    {mc_status.version}")
                if mc_status.identity_hash:
                    print(f"  Identity:   {mc_status.identity_hash}")
                if mc_status.display_name:
                    print(f"  Name:       {mc_status.display_name}")
                print(f"  Peers:      {mc_status.peer_count}")
                print(f"  Messages:   {mc_status.message_count}")
                print(f"  RNS:        {'Connected' if mc_status.rns_connected else 'Disconnected'}")
                if mc_status.uptime_seconds > 0:
                    hrs = mc_status.uptime_seconds // 3600
                    mins = (mc_status.uptime_seconds % 3600) // 60
                    print(f"  Uptime:     {hrs}h {mins}m")
                print(f"  Propagation: {'Yes' if mc_status.propagation_node else 'No'}")
            except Exception as e:
                print(f"\n  API Error:  {e}")

        # RNS shared instance status
        print()
        rnsd_user = self._get_rnsd_user()
        if rnsd_user:
            print(f"  rnsd:       Running (as {rnsd_user})")
        else:
            print("  rnsd:       Not running")
            if running:
                print("              MeshChat may be running its own RNS instance")

        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def _launch_meshchat(self):
        """Start MeshChat service."""
        # Preflight: ensure NomadNet is not running (one LXMF app at a time)
        if not self._ensure_lxmf_exclusive("meshchat"):
            return

        # Preflight: check RNS availability
        if not self._check_rns_for_meshchat():
            return

        if _HAS_MESHCHAT_SERVICE:
            svc = MeshChatService()
            status = svc.check_status(blocking=True)

            if status.running:
                self.ctx.dialog.msgbox(
                    "Already Running",
                    "MeshChat is already running.\n\n"
                    f"Web UI: http://127.0.0.1:8000",
                )
                return

            if status.service_name:
                # Systemd service available — start it
                self.ctx.dialog.infobox(
                    "Starting MeshChat",
                    f"Starting {status.service_name}...",
                )
                svc.start()
                time.sleep(3)

                # Verify
                new_status = svc.check_status(blocking=True)
                if new_status.running:
                    self.ctx.dialog.msgbox(
                        "MeshChat Started",
                        f"MeshChat is running.\n\n"
                        f"Web UI: http://127.0.0.1:8000",
                    )
                else:
                    self.ctx.dialog.msgbox(
                        "Start May Have Failed",
                        f"MeshChat does not appear to be running.\n\n"
                        f"Check: systemctl status {status.service_name}\n"
                        f"       journalctl -u {status.service_name} -n 20",
                    )
                return

        # No systemd service — show manual start instructions
        self.ctx.dialog.msgbox(
            "Manual Start Required",
            "No systemd service found for MeshChat.\n\n"
            "Start manually:\n"
            "  cd ~/reticulum-meshchat\n"
            "  python meshchat.py\n\n"
            "Or create a systemd service for automatic startup.",
        )

    def _stop_meshchat(self):
        """Stop MeshChat service."""
        if not self.ctx.dialog.yesno(
            "Stop MeshChat",
            "Stop the MeshChat service?\n\n"
            "LXMF messaging will be unavailable until restarted.",
        ):
            return

        stopped = False

        if _HAS_MESHCHAT_SERVICE:
            svc = MeshChatService()
            status = svc.check_status(blocking=True)
            if status.service_name:
                svc.stop()
                time.sleep(2)
                stopped = True

        if not stopped:
            # Fallback: kill process
            try:
                subprocess.run(
                    ['pkill', '-f', 'meshchat.py'],
                    capture_output=True, timeout=5,
                )
                time.sleep(1)
                stopped = True
            except (subprocess.SubprocessError, OSError):
                pass

        if stopped and not self._is_meshchat_running():
            self.ctx.dialog.msgbox(
                "MeshChat Stopped",
                "MeshChat has been stopped.",
            )
        else:
            self.ctx.dialog.msgbox(
                "Stop May Have Failed",
                "MeshChat may still be running.\n\n"
                "Try: pkill -f meshchat.py",
            )

    # ------------------------------------------------------------------
    # Peers, Messages, Announce
    # ------------------------------------------------------------------

    def _meshchat_peers(self):
        """Show discovered LXMF peers."""
        clear_screen()
        print("=== MeshChat LXMF Peers ===\n")

        if not _HAS_MESHCHAT_CLIENT:
            print("  MeshChat client library not available.")
            self.ctx.wait_for_enter()
            return

        try:
            client = MeshChatClient()
            peers = client.get_peers()

            if not peers:
                print("  No peers discovered yet.")
                print("\n  Peers appear after LXMF announces propagate.")
                print("  Try: Send Announce from the menu.")
                self.ctx.wait_for_enter()
                return

            # Header
            print(f"  {'Name':<20} {'Hash':<18} {'Online':<8} {'Last Announce'}")
            print(f"  {'─' * 20} {'─' * 18} {'─' * 8} {'─' * 20}")

            for peer in peers:
                name = (peer.display_name or "Unknown")[:20]
                short_hash = peer.destination_hash[:16] + ".."
                online = "Yes" if peer.is_online else "No"
                last = ""
                if peer.last_announce:
                    last = peer.last_announce.strftime("%Y-%m-%d %H:%M")
                print(f"  {name:<20} {short_hash:<18} {online:<8} {last}")

            print(f"\n  Total: {len(peers)} peers")

        except Exception as e:
            print(f"  Error fetching peers: {e}")

        self.ctx.wait_for_enter()

    def _meshchat_messages(self):
        """Show recent LXMF messages."""
        clear_screen()
        print("=== MeshChat Recent Messages ===\n")

        if not _HAS_MESHCHAT_CLIENT:
            print("  MeshChat client library not available.")
            self.ctx.wait_for_enter()
            return

        try:
            client = MeshChatClient()
            messages = client.get_messages(limit=20)

            if not messages:
                print("  No messages yet.")
                self.ctx.wait_for_enter()
                return

            for msg in messages:
                direction = "<<" if msg.is_incoming else ">>"
                ts = msg.timestamp.strftime("%H:%M:%S")
                src = msg.source_hash[:12] + ".."
                delivered = "+" if msg.delivered else " "
                content = msg.content[:60]
                if len(msg.content) > 60:
                    content += "..."
                print(f"  {ts} {direction} {src} {delivered} {content}")

            print(f"\n  Showing {len(messages)} most recent messages")

        except Exception as e:
            print(f"  Error fetching messages: {e}")

        self.ctx.wait_for_enter()

    def _meshchat_announce(self):
        """Send LXMF announce to the network."""
        if not self.ctx.dialog.yesno(
            "Send Announce",
            "Send an LXMF announce to the RNS network?\n\n"
            "This advertises MeshChat's presence to other\n"
            "LXMF clients (NomadNet, Sideband, other MeshChat).",
        ):
            return

        if not _HAS_MESHCHAT_CLIENT:
            self.ctx.dialog.msgbox(
                "Not Available",
                "MeshChat client library not available.",
            )
            return

        try:
            client = MeshChatClient()
            if client.send_announce():
                self.ctx.dialog.msgbox(
                    "Announce Sent",
                    "LXMF announce has been sent to the network.\n\n"
                    "Other nodes will discover MeshChat within minutes.",
                )
            else:
                self.ctx.dialog.msgbox(
                    "Announce Failed",
                    "Failed to send LXMF announce.\n\n"
                    "Check that MeshChat is running and RNS is connected.",
                )
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Announce Error",
                f"Error sending announce: {e}",
            )

    # ------------------------------------------------------------------
    # Web UI
    # ------------------------------------------------------------------

    def _meshchat_web_ui(self):
        """Show MeshChat web UI URL."""
        self.ctx.dialog.msgbox(
            "MeshChat Web UI",
            "MeshChat web interface is available at:\n\n"
            "  http://127.0.0.1:8000\n\n"
            "Access from the same machine in a browser,\n"
            "or via SSH tunnel:\n\n"
            "  ssh -L 8000:127.0.0.1:8000 user@host\n"
            "  Then open: http://127.0.0.1:8000",
        )

    # ------------------------------------------------------------------
    # Logs
    # ------------------------------------------------------------------

    def _meshchat_logs(self):
        """View MeshChat logs."""
        clear_screen()
        print("=== MeshChat Logs ===\n")

        shown = False

        # Try systemd journal first
        if _HAS_MESHCHAT_SERVICE:
            try:
                svc = MeshChatService()
                status = svc.check_status(blocking=True)
                if status.service_name:
                    print(f"  Service: {status.service_name}\n")
                    result = subprocess.run(
                        ['journalctl', '-u', status.service_name,
                         '-n', '30', '--no-pager'],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.stdout and result.stdout.strip():
                        for line in result.stdout.strip().split('\n'):
                            print(f"  {line}")
                        shown = True
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("MeshChat journal read failed: %s", e)

        # Try log file paths
        if not shown:
            user_home = get_real_user_home()
            log_paths = [
                user_home / '.config' / 'meshchat' / 'logs',
                user_home / '.meshchat' / 'logs',
                user_home / 'reticulum-meshchat' / 'logs',
                Path('/var/log/meshchat'),
            ]
            for log_dir in log_paths:
                if log_dir.exists() and log_dir.is_dir():
                    # Find most recent log file
                    log_files = sorted(
                        log_dir.glob('*.log'),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    if log_files:
                        import collections
                        print(f"  Log file: {log_files[0]}\n")
                        try:
                            with open(log_files[0], 'r') as f:
                                last_lines = list(
                                    collections.deque(f, maxlen=30)
                                )
                            for line in last_lines:
                                print(f"  {line.rstrip()}")
                            shown = True
                        except (IOError, OSError) as e:
                            print(f"  Error reading log: {e}")
                    break

        if not shown:
            print("  No logs found.")
            print("\n  If MeshChat is running as a systemd service:")
            print("    journalctl -u meshchat -n 30 --no-pager")
            print("    journalctl -u reticulum-meshchat -n 30 --no-pager")

        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Install, uninstall, RNS preflight (delegated to _meshchat_install)
    # ------------------------------------------------------------------

    def _install_meshchat(self):
        from handlers._meshchat_install import install_meshchat
        install_meshchat(self)

    def _uninstall_meshchat(self):
        from handlers._meshchat_install import uninstall_meshchat
        uninstall_meshchat(self)

    def _get_meshchat_install_dir(self) -> Path:
        from handlers._meshchat_install import get_meshchat_install_dir
        return get_meshchat_install_dir(self)

    def _get_rnsd_user(self):
        from handlers._meshchat_install import get_rnsd_user
        return get_rnsd_user(self)

    def _fix_rnsd_user(self, target_user: str) -> bool:
        from handlers._meshchat_install import fix_rnsd_user
        return fix_rnsd_user(self, target_user)

    def _check_rns_for_meshchat(self) -> bool:
        from handlers._meshchat_install import check_rns_for_meshchat
        return check_rns_for_meshchat(self)
