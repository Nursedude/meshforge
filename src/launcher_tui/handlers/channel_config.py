"""
Channel Config Handler — Meshtastic channel configuration.

Converted from channel_config_mixin.py as part of the mixin-to-registry migration.
"""

import sys
import secrets
import base64

from handler_protocol import BaseHandler


class ChannelConfigHandler(BaseHandler):
    """TUI handler for Meshtastic channel configuration."""

    handler_id = "channel_config"
    menu_section = "configuration"

    def menu_items(self):
        return [
            ("channels", "Channel Config      Meshtastic channels", None),
        ]

    def execute(self, action):
        if action == "channels":
            self._channel_config_menu()

    def _ensure_meshtastic_connection(self) -> bool:
        """Ensure meshtastic connection is configured."""
        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            self.ctx.dialog.infobox("Connection", "Detecting Meshtastic device...")

            result = mesh_cmd.ensure_connection()
            if result.success:
                conn_type = result.data.get('type', 'unknown')
                conn_value = result.data.get('value', '')
                method = result.data.get('method', '')

                if method == 'usb':
                    msg = f"Connected via USB: {conn_value}"
                else:
                    msg = f"Connected via TCP: localhost:4403"

                self.ctx.dialog.infobox("Connected", msg)
                return True
            else:
                self.ctx.dialog.msgbox(
                    "Connection Failed",
                    f"{result.message}\n\n"
                    "Check that your radio is connected\n"
                    "or meshtasticd service is running."
                )
                return False

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Connection check failed:\n{e}")
            return False

    def _channel_config_menu(self):
        """Channel configuration menu."""
        if not self._ensure_meshtastic_connection():
            return

        while True:
            choices = [
                ("list", "View All Channels"),
                ("edit", "Edit Channel"),
                ("add", "Add/Enable Channel"),
                ("disable", "Disable Channel"),
                ("primary", "Quick: Set Primary Name"),
                ("gateway", "Quick: Gateway Channel (Slot 8)"),
                ("psk", "Generate New PSK"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Channel Config",
                "Configure all 8 mesh channels:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "list": ("View Channels", self._view_all_channels),
                "edit": ("Edit Channel", self._edit_channel_menu),
                "add": ("Add Channel", self._add_channel),
                "disable": ("Disable Channel", self._disable_channel),
                "primary": ("Set Primary Channel", self._set_primary_channel),
                "gateway": ("Gateway Channel", self._set_gateway_channel),
                "psk": ("Generate PSK", self._generate_psk),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _view_all_channels(self):
        """View all 8 channels with their configuration."""
        self.ctx.dialog.infobox("Channels", "Loading channels (timeout: 10s)...")

        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            channels = []
            consecutive_failures = 0
            for i in range(8):
                result = mesh_cmd._run_command(
                    ["--ch-index", str(i), "--info"], timeout=10
                )
                if result.success:
                    consecutive_failures = 0
                    raw = result.raw or ''
                    name = self._parse_channel_field(raw, 'name', f'Channel {i}')
                    role = self._parse_channel_field(raw, 'role', 'DISABLED')
                    psk = 'Set' if 'psk' in raw.lower() and 'none' not in raw.lower() else 'None'
                    channels.append({
                        'index': i,
                        'name': name,
                        'role': role,
                        'psk': psk
                    })
                else:
                    consecutive_failures += 1
                    channels.append({
                        'index': i,
                        'name': f'Channel {i}',
                        'role': 'DISABLED',
                        'psk': '-'
                    })
                    if consecutive_failures >= 2:
                        for j in range(i + 1, 8):
                            channels.append({
                                'index': j,
                                'name': f'Channel {j}',
                                'role': 'DISABLED',
                                'psk': '-'
                            })
                        break

            text = "Channel Configuration (8 slots):\n\n"
            text += "Slot  Name           Role        PSK\n"
            text += "\u2500" * 40 + "\n"

            for ch in channels:
                idx = ch['index']
                name = ch['name'][:12].ljust(12)
                role = ch['role'][:10].ljust(10)
                psk = ch['psk']
                marker = "*" if idx == 0 else " "
                text += f"  {idx}{marker}   {name}  {role}  {psk}\n"

            text += "\n* = Primary channel"
            text += "\nUse 'Edit Channel' to configure individually"

            self.ctx.dialog.msgbox("All Channels", text)

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to load channels:\n{e}")

    def _parse_channel_field(self, raw: str, field: str, default: str) -> str:
        """Parse a field from channel info output."""
        for line in raw.split('\n'):
            if field.lower() in line.lower():
                parts = line.split(':')
                if len(parts) >= 2:
                    return parts[1].strip()[:15]
        return default

    def _edit_channel_menu(self):
        """Select and edit a specific channel."""
        choices = []
        for i in range(8):
            label = "PRIMARY" if i == 0 else f"Slot {i+1}"
            choices.append((str(i), f"{label} - Channel {i}"))
        choices.append(("back", "Back"))

        choice = self.ctx.dialog.menu(
            "Edit Channel",
            "Select channel to edit (0-7):",
            choices
        )

        if choice is None or choice == "back":
            return

        try:
            channel_idx = int(choice)
            self._edit_single_channel(channel_idx)
        except ValueError:
            pass

    def _edit_single_channel(self, idx: int):
        """Edit a single channel's settings."""
        while True:
            slot_name = "PRIMARY" if idx == 0 else f"Slot {idx+1}"

            choices = [
                ("name", "Set Channel Name"),
                ("psk", "Set PSK (Encryption Key)"),
                ("role", "Set Role (Primary/Secondary/Disabled)"),
                ("view", "View Current Settings"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                f"Channel {idx} ({slot_name})",
                f"Edit channel {idx} settings:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "name": ("Set Channel Name", self._set_channel_name),
                "psk": ("Set Channel PSK", self._set_channel_psk),
                "role": ("Set Channel Role", self._set_channel_role),
                "view": ("View Channel", self._view_single_channel),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(entry[0], entry[1], idx)

    def _set_channel_name(self, idx: int):
        """Set name for a specific channel."""
        name = self.ctx.dialog.inputbox(
            f"Channel {idx} Name",
            "Enter channel name (max 12 chars):",
            ""
        )

        if not name:
            return

        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            self.ctx.dialog.infobox("Setting", f"Setting channel {idx} name...")
            result = mesh_cmd.set_channel_name(idx, name[:12])
            self.ctx.dialog.msgbox("Result", result.message)

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed:\n{e}")

    def _set_channel_psk(self, idx: int):
        """Set PSK for a specific channel."""
        psk_choices = [
            ("random", "Generate Random PSK"),
            ("default", "Use Default PSK (AQ==)"),
            ("none", "No Encryption (Open)"),
            ("custom", "Enter Custom PSK"),
        ]

        choice = self.ctx.dialog.menu(
            f"Channel {idx} PSK",
            "Select PSK option:",
            psk_choices
        )

        if not choice:
            return

        psk = "AQ=="
        if choice == "random":
            psk = "random"
        elif choice == "none":
            psk = "none"
        elif choice == "custom":
            psk = self.ctx.dialog.inputbox("Custom PSK", "Enter PSK (base64 or hex):", "")
            if not psk:
                return

        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            self.ctx.dialog.infobox("Setting", f"Setting channel {idx} PSK...")
            result = mesh_cmd.set_channel_psk(idx, psk)
            self.ctx.dialog.msgbox("Result", result.message)

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed:\n{e}")

    def _set_channel_role(self, idx: int):
        """Set role for a specific channel."""
        if idx == 0:
            self.ctx.dialog.msgbox("Info", "Channel 0 is always PRIMARY.\nCannot change role.")
            return

        role_choices = [
            ("SECONDARY", "SECONDARY - Active additional channel"),
            ("DISABLED", "DISABLED - Channel not in use"),
        ]

        choice = self.ctx.dialog.menu(
            f"Channel {idx} Role",
            "Select channel role:",
            role_choices
        )

        if not choice:
            return

        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            self.ctx.dialog.infobox("Setting", f"Setting channel {idx} role...")
            result = mesh_cmd._run_command([
                '--ch-index', str(idx),
                '--ch-set', 'module_settings.role', choice
            ])
            self.ctx.dialog.msgbox("Result", result.message if result.success else "Note: Role change may require restart")

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed:\n{e}")

    def _view_single_channel(self, idx: int):
        """View settings for a single channel."""
        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            result = mesh_cmd.get_channel_info(idx)
            if result.success:
                text = f"Channel {idx} Settings:\n\n{result.raw or 'No data'}"
            else:
                text = f"Failed to get channel {idx}:\n{result.message}"

            self.ctx.dialog.msgbox(f"Channel {idx}", text[:1500])

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed:\n{e}")

    def _add_channel(self):
        """Add/enable a new channel."""
        choices = []
        for i in range(1, 8):
            choices.append((str(i), f"Slot {i+1} - Channel {i}"))
        choices.append(("back", "Back"))

        choice = self.ctx.dialog.menu(
            "Add Channel",
            "Select slot for new channel:\n\n"
            "(Channel 0 is always primary)",
            choices
        )

        if choice is None or choice == "back":
            return

        try:
            idx = int(choice)

            name = self.ctx.dialog.inputbox(
                "Channel Name",
                f"Enter name for channel {idx}:",
                f"Channel{idx}"
            )

            if not name:
                return

            use_psk = self.ctx.dialog.yesno(
                "Encryption",
                "Enable encryption for this channel?",
                default_no=False
            )

            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            self.ctx.dialog.infobox("Adding", f"Adding channel {idx}...")

            mesh_cmd.set_channel_name(idx, name[:12])

            if use_psk:
                mesh_cmd.set_channel_psk(idx, "random")
            else:
                mesh_cmd.set_channel_psk(idx, "none")

            self.ctx.dialog.msgbox("Success", f"Channel {idx} configured!\n\nName: {name}")

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed:\n{e}")

    def _disable_channel(self):
        """Disable a channel."""
        choices = []
        for i in range(1, 8):
            choices.append((str(i), f"Channel {i}"))
        choices.append(("back", "Back"))

        choice = self.ctx.dialog.menu(
            "Disable Channel",
            "Select channel to disable:\n\n"
            "(Channel 0 cannot be disabled)",
            choices
        )

        if choice is None or choice == "back":
            return

        try:
            idx = int(choice)

            confirm = self.ctx.dialog.yesno(
                "Confirm",
                f"Disable channel {idx}?\n\n"
                "This will clear the channel configuration.",
                default_no=True
            )

            if not confirm:
                return

            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            self.ctx.dialog.infobox("Disabling", f"Disabling channel {idx}...")

            mesh_cmd._run_command([
                '--ch-index', str(idx),
                '--ch-set', 'name', '',
                '--ch-set', 'psk', 'none'
            ])

            self.ctx.dialog.msgbox("Success", f"Channel {idx} disabled")

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed:\n{e}")

    def _set_primary_channel(self):
        """Set primary channel name."""
        name = self.ctx.dialog.inputbox(
            "Primary Channel",
            "Enter channel name (max 12 chars):",
            "MeshForge"
        )

        if not name:
            return

        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            self.ctx.dialog.infobox("Setting", f"Setting channel name to {name}...")
            result = mesh_cmd.set_channel_name(0, name[:12])
            self.ctx.dialog.msgbox("Result", result.message)

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed:\n{e}")

    def _set_gateway_channel(self):
        """Set up gateway channel on slot 8."""
        confirm = self.ctx.dialog.yesno(
            "Gateway Channel",
            "Set up gateway channel on slot 8?\n\n"
            "This is the recommended channel for\n"
            "MeshForge <-> RNS gateway bridging.\n\n"
            "Channel 8 will be configured as:\n"
            "  Name: Gateway\n"
            "  Role: SECONDARY\n"
            "  PSK: [Generated or custom]",
            default_no=True
        )

        if not confirm:
            return

        psk_choices = [
            ("random", "Generate Random PSK"),
            ("default", "Use Default PSK (AQ==)"),
            ("custom", "Enter Custom PSK"),
        ]

        psk_choice = self.ctx.dialog.menu(
            "Gateway PSK",
            "Select PSK for gateway channel:",
            psk_choices
        )

        if not psk_choice:
            return

        psk = "AQ=="
        if psk_choice == "random":
            psk = "random"
        elif psk_choice == "custom":
            psk = self.ctx.dialog.inputbox("Custom PSK", "Enter PSK (hex or base64):", "")
            if not psk:
                return

        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            self.ctx.dialog.infobox("Setting", "Configuring gateway channel...")

            mesh_cmd.set_channel_name(7, "Gateway")
            mesh_cmd.set_channel_psk(7, psk)

            self.ctx.dialog.msgbox("Success",
                "Gateway channel configured on slot 8!\n\n"
                "Use this channel for gateway bridging.")

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed:\n{e}")

    def _generate_psk(self):
        """Generate a new PSK."""
        psk_bytes = secrets.token_bytes(32)
        psk_b64 = base64.b64encode(psk_bytes).decode()
        psk_hex = psk_bytes.hex()

        self.ctx.dialog.msgbox("Generated PSK",
            f"New 256-bit PSK:\n\n"
            f"Base64:\n{psk_b64}\n\n"
            f"Hex:\n{psk_hex[:32]}...\n\n"
            "Copy this PSK and share securely\n"
            "with your mesh network members.")

    def _gateway_template_menu(self):
        """Gateway template configuration."""
        templates = [
            ("standard", "Standard Gateway (Long Fast)"),
            ("turbo", "Turbo Gateway (Short Turbo + Ch8)"),
            ("mtnmesh", "MtnMesh Gateway (Medium Fast)"),
            ("custom", "Custom Gateway Setup"),
            ("back", "Back"),
        ]

        choice = self.ctx.dialog.menu(
            "Gateway Templates",
            "Pre-configured gateway setups:\n\n"
            "Templates configure radio preset,\n"
            "channel 8 for gateway, and optimize\n"
            "for RNS bridging.",
            templates
        )

        if choice and choice != "back":
            self._apply_gateway_template(choice)

    def _apply_gateway_template(self, template: str):
        """Apply a gateway template."""
        templates = {
            "standard": {
                "name": "Standard Gateway",
                "preset": "LONG_FAST",
                "bw": 250, "sf": 11, "cr": 5,
                "channel": "Gateway",
                "description": "Default Meshtastic settings with gateway channel"
            },
            "turbo": {
                "name": "Turbo Gateway",
                "preset": "SHORT_TURBO",
                "bw": 500, "sf": 7, "cr": 5,
                "channel": "GW-Turbo",
                "description": "Maximum speed for local gateway bridging"
            },
            "mtnmesh": {
                "name": "MtnMesh Gateway",
                "preset": "MEDIUM_FAST",
                "bw": 250, "sf": 10, "cr": 5,
                "channel": "MtnMesh-GW",
                "description": "MtnMesh community standard with gateway"
            },
        }

        if template == "custom":
            self.ctx.dialog.msgbox("Custom Gateway",
                "For custom gateway setup:\n\n"
                "1. Use Radio Presets to set LoRa params\n"
                "2. Use Channel Config > Gateway Channel\n"
                "3. Edit config files for advanced options")
            return

        tmpl = templates.get(template)
        if not tmpl:
            return

        confirm = self.ctx.dialog.yesno(
            tmpl["name"],
            f"Apply {tmpl['name']} template?\n\n"
            f"Preset: {tmpl['preset']}\n"
            f"Bandwidth: {tmpl['bw']} kHz\n"
            f"Spreading Factor: SF{tmpl['sf']}\n"
            f"Gateway Channel: {tmpl['channel']} (Slot 8)\n\n"
            f"{tmpl['description']}\n\n"
            "This will update config and restart service.",
            default_no=True
        )

        if not confirm:
            return

        try:
            self.ctx.dialog.infobox("Applying", f"Applying {tmpl['name']}...")

            # Apply radio preset via meshtastic CLI
            import subprocess
            cli = self.ctx.get_meshtastic_cli()
            subprocess.run(
                [cli, '--host', 'localhost', '--set', 'lora.modem_preset', tmpl['preset']],
                capture_output=True, timeout=30
            )

            # Set gateway channel (index 7 = slot 8)
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd
            mesh_cmd.set_channel_name(7, tmpl['channel'])

            self.ctx.dialog.msgbox("Success",
                f"{tmpl['name']} applied!\n\n"
                f"Radio: {tmpl['preset']}\n"
                f"Gateway Channel: {tmpl['channel']} (Slot 8)\n\n"
                "Ready for RNS bridging.")

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Template failed:\n{e}")
