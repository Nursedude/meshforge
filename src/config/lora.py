"""LoRa-specific configuration module"""

from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

console = Console()


class LoRaConfigurator:
    """Configure LoRa radio settings"""

    # LoRa regions and their frequencies
    REGIONS = {
        'US': {
            'name': 'United States',
            'frequency': '902-928 MHz',
            'channels': 104
        },
        'EU_433': {
            'name': 'Europe 433 MHz',
            'frequency': '433 MHz',
            'channels': 8
        },
        'EU_868': {
            'name': 'Europe 868 MHz',
            'frequency': '863-870 MHz',
            'channels': 8
        },
        'CN': {
            'name': 'China',
            'frequency': '470-510 MHz',
            'channels': 20
        },
        'JP': {
            'name': 'Japan',
            'frequency': '920-923 MHz',
            'channels': 10
        },
        'ANZ': {
            'name': 'Australia/New Zealand',
            'frequency': '915-928 MHz',
            'channels': 20
        },
        'KR': {
            'name': 'Korea',
            'frequency': '920-923 MHz',
            'channels': 8
        },
        'TW': {
            'name': 'Taiwan',
            'frequency': '920-925 MHz',
            'channels': 10
        },
        'RU': {
            'name': 'Russia',
            'frequency': '868-870 MHz',
            'channels': 8
        },
        'IN': {
            'name': 'India',
            'frequency': '865-867 MHz',
            'channels': 4
        },
    }

    # LoRa bandwidth options (kHz)
    BANDWIDTHS = [125, 250, 500]

    # LoRa spreading factors
    SPREADING_FACTORS = [7, 8, 9, 10, 11, 12]

    # Coding rates
    CODING_RATES = [5, 6, 7, 8]  # Represented as 4/5, 4/6, 4/7, 4/8

    # Official Meshtastic Modem Presets (ordered Fastest to Slowest)
    MODEM_PRESETS = {
        'SHORT_TURBO': {
            'name': 'Short Turbo',
            'bandwidth': 500,
            'spreading_factor': 7,
            'coding_rate': 8,
            'description': 'Fastest, highest bandwidth, lowest airtime',
            'use_case': 'Very high speed, very short range. Check local regulations!',
            'air_time': '~0.04s per message',
            'range': 'Very Short (<1 km)',
            'recommended_by': 'Speed-critical, may be illegal in some regions (500kHz BW)',
            'legal_warning': True
        },
        'SHORT_FAST': {
            'name': 'Short Fast',
            'bandwidth': 250,
            'spreading_factor': 7,
            'coding_rate': 8,
            'description': 'Very fast, short range',
            'use_case': 'High-density areas, rapid messaging',
            'air_time': '~0.08s per message',
            'range': 'Short (1-5 km)',
            'recommended_by': 'Urban/high-density deployments'
        },
        'SHORT_SLOW': {
            'name': 'Short Slow',
            'bandwidth': 125,
            'spreading_factor': 7,
            'coding_rate': 8,
            'description': 'Short range, more reliable than Short Fast',
            'use_case': 'Close-range reliable communication',
            'air_time': '~0.16s per message',
            'range': 'Short (1-5 km)',
            'recommended_by': 'Reliable short-range'
        },
        'MEDIUM_FAST': {
            'name': 'Medium Fast',
            'bandwidth': 250,
            'spreading_factor': 10,
            'coding_rate': 8,
            'description': 'Good balance of speed and range (~3.5kbps)',
            'use_case': 'Popular for community meshes, faster than LongFast',
            'air_time': '~0.65s per message',
            'range': 'Medium-Long (5-20 km)',
            'recommended_by': 'MtnMesh community standard'
        },
        'MEDIUM_SLOW': {
            'name': 'Medium Slow',
            'bandwidth': 125,
            'spreading_factor': 10,
            'coding_rate': 8,
            'description': 'Medium range, better reliability in congested areas',
            'use_case': 'Good balance of range and reliability',
            'air_time': '~1.3s per message',
            'range': 'Medium-Long (5-20 km)',
            'recommended_by': 'Alternative to MediumFast'
        },
        'LONG_FAST': {
            'name': 'Long Fast',
            'bandwidth': 250,
            'spreading_factor': 11,
            'coding_rate': 8,
            'description': 'Default preset - great range, ~1kbps',
            'use_case': 'Best for most deployments, good range with acceptable speed',
            'air_time': '~1.3s per message',
            'range': 'Very Long (10-30+ km)',
            'recommended_by': 'Default Meshtastic preset'
        },
        'LONG_MODERATE': {
            'name': 'Long Moderate',
            'bandwidth': 125,
            'spreading_factor': 11,
            'coding_rate': 8,
            'description': 'Longer range than Long Fast, slower',
            'use_case': 'Extended range when speed is not critical',
            'air_time': '~2.6s per message',
            'range': 'Maximum (15-40+ km)',
            'recommended_by': 'For extended range needs'
        },
        'LONG_SLOW': {
            'name': 'Long Slow',
            'bandwidth': 125,
            'spreading_factor': 12,
            'coding_rate': 8,
            'description': 'Very long range, slow speed',
            'use_case': 'Extreme range scenarios',
            'air_time': '~5.2s per message',
            'range': 'Extreme (20-50+ km)',
            'recommended_by': 'Long-range point-to-point'
        },
        'VERY_LONG_SLOW': {
            'name': 'Very Long Slow',
            'bandwidth': 62.5,
            'spreading_factor': 12,
            'coding_rate': 8,
            'description': 'Slowest, longest range - not recommended for meshes',
            'use_case': 'Experimental, extremely slow, poor mesh performance',
            'air_time': '~10.4s per message',
            'range': 'Experimental (30-60+ km)',
            'recommended_by': 'Experimental only - does not mesh well'
        }
    }

    def __init__(self, interface=None):
        self.interface = interface

    def show_regions(self):
        """Display available LoRa regions"""
        table = Table(title="LoRa Regions", show_header=True, header_style="bold magenta")
        table.add_column("Code", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Frequency", style="yellow")
        table.add_column("Channels", style="blue")

        for code, info in self.REGIONS.items():
            table.add_row(code, info['name'], info['frequency'], str(info['channels']))

        console.print(table)

    def configure_region(self):
        """Configure LoRa region"""
        console.print("\n[bold cyan]LoRa Region Configuration[/bold cyan]\n")

        self.show_regions()

        console.print("\n[yellow]Important:[/yellow] Select the region appropriate for your location.")
        console.print("Using the wrong region may be illegal and can cause interference.")

        region_codes = list(self.REGIONS.keys())
        region = Prompt.ask("\nSelect region", choices=region_codes, default="US")

        console.print(f"\n[green]Region set to: {self.REGIONS[region]['name']} ({self.REGIONS[region]['frequency']})[/green]")

        return region

    def configure_advanced(self):
        """Configure advanced LoRa parameters - Full Interactive Mode"""
        console.print("\n[bold cyan]═══ Advanced LoRa Configuration Wizard ═══[/bold cyan]\n")
        console.print("[bold yellow]⚠️  ADVANCED MODE - Full Manual Configuration[/bold yellow]")
        console.print("[dim]Reference: https://meshtastic.org/docs/configuration/radio/lora/[/dim]\n")

        console.print("This wizard allows you to configure ALL LoRa parameters manually.")
        console.print("[yellow]Warning:[/yellow] Incorrect settings can prevent network communication!")
        console.print("For most users, we recommend using preset-based templates instead.\n")

        if not Confirm.ask("Continue with advanced manual configuration?", default=False):
            console.print("[cyan]Tip: Use preset templates for easier configuration[/cyan]")
            return None

        config = {}

        # Step 1: Region Configuration
        console.print("\n[bold]Step 1/8: Region Configuration[/bold]")
        console.print("[yellow]REQUIRED - Device will not transmit without region set[/yellow]")
        config['region'] = 'US'  # Force US region as per requirements
        console.print(f"[green]✓ Region: US (915MHz ISM band)[/green]")

        # Step 2: Modem Configuration Method
        console.print("\n[bold]Step 2/8: Modem Configuration Method[/bold]")
        console.print("1. Use Modem Preset (recommended - pre-defined settings)")
        console.print("2. Manual Configuration (advanced - configure each parameter)")

        method = Prompt.ask("\nSelect configuration method", choices=["1", "2"], default="1")

        if method == "1":
            config['use_preset'] = True
            console.print("\n[cyan]Available Modem Presets:[/cyan]")
            console.print("1. LONG_FAST     - 250kHz BW, SF11 (~5-10km, ~1kbps) [DEFAULT]")
            console.print("2. MEDIUM_FAST   - 250kHz BW, SF10 (~3-7km, ~1.5kbps) [MtnMesh Standard]")
            console.print("3. SHORT_FAST    - 250kHz BW, SF7  (<1km, ~5kbps) [Urban/Dense]")
            console.print("4. LONG_SLOW     - 125kHz BW, SF12 (10-30km, ~0.3kbps) [Maximum Range]")
            console.print("5. VERY_LONG_SLOW - 125kHz BW, SF12 (15-40km) [Emergency/SAR]")

            preset_choice = Prompt.ask("\nSelect preset", choices=["1", "2", "3", "4", "5"], default="1")
            preset_map = {"1": "LONG_FAST", "2": "MEDIUM_FAST", "3": "SHORT_FAST",
                         "4": "LONG_SLOW", "5": "VERY_LONG_SLOW"}
            config['modem_preset'] = preset_map[preset_choice]
            console.print(f"[green]✓ Using preset: {config['modem_preset']}[/green]")
        else:
            config['use_preset'] = False

            # Manual bandwidth configuration
            console.print("\n[bold cyan]Bandwidth Configuration:[/bold cyan]")
            console.print("Bandwidth affects both speed and range:")
            console.print("  125 kHz: Longest range, slowest speed")
            console.print("  250 kHz: Good balance (recommended)")
            console.print("  500 kHz: Shortest range, fastest speed")
            console.print("[dim]Each doubling ~3dB less link budget[/dim]")
            bandwidth = Prompt.ask("\nSelect bandwidth (kHz)", choices=["125", "250", "500"], default="250")
            config['bandwidth'] = int(bandwidth)

            # Manual spreading factor configuration
            console.print("\n[bold cyan]Spreading Factor Configuration:[/bold cyan]")
            console.print("Spreading Factor affects range and speed:")
            console.print("  SF7:  Fastest, shortest range (<500m urban)")
            console.print("  SF10: Balanced (3-7km) [MtnMesh standard]")
            console.print("  SF11: Good range (5-10km) [Default]")
            console.print("  SF12: Maximum range (10-30km), slowest")
            console.print("[dim]Each step up +2.5dB link budget[/dim]")
            sf = Prompt.ask("\nSelect spreading factor",
                          choices=["7", "8", "9", "10", "11", "12"], default="11")
            config['spreading_factor'] = int(sf)

            # Manual coding rate configuration
            console.print("\n[bold cyan]Coding Rate Configuration:[/bold cyan]")
            console.print("Coding Rate (4/x) affects error correction:")
            console.print("  4/5: Least overhead, fastest")
            console.print("  4/8: Most overhead, most reliable (recommended)")
            console.print("[dim]Higher values = better error correction[/dim]")
            cr = Prompt.ask("\nSelect coding rate", choices=["5", "6", "7", "8"], default="8")
            config['coding_rate'] = int(cr)

        # Step 3: Transmit Power
        console.print("\n[bold]Step 3/8: Transmit Power[/bold]")
        console.print("TX Power affects range and battery life:")
        console.print("  Standard modules: 0-22 dBm (max 158mW)")
        console.print("  MeshAdv-Mini: 0-22 dBm (max 158mW)")
        console.print("  MeshAdv-Pi-Hat: 0-30 dBm (max 1W), up to 33dBm on 33S variants")
        console.print("[dim]Higher power = more range but more battery drain[/dim]")
        power = Prompt.ask("\nEnter transmit power (dBm)", default="22")
        try:
            power_val = int(power)
            if 0 <= power_val <= 33:
                config['tx_power'] = power_val
                console.print(f"[green]✓ TX Power: {power_val} dBm[/green]")
            else:
                console.print("[yellow]Value out of range. Using default: 22 dBm[/yellow]")
                config['tx_power'] = 22
        except ValueError:
            console.print("[yellow]Invalid input. Using default: 22 dBm[/yellow]")
            config['tx_power'] = 22

        # Step 4: Hop Limit
        console.print("\n[bold]Step 4/8: Hop Limit[/bold]")
        console.print("Maximum times a message can be retransmitted:")
        console.print("  0: No retransmission (direct only)")
        console.print("  3: Standard for most networks (recommended)")
        console.print("  7: Maximum for wide-area or emergency networks")
        console.print("[dim]Higher values increase network traffic[/dim]")
        hop = Prompt.ask("\nEnter hop limit", choices=["0", "1", "2", "3", "4", "5", "6", "7"], default="3")
        config['hop_limit'] = int(hop)
        console.print(f"[green]✓ Hop Limit: {hop}[/green]")

        # Step 5: Channel Number/Slot
        console.print("\n[bold]Step 5/8: Channel Number (Frequency Slot)[/bold]")
        console.print("Different slots use different frequencies to avoid interference:")
        console.print("  Slot 0: Default/general use")
        console.print("  Slot 20: MtnMesh community standard")
        console.print("  Slots 1-7: Custom channels")
        console.print("  US Region: 0-104 channels available")
        console.print("[yellow]⚠️  Must match across all nodes in your mesh![/yellow]")
        channel = Prompt.ask("\nEnter channel number", default="20")
        try:
            channel_val = int(channel)
            if 0 <= channel_val <= 104:
                config['channel_slot'] = channel_val
                console.print(f"[green]✓ Channel: {channel_val}[/green]")
            else:
                console.print("[yellow]Using default: 20[/yellow]")
                config['channel_slot'] = 20
        except ValueError:
            console.print("[yellow]Using default: 20[/yellow]")
            config['channel_slot'] = 20

        # Step 6: Advanced Features (Optional)
        console.print("\n[bold]Step 6/8: Advanced Features (Optional)[/bold]")
        if Confirm.ask("Configure advanced features (SPI speed, frequency offset, etc.)?", default=False):
            # SPI Speed
            console.print("\n[cyan]SPI Bus Speed:[/cyan]")
            console.print("Typically 2000000 Hz (2 MHz). Only change if experiencing issues.")
            spi = Prompt.ask("Enter SPI speed (Hz)", default="2000000")
            try:
                config['spi_speed'] = int(spi)
            except ValueError:
                config['spi_speed'] = 2000000

            # Frequency Offset
            console.print("\n[cyan]Frequency Offset:[/cyan]")
            console.print("Fine-tune center frequency. Usually 0.")
            freq_offset = Prompt.ask("Enter frequency offset (Hz)", default="0")
            try:
                config['frequency_offset'] = int(freq_offset)
            except ValueError:
                config['frequency_offset'] = 0

            # RX Boosted Gain (if applicable)
            console.print("\n[cyan]RX Boosted Gain (SX126x only):[/cyan]")
            console.print("Improves receiver sensitivity but increases power draw.")
            if Confirm.ask("Enable RX boosted gain?", default=False):
                config['rx_boosted_gain'] = True

        # Step 7: Device Role
        console.print("\n[bold]Step 7/8: Device Role[/bold]")
        console.print("1. CLIENT - Normal node with sleep cycles (mobile/portable)")
        console.print("2. ROUTER - Always-on infrastructure node (repeaters)")
        role_choice = Prompt.ask("Select device role", choices=["1", "2"], default="1")
        config['device_role'] = "CLIENT" if role_choice == "1" else "ROUTER"
        console.print(f"[green]✓ Device Role: {config['device_role']}[/green]")

        # Step 8: Configuration Summary
        console.print("\n[bold]Step 8/8: Configuration Summary[/bold]")
        self._display_advanced_config_summary(config)

        # Save option
        if Confirm.ask("\nSave this configuration?", default=True):
            console.print("\n[green]✓ Configuration ready to apply[/green]")
            console.print("[cyan]Tip: This configuration will be saved to /etc/meshtasticd/config.yaml[/cyan]")
            console.print("[dim]For manual editing, see: templates/available.d/advanced-lora-manual.yaml[/dim]")
            return config
        else:
            console.print("[yellow]Configuration not saved[/yellow]")
            return None

    def _display_advanced_config_summary(self, config):
        """Display comprehensive configuration summary"""
        from rich.panel import Panel

        console.print("\n")
        console.print(Panel.fit(
            "[bold green]Configuration Summary[/bold green]",
            border_style="green"
        ))

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Parameter", style="cyan", width=25)
        table.add_column("Value", style="green", width=30)
        table.add_column("Description", style="dim", width=35)

        # Region
        table.add_row("Region", config.get('region', 'US'), "915MHz ISM band")

        # Modem settings
        if config.get('use_preset'):
            table.add_row("Modem Preset", config.get('modem_preset', 'LONG_FAST'), "Pre-defined modem settings")
        else:
            table.add_row("Bandwidth", f"{config.get('bandwidth', 250)} kHz", "Spectrum width")
            table.add_row("Spreading Factor", str(config.get('spreading_factor', 11)), "Signal spreading")
            table.add_row("Coding Rate", f"4/{config.get('coding_rate', 8)}", "Error correction")

        # Power and network
        table.add_row("TX Power", f"{config.get('tx_power', 22)} dBm", "Transmit power")
        table.add_row("Hop Limit", str(config.get('hop_limit', 3)), "Max retransmissions")
        table.add_row("Channel Slot", str(config.get('channel_slot', 20)), "Frequency slot")
        table.add_row("Device Role", config.get('device_role', 'CLIENT'), "Operating mode")

        # Advanced features
        if 'spi_speed' in config:
            table.add_row("SPI Speed", f"{config['spi_speed']} Hz", "SPI bus speed")
        if 'frequency_offset' in config:
            table.add_row("Freq Offset", f"{config['frequency_offset']} Hz", "Center frequency adjust")
        if config.get('rx_boosted_gain'):
            table.add_row("RX Boost", "Enabled", "Enhanced sensitivity")

        console.print(table)

        # Compatibility warning
        console.print("\n[bold yellow]⚠️  Compatibility Requirements:[/bold yellow]")
        console.print("For devices to communicate, they MUST have identical:")
        console.print("  • Region (US)")
        console.print("  • Modem settings (preset OR bandwidth/SF/CR)")
        console.print("  • Channel number")
        console.print("  • Encryption key (PSK)")

        # Estimated range
        if not config.get('use_preset'):
            bw = config.get('bandwidth', 250)
            sf = config.get('spreading_factor', 11)
            if bw == 125 and sf == 12:
                range_est = "15-40km (Maximum)"
            elif bw == 250 and sf >= 11:
                range_est = "5-15km (Very Long)"
            elif bw == 250 and sf == 10:
                range_est = "3-10km (Long)"
            elif bw == 250 and sf <= 9:
                range_est = "1-5km (Medium)"
            else:
                range_est = "Variable"
            console.print(f"\n[cyan]Estimated Range: {range_est}[/cyan]")
            console.print("[dim](Actual range depends on terrain, antenna, and obstacles)[/dim]")

        return config

    def _display_config_summary(self, config):
        """Display configuration summary"""
        console.print("\n[bold cyan]Configuration Summary:[/bold cyan]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Parameter", style="cyan")
        table.add_column("Value", style="green")

        for key, value in config.items():
            display_key = key.replace('_', ' ').title()
            table.add_row(display_key, str(value))

        console.print(table)

        # Calculate approximate data rate
        self._show_performance_estimate(config)

    def _show_performance_estimate(self, config):
        """Show estimated performance based on configuration"""
        console.print("\n[cyan]Estimated Performance:[/cyan]")

        sf = config.get('spreading_factor', 7)
        bw = config.get('bandwidth', 125)

        # Rough estimates
        if sf <= 7:
            range_estimate = "Short (< 5 km)"
            speed_estimate = "Fast"
        elif sf <= 9:
            range_estimate = "Medium (5-10 km)"
            speed_estimate = "Medium"
        else:
            range_estimate = "Long (> 10 km)"
            speed_estimate = "Slow"

        console.print(f"  Range: [yellow]{range_estimate}[/yellow]")
        console.print(f"  Speed: [yellow]{speed_estimate}[/yellow]")
        console.print(f"  Bandwidth: [yellow]{bw} kHz[/yellow]")

        console.print("\n[dim]Note: Actual range depends on terrain, antennas, and interference[/dim]")

    def get_recommended_settings(self, use_case='general'):
        """Get recommended settings for common use cases"""
        presets = {
            'general': {
                'bandwidth': 125,
                'spreading_factor': 7,
                'coding_rate': 5,
                'tx_power': 20,
                'description': 'Balanced settings for general use'
            },
            'long_range': {
                'bandwidth': 125,
                'spreading_factor': 11,
                'coding_rate': 8,
                'tx_power': 30,
                'description': 'Maximum range (slow, high power)'
            },
            'fast': {
                'bandwidth': 250,
                'spreading_factor': 7,
                'coding_rate': 5,
                'tx_power': 20,
                'description': 'Fast data rate (shorter range)'
            },
            'low_power': {
                'bandwidth': 125,
                'spreading_factor': 9,
                'coding_rate': 5,
                'tx_power': 10,
                'description': 'Battery-efficient (reduced range)'
            }
        }

        return presets.get(use_case, presets['general'])

    def show_modem_presets(self):
        """Display available modem presets (ordered Fastest to Slowest)"""
        console.print("\n[bold cyan]Meshtastic Modem Presets (Fastest → Slowest)[/bold cyan]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Preset", style="cyan", width=16)
        table.add_column("Range", style="green", width=22)
        table.add_column("Air Time", style="yellow", width=15)
        table.add_column("Use Case", style="blue", width=45)

        # Order: Fastest to Slowest (official Meshtastic order)
        preset_order = [
            'SHORT_TURBO', 'SHORT_FAST', 'SHORT_SLOW',
            'MEDIUM_FAST', 'MEDIUM_SLOW',
            'LONG_FAST', 'LONG_MODERATE', 'LONG_SLOW',
            'VERY_LONG_SLOW'
        ]

        for preset_key in preset_order:
            if preset_key in self.MODEM_PRESETS:
                preset = self.MODEM_PRESETS[preset_key]
                name = preset['name']
                if preset_key == 'LONG_FAST':
                    name += " [Default]"
                elif preset_key == 'MEDIUM_FAST':
                    name += " ⭐"
                elif preset_key == 'SHORT_TURBO':
                    name += " ⚠️"
                table.add_row(name, preset['range'], preset['air_time'], preset['use_case'])

        console.print(table)
        console.print("\n[yellow]⭐ MediumFast = MtnMesh community standard[/yellow]")
        console.print("[red]⚠️ SHORT_TURBO uses 500kHz - may be illegal in some regions![/red]")

    def configure_modem_preset(self):
        """Configure using a modem preset with back option"""
        while True:
            console.print("\n[bold cyan]═══════════════ Modem Preset Selection ═══════════════[/bold cyan]\n")

            self.show_modem_presets()

            console.print("\n[dim cyan]── Select Preset (Fastest → Slowest) ──[/dim cyan]")
            console.print("  [bold]1[/bold]. Short Turbo [red](⚠️ Check local laws - 500kHz)[/red]")
            console.print("  [bold]2[/bold]. Short Fast")
            console.print("  [bold]3[/bold]. Short Slow")
            console.print("  [bold]4[/bold]. Medium Fast [yellow](⭐ MtnMesh standard)[/yellow]")
            console.print("  [bold]5[/bold]. Medium Slow")
            console.print("  [bold]6[/bold]. Long Fast [green](Default)[/green]")
            console.print("  [bold]7[/bold]. Long Moderate")
            console.print("  [bold]8[/bold]. Long Slow")
            console.print("  [bold]9[/bold]. Very Long Slow [dim](Not recommended for mesh)[/dim]")
            console.print("  [bold]c[/bold]. Custom (Advanced)")
            console.print("\n  [bold]0[/bold]. Back")
            console.print("  [bold]m[/bold]. Main Menu")

            choice = Prompt.ask("\n[cyan]Select preset[/cyan]",
                              choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "c", "m"], default="6")

            if choice == "0":
                return None
            if choice == "m":
                return None  # Signal to return to main menu

            preset_map = {
                "1": "SHORT_TURBO",
                "2": "SHORT_FAST",
                "3": "SHORT_SLOW",
                "4": "MEDIUM_FAST",
                "5": "MEDIUM_SLOW",
                "6": "LONG_FAST",
                "7": "LONG_MODERATE",
                "8": "LONG_SLOW",
                "9": "VERY_LONG_SLOW"
            }

            if choice == "c":
                return self.configure_advanced()

            preset_key = preset_map[choice]
            preset = self.MODEM_PRESETS[preset_key]

            # Legal warning for SHORT_TURBO
            if preset.get('legal_warning'):
                console.print("\n[bold red]⚠️  LEGAL WARNING ⚠️[/bold red]")
                console.print("[red]SHORT_TURBO uses 500kHz bandwidth which may not be legal in all regions.[/red]")
                console.print("[red]Please verify local regulations before using this preset.[/red]")
                if not Confirm.ask("\n[yellow]Do you understand and accept this risk?[/yellow]", default=False):
                    continue

            config = {
                'preset': preset_key,
                'preset_name': preset['name'],
                'bandwidth': preset['bandwidth'],
                'spreading_factor': preset['spreading_factor'],
                'coding_rate': preset['coding_rate']
            }

            # Display selected preset details
            console.print(f"\n[bold green]Selected: {preset['name']}[/bold green]")
            console.print(f"[cyan]Description:[/cyan] {preset['description']}")
            console.print(f"[cyan]Range:[/cyan] {preset['range']}")
            console.print(f"[cyan]Air Time:[/cyan] {preset['air_time']}")
            console.print(f"[cyan]Recommended by:[/cyan] {preset['recommended_by']}")

            # Show technical details
            table = Table(title="Technical Settings", show_header=True, header_style="bold magenta")
            table.add_column("Parameter", style="cyan")
            table.add_column("Value", style="green")

            table.add_row("Bandwidth", f"{preset['bandwidth']} kHz")
            table.add_row("Spreading Factor", str(preset['spreading_factor']))
            table.add_row("Coding Rate", f"4/{preset['coding_rate']}")

            console.print("\n")
            console.print(table)

            if Confirm.ask("\nUse this preset?", default=True):
                return config
            # If user says no, loop continues to show menu again

    def configure_channels(self):
        """Configure channel settings with interactive menu"""
        channels = []

        while True:
            console.print("\n[bold cyan]═══════════════ Channel Configuration ═══════════════[/bold cyan]\n")

            # Show current channels if any
            if channels:
                console.print("[dim]Current configured channels:[/dim]")
                for idx, ch in enumerate(channels):
                    role = ch.get('role', 'SECONDARY')
                    console.print(f"  Channel {idx}: {ch.get('name', 'Unnamed')} ({role})")
                console.print()

            console.print("[dim cyan]── Actions ──[/dim cyan]")
            console.print("  [bold]1[/bold]. Configure Primary Channel (0)")
            console.print("  [bold]2[/bold]. Add/Edit Secondary Channel")
            console.print("  [bold]3[/bold]. View Channel Summary")
            console.print("  [bold]4[/bold]. Clear All Channels")
            console.print("  [bold]5[/bold]. Done - Save Configuration")
            console.print("\n  [bold]0[/bold]. Back")
            console.print("  [bold]m[/bold]. Main Menu")

            choice = Prompt.ask("\n[cyan]Select option[/cyan]",
                              choices=["0", "1", "2", "3", "4", "5", "m"], default="0")

            if choice == "0":
                return channels if channels else None
            elif choice == "m":
                return None  # Signal to return to main menu
            elif choice == "1":
                primary = self._configure_single_channel(0, "Primary")
                if primary:
                    # Replace or add primary channel
                    if channels:
                        channels[0] = primary
                    else:
                        channels.append(primary)
            elif choice == "2":
                if not channels:
                    console.print("[yellow]Please configure the primary channel first[/yellow]")
                    continue
                # Ask which channel slot
                slot = Prompt.ask("Channel slot (1-7)", default="1")
                try:
                    slot_num = int(slot)
                    if 1 <= slot_num <= 7:
                        secondary = self._configure_single_channel(slot_num, "Secondary")
                        if secondary:
                            # Find and replace or append
                            found = False
                            for i, ch in enumerate(channels):
                                if ch.get('index', i) == slot_num:
                                    channels[i] = secondary
                                    found = True
                                    break
                            if not found:
                                secondary['index'] = slot_num
                                channels.append(secondary)
                    else:
                        console.print("[red]Invalid slot. Use 1-7 for secondary channels.[/red]")
                except ValueError:
                    console.print("[red]Invalid input. Enter a number 1-7.[/red]")
            elif choice == "3":
                self._show_channel_summary(channels)
                Prompt.ask("\n[dim]Press Enter to continue[/dim]")
            elif choice == "4":
                if Confirm.ask("[yellow]Clear all channel configuration?[/yellow]", default=False):
                    channels = []
                    console.print("[green]Channels cleared[/green]")
            elif choice == "5":
                if channels:
                    self._show_channel_summary(channels)
                    if Confirm.ask("\n[cyan]Save this configuration?[/cyan]", default=True):
                        return channels
                else:
                    console.print("[yellow]No channels configured[/yellow]")

        return channels

    def _configure_single_channel(self, slot, role_type):
        """Configure a single channel with back option"""
        console.print(f"\n[bold cyan]Configure {role_type} Channel (Slot {slot})[/bold cyan]\n")
        console.print("[dim]Enter values or press Enter for defaults. Type 'back' to cancel.[/dim]\n")

        channel = {'index': slot}

        name = Prompt.ask("Channel name", default="LongFast" if slot == 0 else f"Channel{slot}")
        if name.lower() == 'back':
            return None
        channel['name'] = name

        psk = Prompt.ask("Pre-shared key (base64, or 'default')", default="AQ==")
        if psk.lower() == 'back':
            return None
        channel['psk'] = psk if psk.lower() != 'default' else "AQ=="

        if slot == 0:
            channel['role'] = "PRIMARY"
        else:
            console.print("\n[cyan]Channel role:[/cyan]")
            console.print("1. Secondary (receives messages)")
            console.print("2. Disabled")
            role_choice = Prompt.ask("Select role", choices=["1", "2", "back"], default="1")
            if role_choice == "back":
                return None
            channel['role'] = "SECONDARY" if role_choice == "1" else "DISABLED"

        console.print(f"\n[green]Channel {slot} configured: {channel['name']} ({channel['role']})[/green]")
        return channel

    def _show_channel_summary(self, channels):
        """Display channel configuration summary"""
        if not channels:
            console.print("[yellow]No channels configured[/yellow]")
            return

        table = Table(title="Channel Summary", show_header=True, header_style="bold magenta")
        table.add_column("Slot", style="cyan", width=6)
        table.add_column("Name", style="green", width=20)
        table.add_column("Role", style="yellow", width=12)
        table.add_column("PSK", style="dim", width=20)

        for idx, ch in enumerate(channels):
            slot = ch.get('index', idx)
            role = ch.get('role', 'SECONDARY')
            psk = ch.get('psk', 'default')
            # Truncate PSK for display
            psk_display = psk[:15] + "..." if len(psk) > 15 else psk
            table.add_row(str(slot), ch.get('name', 'Unnamed'), role, psk_display)

        console.print("\n")
        console.print(table)
