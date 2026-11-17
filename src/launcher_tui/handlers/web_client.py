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
        from utils.web_pair import PAIR_TUI_LABEL
        return [
            ("web", "Web Client          Open web interface", None),
            # Its own row, NOT behind the Web Client gate: pairing needs only
            # the map, and that gate refuses when meshtasticd is down.
            ("weblogin", f"{PAIR_TUI_LABEL:<20}Log a browser in to send", None),
        ]

    def execute(self, action):
        if action == "web":
            self._open_web_client()
        elif action == "weblogin":
            self._show_operator_login(self._local_ip())

    @staticmethod
    def _local_ip() -> str:
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
        return local_ip

    def _open_web_client(self):
        """Show/open Meshtastic web client served by MeshForge."""
        local_ip = self._local_ip()

        meshforge_port = 5000
        web_url = f"http://{local_ip}:{meshforge_port}/mesh/"
        localhost_url = f"http://localhost:{meshforge_port}/mesh/"

        from utils.service_check import check_port
        meshforge_ok = check_port(meshforge_port, timeout=2)
        meshtasticd_ok = check_port(9443, timeout=2)

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
                "Start and check meshtasticd from Service Control, then retry.\n\n"
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
                ("login", "Operator Login       Who can send from the web"),
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
                "login": ("Operator Login", lambda: self._show_operator_login(local_ip)),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
            else:
                self.ctx.notify_unwired(choice, "WebClientHandler._open_web_client")

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
            f"  http://{local_ip}:5000/\n"
            f"Chat inbox (send needs an operator login):\n"
            f"  http://{local_ip}:5000/inbox\n\n"
            f"Requires graphical browser (JavaScript).\n"
            f"Port 5000 = MeshForge (web client + NOC)\n"
            f"Port 4403 = TCP API (CLI/SDK)",
            height=20, width=60
        )

    def _show_operator_login(self, local_ip: str):
        """BIC-3 login state + "Pair a browser" (2026-11-16, operator go).

        The TUI still never MINTS: ``web_pair`` runs the one minting CLI as the
        map unit's User= (a root-minted store locks every browser out).
        """
        from utils import web_auth, web_pair
        while True:
            state = self._map_store_state()
            # Revoke BEFORE the status block: a long UNKNOWN reason on a 24-row
            # terminal clipped the command off the bottom (review r5-5).
            choice = self.ctx.dialog.menu(
                "Operator Login (web)",
                "Viewing the map needs no login. Sending and changing\n"
                "settings from a browser need an operator session.\n"
                "Log every browser out (shell, as the operator):\n"
                f"  {web_auth.REVOKE_COMMAND}\n\n"
                f"{state}",
                [("pair", f"{web_pair.PAIR_TUI_ACTION:<20}Show a one-time login code"),
                 ("back", "Back")],
                height=22, width=72
            )
            if choice != "pair":
                return
            self._pair_browser(local_ip)

    @staticmethod
    def _map_store_state() -> str:
        """Status of the store the MAP reads (the one pairing writes to) —
        never the TUI user's own CONFIG_DIR, which under sudo can differ."""
        import time
        from utils import web_auth, web_pair
        m = web_pair.map_unit()
        user, active = m.user, m.active
        if user is None:
            fix = "" if m.remedy == "shell" else f"\n{web_pair.remedy_text(m.remedy, '')}"
            return f"UNKNOWN - cannot name the map's session store:\n{m.why}{fix}"
        service = f"Map service:            {active or 'unknown'}\n"
        problem = web_pair.store_access_problem(user)
        if problem:
            return f"{service}UNKNOWN - {problem}"
        store = web_auth.SessionStore(web_pair.store_path_for(user))
        # ONE guard over the whole read-AND-format step (r2-F4 then r3-C2: the
        # same kind twice). status() lives in twin-locked web_auth and parses
        # rows as-is; any damaged value — wrong type, out of time_t range,
        # inf/nan, a huge int — must leave the menu (and Pair) drawable.
        try:
            st = store.status()
            last = st["last_used"]
            when = "never" if last is None else time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(last))
            return (f"{service}"
                    f"Live operator sessions: {st['sessions']}\n"
                    f"Last used:              {when}\n"
                    f"Pairing code pending:   {'yes' if st['pairing_pending'] else 'no'}")
        except web_auth.StoreUnavailable as e:
            return f"{service}UNKNOWN - the map's session store could not be read:\n{e}"
        except Exception as e:  # noqa: BLE001 — witnessed: shown + logged, never silent
            logger.warning("Web Login status: session store not summarisable: %s: %s",
                           e.__class__.__name__, e)
            return (f"{service}UNKNOWN - the map's session store could not be summarised "
                    f"({e.__class__.__name__}); a row may be damaged. Pairing still works.")

    def _pair_browser(self, local_ip: str):
        from utils import web_auth, web_pair
        r = web_pair.pair_browser()
        url = f"http://{local_ip}:5000/login"     # the unit's map (option B: no unit, no pairing)
        if r.ok:
            mins = web_auth.PAIRING_TTL_S // 60
            until = web_pair.expiry_hhmm(r.expires)
            valid = f"Valid until {until} ({mins} minutes)" if until else f"Valid {mins} minutes"
            self.ctx.dialog.msgbox(
                "Pairing Code",
                f"Code:   {r.code}\n\n"
                f"On the phone or browser, open:\n  {url}\n"
                "and type the code.\n\n"
                f"{valid}, once; "
                f"{web_auth.PAIRING_MAX_TRIES} wrong tries burn it."
                + (f"\n\nNote: {r.message}" if r.message else ""),
                height=18 if r.message else 14, width=64
            )
            return
        # The remedy FIRST and never capped: whiptail clips a long box (F8),
        # and the remedy is the refusal's OWN (r6-1) — the shell fallback only
        # where it can work. Only the reason text is capped.
        reason = r.message if len(r.message) <= 240 else r.message[:237] + "..."
        minted = f"\n\nA code WAS printed ({r.code}) but may not work." if r.code else ""
        self.ctx.dialog.msgbox(
            "Pairing Failed",
            "No usable code.\n"
            f"{web_pair.remedy_text(r.remedy, url)}\n\n"
            f"Why: {reason}{minted}",
            height=20, width=72
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

            # Don't write — or report success for — a config our regex edit may
            # have malformed: re-parse first. A True returned on a broken edit
            # makes _offer_meshtasticd_restart claim the Web UI will work
            # (honest_failure #3 — a validator must reject what can't be meant).
            try:
                import yaml
                yaml.safe_load(content)
            except Exception as e:
                logger.error("SSL config edit produced invalid YAML; not writing: %s", e)
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
                    "meshtasticd restarted with the new SSL cert.\n"
                    "Open the Web UI over HTTPS to verify the certificate "
                    "loaded — the browser warning should be gone.",
                    height=9, width=55
                )
            else:
                self.ctx.dialog.msgbox(
                    "Restart Failed",
                    f"Could not restart meshtasticd:\n{restart_msg}\n\n"
                    "Restart it from Service Control, or relaunch in Admin mode.",
                    height=12, width=55
                )
        else:
            self.ctx.dialog.msgbox(
                "Certificate Ready",
                "Restart meshtasticd to apply the certificate.",
                height=8, width=50
            )
            from service_remediation import offer_service_fix
            offer_service_fix(self.ctx, "meshtasticd", running=True)

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
