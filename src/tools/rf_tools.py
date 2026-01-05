"""
RF Tools - Radio Frequency utilities for meshtasticd-installer

Provides RF analysis and testing tools:
- Link budget calculator
- FSPL calculations
- LoRa parameter analysis
- SPI/Radio device detection
- Signal strength estimation
"""

import subprocess
import os
import math
from typing import Optional, Dict, List, Tuple
from pathlib import Path
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm, FloatPrompt, IntPrompt
from rich.layout import Layout

console = Console()


@dataclass
class LoRaPreset:
    """LoRa modem preset configuration"""
    name: str
    bandwidth: int  # Hz
    spreading_factor: int
    coding_rate: str
    data_rate: float  # bps
    sensitivity: float  # dBm
    range_los: float  # km line-of-sight
    range_urban: float  # km urban


# LoRa presets with characteristics
LORA_PRESETS = {
    'SHORT_TURBO': LoRaPreset('SHORT_TURBO', 500000, 7, '4/5', 21875, -108, 3, 1),
    'SHORT_FAST': LoRaPreset('SHORT_FAST', 250000, 7, '4/5', 10937, -111, 5, 1.5),
    'SHORT_SLOW': LoRaPreset('SHORT_SLOW', 250000, 8, '4/5', 6250, -114, 8, 2),
    'MEDIUM_FAST': LoRaPreset('MEDIUM_FAST', 250000, 9, '4/5', 3516, -117, 12, 3),
    'MEDIUM_SLOW': LoRaPreset('MEDIUM_SLOW', 250000, 10, '4/5', 1953, -120, 18, 5),
    'LONG_FAST': LoRaPreset('LONG_FAST', 250000, 11, '4/5', 1066, -123, 30, 8),
    'LONG_MODERATE': LoRaPreset('LONG_MODERATE', 125000, 11, '4/5', 533, -126, 50, 12),
    'LONG_SLOW': LoRaPreset('LONG_SLOW', 125000, 12, '4/5', 293, -129, 80, 20),
    'VERY_LONG_SLOW': LoRaPreset('VERY_LONG_SLOW', 62500, 12, '4/5', 146, -132, 120, 30),
}

# Regional frequency bands
FREQUENCY_BANDS = {
    'US': {'name': 'US/Americas', 'freq': 915.0, 'power': 30},
    'EU_868': {'name': 'EU 868MHz', 'freq': 868.0, 'power': 14},
    'EU_433': {'name': 'EU 433MHz', 'freq': 433.0, 'power': 10},
    'CN': {'name': 'China', 'freq': 470.0, 'power': 17},
    'JP': {'name': 'Japan', 'freq': 920.0, 'power': 16},
    'ANZ': {'name': 'Australia/NZ', 'freq': 915.0, 'power': 30},
    'KR': {'name': 'Korea', 'freq': 920.0, 'power': 23},
    'TW': {'name': 'Taiwan', 'freq': 923.0, 'power': 27},
    'RU': {'name': 'Russia', 'freq': 868.0, 'power': 20},
    'IN': {'name': 'India', 'freq': 865.0, 'power': 30},
    'NZ_865': {'name': 'New Zealand 865', 'freq': 865.0, 'power': 30},
    'TH': {'name': 'Thailand', 'freq': 920.0, 'power': 16},
    'UA_868': {'name': 'Ukraine 868', 'freq': 868.0, 'power': 14},
    'UA_433': {'name': 'Ukraine 433', 'freq': 433.0, 'power': 10},
}


