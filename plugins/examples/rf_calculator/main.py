"""
RF Calculator Plugin for MeshForge.io

Provides essential RF calculations:
- Free Space Path Loss (FSPL)
- Effective Isotropic Radiated Power (EIRP)
- Receiver Sensitivity
- Link Budget
- Distance from RSSI
"""

import math
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

# Import plugin base - handle both installed and development scenarios
try:
    from meshforge.core.plugin_base import Plugin, PluginContext
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))
    from core.plugin_base import Plugin, PluginContext


class RFCalculatorPanel(Gtk.Box):
    """RF Calculator panel widget"""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(10)
        self.set_margin_bottom(10)

        self._build_ui()

    def _build_ui(self):
        """Build the calculator UI"""
        # Title
        title = Gtk.Label(label="RF Calculator")
        title.add_css_class("title-2")
        title.set_xalign(0)
        self.append(title)

        subtitle = Gtk.Label(label="Essential calculations for link planning")
        subtitle.add_css_class("dim-label")
        subtitle.set_xalign(0)
        self.append(subtitle)

        # Scrollable content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        content.set_margin_top(15)

        # FSPL Calculator
        self._build_fspl_section(content)

        # EIRP Calculator
        self._build_eirp_section(content)

        # Link Budget
        self._build_link_budget_section(content)

        scrolled.set_child(content)
        self.append(scrolled)

    def _build_fspl_section(self, parent):
        """Build Free Space Path Loss calculator"""
        frame = Gtk.Frame()
        frame.set_label("Free Space Path Loss (FSPL)")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Frequency input
        freq_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        freq_label = Gtk.Label(label="Frequency (MHz):")
        freq_label.set_width_chars(15)
        freq_label.set_xalign(0)
        freq_box.append(freq_label)

        self.fspl_freq = Gtk.SpinButton.new_with_range(100, 6000, 1)
        self.fspl_freq.set_value(915)
        self.fspl_freq.connect("value-changed", self._on_fspl_changed)
        freq_box.append(self.fspl_freq)
        box.append(freq_box)

        # Distance input
        dist_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        dist_label = Gtk.Label(label="Distance (km):")
        dist_label.set_width_chars(15)
        dist_label.set_xalign(0)
        dist_box.append(dist_label)

        self.fspl_dist = Gtk.SpinButton.new_with_range(0.1, 100, 0.1)
        self.fspl_dist.set_value(1)
        self.fspl_dist.set_digits(1)
        self.fspl_dist.connect("value-changed", self._on_fspl_changed)
        dist_box.append(self.fspl_dist)
        box.append(dist_box)

        # Result
        result_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        result_label = Gtk.Label(label="Path Loss:")
        result_label.set_width_chars(15)
        result_label.set_xalign(0)
        result_box.append(result_label)

        self.fspl_result = Gtk.Label(label="-- dB")
        self.fspl_result.add_css_class("heading")
        self.fspl_result.set_xalign(0)
        result_box.append(self.fspl_result)
        box.append(result_box)

        # Formula reference
        formula = Gtk.Label(label="FSPL = 20log(d) + 20log(f) + 32.45")
        formula.add_css_class("dim-label")
        formula.add_css_class("caption")
        box.append(formula)

        frame.set_child(box)
        parent.append(frame)

        # Calculate initial
        self._on_fspl_changed(None)

    def _build_eirp_section(self, parent):
        """Build EIRP calculator"""
        frame = Gtk.Frame()
        frame.set_label("Effective Isotropic Radiated Power (EIRP)")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # TX Power input
        tx_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        tx_label = Gtk.Label(label="TX Power (dBm):")
        tx_label.set_width_chars(15)
        tx_label.set_xalign(0)
        tx_box.append(tx_label)

        self.eirp_tx = Gtk.SpinButton.new_with_range(-10, 30, 1)
        self.eirp_tx.set_value(20)
        self.eirp_tx.connect("value-changed", self._on_eirp_changed)
        tx_box.append(self.eirp_tx)
        box.append(tx_box)

        # Cable loss
        cable_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cable_label = Gtk.Label(label="Cable Loss (dB):")
        cable_label.set_width_chars(15)
        cable_label.set_xalign(0)
        cable_box.append(cable_label)

        self.eirp_cable = Gtk.SpinButton.new_with_range(0, 20, 0.5)
        self.eirp_cable.set_value(1)
        self.eirp_cable.set_digits(1)
        self.eirp_cable.connect("value-changed", self._on_eirp_changed)
        cable_box.append(self.eirp_cable)
        box.append(cable_box)

        # Antenna gain
        ant_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        ant_label = Gtk.Label(label="Antenna Gain (dBi):")
        ant_label.set_width_chars(15)
        ant_label.set_xalign(0)
        ant_box.append(ant_label)

        self.eirp_ant = Gtk.SpinButton.new_with_range(0, 30, 0.5)
        self.eirp_ant.set_value(3)
        self.eirp_ant.set_digits(1)
        self.eirp_ant.connect("value-changed", self._on_eirp_changed)
        ant_box.append(self.eirp_ant)
        box.append(ant_box)

        # Result
        result_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        result_label = Gtk.Label(label="EIRP:")
        result_label.set_width_chars(15)
        result_label.set_xalign(0)
        result_box.append(result_label)

        self.eirp_result = Gtk.Label(label="-- dBm (-- mW)")
        self.eirp_result.add_css_class("heading")
        self.eirp_result.set_xalign(0)
        result_box.append(self.eirp_result)
        box.append(result_box)

        # Formula
        formula = Gtk.Label(label="EIRP = TX Power - Cable Loss + Antenna Gain")
        formula.add_css_class("dim-label")
        formula.add_css_class("caption")
        box.append(formula)

        frame.set_child(box)
        parent.append(frame)

        # Calculate initial
        self._on_eirp_changed(None)

    def _build_link_budget_section(self, parent):
        """Build link budget calculator"""
        frame = Gtk.Frame()
        frame.set_label("Link Budget")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # EIRP input
        eirp_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        eirp_label = Gtk.Label(label="EIRP (dBm):")
        eirp_label.set_width_chars(18)
        eirp_label.set_xalign(0)
        eirp_box.append(eirp_label)

        self.link_eirp = Gtk.SpinButton.new_with_range(-10, 50, 1)
        self.link_eirp.set_value(22)
        self.link_eirp.connect("value-changed", self._on_link_changed)
        eirp_box.append(self.link_eirp)
        box.append(eirp_box)

        # Path loss
        fspl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        fspl_label = Gtk.Label(label="Path Loss (dB):")
        fspl_label.set_width_chars(18)
        fspl_label.set_xalign(0)
        fspl_box.append(fspl_label)

        self.link_fspl = Gtk.SpinButton.new_with_range(0, 200, 1)
        self.link_fspl.set_value(100)
        self.link_fspl.connect("value-changed", self._on_link_changed)
        fspl_box.append(self.link_fspl)
        box.append(fspl_box)

        # RX antenna gain
        rx_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        rx_label = Gtk.Label(label="RX Antenna Gain (dBi):")
        rx_label.set_width_chars(18)
        rx_label.set_xalign(0)
        rx_box.append(rx_label)

        self.link_rx_ant = Gtk.SpinButton.new_with_range(0, 30, 0.5)
        self.link_rx_ant.set_value(3)
        self.link_rx_ant.set_digits(1)
        self.link_rx_ant.connect("value-changed", self._on_link_changed)
        rx_box.append(self.link_rx_ant)
        box.append(rx_box)

        # Receiver sensitivity
        sens_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        sens_label = Gtk.Label(label="RX Sensitivity (dBm):")
        sens_label.set_width_chars(18)
        sens_label.set_xalign(0)
        sens_box.append(sens_label)

        self.link_sens = Gtk.SpinButton.new_with_range(-150, -50, 1)
        self.link_sens.set_value(-130)
        self.link_sens.connect("value-changed", self._on_link_changed)
        sens_box.append(self.link_sens)
        box.append(sens_box)

        # Results
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_top(5)
        sep.set_margin_bottom(5)
        box.append(sep)

        # Expected RSSI
        rssi_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        rssi_label = Gtk.Label(label="Expected RSSI:")
        rssi_label.set_width_chars(18)
        rssi_label.set_xalign(0)
        rssi_box.append(rssi_label)

        self.link_rssi = Gtk.Label(label="-- dBm")
        self.link_rssi.add_css_class("heading")
        self.link_rssi.set_xalign(0)
        rssi_box.append(self.link_rssi)
        box.append(rssi_box)

        # Link margin
        margin_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        margin_label = Gtk.Label(label="Link Margin:")
        margin_label.set_width_chars(18)
        margin_label.set_xalign(0)
        margin_box.append(margin_label)

        self.link_margin = Gtk.Label(label="-- dB")
        self.link_margin.add_css_class("heading")
        self.link_margin.set_xalign(0)
        margin_box.append(self.link_margin)
        box.append(margin_box)

        # Status
        self.link_status = Gtk.Label(label="")
        self.link_status.set_margin_top(5)
        box.append(self.link_status)

        frame.set_child(box)
        parent.append(frame)

        # Calculate initial
        self._on_link_changed(None)

    def _on_fspl_changed(self, widget):
        """Calculate FSPL"""
        freq = self.fspl_freq.get_value()
        dist = self.fspl_dist.get_value()

        # FSPL = 20*log10(d) + 20*log10(f) + 32.45
        # where d is in km and f is in MHz
        if dist > 0 and freq > 0:
            fspl = 20 * math.log10(dist) + 20 * math.log10(freq) + 32.45
            self.fspl_result.set_label(f"{fspl:.1f} dB")
        else:
            self.fspl_result.set_label("-- dB")

    def _on_eirp_changed(self, widget):
        """Calculate EIRP"""
        tx = self.eirp_tx.get_value()
        cable = self.eirp_cable.get_value()
        ant = self.eirp_ant.get_value()

        eirp_dbm = tx - cable + ant
        eirp_mw = 10 ** (eirp_dbm / 10)

        if eirp_mw >= 1000:
            power_str = f"{eirp_mw/1000:.2f} W"
        else:
            power_str = f"{eirp_mw:.1f} mW"

        self.eirp_result.set_label(f"{eirp_dbm:.1f} dBm ({power_str})")

    def _on_link_changed(self, widget):
        """Calculate link budget"""
        eirp = self.link_eirp.get_value()
        fspl = self.link_fspl.get_value()
        rx_ant = self.link_rx_ant.get_value()
        sensitivity = self.link_sens.get_value()

        # Expected RSSI
        rssi = eirp - fspl + rx_ant
        self.link_rssi.set_label(f"{rssi:.1f} dBm")

        # Link margin
        margin = rssi - sensitivity
        self.link_margin.set_label(f"{margin:.1f} dB")

        # Status indicator
        self.link_status.remove_css_class("success")
        self.link_status.remove_css_class("warning")
        self.link_status.remove_css_class("error")

        if margin >= 20:
            self.link_status.set_label("Excellent link - plenty of margin")
            self.link_status.add_css_class("success")
        elif margin >= 10:
            self.link_status.set_label("Good link - adequate margin")
            self.link_status.add_css_class("success")
        elif margin >= 0:
            self.link_status.set_label("Marginal link - may be unreliable")
            self.link_status.add_css_class("warning")
        else:
            self.link_status.set_label("No link - signal below sensitivity")
            self.link_status.add_css_class("error")


class RFCalculatorPlugin(Plugin):
    """RF Calculator plugin for MeshForge.io"""

    def activate(self, context: PluginContext) -> None:
        """Called when plugin is activated"""
        # Register the calculator panel
        context.register_panel(
            panel_id="rf_calculator_panel",
            panel_class=RFCalculatorPanel,
            title="RF Calculator",
            icon="accessories-calculator-symbolic"
        )

        # Also register as a tool
        context.register_tool(
            tool_id="fspl_calc",
            tool_func=self._calculate_fspl,
            name="FSPL Calculator",
            description="Calculate Free Space Path Loss"
        )

    def deactivate(self) -> None:
        """Called when plugin is deactivated"""
        pass

    def _calculate_fspl(self, freq_mhz: float, dist_km: float) -> float:
        """Calculate Free Space Path Loss"""
        if freq_mhz <= 0 or dist_km <= 0:
            return 0
        return 20 * math.log10(dist_km) + 20 * math.log10(freq_mhz) + 32.45
