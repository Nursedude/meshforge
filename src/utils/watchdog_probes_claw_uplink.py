"""Claw UPLINK probes — the out-of-band witness's own path home.

Split into its own module (2026-07-29) rather than appended to
``watchdog_probes_liveness.py``: that file sat at 1,411 lines and this probe
would have carried it past the 1,500-line cap (MF025, whose baseline only
shrinks — you split, you do not grant headroom).

The claws are the fleet's only OUT-OF-BAND witnesses: separate silicon on a
separate subnet, the one vantage no box can fabricate about itself. They reach
the fleet through infrastructure that can silently relocate — and when it does,
every other signal the fleet owns blames the device instead.
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

from utils.watchdog_probe_core import (
    Signal,
    _load_parity_streak,
    _save_parity_streak,
    note_disposition,
)
from utils.watchdog_probes_liveness import _operator_home

DEFAULT_CLAW_UPLINK_DEBOUNCE_PATH = "/var/lib/meshforge/claw_uplink_debounce.json"
DEFAULT_CLAW_UPLINK_CONFIG = "claw_uplink_nodes.json"
_ARP_PATH = "/proc/net/arp"
_ATF_COM = 0x2   # arp entry is COMPLETE — a resolved neighbour, not a failed try


def _read_arp_locations(arp_path: str) -> Optional[Dict[str, List[str]]]:
    """``{mac_lower: [ip, ...]}`` for COMPLETE entries only. None = unreadable.

    ⚠️ ``/proc/net/arp`` also lists INCOMPLETE rows (``Flags 0x0``) — resolution
    attempts that FAILED. Probing a stale address creates exactly such a row
    carrying the target MAC, so a reader that ignores the flags manufactures a
    second "location" for the node out of its own probing, most reliably at the
    moment someone goes looking. Only ATF_COM entries are sightings.
    """
    try:
        with open(arp_path) as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    out: Dict[str, List[str]] = {}
    for line in lines[1:]:                      # row 0 is the header
        parts = line.split()
        if len(parts) < 4:
            continue
        ip, flags_s, mac = parts[0], parts[2], parts[3]
        try:
            flags = int(flags_s, 16)
        except ValueError:
            continue
        if not (flags & _ATF_COM):
            continue                            # failed resolution, not a location
        out.setdefault(mac.lower(), []).append(ip)
    return out


def probe_claw_uplink_node_moved(
    *,
    config_path: Optional[str] = None,
    arp_path: str = _ARP_PATH,
    state_path: Optional[str] = None,
    now: Optional[float] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """A declared claw UPLINK node is answering at an address we did not declare.

    THE distinction this exists to make: a claw can be perfectly healthy —
    booted, associated, holding its DHCP lease, dialling the right broker — and
    still be unreachable, because the node bridging its subnet to the fleet
    segment is not where the fleet believes it is. Every signal the fleet had
    for that state said ``claw_device_dark``, which points the operator at
    hardware that is working. Born 2026-07-29 from dudeclaw-01: ~5 h dark, and
    the investigation spent a power cycle, an antenna reseat, two chip resets
    and three dead hypotheses on a device that was never at fault (its config,
    read off its own LittleFS, had the correct broker the whole time).

    LEADING indicator, deliberately — it fires on the CONDITION (the uplink is
    not where it was declared) rather than the OUTCOME (a claw went quiet), the
    same reasoning as ``gateway_dual_homed_exposure``. A relocated uplink may
    still route fine; it is drift worth naming before it strands a witness.

    Observation-only: reads ``/proc/net/arp``, a plain file. No packets, no
    subprocess, nothing that could itself perturb what it measures.

    Self-guards None: no declaration on this box (INERT — most boxes have no
    claw uplink), arp unreadable, the MAC observed NOWHERE (blindness, never
    "it moved"), or every declared node answering at its declared address.
    Seen at the declared address AND elsewhere counts as home — under-fire
    rather than false-page. 2-tick debounce. Never raises into the tick.
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_CLAW_UPLINK_DEBOUNCE_PATH

        if config_path is None:
            home = _operator_home()
            if not home:
                note_disposition(
                    "claw_uplink_node_moved", "inert",
                    reason="operator home unresolvable; no uplink declaration here")
                return None
            config_path = os.path.join(home, ".config", "meshforge",
                                       DEFAULT_CLAW_UPLINK_CONFIG)

        if not os.path.exists(config_path):
            note_disposition(
                "claw_uplink_node_moved", "inert",
                reason="no claw_uplink_nodes.json (no claw uplink declared here)")
            _save_parity_streak(sp, 0)
            return None

        try:
            with open(config_path) as f:
                declared = json.load(f)
        except (OSError, ValueError) as e:
            note_disposition(
                "claw_uplink_node_moved", "indeterminate",
                reason=f"uplink declaration unreadable ({e.__class__.__name__}); "
                       f"a bad file is not an absent node")
            return None
        if not isinstance(declared, list) or not declared:
            note_disposition(
                "claw_uplink_node_moved", "inert",
                reason="uplink declaration empty")
            _save_parity_streak(sp, 0)
            return None

        locations = _read_arp_locations(arp_path)
        if locations is None:
            note_disposition(
                "claw_uplink_node_moved", "indeterminate",
                reason=f"{arp_path} unreadable; lost the observation channel "
                       f"(blind is not healthy)")
            return None

        moved = []
        unobserved = []
        home_ok = 0
        for node in declared:
            if not isinstance(node, dict):
                continue
            mac = str(node.get("mac") or "").lower().strip()
            want = str(node.get("expected_ip") or "").strip()
            if not mac or not want:
                continue                        # incomplete row: skip, never fatal
            seen = locations.get(mac) or []
            if not seen:
                unobserved.append(node)
            elif want in seen:
                home_ok += 1                    # reachable where declared = home
            else:
                moved.append((node, sorted(seen)))

        if not moved:
            if home_ok:
                note_disposition(
                    "claw_uplink_node_moved", "clean",
                    reason=f"{home_ok} claw uplink node(s) at their declared address")
                _save_parity_streak(sp, 0)
            elif unobserved:
                note_disposition(
                    "claw_uplink_node_moved", "indeterminate",
                    reason=f"{len(unobserved)} declared uplink node(s) not in the "
                           f"neighbour table; not contacted recently — unobserved "
                           f"is not relocated")
            else:
                note_disposition(
                    "claw_uplink_node_moved", "inert",
                    reason="no usable uplink declaration (entries lack mac/expected_ip)")
                _save_parity_streak(sp, 0)
            return None

        streak = _load_parity_streak(sp) + 1
        _save_parity_streak(sp, streak)
        if streak < debounce_ticks:
            note_disposition(
                "claw_uplink_node_moved", "clean",
                reason=f"uplink drift seen (streak {streak}/{debounce_ticks}); "
                       f"debouncing a single odd neighbour read")
            return None

        node, seen = moved[0]
        name = str(node.get("name") or node.get("mac"))
        want = str(node.get("expected_ip"))
        serves = [str(s) for s in (node.get("serves") or [])]
        serves_txt = (", ".join(serves) if serves else "its claw(s)")
        return Signal(
            cls="claw_uplink_node_moved",
            subject=name,
            severity="degraded",
            detail=(
                f"claw uplink node {name} is answering at {', '.join(seen)}, "
                f"not its declared {want} — it serves {serves_txt}. A claw behind "
                f"a relocated uplink can boot, associate, hold a DHCP lease and "
                f"dial the correct broker while having no path out, and the only "
                f"other signal for that state (claw_device_dark) blames the DEVICE. "
                f"Reconcile the declaration or restore the address reservation."
            ),
            extra={
                "mac": str(node.get("mac")),
                "declared_ip": want,
                "observed_ips": seen,
                "serves": serves,
                "also_home": home_ok,
                "unobserved": len(unobserved),
            },
        )
    except Exception:
        note_disposition("claw_uplink_node_moved", "indeterminate",
                         reason="probe raised unexpectedly; unobservable this tick")
        return None
