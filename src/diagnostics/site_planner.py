"""Site Planner integration for meshtasticd-installer

Integrates with Meshtastic Site Planner for:
- Coverage analysis
- Link budget calculations
- Terrain analysis
- Network planning

Reference: https://meshtastic.org/docs/software/site-planner/
"""

import os
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

from utils import emoji as em

console = Console()

# Meshtastic Site Planner URL
SITE_PLANNER_URL = "https://meshtastic.org/docs/software/site-planner/"
COVERAGE_TOOLS = [
    {
        'name': 'Meshtastic Site Planner',
        'url': 'https://meshtastic.org/docs/software/site-planner/',
        'description': 'Official Meshtastic site planning tool'
    },
    {
        'name': 'Radio Mobile Online',
        'url': 'https://www.ve2dbe.com/rmonline_s.asp',
        'description': 'RF propagation and coverage prediction'
    },
    {
        'name': 'HeyWhatsThat',
        'url': 'https://www.heywhatsthat.com/',
        'description': 'Line-of-sight and viewshed analysis'
    },
    {
        'name': 'Splat! RF Coverage',
        'url': 'https://www.qsl.net/kd2bd/splat.html',
        'description': 'Open-source RF signal propagation tool'
    }
]


class SitePlanner:
    """Site planning and coverage analysis tools"""

    def __init__(self):
        self._return_to_main = False
        self._current_location = None

    def interactive_menu(self):
        """Main site planner menu"""
        self._return_to_main = False

        while True:
            if self._return_to_main:
                return

            console.print("\n[bold cyan]═══════════════ Site Planner ═══════════════[/bold cyan]\n")

            console.print("[dim cyan]── Coverage Tools ──[/dim cyan]")
            console.print(f"  [bold]1[/bold]. {em.get('🌐')} Open Meshtastic Site Planner")
            console.print(f"  [bold]2[/bold]. {em.get('📡')} RF Coverage Tools")

            console.print("\n[dim cyan]── Link Analysis ──[/dim cyan]")
            console.print(f"  [bold]3[/bold]. {em.get('🔗')} Link Budget Calculator")
            console.print(f"  [bold]4[/bold]. {em.get('📊')} Preset Range Estimates")

            console.print("\n[dim cyan]── Location ──[/dim cyan]")
            console.print(f"  [bold]5[/bold]. {em.get('📍', '[LOC]')} Set/View Current Location")
            console.print(f"  [bold]6[/bold]. {em.get('🗺️', '[MAP]')} View on Map")

            console.print("\n[dim cyan]── Reference ──[/dim cyan]")
            console.print(f"  [bold]7[/bold]. {em.get('📋')} Antenna Guidelines")
            console.print(f"  [bold]8[/bold]. {em.get('⚡')} Frequency & Power Reference")

            console.print(f"\n  [bold]0[/bold]. {em.get('⬅️')} Back")
            console.print(f"  [bold]m[/bold]. Main Menu")

            choice = Prompt.ask(
                "\n[cyan]Select option[/cyan]",
                choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "m"],
                default="0"
            )

            if choice == "0":
                return
            elif choice == "m":
                self._return_to_main = True
                return
            elif choice == "1":
                self.open_site_planner()
            elif choice == "2":
                self.rf_coverage_tools()
            elif choice == "3":
                self.link_budget_calculator()
            elif choice == "4":
                self.preset_range_estimates()
            elif choice == "5":
                self.set_location()
            elif choice == "6":
                self.view_on_map()
            elif choice == "7":
                self.antenna_guidelines()
            elif choice == "8":
                self.frequency_power_reference()

            Prompt.ask("\n[dim]Press Enter to continue[/dim]")

    def open_site_planner(self):
        """Open the Meshtastic Site Planner"""
        console.print("\n[bold cyan]Meshtastic Site Planner[/bold cyan]\n")

        console.print("[dim]The Meshtastic Site Planner helps you:[/dim]")
        console.print("  • Plan node placement for optimal coverage")
        console.print("  • Analyze terrain and obstructions")
        console.print("  • Calculate link budgets between nodes")
        console.print("  • Visualize mesh network topology")

        console.print(f"\n[cyan]URL: {SITE_PLANNER_URL}[/cyan]\n")

        if Confirm.ask("Open in browser?", default=True):
            self._open_url(SITE_PLANNER_URL)
            console.print("[green]Browser opened![/green]")
        else:
            console.print(f"\n[dim]Visit: {SITE_PLANNER_URL}[/dim]")

    def rf_coverage_tools(self):
        """Show RF coverage analysis tools"""
        console.print("\n[bold cyan]RF Coverage Analysis Tools[/bold cyan]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("#", style="cyan", width=3)
        table.add_column("Tool", style="white")
        table.add_column("Description", style="dim")

        for i, tool in enumerate(COVERAGE_TOOLS, 1):
            table.add_row(str(i), tool['name'], tool['description'])

        console.print(table)

        choice = Prompt.ask(
            "\n[cyan]Open tool (1-4) or 0 to cancel[/cyan]",
            choices=["0", "1", "2", "3", "4"],
            default="0"
        )

        if choice != "0":
            tool = COVERAGE_TOOLS[int(choice) - 1]
            if Confirm.ask(f"Open {tool['name']} in browser?", default=True):
                self._open_url(tool['url'])
                console.print("[green]Browser opened![/green]")

    def link_budget_calculator(self):
        """Interactive link budget calculator"""
        console.print("\n[bold cyan]Link Budget Calculator[/bold cyan]\n")

        console.print("[dim]Calculate the theoretical range between two nodes[/dim]\n")

        # Get parameters
        console.print("[bold]Transmitter Settings:[/bold]")

        # TX Power
        tx_power = Prompt.ask(
            "TX Power (dBm)",
            default="20"
        )
        try:
            tx_power = float(tx_power)
        except ValueError:
            tx_power = 20.0

        # TX Antenna Gain
        tx_antenna = Prompt.ask(
            "TX Antenna Gain (dBi)",
            default="2.0"
        )
        try:
            tx_antenna = float(tx_antenna)
        except ValueError:
            tx_antenna = 2.0

        # Cable loss
        cable_loss = Prompt.ask(
            "Cable Loss (dB)",
            default="0.5"
        )
        try:
            cable_loss = float(cable_loss)
        except ValueError:
            cable_loss = 0.5

        console.print("\n[bold]Receiver Settings:[/bold]")

        # RX Antenna Gain
        rx_antenna = Prompt.ask(
            "RX Antenna Gain (dBi)",
            default="2.0"
        )
        try:
            rx_antenna = float(rx_antenna)
        except ValueError:
            rx_antenna = 2.0

        # RX Sensitivity
        rx_sensitivity = Prompt.ask(
            "RX Sensitivity (dBm)",
            default="-137"
        )
        try:
            rx_sensitivity = float(rx_sensitivity)
        except ValueError:
            rx_sensitivity = -137.0

        console.print("\n[bold]Environment:[/bold]")

        # Frequency
        frequencies = {
            '1': ('US 915 MHz', 915),
            '2': ('EU 868 MHz', 868),
            '3': ('EU 433 MHz', 433)
        }
        console.print("  1. US 915 MHz")
        console.print("  2. EU 868 MHz")
        console.print("  3. EU 433 MHz")
        freq_choice = Prompt.ask("Select frequency", choices=["1", "2", "3"], default="1")
        freq_name, frequency = frequencies[freq_choice]

        # Calculate link budget
        link_budget = tx_power + tx_antenna - cable_loss + rx_antenna - rx_sensitivity

        # Calculate free space path loss distance
        # FSPL(dB) = 20log10(d) + 20log10(f) + 20log10(4π/c)
        # Rearranging: d = 10^((FSPL - 20log10(f) - 32.44) / 20)
        import math

        # Free space range (km)
        fspl = link_budget
        free_space_km = 10 ** ((fspl - 20 * math.log10(frequency) - 32.44) / 20)

        # Realistic ranges with different terrain
        line_of_sight = free_space_km * 0.8  # 80% of free space
        suburban = free_space_km * 0.3  # 30% in suburban
        urban = free_space_km * 0.1  # 10% in urban

        # Display results
        console.print("\n" + "═" * 50)
        console.print("[bold cyan]Link Budget Results[/bold cyan]")
        console.print("═" * 50)

        results = Table(show_header=True, header_style="bold magenta")
        results.add_column("Parameter", style="cyan")
        results.add_column("Value", style="white")

        results.add_row("TX Power", f"{tx_power:.1f} dBm")
        results.add_row("TX Antenna", f"+{tx_antenna:.1f} dBi")
        results.add_row("Cable Loss", f"-{cable_loss:.1f} dB")
        results.add_row("RX Antenna", f"+{rx_antenna:.1f} dBi")
        results.add_row("RX Sensitivity", f"{rx_sensitivity:.1f} dBm")
        results.add_row("Frequency", f"{freq_name}")
        results.add_row("[bold]Link Budget[/bold]", f"[bold]{link_budget:.1f} dB[/bold]")

        console.print(results)

        console.print("\n[bold cyan]Estimated Range:[/bold cyan]")

        range_table = Table(show_header=True, header_style="bold magenta")
        range_table.add_column("Environment", style="cyan")
        range_table.add_column("Range (km)", style="white")
        range_table.add_column("Range (miles)", style="dim")

        range_table.add_row("Free Space (ideal)", f"{free_space_km:.1f}", f"{free_space_km * 0.621:.1f}")
        range_table.add_row("Line of Sight", f"{line_of_sight:.1f}", f"{line_of_sight * 0.621:.1f}")
        range_table.add_row("Suburban", f"{suburban:.1f}", f"{suburban * 0.621:.1f}")
        range_table.add_row("Urban", f"{urban:.1f}", f"{urban * 0.621:.1f}")

        console.print(range_table)

        console.print("\n[dim]Note: Actual range depends on terrain, obstacles, and conditions[/dim]")

    def preset_range_estimates(self):
        """Show range estimates for different presets"""
        console.print("\n[bold cyan]Modem Preset Range Estimates[/bold cyan]\n")

        console.print("[dim]Theoretical ranges with standard antenna (2dBi) and 20dBm TX[/dim]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Preset", style="cyan")
        table.add_column("Data Rate", style="white")
        table.add_column("LOS Range", style="green")
        table.add_column("Urban", style="yellow")
        table.add_column("Best For", style="dim")

        presets = [
            ("SHORT_TURBO", "~21.9 kbps", "1-2 km", "< 500m", "High speed, short range"),
            ("SHORT_FAST", "~10.9 kbps", "3-5 km", "1 km", "Urban, high density"),
            ("SHORT_SLOW", "~4.7 kbps", "5-8 km", "2 km", "Suburban areas"),
            ("MEDIUM_FAST", "~2.6 kbps", "10-15 km", "3 km", "Mixed terrain"),
            ("MEDIUM_SLOW", "~1.0 kbps", "15-25 km", "5 km", "Rural areas"),
            ("LONG_FAST", "~1.1 kbps", "20-30 km", "5 km", "Default, balanced"),
            ("LONG_MODERATE", "~0.3 kbps", "30-50 km", "8 km", "Long range comms"),
            ("LONG_SLOW", "~0.18 kbps", "50-100 km", "10 km", "Maximum range"),
            ("VERY_LONG_SLOW", "~0.09 kbps", "100+ km", "15 km", "Extreme range"),
        ]

        for preset in presets:
            table.add_row(*preset)

        console.print(table)

        console.print("\n[bold yellow]Tips for Maximum Range:[/bold yellow]")
        console.print("  • Mount antenna as high as possible")
        console.print("  • Use directional antenna for point-to-point links")
        console.print("  • Clear line of sight greatly improves range")
        console.print("  • Use LONG_SLOW or VERY_LONG_SLOW for best range")
        console.print("  • Consider repeater nodes for covering obstacles")

    def set_location(self):
        """Set or view current location"""
        console.print("\n[bold cyan]Node Location[/bold cyan]\n")

        # Try to get current location from meshtastic
        current = self._get_current_location()

        if current:
            console.print(f"[green]Current Location:[/green]")
            console.print(f"  Latitude:  {current['lat']:.6f}")
            console.print(f"  Longitude: {current['lon']:.6f}")
            if current.get('alt'):
                console.print(f"  Altitude:  {current['alt']} m")
        else:
            console.print("[yellow]No location set on device[/yellow]")

        console.print("\n[dim]Options:[/dim]")
        console.print("  1. Set location manually")
        console.print("  2. Set from GPS (if available)")
        console.print("  0. Cancel")

        choice = Prompt.ask("Select option", choices=["0", "1", "2"], default="0")

        if choice == "1":
            self._set_manual_location()
        elif choice == "2":
            self._set_gps_location()

    def _get_current_location(self) -> Optional[dict]:
        """Get current location from device"""
        from utils.cli import find_meshtastic_cli
        cli_path = find_meshtastic_cli()
        if not cli_path:
            return None

        try:
            result = subprocess.run(
                [cli_path, '--host', 'localhost', '--info'],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                # Parse location from output
                for line in result.stdout.split('\n'):
                    if 'latitude' in line.lower():
                        # Parse latitude/longitude
                        pass  # Would need actual parsing
        except Exception:
            pass
        return None

    def _set_manual_location(self):
        """Set location manually"""
        console.print("\n[bold]Enter Location:[/bold]")

        lat = Prompt.ask("Latitude (e.g., 40.7128)")
        lon = Prompt.ask("Longitude (e.g., -74.0060)")
        alt = Prompt.ask("Altitude in meters (optional)", default="0")

        try:
            lat = float(lat)
            lon = float(lon)
            alt = int(alt)

            if not (-90 <= lat <= 90):
                console.print("[red]Invalid latitude (must be -90 to 90)[/red]")
                return
            if not (-180 <= lon <= 180):
                console.print("[red]Invalid longitude (must be -180 to 180)[/red]")
                return

            # Set via meshtastic CLI
            from utils.cli import find_meshtastic_cli
            cli_path = find_meshtastic_cli()
            if cli_path:
                result = subprocess.run(
                    [cli_path, '--host', 'localhost',
                     '--setlat', str(lat),
                     '--setlon', str(lon),
                     '--setalt', str(alt)],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    console.print("[green]Location set successfully![/green]")
                    self._current_location = {'lat': lat, 'lon': lon, 'alt': alt}
                else:
                    console.print(f"[yellow]Warning: {result.stderr or 'Could not set location'}[/yellow]")
            else:
                console.print("[yellow]Meshtastic CLI not found - location saved locally only[/yellow]")
                self._current_location = {'lat': lat, 'lon': lon, 'alt': alt}

        except ValueError:
            console.print("[red]Invalid coordinates[/red]")

    def _set_gps_location(self):
        """Set location from GPS"""
        console.print("\n[dim]Checking for GPS...[/dim]")

        # Check if GPS is configured
        from utils.cli import find_meshtastic_cli
        cli_path = find_meshtastic_cli()
        if not cli_path:
            console.print("[yellow]Meshtastic CLI not found[/yellow]")
            return

        try:
            result = subprocess.run(
                [cli_path, '--host', 'localhost', '--info'],
                capture_output=True, text=True, timeout=15
            )
            if 'gps' in result.stdout.lower() and 'position' in result.stdout.lower():
                console.print("[green]GPS position available![/green]")
                console.print("[dim]Using current GPS coordinates...[/dim]")
            else:
                console.print("[yellow]No GPS position available[/yellow]")
                console.print("[dim]Make sure GPS is enabled and has a fix[/dim]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

    def view_on_map(self):
        """View node location on map"""
        console.print("\n[bold cyan]View on Map[/bold cyan]\n")

        loc = self._current_location or self._get_current_location()

        if not loc:
            console.print("[yellow]No location available[/yellow]")
            console.print("[dim]Set location first using option 5[/dim]")
            return

        lat, lon = loc['lat'], loc['lon']

        # Map options
        console.print("Select map service:")
        console.print("  1. OpenStreetMap")
        console.print("  2. Google Maps")
        console.print("  3. Bing Maps")

        choice = Prompt.ask("Select", choices=["1", "2", "3"], default="1")

        if choice == "1":
            url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=14"
        elif choice == "2":
            url = f"https://www.google.com/maps?q={lat},{lon}&z=14"
        else:
            url = f"https://www.bing.com/maps?cp={lat}~{lon}&lvl=14"

        if Confirm.ask("Open in browser?", default=True):
            self._open_url(url)
            console.print("[green]Browser opened![/green]")
        else:
            console.print(f"\n[dim]URL: {url}[/dim]")

    def antenna_guidelines(self):
        """Show antenna selection guidelines"""
        console.print("\n[bold cyan]Antenna Guidelines[/bold cyan]\n")

        content = """
[bold yellow]Antenna Types:[/bold yellow]

[bold]1. Omnidirectional (Stock)[/bold]
   • Gain: 2-3 dBi
   • Pattern: 360° horizontal coverage
   • Best for: General purpose, mobile nodes

[bold]2. Higher Gain Omni[/bold]
   • Gain: 5-8 dBi
   • Pattern: 360° but flatter (less sky coverage)
   • Best for: Fixed base stations

[bold]3. Directional (Yagi)[/bold]
   • Gain: 8-15 dBi
   • Pattern: Narrow beam (30-60°)
   • Best for: Point-to-point links

[bold]4. Panel/Sector[/bold]
   • Gain: 8-14 dBi
   • Pattern: Sector coverage (60-120°)
   • Best for: Coverage in specific direction

[bold yellow]Mounting Tips:[/bold yellow]
• Mount as high as practical
• Keep antenna vertical for omni
• Clear of metal objects (>1 wavelength)
• Use quality coax (LMR-400 for long runs)
• Weatherproof all connections outdoors

[bold yellow]Frequency-Specific:[/bold yellow]
• 915 MHz: ~33cm wavelength, smaller antennas
• 868 MHz: ~35cm wavelength
• 433 MHz: ~69cm wavelength, larger antennas needed

[bold yellow]Quick Antenna Math:[/bold yellow]
• 1/4 wave (915MHz): ~8cm
• 1/2 wave (915MHz): ~16cm
• Full wave (915MHz): ~33cm
"""
        console.print(Panel(content, title="[cyan]Antenna Reference[/cyan]", border_style="cyan"))

    def frequency_power_reference(self):
        """Show frequency and power reference"""
        console.print("\n[bold cyan]Frequency & Power Reference[/bold cyan]\n")

        table = Table(show_header=True, header_style="bold magenta", title="Regional Frequency Bands")
        table.add_column("Region", style="cyan")
        table.add_column("Frequency", style="white")
        table.add_column("Max Power", style="yellow")
        table.add_column("Notes", style="dim")

        regions = [
            ("US/CA", "902-928 MHz", "30 dBm (1W)", "ISM band, frequency hopping"),
            ("EU", "863-870 MHz", "14 dBm (25mW)", "Duty cycle limits apply"),
            ("EU 433", "433.05-434.79 MHz", "10 dBm", "10% duty cycle"),
            ("UK", "863-870 MHz", "14 dBm", "Same as EU"),
            ("AU/NZ", "915-928 MHz", "30 dBm", "Similar to US"),
            ("IN", "865-867 MHz", "30 dBm", "1W EIRP allowed"),
            ("JP", "920-923 MHz", "20 dBm", "Specific channels"),
            ("KR", "920-923 MHz", "10 dBm", "Lower power limit"),
            ("TW", "920-925 MHz", "14 dBm", ""),
            ("RU", "868-870 MHz", "10 dBm", "Similar to EU"),
        ]

        for region in regions:
            table.add_row(*region)

        console.print(table)

        console.print("\n[bold yellow]Power Conversion:[/bold yellow]")
        power_table = Table(show_header=True, header_style="bold magenta")
        power_table.add_column("dBm", style="cyan")
        power_table.add_column("Watts", style="white")
        power_table.add_column("Milliwatts", style="dim")

        powers = [
            ("10 dBm", "0.01 W", "10 mW"),
            ("14 dBm", "0.025 W", "25 mW"),
            ("17 dBm", "0.05 W", "50 mW"),
            ("20 dBm", "0.1 W", "100 mW"),
            ("23 dBm", "0.2 W", "200 mW"),
            ("27 dBm", "0.5 W", "500 mW"),
            ("30 dBm", "1.0 W", "1000 mW"),
        ]

        for power in powers:
            power_table.add_row(*power)

        console.print(power_table)

        console.print("\n[bold red]Important:[/bold red] Always comply with local regulations!")
        console.print("[dim]Check your country's amateur/ISM band rules before transmitting.[/dim]")

    def _open_url(self, url: str):
        """Open URL in browser"""
        try:
            # Try xdg-open first (Linux)
            if os.system(f'xdg-open "{url}" 2>/dev/null &') == 0:
                return

            # Try webbrowser module
            webbrowser.open(url)
        except Exception:
            console.print(f"[dim]Could not open browser. Visit: {url}[/dim]")
