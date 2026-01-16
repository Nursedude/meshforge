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
from utils.paths import get_real_user_home

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
            label="Configure how RNS connects to your Meshtastic device.\n"
                  "TCP recommended when using meshtasticd daemon."
        )
        mesh_desc.set_xalign(0)
        mesh_desc.add_css_class("dim-label")
        mesh_desc.set_wrap(True)
        mesh_iface_box.append(mesh_desc)

        # Connection type selection
        conn_type_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        conn_type_label = Gtk.Label(label="Connection:")
        conn_type_label.set_xalign(0)
        conn_type_label.set_size_request(100, -1)
        conn_type_row.append(conn_type_label)

        self.mesh_conn_type = Gtk.DropDown.new_from_strings([
            "TCP (meshtasticd)",
            "Serial Port",
            "Bluetooth LE"
        ])
        self.mesh_conn_type.set_tooltip_text("How to connect to Meshtastic device")
        self.mesh_conn_type.connect("notify::selected", self._on_mesh_conn_type_changed)
        conn_type_row.append(self.mesh_conn_type)

        # Auto-detect button
        detect_btn = Gtk.Button(label="Detect")
        detect_btn.set_tooltip_text("Auto-detect meshtasticd or serial devices")
        detect_btn.connect("clicked", self._on_detect_meshtastic_connection)
        conn_type_row.append(detect_btn)

        mesh_iface_box.append(conn_type_row)

        # Connection details input (changes based on type)
        self.mesh_conn_details_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        details_label = Gtk.Label(label="Address:")
        details_label.set_xalign(0)
        details_label.set_size_request(100, -1)
        self.mesh_conn_details_box.append(details_label)

        self.mesh_conn_entry = Gtk.Entry()
        self.mesh_conn_entry.set_text("127.0.0.1:4403")
        self.mesh_conn_entry.set_hexpand(True)
        self.mesh_conn_entry.set_tooltip_text("TCP: host:port | Serial: /dev/ttyUSB0 | BLE: device_name")
        self.mesh_conn_details_box.append(self.mesh_conn_entry)

        mesh_iface_box.append(self.mesh_conn_details_box)

        # Radio speed preset
        speed_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        speed_label = Gtk.Label(label="Speed:")
        speed_label.set_xalign(0)
        speed_label.set_size_request(100, -1)
        speed_row.append(speed_label)

        self.mesh_speed_dropdown = Gtk.DropDown.new_from_strings([
            "8 - SHORT_TURBO (21875 bps, ~3km)",
            "6 - SHORT_FAST (10937 bps, ~5km)",
            "4 - MEDIUM_FAST (3516 bps, ~12km)",
            "0 - LONG_FAST (1066 bps, ~30km)",
            "7 - LONG_MODERATE (878 bps, ~40km)",
            "1 - LONG_SLOW (293 bps, ~80km)",
        ])
        self.mesh_speed_dropdown.set_selected(0)  # Default to TURBO
        speed_row.append(self.mesh_speed_dropdown)
        mesh_iface_box.append(speed_row)

        # Action buttons row
        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        apply_config_btn = Gtk.Button(label="Apply Config")
        apply_config_btn.set_tooltip_text("Write Meshtastic Interface config to ~/.reticulum/config")
        apply_config_btn.add_css_class("suggested-action")
        apply_config_btn.connect("clicked", self._on_apply_meshtastic_config)
        action_row.append(apply_config_btn)

        install_iface_btn = Gtk.Button(label="Install Interface")
        install_iface_btn.set_tooltip_text("Download Meshtastic_Interface.py to ~/.reticulum/interfaces/")
        install_iface_btn.connect("clicked", self._install_meshtastic_interface)
        action_row.append(install_iface_btn)

        edit_manual_btn = Gtk.Button(label="Edit Manually")
        edit_manual_btn.set_tooltip_text("Edit RNS config in terminal with nano")
        edit_manual_btn.connect("clicked", lambda b: self._edit_config_terminal(
            get_real_user_home() / ".reticulum" / "config"
        ))
        action_row.append(edit_manual_btn)

        mesh_iface_box.append(action_row)

        # Status label
        self.mesh_iface_status = Gtk.Label(label="")
        self.mesh_iface_status.set_xalign(0)
        self.mesh_iface_status.add_css_class("dim-label")
        mesh_iface_box.append(self.mesh_iface_status)

        mesh_iface_expander.set_child(mesh_iface_box)
        box.append(mesh_iface_expander)

        # Auto-detect on startup
        GLib.idle_add(self._on_detect_meshtastic_connection, None)

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

                    # Check if RNS module is available (gateway will initialize it)
                    # Don't require external rnsd - MeshForge can be the RNS instance
                    rns_available = False
                    try:
                        import RNS
                        rns_available = True
                        logger.debug("[RNS] RNS module available - gateway will initialize")
                    except ImportError:
                        service_issues.append("RNS module not installed")
                        service_issues.append("  Fix: pip install rns")

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

                # Register bridge with commands module so messaging can use it
                if success:
                    try:
                        from commands import gateway as gateway_cmd
                        gateway_cmd.set_bridge(self._gateway_bridge)
                    except Exception as e:
                        logger.warning(f"[RNS] Could not register bridge: {e}")

                GLib.idle_add(self._gateway_start_complete, success)
            except ImportError as e:
                logger.debug(f"[RNS] Gateway start failed - missing module: {e}")
                GLib.idle_add(self._gateway_start_complete, False, f"Missing module: {e}")
            except (SystemExit, KeyboardInterrupt, GeneratorExit):
                raise
            except BaseException as e:
                # Catch pyo3 PanicException and other crashes
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

            # Test Meshtastic - check persistent connection first to avoid conflicts
            try:
                from utils.meshtastic_connection import get_connection_manager
                mgr = get_connection_manager()
                if mgr.has_persistent():
                    # Bridge has a persistent connection - don't create a new one!
                    results['meshtastic']['connected'] = True
                    logger.debug("[RNS] Meshtastic test: using existing persistent connection")
                else:
                    # No persistent connection, safe to do socket check
                    import socket
                    sock = None
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(5)
                        result = sock.connect_ex(('localhost', 4403))
                        results['meshtastic']['connected'] = (result == 0)
                        if result != 0:
                            results['meshtastic']['error'] = "Cannot connect to port 4403"
                    except Exception as e:
                        results['meshtastic']['error'] = str(e)
                    finally:
                        if sock:
                            try:
                                sock.close()
                            except Exception:
                                pass
            except ImportError:
                # Fallback to socket check if connection manager not available
                import socket
                sock = None
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(5)
                    result = sock.connect_ex(('localhost', 4403))
                    results['meshtastic']['connected'] = (result == 0)
                    if result != 0:
                        results['meshtastic']['error'] = "Cannot connect to port 4403"
                except Exception as e:
                    results['meshtastic']['error'] = str(e)
                finally:
                    if sock:
                        try:
                            sock.close()
                        except Exception:
                            pass

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

    def _on_mesh_conn_type_changed(self, dropdown, param):
        """Handle connection type dropdown change"""
        selected = dropdown.get_selected()
        if selected == 0:  # TCP
            self.mesh_conn_entry.set_text("127.0.0.1:4403")
            self.mesh_conn_entry.set_placeholder_text("host:port (e.g., 127.0.0.1:4403)")
        elif selected == 1:  # Serial
            self.mesh_conn_entry.set_text("/dev/ttyUSB0")
            self.mesh_conn_entry.set_placeholder_text("/dev/ttyUSB0 or /dev/ttyACM0")
        elif selected == 2:  # BLE
            self.mesh_conn_entry.set_text("")
            self.mesh_conn_entry.set_placeholder_text("Bluetooth device name")

    def _on_detect_meshtastic_connection(self, button):
        """Auto-detect meshtasticd, Meshtastic devices, and RNodes"""
        GLib.idle_add(self._set_mesh_status, "Detecting devices...")

        def do_detect():
            detected_type = None
            detected_value = None
            status_lines = []

            # Check for meshtasticd on TCP port 4403
            import socket
            meshtasticd_running = False
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex(('localhost', 4403))
                sock.close()
                if result == 0:
                    meshtasticd_running = True
                    detected_type = 0  # TCP
                    detected_value = "127.0.0.1:4403"
                    status_lines.append("Meshtastic: TCP (meshtasticd :4403)")
            except Exception:
                pass

            # Scan serial devices and identify them
            serial_devices = []
            for pattern in ['ttyUSB*', 'ttyACM*']:
                serial_devices.extend(Path('/dev').glob(pattern))

            for device in serial_devices:
                device_str = str(device)
                device_type = self._identify_serial_device(device_str)

                if device_type == "rnode":
                    status_lines.append(f"RNode: {device_str}")
                elif device_type == "meshtastic":
                    status_lines.append(f"Meshtastic: {device_str}")
                    # Only use serial if no TCP detected
                    if detected_type is None:
                        detected_type = 1
                        detected_value = device_str
                else:
                    status_lines.append(f"Serial: {device_str} (unknown)")
                    if detected_type is None:
                        detected_type = 1
                        detected_value = device_str

            if status_lines:
                status_msg = "\n".join(status_lines)
            else:
                status_msg = "No Meshtastic or RNode devices detected"

            if detected_type is not None:
                GLib.idle_add(self._apply_detected_connection, detected_type, detected_value, status_msg)
            else:
                GLib.idle_add(self._set_mesh_status, status_msg)

        threading.Thread(target=do_detect, daemon=True).start()

    def _identify_serial_device(self, port: str) -> str:
        """Identify if a serial device is Meshtastic or RNode.

        Returns: 'meshtastic', 'rnode', or 'unknown'
        """
        # First check USB vendor/product IDs
        try:
            # Extract device name (e.g., ttyUSB0)
            import os
            device_name = os.path.basename(port)

            # Check sysfs for USB info
            usb_path = None
            for p in Path('/sys/class/tty').glob(f'{device_name}/device/../../../'):
                if (p / 'idVendor').exists():
                    usb_path = p
                    break

            if usb_path:
                vendor = (usb_path / 'idVendor').read_text().strip()
                product = (usb_path / 'idProduct').read_text().strip()

                # Known RNode devices (Heltec, LilyGo T-Beam with RNode firmware)
                # CH341 (1a86:5512 or 1a86:7523) - often RNode
                # CP210x (10c4:ea60) - could be either
                if vendor == '1a86':  # QinHeng/CH341
                    return 'rnode'  # CH341 is commonly used for RNode
                elif vendor == '10c4' and product == 'ea60':  # Silicon Labs CP210x
                    # Could be either - try to probe
                    pass
        except Exception:
            pass

        # Try quick probe with meshtastic CLI (only if not locked)
        try:
            result = subprocess.run(
                ['meshtastic', '--port', port, '--info'],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0 and 'Owner' in result.stdout:
                return 'meshtastic'
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            pass

        # Try rnodeconf probe
        try:
            result = subprocess.run(
                ['rnodeconf', port, '-i'],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0 and ('RNode' in result.stdout or 'firmware' in result.stdout.lower()):
                return 'rnode'
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            pass

        return 'unknown'

    def _apply_detected_connection(self, conn_type, conn_value, status_msg):
        """Apply detected connection settings to UI"""
        self.mesh_conn_type.set_selected(conn_type)
        self.mesh_conn_entry.set_text(conn_value)
        self.mesh_iface_status.set_label(status_msg)
        logger.debug(f"[RNS] Auto-detected: {status_msg}")
        return False

    def _set_mesh_status(self, msg):
        """Set mesh interface status label"""
        self.mesh_iface_status.set_label(msg)
        return False

    def _on_apply_meshtastic_config(self, button):
        """Apply Meshtastic Interface config to RNS config file"""
        conn_type = self.mesh_conn_type.get_selected()
        conn_value = self.mesh_conn_entry.get_text().strip()
        speed_idx = self.mesh_speed_dropdown.get_selected()

        # Map speed dropdown index to data_speed values
        speed_map = {0: 8, 1: 6, 2: 4, 3: 0, 4: 7, 5: 1}
        data_speed = speed_map.get(speed_idx, 8)

        # Build connection line based on type
        if conn_type == 0:  # TCP
            conn_line = f"  tcp_port = {conn_value}"
        elif conn_type == 1:  # Serial
            conn_line = f"  port = {conn_value}"
        elif conn_type == 2:  # BLE
            conn_line = f"  ble_port = {conn_value}"
        else:
            conn_line = f"  tcp_port = 127.0.0.1:4403"

        config_section = f'''
# ===== MESHTASTIC INTERFACE =====
# RNS over Meshtastic - configured by MeshForge
# Source: https://github.com/Nursedude/RNS_Over_Meshtastic_Gateway

[[Meshtastic Interface]]
  type = Meshtastic_Interface
  enabled = true
  mode = gateway
{conn_line}
  data_speed = {data_speed}
  hop_limit = 3
  bitrate = 500
'''

        def do_apply():
            try:
                config_file = get_real_user_home() / ".reticulum" / "config"

                if not config_file.exists():
                    GLib.idle_add(self._set_mesh_status, "RNS config not found - run rnsd first")
                    return

                content = config_file.read_text()

                # Check if Meshtastic Interface already exists
                if 'Meshtastic Interface' in content:
                    # Remove existing section and add new one
                    import re
                    # Pattern to match the entire Meshtastic Interface section
                    pattern = r'\n*# =+ MESHTASTIC INTERFACE =+\n.*?\[\[Meshtastic Interface\]\].*?(?=\n\n\[\[|\n*$)'
                    content = re.sub(pattern, '', content, flags=re.DOTALL)
                    # Also try simpler pattern for legacy configs
                    pattern2 = r'\[\[Meshtastic Interface\]\][^\[]*'
                    content = re.sub(pattern2, '', content)

                # Append new config
                content = content.rstrip() + '\n' + config_section

                # Create backup
                backup_path = config_file.with_suffix('.config.bak')
                config_file.rename(backup_path)

                # Write new config
                config_file.write_text(content)

                GLib.idle_add(self._set_mesh_status,
                    f"Config saved! Restart rnsd to apply. (backup: {backup_path.name})")
                GLib.idle_add(lambda: self.main_window.set_status_message("Meshtastic Interface config applied"))
                logger.info(f"[RNS] Meshtastic Interface config applied: {conn_line}")

            except Exception as e:
                logger.error(f"[RNS] Failed to apply config: {e}")
                GLib.idle_add(self._set_mesh_status, f"Error: {e}")

        self._set_mesh_status("Saving config...")
        threading.Thread(target=do_apply, daemon=True).start()
