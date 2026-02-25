"""
Tactical Operations Mixin — SITREP, TASK, CHECKIN, zones, QR, map, ATAK export.

Wires the src/tactical/ package to TUI menus.
Extracted as a mixin to keep main.py under 1,500 lines.
"""

import logging
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from backend import clear_screen
from utils.safe_import import safe_import
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)

# Import tactical modules (first-party — always available)
from tactical.models import (
    TacticalMessage, TacticalType, TacticalPriority, EncryptionMode,
    SITREP, TaskAssignment, CheckIn, ZoneMarking, Resource,
    Mission, Event, Asset, generate_message_id,
)
from tactical.x1_codec import encode, decode, is_x1
from tactical.chunker import chunk, TRANSPORT_LIMITS
from tactical.compliance import get_compliance_badge, validate_ham_compliance
from tactical.timeline import TacticalTimeline

# Optional: QR code support
(_encode_qr_terminal, _generate_checkin_qr, _is_qr_available,
 _HAS_QR) = safe_import(
    'tactical.qr_transport',
    'encode_qr_terminal', 'generate_checkin_qr', 'is_qr_available',
)

# Optional: Tactical map support (requires folium)
(_generate_tactical_map, _export_kml, _export_cot_xml,
 _is_map_available, _HAS_MAP) = safe_import(
    'tactical.tactical_map',
    'generate_tactical_map', 'export_kml', 'export_cot_xml',
    'is_map_available',
)


