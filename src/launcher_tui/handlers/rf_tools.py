"""
RF Tools Handler — Frequency calculator, FSPL, link budget, Fresnel, EIRP, antenna.

Converted from rf_tools_mixin.py as part of the mixin-to-registry migration.
"""

import math

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

_ANTENNA_PRESETS, _get_antenna_preset, _format_antenna_comparison, _coverage_with_antenna, _HAS_ANTENNA = safe_import(
    'utils.antenna_patterns',
    'ANTENNA_PRESETS', 'get_antenna_preset',
    'format_antenna_comparison', 'coverage_with_antenna',
)


class RFToolsHandler(BaseHandler):
    """TUI handler for RF calculation tools."""

    handler_id = "rf_tools"
    menu_section = "rf_sdr"

    def menu_items(self):
        return [
            ("link", "Link Budget         FSPL, Fresnel, range", None),
            ("freq", "Frequency Slots     Channel calculator", None),
            ("antenna", "Antenna Analysis    Compare antenna types", None),
        ]

    def execute(self, action):
        dispatch = {
            "link": self._rf_tools_menu,
            "freq": self._calc_frequency_slot,
            "antenna": self._antenna_comparison,
        }
        method = dispatch.get(action)
        if method:
            method()

    def _rf_tools_menu(self):
        """RF tools menu."""
        choices = [
            ("freq", "Frequency Slot Calculator"),
            ("fspl", "Free Space Path Loss"),
            ("link", "Link Budget Calculator"),
            ("fresnel", "Fresnel Zone"),
            ("power", "EIRP Calculator"),
            ("antenna", "Antenna Comparison"),
            ("back", "Back"),
        ]

        while True:
            choice = self.ctx.dialog.menu(
                "RF Tools",
                "Radio frequency calculations:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "freq": ("Frequency Slots", self._calc_frequency_slot),
                "fspl": ("FSPL Calculator", self._calc_fspl),
                "link": ("Link Budget", self._calc_link_budget),
                "fresnel": ("Fresnel Zone", self._calc_fresnel),
                "power": ("EIRP Calculator", self._calc_eirp),
                "antenna": ("Antenna Comparison", self._antenna_comparison),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _calc_frequency_slot(self):
        """Meshtastic Frequency Slot Calculator."""
        regions = [
            ("US", 902.0, 928.0, 104, "United States ISM"),
            ("ANZ", 915.0, 928.0, 52, "Australia/NZ"),
            ("EU_868", 869.4, 869.65, 1, "EU 869 MHz (SRD)"),
            ("EU_433", 433.0, 434.0, 8, "EU 433 MHz"),
            ("UK_868", 869.4, 869.65, 1, "UK 869 MHz"),
            ("UA_868", 868.0, 868.6, 2, "Ukraine 868 MHz"),
            ("UA_433", 433.0, 434.79, 8, "Ukraine 433 MHz"),
            ("RU", 868.7, 869.2, 2, "Russia"),
            ("JP", 920.8, 923.8, 10, "Japan"),
            ("KR", 920.0, 923.0, 12, "Korea"),
            ("TW", 920.0, 925.0, 20, "Taiwan"),
            ("CN", 470.0, 510.0, 80, "China"),
            ("IN", 865.0, 867.0, 8, "India"),
            ("TH", 920.0, 925.0, 20, "Thailand"),
            ("PH", 920.0, 925.0, 20, "Philippines"),
            ("SG_923", 920.0, 925.0, 20, "Singapore 923"),
            ("MY_433", 433.0, 435.0, 8, "Malaysia 433 MHz"),
            ("MY_919", 919.0, 924.0, 20, "Malaysia 919 MHz"),
            ("NZ_865", 864.0, 868.0, 16, "New Zealand 865 MHz"),
            ("LORA_24", 2400.0, 2483.5, 39, "2.4 GHz ISM (worldwide)"),
        ]

        region_choices = [(r[0], f"{r[0]}: {r[1]:.1f}-{r[2]:.1f} MHz") for r in regions]
        region_choices.append(("back", "Back"))

        region_choice = self.ctx.dialog.menu(
            "Frequency Slot",
            "Select region:",
            region_choices
        )

        if not region_choice or region_choice == "back":
            return

        region = None
        for r in regions:
            if r[0] == region_choice:
                region = r
                break

        if not region:
            return

        mode_choices = [
            ("name", "Calculate from Channel Name"),
            ("slot", "Enter Slot Number Directly"),
            ("back", "Back"),
        ]

        mode = self.ctx.dialog.menu(
            "Input Mode",
            "Calculate frequency from:",
            mode_choices
        )

        if not mode or mode == "back":
            return

        bw_choices = [
            ("500", "500 kHz (SHORT_TURBO)"),
            ("250", "250 kHz (FAST presets)"),
            ("125", "125 kHz (SLOW presets)"),
            ("62.5", "62.5 kHz (VERY_LONG_SLOW)"),
        ]

        bw_choice = self.ctx.dialog.menu(
            "Bandwidth",
            "Select modem bandwidth:",
            bw_choices
        )

        if not bw_choice:
            return

        try:
            bw_khz = float(bw_choice)

            region_name = region[0]
            freq_start = region[1]
            freq_end = region[2]
            max_slots = region[3]
            region_desc = region[4]

            calculated_slots = int(math.floor((freq_end - freq_start) / (bw_khz / 1000)))
            num_channels = min(calculated_slots, max_slots) if calculated_slots > 0 else max_slots

            if mode == "name":
                channel_name = self.ctx.dialog.inputbox(
                    "Channel Name",
                    "Enter channel name:",
                    "LongFast"
                )

                if not channel_name:
                    return

                def djb2_hash(s):
                    h = 5381
                    for c in s:
                        h = ((h << 5) + h) + ord(c)
                        h &= 0xFFFFFFFF
                    return h

                hash_val = djb2_hash(channel_name)
                slot = hash_val % num_channels

            else:
                slot_str = self.ctx.dialog.inputbox(
                    "Slot Number",
                    f"Enter slot number (0-{num_channels-1}):",
                    "20"
                )

                if not slot_str:
                    return

                slot = int(slot_str)
                if slot < 0 or slot >= num_channels:
                    self.ctx.dialog.msgbox("Error", f"Slot must be 0-{num_channels-1}")
                    return

            freq_mhz = freq_start + (bw_khz / 2000) + (slot * (bw_khz / 1000))

            text = f"""Frequency Slot Calculation:

Region: {region_name} ({region_desc})
Band: {freq_start:.1f} - {freq_end:.1f} MHz
Bandwidth: {bw_khz} kHz
Available Slots: {num_channels}

Slot Number: {slot}
Center Frequency: {freq_mhz:.3f} MHz

Channel spans:
  {freq_mhz - bw_khz/2000:.3f} - {freq_mhz + bw_khz/2000:.3f} MHz"""

            if mode == "name":
                text += f"\n\nChannel Name: {channel_name}"
                text += f"\nHash Value: {hash_val}"

            self.ctx.dialog.msgbox("Frequency Result", text)

        except ValueError:
            self.ctx.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", str(e))

    def _calc_fspl(self):
        """Calculate Free Space Path Loss."""
        try:
            dist_str = self.ctx.dialog.inputbox(
                "FSPL Calculator",
                "Distance (km):",
                "1"
            )
            if not dist_str:
                return

            freq_str = self.ctx.dialog.inputbox(
                "FSPL Calculator",
                "Frequency (MHz):",
                "915"
            )
            if not freq_str:
                return

            distance = float(dist_str)
            freq = float(freq_str)

            fspl = 20 * math.log10(distance) + 20 * math.log10(freq) + 32.45

            text = f"""Free Space Path Loss:

Distance: {distance} km
Frequency: {freq} MHz

FSPL: {fspl:.1f} dB

Note: This is theoretical minimum loss.
Actual loss will be higher due to
terrain, vegetation, and atmospheric
conditions."""

            self.ctx.dialog.msgbox("FSPL Result", text)

        except ValueError:
            self.ctx.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", str(e))

    def _calc_link_budget(self):
        """Calculate link budget."""
        try:
            tx_pwr = self.ctx.dialog.inputbox("Link Budget", "TX Power (dBm):", "20")
            if not tx_pwr:
                return

            tx_gain = self.ctx.dialog.inputbox("Link Budget", "TX Antenna Gain (dBi):", "2")
            if not tx_gain:
                return

            rx_gain = self.ctx.dialog.inputbox("Link Budget", "RX Antenna Gain (dBi):", "2")
            if not rx_gain:
                return

            path_loss = self.ctx.dialog.inputbox("Link Budget", "Path Loss (dB):", "100")
            if not path_loss:
                return

            rx_sens = self.ctx.dialog.inputbox("Link Budget", "RX Sensitivity (dBm):", "-130")
            if not rx_sens:
                return

            tx_p = float(tx_pwr)
            tx_g = float(tx_gain)
            rx_g = float(rx_gain)
            pl = float(path_loss)
            rx_s = float(rx_sens)

            rx_power = tx_p + tx_g + rx_g - pl
            link_margin = rx_power - rx_s

            status = "GOOD" if link_margin > 10 else "MARGINAL" if link_margin > 0 else "NO LINK"

            text = f"""Link Budget Analysis:

TX Power: {tx_p} dBm
TX Antenna: +{tx_g} dBi
RX Antenna: +{rx_g} dBi
Path Loss: -{pl} dB
RX Sensitivity: {rx_s} dBm

Received Power: {rx_power:.1f} dBm
Link Margin: {link_margin:.1f} dB

Status: {status}"""

            self.ctx.dialog.msgbox("Link Budget", text)

        except ValueError:
            self.ctx.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", str(e))

    def _calc_fresnel(self):
        """Calculate Fresnel zone."""
        try:
            dist_str = self.ctx.dialog.inputbox("Fresnel Zone", "Distance (km):", "5")
            if not dist_str:
                return

            freq_str = self.ctx.dialog.inputbox("Fresnel Zone", "Frequency (MHz):", "915")
            if not freq_str:
                return

            distance = float(dist_str) * 1000
            freq = float(freq_str) * 1e6

            c = 3e8
            wavelength = c / freq
            d1 = d2 = distance / 2

            r1 = math.sqrt(wavelength * d1 * d2 / (d1 + d2))

            clearance = r1 * 0.6

            text = f"""Fresnel Zone Calculator:

Distance: {distance/1000:.1f} km
Frequency: {freq/1e6:.0f} MHz
Wavelength: {wavelength:.3f} m

1st Fresnel Zone Radius: {r1:.1f} m
60% Clearance Needed: {clearance:.1f} m

For best signal, ensure no obstacles
within {clearance:.1f}m of the line
of sight at the midpoint."""

            self.ctx.dialog.msgbox("Fresnel Zone", text)

        except ValueError:
            self.ctx.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", str(e))

    def _calc_eirp(self):
        """Calculate EIRP."""
        try:
            tx_pwr = self.ctx.dialog.inputbox("EIRP", "TX Power (dBm):", "20")
            if not tx_pwr:
                return

            cable_loss = self.ctx.dialog.inputbox("EIRP", "Cable Loss (dB):", "1")
            if not cable_loss:
                return

            ant_gain = self.ctx.dialog.inputbox("EIRP", "Antenna Gain (dBi):", "6")
            if not ant_gain:
                return

            tx = float(tx_pwr)
            loss = float(cable_loss)
            gain = float(ant_gain)

            eirp = tx - loss + gain

            eirp_watts = 10 ** ((eirp - 30) / 10)

            legal = "LEGAL (under 36 dBm)" if eirp <= 36 else "EXCEEDS FCC LIMIT"

            text = f"""EIRP Calculator:

TX Power: {tx} dBm
Cable Loss: -{loss} dB
Antenna Gain: +{gain} dBi

EIRP: {eirp:.1f} dBm ({eirp_watts*1000:.0f} mW)

US 915MHz ISM: {legal}

Note: Check local regulations."""

            self.ctx.dialog.msgbox("EIRP Result", text)

        except ValueError:
            self.ctx.dialog.msgbox("Error", "Invalid number entered")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", str(e))

    def _antenna_comparison(self):
        """Compare antenna types for Meshtastic deployments."""
        if not _HAS_ANTENNA:
            self.ctx.dialog.msgbox(
                "Not Available",
                "Antenna patterns module not available.\n"
                "File: src/utils/antenna_patterns.py"
            )
            return

        while True:
            choices = [
                ("compare", "Compare All         Side-by-side comparison"),
                ("coverage", "Coverage Estimate   Range with specific antenna"),
                ("specs", "Antenna Specs       Detailed specifications"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Antenna Analysis",
                "Compare antenna types for Meshtastic deployments:",
                choices
            )

            if choice is None or choice == "back":
                break

            if choice == "compare":
                self.ctx.safe_call("Antenna Compare", self._antenna_compare_all)
            elif choice == "coverage":
                self.ctx.safe_call("Coverage Estimate", self._antenna_coverage_estimate)
            elif choice == "specs":
                self.ctx.safe_call("Antenna Specs", self._antenna_specs_display)

    def _antenna_compare_all(self):
        """Show side-by-side antenna comparison table."""
        az_str = self.ctx.dialog.inputbox(
            "Target Azimuth",
            "Target direction in degrees (0=North, 90=East):",
            "0"
        )
        if not az_str:
            return
        azimuth = float(az_str)

        range_str = self.ctx.dialog.inputbox(
            "Base Range",
            "Base range with stock antenna (km):",
            "10"
        )
        if not range_str:
            return
        base_range = float(range_str)

        antennas = []
        for name in _ANTENNA_PRESETS:
            antenna = _get_antenna_preset(name, aim_azimuth=azimuth)
            antennas.append(antenna)

        clear_screen()
        table = _format_antenna_comparison(antennas, base_range, azimuth)
        print(table)
        print(f"\n  Base range (stock whip): {base_range:.1f} km")
        print(f"  Target azimuth: {azimuth:.0f} degrees")
        print(f"\n  Factor: range multiplier vs stock dipole (2.15 dBi)")
        print()
        self.ctx.wait_for_enter()

    def _antenna_coverage_estimate(self):
        """Estimate coverage with a specific antenna at all compass points."""
        preset_choices = []
        for key in _ANTENNA_PRESETS:
            antenna = _get_antenna_preset(key)
            spec = antenna.spec()
            preset_choices.append((key, f"{spec.name:<20} {spec.peak_gain_dbi:>5.1f} dBi"))
        preset_choices.append(("back", "Back"))

        preset = self.ctx.dialog.menu(
            "Select Antenna",
            "Choose antenna type:",
            preset_choices
        )
        if not preset or preset == "back":
            return

        az_str = self.ctx.dialog.inputbox(
            "Antenna Pointing Direction",
            "Aim azimuth in degrees (0=N, 90=E, 180=S, 270=W):",
            "0"
        )
        if not az_str:
            return
        azimuth = float(az_str)

        range_str = self.ctx.dialog.inputbox(
            "Base Range",
            "Base range with stock antenna (km):",
            "10"
        )
        if not range_str:
            return
        base_range = float(range_str)

        antenna = _get_antenna_preset(preset, aim_azimuth=azimuth)
        spec = antenna.spec()

        directions = [
            (0, "N"), (45, "NE"), (90, "E"), (135, "SE"),
            (180, "S"), (225, "SW"), (270, "W"), (315, "NW"),
        ]

        lines = [f"Antenna: {spec.name}"]
        lines.append(f"Peak gain: {spec.peak_gain_dbi:.1f} dBi")
        lines.append(f"H beamwidth: {spec.h_beamwidth_deg:.0f} deg")
        lines.append(f"V beamwidth: {spec.v_beamwidth_deg:.0f} deg")
        if spec.front_to_back_db > 0:
            lines.append(f"F/B ratio: {spec.front_to_back_db:.0f} dB")
        lines.append(f"Aim: {azimuth:.0f} deg")
        lines.append(f"Base range: {base_range:.1f} km (stock whip)\n")
        lines.append(f"{'Direction':>10} {'Gain':>8} {'Range':>8} {'Factor':>7}")
        lines.append("-" * 37)

        for deg, label in directions:
            gain = antenna.gain_at(deg, 0.0)
            rng = _coverage_with_antenna(base_range, antenna, deg)
            factor = antenna.effective_range_factor(deg)
            lines.append(f"{label:>10} {gain:>6.1f}dBi {rng:>6.1f}km {factor:>6.2f}x")

        self.ctx.dialog.msgbox("Coverage Estimate", "\n".join(lines))

    def _antenna_specs_display(self):
        """Show detailed specs for all antenna presets."""
        clear_screen()
        print("=== Antenna Specifications ===\n")
        print(f"  {'Name':<22} {'Type':<14} {'Gain':>6} {'H Beam':>7} {'V Beam':>7} {'F/B':>5}")
        print(f"  {'-'*65}")

        for key in _ANTENNA_PRESETS:
            antenna = _get_antenna_preset(key)
            spec = antenna.spec()
            fb = f"{spec.front_to_back_db:.0f}dB" if spec.front_to_back_db > 0 else "---"
            print(f"  {spec.name:<22} {spec.type_name:<14} "
                  f"{spec.peak_gain_dbi:>5.1f}i {spec.h_beamwidth_deg:>5.0f}° "
                  f"{spec.v_beamwidth_deg:>5.0f}° {fb:>5}")

        print(f"\n  Notes:")
        print(f"  - Gain in dBi (referenced to isotropic)")
        print(f"  - H Beam: horizontal -3dB beamwidth")
        print(f"  - V Beam: vertical -3dB beamwidth")
        print(f"  - F/B: front-to-back ratio (directional only)")
        print()
        self.ctx.wait_for_enter()
