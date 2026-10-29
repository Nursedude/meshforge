"""Watchdog probe — RNS TCPInterface stuck Down while its peer answers.

The 2026-05-30 islanding shape, with its N-consecutive-ticks confirmation
(2026-10-28). Split out of ``watchdog_probes_rns`` (MF025) — import via the
``utils.watchdog_probes`` hub, not from here.
"""
from __future__ import annotations

import re
import socket
from typing import TYPE_CHECKING, List, Optional, Tuple

from utils.watchdog_probe_core import (
    Signal,
    _resolve_main_pid_status,
    env_confirm_ticks,
    note_disposition,
    note_unit_presence_gate,
)
from utils import tx_guard

if TYPE_CHECKING:
    from utils.rns_status_parser import RNSStatus


# ─────────────────────────────────────────────────────────────────────
# Probe: RNS interface Down while peer reachable (2026-05-30)
# ─────────────────────────────────────────────────────────────────────

# A routable TCPInterface display_name embeds the peer host:port, e.g.
#   "Regional RNS/192.0.2.38:4242"
# RNodeInterface / AutoInterface / the Shared Instance line carry no
# host:port and are correctly ignored — only a TCP peer can be probed
# for reachability. The host group is an IPv4 dotted-quad; rnsd renders
# the configured target_host:target_port verbatim.
_TCP_PEER_RE = re.compile(r"(?P<host>[0-9.]+):(?P<port>\d+)\s*$")


def _tcp_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    """Bounded TCP-connect reachability test to ``(host, port)``.

    Returns True when a TCP connection can be established within
    ``timeout`` seconds, False on any ``OSError`` (refused, timed out,
    no route, bad address). Factored out as a module-level function so
    tests can monkeypatch it and do zero real network I/O.
    """
    try:
        with tx_guard.probe_connect(), \
                socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# Confirmation (2026-10-28). The probe used to claim a wedge on ONE Down
# tick. Measured from mini's edge history: four "[RED] … restart rnsd" pages
# on moc3-gateway's TCPInterface 10-27/10-28, every one edge_up→edge_down in
# exactly one ~30 s tick (one was our own drill restart) — a TCP reconnect,
# not a wedge. The 2026-05-30 incident this probe exists for held for HOURS.
# And the false page's cure text is "restart rnsd", the #69 race trigger.
# So the SAME interface must read Down + peer-reachable on N consecutive
# observed ticks. Until then the class reads `indeterminate`, never `clean`.
# Only SEEING the interface Up (or gone from the table) clears a streak. A
# tick that could not see it — rnstatus errored/empty, or the 3 s connect to
# the peer failed — neither advances nor clears it: a lossy path or a loaded
# Pi must not reset a real wedge to zero forever (review 2026-10-28).
# A streak belongs to ONE rnsd lifetime: rnsd's MainPID changing (a restart,
# e.g. the cure itself or a drill) starts every streak over, because the
# TCPInterface reads Down for a tick while it reconnects after any restart
# (measured 18:29 on 10-28) — a carried streak would page on that reconnect.
# Its own knob (hfm #5 parser, not the RPC sibling's value: a reconnecting
# socket and a hung subprocess are different quantities). In-memory for the
# same reason as _rpc_timeout_streak (the 2026-09-02 frozen-saver trap);
# a watchdog restart during a live wedge delays the page by N-1 ticks.
_IFACE_DOWN_CONFIRM_TICKS_ENV = "MESHFORGE_RNS_IFACE_DOWN_CONFIRM_TICKS"
_DEFAULT_IFACE_DOWN_CONFIRM_TICKS = 3

_iface_down_streaks: dict = {}
_iface_down_rnsd_pid: List[Optional[int]] = [None]


def _iface_down_confirm_ticks() -> int:
    """Consecutive Down + reachable ticks before an interface is wedged."""
    return env_confirm_ticks(_IFACE_DOWN_CONFIRM_TICKS_ENV,
                              _DEFAULT_IFACE_DOWN_CONFIRM_TICKS)


def reset_rns_iface_down_streaks() -> None:
    """Clear the per-interface Down streaks and the rnsd lifetime. For tests."""
    _iface_down_streaks.clear()
    _iface_down_rnsd_pid[0] = None


def _rnsd_lifetime_changed() -> bool:
    """True when rnsd's MainPID differs from the one the streaks were built
    under. An unresolvable MainPID (non-systemd box, systemctl error) is not
    a change — it neither resets nor re-anchors."""
    status, pid = _resolve_main_pid_status("rnsd.service")
    if status != "ok" or pid is None:
        return False
    prior, _iface_down_rnsd_pid[0] = _iface_down_rnsd_pid[0], pid
    return prior is not None and prior != pid


