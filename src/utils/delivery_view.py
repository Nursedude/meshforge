"""Delivery view — the domain's END (a message arrives) as one local screen.

Born 2026-09-23 from the metrics audit: the END metrics (delivered,
confirmation rate, soak round trips) were measured on the gateway boxes and
shown in NO UI — the dashboards that existed rendered the harness, and the
Grafana/Prometheus pair that could have shown them was unused since April and
stopped by the operator. This module gathers what the gateway and the soak
exercisers already WRITE and says, per number, where it came from and how old
it is.

READ-ONLY and LOCAL-ONLY, deliberately (the TUI stays a surface; panes are
this box only, so the standalone offering works with no fleet to fan out to).
It reads files the gateway and the soak timers publish — never the SQLite DB
(a root reader strands root-owned WAL/SHM in the operator's data dir, the
#60 class), never an HTTP endpoint (the same process publishes the same
counters to disk), never the radio.

Tri-state per leg (honest_failure_modes #1/#2): ``ok`` carries numbers;
``inert`` means the organ is absent BY DESIGN here (no gateway unit, no soak
timer); ``unknown`` means we could not see — unreadable, stale, future-stamped,
or an organ that is present but publishes nothing. An unknown leg never prints
a number, and the headline is never better than its worst present leg.

Shared definitions are IMPORTED from the probes that judge the same artifacts
(honest_failure_modes #5) — the file paths, the freshness bound, the windowed
confirmation count and its floor — so this screen and the watchdog cannot
disagree about what the same file says.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import textwrap
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from utils.watchdog_probes_gateway import (
    DELIVERY_STALL_MIN_TERMINAL,
    _DELIVERY_SNAPSHOT_FRESH_S,
    _DELIVERY_SNAPSHOT_FUTURE_SLOP_S,
    _DELIVERY_SNAPSHOT_SUBPATH,
    _QUEUE_STATS_SUBPATH,
    confirmation_window,
)
from utils.watchdog_probes_gateway_flow import (
    PROPAGATION_SOAK_TIMER_UNIT,
    SYNTH_SOAK_TIMER_UNIT,
    _PROPAGATION_SOAK_STALE_AFTER_S,
    _SYNTH_SOAK_STALE_AFTER_S,
    _newest_synth_file,
)

OK = "ok"
INERT = "inert"
UNKNOWN = "unknown"

GATEWAY_UNIT = "meshforge-gateway.service"

#: The sister app's delivery DB, relative to the operator home. This screen
#: reads MeshForge's published files only; on a MeshAnchor box the organ
#: exists but is MeshAnchor's, and "no delivery organ here" would be a false
#: inert (found live on meshanchor-server 2026-09-23). The DB is never
#: opened (the #60 WAL/SHM trap). A FILE is not an organ: the writer unit is
#: asked too — the manager box carried an Aug-5 leftover of this DB with its
#: MeshAnchor daemon MASKED, and the headline claimed a record "on this box"
#: (operator's screen, 2026-09-23). Absent, or present but disabled/masked,
#: is a leftover; running, stopped-but-enabled or unreadable is not. ``meshanchor-daemon.service`` is the
#: process that holds the DB open on meshanchor-server (measured: 3 fds).
PEER_DELIVERY_DBS = (
    ("MeshAnchor", os.path.join(".local", "share", "meshanchor",
                                "delivery_counters.db"),
     "meshanchor-daemon.service"),
)

#: Stall-probe severities, restated for the reader (the probe owns firing).
_RATE_DEGRADED = 0.50


@dataclass
class Leg:
    """One source's observation. ``lines`` are only rendered when ``status``
    is OK; for INERT/UNKNOWN ``why`` is the whole story."""
    title: str
    status: str
    source: str = ""
    age_s: Optional[float] = None
    why: str = ""
    lines: List[str] = field(default_factory=list)
    # A present-but-failing observation (soak envelope failed, rate below the
    # probe's degraded bar). Still OK as an OBSERVATION; flagged for the
    # headline.
    failing: bool = False
    # Fresh and not failing, but too little evidence to call it healthy (the
    # stall probe's own floor). Keeps the headline from flattering a quiet box.
    thin: bool = False


@dataclass
class DeliveryView:
    host: str
    now: float
    legs: List[Leg]
    # (app, path) of a sister app's LIVE delivery record on this box (its
    # writer unit exists here, running or not, or could not be asked).
    peer_records: List[Tuple[str, str]] = field(default_factory=list)
    # (app, path, mtime) of a sister-app delivery file with NO writer unit
    # here — a leftover, shown as a footnote, never as an organ.
    peer_leftovers: List[Tuple[str, str, float]] = field(default_factory=list)

    def headline(self) -> str:
        present = [leg for leg in self.legs if leg.status != INERT]
        if not present and self.peer_records:
            apps = ", ".join(sorted({a for a, _p in self.peer_records}))
            return (f"NOT READ HERE — no MeshForge delivery organ, but {apps} "
                    f"keeps a delivery record on this box; read it in {apps}'s "
                    f"own TUI. Absent from this screen is not absent.")
        if not present:
            return ("NO DELIVERY ORGAN HERE — no gateway and no soak timer on "
                    "this box (inert). The mesh's END is read on a gateway box.")
        if any(leg.status == UNKNOWN for leg in present):
            return ("UNKNOWN — at least one delivery source here could not be "
                    "read; do not read this screen as the mesh being fine.")
        if any(leg.failing for leg in present):
            return "DEGRADED — a delivery source reports failures (see below)."
        if any(leg.thin for leg in present):
            return ("QUIET — every source is fresh and nothing is failing, but "
                    "too few recent confirmations to call delivery healthy.")
        return "Messages are arriving — every present source is fresh and passing."


# ── small helpers ────────────────────────────────────────────────────────


def fmt_age(age_s: Optional[float]) -> str:
    if age_s is None:
        return "age ?"
    s = max(0, int(age_s))
    if s < 90:
        return f"{s}s old"
    if s < 5400:
        return f"{s // 60}m old"
    if s < 172800:
        return f"{s // 3600}h old"
    return f"{s // 86400}d old"


def _num(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _read_published(path: str, key: str, now: float
                    ) -> Tuple[Optional[dict], Optional[float], Optional[str]]:
    """``(body, age_s, None)`` for a fresh gateway-published envelope, else
    ``(None, age_or_None, why)``. ``why == "absent"`` means the file does not
    exist — the caller decides whether that is inert or unknown."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return None, None, "absent"
    except (OSError, ValueError) as e:
        return None, None, f"unreadable ({type(e).__name__})"
    if not isinstance(doc, dict) or not isinstance(doc.get(key), dict):
        return None, None, "misshaped (no '%s' object)" % key
    ts = _num(doc.get("ts"))
    if ts is None:
        return None, None, "misshaped (ts missing)"
    age = now - ts
    if age < -_DELIVERY_SNAPSHOT_FUTURE_SLOP_S:
        return None, age, ("stamped in the FUTURE — a clock artifact, not an "
                           "observation")
    if age > _DELIVERY_SNAPSHOT_FRESH_S:
        return None, age, (f"STALE ({fmt_age(age)}, bound "
                           f"{int(_DELIVERY_SNAPSHOT_FRESH_S)}s) — the gateway "
                           f"stopped publishing")
    return doc[key], age, None


