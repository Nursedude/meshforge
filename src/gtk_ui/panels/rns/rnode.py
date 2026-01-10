"""
RNode Interface Configuration Section for RNS Panel

Configure RNode LoRa interface parameters for RNS.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib
import threading
import logging

logger = logging.getLogger(__name__)

# Import path utility
try:
    from utils.paths import get_real_user_home
except ImportError:
    import os
    from pathlib import Path
    def get_real_user_home() -> Path:
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user and sudo_user != 'root':
            return Path(f'/home/{sudo_user}')
        return Path.home()


class RNodeMixin:
    """
    Mixin class providing RNode configuration for RNSPanel.

    Expects the panel to have:
    - main_window: Reference to main application window
    - _get_real_user_home(): Method to get real user's home directory
    - _edit_config_terminal(path): Method to edit config in terminal
    """

    def _build_rnode_config_section(self, parent):
        """Build RNode LoRa interface configuration section"""
        frame = Gtk.Frame()
        frame.set_label("RNode Interface Configuration")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Description
        desc = Gtk.Label(label="Configure RNode LoRa interface for RNS")
        desc.set_xalign(0)
        desc.add_css_class("dim-label")
        box.append(desc)

        # Port selection
        port_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        port_label = Gtk.Label(label="Port:")
        port_label.set_width_chars(14)
        port_label.set_xalign(0)
        port_row.append(port_label)
        self.rnode_port = Gtk.Entry()
        self.rnode_port.set_text("/dev/ttyACM0")
        self.rnode_port.set_hexpand(True)
        port_row.append(self.rnode_port)
        box.append(port_row)

        # Frequency
        freq_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        freq_label = Gtk.Label(label="Frequency (MHz):")
        freq_label.set_width_chars(14)
        freq_label.set_xalign(0)
        freq_row.append(freq_label)
        self.rnode_freq = Gtk.SpinButton.new_with_range(137.0, 1020.0, 0.025)
        self.rnode_freq.set_digits(3)
        self.rnode_freq.set_value(903.625)
        freq_row.append(self.rnode_freq)
        box.append(freq_row)

        # Bandwidth dropdown
        bw_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bw_label = Gtk.Label(label="Bandwidth:")
        bw_label.set_width_chars(14)
        bw_label.set_xalign(0)
        bw_row.append(bw_label)
        self.rnode_bw = Gtk.DropDown.new_from_strings([
            "7.8 kHz", "10.4 kHz", "15.6 kHz", "20.8 kHz", "31.25 kHz",
            "41.7 kHz", "62.5 kHz", "125 kHz", "250 kHz", "500 kHz"
        ])
        self.rnode_bw.set_selected(8)  # 250 kHz default
        bw_row.append(self.rnode_bw)
        box.append(bw_row)

        # Spreading Factor
        sf_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        sf_label = Gtk.Label(label="Spread Factor:")
        sf_label.set_width_chars(14)
        sf_label.set_xalign(0)
        sf_row.append(sf_label)
        self.rnode_sf = Gtk.SpinButton.new_with_range(7, 12, 1)
        self.rnode_sf.set_value(7)
        sf_row.append(self.rnode_sf)
        box.append(sf_row)

        # Coding Rate
        cr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cr_label = Gtk.Label(label="Coding Rate:")
        cr_label.set_width_chars(14)
        cr_label.set_xalign(0)
        cr_row.append(cr_label)
        self.rnode_cr = Gtk.DropDown.new_from_strings(["4/5", "4/6", "4/7", "4/8"])
        self.rnode_cr.set_selected(0)  # 4/5 = codingrate 5
        cr_row.append(self.rnode_cr)
        box.append(cr_row)

        # TX Power
        tx_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        tx_label = Gtk.Label(label="TX Power (dBm):")
        tx_label.set_width_chars(14)
        tx_label.set_xalign(0)
        tx_row.append(tx_label)
        self.rnode_tx = Gtk.SpinButton.new_with_range(0, 22, 1)
        self.rnode_tx.set_value(22)
        tx_row.append(self.rnode_tx)
        box.append(tx_row)

        # Action buttons
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_row.set_halign(Gtk.Align.CENTER)
        btn_row.set_margin_top(10)

        apply_btn = Gtk.Button(label="Apply to Config")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self._apply_rnode_config)
        btn_row.append(apply_btn)

        load_btn = Gtk.Button(label="Load Current")
        load_btn.connect("clicked", self._load_rnode_config)
        btn_row.append(load_btn)

        # Edit in terminal with nano (per configurable files rule)
        config_path = get_real_user_home() / ".reticulum" / "config"
        terminal_btn = Gtk.Button(label="Edit (Terminal)")
        terminal_btn.set_tooltip_text("Edit ~/.reticulum/config in nano")
        terminal_btn.connect("clicked", lambda b: self._edit_config_terminal(config_path))
        btn_row.append(terminal_btn)

        box.append(btn_row)

        # Status
        self.rnode_status = Gtk.Label(label="")
        self.rnode_status.set_xalign(0)
        self.rnode_status.add_css_class("dim-label")
        box.append(self.rnode_status)

        frame.set_child(box)
        parent.append(frame)

        # Try to load current config
        GLib.timeout_add(1500, self._load_rnode_config)

    def _load_rnode_config(self, button=None):
        """Load RNode config from ~/.reticulum/config"""
        def do_load():
            try:
                config_path = get_real_user_home() / ".reticulum" / "config"
                if not config_path.exists():
                    GLib.idle_add(self._set_rnode_status, "Config file not found")
                    return

                content = config_path.read_text()

                # Parse RNodeInterface section
                in_rnode = False
                rnode_config = {}
                for line in content.split('\n'):
                    line = line.strip()
                    if line.startswith('[') and 'RNode' in line:
                        in_rnode = True
                        continue
                    if in_rnode and line.startswith('['):
                        break
                    if in_rnode and '=' in line and not line.startswith('#'):
                        key, val = line.split('=', 1)
                        rnode_config[key.strip().lower()] = val.strip()

                if rnode_config:
                    GLib.idle_add(self._update_rnode_ui, rnode_config)
                else:
                    GLib.idle_add(self._set_rnode_status, "No RNode interface found in config")

            except Exception as e:
                logger.error(f"Load RNode config error: {e}")
                GLib.idle_add(self._set_rnode_status, f"Error: {e}")

        threading.Thread(target=do_load, daemon=True).start()
        return False

    def _update_rnode_ui(self, config):
        """Update UI with loaded RNode config"""
        if 'port' in config:
            self.rnode_port.set_text(config['port'])
        if 'frequency' in config:
            freq_hz = int(config['frequency'])
            self.rnode_freq.set_value(freq_hz / 1000000.0)
        if 'bandwidth' in config:
            bw_hz = int(config['bandwidth'])
            bw_map = {7800: 0, 10400: 1, 15600: 2, 20800: 3, 31250: 4,
                      41700: 5, 62500: 6, 125000: 7, 250000: 8, 500000: 9}
            self.rnode_bw.set_selected(bw_map.get(bw_hz, 8))
        if 'spreadingfactor' in config:
            self.rnode_sf.set_value(int(config['spreadingfactor']))
        if 'codingrate' in config:
            cr = int(config['codingrate']) - 5  # 5->0, 6->1, 7->2, 8->3
            self.rnode_cr.set_selected(max(0, min(3, cr)))
        if 'txpower' in config:
            self.rnode_tx.set_value(int(config['txpower']))
        self._set_rnode_status("Config loaded")

    def _set_rnode_status(self, msg):
        """Set RNode status message"""
        self.rnode_status.set_label(msg)

    def _apply_rnode_config(self, button):
        """Apply RNode configuration to ~/.reticulum/config"""
        # Get values
        port = self.rnode_port.get_text().strip()
        freq_mhz = self.rnode_freq.get_value()
        freq_hz = int(freq_mhz * 1000000)
        bw_values = [7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000]
        bw_hz = bw_values[self.rnode_bw.get_selected()]
        sf = int(self.rnode_sf.get_value())
        cr = int(self.rnode_cr.get_selected()) + 5  # 0->5, 1->6, 2->7, 3->8
        tx = int(self.rnode_tx.get_value())

        def do_apply():
            try:
                # Use safe config utilities
                from ..utils.rns_config import get_rns_config_path, add_interface_to_config

                config_path = get_rns_config_path()

                # Build RNode section
                rnode_section = f"""[[RNode LoRa Interface]]
  type = RNodeInterface
  interface_enabled = True
  port = {port}
  frequency = {freq_hz}
  bandwidth = {bw_hz}
  txpower = {tx}
  spreadingfactor = {sf}
  codingrate = {cr}
