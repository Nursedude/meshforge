"""Delivery Handler — the domain's END on one screen: did messages arrive?

Renders ``utils.delivery_view`` for THIS box: the gateway's own delivery
record (windowed confirmation rate — the same count the stall probe judges —
plus lifetime totals and drops), its queue and dead letters, and the newest
synth / propagation soak round trips. Every number carries its source file
and age; a source that cannot be read says UNKNOWN and prints no number; an
organ absent by design (no gateway, no soak timer) says inert.

"Delivery legs" (BIC-2) is the per-route view of the same published record:
counts per ``<src>><dst>`` transport over 15m / 1h / 24h. It COUNTS and
judges no rate — the verdict stays on the Delivery screen.

⚠️ READ-ONLY. It reads files the gateway and the soak timers already publish
— never the delivery DB, never the radio, never a service. The TUI stays a
surface: acting on delivery stays in scripts run knowingly.
"""

import logging
import sys
from pathlib import Path

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parent.parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


class DeliveryHandler(BaseHandler):
    """Did messages arrive on this box — with source and age per number."""

    handler_id = "delivery"
    menu_section = "dashboard"

    def menu_items(self):
        return [
            ("delivery", "Delivery            did messages arrive? (source+age)", None),
            ("delivery_legs", "Delivery legs       per route: 15m / 1h / 24h counts", None),
        ]

    def execute(self, action):
        if action == "delivery":
            self._show()
        elif action == "delivery_legs":
            self._show_legs()

    def _show(self):
        try:
            from utils.delivery_view import gather, render
            text = render(gather())
        except Exception as e:  # the screen must say it could not see
            logger.warning("delivery view failed: %s", e, exc_info=True)
            text = ("Delivery — UNKNOWN\n\nThe delivery sources on this box "
                    f"could not be read:\n  {type(e).__name__}: {e}\n\n"
                    "UNKNOWN is not a pass. This screen never changes anything.")
        self.ctx.dialog.msgbox("Delivery", text)

    def _show_legs(self):
        try:
            from utils.delivery_view import render_route_legs
            text = render_route_legs()
        except Exception as e:  # the screen must say it could not see
            logger.warning("delivery legs view failed: %s", e, exc_info=True)
            text = ("Delivery legs — UNKNOWN\n\nThe gateway's delivery record "
                    f"could not be read:\n  {type(e).__name__}: {e}\n\n"
                    "UNKNOWN is not a pass. This screen never changes anything.")
        # textbox, not msgbox: routes x 3 windows scroll past one screen.
        self.ctx.dialog.textbox("Delivery legs", text)