class RFTools:
    """RF analysis and testing tools"""

    def __init__(self):
        self._return_to_main = False

    def interactive_menu(self):
        """Main RF tools menu"""
        self._return_to_main = False

        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════ RF Tools ═══════════[/bold cyan]\n")

            console.print("[dim cyan]── Calculations ──[/dim cyan]")
            console.print("  [bold]1[/bold]. Link Budget Calculator")
            console.print("  [bold]2[/bold]. Free Space Path Loss (FSPL)")
            console.print("  [bold]3[/bold]. Fresnel Zone Calculator")

            console.print("\n[dim cyan]── LoRa Analysis ──[/dim cyan]")
            console.print("  [bold]4[/bold]. Preset Comparison")
            console.print("  [bold]5[/bold]. Range Estimator")
            console.print("  [bold]6[/bold]. Time-on-Air Calculator")

            console.print("\n[dim cyan]── Hardware ──[/dim cyan]")
            console.print("  [bold]7[/bold]. Detect LoRa Radio")
            console.print("  [bold]8[/bold]. Check SPI/GPIO Status")
            console.print("  [bold]9[/bold]. Frequency Band Reference")

            console.print("\n  [bold]0[/bold]. Back")
            console.print("  [bold]m[/bold]. Main Menu")
            console.print()

            choice = Prompt.ask("Select option", default="0")

            if choice == "0":
                return
            elif choice.lower() == "m":
                self._return_to_main = True
                return
            elif choice == "1":
                self._link_budget_calculator()
            elif choice == "2":
                self._fspl_calculator()
            elif choice == "3":
                self._fresnel_calculator()
            elif choice == "4":
                self._preset_comparison()
            elif choice == "5":
                self._range_estimator()
            elif choice == "6":
                self._time_on_air()
            elif choice == "7":
                self._detect_radio()
            elif choice == "8":
                self._check_spi_gpio()
            elif choice == "9":
                self._frequency_reference()

    def _link_budget_calculator(self):
        """Interactive link budget calculator"""
        console.print("\n[bold cyan]── Link Budget Calculator ──[/bold cyan]\n")

        # Get parameters
        console.print("[dim]Enter transmitter parameters:[/dim]")
        tx_power = float(Prompt.ask("TX Power (dBm)", default="20"))
        tx_gain = float(Prompt.ask("TX Antenna Gain (dBi)", default="2.15"))
        tx_loss = float(Prompt.ask("TX Cable/Connector Loss (dB)", default="1"))

        console.print("\n[dim]Enter receiver parameters:[/dim]")
        rx_gain = float(Prompt.ask("RX Antenna Gain (dBi)", default="2.15"))
        rx_loss = float(Prompt.ask("RX Cable/Connector Loss (dB)", default="1"))
        rx_sensitivity = float(Prompt.ask("RX Sensitivity (dBm)", default="-126"))

        console.print("\n[dim]Enter path parameters:[/dim]")
        frequency = float(Prompt.ask("Frequency (MHz)", default="915"))
        distance = float(Prompt.ask("Distance (km)", default="10"))

        # Calculate FSPL
        fspl = self.calculate_fspl(distance * 1000, frequency * 1e6)

        # Calculate link budget
        eirp = tx_power + tx_gain - tx_loss
        path_loss = fspl
        rx_power = eirp - path_loss + rx_gain - rx_loss
        margin = rx_power - rx_sensitivity

        # Display results
        console.print("\n")
        table = Table(title="Link Budget Analysis", show_header=True)
        table.add_column("Parameter", style="cyan")
        table.add_column("Value", justify="right")
        table.add_column("Unit")

        table.add_row("TX Power", f"{tx_power:.1f}", "dBm")
        table.add_row("TX Antenna Gain", f"{tx_gain:.1f}", "dBi")
        table.add_row("TX Losses", f"-{tx_loss:.1f}", "dB")
        table.add_row("[bold]EIRP[/bold]", f"[bold]{eirp:.1f}[/bold]", "dBm")
        table.add_row("", "", "")
        table.add_row("Free Space Path Loss", f"-{fspl:.1f}", "dB")
        table.add_row("", "", "")
        table.add_row("RX Antenna Gain", f"{rx_gain:.1f}", "dBi")
        table.add_row("RX Losses", f"-{rx_loss:.1f}", "dB")
        table.add_row("[bold]RX Power[/bold]", f"[bold]{rx_power:.1f}[/bold]", "dBm")
        table.add_row("RX Sensitivity", f"{rx_sensitivity:.1f}", "dBm")
        table.add_row("", "", "")

        if margin > 10:
            margin_color = "green"
            status = "Excellent"
        elif margin > 5:
            margin_color = "green"
            status = "Good"
        elif margin > 0:
            margin_color = "yellow"
            status = "Marginal"
        else:
            margin_color = "red"
            status = "Link Failure"

        table.add_row(
            f"[bold]Link Margin[/bold]",
            f"[{margin_color}][bold]{margin:.1f}[/bold][/{margin_color}]",
            f"dB ({status})"
        )

        console.print(table)

        # Calculate max range
        max_fspl = eirp + rx_gain - rx_loss - rx_sensitivity
        max_distance = self.distance_from_fspl(max_fspl, frequency * 1e6)
        console.print(f"\n[cyan]Maximum theoretical range: {max_distance/1000:.1f} km[/cyan]")

        input("\nPress Enter to continue...")

    def _fspl_calculator(self):
        """Free Space Path Loss calculator"""
        console.print("\n[bold cyan]── Free Space Path Loss (FSPL) ──[/bold cyan]\n")

        console.print("[dim]FSPL = 20*log10(d) + 20*log10(f) + 20*log10(4*pi/c)[/dim]\n")

        frequency = float(Prompt.ask("Frequency (MHz)", default="915"))
        distance = float(Prompt.ask("Distance (km)", default="10"))

        fspl = self.calculate_fspl(distance * 1000, frequency * 1e6)

        console.print(f"\n[green]FSPL at {distance} km, {frequency} MHz: {fspl:.2f} dB[/green]")

        # Show table at various distances
        console.print("\n[cyan]FSPL at various distances:[/cyan]")
        table = Table(show_header=True)
        table.add_column("Distance", justify="right")
        table.add_column("FSPL (dB)", justify="right")

        for d in [0.1, 0.5, 1, 2, 5, 10, 20, 50, 100]:
            loss = self.calculate_fspl(d * 1000, frequency * 1e6)
            table.add_row(f"{d} km", f"{loss:.1f}")

        console.print(table)

        input("\nPress Enter to continue...")

    def _fresnel_calculator(self):
        """Fresnel zone calculator"""
        console.print("\n[bold cyan]── Fresnel Zone Calculator ──[/bold cyan]\n")

        console.print("[dim]The first Fresnel zone should be 60% clear for reliable links[/dim]\n")

        frequency = float(Prompt.ask("Frequency (MHz)", default="915"))
        distance = float(Prompt.ask("Total path distance (km)", default="10"))

        # Calculate at midpoint (worst case)
        d1 = distance / 2  # km
        d2 = distance / 2  # km
        wavelength = 299792458 / (frequency * 1e6)  # meters

        # First Fresnel zone radius
        # r = sqrt((n * lambda * d1 * d2) / (d1 + d2))
        d1_m = d1 * 1000
        d2_m = d2 * 1000
        r1 = math.sqrt((wavelength * d1_m * d2_m) / (d1_m + d2_m))
        clearance_60 = r1 * 0.6

        console.print(f"\n[cyan]At {frequency} MHz over {distance} km:[/cyan]")
        console.print(f"  First Fresnel zone radius (midpoint): {r1:.1f} m")
        console.print(f"  Required clearance (60%): {clearance_60:.1f} m")

        # Show table for various positions
        console.print("\n[cyan]Fresnel zone radius along path:[/cyan]")
        table = Table(show_header=True)
        table.add_column("Position", justify="right")
        table.add_column("Radius (m)", justify="right")
        table.add_column("60% Clearance (m)", justify="right")

        for pct in [10, 20, 30, 40, 50]:
            d1_m = (pct / 100) * distance * 1000
            d2_m = (1 - pct / 100) * distance * 1000
            r = math.sqrt((wavelength * d1_m * d2_m) / (d1_m + d2_m))
            table.add_row(f"{pct}%", f"{r:.1f}", f"{r * 0.6:.1f}")

        console.print(table)

        input("\nPress Enter to continue...")

    def _preset_comparison(self):
        """Compare LoRa presets"""
        console.print("\n[bold cyan]── LoRa Preset Comparison ──[/bold cyan]\n")

        table = Table(title="Meshtastic LoRa Presets", show_header=True)
        table.add_column("Preset", style="cyan")
        table.add_column("BW (kHz)", justify="right")
        table.add_column("SF", justify="center")
        table.add_column("Data Rate", justify="right")
        table.add_column("Sensitivity", justify="right")
        table.add_column("Range (LOS)", justify="right")
        table.add_column("Range (Urban)", justify="right")

        for name, preset in LORA_PRESETS.items():
            table.add_row(
                preset.name,
                f"{preset.bandwidth / 1000:.0f}",
                str(preset.spreading_factor),
                f"{preset.data_rate:.0f} bps",
                f"{preset.sensitivity:.0f} dBm",
                f"{preset.range_los:.0f} km",
                f"{preset.range_urban:.0f} km"
            )

        console.print(table)

        console.print("\n[dim]LOS = Line of Sight (ideal conditions)[/dim]")
        console.print("[dim]Urban = Typical urban environment with obstacles[/dim]")

        input("\nPress Enter to continue...")

    def _range_estimator(self):
        """Estimate range for given parameters"""
        console.print("\n[bold cyan]── Range Estimator ──[/bold cyan]\n")

        # Select preset
        console.print("[cyan]Select modem preset:[/cyan]")
        for i, name in enumerate(LORA_PRESETS.keys(), 1):
            console.print(f"  {i}. {name}")

        choice = Prompt.ask("Select preset", default="6")  # LONG_FAST
        try:
            preset_name = list(LORA_PRESETS.keys())[int(choice) - 1]
            preset = LORA_PRESETS[preset_name]
        except (ValueError, IndexError):
            preset_name = 'LONG_FAST'
            preset = LORA_PRESETS['LONG_FAST']

        console.print(f"\n[cyan]Using preset: {preset_name}[/cyan]")
        console.print(f"  Sensitivity: {preset.sensitivity} dBm")

        # Get parameters
        tx_power = float(Prompt.ask("TX Power (dBm)", default="20"))
        tx_gain = float(Prompt.ask("TX Antenna Gain (dBi)", default="2.15"))
        rx_gain = float(Prompt.ask("RX Antenna Gain (dBi)", default="2.15"))
        frequency = float(Prompt.ask("Frequency (MHz)", default="915"))
        margin = float(Prompt.ask("Desired link margin (dB)", default="10"))

        # Calculate max FSPL
        max_fspl = tx_power + tx_gain + rx_gain - preset.sensitivity - margin

        # Calculate max distance
        max_distance = self.distance_from_fspl(max_fspl, frequency * 1e6)

        console.print(f"\n[green]Estimated maximum range:[/green]")
        console.print(f"  Line of sight: {max_distance/1000:.1f} km")
        console.print(f"  With obstacles: {max_distance/1000 * 0.25:.1f} km (estimated)")
        console.print(f"  Urban: {max_distance/1000 * 0.1:.1f} km (estimated)")

        input("\nPress Enter to continue...")

    def _time_on_air(self):
        """Calculate LoRa time-on-air"""
        console.print("\n[bold cyan]── Time-on-Air Calculator ──[/bold cyan]\n")

        # Select preset or manual
        console.print("[cyan]Select modem preset:[/cyan]")
        for i, name in enumerate(LORA_PRESETS.keys(), 1):
            console.print(f"  {i}. {name}")
        console.print("  0. Manual entry")

        choice = Prompt.ask("Select", default="6")

        if choice == "0":
            bw = int(Prompt.ask("Bandwidth (Hz)", default="125000"))
            sf = int(Prompt.ask("Spreading Factor (7-12)", default="11"))
            cr = Prompt.ask("Coding Rate (4/5, 4/6, 4/7, 4/8)", default="4/5")
        else:
            try:
                preset_name = list(LORA_PRESETS.keys())[int(choice) - 1]
                preset = LORA_PRESETS[preset_name]
                bw = preset.bandwidth
                sf = preset.spreading_factor
                cr = preset.coding_rate
            except (ValueError, IndexError):
                preset = LORA_PRESETS['LONG_FAST']
                bw = preset.bandwidth
                sf = preset.spreading_factor
                cr = preset.coding_rate

        payload = int(Prompt.ask("Payload size (bytes)", default="32"))

        # Calculate time-on-air (simplified)
        # Symbol time = 2^SF / BW
        t_sym = (2 ** sf) / bw

        # Preamble symbols (Meshtastic uses 8)
        n_preamble = 8
        t_preamble = (n_preamble + 4.25) * t_sym

        # Payload symbols (simplified)
        # Parse coding rate (format: "4/5", "4/6", etc.)
        cr_parts = cr.split('/')
        if len(cr_parts) >= 2 and cr_parts[1].isdigit():
            cr_val = int(cr_parts[1]) - 4
        else:
            cr_val = 1  # Default to 4/5 coding rate if invalid format
        n_payload = 8 + max(math.ceil((8 * payload - 4 * sf + 28) / (4 * sf)) * (cr_val + 4), 0)
        t_payload = n_payload * t_sym

        t_total = (t_preamble + t_payload) * 1000  # ms

        console.print(f"\n[cyan]Time-on-Air Analysis:[/cyan]")
        console.print(f"  Symbol time: {t_sym * 1000:.2f} ms")
        console.print(f"  Preamble time: {t_preamble * 1000:.2f} ms")
        console.print(f"  Payload time: {t_payload * 1000:.2f} ms")
        console.print(f"\n[green]Total time-on-air: {t_total:.1f} ms[/green]")

        # Data rate
        data_rate = (payload * 8) / (t_total / 1000)
        console.print(f"  Effective data rate: {data_rate:.0f} bps")

        input("\nPress Enter to continue...")

    def _detect_radio(self):
        """Detect LoRa radio hardware"""
        console.print("\n[bold cyan]── LoRa Radio Detection ──[/bold cyan]\n")

        found_devices = []

        # Check SPI devices
        console.print("[cyan]Checking SPI devices...[/cyan]")
        spi_devices = list(Path('/dev').glob('spidev*'))
        for dev in spi_devices:
            console.print(f"  Found: {dev}")
            found_devices.append(str(dev))

        # Check for common LoRa devices in /sys
        console.print("\n[cyan]Checking for LoRa drivers...[/cyan]")
        lora_paths = [
            '/sys/bus/spi/drivers/sx127x',
            '/sys/bus/spi/drivers/sx126x',
            '/sys/bus/spi/drivers/sx128x',
            '/sys/bus/spi/drivers/lora',
        ]
        for path in lora_paths:
            if Path(path).exists():
                console.print(f"  [green]Found: {path}[/green]")
                found_devices.append(path)

        # Check dmesg for LoRa
        console.print("\n[cyan]Checking kernel messages...[/cyan]")
        try:
            result = subprocess.run(
                ['dmesg'],
                capture_output=True, text=True, timeout=10
            )
            lora_mentions = [l for l in result.stdout.split('\n') if 'lora' in l.lower() or 'sx12' in l.lower()]
            for line in lora_mentions[:5]:
                console.print(f"  {line}")
        except Exception:
            pass

        # Check meshtasticd config
        console.print("\n[cyan]Checking meshtasticd configuration...[/cyan]")
        config_paths = [
            Path('/etc/meshtasticd/config.yaml'),
            Path('/etc/meshtasticd/config.d'),
        ]
        for path in config_paths:
            if path.exists():
                console.print(f"  [green]Found: {path}[/green]")

        if found_devices:
            console.print(f"\n[green]Found {len(found_devices)} potential LoRa device(s)[/green]")
        else:
            console.print("\n[yellow]No LoRa radio devices detected[/yellow]")
            console.print("[dim]Ensure SPI is enabled and radio HAT is connected[/dim]")

        input("\nPress Enter to continue...")

    def _check_spi_gpio(self):
        """Check SPI and GPIO status"""
        console.print("\n[bold cyan]── SPI/GPIO Status ──[/bold cyan]\n")

        # Check if SPI is enabled
        console.print("[cyan]SPI Status:[/cyan]")
        spi_enabled = Path('/dev/spidev0.0').exists() or Path('/dev/spidev0.1').exists()
        if spi_enabled:
            console.print("  [green]SPI is enabled[/green]")
            for dev in Path('/dev').glob('spidev*'):
                console.print(f"    {dev}")
        else:
            console.print("  [red]SPI is not enabled[/red]")
            console.print("  [dim]Enable with: sudo raspi-config -> Interface Options -> SPI[/dim]")

        # Check I2C
        console.print("\n[cyan]I2C Status:[/cyan]")
        i2c_enabled = Path('/dev/i2c-1').exists()
        if i2c_enabled:
            console.print("  [green]I2C is enabled[/green]")
            for dev in Path('/dev').glob('i2c-*'):
                console.print(f"    {dev}")
        else:
            console.print("  [yellow]I2C is not enabled[/yellow]")

        # Check GPIO
        console.print("\n[cyan]GPIO Status:[/cyan]")
        gpio_path = Path('/sys/class/gpio')
        if gpio_path.exists():
            console.print("  [green]GPIO sysfs available[/green]")
            exported = list(gpio_path.glob('gpio[0-9]*'))
            if exported:
                for gpio in exported[:10]:
                    console.print(f"    {gpio.name}")
        else:
            console.print("  [yellow]GPIO sysfs not available[/yellow]")

        # Check for gpiod
        console.print("\n[cyan]GPIO Chip Devices:[/cyan]")
        for dev in Path('/dev').glob('gpiochip*'):
            console.print(f"  {dev}")

        input("\nPress Enter to continue...")

    def _frequency_reference(self):
        """Show frequency band reference"""
        console.print("\n[bold cyan]── Frequency Band Reference ──[/bold cyan]\n")

        table = Table(title="Meshtastic Regional Frequency Bands", show_header=True)
        table.add_column("Region", style="cyan")
        table.add_column("Name")
        table.add_column("Frequency", justify="right")
        table.add_column("Max Power", justify="right")

        for code, band in FREQUENCY_BANDS.items():
            table.add_row(
                code,
                band['name'],
                f"{band['freq']:.1f} MHz",
                f"{band['power']} dBm"
            )

        console.print(table)

        console.print("\n[dim]Note: Always verify local regulations before transmitting.[/dim]")
        console.print("[dim]Power limits may vary by specific frequency and duty cycle.[/dim]")

        input("\nPress Enter to continue...")

    @staticmethod
    def calculate_fspl(distance_m: float, frequency_hz: float) -> float:
        """Calculate Free Space Path Loss in dB"""
        if distance_m <= 0 or frequency_hz <= 0:
            return 0
        c = 299792458  # speed of light
        fspl = 20 * math.log10(distance_m) + 20 * math.log10(frequency_hz) + 20 * math.log10(4 * math.pi / c)
        return fspl

    @staticmethod
    def distance_from_fspl(fspl_db: float, frequency_hz: float) -> float:
        """Calculate distance from FSPL in meters"""
        if frequency_hz <= 0:
            return 0
        c = 299792458
        # FSPL = 20*log10(d) + 20*log10(f) + 20*log10(4*pi/c)
        # Solve for d
        term = fspl_db - 20 * math.log10(frequency_hz) - 20 * math.log10(4 * math.pi / c)
        distance = 10 ** (term / 20)
        return distance
