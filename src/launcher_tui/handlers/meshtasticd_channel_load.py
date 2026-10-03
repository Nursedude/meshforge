"""Channel Load — read-only: how often this radio found its channel over the
25 % knee, its utilization samples, noise floor and decode-failure rate, from
meshtasticd's own journal. Logic and the meaning of every line:
utils/radio_channel_load.py.

WHY (2026-10-03): the busiest box logged `Ch. util >25%. Skip send` 155 times
in 3 h and no operator could see it (honest_failure_modes #11). What each line
proves — and what a MISSING line does not — is in the module docstring; the
first draft got that wrong and two reviewers caught it at the firmware source.

READ-ONLY BY CONSTRUCTION, local box only; never opens a radio session (#17).
"""

import logging

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

_EXPLAIN = {
    "OK": "Every utilization sample is under the 25% knee and the firmware never found it over.",
    "CROSSES KNEE": "The channel goes over 25% at times — the firmware refuses some polite\n"
                    "periodic broadcasts then. Text and relayed traffic are NOT throttled.",
    "MOSTLY OVER KNEE": "Over 25% in at least half the window's minutes (or samples). Check 'own TX' below —\n"
                        "if it is small, the load is the mesh's: hop limit, broadcast intervals, roles\n"
                        "(CLIENT_MUTE) and preset are the mesh-wide levers; lower TX power eases neighbours.",
    "OVER 40%": "Over 40% at times — impolite periodic sends (router/sensor telemetry, NodeInfo\n"
                "replies) are refused too. Text and relayed traffic are still NOT throttled.",
    "UNOBSERVABLE": "No channel evidence could be read — this is NOT a quiet channel.",
    "ABSENT": "meshtasticd is not installed here — absent by design is not a fault.",
}


class MeshtasticdChannelLoadHandler(BaseHandler):
    """Channel Load — time over the 25 % knee, utilization, decode failures."""

    handler_id = "meshtasticd_channel_load"
    menu_section = "meshtasticd"

    def menu_items(self):
        return [
            ("channel_load", "Channel Load        Time over the 25% knee, utilization, decode fails (read-only)", None),
        ]

    def execute(self, action):
        if action == "channel_load":
            self.ctx.safe_call("Channel Load", self._show)
        else:
            self.ctx.notify_unwired(action, "meshtasticd_channel_load")

    def _show(self):
        try:
            from utils.radio_channel_load import read_all
        except ImportError as exc:
            logger.error("radio_channel_load unimportable: %s", exc)
            self.ctx.dialog.msgbox("Channel Load", f"Reader unavailable (import failed): {exc}")
            return
        self.ctx.dialog.infobox("Channel Load", "Reading the meshtasticd journal ...")
        v = read_all()
        text = f"Status: {v.status}\n{_EXPLAIN.get(v.status, '')}\n\n" + "\n".join(v.lines)
        self.ctx.dialog.msgbox("Channel Load", text)