class TacticalOpsMixin:
    """TUI mixin for tactical operations."""

    def _get_timeline(self) -> TacticalTimeline:
        """Get or create the tactical timeline singleton."""
        if not hasattr(self, '_tactical_timeline'):
            self._tactical_timeline = TacticalTimeline()
        return self._tactical_timeline

    def _get_tactical_settings(self) -> dict:
        """Get tactical settings (encryption mode, callsign)."""
        if not hasattr(self, '_tactical_settings'):
            self._tactical_settings = {
                'encryption_mode': 'C',  # Default CLEAR
                'callsign': '',
            }
        return self._tactical_settings

    def _tactical_ops_menu(self):
        """Tactical Operations — structured messages, map, QR, ATAK."""
        while True:
            settings = self._get_tactical_settings()
            mode_badge = get_compliance_badge(
                EncryptionMode(settings['encryption_mode'])
            )

            choices = [
                ("sitrep", "Send SITREP       Situation report"),
                ("task", "Send TASK         Work assignment"),
                ("checkin", "Check-In          Position report"),
                ("zone", "Mark Zone         Geographic area"),
                ("resource", "Resource          Equipment/supply"),
                ("qr", "QR Code           Generate check-in QR"),
                ("map", "Tactical Map      View zones & markers"),
                ("export", "Export            KML/CoT for ATAK"),
                ("timeline", "Timeline          View event log"),
                ("mode", f"Mode {mode_badge:10s}  CLEAR/SECURE setting"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Tactical Operations",
                f"Structured messaging {mode_badge}:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "sitrep": ("Send SITREP", self._tactical_send_sitrep),
                "task": ("Send TASK", self._tactical_send_task),
                "checkin": ("Check-In", self._tactical_checkin),
                "zone": ("Mark Zone", self._tactical_mark_zone),
                "resource": ("Resource", self._tactical_send_resource),
                "qr": ("QR Code", self._tactical_qr_generate),
                "map": ("Tactical Map", self._tactical_map_view),
                "export": ("Export", self._tactical_export),
                "timeline": ("Timeline", self._tactical_timeline_view),
                "mode": ("Compliance Mode", self._tactical_compliance_mode),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _tactical_send_sitrep(self):
        """Send a SITREP (Situation Report)."""
        situation = self.dialog.inputbox(
            "SITREP — Situation",
            "Describe the current situation:"
        )
        if not situation:
            return

        actions = self.dialog.inputbox(
            "SITREP — Actions Taken",
            "Actions taken (or leave empty):"
        ) or ""

        resources = self.dialog.inputbox(
            "SITREP — Resources Needed",
            "Resources needed (or leave empty):"
        ) or ""

        sitrep = SITREP(
            situation=situation,
            actions_taken=actions,
            resources_needed=resources,
        )

        self._send_tactical_message(TacticalType.SITREP, sitrep.to_dict())

    def _tactical_send_task(self):
        """Send a TASK (work assignment)."""
        description = self.dialog.inputbox(
            "TASK — Description",
            "Task description:"
        )
        if not description:
            return

        assignee = self.dialog.inputbox(
            "TASK — Assignee",
            "Assigned to (callsign/name):"
        ) or ""

        task = TaskAssignment(
            description=description,
            assignee=assignee,
            status="assigned",
        )

        self._send_tactical_message(TacticalType.TASK, task.to_dict())

    def _tactical_checkin(self):
        """Send a CHECK-IN (position report)."""
        settings = self._get_tactical_settings()
        default_callsign = settings.get('callsign', '')

        callsign = self.dialog.inputbox(
            "CHECK-IN — Callsign",
            "Your callsign or identifier:",
            init=default_callsign,
        )
        if not callsign:
            return

        # Save callsign for next time
        settings['callsign'] = callsign

        status_choices = [
            ("ok", "OK                All clear"),
            ("needs_help", "Needs Help        Requesting assistance"),
            ("injured", "Injured           Medical attention needed"),
            ("evacuating", "Evacuating        Leaving area"),
        ]
        status = self.dialog.menu("CHECK-IN — Status", "Your status:", status_choices)
        if not status:
            status = "ok"

        count_str = self.dialog.inputbox(
            "CHECK-IN — Personnel",
            "Number of personnel:",
            init="1",
        )
        try:
            personnel_count = int(count_str) if count_str else 1
        except ValueError:
            personnel_count = 1

        checkin = CheckIn(
            callsign=callsign,
            status=status,
            personnel_count=personnel_count,
        )

        self._send_tactical_message(TacticalType.CHECKIN, checkin.to_dict())

    def _tactical_mark_zone(self):
        """Mark a geographic zone."""
        name = self.dialog.inputbox("ZONE — Name", "Zone name:")
        if not name:
            return

        type_choices = [
            ("hazard", "Hazard            Dangerous area"),
            ("safe", "Safe              Cleared/safe area"),
            ("staging", "Staging           Staging area"),
            ("exclusion", "Exclusion         No-go zone"),
            ("operations", "Operations        Active ops area"),
        ]
        zone_type = self.dialog.menu("ZONE — Type", "Zone type:", type_choices)
        if not zone_type:
            return

        lat_str = self.dialog.inputbox("ZONE — Center Latitude", "Latitude (decimal):")
        lon_str = self.dialog.inputbox("ZONE — Center Longitude", "Longitude (decimal):")

        try:
            center_lat = float(lat_str) if lat_str else 0.0
            center_lon = float(lon_str) if lon_str else 0.0
        except ValueError:
            self.dialog.msgbox("Error", "Invalid coordinates.")
            return

        radius_str = self.dialog.inputbox(
            "ZONE — Radius",
            "Radius in meters (0 for point marker):",
            init="100",
        )
        try:
            radius_m = float(radius_str) if radius_str else 0.0
        except ValueError:
            radius_m = 0.0

        zone = ZoneMarking(
            name=name,
            zone_type=zone_type,
            center_lat=center_lat,
            center_lon=center_lon,
            radius_m=radius_m,
        )

        self._send_tactical_message(TacticalType.ZONE, zone.to_dict())

    def _tactical_send_resource(self):
        """Send a RESOURCE message (equipment/supply tracking)."""
        name = self.dialog.inputbox("RESOURCE — Name", "Resource name:")
        if not name:
            return

        type_choices = [
            ("medical", "Medical           First aid, medicine"),
            ("comms", "Communications    Radios, antennas"),
            ("transport", "Transport         Vehicles"),
            ("shelter", "Shelter           Tents, buildings"),
            ("food", "Food              Food supplies"),
            ("water", "Water             Water supplies"),
        ]
        resource_type = self.dialog.menu(
            "RESOURCE — Type", "Resource type:", type_choices
        )
        if not resource_type:
            return

        qty_str = self.dialog.inputbox("RESOURCE — Quantity", "Quantity:", init="1")
        try:
            quantity = int(qty_str) if qty_str else 1
        except ValueError:
            quantity = 1

        resource = Resource(
            name=name,
            resource_type=resource_type,
            quantity=quantity,
            status="available",
        )

        self._send_tactical_message(TacticalType.RESOURCE, resource.to_dict())

    def _tactical_qr_generate(self):
        """Generate a QR code for check-in."""
        if not _HAS_QR:
            self.dialog.msgbox(
                "QR Not Available",
                "QR code generation requires 'qrcode' package.\n"
                "Install with: pip install qrcode"
            )
            return

        if not _is_qr_available():
            self.dialog.msgbox(
                "QR Not Available",
                "QR code library loaded but not functional."
            )
            return

        settings = self._get_tactical_settings()
        callsign = self.dialog.inputbox(
            "QR Check-In",
            "Callsign for QR code:",
            init=settings.get('callsign', ''),
        )
        if not callsign:
            return

        settings['callsign'] = callsign

        clear_screen()
        print(f"=== QR Check-In: {callsign} ===\n")

        try:
            qr_str = _generate_checkin_qr(callsign)
            print(qr_str)
            print(f"\nScan this QR to check in as: {callsign}")
            print("QR contains an X1 CHECKIN message.")
        except Exception as e:
            print(f"Error generating QR: {e}")

        self._wait_for_enter()

    def _tactical_map_view(self):
        """View tactical map with zones and check-ins."""
        if not _HAS_MAP or not _is_map_available():
            self.dialog.msgbox(
                "Map Not Available",
                "Tactical map requires 'folium' package.\n"
                "Install with: pip install folium"
            )
            return

        timeline = self._get_timeline()
        zones = timeline.get_active_zones()
        checkins = timeline.get_recent_checkins(minutes=120)

        if not zones and not checkins:
            self.dialog.msgbox(
                "No Data",
                "No tactical zones or check-ins recorded yet.\n"
                "Send some ZONE or CHECKIN messages first."
            )
            return

        # Generate map to temp file
        output_dir = get_real_user_home() / ".config" / "meshforge" / "maps"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "tactical_map.html"

        try:
            _generate_tactical_map(zones, checkins, [], output_path)
            self.dialog.msgbox(
                "Tactical Map Generated",
                f"Map saved to: {output_path}\n\n"
                f"Zones: {len(zones)}\n"
                f"Check-ins: {len(checkins)}\n\n"
                "Open in a browser to view."
            )
        except Exception as e:
            self.dialog.msgbox("Map Error", f"Failed to generate map: {e}")

    def _tactical_export(self):
        """Export tactical data as KML or CoT XML."""
        choices = [
            ("kml", "KML               Google Earth / ATAK"),
            ("cot", "CoT XML           TAK ecosystem"),
            ("back", "Back"),
        ]

        choice = self.dialog.menu("Export Format", "Choose export format:", choices)
        if choice is None or choice == "back":
            return

        timeline = self._get_timeline()
        output_dir = get_real_user_home() / ".config" / "meshforge" / "exports"
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

        if choice == "kml":
            if not _HAS_MAP:
                self.dialog.msgbox("Not Available", "KML export requires tactical_map module.")
                return
            zones = timeline.get_active_zones()
            output_path = output_dir / f"tactical_{timestamp_str}.kml"
            try:
                _export_kml(zones, [], output_path)
                self.dialog.msgbox(
                    "KML Exported",
                    f"File: {output_path}\n"
                    f"Zones: {len(zones)}\n\n"
                    "Import into Google Earth or ATAK."
                )
            except Exception as e:
                self.dialog.msgbox("Export Error", f"KML export failed: {e}")

        elif choice == "cot":
            if not _HAS_MAP:
                self.dialog.msgbox("Not Available", "CoT export requires tactical_map module.")
                return
            checkins = timeline.get_recent_checkins(minutes=120)
            output_path = output_dir / f"cot_{timestamp_str}.xml"
            try:
                _export_cot_xml(checkins, output_path)
                self.dialog.msgbox(
                    "CoT XML Exported",
                    f"File: {output_path}\n"
                    f"Check-ins: {len(checkins)}\n\n"
                    "Import into ATAK or WinTAK."
                )
            except Exception as e:
                self.dialog.msgbox("Export Error", f"CoT export failed: {e}")

    def _tactical_timeline_view(self):
        """View tactical event timeline."""
        timeline = self._get_timeline()
        events = timeline.query(limit=20)

        if not events:
            self.dialog.msgbox("Timeline", "No tactical events recorded yet.")
            return

        clear_screen()
        print("=== Tactical Timeline (Recent 20) ===\n")

        for event in events:
            badge = get_compliance_badge(event.encryption_mode)
            time_str = event.timestamp.strftime("%H:%M:%S")
            priority = event.priority.name
            print(
                f"[{time_str}] {badge} {event.tactical_type.name:10s} "
                f"({priority}) from {event.sender_id}"
            )
            # Show key content fields
            for key, value in list(event.content.items())[:3]:
                if isinstance(value, str) and value:
                    print(f"  {key}: {value[:60]}")
            print()

        total = timeline.get_count()
        print(f"--- Showing {len(events)} of {total} total events ---")

        self._wait_for_enter()

    def _tactical_compliance_mode(self):
        """Set CLEAR/SECURE encryption mode."""
        settings = self._get_tactical_settings()
        current = settings.get('encryption_mode', 'C')

        choices = [
            ("C", f"CLEAR             Ham-legal, Part 97 {'[current]' if current == 'C' else ''}"),
            ("S", f"SECURE            AES-256-GCM {'[current]' if current == 'S' else ''}"),
        ]

        choice = self.dialog.menu(
            "Compliance Mode",
            "Select encryption mode for tactical messages:",
            choices
        )

        if choice and choice in ('C', 'S'):
            settings['encryption_mode'] = choice
            mode_name = "CLEAR (ham-legal)" if choice == 'C' else "SECURE (encrypted)"
            self.dialog.msgbox("Mode Set", f"Tactical messages will use: {mode_name}")

    # --- Internal helpers ---

    def _send_tactical_message(self, tactical_type: TacticalType, content: dict):
        """Create, encode, display, and record a tactical message."""
        settings = self._get_tactical_settings()
        mode = EncryptionMode(settings.get('encryption_mode', 'C'))
        sender = settings.get('callsign', '')

        msg = TacticalMessage(
            tactical_type=tactical_type,
            priority=TacticalPriority.ROUTINE,
            encryption_mode=mode,
            sender_id=sender,
            content=content,
        )

        # Encode to X1
        x1_string = encode(msg)

        # Validate ham compliance
        is_compliant = validate_ham_compliance(msg)
        badge = get_compliance_badge(mode)

        # Record to timeline
        timeline = self._get_timeline()
        timeline.record(msg)

        # Show result
        clear_screen()
        print(f"=== {tactical_type.name} Sent ===\n")
        print(f"ID:       {msg.id}")
        print(f"Type:     {tactical_type.name}")
        print(f"Mode:     {badge}")
        print(f"Sender:   {sender or '(not set)'}")
        print(f"HAM OK:   {'Yes' if is_compliant else 'No'}")
        print(f"\nX1 Wire:  {x1_string[:80]}{'...' if len(x1_string) > 80 else ''}")
        print(f"Size:     {len(x1_string)} bytes")

        # Show chunking info for different transports
        print("\nChunking:")
        for transport, limit in sorted(TRANSPORT_LIMITS.items()):
            chunks = chunk(x1_string, transport)
            print(f"  {transport:12s}: {len(chunks)} chunk(s) (limit {limit}B)")

        print("\nMessage recorded to timeline.")
        self._wait_for_enter()
