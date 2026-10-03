"""TX Power Truth — read-only: what the radio SAVED vs what meshtasticd APPLIED
vs a change still PENDING in an uncommitted settings transaction.

WHY (2026-10-03): a tx_power change made from the meshtasticd web client sat
in an open transaction — in RAM, never applied to the radio, never saved —
while `meshtastic --get` read the new value back and said "done". The next
restart silently reverted it. Only an assistant reading the journal found it;
an operator at this menu could not (honest_failure_modes #11). This pane is
that witness. Logic and the meaning of every leg: utils/radio_txpower_truth.py.

READ-ONLY BY CONSTRUCTION, local box only. It never opens a radio session
(#17) and never changes a setting — "Set TX Power" lives in the Radio menu.
"""

import logging

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

# Advice verified against the pinned firmware + CLI (2026-10-03): only an
# explicit commit closes the firmware's edit transaction; a single-field
# `--set` does NOT (it would just add another delayed save).
_EXPLAIN = {
    "OK": "Saved and applied agree; no change is pending.",
    "DRIFT": "Saved is LOWER than applied — the next restart lowers the transmit power.",
    "CLAMPED": "Saved is above the region/licence limit; the firmware applies the limit\n"
               "on every start. Not a fault — the transmit power will not change.",
    "DEFAULT": "Saved tx_power is 0: the firmware uses the region limit.",
    "PENDING": "A settings edit transaction is OPEN (often left by the web client).\n"
               "Every change made since — ANY setting, not only tx_power — is in RAM only:\n"
               "NOT on the air, NOT saved. A plain `--set` will NOT fix it. Either:\n"
               "  commit all of them:  meshtastic --host localhost --commit-edit\n"
               "                       (saves + applies everything staged; meshtasticd reboots)\n"
               "  or discard all:      restart meshtasticd (the staged changes are lost)",
    "LOST": "An uncommitted change was thrown away by a reboot — the radio runs the saved value.",
    "FAILED": "The saved prefs file could not be decoded — it may be corrupt. Not a healthy reading.",
    "ABSENT": "meshtasticd is not installed here — absent by design is not a fault.",
    "UNOBSERVABLE": "Part of the picture could not be read — this is NOT a healthy reading.",
}


class MeshtasticdTxPowerHandler(BaseHandler):
    """TX Power Truth — saved vs applied vs pending (read-only)."""

    handler_id = "meshtasticd_txpower"
    menu_section = "meshtasticd"

    def menu_items(self):
        return [
            ("txpower_truth", "TX Power Truth      Saved vs applied vs pending (read-only)", None),
        ]

    def execute(self, action):
        if action == "txpower_truth":
            self.ctx.safe_call("TX Power Truth", self._show)
        else:
            self.ctx.notify_unwired(action, "meshtasticd_txpower")

    def _show(self):
        try:
            from utils.radio_txpower_truth import read_all
        except ImportError as exc:
            # First-party import failure is a defect, not a missing optional dep.
            logger.error("radio_txpower_truth unimportable: %s", exc)
            self.ctx.dialog.msgbox("TX Power Truth", f"Reader unavailable (import failed): {exc}")
            return
        self.ctx.dialog.infobox("TX Power Truth", "Reading the service, journal and prefs file ...")
        v = read_all()
        text = (f"Status: {v.status}\n{_EXPLAIN.get(v.status, '')}\n\n"
                + "\n".join(v.lines))
        self.ctx.dialog.msgbox("TX Power Truth", text)
