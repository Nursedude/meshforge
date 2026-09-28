"""
Site Planner Handler — RF coverage planning, range estimation, preset comparison.

Converted from site_planner_mixin.py as part of the mixin-to-registry migration.
"""

from handler_protocol import BaseHandler
from utils.rf import DeployEnvironment, BuildingType

_ENV_CHOICES = [
    ("free_space", "Free Space / Clear LOS"),
    ("rural_open", "Rural Open Terrain"),
    ("suburban", "Suburban / Residential"),
    ("urban_elevated", "Urban w/ Elevated Gateway"),
    ("urban_ground", "Urban Ground Level"),
    ("dense_urban", "Dense Urban / Downtown"),
    ("forest", "Forest / Heavy Vegetation"),
    ("over_water", "Over Water / Coastal"),
    ("indoor", "Indoor (same building)"),
]

_ENV_MAP = {v.value: v for v in DeployEnvironment}


class SitePlannerHandler(BaseHandler):
    """TUI handler for site planner / RF coverage planning."""

    handler_id = "site_planner"
    menu_section = "rf_sdr"

    def menu_items(self):
        return [
            ("site", "Site Planner        Coverage estimation", None),
        ]

    def execute(self, action):
        if action == "site":
            self._site_planner_menu()

    def _site_planner_menu(self):
        while True:
            choices = [
                ("link", "Link Budget Calculator"),
                ("range", "Range Estimator"),
                ("presets", "LoRa Preset Comparison"),
                ("fresnel", "Fresnel Zone Calculator"),
                ("antenna", "Antenna Guidelines"),
                ("freq", "Frequency Reference"),
                ("tools", "External Planning Tools"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu("Site Planner", "RF coverage and link planning:", choices)
            if choice is None or choice == "back":
                break
            dispatch = {
                "link": ("Link Budget", self._calc_link_budget),
                "range": ("Range Estimator", self._estimate_range),
                "presets": ("Preset Comparison", self._compare_presets),
                "fresnel": ("Fresnel Zone", self._calc_fresnel),
                "antenna": ("Antenna Guidelines", self._antenna_guidelines),
                "freq": ("Frequency Reference", self._frequency_reference),
                "tools": ("External Tools", self._external_tools),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
            else:
                self.ctx.notify_unwired(choice, "SitePlannerHandler._site_planner_menu")

    def _select_environment(self):
        choice = self.ctx.dialog.menu("Environment", "Select deployment environment:", _ENV_CHOICES)
        if choice is None:
            return None
        return _ENV_MAP.get(choice, DeployEnvironment.SUBURBAN)

    def _calc_link_budget(self):
        """Link budget calculator - delegates to RFToolsHandler if available."""
        # This is a shared function also in rf_tools handler. Provide standalone version.
        from utils.rf import free_space_path_loss
        freq = self.ctx.dialog.inputbox("Link Budget", "Frequency (MHz):", "915")
        if not freq:
            return
        dist = self.ctx.dialog.inputbox("Link Budget", "Distance (km):", "1.0")
        if not dist:
            return
        tx_power = self.ctx.dialog.inputbox("Link Budget", "TX Power (dBm):", "22")
        if not tx_power:
            return
        ant_gain = self.ctx.dialog.inputbox("Link Budget", "Total Antenna Gain (dBi):", "4.3")
        if not ant_gain:
            return
        rx_sens = self.ctx.dialog.inputbox("Link Budget", "RX Sensitivity (dBm):", "-130")
        if not rx_sens:
            return
        try:
            f = float(freq)
            d = float(dist)
            tx = float(tx_power)
            ag = float(ant_gain)
            rs = float(rx_sens)
            # free_space_path_loss expects (distance_m, freq_mhz); inputs are
            # km and MHz, so convert km -> m. (was: (f, d) — args swapped, ~60 dB wrong)
            fspl = free_space_path_loss(d * 1000.0, f)
            margin = tx + ag - fspl - rs
            text = f"""Link Budget Calculator:

Frequency: {f} MHz
Distance: {d} km
TX Power: {tx} dBm
Antenna Gain: {ag} dBi
RX Sensitivity: {rs} dBm

FSPL: {fspl:.1f} dB
Link Budget: {tx + ag - fspl:.1f} dBm
Link Margin: {margin:.1f} dB

{"LINK VIABLE" if margin > 0 else "LINK NOT VIABLE"}"""
            self.ctx.dialog.msgbox("Link Budget", text)
        except (ValueError, TypeError):
            self.ctx.dialog.msgbox("Error", "Invalid number entered")

    def _calc_fresnel(self):
        """Fresnel zone calculator."""
        from utils.rf import fresnel_radius
        freq = self.ctx.dialog.inputbox("Fresnel Zone", "Frequency (MHz):", "915")
        if not freq:
            return
        dist = self.ctx.dialog.inputbox("Fresnel Zone", "Total path distance (km):", "5.0")
        if not dist:
            return
        try:
            f = float(freq)
            d = float(dist)
            # fresnel_radius expects (distance_km, freq_ghz); inputs are km and MHz.
            radius = fresnel_radius(d, f / 1000.0)
            text = f"""Fresnel Zone Calculator:

Frequency: {f} MHz
Path Distance: {d} km
1st Fresnel Radius: {radius:.1f} m

60% clearance needed: {radius * 0.6:.1f} m
Minimum antenna height above
obstacles at midpoint: {radius * 0.6:.1f} m"""
            self.ctx.dialog.msgbox("Fresnel Zone", text)
        except (ValueError, TypeError):
            self.ctx.dialog.msgbox("Error", "Invalid number entered")

    def _estimate_range(self):
        from utils.preset_impact import PresetAnalyzer, PRESET_PARAMS
        tx_pwr = self.ctx.dialog.inputbox("Range Estimator", "TX Power (dBm):", "22")
        if not tx_pwr:
            return
        ant_gain = self.ctx.dialog.inputbox("Range Estimator", "Total Antenna Gain (dBi):", "4.3")
        if not ant_gain:
            return
        ant_height = self.ctx.dialog.inputbox("Range Estimator", "Antenna Height (m):", "2")
        if not ant_height:
            return
        preset_choices = [(name, params['desc']) for name, params in PRESET_PARAMS.items()]
        preset = self.ctx.dialog.menu("Select Preset", "Choose LoRa modem preset:", preset_choices)
        if not preset:
            return
        env = self._select_environment()
        if env is None:
            return
        try:
            tx_p = float(tx_pwr)
            ant_g = float(ant_gain)
            ant_h = float(ant_height)
            analyzer = PresetAnalyzer(tx_power_dbm=int(tx_p), tx_gain_dbi=ant_g / 2, rx_gain_dbi=ant_g / 2, environment=env, antenna_height_m=ant_h)
            impact = analyzer.analyze_preset(preset)
            los_analyzer = PresetAnalyzer(tx_power_dbm=int(tx_p), tx_gain_dbi=ant_g / 2, rx_gain_dbi=ant_g / 2, environment=DeployEnvironment.FREE_SPACE, antenna_height_m=ant_h)
            los_impact = los_analyzer.analyze_preset(preset)
            env_label = dict(_ENV_CHOICES).get(env.value, env.value)
            text = f"""Range Estimation ({env_label}):

Preset: {preset}
TX Power: {tx_p:.0f} dBm
Antenna Gain: {ant_g} dBi
Antenna Height: {ant_h} m
Sensitivity: {impact.sensitivity_dbm:.1f} dBm
Link Budget: {impact.link_budget_db:.1f} dB

Estimated Max Range (915 MHz):
  {env_label}: {impact.max_range_km:.1f} km
  Free Space (LOS ref): {los_impact.max_range_km:.1f} km

Coverage Area: {impact.coverage_area_km2:.1f} km2
Airtime: {impact.airtime_ms:.0f} ms
Throughput: {impact.throughput_bps / 1000:.2f} kbps

Note: Uses log-distance propagation model
with environment-specific path loss exponent
and fade margin."""
            self.ctx.dialog.msgbox("Range Estimation", text)
        except (ValueError, TypeError):
            self.ctx.dialog.msgbox("Error", "Invalid number entered")

    def _compare_presets(self):
        from utils.preset_impact import PresetAnalyzer, format_comparison_table
        env = self._select_environment()
        if env is None:
            env = DeployEnvironment.FREE_SPACE
        analyzer = PresetAnalyzer(environment=env)
        comp = analyzer.compare()
        table = format_comparison_table(comp)
        env_label = dict(_ENV_CHOICES).get(env.value, env.value)
        self.ctx.dialog.msgbox(f"Preset Comparison ({env_label})", table)

    @staticmethod
    def _cable_loss_lines() -> str:
        """Cable loss lines GENERATED from utils.rf (the table the link-budget
        math uses). The hand-typed text was ~2x optimistic vs rf.py (LMR-400
        0.7 vs 1.5 dB/10 m) — TUI audit finding 4."""
        from utils.rf import CABLE_LOSS_DB_PER_M
        rows = (("rg58", "RG58", " (avoid)"), ("rg8x", "RG8X", ""),
                ("lmr240", "LMR-240", ""), ("lmr400", "LMR-400", " (recommended)"))
        return "\n".join(f"  - {label}: ~{CABLE_LOSS_DB_PER_M[key] * 10:.1f} dB{note}"
                         for key, label, note in rows)

    @staticmethod
    def _default_frequency_lines() -> str:
        """Default LongFast centre frequencies GENERATED from the firmware's own
        slot math (utils.meshtastic_modem). The hand-typed text said "US Ch 0"
        (906.875 is hashed slot 20; channel_num 0 means "hash the name") and
        EU 433 433.175 (the firmware tunes 433.875) — TUI audit finding 4."""
        from utils.meshtastic_modem import channel_centre_mhz
        out = []
        for region, label in (("US", "US"), ("EU_868", "EU 868"), ("EU_433", "EU 433")):
            f, slot, n = channel_centre_mhz("LONG_FAST", 0, region)
            out.append(f"  {label}: {f:.3f} MHz (LongFast, slot {slot} of {n})")
        return "\n".join(out)

    def _antenna_guidelines(self):
        text = """Antenna Guidelines for 915 MHz:

Height:
  - Higher is better for range
  - 10m height doubles range vs 2m
  - Avoid below tree canopy

Antenna Types:
  - Dipole: 2.15 dBi, omnidirectional
  - 1/4 wave GP: 2-3 dBi, omnidirectional
  - Yagi: 6-12 dBi, directional
  - Colinear: 5-9 dBi, omnidirectional

Cable Loss (per 10m @ 915MHz):
{cable}

Best Practices:
  - Mount antenna clear of obstructions
  - Use quality coax, keep runs short
  - Ground antenna mast for lightning
  - Weatherproof all connections""".format(cable=self._cable_loss_lines())
        self.ctx.dialog.msgbox("Antenna Guidelines", text)

    def _frequency_reference(self):
        text = """LoRa Frequency Reference:

Region      Frequencies       TX Power
───────────────────────────────────────
US/FCC      902-928 MHz       30 dBm
EU 868      863-870 MHz       14-27 dBm
EU 433      433.05-434.79     10 dBm
UK          868 MHz           25 dBm
AU/NZ       915-928 MHz       30 dBm
AS          920-923 MHz       varies
CN          470-510 MHz       17 dBm
JP          920-923 MHz       13 dBm

Default Meshtastic Frequencies:
{defaults}

ISM Band Limits (US):
  EIRP: 36 dBm (4W) max
  Duty Cycle: No limit (FHSS)""".format(defaults=self._default_frequency_lines())
        self.ctx.dialog.msgbox("Frequency Reference", text)

    def _external_tools(self):
        text = """External RF Planning Tools:

Web-Based:
  meshtastic.org/docs/software/coverage/
    - Meshtastic Site Planner
    - Coverage prediction

  heywhatsthat.com
    - Line of sight analysis
    - Terrain profiles

  splat.ecso.org
    - Detailed RF coverage
    - Terrain analysis

Software:
  Radio Mobile (Windows/Wine)
    - Professional RF planning
    - Free for amateur use

  SPLAT! (Linux)
    - RF Signal Propagation
    - Terrain analysis

  CloudRF
    - Cloud-based planning
    - API available

Tip: Use these tools to plan
repeater locations and verify
line-of-sight paths."""
        self.ctx.dialog.msgbox("External Planning Tools", text)
