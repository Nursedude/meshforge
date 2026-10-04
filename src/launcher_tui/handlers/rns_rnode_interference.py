"""RNode Interference — read-only: the RNode's own interference flag over a few
samples, WITH what the number is of and what it may cost. Logic and sources:
utils/rnode_interference.py.

WHY (2026-10-03): a fleet RNode reported "-71 dBm interference" most of the
time while two SDRs beside it measured nothing in the air. The bare number
reads as "the band is jammed"; this pane shows it with its meaning
(honest_failure_modes #11).

READ-ONLY BY CONSTRUCTION: `rnstatus -j` only; never opens the serial port.
Shown in a scrolling textbox — the caveat is the point and must not be cut off.
"""

import logging

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

_EXPLAIN = {
    "NONE": "No interference reported in the samples or since rnsd started.",
    "EARLIER": "None in these samples; the radio reported some earlier (time shown).",
    "ASSERTED": "The radio asserts interference at times — read the caveat before\n"
                "concluding the band is jammed.",
    "MOSTLY ASSERTED": "The radio asserts interference in most samples. Check with an external\n"
                       "receiver beside the antenna; if the air is quiet, candidates include the\n"
                       "USB cable (ferrite), the supply, the board, and the radio's own floor estimate.",
    "DOWN": "An RNode interface is down in every sample — its figures are not current.",
    "UNOBSERVABLE": "Interference could not be read — this is NOT a clean channel.",
    "ABSENT": "No RNode on this box — absent by design is not a fault.",
}


class RNodeInterferenceHandler(BaseHandler):
    """RNode Interference — the radio's flag, with its meaning and possible cost."""

    handler_id = "rns_rnode_interference"
    menu_section = "rns"

    def menu_items(self):
        return [
            ("rnode_interference", "RNode Interference  Radio's own flag, with what it means (read-only)", None),
        ]

    def execute(self, action):
        if action == "rnode_interference":
            self.ctx.safe_call("RNode Interference", self._show)
        else:
            self.ctx.notify_unwired(action, "rns_rnode_interference")

    def _show(self):
        try:
            from utils.rnode_interference import read_all
        except ImportError as exc:
            logger.error("rnode_interference unimportable: %s", exc)
            self.ctx.dialog.msgbox("RNode Interference", f"Reader unavailable (import failed): {exc}")
            return
        self.ctx.dialog.infobox("RNode Interference",
                                "Sampling rnstatus up to 5 times (at most ~35 s) ...")
        v = read_all()
        text = f"Status: {v.status}\n{_EXPLAIN.get(v.status, '')}\n\n" + "\n".join(v.lines)
        self.ctx.dialog.textbox("RNode Interference", text)
