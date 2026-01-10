"""
RNS-Meshtastic Gateway Section for RNS Panel

Bridge Reticulum and Meshtastic networks for unified mesh communication.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib
import subprocess
import threading
import json
import os
from pathlib import Path
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Import path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()

# Import service availability checker
try:
    from utils.service_check import check_service, ServiceState
except ImportError:
    check_service = None
    ServiceState = None


class GatewayMixin:
    """
    Mixin class providing gateway functionality for RNSPanel.

    Expects the panel to have:
    - main_window: Reference to main application window
    - _get_real_user_home(): Method to get real user's home directory
    - _get_real_username(): Method to get real username
    - _edit_config_terminal(path): Method to edit config in terminal
    - _install_meshtastic_interface(): Method to install interface
    - _add_meshtastic_interface_config(): Method to add config template
    - _edit_meshtastic_interface(): Method to edit interface file
    """

    def _build_gateway_section(self, parent):
        """Build RNS-Meshtastic gateway section"""
        frame = Gtk.Frame()
        frame.set_label("RNS-Meshtastic Gateway")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Description
        desc = Gtk.Label(label="Bridge Reticulum and Meshtastic networks for unified mesh communication")
        desc.set_xalign(0)
        desc.set_wrap(True)
        desc.add_css_class("dim-label")
        box.append(desc)

        # Gateway status row
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)

        self.gateway_status_icon = Gtk.Image.new_from_icon_name("network-offline-symbolic")
        self.gateway_status_icon.set_pixel_size(24)
        status_row.append(self.gateway_status_icon)

        status_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.gateway_status_label = Gtk.Label(label="Gateway: Stopped")
        self.gateway_status_label.set_xalign(0)
        self.gateway_status_label.add_css_class("heading")
        status_info.append(self.gateway_status_label)

        self.gateway_detail_label = Gtk.Label(label="Not running")
        self.gateway_detail_label.set_xalign(0)
        self.gateway_detail_label.add_css_class("dim-label")
        status_info.append(self.gateway_detail_label)
        status_row.append(status_info)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        status_row.append(spacer)

        # Gateway control buttons
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)

        self.gateway_start_btn = Gtk.Button(label="Start")
        self.gateway_start_btn.add_css_class("suggested-action")
        self.gateway_start_btn.connect("clicked", self._on_gateway_start)
        btn_box.append(self.gateway_start_btn)

        self.gateway_stop_btn = Gtk.Button(label="Stop")
        self.gateway_stop_btn.add_css_class("destructive-action")
        self.gateway_stop_btn.connect("clicked", self._on_gateway_stop)
        self.gateway_stop_btn.set_sensitive(False)
        btn_box.append(self.gateway_stop_btn)

        status_row.append(btn_box)
        box.append(status_row)

        # Gateway enable switch
        enable_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        enable_label = Gtk.Label(label="Enable Gateway:")
        enable_label.set_xalign(0)
        enable_row.append(enable_label)

        self.gateway_enable_switch = Gtk.Switch()
        self.gateway_enable_switch.connect("state-set", self._on_gateway_enable_changed)
        enable_row.append(self.gateway_enable_switch)

        spacer2 = Gtk.Box()
        spacer2.set_hexpand(True)
        enable_row.append(spacer2)

        box.append(enable_row)

        # Connection status
        conn_frame = Gtk.Frame()
        conn_frame.set_label("Connection Status")
        conn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        conn_box.set_margin_start(10)
        conn_box.set_margin_end(10)
        conn_box.set_margin_top(8)
        conn_box.set_margin_bottom(8)

        # Meshtastic connection
        mesh_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.mesh_conn_icon = Gtk.Image.new_from_icon_name("dialog-question-symbolic")
        self.mesh_conn_icon.set_pixel_size(16)
        mesh_box.append(self.mesh_conn_icon)
        self.mesh_conn_label = Gtk.Label(label="Meshtastic: Unknown")
        mesh_box.append(self.mesh_conn_label)
        conn_box.append(mesh_box)

        # RNS connection
        rns_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.rns_conn_icon = Gtk.Image.new_from_icon_name("dialog-question-symbolic")
        self.rns_conn_icon.set_pixel_size(16)
        rns_box.append(self.rns_conn_icon)
        self.rns_conn_label = Gtk.Label(label="RNS: Unknown")
        rns_box.append(self.rns_conn_label)
        conn_box.append(rns_box)

        conn_frame.set_child(conn_box)
        box.append(conn_frame)

        # Test and configure buttons
        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        action_row.set_halign(Gtk.Align.CENTER)
        action_row.set_margin_top(5)

        test_btn = Gtk.Button(label="Test Connections")
        test_btn.connect("clicked", self._on_test_gateway)
        action_row.append(test_btn)

        config_btn = Gtk.Button(label="Configure")
        config_btn.set_tooltip_text("Open GUI config editor")
        config_btn.connect("clicked", self._on_configure_gateway)
        action_row.append(config_btn)

        # Terminal editor for gateway config
        config_terminal_btn = Gtk.Button(label="Edit (Terminal)")
        config_terminal_btn.set_tooltip_text("Edit gateway.json in terminal with nano")
        config_terminal_btn.connect("clicked", self._on_edit_gateway_terminal)
        action_row.append(config_terminal_btn)

        view_nodes_btn = Gtk.Button(label="View Nodes")
        view_nodes_btn.connect("clicked", self._on_view_nodes)
        action_row.append(view_nodes_btn)

        # Diagnostic wizard button
        diag_btn = Gtk.Button(label="Diagnose")
        diag_btn.set_tooltip_text("Run gateway setup diagnostic wizard")
        diag_btn.add_css_class("suggested-action")
        diag_btn.connect("clicked", self._on_run_diagnostic)
        action_row.append(diag_btn)

        box.append(action_row)

        # Statistics
        stats_expander = Gtk.Expander(label="Statistics")
        stats_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        stats_box.set_margin_start(10)
        stats_box.set_margin_top(5)

        self.stats_labels = {}
        for stat_name in ["Messages Mesh→RNS", "Messages RNS→Mesh", "Total Nodes", "Errors"]:
            stat_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            name_lbl = Gtk.Label(label=f"{stat_name}:")
            name_lbl.set_xalign(0)
            name_lbl.set_size_request(150, -1)
            stat_row.append(name_lbl)
            val_lbl = Gtk.Label(label="0")
            val_lbl.set_xalign(0)
            self.stats_labels[stat_name] = val_lbl
            stat_row.append(val_lbl)
            stats_box.append(stat_row)

        stats_expander.set_child(stats_box)
        box.append(stats_expander)

        # Meshtastic Interface Setup
        mesh_iface_expander = Gtk.Expander(label="Meshtastic Interface Setup")
        mesh_iface_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        mesh_iface_box.set_margin_start(10)
        mesh_iface_box.set_margin_top(5)
        mesh_iface_box.set_margin_bottom(5)

        mesh_desc = Gtk.Label(
            label="Install the RNS-Meshtastic interface to bridge networks.\n"
                  "Requires: pip install meshtastic"
        )
        mesh_desc.set_xalign(0)
        mesh_desc.add_css_class("dim-label")
        mesh_desc.set_wrap(True)
        mesh_iface_box.append(mesh_desc)

        # Install button row
        install_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        install_iface_btn = Gtk.Button(label="Install Interface")
        install_iface_btn.set_tooltip_text("Download Meshtastic_Interface.py to ~/.reticulum/interfaces/")
        install_iface_btn.connect("clicked", self._install_meshtastic_interface)
        install_row.append(install_iface_btn)

        add_config_btn = Gtk.Button(label="Add Config Template")
        add_config_btn.set_tooltip_text("Add Meshtastic Interface config to RNS config file")
        add_config_btn.connect("clicked", self._add_meshtastic_interface_config)
        install_row.append(add_config_btn)

        # Edit interface file button
        edit_iface_btn = Gtk.Button(label="Edit Interface")
        edit_iface_btn.set_tooltip_text("Edit Meshtastic_Interface.py in terminal (set speed, connection type)")
        edit_iface_btn.connect("clicked", self._edit_meshtastic_interface)
        install_row.append(edit_iface_btn)

        mesh_iface_box.append(install_row)

        # Status label
        self.mesh_iface_status = Gtk.Label(label="")
        self.mesh_iface_status.set_xalign(0)
        self.mesh_iface_status.add_css_class("dim-label")
        mesh_iface_box.append(self.mesh_iface_status)

        # Check if already installed
        iface_file = get_real_user_home() / ".reticulum" / "interfaces" / "Meshtastic_Interface.py"
        if iface_file.exists():
            self.mesh_iface_status.set_label("Meshtastic_Interface.py installed")

        mesh_iface_expander.set_child(mesh_iface_box)
        box.append(mesh_iface_expander)

        frame.set_child(box)
        parent.append(frame)

        # Initialize gateway state
        self._gateway_bridge = None
        self._update_gateway_status()

    def _update_gateway_status(self):
        """Update gateway status display"""
        if self._gateway_bridge and self._gateway_bridge.is_running:
            status = self._gateway_bridge.get_status()
            self.gateway_status_icon.set_from_icon_name("network-transmit-receive-symbolic")
            self.gateway_status_label.set_label("Gateway: Running")

            mesh_status = "Connected" if status['meshtastic_connected'] else "Disconnected"
            rns_status = "Connected" if status['rns_connected'] else "Disconnected"
            self.gateway_detail_label.set_label(f"Mesh: {mesh_status} | RNS: {rns_status}")

            self.gateway_start_btn.set_sensitive(False)
            self.gateway_stop_btn.set_sensitive(True)

            # Update connection indicators
            if status['meshtastic_connected']:
                self.mesh_conn_icon.set_from_icon_name("emblem-default-symbolic")
                self.mesh_conn_label.set_label("Meshtastic: Connected")
            else:
                self.mesh_conn_icon.set_from_icon_name("dialog-warning-symbolic")
                self.mesh_conn_label.set_label("Meshtastic: Disconnected")

            if status['rns_connected']:
                self.rns_conn_icon.set_from_icon_name("emblem-default-symbolic")
                self.rns_conn_label.set_label("RNS: Connected")
            else:
                self.rns_conn_icon.set_from_icon_name("dialog-warning-symbolic")
                self.rns_conn_label.set_label("RNS: Disconnected")

            # Update statistics
            stats = status.get('statistics', {})
            node_stats = status.get('node_stats', {})
            self.stats_labels["Messages Mesh→RNS"].set_label(str(stats.get('messages_mesh_to_rns', 0)))
            self.stats_labels["Messages RNS→Mesh"].set_label(str(stats.get('messages_rns_to_mesh', 0)))
            self.stats_labels["Total Nodes"].set_label(str(node_stats.get('total', 0)))
            self.stats_labels["Errors"].set_label(str(stats.get('errors', 0)))

        else:
            self.gateway_status_icon.set_from_icon_name("network-offline-symbolic")
            self.gateway_status_label.set_label("Gateway: Stopped")
            self.gateway_detail_label.set_label("Not running")
            self.gateway_start_btn.set_sensitive(True)
            self.gateway_stop_btn.set_sensitive(False)

            self.mesh_conn_icon.set_from_icon_name("dialog-question-symbolic")
            self.mesh_conn_label.set_label("Meshtastic: Unknown")
            self.rns_conn_icon.set_from_icon_name("dialog-question-symbolic")
            self.rns_conn_label.set_label("RNS: Unknown")

    def _on_gateway_start(self, button):
        """Start the gateway bridge"""
        logger.debug("[RNS] Starting gateway...")
        self.main_window.set_status_message("Checking service prerequisites...")

        def do_start():
            try:
                # Pre-flight service checks
                service_issues = []

                if check_service:
                    # Check meshtasticd service
                    meshtastic_status = check_service('meshtasticd')
                    if not meshtastic_status.available:
                        service_issues.append(f"meshtasticd: {meshtastic_status.message}")
                        if meshtastic_status.fix_hint:
                            service_issues.append(f"  Fix: {meshtastic_status.fix_hint}")

                    # Check rnsd service
                    rnsd_status = check_service('rnsd')
                    if not rnsd_status.available:
                        service_issues.append(f"rnsd: {rnsd_status.message}")
                        if rnsd_status.fix_hint:
                            service_issues.append(f"  Fix: {rnsd_status.fix_hint}")

                if service_issues:
                    logger.warning(f"[RNS] Gateway pre-checks failed: {service_issues}")
                    GLib.idle_add(
                        self._show_service_warning,
                        "Service Prerequisites Not Met",
                        "\n".join(service_issues)
                    )
                    GLib.idle_add(self._gateway_start_complete, False, "Required services not running")
                    return

                # All checks passed, proceed with gateway start
                GLib.idle_add(
                    lambda: self.main_window.set_status_message("Starting gateway...")
                )

                from gateway.rns_bridge import RNSMeshtasticBridge
                from gateway.config import GatewayConfig

                config = GatewayConfig.load()
                config.enabled = True
                config.save()

                self._gateway_bridge = RNSMeshtasticBridge(config)
                success = self._gateway_bridge.start()
                logger.debug(f"[RNS] Gateway start: {'OK' if success else 'FAILED'}")

                GLib.idle_add(self._gateway_start_complete, success)
            except ImportError as e:
                logger.debug(f"[RNS] Gateway start failed - missing module: {e}")
                GLib.idle_add(self._gateway_start_complete, False, f"Missing module: {e}")
            except Exception as e:
                logger.debug(f"[RNS] Gateway start exception: {e}")
                GLib.idle_add(self._gateway_start_complete, False, str(e))

        thread = threading.Thread(target=do_start)
        thread.daemon = True
        thread.start()

    def _show_service_warning(self, title, message):
        """Show a warning dialog about service issues"""
        try:
            dialog = Adw.MessageDialog(
                transient_for=self.main_window,
                heading=title,
                body=message
            )
            dialog.add_response("ok", "OK")
            dialog.set_default_response("ok")
            dialog.present()
        except Exception as e:
            logger.error(f"Failed to show service warning dialog: {e}")
            self.main_window.set_status_message(f"Warning: {message}")

    def _gateway_start_complete(self, success, error=None):
        """Handle gateway start completion"""
        if success:
            self.main_window.set_status_message("Gateway started successfully")
            self.gateway_enable_switch.set_active(True)
        else:
            self.main_window.set_status_message(f"Failed to start gateway: {error}")

        self._update_gateway_status()
        return False

    def _on_gateway_stop(self, button):
        """Stop the gateway bridge"""
        logger.debug("[RNS] Stopping gateway...")
        self.main_window.set_status_message("Stopping gateway...")

        if self._gateway_bridge:
            self._gateway_bridge.stop()
            self._gateway_bridge = None
            logger.debug("[RNS] Gateway stopped")
        else:
            logger.debug("[RNS] No gateway running")

        self.main_window.set_status_message("Gateway stopped")
        self._update_gateway_status()

    def _on_gateway_enable_changed(self, switch, state):
        """Handle gateway enable switch toggle"""
        try:
            from gateway.config import GatewayConfig
            config = GatewayConfig.load()
            config.enabled = state
            config.save()
        except ImportError:
            pass
        return False

    def _on_test_gateway(self, button):
        """Test gateway connections"""
        logger.debug("[RNS] Testing gateway connections...")
        self.main_window.set_status_message("Testing connections...")

        def do_test():
            results = {
                'meshtastic': {'connected': False, 'error': None},
                'rns': {'connected': False, 'error': None},
            }

            # Test Meshtastic
            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                result = sock.connect_ex(('localhost', 4403))
                sock.close()
                results['meshtastic']['connected'] = (result == 0)
                if result != 0:
                    results['meshtastic']['error'] = "Cannot connect to port 4403"
            except Exception as e:
                results['meshtastic']['error'] = str(e)

            # Test RNS
            try:
                result = subprocess.run(
                    ['python3', '-c', 'import RNS; print("OK")'],
                    capture_output=True, text=True, timeout=5
                )
                results['rns']['connected'] = (result.returncode == 0)
                if result.returncode != 0:
                    results['rns']['error'] = "RNS not installed"
            except Exception as e:
                results['rns']['error'] = str(e)

            GLib.idle_add(self._test_complete, results)

        thread = threading.Thread(target=do_test)
        thread.daemon = True
        thread.start()

    def _test_complete(self, results):
        """Handle test completion"""
        mesh_ok = results['meshtastic']['connected']
        rns_ok = results['rns']['connected']
        logger.debug(f"[RNS] Test results - Meshtastic: {'OK' if mesh_ok else 'FAIL'}, RNS: {'OK' if rns_ok else 'FAIL'}")

        # Update icons
        if mesh_ok:
            self.mesh_conn_icon.set_from_icon_name("emblem-default-symbolic")
            self.mesh_conn_label.set_label("Meshtastic: OK")
        else:
            self.mesh_conn_icon.set_from_icon_name("dialog-error-symbolic")
            self.mesh_conn_label.set_label(f"Meshtastic: {results['meshtastic']['error'] or 'Failed'}")

        if rns_ok:
            self.rns_conn_icon.set_from_icon_name("emblem-default-symbolic")
            self.rns_conn_label.set_label("RNS: OK")
        else:
            self.rns_conn_icon.set_from_icon_name("dialog-error-symbolic")
            self.rns_conn_label.set_label(f"RNS: {results['rns']['error'] or 'Failed'}")

        status = "Meshtastic: " + ("OK" if mesh_ok else "FAIL")
        status += " | RNS: " + ("OK" if rns_ok else "FAIL")
        self.main_window.set_status_message(f"Test complete - {status}")

        return False

    def _on_configure_gateway(self, button):
        """Open gateway configuration dialog"""
        try:
            from ..dialogs.gateway_config import GatewayConfigDialog
            dialog = GatewayConfigDialog(self.main_window)
            dialog.present()
        except ImportError as e:
            # Fallback if dialog not available
            dialog = Adw.MessageDialog(
                transient_for=self.main_window,
                heading="Gateway Configuration",
                body=f"Config editor not available: {e}\n\n"
                     "Config file: ~/.config/meshforge/gateway.json"
            )
            dialog.add_response("ok", "OK")
            dialog.present()

    def _on_edit_gateway_terminal(self, button):
        """Edit gateway config in terminal with nano"""
        real_home = self._get_real_user_home()
        config_file = real_home / ".config" / "meshforge" / "gateway.json"

        logger.debug(f"[RNS] Opening gateway config in terminal: {config_file}")

        # Create default config if it doesn't exist
        if not config_file.exists():
            try:
                config_file.parent.mkdir(parents=True, exist_ok=True)
                default_config = {
                    "enabled": False,
                    "meshtastic": {
                        "host": "localhost",
                        "port": 4403
                    },
                    "rns": {
                        "config_dir": "",
                        "announce_interval": 300
                    },
                    "telemetry": {
                        "enabled": True,
                        "interval": 60
                    },
                    "routing": {
                        "rules": []
                    }
                }
                config_file.write_text(json.dumps(default_config, indent=2))

                # Fix ownership if running as root
                real_user = self._get_real_username()
                is_root = os.geteuid() == 0
                if is_root and real_user != 'root':
                    subprocess.run(['chown', '-R', f'{real_user}:{real_user}', str(config_file.parent)],
                                   capture_output=True, timeout=10)

                logger.debug(f"[RNS] Created default gateway config: {config_file}")
            except Exception as e:
                logger.debug(f"[RNS] Failed to create gateway config: {e}")

        # Open in terminal editor
        self._edit_config_terminal(config_file)

    def _on_view_nodes(self, button):
        """Show tracked nodes from both networks"""
        if self._gateway_bridge:
            nodes = self._gateway_bridge.node_tracker.get_all_nodes()
            stats = self._gateway_bridge.node_tracker.get_stats()

            body = f"Total Nodes: {stats['total']}\n"
            body += f"Meshtastic: {stats['meshtastic']}\n"
            body += f"RNS: {stats['rns']}\n"
            body += f"Online: {stats['online']}\n"
            body += f"With Position: {stats['with_position']}\n\n"

            if nodes:
                body += "Recent Nodes:\n"
                for node in sorted(nodes, key=lambda n: n.last_seen or datetime.min, reverse=True)[:10]:
                    body += f"  - {node.name} ({node.network}) - {node.get_age_string()}\n"
        else:
            body = "Gateway not running. Start the gateway to track nodes."

        dialog = Adw.MessageDialog(
            transient_for=self.main_window,
            heading="Tracked Nodes",
            body=body
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def _on_run_diagnostic(self, button):
        """Run the gateway diagnostic wizard"""
        logger.debug("[RNS] Running gateway diagnostic...")
        self.main_window.set_status_message("Running gateway diagnostic...")

        def do_diagnostic():
            try:
                from utils.gateway_diagnostic import GatewayDiagnostic

                diag = GatewayDiagnostic()
                wizard_output = diag.run_wizard()

                # Also get structured results for UI
                failures = [r for r in diag.results if r.status.value == "FAIL"]
                warnings = [r for r in diag.results if r.status.value == "WARN"]
                passes = [r for r in diag.results if r.status.value == "PASS"]

                GLib.idle_add(self._show_diagnostic_results,
                             wizard_output, len(failures), len(warnings), len(passes))

            except ImportError as e:
                logger.debug(f"[RNS] Diagnostic import error: {e}")
                GLib.idle_add(lambda: self.main_window.set_status_message(f"Diagnostic error: {e}"))
            except Exception as e:
                logger.debug(f"[RNS] Diagnostic error: {e}")
                GLib.idle_add(lambda: self.main_window.set_status_message(f"Error: {e}"))

        threading.Thread(target=do_diagnostic, daemon=True).start()

    def _show_diagnostic_results(self, wizard_output, fail_count, warn_count, pass_count):
        """Show diagnostic results in a dialog"""
        if fail_count == 0:
            heading = "Gateway Ready"
            status_msg = "All checks passed"
        else:
            heading = f"{fail_count} Issue(s) Found"
            status_msg = f"{fail_count} failures, {warn_count} warnings"

        self.main_window.set_status_message(status_msg)

        # Create scrollable dialog for results
        dialog = Adw.MessageDialog(
            transient_for=self.main_window,
            heading=heading,
            body=""  # We'll use a custom widget instead
        )

        # Create scrollable text view for output
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(400)
        scroll.set_min_content_width(500)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_monospace(True)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        text_view.set_margin_start(10)
        text_view.set_margin_end(10)
        text_view.set_margin_top(10)
        text_view.set_margin_bottom(10)

        buffer = text_view.get_buffer()
        buffer.set_text(wizard_output)

        scroll.set_child(text_view)
        dialog.set_extra_child(scroll)

        dialog.add_response("ok", "OK")
        dialog.present()

        return False