"""

                # Use safe add_interface_to_config with validation and backup
                result = add_interface_to_config(config_path, rnode_section, "RNode")

                if result['success']:
                    backup_msg = f" (backup: {result['backup_path']})" if result['backup_path'] else ""
                    GLib.idle_add(self._set_rnode_status, f"Config saved! Restart rnsd to apply.{backup_msg}")
                    logger.info(f"RNode config saved: freq={freq_hz}, bw={bw_hz}, sf={sf}, cr={cr}, tx={tx}")
                else:
                    GLib.idle_add(self._set_rnode_status, f"Error: {result['error']}")
                    logger.error(f"RNode config save failed: {result['error']}")

            except ImportError:
                # Fallback to old method if utils not available
                logger.warning("rns_config utils not available, using fallback")
                self._apply_rnode_config_fallback(port, freq_hz, bw_hz, tx, sf, cr)
            except Exception as e:
                logger.error(f"Apply RNode config error: {e}")
                GLib.idle_add(self._set_rnode_status, f"Error: {e}")

        self._set_rnode_status("Saving...")
        threading.Thread(target=do_apply, daemon=True).start()

    def _apply_rnode_config_fallback(self, port, freq_hz, bw_hz, tx, sf, cr):
        """Fallback config save without validation (legacy support)"""
        import re
        try:
            config_path = get_real_user_home() / ".reticulum" / "config"

            if config_path.exists():
                content = config_path.read_text()
            else:
                content = "[reticulum]\n  share_instance = Yes\n\n[interfaces]\n"

            rnode_section = f"""[[RNode LoRa Interface]]
  type = RNodeInterface
  interface_enabled = True
  port = {port}
  frequency = {freq_hz}
  bandwidth = {bw_hz}
  txpower = {tx}
  spreadingfactor = {sf}
  codingrate = {cr}
"""
            if '[[RNode' in content or '[RNode' in content:
                pattern = r'\[\[?RNode[^\]]*\]\]?[^\[]*'
                content = re.sub(pattern, rnode_section.strip() + '\n\n', content, flags=re.IGNORECASE)
            else:
                content = content.rstrip() + '\n\n' + rnode_section

            config_path.write_text(content)
            GLib.idle_add(self._set_rnode_status, f"Config saved (legacy)! Restart rnsd.")
        except Exception as e:
            logger.error(f"Fallback config save error: {e}")
            GLib.idle_add(self._set_rnode_status, f"Error: {e}")
