"""
Site Planner Handler — RF coverage planning, range estimation, preset comparison,
terrain LOS analysis, and terrain-aware coverage prediction.

Converted from site_planner_mixin.py as part of the mixin-to-registry migration.
"""

import threading
import webbrowser

from handler_protocol import BaseHandler
from utils.rf import DeployEnvironment, BuildingType
from utils.safe_import import safe_import

_LOSAnalyzer, _SRTMProvider, _FlatTerrainProvider, _HAS_TERRAIN = safe_import(
    'utils.terrain', 'LOSAnalyzer', 'SRTMProvider', 'FlatTerrainProvider'
)
_CoverageMapGenerator, _HAS_COVERAGE_MAP = safe_import(
    'utils.coverage_map', 'CoverageMapGenerator'
)

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
                ("los", "Line-of-Sight Check"),
                ("terrain", "Terrain Coverage Map"),
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
                "los": ("Line-of-Sight Check", self._los_check),
                "terrain": ("Terrain Coverage Map", self._terrain_coverage),
                "antenna": ("Antenna Guidelines", self._antenna_guidelines),
                "freq": ("Frequency Reference", self._frequency_reference),
                "tools": ("External Tools", self._external_tools),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

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
            fspl = free_space_path_loss(f, d)
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
        from utils.rf import fresnel_zone_radius
        freq = self.ctx.dialog.inputbox("Fresnel Zone", "Frequency (MHz):", "915")
        if not freq:
            return
        dist = self.ctx.dialog.inputbox("Fresnel Zone", "Total path distance (km):", "5.0")
        if not dist:
            return
        try:
            f = float(freq)
            d = float(dist)
            radius = fresnel_zone_radius(f, d)
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
  - RG58: ~2.5 dB (avoid)
  - RG8X: ~1.8 dB
  - LMR-240: ~1.3 dB
  - LMR-400: ~0.7 dB (recommended)

