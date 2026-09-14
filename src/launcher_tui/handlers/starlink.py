"""
Starlink dish handler — read-only uplink telemetry pane.

WHY: ``wan_path_degraded`` can say "loss beyond the ISP" but never why. On a
satellite uplink that is the whole story — obstruction and satellite handover
produce short, frequent dropouts where fibre fails rarely and long. This pane
shows the dish's own view so an operator can tell those apart.

READ-ONLY BY CONSTRUCTION. The TUI is a surface: it renders declared-vs-actual
and never acts on the network. Nothing here reboots, stows or reconfigures the
dish, even though the dish's API offers all three.

⚠️ Site-local. This describes the uplink of the box you are sitting at. A box
at another site, or behind a different uplink, will correctly report the dish
as unreachable — that is not a fault, and it is never a claim about the fleet.
"""

import logging

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class StarlinkHandler(BaseHandler):
    """Starlink — read-only dish telemetry for this box's uplink."""

    handler_id = "starlink"
    menu_section = "system"

    def menu_items(self):
        return [
            ("starlink_status", "Starlink Dish       Uplink telemetry (read-only)", None),
            ("starlink_skymap", "Starlink Sky Map    Obstruction map + bearings", None),
        ]

    def execute(self, action):
        if action == "starlink_status":
            self.ctx.safe_call("Starlink Dish", self._show_status)
        elif action == "starlink_skymap":
            self.ctx.safe_call("Starlink Sky Map", self._show_skymap)

    def _show_status(self):
        from backend import clear_screen

        clear_screen()
        print("Starlink dish telemetry")
        print("=" * 52)
        print()

        try:
            from utils.starlink_dish import format_status, get_dish_status
        except ImportError as exc:
            # First-party import failure is a real defect, not a missing
            # optional dep — say so rather than rendering an empty pane.
            logger.error("starlink_dish unimportable: %s", exc)
            print(f"Starlink reader unavailable (import failed): {exc}")
            self._pause()
            return

        print("Querying 192.168.100.1 ...")
        status = get_dish_status()
        print()
        print(format_status(status))
        print()

        if status.state == "unreachable":
            print("No dish answered. That means this box could not ASK —")
            print("it is not a statement that the uplink is healthy.")
            print("Expected on any box whose uplink is not Starlink.")
        elif status.state == "unsupported":
            print("curl is missing on this box, so the dish cannot be queried.")
        elif status.state == "malformed":
            print("The dish answered but the reply did not parse. Field")
            print("numbers are firmware-coupled — see utils/starlink_dish.py.")
        elif status.active_alerts:
            print("Dish is reporting active alerts (listed above).")

        self._pause()

    def _show_skymap(self):
        from backend import clear_screen

        clear_screen()
        try:
            from utils.starlink_dish import (get_obstruction_map,
                                             render_obstruction_map)
        except ImportError as exc:
            logger.error("starlink_dish unimportable: %s", exc)
            print(f"Sky map unavailable (import failed): {exc}")
            self._pause()
            return

        print("Fetching obstruction map (this takes a few seconds) ...")
        # The map is ~60 KB on the wire and the dish is slower to answer than
        # for status, so it gets a longer budget than DEFAULT_TIMEOUT.
        sky = get_obstruction_map(timeout=20)
        clear_screen()
        print(render_obstruction_map(sky))
        print()
        if sky.state == "unreachable":
            print("No dish answered — this box could not ASK. Not a claim")
            print("that the sky is clear.")
        self._pause()

    def _pause(self):
        try:
            self.ctx.wait_for_enter("\nPress Enter to return to menu...")
        except KeyboardInterrupt:
            print()
