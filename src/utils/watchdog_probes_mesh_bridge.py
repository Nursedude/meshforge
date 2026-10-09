"""Watchdog probe: a DECLARED mesh_bridge leg reads disconnected.

Part of the ``watchdog_probes`` split — import via the ``utils.watchdog_probes``
hub, not from here.

Born 2026-09-27 from an operator DRILL: the SHORT_TURBO leg of moc's
cross-preset bridge (a USB Heltec V3) was pulled on purpose "to see if the
domain would notice — nope". The leg sat dead 08:36→14:19 and nothing paged:
no probe read bridge-leg state at all. (The gateway half — the leg never even
noticed its radio was gone — is fixed in ``gateway.mesh_bridge``.) Added
during the 2026-09-09 harness freeze BECAUSE a live drill proved the blind
spot (the freeze was reviewed and deleted 2026-10-09; this probe was one of
the two it could not stop, and that was counted in its favour).

Evidence is the gateway's OWN periodic self-report (``bridge_cli`` prints
``<PRESET>: connected|disconnected`` per leg every ~30 s), read from the
journal — never a connection to the radio (#17). Only labels the box
DECLARES in gateway.json ``mesh_bridge`` are judged (declared vs actual).
Stateless: a window of readings, not a persisted streak, so there is no
state file whose write can fail silently (the 2026-09-02 saver class).
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Optional, Tuple

from utils.watchdog_probe_core import (
    Signal,
    _journal_match_lines,
    _resolve_main_pid_status,
    note_disposition,
    note_unit_presence_gate,
)

# A reboot / re-flash of a USB radio is ~30 s; a leg must read disconnected
# in EVERY status block across the window, with at least this many readings.
DEFAULT_LOOKBACK = "5min"
DEFAULT_MIN_READINGS = 5

_LEG_RE = r"\b({labels}): (connected|disconnected)\b"


def _declared_legs(home: Optional[str]) -> Tuple[str, Optional[List[str]]]:
    """Tri-state read of the leg labels ``bridge_cli`` prints for this box.

    ``("ok", [labels])`` · ``("absent", None)`` no gateway.json / mesh_bridge
    not enabled (no organ) · ``("unreadable", None)``. The label is the leg's
    ``preset``, else its leg name — the same rule as ``bridge_cli._link_legs``.
    """
    if not home:
        return ("unreadable", None)
    path = os.path.join(home, ".config", "meshforge", "gateway.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return ("absent", None)
    except (OSError, ValueError, TypeError):
        return ("unreadable", None)
    mb = data.get("mesh_bridge") if isinstance(data, dict) else None
    if not isinstance(mb, dict) or not mb.get("enabled"):
        return ("absent", None)
    labels = []
    for leg in ("primary", "secondary"):
        info = mb.get(leg)
        if not isinstance(info, dict):
            return ("unreadable", None)
        labels.append(str(info.get("preset") or leg))
    return ("ok", labels)


def probe_bridge_leg_down(
    *,
    unit: str = "meshforge-gateway.service",
    lookback: str = DEFAULT_LOOKBACK,
    min_readings: int = DEFAULT_MIN_READINGS,
    journalctl_path: str = "journalctl",
    systemctl_path: str = "systemctl",
    home: Optional[str] = None,
    lines_fn=None,
    pid_status_fn=None,
) -> Optional[Signal]:
    """Fire when a declared mesh_bridge leg read ``disconnected`` in every
    gateway status block across ``lookback`` (≥ ``min_readings`` readings).

    Dispositions: no gateway unit / gateway stopped / no mesh_bridge declared
    → ``inert`` (service_inactive pages a stopped unit); journal or
    gateway.json unreadable, or a declared leg with NO readings while the
    gateway runs → ``indeterminate`` (a silent self-report is not a healthy
    one); some-but-not-all disconnected, or too few readings → under
    debounce → ``indeterminate``; all connected → ``clean``.

    Recovery: find the leg's radio (USB unplugged/renamed? preset changed?),
    fix ``mesh_bridge.<leg>`` in gateway.json (prefer a
    ``/dev/serial/by-id/`` path for serial legs), restart meshforge-gateway,
    and confirm the next status block reads ``<PRESET>: connected``.
    """
    status_fn = pid_status_fn or (
        lambda: _resolve_main_pid_status(unit, systemctl_path=systemctl_path))
    gw_status, gw_pid = status_fn()
    if gw_pid is None:
        note_unit_presence_gate(
            "bridge_leg_down", gw_status,
            stopped_is_inert=True,
            absent_reason=f"gateway not running on this box ({gw_status})",
            unresolved_reason=(f"{unit} state unobservable; cannot tell "
                               f"whether a bridge organ exists here"))
        return None

    if home is None:
        from utils.watchdog_probes_gateway_flow import _resolve_operator_home
        home = _resolve_operator_home()
    decl, labels = _declared_legs(home)
    if decl == "absent":
        note_disposition("bridge_leg_down", "inert",
                         reason="no mesh_bridge declared on this box")
        return None
    if decl != "ok" or not labels:
        note_disposition("bridge_leg_down", "indeterminate",
                         reason="gateway.json mesh_bridge unreadable")
        return None

    pattern = _LEG_RE.format(labels="|".join(re.escape(x) for x in labels))
    if lines_fn is None:
        lines = _journal_match_lines(unit, pattern, lookback,
                                     journalctl_path=journalctl_path)
    else:
        lines = lines_fn(pattern)
    if lines is None:
        note_disposition("bridge_leg_down", "indeterminate",
                         reason="gateway journal unavailable")
        return None

    rx = re.compile(pattern)
    readings = {label: [] for label in labels}
    for ln in lines:
        for label, state in rx.findall(ln):
            if label in readings:
                readings[label].append(state)

    silent = [lb for lb, rs in readings.items() if not rs]
    if silent:
        note_disposition(
            "bridge_leg_down", "indeterminate",
            reason=(f"no gateway status readings for declared leg(s) "
                    f"{', '.join(silent)} in {lookback}"))
        return None

    down = [lb for lb, rs in readings.items()
            if len(rs) >= min_readings and all(s == "disconnected" for s in rs)]
    if not down:
        if any("disconnected" in rs for rs in readings.values()):
            note_disposition("bridge_leg_down", "indeterminate",
                             reason="leg disconnect under debounce")
        else:
            note_disposition("bridge_leg_down", "clean")
        return None

    leg = down[0]
    n = len(readings[leg])
    detail = (
        f"mesh_bridge leg {leg} has read DISCONNECTED in all {n} gateway "
        f"status blocks over the last {lookback} — nothing crosses between "
        f"the bridge's presets. Usual cause: the leg's radio is gone or "
        f"renamed (USB unplug, /dev/tty* renumbering, preset change). Fix: "
        f"check the device in gateway.json mesh_bridge (prefer a "
        f"/dev/serial/by-id/ path), restart meshforge-gateway, then confirm "
        f"the next status block reads '{leg}: connected'."
    )
    return Signal(
        cls="bridge_leg_down",
        subject=f"mesh_bridge:{leg}",
        severity="degraded",
        detail=detail,
        extra={"leg": leg, "readings": n, "down_legs": down,
               "lookback": lookback},
    )
