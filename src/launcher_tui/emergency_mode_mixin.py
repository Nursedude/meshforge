"""
Emergency Mode Mixin — simplified EMCOMM interface for field operators.

Provides a stripped-down UI focused on essential communication tasks:
- Send messages (broadcast and direct)
- View node status (who's online)
- Check position/GPS
- View recent messages

Designed for high-stress field use: minimal menus, clear confirmations,
no unnecessary options. Accessible from the main menu.
"""

import subprocess
import sys
import time
import logging
from typing import Optional
from backend import clear_screen
from plugins.eas_alerts import EASAlertsPlugin

logger = logging.getLogger(__name__)

# Emergency broadcast prefix for EMCOMM messages
EMCOMM_PREFIX = "[EMCOMM] "


class EmergencyModeMixin:
    """Simplified EMCOMM interface for field operations."""

    def _get_emcomm_cli(self) -> str:
        """Get meshtastic CLI path with emergency-safe error handling.

        Returns the CLI path or 'meshtastic' as fallback.
        Emergency mode must never crash on CLI lookup.
        """
        try:
            return self._get_meshtastic_cli()
        except Exception as e:
            logger.warning(f"CLI lookup failed in EMCOMM mode: {e}")
            return 'meshtastic'  # Best-effort fallback

    def _emergency_mode(self):
        """Run emergency mode — simplified menu for field ops.

        This mode is designed for high-stress field use.
        Every action is individually protected to ensure the menu
        always returns, even if individual operations fail.
        """
        while True:
            choices = [
                ("send", "SEND MESSAGE (broadcast)"),
                ("direct", "SEND DIRECT (to node)"),
                ("status", "WHO IS ONLINE"),
                ("msgs", "RECENT MESSAGES"),
                ("pos", "MY POSITION"),
                ("sos", "SOS BEACON (repeating)"),
                ("alerts", "WEATHER/EAS ALERTS"),
                ("exit", "EXIT Emergency Mode"),
            ]

            choice = self.dialog.menu(
                "EMERGENCY MODE",
                "EMCOMM Quick Actions — field operations:",
                choices
            )

            if choice is None or choice == "exit":
                break

            # Emergency mode wraps every action individually.
            # Field operators must never be dumped to a traceback.
            dispatch = {
                "send": ("EMCOMM Broadcast", self._emcomm_broadcast),
                "direct": ("EMCOMM Direct Message", self._emcomm_direct),
                "status": ("EMCOMM Node Status", self._emcomm_status),
                "msgs": ("EMCOMM Messages", self._emcomm_messages),
                "pos": ("EMCOMM Position", self._emcomm_position),
                "sos": ("EMCOMM SOS Beacon", self._emcomm_sos_beacon),
                "alerts": ("EMCOMM EAS Alerts", self._emcomm_eas_alerts),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _emcomm_broadcast(self):
        """Send a broadcast message to all nodes."""
        msg = self.dialog.inputbox(
            "BROADCAST MESSAGE",
            "Enter message to send to ALL nodes:\n"
            "(Will be prefixed with [EMCOMM])",
            ""
        )

        if msg is None or msg.strip() == "":
            return

        full_msg = f"{EMCOMM_PREFIX}{msg.strip()}"

        # Confirm before sending
        if not self.dialog.yesno(
            "Confirm Broadcast",
            f"Send to ALL nodes?\n\n"
            f"Message: {full_msg}\n\n"
            f"This will transmit on all channels.",
            default_no=True
        ):
            return

        clear_screen()
        print(f"Broadcasting: {full_msg}\n")
        try:
            cli_path = self._get_emcomm_cli()
            subprocess.run(
                [cli_path, '--sendtext', full_msg],
                timeout=30
            )
            print("\nMessage sent.")
        except FileNotFoundError:
            print("ERROR: meshtastic CLI not available.")
        except subprocess.TimeoutExpired:
            print("ERROR: Send timed out. Check radio connection.")
        except Exception as e:
            print(f"ERROR: {e}")

        self._wait_for_enter()

    def _emcomm_direct(self):
        """Send a direct message to a specific node."""
        # First get the destination
        dest = self.dialog.inputbox(
            "DIRECT MESSAGE",
            "Enter destination node ID (e.g., !abc12345)\n"
            "or short name:",
            ""
        )

        if dest is None or dest.strip() == "":
            return

        # Then get the message
        msg = self.dialog.inputbox(
            "MESSAGE TEXT",
            f"Message to {dest.strip()}:\n"
            "(Will be prefixed with [EMCOMM])",
            ""
        )

        if msg is None or msg.strip() == "":
            return

        full_msg = f"{EMCOMM_PREFIX}{msg.strip()}"
        dest_clean = dest.strip()

        # Validate node ID format: !hex or ^all
        import re
        if not re.match(r'^(![\da-fA-F]{6,16}|\^all)$', dest_clean):
            self.dialog.msgbox(
                "Invalid Destination",
                f"'{dest_clean}' is not a valid node ID.\n"
                "Use format: !abc12345 or ^all"
            )
            return

        # Confirm
        if not self.dialog.yesno(
            "Confirm Direct Message",
            f"Send to: {dest_clean}\n"
            f"Message: {full_msg}",
            default_no=True
        ):
            return

        clear_screen()
        print(f"Sending to {dest_clean}: {full_msg}\n")
        try:
            cli_path = self._get_emcomm_cli()
            subprocess.run(
                [cli_path, '--dest', dest_clean, '--sendtext', full_msg],
                timeout=30
            )
            print("\nMessage sent.")
        except FileNotFoundError:
            print("ERROR: meshtastic CLI not available.")
        except subprocess.TimeoutExpired:
            print("ERROR: Send timed out. Check radio connection.")
        except Exception as e:
            print(f"ERROR: {e}")

        self._wait_for_enter()

    def _emcomm_status(self):
        """Show which nodes are currently online."""
        clear_screen()
        print("=== NODES ONLINE ===\n")
        try:
            cli_path = self._get_emcomm_cli()
            subprocess.run(
                [cli_path, '--nodes'],
                timeout=30
            )
        except FileNotFoundError:
            print("ERROR: meshtastic CLI not available.")
            print("Install: pipx install meshtastic[cli]")
        except subprocess.TimeoutExpired:
            print("ERROR: Command timed out.")
        except Exception as e:
            print(f"ERROR: {e}")

        print()
        self._wait_for_enter("Press Enter to continue...")

    def _emcomm_messages(self):
        """Show recent messages from the mesh."""
        clear_screen()
        print("=== RECENT MESSAGES ===\n")

        # Try to get messages from meshtasticd journal
        try:
            result = subprocess.run(
                ['journalctl', '-u', 'meshtasticd', '--no-pager',
                 '-n', '100', '--output', 'cat'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                # Filter for message lines
                lines = result.stdout.split('\n')
                msg_lines = [
                    l for l in lines
                    if 'received' in l.lower() or
                       'text' in l.lower() or
                       'message' in l.lower()
                ]
                if msg_lines:
                    for line in msg_lines[-20:]:
                        print(f"  {line.strip()}")
                else:
                    print("  No recent messages found in logs.")
            else:
                print("  Could not read meshtasticd logs.")
        except FileNotFoundError:
            print("  journalctl not available.")
        except subprocess.TimeoutExpired:
            print("  Log read timed out.")
        except Exception as e:
            print(f"  Error: {e}")

        print()
        self._wait_for_enter("Press Enter to continue...")

    def _emcomm_position(self):
        """Show current GPS position."""
        clear_screen()
        print("=== MY POSITION ===\n")
        try:
            cli_path = self._get_emcomm_cli()
            subprocess.run(
                [cli_path, '--get', 'position'],
                timeout=30
            )
        except FileNotFoundError:
            print("ERROR: meshtastic CLI not available.")
        except subprocess.TimeoutExpired:
            print("ERROR: Command timed out.")
        except Exception as e:
            print(f"ERROR: {e}")

        print()
        self._wait_for_enter("Press Enter to continue...")

    def _emcomm_sos_beacon(self):
        """Send repeating SOS beacon messages."""
        # Safety confirmation
        if not self.dialog.yesno(
            "SOS BEACON",
            "This will send repeating SOS messages every 60 seconds.\n\n"
            "USE ONLY IN REAL EMERGENCIES.\n\n"
            "Press Ctrl+C to stop.\n\n"
            "Start SOS beacon?",
            default_no=True
        ):
            return

        # Get operator info for beacon
        info = self.dialog.inputbox(
            "BEACON INFO",
            "Optional: Your callsign/name and situation:\n"
            "(Press Enter for generic SOS)",
            ""
        )

        if info is None:
            return

        beacon_msg = f"{EMCOMM_PREFIX}SOS"
        if info.strip():
            beacon_msg += f" - {info.strip()}"

        clear_screen()
        print(f"=== SOS BEACON ACTIVE ===\n")
        print(f"Message: {beacon_msg}")
        print(f"Interval: 60 seconds")
        print(f"Press Ctrl+C to stop\n")

        count = 0
        try:
            while True:
                count += 1
                print(f"  [{count}] Sending beacon... ", end="", flush=True)
                try:
                    cli_path = self._get_emcomm_cli()
                    result = subprocess.run(
                        [cli_path, '--sendtext', beacon_msg],
                        capture_output=True, timeout=30
                    )
                    if result.returncode == 0:
                        print("SENT")
                    else:
                        print("FAILED (radio error)")
                except FileNotFoundError:
                    print("FAILED (meshtastic not found)")
                    break
                except subprocess.TimeoutExpired:
                    print("TIMEOUT")

                # Wait 60 seconds between beacons
                print(f"  Next beacon in 60s (Ctrl+C to stop)...")
                time.sleep(60)

        except KeyboardInterrupt:
            print(f"\n\nSOS Beacon stopped after {count} transmission(s).")

        self._wait_for_enter()

    def _emcomm_eas_alerts(self):
        """Check weather and emergency alerts — field-safe implementation.

        Uses the EAS Alerts plugin to fetch current NOAA/NWS weather alerts.
        Designed for field operations: never crashes, always returns to menu.
        """
        clear_screen()
        print("=== WEATHER / EAS ALERTS ===\n")

        try:
            plugin = EASAlertsPlugin()

            print("Checking NOAA weather alerts...")
            alerts = plugin.get_weather_alerts()

            if not alerts:
                print("\n  No active weather alerts for your area.")
                print("  (Configure location in MeshForge Settings)")
            else:
                print(f"\n  {len(alerts)} active alert(s):\n")
                for i, alert in enumerate(alerts[:10], 1):
                    severity = getattr(alert, 'severity', 'Unknown')
                    headline = getattr(alert, 'headline', str(alert))
                    # Truncate long headlines for terminal display
                    if len(headline) > 70:
                        headline = headline[:67] + "..."
                    print(f"  {i}. [{severity}] {headline}")

            # Also check volcano alerts if available
            print("\nChecking USGS volcano alerts...")
            try:
                volcano_alerts = plugin.get_volcano_alerts()
                if volcano_alerts:
                    print(f"\n  {len(volcano_alerts)} volcano alert(s):")
                    for alert in volcano_alerts[:5]:
                        name = getattr(alert, 'volcano_name', str(alert))
                        level = getattr(alert, 'alert_level', 'Unknown')
                        print(f"  - [{level}] {name}")
                else:
                    print("  No active volcano alerts.")
            except Exception:
                print("  Volcano alert check unavailable.")

        except Exception as e:
            print(f"  Alert check failed: {e}")
            print("  (Check network connectivity)")

        print()
        self._wait_for_enter("Press Enter to continue...")