def probe_rns_interface_down_peer_reachable(
    *,
    rnstatus_status: "Optional[RNSStatus]" = None,
    rnstatus_text: Optional[str] = None,
    reachable_timeout_s: float = 3.0,
    rns_presence: Optional[str] = None,
) -> Optional[Signal]:
    """Detect a configured TCPInterface stuck ``Status: Down`` while its
    peer host:port is still TCP-reachable — the 2026-05-30 incident shape.

    The production incident: rnsd itself was healthy (Up, owned ``@rns``,
    answered ``rnstatus``) and the peer host:port + L3 were reachable,
    but the box's SOLE RNS uplink ``TCPInterface`` sat ``Status: Down``.
    The fleet was islanded until rnsd was restarted. The existing
    watchdog only caught this indirectly via ``tracer_peer_unreachable``;
    this probe catches it DIRECTLY at the interface layer.

    Logic:

    * Resolve interfaces from ``rnstatus_status`` (a pre-parsed
      ``RNSStatus`` — the runner shares one ``run_rnstatus`` call across
      the rnstatus-consuming probes), else parse ``rnstatus_text``
      (tests), else run ``rnstatus`` live — via ``utils.rns_status_parser``.
    * For each TCPInterface whose status is Down AND whose display_name
      embeds a ``host:port``, run a bounded TCP-connect reachability test.
    * If the connect SUCCEEDS → peer is reachable but the interface is
      stuck Down → emit a wedge signal (cure: restart rnsd).
    * If the connect FAILS → genuine peer/network outage, already owned
      by ``tracer_peer_unreachable``; do NOT emit here.

    Returns the FIRST qualifying interface's signal (consistent with the
    other single-return probes), with ``extra.down_reachable_count`` when
    more than one interface qualifies. Returns None when no interface
    qualifies, rnstatus is unreadable/errored, or rnsd is down (a
    different probe owns those).
    """
    if rnstatus_status is not None:
        status = rnstatus_status
    elif rnstatus_text is not None:
        from utils.rns_status_parser import parse_rnstatus
        status = parse_rnstatus(rnstatus_text)
    else:
        from utils.rns_status_parser import run_rnstatus
        status = run_rnstatus()

    # rnstatus errored (rnsd unreachable, binary missing, timeout) →
    # don't speculate. service_inactive owns rnsd-down; the
    # rns_rpc_unresponsive probe owns the rnstatus-timeout (wedged-RPC)
    # case (it keys on RNSStatus.timed_out, not on this parse_error).
    if status.parse_error:
        # Looked up at call time (the rf_leg idiom) so a patch of the
        # canonical ``watchdog_probes_rns.rns_stack_presence`` reaches here.
        from utils.watchdog_probes_rns import (
            RNS_ABSENT_BY_DESIGN_REASON, rns_stack_presence)
        # A timeout is something answering slowly (#72) — never absence.
        note_unit_presence_gate(
            "rns_interface_down_peer_reachable",
            "unknown" if status.timed_out
            else (rns_presence or rns_stack_presence()),
            absent_reason=RNS_ABSENT_BY_DESIGN_REASON,
            unresolved_reason="rnstatus errored; interface table unobservable")
        return None
    # N2 (2026-07-18): empty rnstatus output / unrecognized error text
    # yields parse_error=None WITH interfaces=[] — but a healthy rnstatus
    # always prints at least the shared-instance block, so an empty
    # interface table means the table was never actually observed. Noting
    # clean here would be an affirmative green on an unobserved surface.
    if not status.interfaces:
        note_disposition("rns_interface_down_peer_reachable", "indeterminate",
                         reason="rnstatus output empty/unrecognized — "
                                "interface table unobservable")
        return None

    from utils.rns_status_parser import InterfaceStatus

    qualifying: List[Tuple[str, str, int]] = []  # (interface_label, host, port)
    held = set()  # Down, peer connect failed — unobserved, streak held
    for iface in status.interfaces:
        # Only TCP interfaces carry a routable peer host:port.
        if "tcp" not in iface.type_name.lower():
            continue
        if iface.status != InterfaceStatus.DOWN:
            continue
        m = _TCP_PEER_RE.search(iface.display_name)
        if not m:
            continue
        host = m.group("host")
        try:
            port = int(m.group("port"))
        except (TypeError, ValueError):
            continue
        if _tcp_reachable(host, port, timeout=reachable_timeout_s):
            qualifying.append((iface.full_name, host, port))
        else:
            held.add(iface.full_name)

    # The table WAS observed: an interface neither qualifying nor held is Up
    # or gone from the table — its streak ends.
    seen = {q[0] for q in qualifying}
    for stale in [k for k in _iface_down_streaks
                  if k not in seen and k not in held]:
        del _iface_down_streaks[stale]
    # Resolved only on the rare tick something qualifies (one systemctl).
    if seen and _rnsd_lifetime_changed():
        _iface_down_streaks.clear()
    for q_label in seen:
        _iface_down_streaks[q_label] = _iface_down_streaks.get(q_label, 0) + 1

    if not qualifying:
        note_disposition("rns_interface_down_peer_reachable", "clean")
        return None

    needed = _iface_down_confirm_ticks()
    confirmed = [q for q in qualifying if _iface_down_streaks[q[0]] >= needed]
    if not confirmed:
        worst = max(qualifying, key=lambda q: _iface_down_streaks[q[0]])[0]
        more = (f" (+{len(qualifying) - 1} more interface(s) pending)"
                if len(qualifying) > 1 else "")
        note_disposition(
            "rns_interface_down_peer_reachable", "indeterminate",
            reason=(
                f"OBSERVED {worst} Down with peer reachable — UNCONFIRMED: "
                f"{_iface_down_streaks[worst]} of {needed} consecutive "
                f"tick(s) required{more}. A reconnect clears inside one "
                "tick, a wedge persists; a one-shot run cannot confirm — "
                "re-check with `rnstatus` over a minute"
            ),
        )
        return None

    label, host, port = confirmed[0]
    extra = {
        "interface": label,
        "host": host,
        "port": port,
        "peer_reachable": True,
        "consecutive_down": _iface_down_streaks[label],
        "confirm_ticks": needed,
    }
    if len(confirmed) > 1:
        extra["down_reachable_count"] = len(confirmed)

    return Signal(
        cls="rns_interface_down_peer_reachable",
        subject=label,
        severity="wedge",
        detail=(
            f"rnstatus shows {label} Status: Down but its peer "
            f"{host}:{port} is TCP-reachable — stuck interface, not a "
            f"peer outage. rnsd is up but this uplink is wedged; the box "
            f"may be islanded. Recovery: sudo systemctl restart "
            f"rnsd.service. See 2026-05-30 incident."
        ),
        extra=extra,
    )
