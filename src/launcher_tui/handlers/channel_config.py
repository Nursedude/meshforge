"""
Channel Config Handler — Meshtastic channel configuration.

Converted from channel_config_mixin.py as part of the mixin-to-registry migration.
"""

import sys
import json
import re
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

                # ensure_connection() CHOOSES a transport; it has not opened
                # it. "Connected via USB" was claimed from a /dev node's
                # presence with every external dead (truth sweep, non-author
                # review 2026-09-22) — say what was decided, not what was proven.
                if method == 'usb':
                    msg = f"Will use USB device: {conn_value} (present, not verified)"
                else:
                    msg = "Will use TCP: localhost:4403 (not verified)"

                self.ctx.dialog.infobox("Transport", msg)
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
            else:
                self.ctx.notify_unwired(choice, "ChannelConfigHandler._channel_config_menu")

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
                    # Classify THIS channel's psk field (not a whole-output
                    # substring scan: 'none'/'psk' appearing anywhere in the blob
                    # gave a false encryption verdict — wrong for a security
                    # audit). Unparseable -> '?' (honest unknown, never false
                    # 'None'). (S6, #74-#77)
                    psk = self._classify_psk(self._parse_channel_field(raw, 'psk', ''))
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

    @staticmethod
    def _classify_psk(psk_field: str) -> str:
        """Map a parsed channel 'psk' field value to an honest display token.

        Derived from the channel's own psk field, so the encryption state
        shown is trustworthy for a security audit (S6, #74-#77):
          ''/unparseable -> '?'   (unknown, NOT a false 'None')
          none/unset      -> 'None'
          AQ== (the well-known default key) -> 'Default' (effectively public)
          anything else   -> 'Set'
        """
        v = re.sub(r'[^a-z0-9=]', '', (psk_field or '').lower())
        if not v:
            return '?'
        if v in ('none', 'unset', 'disabled', '0', 'null'):
            return 'None'
        if v in ('aq', 'aq=', 'aq=='):
            return 'Default'
        return 'Set'

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
            else:
                self.ctx.notify_unwired(choice, "ChannelConfigHandler._edit_single_channel")

    def _set_channel_name(self, idx: int):
        """Set name for a specific channel.

        Channel 0 goes through `_set_primary_channel` — the one door that reads
        the current name and confirms. Edit Channel › PRIMARY › Set Channel
        Name used to rename the mesh's primary on one typed word (reader pair
        A4/B1, 2026-09-29).
        """
        if idx == 0:
            self._set_primary_channel()
            return
        limit = self.CHANNEL_NAME_MAX_BYTES
        name = self.ctx.dialog.inputbox(
            f"Channel {idx} Name",
            f"Enter channel name (max {limit} bytes; ō/ū/ʻ take 2):",
            ""
        )

        if name is None:
            return
        name = name.strip()
        if not name:
            return
        problem = self._channel_name_problem(name)
        if problem:
            self.ctx.dialog.msgbox(
                f"Channel {idx} Name",
                f"Not written — '{name}' {problem}.")
            return
        # Peers find a channel by name + key, and the gateway bridge resolves
        # its channel BY NAME (gateway/_channel_resolver.py) — a rename is
        # not cosmetic (reader pair, 2026-09-30).
        if not self.ctx.dialog.yesno(
                f"Rename Channel {idx}",
                f"Rename channel {idx} to '{name}'?\n\n"
                "Nodes and the gateway bridge that look this channel up by\n"
                "its old name stop finding it.",
                default_no=True):
            return

        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            self.ctx.dialog.infobox("Setting", f"Setting channel {idx} name...")
            result = mesh_cmd.set_channel_name(idx, name)
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

        # The CLI's word for the well-known key; "AQ==" is NOT understood —
        # fromPSK returns it as a str and the protobuf assign raises
        # TypeError, so this option never worked (reader pair, 2026-09-30).
        psk = "default"
        effect = None
        if choice == "random":
            psk = "random"
        elif choice == "none":
            psk = "none"
        elif choice == "custom":
            psk, effect = self._read_custom_psk()
            if not psk:
                return

        if not self._confirm_psk_change(idx, choice, effect):
            self.ctx.dialog.msgbox(f"Channel {idx} PSK",
                                   "No change — the key was not written.")
            return

        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            self.ctx.dialog.infobox("Setting", f"Setting channel {idx} PSK...")
            result = mesh_cmd.set_channel_psk(idx, psk)
            self.ctx.dialog.msgbox("Result", result.message)

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed:\n{e}")

    # The word an operator must TYPE to re-key channel 0. A yes/no is one
    # keystroke from a slip; this is the mesh's key.
    PRIMARY_PSK_CONFIRM_WORD = "PRIMARY"
    _PSK_EFFECT = {
        "random": "a NEW random key that no other node has",
        "default": "the public default key (AQ==) — anyone can read it",
        "none": "NO encryption — anyone in range can read it",
        "custom": "the custom key you entered",
    }

    def _confirm_psk_change(self, idx: int, choice: str, effect: str = None) -> bool:
        """Default-No confirm for any key change; channel 0 needs the typed
        word. Changing a channel's key cuts this node off from every peer
        that does not get the same key — on channel 0, from the whole mesh.
        Edit Channel › PRIMARY › Generate Random PSK used to do that on two
        menu picks (reader pair A4/B1, 2026-09-29)."""
        effect = effect or self._PSK_EFFECT.get(choice, choice)
        if idx != 0:
            return self.ctx.dialog.yesno(
                f"Change Channel {idx} Key",
                f"Set channel {idx}'s key to {effect}?\n\n"
                "Nodes without the same key stop hearing this channel.",
                default_no=True)
        word = self.PRIMARY_PSK_CONFIRM_WORD
        typed = self.ctx.dialog.inputbox(
            "Re-key PRIMARY Channel",
            f"You are changing the PRIMARY channel's key to {effect}.\n\n"
            "Every node on this mesh must use the same primary key, or this\n"
            "node and the rest of the mesh stop hearing each other.\n\n"
            f"Type {word} to confirm; anything else cancels:",
            "")
        return (typed or "").strip() == word

    @staticmethod
    def _normalize_custom_psk(text):
        """(cli_token, description) for a custom key, or (None, reason).

        The meshtastic CLI only understands `0x<hex>` and `base64:<b64>`
        (plus the words default/random/none) — a bare base64 or hex string
        raises TypeError inside it (measured, meshtastic 2.7.x fromPSK). A
        bare paste is prefixed only when its length makes it unambiguous:
        a 16/32-byte key is 24/44 base64 chars or 32/64 hex chars.
        """
        t = (text or "").strip()
        m = re.fullmatch(r"simple([0-9])", t.lower())
        if m:
            # the CLI's well-known one-byte keys (fromPSK: simpleN -> N+1)
            return (t.lower(), f"the well-known key {t.lower()} — effectively public")
        raw = None
        if t.lower().startswith("0x"):
            try:
                raw = bytes.fromhex(t[2:])
            except ValueError:
                return None, "not valid hex after 0x"
        elif t.lower().startswith("base64:"):
            raw = ChannelConfigHandler._b64(t[7:])
            if raw is None:
                return None, "not valid base64 after base64:"
        elif len(t) in (32, 64) and all(c in "0123456789abcdefABCDEF" for c in t):
            raw = bytes.fromhex(t)
        elif ChannelConfigHandler._b64(t) is not None and len(ChannelConfigHandler._b64(t)) in (16, 32):
            raw = ChannelConfigHandler._b64(t)
        else:
            return None, ("not a 16- or 32-byte key (paste base64 or hex, "
                          "prefix it with base64: / 0x, or type simple0..simple9)")
        if len(raw) not in (16, 32):
            return None, (f"decodes to {len(raw)} bytes; a custom key must be "
                          "16 (AES-128) or 32 (AES-256) — for a well-known key "
                          "type simple0..simple9, or use the menu for default / none")
        b64 = base64.b64encode(raw).decode()
        bits = len(raw) * 8
        # A 128-bit key where 256 was meant is the classic truncated paste
        # (reader pair 2, 2026-09-30) — say so where the operator looks.
        warn = " — only 128-bit: expected 256? check the paste" if bits == 128 else ""
        return "base64:" + b64, f"a custom {bits}-bit key starting {b64[:4]}…{warn}"

    @staticmethod
    def _b64(text):
        """Decode base64 the way people paste it — wrapped lines, urlsafe
        -/_ (Meshtastic share links), missing padding — or None."""
        t = re.sub(r"\s+", "", text or "").replace("-", "+").replace("_", "/")
        if not t or not re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", t):
            return None
        t = t.rstrip("=")
        t += "=" * (-len(t) % 4)
        try:
            return base64.b64decode(t, validate=True)
        except ValueError:
            return None

    def _read_custom_psk(self):
        """Ask for a custom key; (token, description) or (None, None) after
        telling the operator why it was refused."""
        text = self.ctx.dialog.inputbox(
            "Custom PSK",
            "Enter a 16- or 32-byte key (base64 or hex), or simple0..simple9:", "")
        if not text:
            return None, None
        token, desc = self._normalize_custom_psk(text)
        if not token:
            self.ctx.dialog.msgbox("Custom PSK", f"Not written — the key is {desc}.")
            return None, None
        return token, desc

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
            if result.success:
                self.ctx.dialog.msgbox(
                    "Result", result.message or f"Channel {idx} role set to {choice}.")
            else:
                # A failed role-set is a real error, not a soft "may need restart".
                self.ctx.dialog.msgbox(
                    "Role Change Failed",
                    f"Could not set channel {idx} role:\n{result.message}")

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

            if name is None:
                return
            name = name.strip()
            if not name:
                return
            problem = self._channel_name_problem(name)
            if problem:
                self.ctx.dialog.msgbox(
                    "Channel Name",
                    f"Not written — '{name}' {problem}.")
                return

            use_psk = self.ctx.dialog.yesno(
                "Encryption",
                "Enable encryption for this channel?",
                default_no=False
            )

            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            self.ctx.dialog.infobox("Adding", f"Adding channel {idx}...")

            name_result = mesh_cmd.set_channel_name(idx, name)
            psk_result = mesh_cmd.set_channel_psk(
                idx, "random" if use_psk else "none",
            )

            if name_result.success and psk_result.success:
                self.ctx.dialog.msgbox(
                    "Success", f"Channel {idx} configured!\n\nName: {name}",
                )
            else:
                fails = []
                if not name_result.success:
                    fails.append(f"name: {name_result.message}")
                if not psk_result.success:
                    fails.append(f"psk: {psk_result.message}")
                self.ctx.dialog.msgbox(
                    "Error",
                    f"Channel {idx} configuration failed:\n\n" + "\n".join(fails),
                )

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

            result = mesh_cmd._run_command([
                '--ch-index', str(idx),
                '--ch-set', 'name', '',
                '--ch-set', 'psk', 'none'
            ])

            if result.success:
                self.ctx.dialog.msgbox("Success", f"Channel {idx} disabled")
            else:
                self.ctx.dialog.msgbox(
                    "Error",
                    f"Failed to disable channel {idx}:\n\n{result.message}",
                )

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed:\n{e}")

    # Channel 0's line in `meshtastic --info`. MeshAnchor twin: same code.
    _CH0_LINE = re.compile(r'Index 0: PRIMARY[^\n{]*(\{[^\n]*\})')
    # nanopb ChannelSettings.name max_size:12 — 11 UTF-8 bytes + NUL.
    CHANNEL_NAME_MAX_BYTES = 11

    @classmethod
    def _channel_name_problem(cls, name: str):
        """Why the radio would not end up with exactly `name`, or None.

        Refuse, never truncate: the CLI reports success on an over-long name
        and the radio keeps the old one (measured 2026-09-29). And the CLI
        runs every --ch-set value through meshtastic.util.fromStr, so a name
        that reads as a number, a boolean or a 0x/base64: literal is written
        as that value or crashes the CLI: `0x41` becomes 'A', `007`/`yes`/
        `nan` raise (reader pair 2, 2026-09-30).
        """
        n = len(name.encode("utf-8"))
        if n > cls.CHANNEL_NAME_MAX_BYTES:
            return f"is {n} bytes; the radio stores at most {cls.CHANNEL_NAME_MAX_BYTES}"
        low = name.lower()
        if (low.startswith(("0x", "base64:"))
                or low in ("t", "true", "yes", "f", "false", "no")):
            return "would be read by the meshtastic CLI as a value, not a name"
        for conv in (int, float):
            try:
                conv(name)
                return "would be read by the meshtastic CLI as a number, not a name"
            except ValueError:
                pass
        return None

    @classmethod
    def _parse_primary_name(cls, info: str):
        """Channel 0's name from `meshtastic --info`: "" when the primary is
        unnamed (the firmware then shows its preset name), None when the
        output carries no readable primary channel — a read that did not
        happen. The CLI prints the channel through protobuf json_format
        (ensure_ascii), so the object is JSON-DECODED: a regex capture handed
        back escape text for any non-ASCII or quoted name (reader pair,
        2026-09-29)."""
        if not info or "Index 0: PRIMARY" not in info:
            return None
        m = cls._CH0_LINE.search(info)
        if not m:
            return None
        try:
            obj = json.loads(m.group(1))
        except ValueError:
            return None
        name = obj.get("name", "") if isinstance(obj, dict) else None
        return name if isinstance(name, str) else None

    def _set_primary_channel(self):
        """Set primary channel name.

        Pre-fills the radio's CURRENT name and writes only a deliberate,
        confirmed change. It used to pre-fill "MeshForge" and write on one
        Enter with no confirm — renaming the mesh's primary channel (sandbox
        journey primary_channel_one_enter, 2026-09-27).
        """
        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            self.ctx.dialog.infobox("Primary Channel", "Reading the radio's channels...")
            info = mesh_cmd.get_node_info()
            raw = (getattr(info, 'raw', None) or getattr(info, 'raw_output', None) or "")
            current = self._parse_primary_name(raw) if info.success else None
            shown = ("(unnamed — firmware default)" if current == ""
                     else repr(current) if current is not None and current != current.strip()
                     else current if current is not None
                     else "UNKNOWN — could not read the radio")
            limit = self.CHANNEL_NAME_MAX_BYTES
            name = self.ctx.dialog.inputbox(
                "Primary Channel",
                f"Enter channel name (max {limit} bytes; ō/ū/ʻ take 2):\n\nCurrent: {shown}",
                current or ""
            )
            if name is None:
                return
            name = name.strip()
            if not name or (current is not None and name == current.strip()):
                self.ctx.dialog.msgbox("Primary Channel",
                                       "No change — the primary channel name was not written.")
                return
            problem = self._channel_name_problem(name)
            if problem:
                self.ctx.dialog.msgbox(
                    "Primary Channel",
                    f"Not written — '{name}' {problem}.")
                return
            if not self.ctx.dialog.yesno(
                    "Rename Primary Channel",
                    f"Rename the PRIMARY channel?\n\n  {shown}  ->  {name}\n\n"
                    "Every node on this mesh must use the same primary channel\n"
                    "name and key, or they stop hearing each other.",
                    default_no=True):
                return

            self.ctx.dialog.infobox("Setting", f"Setting channel name to {name}...")
            result = mesh_cmd.set_channel_name(0, name)
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

        psk = "default"
        if psk_choice == "random":
            psk = "random"
        elif psk_choice == "custom":
            psk, desc = self._read_custom_psk()
            if not psk or not self._confirm_psk_change(7, "custom", desc):
                return

        try:
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd

            self.ctx.dialog.infobox("Setting", "Configuring gateway channel...")

            name_result = mesh_cmd.set_channel_name(7, "Gateway")
            psk_result = mesh_cmd.set_channel_psk(7, psk)

            if name_result.success and psk_result.success:
                self.ctx.dialog.msgbox("Success",
                    "Gateway channel configured on slot 8!\n\n"
                    "Use this channel for gateway bridging.")
            else:
                fails = []
                if not name_result.success:
                    fails.append(f"name: {name_result.message}")
                if not psk_result.success:
                    fails.append(f"psk: {psk_result.message}")
                self.ctx.dialog.msgbox(
                    "Error",
                    "Gateway channel configuration failed:\n\n"
                    + "\n".join(fails),
                )

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
            f"Hex:\n{psk_hex[:32]}\n{psk_hex[32:]}\n\n"
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
        # The radio gets the preset BY NAME; the numbers shown come from the
        # firmware's table (the literal "mtnmesh" row said SF10 — it is SF9).
        from utils.meshtastic_modem import firmware_params
        sf, bw, cr = firmware_params(tmpl["preset"])
        tmpl = {**tmpl, "bw": bw // 1000, "sf": sf, "cr": cr}

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

            # Apply radio preset via meshtastic CLI. Check the returncode — a
            # nonzero exit raises no exception, so an unchecked result would let
            # us claim "Radio: {preset}" for a preset that never applied.
            import subprocess
            cli = self.ctx.get_meshtastic_cli()
            preset_result = subprocess.run(
                [cli, '--host', 'localhost', '--set', 'lora.modem_preset', tmpl['preset']],
                capture_output=True, timeout=30
            )
            preset_ok = preset_result.returncode == 0

            # Set gateway channel (index 7 = slot 8)
            sys.path.insert(0, str(self.ctx.src_dir))
            from commands import meshtastic as mesh_cmd
            name_result = mesh_cmd.set_channel_name(7, tmpl['channel'])

            if name_result.success and preset_ok:
                self.ctx.dialog.msgbox("Success",
                    f"{tmpl['name']} applied!\n\n"
                    f"Radio: {tmpl['preset']}\n"
                    f"Gateway Channel: {tmpl['channel']} (Slot 8)\n\n"
                    "Ready for RNS bridging.")
            elif name_result.success and not preset_ok:
                err = (preset_result.stderr or b"").decode("utf-8", "replace").strip()
                self.ctx.dialog.msgbox(
                    "Error",
                    f"{tmpl['name']} partially applied — the gateway channel was "
                    f"set, but the radio preset '{tmpl['preset']}' did NOT apply "
                    f"(meshtastic CLI exit {preset_result.returncode}).\n\n"
                    f"{err}\n\n"
                    "Set the preset manually from Radio Config, then retry.",
                )
            else:
                preset_state = ("Radio preset was set" if preset_ok else
                                f"Radio preset also failed (exit {preset_result.returncode})")
                self.ctx.dialog.msgbox(
                    "Error",
                    f"{tmpl['name']} template partially applied — gateway "
                    f"channel setup failed:\n\n{name_result.message}\n\n"
                    f"{preset_state}, but the gateway channel was not "
                    "configured. Re-run from Channel Config > Gateway "
                    "Channel to retry.",
                )

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Template failed:\n{e}")
