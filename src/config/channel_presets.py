"""Channel Configuration Presets for common use cases"""

import os
import yaml
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.panel import Panel

console = Console()


class ChannelPresetManager:
    """Manage channel configuration presets for common setups"""

    # Common channel presets for different use cases
    CHANNEL_PRESETS = {
        'default': {
            'name': 'Default Meshtastic',
            'description': 'Standard Meshtastic default channel',
            'use_case': 'General purpose, compatible with most Meshtastic devices',
            'channels': [
                {
                    'name': 'LongFast',
                    'psk': 'AQ==',  # Default PSK
                    'role': 'PRIMARY',
                    'modem_preset': 'LONG_FAST'
                }
            ],
            'settings': {
                'modem_preset': 'LONG_FAST',
                'channel_slot': 0,
                'hop_limit': 3
            }
        },
        'mtnmesh': {
            'name': 'MtnMesh Community',
            'description': 'MtnMesh community standard (MediumFast)',
            'use_case': 'Mountain/rural mesh networks, optimized for range and speed',
            'channels': [
                {
                    'name': 'MtnMesh',
                    'psk': 'AQ==',
                    'role': 'PRIMARY',
                    'modem_preset': 'MEDIUM_FAST'
                }
            ],
            'settings': {
                'modem_preset': 'MEDIUM_FAST',
                'channel_slot': 20,
                'hop_limit': 3
            }
        },
        'emergency': {
            'name': 'Emergency/SAR',
            'description': 'Search and Rescue / Emergency communications',
            'use_case': 'Maximum range for emergency situations',
            'channels': [
                {
                    'name': 'Emergency',
                    'psk': 'AQ==',
                    'role': 'PRIMARY',
                    'modem_preset': 'LONG_SLOW'
                },
                {
                    'name': 'SAR-Ops',
                    'psk': 'AQ==',
                    'role': 'SECONDARY',
                    'modem_preset': 'LONG_SLOW'
                }
            ],
            'settings': {
                'modem_preset': 'LONG_SLOW',
                'channel_slot': 0,
                'hop_limit': 7,
                'tx_power': 30
            }
        },
        'urban': {
            'name': 'Urban High-Density',
            'description': 'Optimized for cities with many nodes',
            'use_case': 'Dense urban areas where speed matters more than range',
            'channels': [
                {
                    'name': 'Urban',
                    'psk': 'AQ==',
                    'role': 'PRIMARY',
                    'modem_preset': 'SHORT_FAST'
                }
            ],
            'settings': {
                'modem_preset': 'SHORT_FAST',
                'channel_slot': 0,
                'hop_limit': 3,
                'tx_power': 20
            }
        },
        'private_group': {
            'name': 'Private Group',
            'description': 'Template for private encrypted channel',
            'use_case': 'Private communications with custom encryption',
            'channels': [
                {
                    'name': 'Private',
                    'psk': 'GENERATE',  # Will be generated
                    'role': 'PRIMARY',
                    'modem_preset': 'MEDIUM_FAST'
                }
            ],
            'settings': {
                'modem_preset': 'MEDIUM_FAST',
                'channel_slot': 0,
                'hop_limit': 3
            }
        },
        'multi_channel': {
            'name': 'Multi-Channel Setup',
            'description': 'Multiple channels for different purposes',
            'use_case': 'Organizations needing separate channels for different groups',
            'channels': [
                {
                    'name': 'General',
                    'psk': 'AQ==',
                    'role': 'PRIMARY',
                    'modem_preset': 'MEDIUM_FAST'
                },
                {
                    'name': 'Admin',
                    'psk': 'GENERATE',
                    'role': 'SECONDARY'
                },
                {
                    'name': 'Location',
                    'psk': 'AQ==',
                    'role': 'SECONDARY'
                }
            ],
            'settings': {
                'modem_preset': 'MEDIUM_FAST',
                'channel_slot': 0,
                'hop_limit': 3
            }
        },
        'long_range': {
            'name': 'Long Range',
            'description': 'Maximum range configuration',
            'use_case': 'Rural areas, mountain-to-mountain, maximum distance',
            'channels': [
                {
                    'name': 'LongRange',
                    'psk': 'AQ==',
                    'role': 'PRIMARY',
                    'modem_preset': 'LONG_MODERATE'
                }
            ],
            'settings': {
                'modem_preset': 'LONG_MODERATE',
                'channel_slot': 0,
                'hop_limit': 5,
                'tx_power': 30
            }
        },
        'repeater': {
            'name': 'Repeater/Router',
            'description': 'Configuration for dedicated repeater nodes',
            'use_case': 'Fixed infrastructure nodes for extending mesh coverage',
            'channels': [
                {
                    'name': 'LongFast',
                    'psk': 'AQ==',
                    'role': 'PRIMARY',
                    'modem_preset': 'LONG_FAST'
                }
            ],
            'settings': {
                'modem_preset': 'LONG_FAST',
                'channel_slot': 0,
                'hop_limit': 7,
                'tx_power': 30,
                'is_router': True
            }
        }
    }

    def __init__(self):
        self.user_presets_dir = Path.home() / '.config' / 'meshtasticd' / 'presets'
        self.user_presets_dir.mkdir(parents=True, exist_ok=True)

    # Emoji icons for presets
    PRESET_ICONS = {
        'default': '📡',
        'mtnmesh': '🏔️',
        'emergency': '🚨',
        'urban': '🏙️',
        'private_group': '🔐',
        'multi_channel': '📻',
        'long_range': '📶',
        'repeater': '🔄'
    }

    def show_presets(self):
        """Display available channel presets"""
        console.print("\n[bold cyan]═══════════════ Channel Presets ═══════════════[/bold cyan]\n")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("#", style="cyan", width=3)
        table.add_column("", width=3)  # Icon column
        table.add_column("Name", style="green", width=20)
        table.add_column("Description", style="white", width=35)
        table.add_column("Use Case", style="yellow", width=30)

        for idx, (key, preset) in enumerate(self.CHANNEL_PRESETS.items(), 1):
            icon = self.PRESET_ICONS.get(key, '📻')
            # Add star for recommended presets
            name = preset['name']
            if key == 'mtnmesh':
                name = f"[yellow]{name}[/yellow] ⭐"
            table.add_row(
                str(idx),
                icon,
                name,
                preset['description'],
                preset['use_case'][:30] + '...' if len(preset['use_case']) > 30 else preset['use_case']
            )

        console.print(table)
        console.print("\n[dim]⭐ = Recommended for community networks[/dim]")

        # Show user presets if any
        user_presets = self.load_user_presets()
        if user_presets:
            console.print("\n[cyan]📁 User-saved presets:[/cyan]")
            for name in user_presets.keys():
                console.print(f"  • {name}")

    def select_preset(self):
        """Interactive preset selection"""
        self.show_presets()

        preset_keys = list(self.CHANNEL_PRESETS.keys())

        console.print("\n[cyan]Select a preset:[/cyan]")
        for idx, key in enumerate(preset_keys, 1):
            icon = self.PRESET_ICONS.get(key, '📻')
            name = self.CHANNEL_PRESETS[key]['name']
            extra = " [yellow]⭐ Recommended[/yellow]" if key == 'mtnmesh' else ""
            console.print(f"  [bold]{idx}[/bold]. {icon} {name}{extra}")

        console.print(f"\n  [bold]{len(preset_keys) + 1}[/bold]. ✏️  Custom Configuration")
        console.print(f"  [bold]{len(preset_keys) + 2}[/bold]. 📂 Load Saved Preset")

        choice = Prompt.ask(
            "\n[cyan]Enter selection[/cyan]",
            choices=[str(i) for i in range(1, len(preset_keys) + 3)],
            default="2"  # Default to MtnMesh (option 2)
        )

        choice_idx = int(choice) - 1

        if choice_idx < len(preset_keys):
            return self.configure_preset(preset_keys[choice_idx])
        elif choice_idx == len(preset_keys):
            return self.custom_channel_config()
        else:
            return self.load_saved_preset()

    def configure_preset(self, preset_key):
        """Configure a specific preset with customization options"""
        preset = self.CHANNEL_PRESETS[preset_key]

        console.print(f"\n[bold cyan]Configuring: {preset['name']}[/bold cyan]")
        console.print(f"[dim]{preset['description']}[/dim]\n")

        # Show preset details
        self._display_preset_details(preset)

        if not Confirm.ask("\nUse this preset?", default=True):
            return None

        config = {
            'preset_name': preset['name'],
            'channels': [],
            'settings': preset['settings'].copy()
        }

        # Process channels
        for idx, channel in enumerate(preset['channels']):
            ch_config = channel.copy()

            # Generate PSK if needed
            if channel.get('psk') == 'GENERATE':
                if Confirm.ask(f"Generate random PSK for '{channel['name']}' channel?", default=True):
                    ch_config['psk'] = self._generate_psk()
                    console.print(f"[green]Generated PSK: {ch_config['psk']}[/green]")
                else:
                    ch_config['psk'] = Prompt.ask("Enter custom PSK (base64)")

            # Allow channel name customization
            if Confirm.ask(f"Customize channel name (current: {channel['name']})?", default=False):
                ch_config['name'] = Prompt.ask("Enter channel name", default=channel['name'])

            config['channels'].append(ch_config)

        # Allow settings customization
        if Confirm.ask("\nCustomize radio settings?", default=False):
            config['settings'] = self._customize_settings(config['settings'])

        # Show final configuration
        self._display_final_config(config)

        # Offer to save as user preset
        if Confirm.ask("\nSave as personal preset for future use?", default=False):
            preset_name = Prompt.ask("Enter preset name")
            self.save_user_preset(preset_name, config)

        return config

    def _display_preset_details(self, preset):
        """Display detailed preset information"""
        console.print("[cyan]Channels:[/cyan]")
        for idx, channel in enumerate(preset['channels']):
            role_color = 'green' if channel.get('role') == 'PRIMARY' else 'yellow'
            console.print(f"  [{role_color}]{idx}[/{role_color}] {channel['name']} ({channel.get('role', 'SECONDARY')})")

        console.print("\n[cyan]Settings:[/cyan]")
        settings = preset['settings']
        console.print(f"  Modem Preset: {settings.get('modem_preset', 'LONG_FAST')}")
        console.print(f"  Channel Slot: {settings.get('channel_slot', 0)}")
        console.print(f"  Hop Limit: {settings.get('hop_limit', 3)}")
        if 'tx_power' in settings:
            console.print(f"  TX Power: {settings['tx_power']} dBm")

    def _display_final_config(self, config):
        """Display the final configuration"""
        console.print("\n[bold cyan]Final Configuration[/bold cyan]")

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Parameter", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Preset", config['preset_name'])

        for idx, channel in enumerate(config['channels']):
            table.add_row(f"Channel {idx}", f"{channel['name']} ({channel.get('role', 'SECONDARY')})")

        for key, value in config['settings'].items():
            table.add_row(key.replace('_', ' ').title(), str(value))

        console.print(table)

    def _customize_settings(self, settings):
        """Allow user to customize radio settings"""
        from rich.prompt import IntPrompt

        console.print("\n[cyan]Customize Radio Settings:[/cyan]")

        # Channel slot
        settings['channel_slot'] = IntPrompt.ask(
            "Channel slot",
            default=settings.get('channel_slot', 0)
        )

        # Hop limit
        settings['hop_limit'] = IntPrompt.ask(
            "Hop limit (1-7)",
            default=settings.get('hop_limit', 3)
        )

        # TX Power
        settings['tx_power'] = IntPrompt.ask(
            "TX Power (dBm, 0-30)",
            default=settings.get('tx_power', 20)
        )

        return settings

    def _generate_psk(self):
        """Generate a random PSK"""
        import base64
        import secrets
        # Generate 32 bytes of random data for AES-256
        random_bytes = secrets.token_bytes(32)
        return base64.b64encode(random_bytes).decode('utf-8')

    def custom_channel_config(self):
        """Create a fully custom channel configuration"""
        console.print("\n[bold cyan]Custom Channel Configuration[/bold cyan]\n")

        config = {
            'preset_name': 'Custom',
            'channels': [],
            'settings': {}
        }

        # Configure primary channel
        console.print("[cyan]Primary Channel (Index 0):[/cyan]")
        primary = {
            'name': Prompt.ask("Channel name", default="Primary"),
            'role': 'PRIMARY'
        }

        psk_choice = Prompt.ask(
            "PSK option",
            choices=["default", "generate", "custom"],
            default="default"
        )

        if psk_choice == "default":
            primary['psk'] = "AQ=="
        elif psk_choice == "generate":
            primary['psk'] = self._generate_psk()
            console.print(f"[green]Generated PSK: {primary['psk']}[/green]")
        else:
            primary['psk'] = Prompt.ask("Enter PSK (base64)")

        config['channels'].append(primary)

        # Additional channels
        channel_count = 1
        while channel_count < 8 and Confirm.ask(f"\nAdd channel {channel_count}?", default=False):
            channel = {
                'name': Prompt.ask(f"Channel {channel_count} name", default=f"Channel{channel_count}"),
                'role': 'SECONDARY',
                'psk': Prompt.ask("PSK (base64, or 'generate')", default="AQ==")
            }
            if channel['psk'].lower() == 'generate':
                channel['psk'] = self._generate_psk()
                console.print(f"[green]Generated PSK: {channel['psk']}[/green]")

            config['channels'].append(channel)
            channel_count += 1

        # Settings
        from config.lora import LoRaConfigurator
        lora = LoRaConfigurator()
        modem_config = lora.configure_modem_preset()

        if modem_config:
            config['settings'].update(modem_config)

        from rich.prompt import IntPrompt
        config['settings']['channel_slot'] = IntPrompt.ask("Channel slot", default=0)
        config['settings']['hop_limit'] = IntPrompt.ask("Hop limit", default=3)

        return config

    def save_user_preset(self, name, config):
        """Save configuration as a user preset"""
        preset_file = self.user_presets_dir / f"{name.lower().replace(' ', '_')}.yaml"

        try:
            with open(preset_file, 'w') as f:
                yaml.dump(config, f, default_flow_style=False)
            console.print(f"[green]Preset saved to: {preset_file}[/green]")
            return True
        except Exception as e:
            console.print(f"[red]Failed to save preset: {e}[/red]")
            return False

    def load_user_presets(self):
        """Load all user-saved presets"""
        presets = {}
        if self.user_presets_dir.exists():
            for preset_file in self.user_presets_dir.glob('*.yaml'):
                try:
                    with open(preset_file, 'r') as f:
                        presets[preset_file.stem] = yaml.safe_load(f)
                except Exception as e:
                    from utils.logger import get_logger
                    logger = get_logger()
                    logger.warning(f"Could not load preset file {preset_file}: {e}")
        return presets

    def load_saved_preset(self):
        """Load a user-saved preset"""
        presets = self.load_user_presets()

        if not presets:
            console.print("[yellow]No saved presets found[/yellow]")
            return None

        console.print("\n[cyan]Saved Presets:[/cyan]")
        preset_names = list(presets.keys())
        for idx, name in enumerate(preset_names, 1):
            console.print(f"  {idx}. {name}")

        choice = Prompt.ask(
            "Select preset",
            choices=[str(i) for i in range(1, len(preset_names) + 1)]
        )

        selected = preset_names[int(choice) - 1]
        console.print(f"[green]Loaded preset: {selected}[/green]")
        return presets[selected]

    def apply_preset_to_config(self, config, output_file='/etc/meshtasticd/config.yaml'):
        """Apply preset configuration to meshtasticd config file"""
        console.print(f"\n[cyan]Applying configuration to {output_file}...[/cyan]")

        try:
            # Build YAML config
            yaml_config = {
                'Lora': {},
                'Channels': []
            }

            # Apply LoRa settings
            settings = config.get('settings', {})
            if 'bandwidth' in settings:
                yaml_config['Lora']['Bandwidth'] = settings['bandwidth']
            if 'spreading_factor' in settings:
                yaml_config['Lora']['SpreadFactor'] = settings['spreading_factor']
            if 'coding_rate' in settings:
                yaml_config['Lora']['CodingRate'] = settings['coding_rate']
            if 'tx_power' in settings:
                yaml_config['Lora']['TXpower'] = settings['tx_power']
            if 'hop_limit' in settings:
                yaml_config['Lora']['HopLimit'] = settings['hop_limit']
            if 'channel_slot' in settings:
                yaml_config['Lora']['ChannelNum'] = settings['channel_slot']

            # Apply channels
            for channel in config.get('channels', []):
                yaml_config['Channels'].append({
                    'name': channel['name'],
                    'psk': channel.get('psk', 'AQ=='),
                    'role': channel.get('role', 'SECONDARY')
                })

            # Write config
            with open(output_file, 'w') as f:
                yaml.dump(yaml_config, f, default_flow_style=False)

            console.print(f"[green]Configuration saved to {output_file}[/green]")
            return True

        except Exception as e:
            console.print(f"[red]Failed to apply configuration: {e}[/red]")
            return False
