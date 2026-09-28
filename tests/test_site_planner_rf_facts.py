"""TUI audit finding 4 (2026-09-27): the Site Planner's reference text gave
HAMs wrong RF facts — "US 906.875 (Ch 0)" (that is hashed slot 20; channel
0 means "hash the name"), EU 433 at 433.175 MHz (the firmware tunes
433.875), and coax losses ~2x optimistic vs utils.rf. Those lines are now
GENERATED from the firmware slot math (utils.meshtastic_modem) and the
link-budget table (utils.rf); these tests render the real dialogs.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))
sys.path.insert(0, os.path.dirname(__file__))

from handler_test_utils import make_handler_context
from utils.meshtastic_modem import channel_centre_mhz
from utils.rf import CABLE_LOSS_DB_PER_M


def _render(method):
    from handlers.site_planner import SitePlannerHandler
    h = SitePlannerHandler.__new__(SitePlannerHandler)
    h.ctx = make_handler_context()
    getattr(h, method)()
    boxes = [c for c in h.ctx.dialog.calls if c[0] == "msgbox"]
    assert boxes, f"{method} showed nothing"
    return boxes[-1][1][1]


def test_frequency_reference_matches_firmware_math():
    text = _render("_frequency_reference")
    for region, label in (("US", "US"), ("EU_868", "EU 868"), ("EU_433", "EU 433")):
        f, slot, n = channel_centre_mhz("LONG_FAST", 0, region)
        assert f"{label}: {f:.3f} MHz (LongFast, slot {slot} of {n})" in text
    assert "433.175" not in text          # the old wrong EU 433 default
    assert "(Ch 0)" not in text           # 906.875 is slot 20, not channel 0
    assert "906.875 MHz (LongFast, slot 20 of 104)" in text   # pinned value


def test_antenna_cable_losses_match_rf_table():
    text = _render("_antenna_guidelines")
    for key, label in (("rg58", "RG58"), ("rg8x", "RG8X"),
                       ("lmr240", "LMR-240"), ("lmr400", "LMR-400")):
        m = re.search(rf"- {re.escape(label)}: ~([0-9.]+) dB", text)
        assert m, f"{label} line missing"
        assert float(m.group(1)) == round(CABLE_LOSS_DB_PER_M[key] * 10, 1)
    assert "~0.7 dB" not in text          # the old 2x-optimistic LMR-400 figure


def test_no_unformatted_placeholders_leak():
    for method in ("_frequency_reference", "_antenna_guidelines"):
        text = _render(method)
        assert "{" not in text and "}" not in text, method