Best Practices:
  - Mount antenna clear of obstructions
  - Use quality coax, keep runs short
  - Ground antenna mast for lightning
  - Weatherproof all connections"""
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
  US: 906.875 MHz (Ch 0)
  EU 868: 869.525 MHz
  EU 433: 433.175 MHz

ISM Band Limits (US):
  EIRP: 36 dBm (4W) max
  Duty Cycle: No limit (FHSS)"""
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

    def _los_check(self):
        """Line-of-sight analysis between two coordinates using terrain data."""
        if not _HAS_TERRAIN:
            self.ctx.dialog.msgbox(
                "Not Available",
                "Terrain module not available.\n\n"
                "The terrain analysis engine requires\n"
                "SRTM elevation data support."
            )
            return

        # Collect Point A
        lat1 = self.ctx.dialog.inputbox("LOS Check - Point A", "Latitude:", "21.31")
        if not lat1:
            return
        lon1 = self.ctx.dialog.inputbox("LOS Check - Point A", "Longitude:", "-157.86")
        if not lon1:
            return
        alt1 = self.ctx.dialog.inputbox("LOS Check - Point A", "Antenna height (m):", "10")
        if not alt1:
            return

        # Collect Point B
        lat2 = self.ctx.dialog.inputbox("LOS Check - Point B", "Latitude:", "21.35")
        if not lat2:
            return
        lon2 = self.ctx.dialog.inputbox("LOS Check - Point B", "Longitude:", "-157.90")
        if not lon2:
            return
        alt2 = self.ctx.dialog.inputbox("LOS Check - Point B", "Antenna height (m):", "5")
        if not alt2:
            return

        freq = self.ctx.dialog.inputbox("LOS Check", "Frequency (MHz):", "915")
        if not freq:
            return

        try:
            la1, lo1, a1 = float(lat1), float(lon1), float(alt1)
            la2, lo2, a2 = float(lat2), float(lon2), float(alt2)
            f = float(freq)

            self.ctx.dialog.infobox(
                "Calculating",
                "Fetching terrain data and analyzing LOS...\n\n"
                "First run may download SRTM elevation tiles."
            )

            try:
                provider = _SRTMProvider(auto_download=True)
            except Exception:
                provider = _FlatTerrainProvider()

            analyzer = _LOSAnalyzer(provider)
            result = analyzer.analyze(la1, lo1, a1, la2, lo2, a2, f)

            status = "CLEAR" if result.is_clear else "OBSTRUCTED"
            text = f"""Line-of-Sight Analysis:

Point A: {la1:.4f}, {lo1:.4f} ({a1:.0f}m antenna)
Point B: {la2:.4f}, {lo2:.4f} ({a2:.0f}m antenna)
Frequency: {f:.0f} MHz

Status: {status}
Distance: {result.distance_m / 1000:.2f} km

Path Loss:
  Free Space: {result.fspl_db:.1f} dB
  Terrain:    {result.terrain_loss_db:.1f} dB
  Total:      {result.total_loss_db:.1f} dB

Terrain Analysis:
  Obstructions: {result.num_obstructions}
  Worst Obstruction: {result.worst_obstruction_m:.1f} m
  Fresnel Clearance: {result.fresnel_clearance_pct:.0f}%
  Earth Bulge (mid): {result.earth_bulge_m:.1f} m"""

            self.ctx.dialog.msgbox("LOS Result", text)
        except (ValueError, TypeError):
            self.ctx.dialog.msgbox("Error", "Invalid number entered")

    def _terrain_coverage(self):
        """Generate terrain-aware coverage map and open in browser."""
        if not _HAS_TERRAIN:
            self.ctx.dialog.msgbox(
                "Not Available",
                "Terrain module not available.\n\n"
                "The terrain analysis engine requires\n"
                "SRTM elevation data support."
            )
            return
        if not _HAS_COVERAGE_MAP:
            self.ctx.dialog.msgbox(
                "Not Available",
                "Coverage map generator not available.\n\n"
                "Install folium: pip3 install folium"
            )
            return

        lat = self.ctx.dialog.inputbox("Terrain Coverage", "Node Latitude:", "21.31")
        if not lat:
            return
        lon = self.ctx.dialog.inputbox("Terrain Coverage", "Node Longitude:", "-157.86")
        if not lon:
            return
        height = self.ctx.dialog.inputbox("Terrain Coverage", "Antenna height (m):", "10")
        if not height:
            return
        radius = self.ctx.dialog.inputbox("Terrain Coverage", "Analysis radius (km):", "5")
        if not radius:
            return
        freq = self.ctx.dialog.inputbox("Terrain Coverage", "Frequency (MHz):", "915")
        if not freq:
            return

        try:
            la, lo = float(lat), float(lon)
            h, r, f = float(height), float(radius), float(freq)

            self.ctx.dialog.infobox(
                "Calculating",
                f"Generating terrain coverage for {la:.4f}, {lo:.4f}...\n"
                f"Radius: {r} km, Height: {h} m\n\n"
                "This may take a moment (fetching SRTM data)..."
            )

            from utils.paths import get_real_user_home
            generator = _CoverageMapGenerator()
            output_dir = get_real_user_home() / ".local" / "share" / "meshforge"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / "terrain_coverage.html")

            result_path = generator.generate_terrain_coverage(
                la, lo,
                antenna_height=h,
                radius_km=r,
                freq_mhz=f,
                output_path=output_path,
            )

            if not result_path:
                self.ctx.dialog.msgbox("Error", "Terrain coverage generation failed.")
                return

            self.ctx.dialog.msgbox(
                "Terrain Coverage",
                f"Coverage map saved to:\n{result_path}\n\n"
                "Opening in browser..."
            )

            threading.Thread(
                target=lambda: webbrowser.open(f"file://{result_path}"),
                daemon=True
            ).start()

        except (ValueError, TypeError):
            self.ctx.dialog.msgbox("Error", "Invalid number entered")
