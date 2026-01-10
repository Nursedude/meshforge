"""
NomadNet Tools Section for RNS Panel

Provides terminal-based encrypted messaging over Reticulum.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading
import shutil
import shlex
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class NomadNetMixin:
    """
    Mixin class providing NomadNet functionality for RNSPanel.

    Expects the panel to have:
    - main_window: Reference to main application window
    - _get_real_user_home(): Method to get real user's home directory
    - _get_real_username(): Method to get real username
    - _gateway_bridge: Gateway bridge instance (may be None)
    - _check_rns_service(): Method to check RNS service status
    - _update_gateway_status(): Method to update gateway status
    - _open_config_folder(path): Method to open folder in file manager
    - _refresh_all(): Method to refresh all sections
    """

    def _build_nomadnet_section(self, parent):
        """Build NomadNet tools section"""
        frame = Gtk.Frame()
        frame.set_label("NomadNet Tools")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Description
        desc = Gtk.Label(label="Terminal-based encrypted messaging and browsing over Reticulum")
        desc.set_xalign(0)
        desc.set_wrap(True)
        desc.add_css_class("dim-label")
        box.append(desc)

        # Status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.nomadnet_status_icon = Gtk.Image.new_from_icon_name("emblem-question")
        self.nomadnet_status_icon.set_pixel_size(20)
        status_row.append(self.nomadnet_status_icon)

        self.nomadnet_status_label = Gtk.Label(label="Checking...")
        self.nomadnet_status_label.set_xalign(0)
        status_row.append(self.nomadnet_status_label)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh status")
        refresh_btn.connect("clicked", lambda b: self._check_nomadnet_status())
        status_row.append(refresh_btn)

        box.append(status_row)

        # Launch buttons row
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_row.set_halign(Gtk.Align.CENTER)

        # NomadNet Text UI
        self.nomadnet_textui_btn = Gtk.Button(label="Launch Text UI")
        self.nomadnet_textui_btn.add_css_class("suggested-action")
        self.nomadnet_textui_btn.set_tooltip_text("Launch NomadNet in a terminal window")
        self.nomadnet_textui_btn.connect("clicked", lambda b: self._launch_nomadnet("textui"))
        btn_row.append(self.nomadnet_textui_btn)

        # NomadNet Daemon
        self.nomadnet_daemon_btn = Gtk.Button(label="Start Daemon")
        self.nomadnet_daemon_btn.set_tooltip_text("Run NomadNet as background daemon")
        self.nomadnet_daemon_btn.connect("clicked", lambda b: self._launch_nomadnet("daemon"))
        btn_row.append(self.nomadnet_daemon_btn)

        # Stop Daemon
        self.nomadnet_stop_btn = Gtk.Button(label="Stop Daemon")
        self.nomadnet_stop_btn.add_css_class("destructive-action")
        self.nomadnet_stop_btn.set_tooltip_text("Stop NomadNet daemon")
        self.nomadnet_stop_btn.connect("clicked", lambda b: self._stop_nomadnet())
        btn_row.append(self.nomadnet_stop_btn)

        box.append(btn_row)

        # Config row - use lambdas to defer path resolution
        config_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        config_row.set_halign(Gtk.Align.CENTER)

        config_btn = Gtk.Button(label="Edit Config")
        config_btn.set_tooltip_text("Edit ~/.nomadnetwork/config")
        config_btn.connect("clicked", lambda b: self._edit_nomadnet_config())
        config_row.append(config_btn)

        # Open config folder
        folder_btn = Gtk.Button(label="Open Folder")
        folder_btn.set_tooltip_text("Open ~/.nomadnetwork folder")
        folder_btn.connect("clicked", lambda b: self._open_nomadnet_folder())
        config_row.append(folder_btn)

        box.append(config_row)

        # Links row
        links_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        links_row.set_halign(Gtk.Align.CENTER)

        docs_link = Gtk.LinkButton.new_with_label(
            "https://github.com/markqvist/NomadNet",
            "Documentation"
        )
        links_row.append(docs_link)

        reticulum_link = Gtk.LinkButton.new_with_label(
            "https://reticulum.network/",
            "Reticulum Network"
        )
        links_row.append(reticulum_link)

        box.append(links_row)

        # Testnet info (expandable)
        testnet_expander = Gtk.Expander(label="RNS Testnet Hubs")
        testnet_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        testnet_box.set_margin_start(10)
        testnet_box.set_margin_top(5)

        testnet_desc = Gtk.Label(label="Use Ctrl+U in NomadNet Network view to connect:")
        testnet_desc.set_xalign(0)
        testnet_desc.add_css_class("dim-label")
        testnet_box.append(testnet_desc)

        # Dublin hub
        dublin_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        dublin_label = Gtk.Label(label="Dublin: ")
        dublin_label.set_xalign(0)
        dublin_row.append(dublin_label)
        dublin_addr = Gtk.Label(label="abb3ebcd03cb2388a838e70c001291f9")
        dublin_addr.set_selectable(True)
        dublin_addr.add_css_class("monospace")
        dublin_row.append(dublin_addr)
        testnet_box.append(dublin_row)

        # Frankfurt hub
        frankfurt_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        frankfurt_label = Gtk.Label(label="Frankfurt: ")
        frankfurt_label.set_xalign(0)
        frankfurt_row.append(frankfurt_label)
        frankfurt_addr = Gtk.Label(label="ea6a715f814bdc37e56f80c34da6ad51")
        frankfurt_addr.set_selectable(True)
        frankfurt_addr.add_css_class("monospace")
        frankfurt_row.append(frankfurt_addr)
        testnet_box.append(frankfurt_row)

        testnet_expander.set_child(testnet_box)
        box.append(testnet_expander)

        frame.set_child(box)
        parent.append(frame)

        # Check status on load
        GLib.timeout_add(500, self._check_nomadnet_status)

    def _find_nomadnet(self):
        """Find nomadnet executable, checking user local bin if running as root"""
        # First check system PATH
        nomadnet_path = shutil.which('nomadnet')
        if nomadnet_path:
            return nomadnet_path

        # Check real user's local bin (for --user pip installs)
        real_home = self._get_real_user_home()
        user_local_bin = real_home / ".local" / "bin" / "nomadnet"
        if user_local_bin.exists():
            return str(user_local_bin)

        return None

    def _check_nomadnet_status(self):
        """Check if NomadNet daemon is running"""
        def check():
            try:
                running = False

                # Simple approach: use pgrep -f to find any process with "nomadnet" in cmdline
                result = subprocess.run(
                    ['pgrep', '-f', 'nomadnet'],
                    capture_output=True, text=True, timeout=5
                )

                if result.returncode == 0 and result.stdout.strip():
                    # Filter out any pgrep or grep processes from the PIDs
                    pids = result.stdout.strip().split('\n')
                    logger.debug(f"[RNS] pgrep found PIDs: {pids}")

                    # Verify at least one PID is actually nomadnet (not grep/pgrep)
                    for pid in pids:
                        try:
                            # Read the cmdline for this PID
                            cmdline_path = f"/proc/{pid.strip()}/cmdline"
                            with open(cmdline_path, 'r') as f:
                                cmdline = f.read().replace('\x00', ' ')

                            # Check it's actually nomadnet, not grep/pgrep
                            if 'nomadnet' in cmdline and 'grep' not in cmdline and 'pgrep' not in cmdline:
                                running = True
                                logger.debug(f"[RNS] NomadNet daemon running (PID {pid.strip()}): {cmdline[:80]}")
                                break
                        except (FileNotFoundError, PermissionError):
                            # Process may have exited
                            continue

                if not running:
                    logger.debug("[RNS] NomadNet daemon not detected")

                GLib.idle_add(self._update_nomadnet_status, running)
            except Exception as e:
                logger.debug(f"[RNS] Error checking nomadnet status: {e}")
                GLib.idle_add(self._update_nomadnet_status, False)

        threading.Thread(target=check, daemon=True).start()
        return False

    def _is_nomadnet_daemon_running(self):
        """Synchronously check if NomadNet daemon is running"""
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'nomadnet'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    try:
                        cmdline_path = f"/proc/{pid.strip()}/cmdline"
                        with open(cmdline_path, 'r') as f:
                            cmdline = f.read().replace('\x00', ' ')
                        if 'nomadnet' in cmdline and 'grep' not in cmdline and 'pgrep' not in cmdline:
                            return True
                    except (FileNotFoundError, PermissionError):
                        continue
            return False
        except Exception:
            return False

    def _update_nomadnet_status(self, running):
        """Update NomadNet status display"""
        nomadnet_path = self._find_nomadnet()

        if running:
            self.nomadnet_status_icon.set_from_icon_name("emblem-default-symbolic")
            self.nomadnet_status_label.set_label("NomadNet daemon running")
            self.nomadnet_daemon_btn.set_sensitive(False)
            self.nomadnet_stop_btn.set_sensitive(True)
        else:
            # Check if installed
            if nomadnet_path:
                self.nomadnet_status_icon.set_from_icon_name("media-playback-stop-symbolic")
                self.nomadnet_status_label.set_label("NomadNet installed (daemon stopped)")
                self.nomadnet_daemon_btn.set_sensitive(True)
                self.nomadnet_stop_btn.set_sensitive(False)
            else:
                self.nomadnet_status_icon.set_from_icon_name("dialog-warning-symbolic")
                self.nomadnet_status_label.set_label("NomadNet not installed")
                self.nomadnet_daemon_btn.set_sensitive(False)
                self.nomadnet_stop_btn.set_sensitive(False)
        return False

    def _launch_nomadnet(self, mode):
        """Launch NomadNet in specified mode"""
        logger.debug(f"[RNS] Launching NomadNet ({mode})...")

        # Disable button immediately to prevent double-clicks
        if mode == "textui" and hasattr(self, 'nomadnet_textui_btn'):
            self.nomadnet_textui_btn.set_sensitive(False)
            # Re-enable after 2 seconds
            GLib.timeout_add(2000, lambda: self.nomadnet_textui_btn.set_sensitive(True) or False)

        nomadnet_path = self._find_nomadnet()
        if not nomadnet_path:
            self.main_window.set_status_message("NomadNet not installed - install with: pip3 install nomadnet")
            logger.debug("[RNS] NomadNet not found")
            return

        logger.debug(f"[RNS] Found nomadnet at: {nomadnet_path}")

        # Check if running as root via sudo
        is_root = os.geteuid() == 0
        real_user = self._get_real_username()
        real_home = self._get_real_user_home()
        config_dir = real_home / ".nomadnetwork"

        # Find available terminal emulator
        terminals = ['lxterminal', 'xfce4-terminal', 'gnome-terminal', 'konsole', 'xterm']
        terminal = None
        for t in terminals:
            if shutil.which(t):
                terminal = t
                break

        if not terminal:
            self.main_window.set_status_message("No terminal emulator found - install lxterminal")
            logger.error("[RNS] No terminal emulator found")
            return

        try:
            if mode == "textui":
                # Stop all RNS-related processes to free ports
                # NomadNet needs exclusive access to RNS AutoInterface ports
                import time
                stopped_something = False

                # Stop NomadNet daemon first if running
                if self._is_nomadnet_daemon_running():
                    logger.debug("[RNS] Stopping NomadNet daemon")
                    subprocess.run(['pkill', '-9', '-f', 'nomadnet'], capture_output=True, timeout=5)
                    stopped_something = True

                # Stop MeshForge gateway bridge if running
                if hasattr(self, '_gateway_bridge') and self._gateway_bridge and self._gateway_bridge.is_running:
                    logger.debug("[RNS] Stopping gateway bridge")
                    self._gateway_bridge.stop()
                    self._gateway_bridge = None
                    stopped_something = True
                    GLib.idle_add(self._update_gateway_status)

                # Stop rnsd if running
                if hasattr(self, '_check_rns_service') and self._check_rns_service():
                    logger.debug("[RNS] Stopping rnsd")
                    subprocess.run(['pkill', '-9', '-f', 'rnsd'], capture_output=True, timeout=5)
                    stopped_something = True

                # If we stopped anything, wait for kernel to release sockets
                if stopped_something:
                    self.main_window.set_status_message("Releasing RNS ports...")
                    time.sleep(2.0)  # Wait for TIME_WAIT to clear
                    GLib.timeout_add(3000, lambda: self._refresh_all() or False)

                # Build the terminal command - wrap in bash to keep terminal open on exit
                # Check for ~/CONFIG first (custom RNS setup), fallback to ~/.nomadnetwork
                config_dir = real_home / "CONFIG"
                if not config_dir.exists():
                    config_dir = real_home / ".nomadnetwork"
                if is_root and real_user != 'root':
                    # Running as root but need to launch as real user
                    nomadnet_cmd = f"sudo -i -u {real_user} {nomadnet_path} --config {config_dir}"
                else:
                    nomadnet_cmd = f"{nomadnet_path} --config {config_dir}"

                # Different terminals have different exec syntax
                # Use bash -c 'cmd; read' format - tested working with lxterminal
                if terminal in ['lxterminal', 'xfce4-terminal']:
                    term_cmd = [terminal, '-e', f"bash -c '{nomadnet_cmd}; read'"]
                elif terminal == 'gnome-terminal':
                    term_cmd = [terminal, '--', 'bash', '-c', f"{nomadnet_cmd}; read"]
                elif terminal == 'konsole':
                    term_cmd = [terminal, '-e', 'bash', '-c', f"{nomadnet_cmd}; read"]
                else:  # xterm
                    term_cmd = [terminal, '-hold', '-e', nomadnet_cmd]

                logger.debug(f"[RNS] Terminal command: {term_cmd}")
                subprocess.Popen(
                    term_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True
                )
                self.main_window.set_status_message("NomadNet launched in terminal")
                return
            elif mode == "daemon":
                # Stop all RNS-related processes to free ports
                import time
                stopped_something = False

                if hasattr(self, '_gateway_bridge') and self._gateway_bridge and self._gateway_bridge.is_running:
                    logger.debug("[RNS] Stopping gateway bridge")
                    self._gateway_bridge.stop()
                    self._gateway_bridge = None
                    stopped_something = True
                    GLib.idle_add(self._update_gateway_status)

                if hasattr(self, '_check_rns_service') and self._check_rns_service():
                    logger.debug("[RNS] Stopping rnsd")
                    subprocess.run(['pkill', '-9', '-f', 'rnsd'], capture_output=True, timeout=5)
                    stopped_something = True

                if stopped_something:
                    time.sleep(2.0)  # Wait for TIME_WAIT to clear

                # Run as daemon using full path
                # When running as root, run as real user
                if is_root and real_user != 'root':
                    cmd = ['sudo', '-u', real_user, nomadnet_path, '--daemon']
                else:
                    cmd = [nomadnet_path, '--daemon']

                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True
                )
                self.main_window.set_status_message("NomadNet daemon started")
                logger.debug(f"[RNS] NomadNet daemon started (user: {real_user})")
                # Refresh status after a moment
                GLib.timeout_add(1000, self._check_nomadnet_status)
        except Exception as e:
            logger.debug(f"[RNS] Failed to launch NomadNet: {e}")
            self.main_window.set_status_message(f"Failed: {e}")

    def _stop_nomadnet(self):
        """Stop NomadNet daemon"""
        logger.debug("[RNS] Stopping NomadNet daemon...")
        try:
            result = subprocess.run(['pkill', '-f', 'nomadnet'], capture_output=True, timeout=10)
            if result.returncode == 0:
                self.main_window.set_status_message("NomadNet daemon stopped")
                logger.debug("[RNS] NomadNet stopped")
                # Refresh status
                GLib.timeout_add(500, self._check_nomadnet_status)
            else:
                self.main_window.set_status_message("NomadNet was not running")
        except Exception as e:
            logger.debug(f"[RNS] Failed to stop NomadNet: {e}")
            self.main_window.set_status_message(f"Failed: {e}")

    def _edit_nomadnet_config(self):
        """Edit NomadNet config file"""
        real_home = self._get_real_user_home()
        config_file = real_home / ".nomadnetwork" / "config"

        # If config doesn't exist or is empty, create default config
        if not config_file.exists() or config_file.stat().st_size == 0:
            self._create_default_nomadnet_config(config_file)

        self._edit_config_terminal(config_file)

    def _create_default_nomadnet_config(self, config_file):
        """Create a default NomadNet config file with sensible defaults"""
        default_config = '''# NomadNet Configuration File
# Edit this file to customize your NomadNet settings
# Reference: https://github.com/markqvist/NomadNet

[logging]
# Valid log levels are 0 through 7:
#   0: Log only critical information
#   1: Log errors and lower log levels
#   2: Log warnings and lower log levels
#   3: Log notices and lower log levels
#   4: Log info and lower (this is the default)
#   5: Verbose logging
#   6: Debug logging
#   7: Extreme logging

loglevel = 4
destination = file

[client]

enable_client = yes
user_interface = text
downloads_path = ~/Downloads
notify_on_new_message = yes

# Announce this peer at startup to let others reach it
announce_at_start = yes

# Try LXMF propagation network if direct delivery fails
try_propagation_on_send_fail = yes

# Periodically sync messages from propagation nodes
periodic_lxmf_sync = yes

# Sync interval in minutes (360 = 6 hours)
lxmf_sync_interval = 360

# Max messages to download per sync (0 = unlimited)
lxmf_sync_limit = 8

# Required stamp cost for inbound messages (0 = disabled)
# stamp_cost = 8

[textui]
# Text UI theme: dark, light
theme = dark

# Editor to use for composing messages
# editor = nano

# Hide guide on startup after first run
hide_guide = no

[node]
# Enable hosting a NomadNet node
enable_node = no

# Node name displayed to visitors
# node_name = My Node

# Enable as LXMF propagation node
enable_propagation = no

# Max message storage in MB
message_storage_limit = 2000
'''
        try:
            config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(config_file, 'w') as f:
                f.write(default_config)

            # Fix ownership if running as root
            real_user = self._get_real_username()
            if os.geteuid() == 0 and real_user != 'root':
                subprocess.run(['chown', '-R', f'{real_user}:{real_user}',
                               str(config_file.parent)], capture_output=True, timeout=10)

            logger.debug(f"[RNS] Created default NomadNet config: {config_file}")
            self.main_window.set_status_message("Created default NomadNet config")
        except Exception as e:
            logger.debug(f"[RNS] Failed to create config: {e}")

    def _open_nomadnet_folder(self):
        """Open NomadNet config folder"""
        real_home = self._get_real_user_home()
        folder = real_home / ".nomadnetwork"
        self._open_config_folder(folder)

    def _edit_config_terminal(self, config_file):
        """Open config file in terminal with nano/vim"""
        config_path = Path(config_file)
        logger.debug(f"[RNS] Opening config in terminal: {config_path}")

        # Get the real user for running commands
        real_user = self._get_real_username()
        is_root = os.geteuid() == 0

        # Create the config file if it doesn't exist (as the real user)
        if not config_path.exists():
            try:
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.touch()
                # Fix ownership if running as root
                if is_root and real_user != 'root':
                    subprocess.run(['chown', f'{real_user}:{real_user}', str(config_path)],
                                   capture_output=True, timeout=10)
                    subprocess.run(['chown', f'{real_user}:{real_user}', str(config_path.parent)],
                                   capture_output=True, timeout=10)
                logger.debug(f"[RNS] Created config file: {config_path}")
            except Exception as e:
                logger.debug(f"[RNS] Failed to create config: {e}")

        try:
            # Security: Use argument lists instead of shell=True to prevent command injection
            # Terminal configs: (binary, args_before_command, split_args)
            terminals = [
                ('lxterminal', ['-e'], False),      # lxterminal -e "command"
                ('xfce4-terminal', ['-e'], False),  # xfce4-terminal -e "command"
                ('gnome-terminal', ['--'], True),   # gnome-terminal -- cmd args
                ('konsole', ['-e'], True),          # konsole -e cmd args
                ('xterm', ['-e'], True),            # xterm -e cmd args
            ]

            # Build the editor command
            if is_root and real_user != 'root':
                # Run nano as the real user via sudo
                editor_cmd = ['sudo', '-i', '-u', real_user, 'nano', str(config_path)]
            else:
                editor_cmd = ['nano', str(config_path)]

            for term_name, term_args, split_args in terminals:
                term_path = shutil.which(term_name)
                if term_path:
                    try:
                        if split_args:
                            # Terminal expects command as separate arguments
                            full_cmd = [term_path] + term_args + editor_cmd
                        else:
                            # Terminal expects command as single string argument
                            cmd_string = ' '.join(shlex.quote(arg) for arg in editor_cmd)
                            full_cmd = [term_path] + term_args + [cmd_string]

                        logger.debug(f"[RNS] Using terminal: {term_name} (user: {real_user})")
                        logger.debug(f"[RNS] Command: {full_cmd}")
                        subprocess.Popen(full_cmd, start_new_session=True)
                        self.main_window.set_status_message(f"Editing {config_path.name} in terminal")
                        return
                    except Exception as e:
                        logger.debug(f"[RNS] Failed to launch {term_name}: {e}")
                        continue

            self.main_window.set_status_message("No terminal emulator found")
            logger.debug("[RNS] No terminal emulator found")
        except Exception as e:
            logger.debug(f"[RNS] Failed to open terminal editor: {e}")
            self.main_window.set_status_message(f"Failed to open editor: {e}")
