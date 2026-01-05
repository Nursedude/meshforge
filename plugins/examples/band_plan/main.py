"""
Band Plan Reference Plugin for MeshForge.io

Provides quick reference for:
- LoRa ISM frequency allocations by region
- Part 97 amateur radio bands
- Meshtastic channel presets
- Power limits and regulations
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Pango

# Import plugin base
try:
    from meshforge.core.plugin_base import Plugin, PluginContext
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))
    from core.plugin_base import Plugin, PluginContext


# Band plan data
LORA_BANDS = {
    "US": {
        "name": "United States (FCC)",
        "frequency": "902-928 MHz",
        "channels": "902.0 - 928.0 MHz",
        "power": "1W (30 dBm) conducted",
        "duty_cycle": "No limit",
        "notes": "ISM band, Part 15 rules",
    },
    "EU": {
        "name": "Europe (ETSI)",
        "frequency": "863-870 MHz",
        "channels": "863.0 - 870.0 MHz",
        "power": "25 mW (14 dBm) ERP typical",
        "duty_cycle": "0.1% - 10% depending on sub-band",
        "notes": "SRD bands, EN 300 220",
    },
    "AU/NZ": {
        "name": "Australia/New Zealand",
        "frequency": "915-928 MHz",
        "channels": "915.0 - 928.0 MHz",
        "power": "1W (30 dBm) EIRP",
        "duty_cycle": "No limit",
        "notes": "ISM band",
    },
    "AS": {
        "name": "Asia (AS923)",
        "frequency": "920-923 MHz",
        "channels": "920.0 - 923.0 MHz",
        "power": "Varies by country",
        "duty_cycle": "Varies",
        "notes": "Common in Southeast Asia",
    },
    "KR": {
        "name": "South Korea",
        "frequency": "920-923 MHz",
        "channels": "920.9 - 923.3 MHz",
        "power": "10 mW (10 dBm)",
        "duty_cycle": "No limit",
        "notes": "KR920 band",
    },
    "JP": {
        "name": "Japan",
        "frequency": "920-928 MHz",
        "channels": "920.6 - 928.0 MHz",
        "power": "20 mW (13 dBm)",
        "duty_cycle": "10%",
        "notes": "AS923-1-JP",
    },
    "IN": {
        "name": "India",
        "frequency": "865-867 MHz",
        "channels": "865.0 - 867.0 MHz",
        "power": "1W (30 dBm)",
        "duty_cycle": "No limit",
        "notes": "IN865 band",
    },
}

AMATEUR_BANDS = {
    "33cm": {
        "name": "33 cm (902 MHz)",
        "frequency": "902-928 MHz",
        "modes": "All modes",
        "power": "1500W PEP",
        "license": "Technician",
        "notes": "Shared with ISM, Part 97.303",
    },
    "70cm": {
        "name": "70 cm (420 MHz)",
        "frequency": "420-450 MHz",
        "modes": "All modes",
        "power": "1500W PEP",
        "license": "Technician",
        "notes": "Popular for FM, digital",
    },
    "23cm": {
        "name": "23 cm (1.2 GHz)",
        "frequency": "1240-1300 MHz",
        "modes": "All modes",
        "power": "1500W PEP",
        "license": "Technician",
        "notes": "Microwave, ATV",
    },
    "13cm": {
        "name": "13 cm (2.4 GHz)",
        "frequency": "2390-2450 MHz",
        "modes": "All modes",
        "power": "1500W PEP",
        "license": "Technician",
        "notes": "Shared with WiFi",
    },
}

MESHTASTIC_PRESETS = [
    ("Short Fast", "SF7", "250 kHz", "~5.5 kbps", "Fastest, shortest range"),
    ("Short Slow", "SF8", "250 kHz", "~3.1 kbps", "Fast urban"),
    ("Medium Fast", "SF9", "250 kHz", "~1.8 kbps", "Balanced"),
    ("Medium Slow", "SF10", "250 kHz", "~0.98 kbps", "Default for most"),
    ("Long Fast", "SF11", "250 kHz", "~0.54 kbps", "Good range"),
    ("Long Moderate", "SF11", "125 kHz", "~0.34 kbps", "Better range"),
    ("Long Slow", "SF12", "125 kHz", "~0.18 kbps", "Maximum range"),
    ("Very Long Slow", "SF12", "62.5 kHz", "~0.09 kbps", "Extreme range"),
]


class BandPlanPanel(Gtk.Box):
    """Band Plan Reference panel"""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.set_margin_start(20)
        self.set_margin_end(20)
        self.set_margin_top(10)
        self.set_margin_bottom(10)

        self._build_ui()

    def _build_ui(self):
        """Build the panel UI"""
        # Title
        title = Gtk.Label(label="Band Plan Reference")
        title.add_css_class("title-2")
        title.set_xalign(0)
        self.append(title)

        # Notebook for tabs
        notebook = Gtk.Notebook()
        notebook.set_vexpand(True)

        # Tab 1: LoRa ISM Bands
        lora_page = self._build_lora_tab()
        notebook.append_page(lora_page, Gtk.Label(label="LoRa ISM Bands"))

        # Tab 2: Amateur Bands
        amateur_page = self._build_amateur_tab()
        notebook.append_page(amateur_page, Gtk.Label(label="Amateur Bands"))

        # Tab 3: Meshtastic Presets
        presets_page = self._build_presets_tab()
        notebook.append_page(presets_page, Gtk.Label(label="Meshtastic Presets"))

        self.append(notebook)

    def _build_lora_tab(self) -> Gtk.Widget:
        """Build LoRa ISM bands tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        for region_code, info in LORA_BANDS.items():
            frame = Gtk.Frame()
            frame.set_label(info["name"])

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            content.set_margin_start(10)
            content.set_margin_end(10)
            content.set_margin_top(5)
            content.set_margin_bottom(10)

            # Frequency
            freq_row = self._create_info_row("Frequency:", info["frequency"])
            content.append(freq_row)

            # Power
            power_row = self._create_info_row("Max Power:", info["power"])
            content.append(power_row)

            # Duty cycle
            duty_row = self._create_info_row("Duty Cycle:", info["duty_cycle"])
            content.append(duty_row)

            # Notes
            notes = Gtk.Label(label=info["notes"])
            notes.add_css_class("dim-label")
            notes.add_css_class("caption")
            notes.set_xalign(0)
            content.append(notes)

            frame.set_child(content)
            box.append(frame)

        scrolled.set_child(box)
        return scrolled

    def _build_amateur_tab(self) -> Gtk.Widget:
        """Build amateur bands tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Header note
        note = Gtk.Label(label="Amateur radio (Part 97) bands relevant to mesh networking")
        note.add_css_class("dim-label")
        note.set_xalign(0)
        note.set_wrap(True)
        note.set_margin_bottom(10)
        box.append(note)

        for band_id, info in AMATEUR_BANDS.items():
            frame = Gtk.Frame()
            frame.set_label(info["name"])

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            content.set_margin_start(10)
            content.set_margin_end(10)
            content.set_margin_top(5)
            content.set_margin_bottom(10)

            # Frequency
            freq_row = self._create_info_row("Frequency:", info["frequency"])
            content.append(freq_row)

            # Power
            power_row = self._create_info_row("Max Power:", info["power"])
            content.append(power_row)

            # License
            license_row = self._create_info_row("Min License:", info["license"])
            content.append(license_row)

            # Notes
            notes = Gtk.Label(label=info["notes"])
            notes.add_css_class("dim-label")
            notes.add_css_class("caption")
            notes.set_xalign(0)
            content.append(notes)

            frame.set_child(content)
            box.append(frame)

        scrolled.set_child(box)
        return scrolled

    def _build_presets_tab(self) -> Gtk.Widget:
        """Build Meshtastic presets tab"""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # Header
        header = Gtk.Label(label="Meshtastic Channel Presets")
        header.add_css_class("heading")
        header.set_xalign(0)
        box.append(header)

        desc = Gtk.Label(label="Higher spreading factor = longer range but slower speed")
        desc.add_css_class("dim-label")
        desc.set_xalign(0)
        desc.set_margin_bottom(10)
        box.append(desc)

        # Create list box for presets
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")

        for preset_name, sf, bw, rate, desc in MESHTASTIC_PRESETS:
            row = Gtk.ListBoxRow()
            row.set_activatable(False)

            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row_box.set_margin_start(10)
            row_box.set_margin_end(10)
            row_box.set_margin_top(8)
            row_box.set_margin_bottom(8)

            # Preset name
            name_label = Gtk.Label(label=preset_name)
            name_label.add_css_class("heading")
            name_label.set_width_chars(15)
            name_label.set_xalign(0)
            row_box.append(name_label)

            # Technical specs
            specs = Gtk.Label(label=f"{sf} / {bw}")
            specs.set_width_chars(15)
            specs.add_css_class("dim-label")
            row_box.append(specs)

            # Data rate
            rate_label = Gtk.Label(label=rate)
            rate_label.set_width_chars(12)
            rate_label.add_css_class("dim-label")
            row_box.append(rate_label)

            # Description
            desc_label = Gtk.Label(label=desc)
            desc_label.add_css_class("dim-label")
            desc_label.add_css_class("caption")
            desc_label.set_hexpand(True)
            desc_label.set_xalign(0)
            row_box.append(desc_label)

            row.set_child(row_box)
            listbox.append(row)

        box.append(listbox)

        scrolled.set_child(box)
        return scrolled

    def _create_info_row(self, label: str, value: str) -> Gtk.Box:
        """Create a label-value row"""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        label_widget = Gtk.Label(label=label)
        label_widget.set_width_chars(12)
        label_widget.set_xalign(0)
        label_widget.add_css_class("dim-label")
        row.append(label_widget)

        value_widget = Gtk.Label(label=value)
        value_widget.set_xalign(0)
        row.append(value_widget)

        return row


class BandPlanPlugin(Plugin):
    """Band Plan Reference plugin for MeshForge.io"""

    def activate(self, context: PluginContext) -> None:
        """Called when plugin is activated"""
        context.register_panel(
            panel_id="band_plan_panel",
            panel_class=BandPlanPanel,
            title="Band Plans",
            icon="view-list-symbolic"
        )

    def deactivate(self) -> None:
        """Called when plugin is deactivated"""
        pass
