"""
Web Client Mixin for MeshForge Launcher TUI.

Provides meshtasticd web client related methods extracted from main launcher
to reduce file size and improve maintainability.

Includes:
- Open web client menu
- Browser launch with proper sudo handling
- URL display for network access
- SSL certificate help
"""

import logging
import os
import socket
import subprocess
import threading

logger = logging.getLogger(__name__)


class WebClientMixin:
    """Mixin providing meshtasticd web client tools for the TUI launcher."""

    def _open_web_client(self):
        """Show/open meshtasticd web client for full radio configuration."""
        import webbrowser

        # Get local IP for network access
        local_ip = "localhost"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            try:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
            finally:
                s.close()
        except Exception:
            pass

        web_url = f"https://{local_ip}:9443"
        localhost_url = "https://localhost:9443"

        # Check if web server is responding (try localhost first, then IP)
        port_ok = False
        for check_host in ["localhost", local_ip]:
            if check_host == local_ip and local_ip == "localhost":
                continue  # Skip duplicate check
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.settimeout(2)
                    if sock.connect_ex((check_host, 9443)) == 0:
                        port_ok = True
                        break
                finally:
                    sock.close()
            except Exception as e:
                logger.debug(f"Socket check for web client ({check_host}): {e}")

        if not port_ok:
            # Web client not responding - show setup help
            self.dialog.msgbox(
                "Web Client Not Running",
                "meshtasticd Web Client is NOT responding.\n\n"
                "To enable the web interface:\n\n"
                "1. Start meshtasticd:\n"
                "   sudo systemctl start meshtasticd\n\n"
                "2. Check status:\n"
                "   sudo systemctl status meshtasticd\n\n"
                "3. View logs for errors:\n"
                "   sudo journalctl -u meshtasticd -f\n\n"
                "Note: Ensure config has Webserver section with Port: 9443"
            )
            return

        # Web client is running - show options
        while True:
            choices = [
                ("open", "Open in Browser      Launch in default browser"),
                ("url", "Show URLs            Copy to access from other devices"),
                ("ssl", "SSL Certificate      First-time setup help"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Meshtastic Web Client",
                "Web Client is RUNNING on port 9443\n\n"
                "Full radio configuration via browser:\n"
                "  Config → LoRa      Region, Preset, TX Power\n"
                "  Config → Channels  PSK keys, channel names\n"
                "  Config → Device    Node name, role, position\n\n"
                "Also: messaging, node map, telemetry",
                choices,
                height=20, width=65
            )

            if choice is None or choice == "back":
                break
            elif choice == "open":
                self._launch_web_client_browser(localhost_url)
            elif choice == "url":
                self._show_web_client_urls(local_ip)
            elif choice == "ssl":
                self._show_ssl_certificate_help(local_ip)

    def _launch_web_client_browser(self, url: str):
        """Launch meshtasticd web client in browser."""
        import webbrowser

        def do_open():
            try:
                # When running as root, use sudo -u to run as real user
                real_user = os.environ.get('SUDO_USER')
                if os.geteuid() == 0 and real_user:
                    subprocess.run(
                        ['sudo', '-u', real_user, 'xdg-open', url],
                        capture_output=True,
                        timeout=10
                    )
                else:
                    subprocess.run(
                        ['xdg-open', url],
                        capture_output=True,
                        timeout=10
                    )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                try:
                    webbrowser.open(url)
                except (webbrowser.Error, OSError):
                    pass

        threading.Thread(target=do_open, daemon=True).start()

        self.dialog.msgbox(
            "Browser Launched",
            f"Opening: {url}\n\n"
            "If this is your first time:\n"
            "1. Browser will show SSL certificate warning\n"
            "2. Click 'Advanced' → 'Proceed' to accept\n"
            "3. This is normal for self-signed certificates\n\n"
            "The certificate is generated by meshtasticd\n"
            "and is safe to accept for local access."
        )

    def _show_web_client_urls(self, local_ip: str):
        """Show web client URLs for copying."""
        self.dialog.msgbox(
            "Web Client URLs",
            f"Access from THIS device:\n"
            f"  https://localhost:9443\n\n"
            f"Access from OTHER devices on network:\n"
            f"  https://{local_ip}:9443\n\n"
            f"Mobile/tablet (same network):\n"
            f"  Use the IP address above\n\n"
            f"Note: All devices must accept the\n"
            f"SSL certificate on first access.\n\n"
            f"Port 9443 = HTTPS Web UI\n"
            f"Port 4403 = TCP API (CLI/SDK)"
        )

    def _show_ssl_certificate_help(self, local_ip: str):
        """Show SSL certificate acceptance guidance."""
        self.dialog.msgbox(
            "SSL Certificate Help",
            "meshtasticd uses a self-signed SSL certificate.\n"
            "Browsers show a warning - this is expected.\n\n"
            "To accept the certificate:\n\n"
            "Chrome/Edge:\n"
            "  1. Click 'Advanced'\n"
            "  2. Click 'Proceed to localhost (unsafe)'\n\n"
            "Firefox:\n"
            "  1. Click 'Advanced...'\n"
            "  2. Click 'Accept the Risk and Continue'\n\n"
            "Safari:\n"
            "  1. Click 'Show Details'\n"
            "  2. Click 'visit this website'\n\n"
            "This only needs to be done ONCE per browser.\n"
            "The certificate is local and safe to accept."
        )
