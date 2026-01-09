"""
Install/Update Panel - Install and update meshtasticd
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading
from pathlib import Path


class InstallPanel(Gtk.Box):
    """Install and update panel"""

    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        self.main_window = main_window

        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(20)
        self.set_margin_bottom(20)

        self._build_ui()
        self._check_installed()

    def _build_ui(self):
        """Build the install panel UI"""
        # Title
        title = Gtk.Label(label="Install / Update Meshtasticd")
        title.add_css_class("title-1")
        title.set_xalign(0)
        self.append(title)

        # Current installation status
        status_frame = Gtk.Frame()
        status_frame.set_label("Current Installation")

        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        status_box.set_margin_start(15)
        status_box.set_margin_end(15)
        status_box.set_margin_top(10)
        status_box.set_margin_bottom(10)

        self.install_status = Gtk.Label(label="Checking...")
        self.install_status.set_xalign(0)
        status_box.append(self.install_status)

        self.version_label = Gtk.Label(label="Version: Checking...")
        self.version_label.set_xalign(0)
        status_box.append(self.version_label)

        self.cli_status = Gtk.Label(label="CLI: Checking...")
        self.cli_status.set_xalign(0)
        status_box.append(self.cli_status)

        check_btn = Gtk.Button(label="Check for Updates")
        check_btn.connect("clicked", lambda b: self._check_updates())
        check_btn.set_halign(Gtk.Align.START)
        status_box.append(check_btn)

        status_frame.set_child(status_box)
        self.append(status_frame)

        # Installation options
        install_frame = Gtk.Frame()
        install_frame.set_label("Installation Options")

        install_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        install_box.set_margin_start(15)
        install_box.set_margin_end(15)
        install_box.set_margin_top(10)
        install_box.set_margin_bottom(10)

        # Repository selection
        repo_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        repo_box.append(Gtk.Label(label="Repository:"))

        self.repo_dropdown = Gtk.DropDown.new_from_strings([
            "stable - Recommended for production",
            "beta - Latest beta releases",
            "daily - Cutting-edge builds",
            "alpha - Experimental"
        ])
        self.repo_dropdown.set_selected(0)
        repo_box.append(self.repo_dropdown)
        install_box.append(repo_box)

        # Info about OpenSUSE OBS
        info_label = Gtk.Label(
            label="Packages are installed from the OpenSUSE Build Service:\n"
                  "https://software.opensuse.org/download.html?project=network:Meshtastic"
        )
        info_label.set_xalign(0)
        info_label.add_css_class("dim-label")
        install_box.append(info_label)

        # Action buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_halign(Gtk.Align.CENTER)
        button_box.set_margin_top(10)

        self.install_btn = Gtk.Button(label="Install Meshtasticd")
        self.install_btn.add_css_class("suggested-action")
        self.install_btn.connect("clicked", lambda b: self._do_install())
        button_box.append(self.install_btn)

        self.update_btn = Gtk.Button(label="Update Meshtasticd")
        self.update_btn.connect("clicked", lambda b: self._do_update())
        button_box.append(self.update_btn)

        self.install_cli_btn = Gtk.Button(label="Install CLI (pipx)")
        self.install_cli_btn.connect("clicked", lambda b: self._install_cli())
        button_box.append(self.install_cli_btn)

        install_box.append(button_box)
        install_frame.set_child(install_box)
        self.append(install_frame)

        # Dependencies frame
        deps_frame = Gtk.Frame()
        deps_frame.set_label("Dependencies")

        deps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        deps_box.set_margin_start(15)
        deps_box.set_margin_end(15)
        deps_box.set_margin_top(10)
        deps_box.set_margin_bottom(10)

        self.deps_status = Gtk.Label(label="Click 'Check' to verify dependencies")
        self.deps_status.set_xalign(0)
        deps_box.append(self.deps_status)

        deps_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        check_deps_btn = Gtk.Button(label="Check Dependencies")
        check_deps_btn.connect("clicked", lambda b: self._check_dependencies())
        deps_btn_box.append(check_deps_btn)

        fix_deps_btn = Gtk.Button(label="Fix Dependencies")
        fix_deps_btn.connect("clicked", lambda b: self._fix_dependencies())
        deps_btn_box.append(fix_deps_btn)

        deps_box.append(deps_btn_box)
        deps_frame.set_child(deps_box)
        self.append(deps_frame)

        # Progress and output
        output_frame = Gtk.Frame()
        output_frame.set_label("Installation Progress")
        output_frame.set_vexpand(True)

        output_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)

        # Progress bar
        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_margin_start(10)
        self.progress.set_margin_end(10)
        self.progress.set_margin_top(5)
        output_box.append(self.progress)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.output_text = Gtk.TextView()
        self.output_text.set_editable(False)
        self.output_text.set_monospace(True)
        self.output_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scrolled.set_child(self.output_text)

        output_box.append(scrolled)
        output_frame.set_child(output_box)
        self.append(output_frame)

    def _check_installed(self):
        """Check current installation status"""
        def check():
            # Check meshtasticd
            try:
                result = subprocess.run(
                    ['meshtasticd', '--version'],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    version = result.stdout.strip()
                    GLib.idle_add(self._update_status, True, version)
                else:
                    GLib.idle_add(self._update_status, False, None)
            except FileNotFoundError:
                GLib.idle_add(self._update_status, False, None)

            # Check CLI
            import shutil
            cli_available = shutil.which("meshtastic") or shutil.which("/root/.local/bin/meshtastic")
            GLib.idle_add(self._update_cli_status, cli_available)

        thread = threading.Thread(target=check)
        thread.daemon = True
        thread.start()

    def _update_status(self, installed, version):
        """Update installation status UI"""
        if installed:
            self.install_status.set_label("Status: Installed")
            self.install_status.add_css_class("success")
            self.version_label.set_label(f"Version: {version}")
            self.install_btn.set_label("Reinstall")
        else:
            self.install_status.set_label("Status: Not Installed")
            self.install_status.add_css_class("warning")
            self.version_label.set_label("Version: N/A")
        return False

    def _update_cli_status(self, available):
        """Update CLI status"""
        if available:
            self.cli_status.set_label("CLI: Installed")
            self.cli_status.add_css_class("success")
        else:
            self.cli_status.set_label("CLI: Not Installed")
            self.cli_status.add_css_class("warning")
        return False

    def _get_repo_channel(self):
        """Get selected repository channel"""
        channels = ["stable", "beta", "daily", "alpha"]
        return channels[self.repo_dropdown.get_selected()]

    def _do_install(self):
        """Perform installation"""
        channel = self._get_repo_channel()

        if channel in ["daily", "alpha"]:
            self.main_window.show_confirm_dialog(
                "Warning",
                f"The {channel} repository may contain unstable builds. Continue?",
                lambda confirmed: self._run_install(channel) if confirmed else None
            )
        else:
            self._run_install(channel)

    def _run_install(self, channel):
        """Run the installation"""
        self.progress.set_fraction(0)
        self.progress.set_text("Starting installation...")
        self._append_output(f"Installing meshtasticd from {channel} repository...\n")

        def install():
            try:
                # Determine architecture
                import platform
                arch = platform.machine()

                # Find install script
                script_dir = Path(__file__).parent.parent.parent.parent / "scripts"

                if arch in ['aarch64', 'arm64']:
                    script = script_dir / "install_arm64.sh"
                elif arch in ['armv7l', 'armhf']:
                    script = script_dir / "install_arm.sh"
                else:
                    script = script_dir / "install_arm64.sh"  # Default

                if not script.exists():
                    GLib.idle_add(
                        self._append_output,
                        f"Error: Install script not found at {script}\n"
                    )
                    return

                # Run install script
                env = {"REPO_CHANNEL": channel}
                process = subprocess.Popen(
                    ['bash', str(script)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env={**subprocess.os.environ, **env}
                )

                for line in process.stdout:
                    GLib.idle_add(self._append_output, line)
                    GLib.idle_add(self._pulse_progress)

                process.wait()

                if process.returncode == 0:
                    GLib.idle_add(self._install_complete, True)
                else:
                    GLib.idle_add(self._install_complete, False)

            except Exception as e:
                GLib.idle_add(self._append_output, f"Error: {e}\n")
                GLib.idle_add(self._install_complete, False)

        thread = threading.Thread(target=install)
        thread.daemon = True
        thread.start()

    def _do_update(self):
        """Perform update"""
        self.progress.set_fraction(0)
        self.progress.set_text("Updating...")
        self._append_output("Updating meshtasticd...\n")

        def update():
            try:
                # Update package lists
                process = subprocess.Popen(
                    ['apt-get', 'update'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )

                for line in process.stdout:
                    GLib.idle_add(self._append_output, line)
                    GLib.idle_add(self._pulse_progress)

                process.wait()

                # Upgrade meshtasticd
                process = subprocess.Popen(
                    ['apt-get', 'install', '-y', '--only-upgrade', 'meshtasticd'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )

                for line in process.stdout:
                    GLib.idle_add(self._append_output, line)
                    GLib.idle_add(self._pulse_progress)

                process.wait()

                if process.returncode == 0:
                    GLib.idle_add(self._install_complete, True)
                else:
                    GLib.idle_add(self._install_complete, False)

            except Exception as e:
                GLib.idle_add(self._append_output, f"Error: {e}\n")
                GLib.idle_add(self._install_complete, False)

        thread = threading.Thread(target=update)
        thread.daemon = True
        thread.start()

    def _install_cli(self):
        """Install meshtastic CLI via pipx"""
        self._append_output("Installing meshtastic CLI via pipx...\n")

        def install():
            try:
                # Install pipx if needed
                process = subprocess.Popen(
                    ['apt-get', 'install', '-y', 'pipx'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )

                for line in process.stdout:
                    GLib.idle_add(self._append_output, line)

                process.wait()

                # Install meshtastic
                process = subprocess.Popen(
                    ['pipx', 'install', 'meshtastic[cli]', '--force'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )

                for line in process.stdout:
                    GLib.idle_add(self._append_output, line)

                process.wait()

                # Ensure path
                subprocess.run(['pipx', 'ensurepath'], capture_output=True, timeout=30)

                GLib.idle_add(self._append_output, "\nCLI installation complete!\n")
                GLib.idle_add(self._check_installed)

            except Exception as e:
                GLib.idle_add(self._append_output, f"Error: {e}\n")

        thread = threading.Thread(target=install)
        thread.daemon = True
        thread.start()

    def _check_updates(self):
        """Check for available updates"""
        self._append_output("Checking for updates...\n")

        def check():
            try:
                # Update apt cache
                subprocess.run(['apt-get', 'update'], capture_output=True, timeout=300)

                # Check if update available
                result = subprocess.run(
                    ['apt-cache', 'policy', 'meshtasticd'],
                    capture_output=True, text=True, timeout=10
                )

                GLib.idle_add(self._append_output, result.stdout + "\n")

            except Exception as e:
                GLib.idle_add(self._append_output, f"Error: {e}\n")

        thread = threading.Thread(target=check)
        thread.daemon = True
        thread.start()

    def _check_dependencies(self):
        """Check dependencies"""
        self.deps_status.set_label("Checking dependencies...")

        def check():
            issues = []

            # Check SPI
            if not Path('/dev/spidev0.0').exists():
                issues.append("SPI not enabled")

            # Check I2C
            if not Path('/dev/i2c-1').exists():
                issues.append("I2C not enabled")

            # Check gpio group
            import grp
            try:
                grp.getgrnam('gpio')
            except KeyError:
                issues.append("gpio group not found")

            if issues:
                GLib.idle_add(
                    self.deps_status.set_label,
                    f"Issues found: {', '.join(issues)}"
                )
            else:
                GLib.idle_add(
                    self.deps_status.set_label,
                    "All dependencies OK"
                )

        thread = threading.Thread(target=check)
        thread.daemon = True
        thread.start()

    def _fix_dependencies(self):
        """Fix dependencies"""
        self._append_output("Fixing dependencies...\n")

        def fix():
            try:
                # Enable SPI
                if not Path('/dev/spidev0.0').exists():
                    GLib.idle_add(self._append_output, "Enabling SPI...\n")
                    subprocess.run(['raspi-config', 'nonint', 'do_spi', '0'], capture_output=True, timeout=30)

                # Enable I2C
                if not Path('/dev/i2c-1').exists():
                    GLib.idle_add(self._append_output, "Enabling I2C...\n")
                    subprocess.run(['raspi-config', 'nonint', 'do_i2c', '0'], capture_output=True, timeout=30)

                GLib.idle_add(
                    self._append_output,
                    "\nDependencies fixed. A reboot may be required.\n"
                )

                # Ask for reboot
                GLib.idle_add(
                    self.main_window.request_reboot,
                    "SPI/I2C settings changed"
                )

            except Exception as e:
                GLib.idle_add(self._append_output, f"Error: {e}\n")

        thread = threading.Thread(target=fix)
        thread.daemon = True
        thread.start()

    def _append_output(self, text):
        """Append text to output"""
        buffer = self.output_text.get_buffer()
        buffer.insert(buffer.get_end_iter(), text)
        self.output_text.scroll_to_iter(buffer.get_end_iter(), 0, False, 0, 0)
        return False

    def _pulse_progress(self):
        """Pulse the progress bar"""
        self.progress.pulse()
        return False

    def _install_complete(self, success):
        """Handle installation completion"""
        if success:
            self.progress.set_fraction(1.0)
            self.progress.set_text("Complete!")
            self._append_output("\nInstallation complete!\n")
            self.main_window.set_status_message("Installation complete")
        else:
            self.progress.set_text("Failed")
            self._append_output("\nInstallation failed. Check output for errors.\n")
            self.main_window.set_status_message("Installation failed")

        self._check_installed()
        return False
