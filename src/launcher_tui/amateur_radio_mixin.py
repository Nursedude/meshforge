"""
Amateur Radio Mixin — Callsign lookup, Part 97 compliance, ARES/RACES tools.

Wires the following modules into the TUI:
- amateur.callsign (CallsignManager, callsign lookup)
- amateur.compliance (Part97Reference, ComplianceChecker)
- amateur.ares_races (ARESRACESTools, ICS-213 messages)

Provides a top-level "Ham Radio" menu accessible from main menu or
Emergency Mode for EMCOMM operations.
"""

import subprocess
from backend import clear_screen
from utils.safe_import import safe_import

# Direct import — first-party module, always available
from amateur.callsign import CallsignManager
Part97Reference, ComplianceChecker, _HAS_COMPLIANCE = safe_import(
    'amateur.compliance', 'Part97Reference', 'ComplianceChecker'
)
ARESRACESTools, MessagePriority, _HAS_ARES = safe_import(
    'amateur.ares_races', 'ARESRACESTools', 'MessagePriority'
)


class AmateurRadioMixin:
    """TUI mixin for amateur radio operator features."""

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

            choice = self.dialog.menu(
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
                self._safe_call(*entry)

    def _callsign_lookup(self):
        """Look up a callsign in the FCC database."""
        callsign = self.dialog.inputbox(
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
            info = mgr.lookup(callsign)

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
        self._wait_for_enter()

    def _band_plan_display(self):
        """Display Part 97 band plan reference."""
        clear_screen()
        print("=== Part 97 Band Plan (ISM/LoRa Relevant) ===\n")

        if not _HAS_COMPLIANCE:
            # Show a basic reference even without the module
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
            self._wait_for_enter()
            return

        try:
            ref = Part97Reference()
            bands = ref.get_ism_relevant_bands()

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
        self._wait_for_enter()

    def _compliance_check(self):
        """Check compliance for current radio configuration."""
        clear_screen()
        print("=== Compliance Check ===\n")

        if not _HAS_COMPLIANCE:
            print("  Compliance module not available.")
            print("  File: src/amateur/compliance.py")
            self._wait_for_enter()
            return

        checker = ComplianceChecker()

        # Get current frequency from meshtasticd if possible
        freq = None
        power = None
        try:
            cli = self._get_meshtastic_cli()
            result = subprocess.run(
                [cli, '--host', 'localhost', '--get', 'lora'],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if 'frequency' in line.lower():
                        # Try to extract frequency value
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
                result = checker.check_frequency(freq, power_dbm=power)
                if result.compliant:
                    print("  \033[0;32mCOMPLIANT\033[0m - Operation within legal limits")
                else:
                    print("  \033[0;31mNON-COMPLIANT\033[0m - Review settings")
                for note in result.notes:
                    print(f"    - {note}")
            except Exception as e:
                print(f"  Check failed: {e}")
        else:
            print("  Could not determine current frequency.")
            print("  Ensure meshtasticd is running.")
            print("\n  Manual check: meshtastic --host localhost --get lora")

        print()
        self._wait_for_enter()

    def _ares_races_menu(self):
        """ARES/RACES emergency communications tools."""
        while True:
            choices = [
                ("netchecklist", "Net Checklist       Net control ops"),
                ("ics213", "ICS-213 Message     Formal traffic"),
                ("status", "Net Status          Current net info"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
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
                self._safe_call(*entry)

    def _ics213_compose(self):
        """Compose an ICS-213 formal traffic message."""
        clear_screen()
        print("=== ICS-213 General Message Form ===\n")

        if not _HAS_ARES:
            print("  ARES/RACES module not available.")
            print("  File: src/amateur/ares_races.py")
            self._wait_for_enter()
            return

        # Collect message fields via dialog
        to_field = self.dialog.inputbox("ICS-213", "To (position/name):", "")
        if not to_field:
            return

        from_field = self.dialog.inputbox("ICS-213", "From (position/name):", "")
        if not from_field:
            return

        subject = self.dialog.inputbox("ICS-213", "Subject:", "")
        if not subject:
            return

        # Priority selection
        priority_choices = [
            ("R", "Routine"),
            ("P", "Priority"),
            ("O", "Immediate"),
        ]
        priority = self.dialog.menu("Message Priority", "Select priority:", priority_choices)
        if not priority:
            priority = "R"

        message = self.dialog.inputbox("ICS-213", "Message body:", "")
        if not message:
            return

        # Display formatted message
        clear_screen()
        print("=== ICS-213 GENERAL MESSAGE ===")
        print(f"  Priority:  {priority}")
        print(f"  To:        {to_field}")
        print(f"  From:      {from_field}")
        print(f"  Subject:   {subject}")
        print(f"  Message:   {message}")
        print("=" * 40)
        print("\n  Message composed. Ready to transmit via mesh.")

        # Save option
        try:
            tools = ARESRACESTools()
            msg = tools.create_traffic_message(
                to=to_field,
                from_field=from_field,
                subject=subject,
                body=message,
                priority=priority,
            )
            if msg:
                print(f"  Saved: {msg.get('file', 'in memory')}")
        except Exception as e:
            print(f"  Save note: {e}")

        print()
        self._wait_for_enter()

    def _net_checklist(self):
        """Display net control operator checklist."""
        clear_screen()
        print("=== Net Control Operator Checklist ===\n")

        if _HAS_ARES:
            tools = ARESRACESTools()
            checklist = tools.get_net_checklist()

            for i, item in enumerate(checklist, 1):
                status = "\033[0;32m[X]\033[0m" if item.completed else "[ ]"
                print(f"  {status} {i:2d}. {item.task}")
                print(f"       {item.description}")
            print()
        else:
            # Fallback: show standard NCS checklist
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

        self._wait_for_enter()

    def _ares_net_status(self):
        """Show current ARES/RACES net status."""
        clear_screen()
        print("=== ARES/RACES Net Status ===\n")

        if not _HAS_ARES:
            print("  ARES/RACES module not available.")
            print("  File: src/amateur/ares_races.py")
        else:
            try:
                tools = ARESRACESTools()
                status = tools.get_net_status()

                if status:
                    print(f"  Net Active: {'Yes' if status.get('active') else 'No'}")
                    print(f"  NCS:        {status.get('ncs', 'Not set')}")
                    print(f"  Frequency:  {status.get('frequency', 'Not set')}")
                    print(f"  Check-ins:  {status.get('checkin_count', 0)}")
                    print(f"  Traffic:    {status.get('traffic_count', 0)} messages")
                else:
                    print("  No active net session.")
                    print("  Use 'Net Checklist' to start operations.")
            except Exception as e:
                print(f"  Status unavailable: {e}")

        print()
        self._wait_for_enter()
