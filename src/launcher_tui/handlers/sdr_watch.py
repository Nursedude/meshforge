"""SDR Watch Handler — what the Airspy timer recorded, on one screen.

Renders ``utils.sdr_view`` for THIS box: per-window witness (age of the
newest OK capture — never merely the newest row), the latest fleet run's
interference classes with recurrence, class B's floor deltas, the hourly
adjacent-band pass, and the blind spots every time.

⚠️ READ-ONLY. It reads the JSONL the ``meshforge-sdr`` user timer writes and
never opens the Airspy — the device belongs to the timer. Distinct from the
older "SDR Monitor" (SoapySDR live capture, which falls back to a MOCK
backend where SoapySDR is absent).
"""

import logging
import sys
from pathlib import Path

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parent.parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


class SDRWatchHandler(BaseHandler):
    """Interference the Airspy timer recorded — with witness and blind spots."""

    handler_id = "sdr_watch"
    menu_section = "rf_sdr"

    def menu_items(self):
        return [
            ("sdr_watch", "Interference Watch  what the Airspy timer saw (read-only)", None),
        ]

    def execute(self, action):
        if action == "sdr_watch":
            self._show()

    def _show(self):
        try:
            from utils.sdr_view import load, render
            state, rows = load()
            text = render(state, rows)
        except Exception as e:  # the screen must say it could not see
            logger.warning("sdr watch view failed: %s", e, exc_info=True)
            text = ("SDR Interference Watch — UNKNOWN\n\nThe SDR data on this box "
                    f"could not be read:\n  {type(e).__name__}: {e}\n\n"
                    "UNKNOWN is not a pass. This screen never changes anything.")
        self.ctx.dialog.textbox("Interference Watch", text)
