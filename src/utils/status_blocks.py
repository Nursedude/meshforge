"""Status blocks shared by both apps — byte-identical with MeshAnchor
(parity BYTE_IDENTICAL), split out of MeshForge's _map_status_endpoints on
2026-10-15 (NOC read grant, the deferred move).

Pure transforms from a box's RAW state (watchdog.json, mini state, the radio
LISTEN/usb probe) into the ``/api/status`` / ``/fleet/slo`` blocks the
fleet-truth builder reads. Every consumer — each app's own status endpoint
AND each app's ssh-spool fallback for a map-less box — calls THESE, so one
artifact has one transform and one staleness threshold (honest_failure_modes
#5). MeshAnchor's private copy of the mini transform had already drifted:
it lacked the missing-timestamp and future-timestamp legs below.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterable, Optional

from utils.finite_stamp import finite  # the ONE finite-stamp rule (P2, 11-27)
from utils.mini_state_view import mini_state_view  # the ONE state reader (P3 r3)


WATCHDOG_STALE_S = 300.0  # 5 min — 10x the watchdog's 30s tick
MINI_STALE_S = 300.0      # 5 min — 10x mini-dudeai's 30s tick (SSOT for both consumers)
# B7: `age_s = max(0.0, now - ts)` clamps a FUTURE-stamped tick to age 0, so it
# reads fresh/ok for the whole skew window (RTC-less fleet: fake-hwclock steps
# forward, NTP corrects back). A ts more than this far ahead of now is its own
# unobservable leg, never freshness. Generous enough for ordinary NTP jitter.
FUTURE_TS_SLACK_S = 120.0


def watchdog_block_from_payload(
    payload: Dict[str, Any],
    *,
    now: Optional[float] = None,
    stale_after_s: float = WATCHDOG_STALE_S,
) -> Dict[str, Any]:
    """Shape a raw watchdog.json payload into the ``/api/status.watchdog``
    block, re-deriving staleness at read time.

    Module-level so BOTH consumers — the status handler's
    ``_read_watchdog_block`` and the fleet-truth ssh-spool fallback (which
    reads a map-less box's raw watchdog.json over ssh) — share ONE transform
    and ONE staleness threshold (honest_failure_modes #5: two consumers of
    one artifact share one constant). A stale payload reads ok=False with a
    ``stale:`` reason, which the fleet-truth classifier maps to DARK.
    """
    if not isinstance(payload, dict):
        return {"installed": True, "ok": False,
                "reason": "malformed_json: not an object"}

    # finite(), never isinstance: NaN read ok age 0 (every NaN comparison is
    # False), 10**400 raised out of the cross-box fetch, bool is an int (P2,
    # 11-27 Fable). A non-finite stamp OR reader clock is unobservable.
    ts = finite(payload.get("ts"))
    now_v = finite(now if now is not None else time.time())
    age_s: Optional[float] = None
    future = False
    if ts is not None and now_v is not None:
        # the mini transform's B7 legs, now that this feeds a CROSS-BOX view
        # (step-8 non-author pass 2026-10-15, F2): a frozen, future-stamped
        # file on an RTC-less box read fresh for the whole skew window
        future = ts > now_v + FUTURE_TS_SLACK_S
        age_s = max(0.0, now_v - ts)
    no_ts = age_s is None
    stale = bool(age_s is not None and age_s > stale_after_s)
    # only a real True is ok; an absent `ok` (legacy writer) still reads ok
    ok_field = payload.get("ok", True) is True
    block = {
        "installed": True,
        "ok": ok_field and not stale and not no_ts and not future,
        # the RAW value when finite (an int stays an int); never a NaN/inf,
        # which is not JSON (allow_nan=False consumers refuse the block)
        "ts": payload.get("ts") if ts is not None else None,
        "age_s": age_s,
        "probe_count": payload.get("probe_count"),
        "signals": payload.get("signals", []),
    }
    # Per-class disposition map (fleet-truth Phase 0). Passed through
    # verbatim for fleet_truth.merge_coverage; absent on legacy watchdog
    # writers — consumers treat absence as pre-coverage (every non-active
    # class dark), never as clean.
    if isinstance(payload.get("coverage"), dict):
        block["coverage"] = payload["coverage"]
    # knob posture (effective + IGNORED values, 2026-11-26): the journal
    # WARNING rotates away, so this is the witness an operator can still read
    if isinstance(payload.get("knobs"), dict):
        # a frozen/dead writer's posture describes no running process (hfm #2)
        block["knobs"] = ({"state": "stale"} if (stale or no_ts or future)
                          else payload["knobs"])
    if no_ts:
        block["reason"] = ("no_tick_timestamp: watchdog state lacks a finite numeric "
                           "ts (or this reader's clock is not one) — freshness "
                           "unobservable, treating as not-ok")
    elif future:
        block["reason"] = (f"tick timestamp in the future ({ts - now_v:.0f}s ahead, "
                           f"slack {FUTURE_TS_SLACK_S:.0f}s) — clock skew; freshness "
                           "unobservable until the clock settles")
    elif stale:
        block["reason"] = (
            f"stale: last write {age_s:.0f}s ago "
            f"(threshold {stale_after_s:.0f}s) — watchdog "
            f"daemon may have crashed"
        )
    return block


def mini_block_from_payload(
    payload: Any,
    *,
    now: Optional[float] = None,
    stale_after_s: float = MINI_STALE_S,
) -> Dict[str, Any]:
    """Shape a raw ``mini_dudeai_state.json`` payload into the
    ``/api/status.mini_dudeai`` block, re-deriving staleness at read time.

    Module-level so BOTH consumers — the status handler's
    ``_read_mini_state_block`` and the fleet-truth ssh-spool fallback (which
    reads a map-less gateway's raw mini state over ssh) — share ONE transform
    and ONE staleness threshold (honest_failure_modes #5: two consumers of one
    artifact share one constant). A stale payload reads ``ok=False`` with a
    ``stale:`` reason, which the fleet-truth classifier maps to DARK — never
    green-with-old-numbers.
    """
    if not isinstance(payload, dict):
        return {"installed": True, "ok": False,
                "reason": "malformed_json: not an object"}

    # finite(), never isinstance (P2, 11-27): see the watchdog transform.
    ts = finite(payload.get("last_tick_ts"))
    now_v = finite(now if now is not None else time.time())
    age_s: Optional[float] = None
    future = False
    if ts is not None and now_v is not None:
        # Beyond-slack future ts = clock skew (B7): the max(0.0, ...) clamp
        # below would otherwise read it fresh/ok for the whole skew window.
        future = ts > now_v + FUTURE_TS_SLACK_S
        age_s = max(0.0, now_v - ts)
    # Missing/non-numeric last_tick_ts is NOT "fresh forever": a valid-JSON
    # state file without the freshness field (schema drift, foreign writer)
    # must read not-ok, not green (honest_failure_modes #2; 07-23 audit —
    # this block now feeds a second consumer via the ssh-spool path, so the
    # false-green would render fleet-wide).
    no_ts = age_s is None
    stale = bool(age_s is not None and age_s > stale_after_s)

    # The ONE mini state reader (P3 seam r3, 11-27): `rules:[1]` raised out
    # of /api/status and the ssh-spool path; mixed `fire_count_24h` types
    # raised the sort; `error_count` NaN was echoed (NaN is not JSON).
    view = mini_state_view(payload)
    rules = view["rules"]
    active = [
        {"rule_id": rs.get("rule_id"), "subject": rs.get("subject"),
         "detail": rs.get("last_detail", "")}
        for rs in rules.values()
        if rs.get("currently_active")
    ]
    top = sorted(
        ({"rule_id": rs.get("rule_id"), "subject": rs.get("subject"),
          "fire_count_24h": rs.get("fire_count_24h", 0)}
         for rs in rules.values()
         if rs.get("fire_count_24h")),
        key=lambda r: r["fire_count_24h"], reverse=True,
    )[:5]

    block = {
        "installed": True,
        "ok": not stale and not no_ts and not future,
        "ts": payload.get("last_tick_ts") if ts is not None else None,
        "last_tick_iso": (payload.get("last_tick_iso")
                          if isinstance(payload.get("last_tick_iso"), str) else None),
        "age_s": age_s,
        "host": view["host"],
        # absent stays None (unknown never reads 0); garbage -> None
        "rule_count": view["rule_count"] if "rule_count" in payload else None,
        "error_count": view["error_count"] if "error_count" in payload else None,
        "active_rules": active,
        "top_rules_24h": top,
    }
    if view["unreadable"]:
        # witnessed, never absorbed — the consumer can name the blind part
        block["unreadable"] = list(view["unreadable"])
    if no_ts:
        block["reason"] = (
            "no_tick_timestamp: state lacks a finite numeric last_tick_ts — "
            "freshness unobservable, treating as not-ok (schema drift?)"
        )
    elif future:
        block["reason"] = (
            f"tick timestamp in the future ({ts - now_v:.0f}s ahead, "
            f"slack {FUTURE_TS_SLACK_S:.0f}s) — clock skew; freshness "
            f"unobservable until the clock settles"
        )
    elif stale:
        block["reason"] = (
            f"stale: last tick {age_s:.0f}s ago "
            f"(threshold {stale_after_s:.0f}s) — mini-dudeai "
            f"daemon may have crashed"
        )
    return block


#: The USB tty names a radio can appear under — ONE list for every consumer
#: (map /api/status, the ssh-spool writer, fleet_snapshot): honest_failure_
#: modes #5. /dev/ttyMeshCore is a udev symlink onto one of these.
USB_RADIO_GLOBS = ("/dev/ttyUSB*", "/dev/ttyACM*")


def usb_radio_devices(globs: "Iterable[str]" = USB_RADIO_GLOBS) -> "list[str]":
    """Sorted, de-duplicated USB tty device paths present now (presence only)."""
    import glob
    return sorted({d for g in globs for d in glob.glob(g)})


def tty_held(paths: "Iterable[str]",
             proc_root: str = "/proc") -> Optional[bool]:
    """Is any of these device nodes open by a running process? Read from
    ``/proc/<pid>/fd`` links only — never an open() of the device, which on a
    radio tty can reset the board or steal its single reader.

    True  = a process holds one (the device is IN USE by something — which
            process is NOT judged here; see radio_connection_from_probe).
    False = no holder among the processes this caller could read, and none it
            could not. In practice only a ROOT caller gets here: a non-root
            caller always meets unreadable root-owned fd dirs (/proc/1/fd) and
            gets None (review r3, measured). Under hidepid / ProtectProc=
            invisible other uids' pids are not even listed, so False there is
            weaker than it reads.
    None  = could not tell: /proc unreadable, or a process we may not read
            (another uid) might be the holder. Unobservable is never "not held".
    A link matches only the exact device node; a stale fd onto a removed node
    reads "<path> (deleted)" and is (correctly) not a holder of the new node.
    """
    targets = set()
    for p in paths:
        try:
            targets.add(os.path.realpath(p))
        except (OSError, ValueError):
            continue
    if not targets:
        return False
    try:
        pids = [d for d in os.listdir(proc_root) if d.isdigit()]
    except OSError:
        return None
    blind = False
    for pid in pids:
        fd_dir = os.path.join(proc_root, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except FileNotFoundError:
            continue                      # exited mid-scan
        except OSError:
            blind = True                  # another uid's process — unreadable
            continue
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) in targets:
                    return True
            except OSError:
                continue
    return None if blind else False


def radio_connection_from_probe(tcp_listening: bool,
                                usb_present: bool,
                                usb_held: Optional[bool] = None,
                                ) -> "tuple[Optional[bool], str]":
    """The (connected, mode) decision for the radio cell, from a NON-perturbing
    probe: meshtasticd's :4403 LISTEN state (read from /proc, never a connect —
    a connect seizes the single-consumer PhoneAPI and wedges mesh-TX, #17/#75)
    or a USB radio device. Module-level so every consumer — the map's own
    status summary, the ssh-spool fallback, fleet_snapshot — reaches the
    identical verdict (honest_failure_modes #5).

    A USB tty is PRESENCE, not use: a dev board on a desk enumerates exactly
    like a radio (meshforge-lab, 2026-11-15). Only a device a process HOLDS
    (``tty_held`` True — e.g. the MeshAnchor daemon on /dev/ttyMeshCore) reads
    connected/serial. Otherwise connected is ``None`` (unobservable -> DARK,
    never HEALTHY, never FAILED) with the mode saying which blindness:
    ``usb_unheld`` (no readable holder) or ``usb_holder_unknown`` (holders
    unreadable, or a spool written before ``usb_held`` existed). Only a real
    ``True``/``False`` counts as holder evidence.

    Known limit (review r2 F1, 2026-11-19): "held" means held by ANY process —
    an RNode held by rnsd reads serial even if the Meshtastic daemon is down.
    Which device IS the configured radio is the next arc."""
    if tcp_listening:
        return True, "tcp"
    if usb_present:
        if usb_held is True:
            return True, "serial"
        if usb_held is False:
            return None, "usb_unheld"
        return None, "usb_holder_unknown"
    return False, "none"
