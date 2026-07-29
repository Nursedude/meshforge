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
import re
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

# The NATS pinhole is the AUTHORITY on which uplink address is admitted.
# moc2 default-denies 4222 and allows a hardcoded allowlist, because WireClaw
# cannot send NATS credentials. That allowlist and the uplink's DHCP lease are
# two independent copies of ONE constant, and on 2026-07-29 they drifted:
# lease moved .24 -> .250, pinhole kept .24, and every SYN from a healthy claw
# was dropped for 6.5 h. Reading the pinhole here is what stops this probe
# from being a THIRD copy of that constant (honest_failure_modes #5).
DEFAULT_PINHOLE_PATH = "/etc/nftables.conf"
DEFAULT_PINHOLE_PORT = 4222
_PINHOLE_RE = re.compile(
    r"ip\s+saddr\s*\{([^}]*)\}\s*tcp\s+dport\s+(\d+)\s+accept")


def _read_pinhole_allowlist(path: str, port: int) -> Optional[List[str]]:
    """Sorted allowlisted source IPs for ``port``. None = no opinion.

    None means "this box does not gate that port" — either the file is absent
    (most boxes) or its ruleset never mentions the port. That is NOT an empty
    allowlist: "admits nobody" and "does not gate" are opposite facts, and
    collapsing them would page every box that simply has no pinhole.
    """
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return None
    found = None
    for m in _PINHOLE_RE.finditer(text):
        if int(m.group(2)) != port:
            continue
        ips = [p.strip() for p in m.group(1).split(",") if p.strip()]
        found = sorted(set((found or []) + ips))
    return found


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
    pinhole_path: str = DEFAULT_PINHOLE_PATH,
    pinhole_port: int = DEFAULT_PINHOLE_PORT,
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

        # The AUTHORITY leg: is the uplink's CURRENT address one the firewall
        # will actually admit? None = this box does not gate the port, so the
        # leg abstains rather than inventing a verdict.
        allow = _read_pinhole_allowlist(pinhole_path, pinhole_port)

        moved = []
        unobserved = []
        home_ok = 0
        for node in declared:
            if not isinstance(node, dict):
                continue
            mac = str(node.get("mac") or "").lower().strip()
            want = str(node.get("expected_ip") or "").strip()
            if not mac:
                continue                        # incomplete row: skip, never fatal
            seen = locations.get(mac) or []
            if not seen:
                unobserved.append(node)
                continue
            if allow is not None and not any(ip in allow for ip in seen):
                # THE 2026-07-29 case: the node is reachable, possibly exactly
                # where declared, but the pinhole admits a DIFFERENT address —
                # so the claw's SYNs are dropped and it looks like dead hardware.
                moved.append((node, sorted(seen), allow, False))
            elif want and want not in seen:
                moved.append((node, sorted(seen), allow, True))
            else:
                home_ok += 1

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

        node, seen, allow_at_fire, admitted = moved[0]
        name = str(node.get("name") or node.get("mac"))
        want = str(node.get("expected_ip") or "")
        serves = [str(s) for s in (node.get("serves") or [])]
        serves_txt = (", ".join(serves) if serves else "its claw(s)")
        common = (
            f" It serves {serves_txt}. A claw behind a mis-addressed uplink can "
            f"boot, associate, hold a DHCP lease and dial the correct broker "
            f"while every SYN is dropped, and the only other signal for that "
            f"state (claw_device_dark) blames the DEVICE."
        )
        if not admitted:
            detail = (
                f"claw uplink node {name} answers at {', '.join(seen)}, which the "
                f"port-{pinhole_port} pinhole does NOT admit "
                f"(allows: {', '.join(allow_at_fire or []) or 'nobody'})."
                + common +
                f" Fix: add {seen[0]} to the pinhole in {pinhole_path} (reload "
                f"nftables), then pin that address with a DHCP reservation so the "
                f"firewall's hardcoded copy cannot drift from the lease again."
            )
        else:
            detail = (
                f"claw uplink node {name} answers at {', '.join(seen)}, not its "
                f"declared {want} — the pinhole still admits it, so traffic flows, "
                f"but the declaration is now stale."
                + common +
                f" Fix: reconcile claw_uplink_nodes.json, or restore the reservation."
            )
        return Signal(
            cls="claw_uplink_node_moved",
            subject=name,
            severity="degraded",
            detail=detail,
            extra={
                "mac": str(node.get("mac")),
                "declared_ip": want,
                "observed_ips": seen,
                "pinhole_allows": allow_at_fire,
                "pinhole_port": pinhole_port,
                "admitted": admitted,
                "serves": serves,
                "also_home": home_ok,
                "unobserved": len(unobserved),
            },
        )
    except Exception:
        note_disposition("claw_uplink_node_moved", "indeterminate",
                         reason="probe raised unexpectedly; unobservable this tick")
        return None
