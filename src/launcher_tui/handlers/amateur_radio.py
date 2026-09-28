"""
Amateur Radio Handler — Callsign lookup, Part 97 compliance, ARES/RACES tools.

Converted from amateur_radio_mixin.py as part of the mixin-to-registry migration.
"""

import subprocess

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

# Direct import — first-party module, always available
from amateur.callsign import CallsignManager

Part97Reference, ComplianceChecker, LicenseClass, _HAS_COMPLIANCE = safe_import(
    'amateur.compliance', 'Part97Reference', 'ComplianceChecker', 'LicenseClass'
)
ARESRACESTools, MessagePriority, _HAS_ARES = safe_import(
    'amateur.ares_races', 'ARESRACESTools', 'MessagePriority'
)


class AmateurRadioHandler(BaseHandler):
    """TUI handler for amateur radio operator features."""

    handler_id = "amateur_radio"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("ham", "Ham Radio           Callsign, Part 97, ARES", None),
        ]

    def execute(self, action):
        if action == "ham":
            self._amateur_radio_menu()

    def _amateur_radio_menu(self):
        """Amateur radio tools submenu."""
        while True:
            choices = [
                ("callsign", "Callsign Lookup     FCC database query"),
                ("bands", "Band Plan           Part 97 frequencies"),
                ("compliance", "Compliance Check    Verify operation legality"),
                ("ares", "ARES/RACES          Emergency comms tools"),
                ("ics213", "ICS-213 Message     Formal traffic message"),
                ("netchecklist", "Net Checklist       Net control checklist"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Amateur Radio Tools",
                "Licensed operator utilities (WH6GXZ):",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "callsign": ("Callsign Lookup", self._callsign_lookup),
                "bands": ("Band Plan", self._band_plan_display),
                "compliance": ("Compliance Check", self._compliance_check),
                "ares": ("ARES/RACES Tools", self._ares_races_menu),
                "ics213": ("ICS-213 Message", self._ics213_compose),
                "netchecklist": ("Net Checklist", self._net_checklist),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
            else:
                self.ctx.notify_unwired(choice, "AmateurRadioHandler._amateur_radio_menu")

    def _callsign_lookup(self):
        """Look up a callsign in the FCC database."""
        callsign = self.ctx.dialog.inputbox(
            "Callsign Lookup",
            "Enter callsign to look up (e.g., WH6GXZ):",
            ""
        )

        if not callsign:
            return

        callsign = callsign.strip().upper()
        clear_screen()
        print(f"=== Callsign Lookup: {callsign} ===\n")

        print(f"Looking up {callsign}...\n")

        try:
            mgr = CallsignManager()
            info = mgr.lookup_callsign(callsign)

            if info and info.is_valid():
                print(f"  Callsign:  {info.callsign}")
                print(f"  Name:      {info.name}")
                if info.city:
                    print(f"  Location:  {info.city}, {info.state} {info.zip_code}")
                if info.grid_square:
                    print(f"  Grid:      {info.grid_square}")
                if info.license_class:
                    print(f"  Class:     {info.license_class}")
                if info.grant_date:
                    print(f"  Granted:   {info.grant_date}")
                if info.expiration_date:
                    expired = " (EXPIRED)" if info.is_expired() else ""
                    print(f"  Expires:   {info.expiration_date}{expired}")
                if info.frn:
                    print(f"  FRN:       {info.frn}")
                if info.latitude and info.longitude:
                    print(f"  Coords:    {info.latitude:.4f}, {info.longitude:.4f}")
            else:
                print(f"  No results found for {callsign}")
                print(f"  Verify at: https://www.fcc.gov/uls/")
        except Exception as e:
            print(f"  Lookup failed: {e}")
            print(f"\n  This may require internet access.")

        print()
        self.ctx.wait_for_enter()

    def _band_plan_display(self):
        """Display Part 97 band plan reference."""
        clear_screen()
        print("=== Part 97 Band Plan (ISM/LoRa Relevant) ===\n")

        if not _HAS_COMPLIANCE:
            print("  ISM Bands Used by Meshtastic:\n")
            print("  Band        Frequency       Power    Notes")
            print("  " + "-" * 55)
            print("  900 MHz     902-928 MHz     1W       US ISM (Part 15)")
            print("  868 MHz     863-870 MHz     25mW     EU ISM")
            print("  433 MHz     433.05-434.79   10mW     EU ISM")
            print("  2.4 GHz     2400-2483.5     100mW    Worldwide ISM")
            print()
            print("  Part 97 (Licensed) Advantages:")
            print("  " + "-" * 40)
            print("  - Higher power limits (up to 1500W PEP)")
            print("  - Identification required (callsign)")
            print("  - No encryption allowed on ham bands")
            print("  - Meshtastic ham mode: higher power, ID broadcast")
            print()
            self.ctx.wait_for_enter()
            return

        try:
            # get_ism_relevant_bands() never existed (TUI audit finding 6):
            # the relevant bands are the Part 97 allocations that overlap the
            # ISM ranges LoRa uses (33 cm / 70 cm).
            lora_ranges = ((420.0, 450.0), (902.0, 928.0))
            bands = [b for b in Part97Reference.get_bands_for_license(LicenseClass.EXTRA)
                     if any(b.frequency_start < hi and b.frequency_end > lo
                            for lo, hi in lora_ranges)]

            print("  Band        Frequency       Power    License")
            print("  " + "-" * 55)

            for band in bands:
                print(f"  {band.band:<12} {band.frequency_start:.3f}-{band.frequency_end:.3f} MHz"
                      f"  {band.max_power_watts}W")

            print()
            print("  Use 'Compliance Check' to verify specific operation parameters.")
        except Exception as e:
            print(f"  Error loading band plan: {e}")

        print()
        self.ctx.wait_for_enter()

    def _compliance_check(self):
        """Check compliance for current radio configuration."""
        clear_screen()
        print("=== Compliance Check ===\n")

        if not _HAS_COMPLIANCE:
            print("  Compliance module not available.")
            print("  File: src/amateur/compliance.py")
            self.ctx.wait_for_enter()
            return

        checker = ComplianceChecker()

        freq = None
        power = None
        try:
            cli = self.ctx.get_meshtastic_cli()
            result = subprocess.run(
                [cli, '--host', 'localhost', '--get', 'lora'],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if 'frequency' in line.lower():
                        parts = line.split(':')
                        if len(parts) > 1:
                            try:
                                freq = float(parts[-1].strip().replace('MHz', ''))
                            except ValueError:
                                pass
                    if 'tx_power' in line.lower() or 'txpower' in line.lower():
                        parts = line.split(':')
                        if len(parts) > 1:
                            try:
                                power = int(parts[-1].strip().replace('dBm', ''))
                            except ValueError:
                                pass
        except Exception:
            pass

        if freq:
            print(f"  Current frequency: {freq:.3f} MHz")
            if power:
                print(f"  Current TX power:  {power} dBm")
            print()

            try:
                # check_frequency(freq) returns a dict ('authorized', 'band',
                # 'warnings'); it takes no power argument (TUI audit finding 6).
                result = checker.check_frequency(freq)
                if result.get('authorized'):
                    print("  \033[0;32mAUTHORIZED\033[0m - frequency within your license privileges")
                else:
                    print("  \033[0;31mNOT AUTHORIZED\033[0m - review settings")
                for note in result.get('warnings', []):
                    print(f"    - {note}")
                if power is not None:
                    print(f"    (TX power {power} dBm was NOT checked — the checker covers frequency only)")
            except Exception as e:
                print(f"  Check failed: {e}")
        else:
            print("  Could not determine current frequency.")
            print("  Ensure meshtasticd is running.")
            print("\n  Manual check: meshtastic --host localhost --get lora")

        print()
        self.ctx.wait_for_enter()

    def _ares_races_menu(self):
        """ARES/RACES emergency communications tools."""
        while True:
            choices = [
                ("netchecklist", "Net Checklist       Net control ops"),
                ("ics213", "ICS-213 Message     Formal traffic"),
                ("status", "Net Status          Current net info"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "ARES/RACES Tools",
                "Emergency communications:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "netchecklist": ("Net Checklist", self._net_checklist),
                "ics213": ("ICS-213 Message", self._ics213_compose),
                "status": ("Net Status", self._ares_net_status),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
            else:
                self.ctx.notify_unwired(choice, "AmateurRadioHandler._ares_races_menu")

    def _ics213_compose(self):
        """Compose an ICS-213 formal traffic message."""
        clear_screen()
        print("=== ICS-213 General Message Form ===\n")

        if not _HAS_ARES:
            print("  ARES/RACES module not available.")
            print("  File: src/amateur/ares_races.py")
            self.ctx.wait_for_enter()
            return

        to_field = self.ctx.dialog.inputbox("ICS-213", "To (position/name):", "")
        if not to_field:
            return

        from_field = self.ctx.dialog.inputbox("ICS-213", "From (position/name):", "")
        if not from_field:
            return

        subject = self.ctx.dialog.inputbox("ICS-213", "Subject:", "")
        if not subject:
            return

        priority_choices = [
            ("R", "Routine"),
            ("P", "Priority"),
            ("O", "Immediate"),
        ]
        priority = self.ctx.dialog.menu("Message Priority", "Select priority:", priority_choices)
        if not priority:
            priority = "R"

        message = self.ctx.dialog.inputbox("ICS-213", "Message body:", "")
        if not message:
            return

        clear_screen()
        print("=== ICS-213 GENERAL MESSAGE ===")
        print(f"  Priority:  {priority}")
        print(f"  To:        {to_field}")
        print(f"  From:      {from_field}")
        print(f"  Subject:   {subject}")
        print(f"  Message:   {message}")
        print("=" * 40)
        print("\n  Message composed. Ready to transmit via mesh.")

        try:
            # create_traffic_message() never existed (TUI audit finding 6).
            # log_message() swallows a failed write, so "saved" is only said
            # after the log file is READ BACK and holds this message.
            tools = ARESRACESTools()
            msg = tools.create_message(station_id=from_field)
            msg.to_position, msg.from_position = to_field, from_field
            msg.subject, msg.message = subject, message
            msg.priority = MessagePriority(priority) if priority in ("R", "P", "O") \
                else MessagePriority.ROUTINE
            tools.log_message(msg)
            log_file = tools.data_dir / f"traffic_log_{msg.date.replace('-', '')}.json"
            import json as _json
            saved = False
            try:
                saved = any(m.get('message_number') == msg.message_number
                            for m in _json.loads(log_file.read_text()))
            except (OSError, ValueError, AttributeError):
                saved = False
            if saved:
                print(f"  Saved as message {msg.message_number} -> {log_file}")
            else:
                print(f"  NOT SAVED: message {msg.message_number} is not in {log_file}")
        except Exception as e:
            print(f"  NOT SAVED: {e}")

        print()
        self.ctx.wait_for_enter()

    def _net_checklist(self):
        """Display net control operator checklist."""
        clear_screen()
        print("=== Net Control Operator Checklist ===\n")

        if _HAS_ARES:
            tools = ARESRACESTools()
            # A fresh ARESRACESTools holds no checklist; the method this called
            # (get_net_checklist) never existed, so the item crashed every time
            # (found by the truth sweep level-two walk, 2026-09-22).
            checklist = tools.start_new_checklist()

            for i, item in enumerate(checklist, 1):
                status = "\033[0;32m[X]\033[0m" if item.completed else "[ ]"
                print(f"  {status} {i:2d}. {item.task}")
                print(f"       {item.description}")
            print()
        else:
            checklist = [
                ("Pre-Net", "Verify radio/antenna, check propagation"),
                ("Pre-Net", "Prepare net preamble and frequencies"),
                ("Open Net", "Call net to order, identify NCS"),
                ("Roll Call", "Take check-ins, assign precedence"),
                ("Traffic", "Handle formal traffic (ICS-213)"),
                ("Announcements", "Share bulletins, next net schedule"),
                ("Close Net", "Final check-ins, close net"),
                ("Post-Net", "File net report, log participants"),
            ]
            for i, (phase, task) in enumerate(checklist, 1):
                print(f"  [ ] {i:2d}. [{phase}] {task}")
            print()

        self.ctx.wait_for_enter()

    def _ares_net_status(self):
        """Show current ARES/RACES net status."""
        clear_screen()
        print("=== ARES/RACES Net Status ===\n")

        if not _HAS_ARES:
            print("  ARES/RACES module not available.")
            print("  File: src/amateur/ares_races.py")
        else:
            try:
                # get_net_status() never existed (TUI audit finding 6). There
                # is no live net-session tracking; show what IS persisted.
                tools = ARESRACESTools()
                print("  (No live net-session tracking exists — showing saved data.)\n")
                tac = getattr(tools, "tactical_assignments", {}) or {}
                print(f"  Tactical assignments: {len(tac)}")
                for t, c in sorted(tac.items())[:10]:
                    print(f"    {t:<12} {c}")
                import json as _json
                from datetime import datetime as _dt
                today = tools.data_dir / f"traffic_log_{_dt.now().strftime('%Y%m%d')}.json"
                try:
                    n = len(_json.loads(today.read_text()))
                    print(f"  Traffic logged today: {n} message(s)")
                except FileNotFoundError:
                    print("  Traffic logged today: 0 (no log file yet)")
                except (OSError, ValueError) as e:
                    print(f"  Traffic log UNREADABLE: {e}")
            except Exception as e:
                print(f"  Status unavailable: {e}")

        print()
        self.ctx.wait_for_enter()