def _unit_state(unit: str, resolver) -> str:
    """``ok`` | ``down`` | ``absent`` | ``unknown`` for any unit."""
    if resolver is None:
        from utils.watchdog_probe_core import _resolve_main_pid_status
        resolver = _resolve_main_pid_status
    try:
        status, _pid = resolver(unit)
    except Exception:
        return "unknown"
    return status if status in ("ok", "down", "absent") else "unknown"


def _gateway_state(resolver: Optional[Callable[[str], Tuple[str, Optional[int]]]],
                   enabled_fn: Optional[Callable[[str], Optional[bool]]] = None
                   ) -> str:
    """``ok`` | ``down`` | ``down-disabled`` | ``absent`` | ``unknown``."""
    if resolver is None:
        from utils.watchdog_probe_core import _resolve_main_pid_status
        resolver = _resolve_main_pid_status
    try:
        status, _pid = resolver(GATEWAY_UNIT)
    except Exception:
        return "unknown"
    if status == "down":
        try:
            enabled = (enabled_fn or _unit_enabled)(GATEWAY_UNIT)
        except Exception:
            enabled = None
        return "down-disabled" if enabled is False else "down"
    return status if status in ("ok", "absent") else "unknown"


def _unit_enabled(unit: str, timeout: float = 5.0) -> Optional[bool]:
    """Tri-state ``systemctl is-enabled``: True / False / None (unobservable).

    Not ``service_check.is_service_enabled`` — that returns False on ANY
    error, and here False means "stopped by decision" and renders inert, so a
    systemctl we could not run would be read as a deliberate absence
    (honest_failure_modes #1)."""
    try:
        r = subprocess.run(["systemctl", "is-enabled", unit],
                           capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return None
    word = r.stdout.strip()
    if word in ("enabled", "enabled-runtime"):
        return True
    if word in ("disabled", "masked", "masked-runtime"):
        return False
    return None


def _missing_record_leg(title: str, source: str, gw: str) -> Leg:
    """The publish file is absent: inert only when no gateway exists here, or
    it is installed but stopped AND disabled — a decision (moc1/moc2 are
    federation relays that do not bridge, fleet_roles.yaml)."""
    if gw == "absent":
        return Leg(title, INERT, source,
                   why=f"no {GATEWAY_UNIT} on this box — not a gateway (by design)")
    if gw == "down-disabled":
        return Leg(title, INERT, source,
                   why=f"{GATEWAY_UNIT} is installed but stopped AND disabled "
                       f"— this box does not bridge (a decision, not an outage)")
    if gw == "down":
        return Leg(title, UNKNOWN, source,
                   why=f"{GATEWAY_UNIT} is installed but NOT running (enabled, "
                       f"or its enablement could not be read) and no record "
                       f"exists — nothing is being delivered here")
    if gw == "ok":
        return Leg(title, UNKNOWN, source,
                   why="the gateway is running but has published no record — "
                       "an older gateway build, or its data dir is unwritable")
    return Leg(title, UNKNOWN, source,
               why="no record, and the gateway unit's state could not be read")


# ── legs ────────────────────────────────────────────────────────────────


def delivery_leg(home: str, now: float, gw: str) -> Leg:
    title = "Gateway delivery record"
    path = os.path.join(home, _DELIVERY_SNAPSHOT_SUBPATH)
    snap, age, why = _read_published(path, "snapshot", now)
    if snap is None:
        if why == "absent":
            return _missing_record_leg(title, path, gw)
        return Leg(title, UNKNOWN, path, age, why=why)

    leg = Leg(title, OK, path, age)
    health = snap.get("health") if isinstance(snap.get("health"), dict) else {}
    if health.get("db_unobservable") or health.get("preflight_ok") is False:
        leg.status = UNKNOWN
        leg.why = ("the gateway could not read its own delivery DB — "
                   f"{health.get('preflight_error') or 'snapshot read failed'}")
        return leg

    win = confirmation_window(snap)
    if not win["confirmable"]:
        # Nothing here PROVES an arrival: a fresh record with no confirmable
        # protocol must not let the headline say "arriving" (review
        # 2026-09-23 — it did, on a mesh-only gateway).
        leg.thin = True
        # Not "Meshtastic has no ACK": a want_ack DM IS acked by its
        # destination (13/13 measured 2026-10-03). The gateway just does
        # not consume acks (meshtastic_ack_consumption_enabled off).
        leg.lines.append("Recent window: no protocol has ever confirmed here — "
                         "cannot judge a rate (the gateway does not consume "
                         "Meshtastic ACKs). Nothing on this box proves an "
                         "arrival. Measure DMs: scripts/dm_delivery_check.py")
    elif win["ring_source"] is None:
        leg.thin = True
        leg.lines.append("Recent window: UNKNOWN — the events ring is missing; "
                         "the rate cannot be judged.")
    else:
        protos = ", ".join(win["confirmable"])
        t = win["terminal"]
        if t < DELIVERY_STALL_MIN_TERMINAL:
            leg.thin = True
            leg.lines.append(
                f"Recent window ({protos}): {win['confirmed']} confirmed / "
                f"{win['failed']} failed — TOO FEW to judge "
                f"({t} of {DELIVERY_STALL_MIN_TERMINAL} needed). Quiet, not "
                f"proven healthy.")
        else:
            rate = win["confirmed"] / t
            leg.lines.append(
                f"Recent window ({protos}): {win['confirmed']} confirmed / "
                f"{win['failed']} failed of {t} -> {rate:.1%}")
            if rate <= _RATE_DEGRADED:
                leg.failing = True
                leg.lines.append(
                    f"  !! at or below {_RATE_DEGRADED:.0%} — the stall probe "
                    f"calls this degraded")
        span = _ring_span(win["ring"], now)
        if span:
            leg.lines.append(f"  window covers {span}")

    tot = snap.get("state_totals") if isinstance(snap.get("state_totals"), dict) else {}
    drops = snap.get("drop_reasons") if isinstance(snap.get("drop_reasons"), dict) else {}
    nz = ", ".join(f"{k} {v}" for k, v in sorted(
        drops.items(), key=lambda kv: -(_num(kv[1]) or 0)) if (_num(v) or 0) > 0)
    first = _num(snap.get("first_event_ts"))
    since = (time.strftime(" since %Y-%m-%d", time.localtime(first))
             if first else "")
    leg.lines.append(
        f"Lifetime{since}: confirmed {tot.get('confirmed', '?')} · sent "
        f"{tot.get('sent', '?')} · dropped {tot.get('dropped', '?')}")
    if nz:
        leg.lines.append(f"  drops: {nz}")
    rate = _num(snap.get("confirmation_rate"))
    if rate is not None:
        leg.lines.append(f"  lifetime confirmation_rate {rate:.3f} "
                         f"(confirmable protocols only)")
    uncon = _num(snap.get("unconfirmable_sent"))
    if uncon:
        leg.lines.append(f"  {int(uncon)} sent with no ACK consumed — "
                         f"handed to the radio, arrival not provable")
    last = _num(snap.get("last_event_ts"))
    if last is not None:
        leg.lines.append(f"Last gateway event (any protocol): "
                         f"{fmt_age(now - last).replace(' old', ' ago')}")
    errs = _num(health.get("consecutive_write_errors"))
    if errs:
        leg.failing = True
        leg.lines.append(f"  !! {int(errs)} consecutive write errors to the "
                         f"delivery DB: {health.get('last_write_error')}")
    return leg


def _ring_span(ring, now: float) -> str:
    ts = [_num(e.get("ts")) for e in ring or () if isinstance(e, dict)]
    ts = [t for t in ts if t is not None]
    if not ts:
        return ""
    hours = (max(ts) - min(ts)) / 3600.0
    return f"{len(ts)} events over {hours:.1f} h, newest {fmt_age(now - max(ts))}"


def queue_leg(home: str, now: float, gw: str) -> Leg:
    title = "Gateway queue"
    path = os.path.join(home, _QUEUE_STATS_SUBPATH)
    stats, age, why = _read_published(path, "stats", now)
    if stats is None:
        if why == "absent":
            return _missing_record_leg(title, path, gw)
        return Leg(title, UNKNOWN, path, age, why=why)
    leg = Leg(title, OK, path, age)
    g = stats.get
    # Two different clocks in one record (message_queue.get_stats): row
    # COUNTS still held in the queue DB (pruned by retention — measured
    # 203 -> 199 between two reads with no restart), and in-memory counters
    # since the gateway process started. The first cut of this screen titled
    # all of it "since the gateway last started" (2026-09-23).
    leg.lines.append(
        f"held in the queue DB: delivered {g('delivered', '?')} · pending "
        f"{g('pending', '?')} · in progress {g('in_progress', '?')} · DEAD "
        f"LETTERS {g('dead_letter', '?')} · depth {g('queue_depth', '?')}/"
        f"{g('max_queue_size', '?')}")
    leg.lines.append(
        f"since the gateway started: failed {g('failed', '?')} · retried "
        f"{g('retried', '?')} · shed {g('shed', '?')}")
    dead = _num(g("dead_letter"))
    failed = _num(g("failed"))
    if (dead or 0) > 0 or (failed or 0) > 0:
        leg.failing = True
    return leg


def soak_leg(title: str, home: str, leaf: str, prefix: str, unit: str,
             stale_after_s: float, now: float,
             enrolled_fn: Optional[Callable[[str, str], Optional[bool]]] = None
             ) -> Leg:
    sdir = os.path.join(home, ".local", "state", "meshforge", leaf)
    if enrolled_fn is None:
        from utils.user_units import user_timer_enrolled
        enrolled_fn = user_timer_enrolled
    try:
        enrolled = enrolled_fn(unit, home)
    except Exception:
        enrolled = None
    newest = _newest_synth_file(sdir, prefix=prefix) if os.path.isdir(sdir) else None

    if enrolled is False:
        if newest is None:
            return Leg(title, INERT, sdir, why=f"{unit} not enabled here — "
                                               f"this box does not run it")
        # Hand-run artifacts are not a cadence (the 08-09 78-day lesson) —
        # but only a STALE one is a leftover. A FRESH hand-run result is a
        # real round-trip observation and the probe judges it regardless of
        # enrollment (watchdog_probes_gateway_flow: enrollment is consulted
        # only on the stale branch); hiding a fresh FAIL as inert flattered
        # the headline (review 2026-09-23).
        path, mtime = newest
        if now - mtime > stale_after_s:
            return Leg(title, INERT, path, now - mtime,
                       why=f"{unit} not enabled here; the newest result is a "
                           f"hand-run artifact, not a live exerciser")
        head = f"{unit} not enabled here — this is a hand-run result, not a cadence"
    elif enrolled is None:
        head = "cannot tell whether the soak timer is enabled here"
    else:
        head = ""
    if newest is None:
        return Leg(title, UNKNOWN, sdir,
                   why=head or f"{unit} is enabled but no result has been "
                               f"written yet")
    path, mtime = newest
    age = now - mtime
    if age > stale_after_s:
        return Leg(title, UNKNOWN, path, age,
                   why=f"newest result is STALE (bound {int(stale_after_s)}s) "
                       f"— the exerciser has gone quiet")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            res = json.load(fh)
        if not isinstance(res, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as e:
        return Leg(title, UNKNOWN, path, age,
                   why=f"newest result unreadable ({type(e).__name__})")
    passed = res.get("pass_envelope")
    if not isinstance(passed, bool):
        # Say the exerciser's OWN reason: the propagation soak writes
        # pass_envelope null for an INDETERMINATE round (e.g. "send timeout
        # in state OUTBOUND" — the node was never exercised), and "no
        # pass/fail verdict" alone sent the reader digging (moc3 2026-09-23).
        why = "newest result carries no pass/fail verdict"
        indet = res.get("total_indeterminate")
        reasons = [r.get("reason") for r in res.get("round_results") or ()
                   if isinstance(r, dict) and r.get("reason")]
        if isinstance(indet, int) and indet > 0:
            why = (f"the exerciser called its newest round INDETERMINATE "
                   f"({indet} round(s) not exercised)")
        if reasons:
            why += f" — {reasons[0]}"
        return Leg(title, UNKNOWN, path, age, why=why)
    leg = Leg(title, OK, path, age, failing=not passed)
    ok, n = res.get("total_ok", "?"), res.get("total_samples", "?")
    thr = _num(res.get("ok_ratio_threshold"))
    thr_s = f", threshold {thr:.0%}" if thr is not None else ""
    ratio = _num(res.get("ok_ratio"))
    ratio_s = f" ({ratio:.0%})" if ratio is not None else ""
    leg.lines.append(f"{'PASS' if passed else 'FAIL'} — {ok}/{n} round trips "
                     f"arrived{ratio_s}{thr_s}")
    if head:
        leg.lines.append(f"  note: {head}")
    pairs = res.get("pair_results")
    if isinstance(pairs, list) and pairs:
        lat = [_num(p.get("p95_ms")) for p in pairs if isinstance(p, dict)]
        lat = [v for v in lat if v is not None]
        bad = [p for p in pairs if isinstance(p, dict) and (_num(p.get("fail_pct")) or 0) > 0]
        leg.lines.append(f"  {len(pairs)} user->peer pairs, "
                         f"{len(bad)} with failures"
                         + (f"; worst p95 {max(lat):.0f} ms" if lat else ""))
        for p in sorted(bad, key=lambda p: -(_num(p.get("fail_pct")) or 0))[:3]:
            leg.lines.append(f"    {p.get('user', '?')}->{p.get('peer', '?')}: "
                             f"{p.get('ok', '?')}/{p.get('samples', '?')} ok")
    lat = res.get("latency_s")
    if isinstance(lat, dict) and lat:
        parts = [f"{k} {v:.1f}s" for k, v in lat.items() if _num(v) is not None]
        if parts:
            leg.lines.append("  latency: " + ", ".join(parts))
    return leg


# ── entry point ─────────────────────────────────────────────────────────


def gather(home: Optional[str] = None, now: Optional[float] = None,
           gateway_resolver=None, enrolled_fn=None,
           unit_enabled_fn=None) -> DeliveryView:
    """Every leg for THIS box. ``home`` defaults to the real operator home
    (MF001-safe under sudo); the other arguments are test seams."""
    now = time.time() if now is None else now
    if home is None:
        from utils.paths import get_real_user_home
        home = str(get_real_user_home())
    gw = _gateway_state(gateway_resolver, unit_enabled_fn)
    legs = [
        delivery_leg(home, now, gw),
        queue_leg(home, now, gw),
        soak_leg("Synth soak (hourly LXMF round trips)", home, "synth_soak",
                 "synth-", SYNTH_SOAK_TIMER_UNIT, _SYNTH_SOAK_STALE_AFTER_S,
                 now, enrolled_fn),
        soak_leg("Propagation soak (LXMF via a propagation node)", home,
                 "propagation_soak", "prop-", PROPAGATION_SOAK_TIMER_UNIT,
                 _PROPAGATION_SOAK_STALE_AFTER_S, now, enrolled_fn),
    ]
    peers, leftovers = [], []
    for app, rel, unit in PEER_DELIVERY_DBS:
        path = os.path.join(home, rel)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        st = _unit_state(unit, gateway_resolver)
        if st == "down":
            try:
                enabled = (unit_enabled_fn or _unit_enabled)(unit)
            except Exception:
                enabled = None
            if enabled is False:
                st = "absent"  # disabled/masked: a decision, not an organ
        if st == "absent":
            leftovers.append((app, path, mtime))
        else:  # ok / down / unknown: an organ may be here — do not call it absent
            peers.append((app, path))
    return DeliveryView(host=socket.gethostname(), now=now, legs=legs,
                        peer_records=peers, peer_leftovers=leftovers)


def render(view: DeliveryView) -> str:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(view.now))
    out = [f"DELIVERY — {view.host}, read {stamp}",
           view.headline(), ""]
    label = {OK: "", INERT: "[inert] ", UNKNOWN: "[UNKNOWN] "}
    for leg in view.legs:
        age = f" ({fmt_age(leg.age_s)})" if leg.age_s is not None else ""
        flag = "[FAILING] " if leg.failing and leg.status == OK else ""
        out.append(f"── {label[leg.status]}{flag}{leg.title}{age}")
        if leg.status == OK:
            out.extend("  " + ln for ln in leg.lines)
        else:
            out.append("  " + leg.why)
        if leg.source:
            out.append(f"  source: {leg.source}")
        out.append("")
    for app, path in view.peer_records:
        out += [f"── [not read] {app} delivery record", f"  source: {path}", ""]
    for app, path, mtime in view.peer_leftovers:
        when = time.strftime("%Y-%m-%d", time.localtime(mtime))
        out += [f"── [leftover] {app} delivery file, last written {when}",
                f"  its writer unit is absent, disabled or masked here — a "
                f"leftover file, not a delivery organ", f"  source: {path}", ""]
    out += ["Every number above was read from a file on THIS box, with its age.",
            "Other boxes: `ssh <box>` and open this screen there, or /fleet.",
            "This screen never changes anything."]
    return "\n".join(out)


# ── BIC-2: per-route delivery legs ────────────────────────────────────
# "Leg" above means one SOURCE of this screen; a ROUTE leg is a direction a
# message crossed the bridge (``<src>><dst>`` transport). Read from the same
# file the gateway publishes; every counter honesty rule must survive here.

# Test-pinned to gateway.delivery_legs WINDOWS / OUTCOMES (hfm #5/#7): the
# TUI must not import the gateway package (it pulls RNS), so the two share
# the names by test, not by import.
_ROUTE_WINDOW_SECS = (("15m", 900), ("1h", 3600), ("24h", 86400))
_ROUTE_WINDOWS = tuple(label for label, _s in _ROUTE_WINDOW_SECS)
_ROUTE_OUTCOMES = ("queued", "sent", "confirmed", "failed", "dropped_other",
                   "refused")
# dialog --textbox is 78 wide incl. border + scrollbar: ~74 usable. The
# shared render() indents section lines by 2 AFTER this wrap, so prose
# wraps at 70 (re-review #1/#2: the caveats are what a clipped line loses).
_BOX_INTERIOR = 74
_ROUTE_WIDTH = _BOX_INTERIOR - 4
_ROW = re.compile(r"^\s*(\S+\s+(15m|1h|24h)\s|route\s+win\s)")


def _wrap(text: str, indent: str = "") -> List[str]:
    return textwrap.wrap(text, width=_ROUTE_WIDTH, initial_indent=indent,
                         subsequent_indent=indent + "  ") or [indent]


def _int_count(v) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _route_row(name: str, label: str, win) -> str:
    # A row is never wrapped (its cells would leave their headers, #3).
    name = name if len(name) <= 20 else name[:19] + "…"
    if win is None:
        return f"  {name:<20} {label:>4}  — nothing observed"
    if not isinstance(win, dict):
        return f"  {name:<20} {label:>4}  ? misshaped window (not shown)"
    cells = [win.get(k, 0) for k in _ROUTE_OUTCOMES]
    cells = [c if _int_count(c) is not None else "?" for c in cells]
    return (f"  {name:<20} {label:>4}  " +
            " ".join(f"{c!s:>{w}}" for c, w in zip(cells, (6, 5, 9, 6, 5, 7))))


def _window_reach(bucket_s) -> str:
    """How far each window REALLY reaches back: it sums every bucket that
    overlaps it, so up to one bucket past its label. Derived from the size
    the writer publishes — from_ts is the oldest bucket that HAD events,
    not the window's start (the live render read it as "15m spans 5 min")."""
    b = _int_count(bucket_s)
    if not b or b <= 0:
        return ("Windows sum whole buckets; bucket size not published (an "
                "older gateway) — window edges are approximate.")

    def _dur(sec: int) -> str:
        if sec % 60:
            return f"{sec} s"
        m = sec // 60
        return f"{m} min" if m < 180 else f"{m // 60} h {m % 60} min"
    unit = f"{b // 60}-min" if b % 60 == 0 else f"{b}-s"
    reach = [f"{label} up to {_dur(sec + b)}" for label, sec in _ROUTE_WINDOW_SECS]
    return (f"Windows sum whole {unit} buckets: {reach[0].replace(' up', ' reaches up', 1)}, "
            + ", ".join(reach[1:]) + ".")


def route_legs_leg(home: str, now: float, gw) -> Leg:
    """The gateway's per-route counts as one screen section (BIC-2).
    ``gw`` is the gateway unit state, or a callable that resolves it — only
    consulted when the record is absent (review #7: no systemctl on a fresh
    record)."""
    title = "Delivery legs (per route: 15m / 1h / 24h)"
    path = os.path.join(home, _DELIVERY_SNAPSHOT_SUBPATH)
    snap, age, why = _read_published(path, "snapshot", now)
    if snap is None:
        if why == "absent":
            return _missing_record_leg(title, path, gw() if callable(gw) else gw)
        return Leg(title, UNKNOWN, path, age, why=why)
    if "legs" not in snap:
        return Leg(title, UNKNOWN, path, age,
                   why="the gateway's record carries no `legs` — an older "
                       "gateway that does not count per route (deploy it); "
                       "this is not the same as no traffic")
    legs = snap.get("legs")
    if not isinstance(legs, dict):
        return Leg(title, UNKNOWN, path, age,
                   why="the gateway could not read its own leg counts "
                       "(legs unobservable) — no number is shown")
    leg = Leg(title, OK, path, age)
    since = _num(snap.get("legs_since_ts"))
    partial_raw = snap.get("legs_partial_windows")
    if isinstance(partial_raw, list):
        partial = [w for w in partial_raw if w in _ROUTE_WINDOWS]
    elif since is not None:   # misshaped, but computable from the start (#4)
        partial = [label for label, sec in _ROUTE_WINDOW_SECS
                   if since > now - sec]
    else:
        partial = list(_ROUTE_WINDOWS)
    if since is None and not legs:
        leg.lines += _wrap("Counting is armed but has not counted its first "
                           "event yet — every window is empty by construction.")
    elif since is None:
        # leg_meta lost while buckets survived (review #4): counts exist, the
        # start does not — every window is partial, never "empty".
        leg.lines += _wrap("Counting start is UNKNOWN (its stamp is missing) — "
                           "every window is PARTIAL.")
    else:
        leg.lines.append(f"Counting since {fmt_age(now - since)[:-4]} ago.")
        if partial:
            leg.lines += _wrap(f"PARTIAL: {', '.join(partial)} reach back "
                               f"before counting began — fewer events, not "
                               f"fewer losses.")
    errors = _int_count(snap.get("legs_write_errors"))
    if errors is not None and errors > 0:   # a negative count is no alarm
        last = _num(snap.get("legs_last_write_error_ts"))
        when = f", last {fmt_age(now - last)}" if last is not None else ""
        leg.lines += _wrap(f"⚠ {errors} leg write error(s){when} — counts may "
                           f"be LOW; the core delivery record is unaffected.")
    if not legs:
        if since is not None:
            leg.lines.append("Counting, but no leg has counted an event "
                             "inside 24h.")
        return leg
    leg.lines.append("")
    leg.lines.append(f"  {'route':<20} {'win':>4}  queued  sent confirmed "
                     f"failed other refused")
    for name in sorted(legs):
        windows = legs.get(name)
        if not isinstance(windows, dict):
            # One bad route is that route's problem, not the pane's (#1).
            leg.lines.append(f"  {name:<20}       ? misshaped (not shown)")
            continue
        for label in _ROUTE_WINDOWS:
            leg.lines.append(_route_row(name, label, windows.get(label)))
    leg.lines.append("")
    leg.lines += _wrap("— = nothing observed in that window (not zero).")
    leg.lines += _wrap(_window_reach(snap.get("legs_bucket_s")))
    if any(n.startswith("?>") for n in legs):
        leg.lines += _wrap("?>… = the sender did not name its origin "
                           "(counted, never guessed onto a real leg).")
    leg.lines += _wrap("sent − confirmed is NOT a loss count: broadcasts and "
                       "un-acked DMs can never confirm. A loss is `failed`.")
    if any(isinstance(w, dict) and _int_count(w.get("refused"))
           for ws in legs.values() if isinstance(ws, dict) for w in ws.values()):
        leg.lines += _wrap("refused = the ingress allowlist declined it AFTER "
                           "the sender's LXMF read it delivered — the sender "
                           "was not told (journal: `RNS ingress REFUSED`).")
    return leg


def render_route_legs(home: Optional[str] = None, now: Optional[float] = None,
                      gateway_resolver=None, unit_enabled_fn=None) -> str:
    """The per-route pane for THIS box. Read-only; a section that cannot see
    says UNKNOWN, and one misshaped route never blinds the others."""
    now = time.time() if now is None else now
    if home is None:
        from utils.paths import get_real_user_home
        home = str(get_real_user_home())
    leg = route_legs_leg(
        home, now, lambda: _gateway_state(gateway_resolver, unit_enabled_fn))
    view = DeliveryView(host=socket.gethostname(), now=now, legs=[leg],
                        peer_records=[], peer_leftovers=[])
    lines = render(view).split("\n")
    lines[0] = "DELIVERY LEGS" + lines[0][len("DELIVERY"):]
    # The shared headline speaks for the WHOLE Delivery screen ("arriving",
    # "no gateway and no soak timer"); this pane measured one record, so it
    # says only that (reviews: "arriving" 10-23, inert #3).
    if leg.status == OK:
        head = ("COUNTS ONLY — per-route events from this box's gateway; no "
                "rate is judged here. The verdict is the Delivery screen.")
    elif leg.status == INERT:   # its why is printed once, in the section
        head = "INERT — this box publishes no per-route record (why below)."
    else:
        head = ("UNKNOWN — the per-route record could not be read; do not "
                "read this pane as the legs being quiet.")
    lines[1:2] = _wrap(head)
    out = []
    for ln in lines:   # the shared section's prose too; table rows never
        if len(ln) <= _BOX_INTERIOR or _ROW.match(ln):
            out.append(ln)
        else:
            ind = ln[:len(ln) - len(ln.lstrip())]
            out += textwrap.wrap(ln.strip(), width=_BOX_INTERIOR,
                                 initial_indent=ind,
                                 subsequent_indent=ind + "  ",
                                 break_long_words=True) or [ind]
    return "\n".join(out)


if __name__ == "__main__":  # pragma: no cover — operator/CLI convenience
    print(render(gather()))
