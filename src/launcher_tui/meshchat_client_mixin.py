"""
MeshChat Client Mixin for MeshForge Launcher TUI.

Provides TUI handlers to install, manage, and monitor MeshChat --
an LXMF messaging client with HTTP API and web UI.

MeshChat runs as an external service (systemd or manual) and exposes
a REST API on port 8000. This mixin wraps the existing MeshChat plugin
(src/plugins/meshchat/) with TUI menus.

Data flow:
  Meshtastic (Short Turbo) <> meshtasticd <> MeshForge Gateway
  <> LXMF <> rnsd <> LXMF <> MeshChat

Install:  Automated via TUI (git clone + npm + pip + systemd service)
          Or manually: see plugins/meshchat/service.py INSTALL_HINT

LXMF exclusivity:
  MeshChat and NomadNet are both LXMF clients. Only one should run
  at a time to avoid port 37428 conflicts. The _ensure_lxmf_exclusive()
  helper enforces this by offering to stop the other app before starting.
"""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from backend import clear_screen

logger = logging.getLogger(__name__)

from utils.paths import get_real_user_home
from utils.safe_import import safe_import

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


class MeshChatClientMixin:
    """Mixin providing MeshChat client management for the TUI launcher."""

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
    # Top-level submenu
    # ------------------------------------------------------------------

    def _meshchat_menu(self):
        """MeshChat LXMF client -- install, manage, monitor."""
        while True:
            running = self._is_meshchat_running()
            installed = self._is_meshchat_installed()

            if not installed:
                subtitle = "MeshChat is NOT INSTALLED"
            elif running:
                subtitle = "MeshChat is RUNNING (http://127.0.0.1:8000)"
            else:
                subtitle = "MeshChat is installed (not running)"

            choices = [
                ("status", "MeshChat Status"),
            ]

            if installed:
                if running:
                    choices.append(("stop", "Stop MeshChat"))
                    choices.append(("peers", "View LXMF Peers"))
                    choices.append(("messages", "Recent Messages"))
                    choices.append(("announce", "Send LXMF Announce"))
                    choices.append(("web", "Web UI (show URL)"))
                else:
                    choices.append(("start", "Start MeshChat"))
                choices.append(("logs", "View Logs"))
                choices.append(("uninstall", "Disable MeshChat"))
            else:
                choices.append(("install", "Install MeshChat"))

            choices.append(("back", "Back"))

            choice = self.dialog.menu(
                "MeshChat Client",
                f"LXMF messaging with HTTP API & web UI:\n\n"
                f"{subtitle}",
                choices,
            )

            if choice is None or choice == "back":
                break

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
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

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
            self._wait_for_enter()
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
        rnsd_user = self._get_rnsd_user() if hasattr(self, '_get_rnsd_user') else None
        if rnsd_user:
            print(f"  rnsd:       Running (as {rnsd_user})")
        else:
            print("  rnsd:       Not running")
            if running:
                print("              MeshChat may be running its own RNS instance")

        self._wait_for_enter()

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
                self.dialog.msgbox(
                    "Already Running",
                    "MeshChat is already running.\n\n"
                    f"Web UI: http://127.0.0.1:8000",
                )
                return

            if status.service_name:
                # Systemd service available — start it
                self.dialog.infobox(
                    "Starting MeshChat",
                    f"Starting {status.service_name}...",
                )
                svc.start()
                time.sleep(3)

                # Verify
                new_status = svc.check_status(blocking=True)
                if new_status.running:
                    self.dialog.msgbox(
                        "MeshChat Started",
                        f"MeshChat is running.\n\n"
                        f"Web UI: http://127.0.0.1:8000",
                    )
                else:
                    self.dialog.msgbox(
                        "Start May Have Failed",
                        f"MeshChat does not appear to be running.\n\n"
                        f"Check: systemctl status {status.service_name}\n"
                        f"       journalctl -u {status.service_name} -n 20",
                    )
                return

        # No systemd service — show manual start instructions
        self.dialog.msgbox(
            "Manual Start Required",
            "No systemd service found for MeshChat.\n\n"
            "Start manually:\n"
            "  cd ~/reticulum-meshchat\n"
            "  python meshchat.py\n\n"
            "Or create a systemd service for automatic startup.",
        )

    def _stop_meshchat(self):
        """Stop MeshChat service."""
        if not self.dialog.yesno(
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
            self.dialog.msgbox(
                "MeshChat Stopped",
                "MeshChat has been stopped.",
            )
        else:
            self.dialog.msgbox(
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
            self._wait_for_enter()
            return

        try:
            client = MeshChatClient()
            peers = client.get_peers()

            if not peers:
                print("  No peers discovered yet.")
                print("\n  Peers appear after LXMF announces propagate.")
                print("  Try: Send Announce from the menu.")
                self._wait_for_enter()
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

        self._wait_for_enter()

    def _meshchat_messages(self):
        """Show recent LXMF messages."""
        clear_screen()
        print("=== MeshChat Recent Messages ===\n")

        if not _HAS_MESHCHAT_CLIENT:
            print("  MeshChat client library not available.")
            self._wait_for_enter()
            return

        try:
            client = MeshChatClient()
            messages = client.get_messages(limit=20)

            if not messages:
                print("  No messages yet.")
                self._wait_for_enter()
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

        self._wait_for_enter()

    def _meshchat_announce(self):
        """Send LXMF announce to the network."""
        if not self.dialog.yesno(
            "Send Announce",
            "Send an LXMF announce to the RNS network?\n\n"
            "This advertises MeshChat's presence to other\n"
            "LXMF clients (NomadNet, Sideband, other MeshChat).",
        ):
            return

        if not _HAS_MESHCHAT_CLIENT:
            self.dialog.msgbox(
                "Not Available",
                "MeshChat client library not available.",
            )
            return

        try:
            client = MeshChatClient()
            if client.send_announce():
                self.dialog.msgbox(
                    "Announce Sent",
                    "LXMF announce has been sent to the network.\n\n"
                    "Other nodes will discover MeshChat within minutes.",
                )
            else:
                self.dialog.msgbox(
                    "Announce Failed",
                    "Failed to send LXMF announce.\n\n"
                    "Check that MeshChat is running and RNS is connected.",
                )
        except Exception as e:
            self.dialog.msgbox(
                "Announce Error",
                f"Error sending announce: {e}",
            )

    # ------------------------------------------------------------------
    # Web UI
    # ------------------------------------------------------------------

    def _meshchat_web_ui(self):
        """Show MeshChat web UI URL."""
        self.dialog.msgbox(
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

        self._wait_for_enter()

    # ------------------------------------------------------------------
    # Install (fully automated)
    # ------------------------------------------------------------------

    MESHCHAT_REPO = "https://github.com/liamcottle/reticulum-meshchat"
    MESHCHAT_SERVICE_NAME = "reticulum-meshchat"

    def _get_meshchat_install_dir(self) -> Path:
        """Return the MeshChat install directory under user home."""
        return get_real_user_home() / 'reticulum-meshchat'

    def _get_pip_command(self) -> list:
        """Return the pip command appropriate for this install.

        Prefers MeshForge's venv pip, falls back to system pip3
        with --break-system-packages for PEP 668 compatibility.
        """
        venv_pip = Path('/opt/meshforge/venv/bin/pip')
        if venv_pip.exists():
            return [str(venv_pip)]

        # Check for PEP 668 (externally-managed Python)
        import glob
        if glob.glob('/usr/lib/python3*/EXTERNALLY-MANAGED'):
            return ['pip3', 'install', '--break-system-packages']

        return ['pip3']

    def _install_meshchat(self):
        """Automated MeshChat installation.

        Steps:
        1. Confirm with user
        2. Check/install prerequisites (git, nodejs, npm)
        3. git clone reticulum-meshchat
        4. pip install -r requirements.txt
        5. npm install && npm run build-frontend
        6. Create systemd service
        7. Enable + start service
        """
        if self._is_meshchat_installed():
            self.dialog.msgbox(
                "Already Installed",
                "MeshChat is already installed.\n\n"
                "Use Start/Stop from the menu to manage it.",
            )
            return

        if not self.dialog.yesno(
            "Install MeshChat",
            "Install MeshChat (Reticulum MeshChat)?\n\n"
            "This will:\n"
            "  1. Install Node.js/npm (if needed)\n"
            "  2. Clone the MeshChat repository\n"
            "  3. Install Python dependencies\n"
            "  4. Build the web frontend (npm)\n"
            "  5. Create a systemd service\n\n"
            "MeshChat provides LXMF messaging with a\n"
            "web UI at http://127.0.0.1:8000\n\n"
            "Source: github.com/liamcottle/reticulum-meshchat\n\n"
            "Install now?",
        ):
            return

        # LXMF exclusivity check — stop NomadNet if running
        if not self._ensure_lxmf_exclusive("meshchat"):
            return

        clear_screen()
        print("=== Installing MeshChat ===\n")

        install_dir = self._get_meshchat_install_dir()
        sudo_user = os.environ.get('SUDO_USER')
        run_as_user = sudo_user if sudo_user and sudo_user != 'root' else None

        try:
            # Step 1: Prerequisites
            if not self._install_meshchat_prerequisites():
                self._wait_for_enter()
                return

            # Step 2: Git clone
            if not self._install_meshchat_clone(install_dir, run_as_user):
                self._wait_for_enter()
                return

            # Step 3: pip install
            if not self._install_meshchat_pip(install_dir, run_as_user):
                self._wait_for_enter()
                return

            # Step 4: npm build
            if not self._install_meshchat_npm(install_dir, run_as_user):
                self._wait_for_enter()
                return

            # Step 5: systemd service
            if not self._install_meshchat_service(install_dir, run_as_user):
                print("\nSystemd service creation failed.")
                print("You can still run MeshChat manually:")
                print(f"  cd {install_dir} && python3 meshchat.py")

            # Step 6: Start service
            print("\nStarting MeshChat service...")
            try:
                subprocess.run(
                    ['systemctl', 'start', self.MESHCHAT_SERVICE_NAME],
                    capture_output=True, timeout=15,
                )
                time.sleep(3)
            except (subprocess.SubprocessError, OSError) as e:
                logger.debug("Service start failed: %s", e)

            # Verify
            if self._is_meshchat_running():
                print("\nMeshChat is running!")
                print("Web UI: http://127.0.0.1:8000")
            else:
                print("\nMeshChat installed but may not be running yet.")
                print(f"Check: systemctl status {self.MESHCHAT_SERVICE_NAME}")

            print("\nInstallation complete.")

        except KeyboardInterrupt:
            print("\n\nInstallation cancelled.")
        except Exception as e:
            print(f"\nInstallation error: {e}")
            logger.exception("MeshChat install failed")

        try:
            self._wait_for_enter()
        except (EOFError, KeyboardInterrupt):
            pass

    def _install_meshchat_prerequisites(self) -> bool:
        """Check and install git, nodejs, npm. Returns True on success."""
        # git
        if not shutil.which('git'):
            print("Installing git...")
            result = subprocess.run(
                ['apt-get', 'install', '-y', '-qq', 'git'],
                capture_output=True, timeout=120,
            )
            if result.returncode != 0:
                print("Failed to install git.")
                print("Try: sudo apt install git")
                return False

        # Node.js + npm
        if not shutil.which('node') or not shutil.which('npm'):
            print("Installing Node.js and npm...")
            result = subprocess.run(
                ['apt-get', 'install', '-y', '-qq', 'nodejs', 'npm'],
                capture_output=True, timeout=180,
            )
            if result.returncode != 0:
                print("Failed to install Node.js/npm.")
                print("Try: sudo apt install nodejs npm")
                return False
            print("Node.js and npm installed.")

        # Verify
        for tool in ['git', 'node', 'npm']:
            if not shutil.which(tool):
                print(f"Error: {tool} not found after install.")
                return False

        print("Prerequisites OK (git, node, npm)\n")
        return True

    def _install_meshchat_clone(self, install_dir: Path, run_as_user: str = None) -> bool:
        """Clone the MeshChat repository. Returns True on success."""
        if install_dir.exists():
            print(f"Directory exists: {install_dir}")
            # Pull latest instead of clone
            print("Pulling latest changes...")
            cmd = ['git', '-C', str(install_dir), 'pull', '--ff-only']
            if run_as_user:
                cmd = ['sudo', '-H', '-u', run_as_user] + cmd
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                print(f"Git pull failed: {result.stderr.strip()}")
                print("Continuing with existing checkout.")
            else:
                print("Repository updated.")
            return True

        print(f"Cloning MeshChat to {install_dir}...")
        cmd = ['git', 'clone', self.MESHCHAT_REPO, str(install_dir)]
        if run_as_user:
            cmd = ['sudo', '-H', '-u', run_as_user] + cmd

        result = subprocess.run(cmd, timeout=120)
        if result.returncode != 0:
            print("Git clone failed.")
            print(f"Try: git clone {self.MESHCHAT_REPO} {install_dir}")
            return False

        print("Repository cloned.\n")
        return True

    def _install_meshchat_pip(self, install_dir: Path, run_as_user: str = None) -> bool:
        """Install MeshChat Python dependencies. Returns True on success."""
        req_file = install_dir / 'requirements.txt'
        if not req_file.exists():
            print("No requirements.txt found — skipping pip install.")
            return True

        print("Installing Python dependencies...")
        pip_cmd = self._get_pip_command()

        # Build install command
        if len(pip_cmd) > 1 and pip_cmd[1] == 'install':
            # pip3 install --break-system-packages case
            cmd = pip_cmd + ['--timeout', '60', '-r', str(req_file)]
        else:
            cmd = pip_cmd + ['install', '--timeout', '60', '-r', str(req_file)]

        if run_as_user:
            cmd = ['sudo', '-H', '-u', run_as_user] + cmd

        result = subprocess.run(cmd, timeout=300)
        if result.returncode != 0:
            print("pip install failed.")
            print(f"Try: pip3 install -r {req_file}")
            return False

        print("Python dependencies installed.\n")
        return True

    def _install_meshchat_npm(self, install_dir: Path, run_as_user: str = None) -> bool:
        """Build MeshChat web frontend with npm. Returns True on success."""
        pkg_json = install_dir / 'package.json'
        if not pkg_json.exists():
            print("No package.json found — skipping npm build.")
            return True

        print("Installing npm dependencies...")
        cmd = ['npm', 'install', '--omit=dev']
        if run_as_user:
            cmd = ['sudo', '-H', '-u', run_as_user] + cmd

        result = subprocess.run(cmd, cwd=str(install_dir), timeout=300)
        if result.returncode != 0:
            print("npm install failed.")
            print(f"Try: cd {install_dir} && npm install --omit=dev")
            return False

        print("Building web frontend...")
        cmd = ['npm', 'run', 'build-frontend']
        if run_as_user:
            cmd = ['sudo', '-H', '-u', run_as_user] + cmd

        result = subprocess.run(cmd, cwd=str(install_dir), timeout=300)
        if result.returncode != 0:
            print("npm build failed.")
            print(f"Try: cd {install_dir} && npm run build-frontend")
            return False

        print("Web frontend built.\n")
        return True

    def _install_meshchat_service(self, install_dir: Path, run_as_user: str = None) -> bool:
        """Create systemd service for MeshChat. Returns True on success."""
        service_user = run_as_user or 'root'
        user_home = get_real_user_home()
        python_path = shutil.which('python3') or '/usr/bin/python3'
        meshchat_py = install_dir / 'meshchat.py'

        service_content = (
            f"[Unit]\n"
            f"Description=Reticulum MeshChat LXMF Client\n"
            f"After=network.target rnsd.service\n"
            f"Wants=rnsd.service\n"
            f"\n"
            f"[Service]\n"
            f"Type=simple\n"
            f"User={service_user}\n"
            f"WorkingDirectory={install_dir}\n"
            f"ExecStart={python_path} {meshchat_py}\n"
            f"Restart=on-failure\n"
            f"RestartSec=5\n"
            f"StartLimitBurst=5\n"
            f"StartLimitIntervalSec=60\n"
            f"Environment=HOME={user_home}\n"
            f"\n"
            f"[Install]\n"
            f"WantedBy=multi-user.target\n"
        )

        service_path = f"/etc/systemd/system/{self.MESHCHAT_SERVICE_NAME}.service"
        print(f"Creating systemd service: {service_path}")

        try:
            # Write service file
            with open(service_path, 'w') as f:
                f.write(service_content)

            # Reload systemd and enable
            subprocess.run(
                ['systemctl', 'daemon-reload'],
                capture_output=True, timeout=15,
            )
            subprocess.run(
                ['systemctl', 'enable', self.MESHCHAT_SERVICE_NAME],
                capture_output=True, timeout=15,
            )
            print("Service created and enabled.\n")
            return True

        except (IOError, OSError, subprocess.SubprocessError) as e:
            print(f"Failed to create service: {e}")
            return False

    # ------------------------------------------------------------------
    # Uninstall (stop + disable)
    # ------------------------------------------------------------------

    def _uninstall_meshchat(self):
        """Stop and disable MeshChat service.

        Leaves files in place for easy re-enable. Does not delete
        the cloned repository or configuration.
        """
        if not self.dialog.yesno(
            "Disable MeshChat",
            "Stop and disable the MeshChat service?\n\n"
            "This will:\n"
            "  - Stop MeshChat if running\n"
            "  - Disable auto-start on boot\n\n"
            "Files remain at ~/reticulum-meshchat\n"
            "for easy re-enable later.\n\n"
            "Disable now?",
        ):
            return

        clear_screen()
        print("=== Disabling MeshChat ===\n")

        # Stop service
        try:
            print("Stopping MeshChat...")
            subprocess.run(
                ['systemctl', 'stop', self.MESHCHAT_SERVICE_NAME],
                capture_output=True, timeout=15,
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Service stop: %s", e)

        # Kill any running process
        try:
            subprocess.run(
                ['pkill', '-f', 'meshchat.py'],
                capture_output=True, timeout=5,
            )
        except (subprocess.SubprocessError, OSError):
            pass

        time.sleep(1)

        # Disable service
        try:
            print("Disabling auto-start...")
            subprocess.run(
                ['systemctl', 'disable', self.MESHCHAT_SERVICE_NAME],
                capture_output=True, timeout=15,
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Service disable: %s", e)

        install_dir = self._get_meshchat_install_dir()
        if self._is_meshchat_running():
            print("\nMeshChat may still be running.")
            print("Try: sudo pkill -f meshchat.py")
        else:
            print("\nMeshChat stopped and disabled.")

        print(f"\nFiles remain at: {install_dir}")
        print("To re-enable: systemctl enable --now reticulum-meshchat")

        self._wait_for_enter()

    # ------------------------------------------------------------------
    # LXMF exclusivity (one app at a time)
    # ------------------------------------------------------------------

    def _ensure_lxmf_exclusive(self, starting_app: str) -> bool:
        """Ensure only one LXMF app runs at a time.

        MeshChat and NomadNet both use LXMF and can conflict on
        port 37428 when connecting to rnsd. Only one should run.

        Args:
            starting_app: "meshchat" or "nomadnet"

        Returns:
            True if OK to proceed, False if user cancelled.
        """
        if starting_app == "meshchat":
            # Check if NomadNet is running
            nomadnet_running = False
            if _HAS_SERVICE_CHECK and check_process_running:
                nomadnet_running = check_process_running('nomadnet')
            if not nomadnet_running:
                try:
                    result = subprocess.run(
                        ['pgrep', '-f', 'bin/nomadnet'],
                        capture_output=True, text=True, timeout=5,
                    )
                    nomadnet_running = (
                        result.returncode == 0 and
                        bool(result.stdout.strip())
                    )
                except (subprocess.SubprocessError, OSError):
                    pass

            if nomadnet_running:
                if not self.dialog.yesno(
                    "NomadNet Running",
                    "NomadNet is currently running.\n\n"
                    "Only one LXMF app should run at a time\n"
                    "to avoid port 37428 conflicts.\n\n"
                    "Stop NomadNet and start MeshChat?",
                ):
                    return False
                # Stop NomadNet
                try:
                    subprocess.run(
                        ['pkill', '-f', 'bin/nomadnet'],
                        capture_output=True, timeout=10,
                    )
                    time.sleep(2)
                except (subprocess.SubprocessError, OSError):
                    pass

        elif starting_app == "nomadnet":
            # Check if MeshChat is running
            if self._is_meshchat_running():
                if not self.dialog.yesno(
                    "MeshChat Running",
                    "MeshChat is currently running.\n\n"
                    "Only one LXMF app should run at a time\n"
                    "to avoid port 37428 conflicts.\n\n"
                    "Stop MeshChat and start NomadNet?",
                ):
                    return False
                # Stop MeshChat
                try:
                    subprocess.run(
                        ['systemctl', 'stop', self.MESHCHAT_SERVICE_NAME],
                        capture_output=True, timeout=15,
                    )
                except (subprocess.SubprocessError, OSError):
                    pass
                try:
                    subprocess.run(
                        ['pkill', '-f', 'meshchat.py'],
                        capture_output=True, timeout=5,
                    )
                except (subprocess.SubprocessError, OSError):
                    pass
                time.sleep(2)

        return True

    # ------------------------------------------------------------------
    # Preflight: RNS check
    # ------------------------------------------------------------------

    def _check_rns_for_meshchat(self) -> bool:
        """Check that RNS is available for MeshChat.

        MeshChat can run with or without rnsd:
        - With rnsd: connects as shared instance client (recommended)
        - Without: starts its own RNS instance

        Returns True to proceed, False if user cancelled.
        """
        rnsd_user = None
        if hasattr(self, '_get_rnsd_user'):
            rnsd_user = self._get_rnsd_user()

        if not rnsd_user:
            # rnsd not running — warn but allow proceeding
            return self.dialog.yesno(
                "rnsd Not Running",
                "The RNS daemon (rnsd) is not running.\n\n"
                "MeshChat can start its own RNS instance,\n"
                "but for Meshtastic bridging you should run rnsd\n"
                "with share_instance = Yes in the Reticulum config.\n\n"
                "Continue anyway?",
            )

        # rnsd is running — check for root mismatch
        sudo_user = os.environ.get('SUDO_USER', '')
        if rnsd_user == 'root' and sudo_user and sudo_user != 'root':
            choice = self.dialog.menu(
                "rnsd Running as Root",
                f"rnsd is running as root, but MeshChat should\n"
                f"use the same RNS identity as '{sudo_user}'.\n\n"
                "This may cause RPC authentication failures.",
                [
                    ("continue", "Continue anyway"),
                    ("fix", f"Fix rnsd to run as {sudo_user}"),
                    ("cancel", "Cancel"),
                ],
            )
            if choice == "fix" and hasattr(self, '_fix_rnsd_user'):
                self._fix_rnsd_user(sudo_user)
                return True
            elif choice == "cancel" or choice is None:
                return False
            # "continue" falls through

        return True
