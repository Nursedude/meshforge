"""
Web Client Handler — Meshtastic web client management for the TUI.

Converted from web_client_mixin.py as part of the mixin-to-registry migration.
Provides web client launch, URL display, SSL certificate management, and
pre-flight health checks.
"""

import logging
import os
import socket
import subprocess
import threading

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class WebClientHandler(BaseHandler):
    """TUI handler for meshtasticd web client tools."""

    handler_id = "web_client"
    menu_section = "about"

    def menu_items(self):
        return [
            ("web", "Web Client          Open web interface", None),
        ]

    def execute(self, action):
        if action == "web":
            self._open_web_client()

    def _open_web_client(self):
        """Show/open Meshtastic web client served by MeshForge."""
        local_ip = "localhost"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            try:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
            finally:
                s.close()
        except OSError as e:
            logger.debug("Local IP detection failed: %s", e)

        meshforge_port = 5000
        web_url = f"http://{local_ip}:{meshforge_port}/mesh/"
        localhost_url = f"http://localhost:{meshforge_port}/mesh/"

        meshforge_ok = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(2)
                if sock.connect_ex(("localhost", meshforge_port)) == 0:
                    meshforge_ok = True
            finally:
                sock.close()
        except Exception as e:
            logger.debug("Socket check for MeshForge web server: %s", e)

        meshtasticd_ok = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(2)
                if sock.connect_ex(("localhost", 9443)) == 0:
                    meshtasticd_ok = True
            finally:
                sock.close()
        except Exception as e:
            logger.debug("Socket check for meshtasticd: %s", e)

        if not meshforge_ok:
            self.ctx.dialog.msgbox(
                "MeshForge Web Server Not Running",
                "MeshForge web server is NOT responding on port 5000.\n\n"
                "The web server starts with the NOC map.\n"
                "Check Maps & Viz > NOC Node Map to start it.\n\n"
                "Or start it from command line:\n"
                "  sudo python3 src/launcher_tui/main.py"
            )
            return

        if not meshtasticd_ok:
            self.ctx.dialog.msgbox(
                "meshtasticd Not Running",
                "meshtasticd is NOT responding on port 9443.\n\n"
                "The web client needs meshtasticd for radio access.\n\n"
                "1. Start meshtasticd:\n"
                "   sudo systemctl start meshtasticd\n\n"
                "2. Check status:\n"
                "   sudo systemctl status meshtasticd\n\n"
                "Note: Ensure config has Webserver section with Port: 9443"
            )
            return

        warnings = self._web_client_preflight()
        if warnings:
            warning_text = "\n".join(warnings)
            if not self.ctx.dialog.yesno(
                "Web Client Health Check",
                f"Potential issues detected:\n\n{warning_text}\n\n"
                "Open web client anyway?",
                default_no=True
            ):
                return

        while True:
            choices = [
                ("open", "Open in Browser      Launch in default browser"),
                ("url", "Show URLs            Copy to access from other devices"),
                ("ssl", "SSL Certificate      Fix warnings / generate cert"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Meshtastic Web Client",
                "Web Client available at port 5000/mesh/\n"
                "Served by MeshForge (multiplexed API proxy)\n\n"
                "Full radio configuration via browser:\n"
                "  Config, Channels, Device, Position, Messaging\n\n"
                "Requires a graphical browser (JavaScript).\n"
                "Use Show URLs to access from another device.",
                choices,
                height=20, width=65
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "open": ("Launch Browser", lambda: self._launch_web_client_browser(localhost_url)),
                "url": ("Show URLs", lambda: self._show_web_client_urls(local_ip)),
                "ssl": ("SSL Help", lambda: self._show_ssl_certificate_help(local_ip)),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _launch_web_client_browser(self, url: str):
        """Launch meshtasticd web client in browser."""
        has_display = bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))

        if not has_display:
            self.ctx.dialog.msgbox(
                "No Graphical Browser",
                f"No display detected (headless/SSH).\n\n"
                f"Access from a device with a browser:\n"
                f"  {url}\n\n"
                f"The web UI requires JavaScript.\n"
                f"Text browsers (lynx) cannot render it.",
                height=13, width=55
            )
            return

        import webbrowser

        def do_open():
            try:
                real_user = os.environ.get('SUDO_USER')
                if os.geteuid() == 0 and real_user:
                    subprocess.run(
                        ['sudo', '-u', real_user, 'xdg-open', url],
                        capture_output=True, timeout=10
                    )
                else:
                    subprocess.run(
                        ['xdg-open', url],
                        capture_output=True, timeout=10
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                try:
                    webbrowser.open(url)
                except (webbrowser.Error, OSError):
                    pass

        threading.Thread(target=do_open, daemon=True).start()

        self.ctx.dialog.msgbox(
            "Browser Launched",
            f"Opening: {url}\n\n"
            "If you see an SSL certificate warning:\n"
            "  Click 'Advanced' then 'Proceed'\n"
            "  Or generate a trusted cert (SSL menu)",
            height=11, width=55
        )

    def _show_web_client_urls(self, local_ip: str):
        """Show web client URLs for copying."""
        self.ctx.dialog.msgbox(
            "Web Client URLs",
            f"Access from THIS device:\n"
            f"  http://localhost:5000/mesh/\n\n"
            f"Access from OTHER devices on network:\n"
            f"  http://{local_ip}:5000/mesh/\n\n"
            f"MeshForge NOC Map:\n"
            f"  http://{local_ip}:5000/\n\n"
            f"Requires graphical browser (JavaScript).\n"
            f"Port 5000 = MeshForge (web client + NOC)\n"
            f"Port 4403 = TCP API (CLI/SDK)",
            height=18, width=60
        )

    def _show_ssl_certificate_help(self, local_ip: str):
        """Show SSL certificate menu with generation option."""
        try:
            from utils.ssl_cert import is_cert_installed
            cert_installed = is_cert_installed()
        except ImportError:
            cert_installed = False

        if cert_installed:
            status_line = "Trusted certificate: INSTALLED"
        else:
            status_line = "Trusted certificate: NOT installed (using meshtasticd default)"

        while True:
            choices = [
                ("generate", "Generate Trusted Cert   Eliminate browser warnings"),
                ("manual", "Manual Accept Help     Browser-specific instructions"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "SSL Certificate",
                f"{status_line}\n\n"
                "meshtasticd uses HTTPS with a self-signed certificate.\n"
                "This causes warnings in browsers and blocks lynx/curl.\n\n"
                "Generate a trusted certificate to fix this permanently.",
                choices,
                height=18, width=65
            )

            if choice is None or choice == "back":
                break

            if choice == "generate":
                self._generate_trusted_cert()
                try:
                    cert_installed = is_cert_installed()
                except Exception:
                    pass
            elif choice == "manual":
                self._show_manual_ssl_help()

    def _generate_trusted_cert(self):
        """Generate and install a trusted localhost SSL certificate."""
        try:
            from utils.ssl_cert import generate_localhost_cert
        except ImportError:
            self.ctx.dialog.msgbox(
                "Error",
                "SSL certificate module not available.\n"
                "Ensure src/utils/ssl_cert.py exists."
            )
            return

        if os.geteuid() != 0:
            self.ctx.dialog.msgbox(
                "Root Required",
                "Certificate generation requires root privileges.\n\n"
                "MeshForge should already be running with sudo.\n"
                "If not, restart with: sudo python3 src/launcher_tui/main.py"
            )
            return

        self.ctx.dialog.msgbox(
            "Generate Certificate",
            "This will generate a trusted SSL certificate\n"
            "for localhost and install it system-wide.\n\n"
            "Press OK to continue.",
            height=10, width=50
        )

        success, msg = generate_localhost_cert()

        if success:
            config_updated = self._update_meshtasticd_ssl_config()

            if config_updated:
                self._offer_meshtasticd_restart()
            else:
                self.ctx.dialog.msgbox(
                    "Certificate Generated",
                    "Trusted SSL certificate generated.\n\n"
                    "Could not update config.yaml automatically.\n"
                    "Add to Webserver section manually:\n"
                    "  SSLCert: /etc/meshtasticd/ssl/certificate.pem\n"
                    "  SSLKey: /etc/meshtasticd/ssl/private_key.pem",
                    height=12, width=60
                )
        else:
            self.ctx.dialog.msgbox("Certificate Error", msg, height=10, width=65)

    def _update_meshtasticd_ssl_config(self) -> bool:
        """Add SSL cert/key paths to meshtasticd config.yaml."""
        from pathlib import Path

        config_path = Path("/etc/meshtasticd/config.yaml")
        if not config_path.exists():
            return False

        try:
            content = config_path.read_text()
            cert_path = "/etc/meshtasticd/ssl/certificate.pem"
            key_path = "/etc/meshtasticd/ssl/private_key.pem"

            if f"SSLCert: {cert_path}" in content and f"SSLKey: {key_path}" in content:
                return True

            import re

            if "SSLCert:" in content:
                content = re.sub(
                    r'(\s+)SSLCert:.*',
                    rf'\1SSLCert: {cert_path}',
                    content
                )
                content = re.sub(
                    r'(\s+)SSLKey:.*',
                    rf'\1SSLKey: {key_path}',
                    content
                )
            elif "Webserver:" in content:
                content = re.sub(
                    r'(Webserver:.*?\n(?:\s+\w+:.*\n)*)',
                    rf'\1  SSLCert: {cert_path}\n'
                    rf'  SSLKey: {key_path}\n',
                    content
                )
            else:
                return False

            config_path.write_text(content)
            logger.info("Updated meshtasticd config with SSL cert paths")
            return True

        except Exception as e:
            logger.error("Failed to update meshtasticd SSL config: %s", e)
            return False

    def _offer_meshtasticd_restart(self):
        """Offer to restart meshtasticd after SSL cert update."""
        choice = self.ctx.dialog.yesno(
            "Restart meshtasticd?",
            "Certificate generated and config updated.\n\n"
            "meshtasticd must restart to use the new cert.\n"
            "Restart now?",
            height=10, width=50
        )
        if choice:
            from utils.service_check import apply_config_and_restart
            ok, restart_msg = apply_config_and_restart('meshtasticd')

            if ok:
                self.ctx.dialog.msgbox(
                    "Restarted",
                    "meshtasticd restarted with new SSL cert.\n"
                    "Web UI should now work without warnings.",
                    height=9, width=50
                )
            else:
                self.ctx.dialog.msgbox(
                    "Restart Failed",
                    f"Could not restart meshtasticd:\n{restart_msg}\n\n"
                    "Try manually:\n"
                    "  sudo systemctl restart meshtasticd",
                    height=12, width=55
                )
        else:
            self.ctx.dialog.msgbox(
                "Certificate Ready",
                "Restart meshtasticd when ready:\n"
                "  sudo systemctl restart meshtasticd",
                height=8, width=50
            )

    def _show_manual_ssl_help(self):
        """Show manual SSL certificate acceptance guidance."""
        self.ctx.dialog.msgbox(
            "Manual SSL Accept",
            "If you prefer to manually accept the certificate:\n\n"
            "Chrome/Edge:\n"
            "  1. Click 'Advanced'\n"
            "  2. Click 'Proceed to localhost (unsafe)'\n\n"
            "Firefox:\n"
            "  1. Click 'Advanced...'\n"
            "  2. Click 'Accept the Risk and Continue'\n\n"
            "Safari:\n"
            "  1. Click 'Show Details'\n"
            "  2. Click 'visit this website'\n\n"
            "lynx:\n"
            "  Type 'y' at the certificate prompt\n\n"
            "This must be done per-browser.\n"
            "Generate a trusted cert to avoid this entirely."
        )

    def _web_client_preflight(self):
        """Pre-flight check for issues that hang/crash the web client."""
        warnings = []

        try:
            from utils.meshtastic_http import get_http_client
            client = get_http_client()
            if client.is_available:
                nodes = client.get_nodes()
                phantom_count = 0
                for node in nodes:
                    has_name = bool(
                        (node.long_name or "").strip()
                        or (node.short_name or "").strip()
                    )
                    if not has_name:
                        phantom_count += 1

                if phantom_count > 0:
                    warnings.append(
                        f"[!] {phantom_count} phantom node(s) with no name data.\n"
                        "    Clicking these in search will crash the web client.\n"
                        "    Fix: Meshtasticd > Node DB Cleanup > Scan"
                    )
        except Exception as e:
            logger.debug("Preflight phantom node check failed: %s", e)

        try:
            result = subprocess.run(
                ['journalctl', '-u', 'meshtasticd', '--since', '5 min ago',
                 '--no-pager', '-q'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and 'queue is full' in result.stdout:
                warnings.append(
                    "[!] MQTT queue overflow detected (tophone queue full).\n"
                    "    Browser will hang. MQTT downlink is flooding the device.\n"
                    "    Fix: Meshtasticd > Node DB Cleanup > Check MaxNodes\n"
                    "    Or disable downlink: meshtastic --ch-index 0\n"
                    "         --ch-set downlink_enabled false"
                )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug("Preflight journal check failed: %s", e)

        return warnings
