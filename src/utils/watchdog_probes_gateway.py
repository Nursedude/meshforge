"""Watchdog probes — gateway delivery-path failure shapes.

Delivery write canary (#63), queue backlog (#74), delivery confirmation
stall (#74), cross-gateway duplicate delivery (dedup arc). Part of the
``watchdog_probes`` split (2026-06-09) — import via the
``utils.watchdog_probes`` hub, not from here. The 2026-06 delivery-flow
observer probes (synth-soak, gateway-delivery-degraded, resource-canary,
oracle-delivery) were split into ``watchdog_probes_gateway_flow`` (2026-07-14,
MF025 size cap); this module re-exports them for back-compat.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
from typing import List, Optional, Tuple
from urllib.request import urlopen
from urllib.error import URLError

from utils.watchdog_probe_core import (
    Signal,
    _journal_count_match,
    _load_parity_streak,
    _resolve_main_pid_status,
    _save_parity_streak,
    _short_unix_ts,
    note_disposition,
    note_unit_presence_gate,
    operator_cron_wired,
)

# Same logger name the runner uses (watchdog_runner.py) so a swallowed
# state-write failure lands in the one "watchdog" namespace the operator
# already greps — honest_failure_modes #9 ("every swallow gets a witness").
logger = logging.getLogger("watchdog")

# ─────────────────────────────────────────────────────────────────────
# Shared payload fetch for the two /api/gateway/delivery consumers
# (delivery_write_canary + delivery_confirmation_stall)
# ─────────────────────────────────────────────────────────────────────

#: Reader halves of ``gateway.delivery_counters.DELIVERY_SNAPSHOT_STATE_SUBPATH``
#: and ``gateway.message_queue.QUEUE_STATS_STATE_SUBPATH`` — two consumers of
#: one artifact share ONE constant (honest_failure_modes #5); tests pin each
#: pair equal rather than importing the gateway modules into the watchdog at
#: probe load.
_DELIVERY_SNAPSHOT_SUBPATH = os.path.join(
    ".local", "share", "meshforge", "delivery_snapshot.json")
_QUEUE_STATS_SUBPATH = os.path.join(
    ".local", "share", "meshforge", "queue_stats.json")

#: The gateway publishes every ~60s (both files ride rns_bridge's
#: content_id_view throttle). Older than this and the file is a corpse: the
#: gateway stopped publishing, which is NOT an observation.
_DELIVERY_SNAPSHOT_FRESH_S = 300.0

#: Wall-clock is forgeable on RTC-less Pis (honest_failure_modes #6) — a
#: ts this far in the future is a clock artifact, not an observation.
_DELIVERY_SNAPSHOT_FUTURE_SLOP_S = 900.0


def _read_gateway_state_file(
    *, state_path: Optional[str], api: str, noun: str, key: str,
    subpath: str, now: Optional[float] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """``(payload, blind_reason)`` from a gateway-published state file.

    Shared fallback for the delivery and queue probes. The envelope is
    ``{schema, host, ts, <key>: {...}}``; a missing/stale/misshaped/
    future-stamped file returns ``(None, reason)`` naming the failing leg —
    a corpse never testifies (honest_failure_modes #2/#6).
    """
    import time as _time
    now = _time.time() if now is None else now
    if state_path is None:
        home = _operator_home()
        if not home:
            return None, (f"{api} unreachable and operator home "
                          "unresolvable — no fallback state to read")
        state_path = os.path.join(home, subpath)
    if not os.path.exists(state_path):
        return None, (f"{api} unreachable and no {noun} state file — "
                      "no gateway publishing here")
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            wrapped = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None, f"{api} unreachable and {noun} state file unreadable"
    if not isinstance(wrapped, dict) or not isinstance(
            wrapped.get(key), dict):
        return None, f"{api} unreachable and {noun} state file misshaped"
    ts = wrapped.get("ts")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return None, f"{api} unreachable and {noun} state ts missing/garbage"
    age = now - float(ts)
    if age < -_DELIVERY_SNAPSHOT_FUTURE_SLOP_S:
        return None, (f"{api} unreachable and {noun} state ts is in the "
                      "future — clock artifact, not an observation")
    if age > _DELIVERY_SNAPSHOT_FRESH_S:
        return None, (f"{api} unreachable and {noun} state stale "
                      f"({int(age)}s) — gateway not publishing")
    return wrapped[key], None


def _fetch_delivery_payload(
    host: str, port: int, timeout_s: float,
    *, state_path: Optional[str] = None, now: Optional[float] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """``(payload, blind_reason)`` for the delivery-counters snapshot.

    HTTP first — ``/api/gateway/delivery`` on the local map, the normal
    path on every map-running box. When :5000 is unreachable the box may be
    the gateway-only shape (moc3, 2026-07-31: meshforge-map disabled BY
    DESIGN, so the delivery probes sat permanently detector-blind while the
    gateway's own truth was on disk the whole time) — fall back to the
    snapshot state file the gateway publishes next to its DB. NEVER the
    SQLite DB itself: a root reader can strand root-owned WAL/SHM files in
    the operator's data dir (the #60-class trap).

    A payload is returned ONLY from a live source. Transport failure plus a
    missing/stale/misshaped/future-stamped file is ``(None, reason)`` — the
    reason names which leg failed and how, so the detector_blind annotation
    carries the real cause instead of the old blanket "unreachable".
    """
    url = f"http://{host}:{port}/api/gateway/delivery"
    try:
        with urlopen(url, timeout=timeout_s) as resp:
            payload = json.loads(resp.read())
        if isinstance(payload, dict):
            return payload, None
        return None, "delivery payload not a dict"
    except (URLError, socket.timeout, json.JSONDecodeError, OSError,
            ValueError):
        pass
    return _read_gateway_state_file(
        state_path=state_path, api="delivery API", noun="delivery snapshot",
        key="snapshot", subpath=_DELIVERY_SNAPSHOT_SUBPATH, now=now)


def _fetch_queue_payload(
    host: str, port: int, timeout_s: float,
    *, state_path: Optional[str] = None, now: Optional[float] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """``(payload, blind_reason)`` for the queue stats — the queue-side twin
    of ``_fetch_delivery_payload``: ``/api/gateway/queue`` over HTTP first,
    then the ``queue_stats.json`` state file the gateway publishes (same
    moc3 gateway-only rationale, same corpse-refusal guards)."""
    url = f"http://{host}:{port}/api/gateway/queue"
    try:
        with urlopen(url, timeout=timeout_s) as resp:
            payload = json.loads(resp.read())
        if isinstance(payload, dict):
            return payload, None
        return None, "queue payload not a dict"
    except (URLError, socket.timeout, json.JSONDecodeError, OSError,
            ValueError):
        pass
    return _read_gateway_state_file(
        state_path=state_path, api="queue API", noun="queue stats",
        key="stats", subpath=_QUEUE_STATS_SUBPATH, now=now)


# ─────────────────────────────────────────────────────────────────────
# Probe: delivery counters write canary (Issue #63)
# ─────────────────────────────────────────────────────────────────────


def probe_delivery_write_canary(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout_s: float = 3.0,
    error_threshold: int = 3,
    snapshot_state_path: Optional[str] = None,
) -> Optional[Signal]:
    """Read ``/api/gateway/delivery.health`` and surface preflight/runtime errors.

    Reuses the gateway's existing self-reported health block (Issue #63).
    The watchdog itself doesn't re-probe SQLite — that's the gateway's
    job; we just amplify the signal so an operator skimming /fleet sees
    "delivery counters can't write" the same way they see "RNS wedged".

    On a gateway-only box (no map serving :5000) the payload comes from the
    gateway-published snapshot state file instead — see
    ``_fetch_delivery_payload``. ``snapshot_state_path`` overrides the
    fallback file location (test seam).

    Severities:
      - wedge: preflight_ok=False (every write attempt is failing)
      - degraded: consecutive_write_errors >= error_threshold
      - None: healthy, or endpoint unreachable (a different probe surfaces that)
    """
    payload, blind = _fetch_delivery_payload(
        host, port, timeout_s, state_path=snapshot_state_path)
    if payload is None:
        note_disposition("delivery_write_canary", "indeterminate",
                         reason=blind)
        return None  # don't false-alarm on transport problems

    health = payload.get("health") if isinstance(payload, dict) else None
    if not isinstance(health, dict):
        note_disposition("delivery_write_canary", "indeterminate",
                         reason="no health block in delivery payload")
        return None

    # G3 (2026-07-18): the producer marks the health block unobservable
    # when its snapshot() DB read failed (delivery DB became unreadable
    # AFTER startup) — the remaining fields are the reader's stale startup
    # state, not an observation. Never let that read clean.
    if health.get("db_unobservable") is True:
        note_disposition("delivery_write_canary", "indeterminate",
                         reason="delivery DB unobservable — snapshot failed")
        return None

    preflight_ok = health.get("preflight_ok")
    consecutive = health.get("consecutive_write_errors") or 0
    last_err = health.get("last_write_error")
    db_path = health.get("db_path", "?")

    if preflight_ok is False:
        return Signal(
            cls="delivery_write_canary",
            subject="meshforge-gateway",
            severity="wedge",
            detail=(
                f"delivery_counters preflight FAILED at {db_path}. "
                f"Every record() is failing. Most likely systemd sandbox "
                f"ReadWritePaths missing the data bucket (Issue #58/#60). "
                f"Last error: {last_err}"
            ),
            issue_ref=63,
            extra={
                "db_path": db_path,
                "consecutive_write_errors": consecutive,
                "last_write_error": last_err,
            },
        )

    try:
        consec_int = int(consecutive)
    except (TypeError, ValueError):
        # Worst-wins: this note outranks the clean note at the exit below.
        note_disposition("delivery_write_canary", "indeterminate",
                         reason="consecutive_write_errors unparseable")
        consec_int = 0
    if consec_int >= error_threshold:
        return Signal(
            cls="delivery_write_canary",
            subject="meshforge-gateway",
            severity="degraded",
            detail=(
                f"delivery_counters has {consec_int} consecutive write "
                f"errors. Last: {last_err}. Threshold for alarm: "
                f"{error_threshold}."
            ),
            issue_ref=63,
            extra={
                "consecutive_write_errors": consec_int,
                "last_write_error": last_err,
            },
        )

    note_disposition("delivery_write_canary", "clean")
    return None


# ─────────────────────────────────────────────────────────────────────
# Probe: queue backpressure (Issue #74)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_QUEUE_BACKLOG_STATE_PATH = "/var/lib/meshforge/queue_backlog_debounce.json"


def _load_dead_letter_baseline(state_path: str) -> Optional[int]:
    """Read the last-seen dead_letter count. Best-effort: any error → None.

    None means 'no baseline yet' — the first observation establishes
    the baseline and never fires, so a long-uptime box with a static
    historical dead-letter pile doesn't false-alarm on watchdog
    restart (the probe judges GROWTH, not absolute count).
    """
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            value = json.load(fh).get("dead_letter")
        return int(value) if value is not None and int(value) >= 0 else None
    except (OSError, ValueError, TypeError):
        return None


def _save_dead_letter_baseline(state_path: str, count: int) -> None:
    """Persist the dead_letter baseline (atomic-rename, never raises)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"dead_letter": int(count)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        pass


def probe_queue_backlog(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout_s: float = 3.0,
    depth_degraded: float = 0.80,
    depth_wedge: float = 0.95,
    dead_letter_growth_degraded: int = 10,
    dead_letter_growth_wedge: int = 50,
    state_path: Optional[str] = None,
    stats_state_path: Optional[str] = None,
) -> Optional[Signal]:
    """Persistent-queue backpressure via ``/api/gateway/queue`` (Issue #74).

    A deep backlog masks delivery failures: messages sit 'pending'
    while the operator reads the gateway as healthy, and at the shed
    threshold ``_shed_overflow`` starts silently dropping LOW/NORMAL
    priority. Two legs, max severity wins:

      - depth: queue_depth / max_queue_size ≥ 95% → wedge (shed is
        imminent/active), ≥ 80% → degraded. Skipped when
        max_queue_size ≤ 0 (unlimited — no ceiling to judge against,
        mirrors the fd probe's "unlimited" guard).
      - dead-letter GROWTH since the last tick (baseline persisted to
        ``state_path``, parity-debounce pattern): ≥ 50 new → wedge
        (retries exhausting en masse), ≥ 10 new → degraded. A static
        historical pile never fires.

    Reads over localhost HTTP, never the queue DB directly — the
    watchdog is root in a hardened sandbox and the DB lives under the
    operator's home (the #60-class trap; derive context from the
    SERVICE). On a gateway-only box (no map serving :5000) the payload
    comes from the gateway-published queue stats state file instead —
    see ``_fetch_queue_payload``; ``stats_state_path`` overrides the
    fallback file location (test seam; distinct from ``state_path``,
    the dead-letter baseline). Transport/shape errors → None
    (http_local / service_inactive own those).
    """
    payload, blind = _fetch_queue_payload(
        host, port, timeout_s, state_path=stats_state_path)
    if payload is None:
        note_disposition("queue_backlog", "indeterminate", reason=blind)
        return None
    if not isinstance(payload, dict) or "queue_depth" not in payload:
        note_disposition("queue_backlog", "indeterminate",
                         reason="queue payload missing/misshaped")
        return None

    try:
        queue_depth = int(payload.get("queue_depth") or 0)
        max_queue_size = int(payload.get("max_queue_size") or 0)
        dead_letter = int(payload.get("dead_letter") or 0)
    except (TypeError, ValueError):
        note_disposition("queue_backlog", "indeterminate",
                         reason="queue counters unparseable")
        return None

    findings: List[Tuple[str, str]] = []  # (severity, detail-fragment)

    if max_queue_size > 0:
        usage = queue_depth / max_queue_size
        if usage >= depth_wedge:
            findings.append((
                "wedge",
                f"queue at {usage:.0%} of max ({queue_depth}/"
                f"{max_queue_size}) — shed threshold; LOW/NORMAL "
                f"priority messages are being dropped",
            ))
        elif usage >= depth_degraded:
            findings.append((
                "degraded",
                f"queue backlog building: {usage:.0%} of max "
                f"({queue_depth}/{max_queue_size})",
            ))

    sp = state_path or DEFAULT_QUEUE_BACKLOG_STATE_PATH
    baseline = _load_dead_letter_baseline(sp)
    _save_dead_letter_baseline(sp, dead_letter)
    if baseline is not None:
        growth = dead_letter - baseline
        if growth >= dead_letter_growth_wedge:
            findings.append((
                "wedge",
                f"dead-letter spiked +{growth} since last tick "
                f"(now {dead_letter}) — retries exhausting en masse",
            ))
        elif growth >= dead_letter_growth_degraded:
            findings.append((
                "degraded",
                f"dead-letter grew +{growth} since last tick "
                f"(now {dead_letter})",
            ))

    if not findings:
        if max_queue_size <= 0:
            note_disposition("queue_backlog", "indeterminate",
                             reason="unlimited queue — no depth ceiling to judge")
        elif baseline is None:
            note_disposition("queue_backlog", "indeterminate",
                             reason="no dead-letter baseline yet (first tick)")
        else:
            note_disposition("queue_backlog", "clean")
        return None

    severity = "wedge" if any(s == "wedge" for s, _ in findings) else "degraded"
    return Signal(
        cls="queue_backlog",
        subject="meshforge-gateway",
        severity=severity,
        detail=(
            "Persistent queue backpressure: "
            + "; ".join(d for _, d in findings)
            + ". Check /api/gateway/queue and the gateway journal for "
            "the failing destination."
        ),
        issue_ref=74,
        extra={
            "queue_depth": queue_depth,
            "max_queue_size": max_queue_size,
            "dead_letter": dead_letter,
            "dead_letter_baseline": baseline,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: delivery confirmation stall (Issue #74)
# ─────────────────────────────────────────────────────────────────────


# Drop reasons that mean a delivery was ATTEMPTED and FAILED — the
# denominator-mates of `confirmed` for a confirmable protocol. Benign
# drops (dedup, capacity shedding) are NOT delivery failures and must
# never count against the confirmation rate.
_DELIVERY_FAILURE_REASONS = frozenset({
    "rns_delivery_failed", "retries_exhausted", "destination_unreachable",
    "delivery_timeout", "non_retriable_error", "circuit_open", "wedged",
})

#: Protocols that HAVE a confirmation mechanism today. Membership means "an
#: absence of `confirmed` events for this protocol is a fact about the
#: WIRING, not about the protocol" — the discriminator that lets the
#: never-confirmed-anything case below be a signal instead of a shrug.
#: Meshtastic joins this set once ROUTING_APP ACK consumption lands.
_CONFIRMABLE_CAPABLE_PROTOCOLS = frozenset({"rns"})

#: Confirmable-capable terminal events (sent + failed drops) needed before
#: "zero confirmations EVER" is called a fault rather than a quiet box.
#: Set well above incidental traffic: the live federator box carried ~11,800
#: when this was written, and both real gateways confirm in the tens of
#: thousands, so no healthy fleet box is anywhere near this boundary.
_NEVER_CONFIRMED_MIN_TERMINAL = 50


def _count(bucket, proto) -> int:
    """One protocol's count out of a state bucket; 0 for anything non-numeric
    (a misshaped payload must not manufacture traffic)."""
    v = (bucket or {}).get(proto)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0
    return int(v) if v > 0 else 0


def _never_confirmed_signal(by_proto: dict) -> Optional[Signal]:
    """A confirmable-capable protocol terminated real traffic and has NEVER
    recorded a confirmation → the confirmation channel is unwired.

    Deliberately cumulative, not windowed: the claim is "not once, ever",
    which is precisely what a windowed rate cannot express. Returns None
    (caller falls through to indeterminate) unless the evidence is
    unambiguous — a gateway with no RNS traffic at all is quiet, not broken.
    """
    sent, dropped = by_proto.get("sent") or {}, by_proto.get("dropped") or {}
    confirmed = by_proto.get("confirmed") or {}
    worst = None
    for proto in sorted(_CONFIRMABLE_CAPABLE_PROTOCOLS):
        # SELF-GUARD, not redundancy: the caller only reaches here when no
        # protocol has confirmed, but a helper that answers wrongly on its
        # own inputs is a trap for the next caller. "Never confirmed" must
        # mean it, whoever asks.
        raw = (confirmed or {}).get(proto)
        if raw is not None and (isinstance(raw, bool)
                                or not isinstance(raw, (int, float))):
            continue  # present but not a count → UNOBSERVABLE, never "zero"
        if _count(confirmed, proto) > 0:
            continue
        terminal = _count(sent, proto) + _count(dropped, proto)
        if terminal >= _NEVER_CONFIRMED_MIN_TERMINAL:
            if worst is None or terminal > worst[1]:
                worst = (proto, terminal, _count(sent, proto),
                         _count(dropped, proto))
    if worst is None:
        return None
    proto, terminal, n_sent, n_dropped = worst
    return Signal(
        cls="delivery_confirmation_stall",
        subject=f"{proto}:never-confirmed",
        severity="degraded",
        detail=(
            f"{proto} has terminated {terminal} deliveries on this gateway "
            f"({n_sent} sent, {n_dropped} dropped) and has recorded ZERO "
            f"confirmations — not a low rate, none at all, ever. {proto} "
            f"has a confirmation mechanism, so an empty `confirmed` bucket "
            f"is a fact about the WIRING, not the protocol: the ack/proof "
            f"path is not recording. Until 2026-08-05 this state was "
            f"structurally invisible — the rate guard needed at least one "
            f"confirmation to exist before it could judge, so a TOTAL "
            f"collapse read as 'nothing to judge' while a partial one "
            f"fired. Check the gateway's ack tracker is running and that "
            f"delivery proofs reach delivery_counters; compare a healthy "
            f"gateway's state_proto.confirmed.{proto}. See the 2026-08-05 "
            f"persistent_issues entry."
        ),
        issue_ref=74,
        extra={"protocol": proto, "terminal_events": terminal,
               "sent": n_sent, "dropped": n_dropped, "confirmed": 0},
    )


def probe_delivery_confirmation_stall(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout_s: float = 3.0,
    min_terminal: int = 20,
    rate_degraded: float = 0.50,
    rate_wedge: float = 0.10,
    snapshot_state_path: Optional[str] = None,
    gateway_unit: str = "meshforge-gateway.service",
    gateway_main_pid: Optional[int] = None,
    # Present so the organ gate's UNKNOWN branch can be drilled against real
    # systemd, exactly as its twin probe_gateway_delivery_degraded already
    # allows. Added 2026-08-12 after the end-of-session double tap: trying to
    # live-drill that branch here, I had to plant a nonexistent UNIT NAME
    # instead — which is the ABSENT branch — and very nearly filed the result
    # under UNKNOWN. Two probes that mirror each other must be equally
    # testable, or the drill silently exercises the wrong path.
    systemctl_path: str = "systemctl",
) -> Optional[Signal]:
    """A confirmable protocol's deliveries are failing instead of confirming
    (Issue #74).

    Closes the honest-signal gap where the bridge's own status reads HEALTHY
    while nothing confirms. Judges a WINDOWED rate from the snapshot's
    recent-events ring (``SNAPSHOT_RECENT_LIMIT`` events, newest-last; 200
    since 2026-08-10 — at 50 a mesh-heavy gateway's ring held fewer
    confirmable terminals than ``min_terminal`` and this probe flapped
    blind on moc for weeks) — the lifetime-cumulative rate would mask a
    recent collapse.

    CRUCIAL: judges ONLY protocols that actually have a confirmation mechanism
    (record `confirmed` events — RNS today; Meshtastic too once ROUTING_APP
    ACK consumption lands), and compares that protocol's two REAL terminal
    outcomes — `confirmed` vs a failed-delivery `dropped` — NOT the meaningless
    cross-population `confirmed/sent` ratio. The counters use disjoint lifecycle
    states per protocol (RNS: queued→confirmed, never `sent`; Meshtastic:
    queued→sent, never `confirmed`), so `confirmed/sent` was (RNS-confirmed ÷
    Meshtastic-sent) — two different message populations that never measured a
    coherent rate and false-alarmed ~50% on every mesh-heavy gateway.

    On a gateway-only box (no map serving :5000) the payload comes from the
    gateway-published snapshot state file instead — see
    ``_fetch_delivery_payload``. ``snapshot_state_path`` overrides the
    fallback file location (test seam).

    Self-guards (silence is NOT failure here — the explicit inversion of
    channel_feed_dark):
      - transport/shape errors → None (other probes own those)
      - no confirmable protocol → None (nothing tracks confirmation; e.g. an
        RNS-less box — Meshtastic has no ACK)
      - confirmable terminal events < min_terminal → None (low-traffic /
        small-sample box: one failure tanks a tiny denominator — honest
        over too small a sample. The ring is sized so a busy gateway
        clears this floor comfortably; see the cross-constant test)

    Severities: rate ≤ 10% → wedge, ≤ 50% → degraded.
    """
    # Organ presence FIRST. Without a gateway on this box there is no
    # delivery organ to stall, so the honest answer is INERT — what the
    # sibling gateway_delivery_degraded has always said. Until 2026-08-05
    # this probe instead fell through to "no confirmable protocol recorded"
    # and sat INDETERMINATE forever on every non-gateway box, which both
    # buried a real blindness and made the disposition meaningless.
    if gateway_main_pid is not None:
        gw_status, gw_pid = "ok", gateway_main_pid
    else:
        gw_status, gw_pid = _resolve_main_pid_status(
            gateway_unit, systemctl_path=systemctl_path)
    if gw_pid is None:
        # 2026-08-12: absent/stopped are both honestly INERT here (a
        # stopped-but-installed gateway is service_inactive's to page), but
        # a systemctl we could not RUN is not an observation that this box
        # has no gateway — that one is unobservable and says so. Policy in
        # ONE place (2026-08-12 review).
        note_unit_presence_gate(
            "delivery_confirmation_stall", gw_status,
            stopped_is_inert=True,
            absent_reason=f"gateway not running on this box ({gw_status})",
            unresolved_reason=(f"{gateway_unit} state unobservable; cannot tell "
                               f"whether a gateway organ exists here"))
        return None

    payload, blind = _fetch_delivery_payload(
        host, port, timeout_s, state_path=snapshot_state_path)
    if payload is None:
        note_disposition("delivery_confirmation_stall", "indeterminate",
                         reason=blind)
        return None

    # Confirmable = protocols that have ever recorded a `confirmed` event.
    # Meshtastic isn't here until ACK consumption exists, so its
    # structurally-unconfirmable sends never drag the rate.
    by_proto = payload.get("state_by_protocol") or {}
    confirmed_by_proto = by_proto.get("confirmed") or {}
    confirmable = {
        p for p, c in confirmed_by_proto.items()
        if isinstance(c, (int, float)) and not isinstance(c, bool) and c > 0
    }
    if not confirmable:
        # ⚠️ THE BLIND SPOT this guard used to be (2026-08-05): "no protocol
        # has ever confirmed" was treated as nothing-to-judge, so a TOTAL,
        # permanent confirmation collapse — the most extreme form of exactly
        # the #74 class this probe exists for — could never fire, while a
        # partial one did. The detector only worked once at least one
        # confirmation had been recorded.
        #
        # Discriminate: if a CONFIRMABLE-CAPABLE protocol is terminating
        # real traffic and has never once confirmed, the confirmation
        # channel is unwired, not absent. Anything else (mesh-only gateway,
        # quiet box) stays the honest indeterminate.
        sig = _never_confirmed_signal(by_proto)
        if sig is not None:
            return sig
        note_disposition("delivery_confirmation_stall", "indeterminate",
                         reason="no confirmable protocol recorded — cannot judge")
        return None

    recent = payload.get("recent")
    if not isinstance(recent, list):
        note_disposition("delivery_confirmation_stall", "indeterminate",
                         reason="recent-events ring absent/misshaped")
        return None

    ring_confirmed = 0
    ring_failed = 0
    for e in recent:
        if not isinstance(e, dict) or e.get("protocol") not in confirmable:
            continue
        st = e.get("state")
        if st == "confirmed":
            ring_confirmed += 1
        elif st == "dropped" and e.get("drop_reason") in _DELIVERY_FAILURE_REASONS:
            ring_failed += 1

    terminal = ring_confirmed + ring_failed
    if terminal < min_terminal:
        note_disposition("delivery_confirmation_stall", "indeterminate",
                         reason="too few confirmable terminal events to judge")
        return None

    ring_rate = ring_confirmed / terminal
    if ring_rate > rate_degraded:
        note_disposition("delivery_confirmation_stall", "clean")
        return None

    severity = "wedge" if ring_rate <= rate_wedge else "degraded"
    protos = ", ".join(sorted(confirmable))
    return Signal(
        cls="delivery_confirmation_stall",
        subject="meshforge-gateway",
        severity=severity,
        detail=(
            f"Delivery confirmations collapsed: {ring_confirmed}/{terminal} "
            f"{protos} messages confirmed in the recent window "
            f"({ring_rate:.0%}); the rest failed delivery. Check RNS paths "
            f"to the fan-out peers and /api/gateway/delivery drop_reasons."
        ),
        issue_ref=74,
        extra={
            "confirmable": sorted(confirmable),
            "ring_confirmed": ring_confirmed,
            "ring_failed": ring_failed,
            "terminal": terminal,
            "ring_rate": round(ring_rate, 3),
            "cumulative_confirmation_rate": payload.get("confirmation_rate"),
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Probe: cross-gateway duplicate delivery (dedup/identity arc STEP 5)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_GATEWAY_DUP_DEBOUNCE_PATH = "/var/lib/meshforge/gateway_dup_debounce.json"


def _load_gateway_dup_streak(state_path: str) -> int:
    """Consecutive-over-threshold streak. Any error → 0 (favour silence on
    uncertainty — mirrors _load_synth_streak)."""
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_gateway_dup_streak(state_path: str, streak: int) -> None:
    """Persist the debounce streak (atomic-rename, never raises)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        pass


_DUPS_COLLECTOR_TOKEN = "fleet_dup_collector"


def _dups_collector_wired_here() -> Optional[bool]:
    """Does THIS box run the ``/fleet/dups`` collector cron?

    ``True`` / ``False`` / ``None`` when the crontab cannot be read.

    This exists so the probe stops inferring its own ROLE from the ABSENCE of
    the artifact it audits (2026-07-28 review). ``/fleet/dups`` answers
    ``unavailable`` on exactly one condition — ``FileNotFoundError`` on the
    rollup state file — and off the manager that means "no collector here",
    while ON the manager it means the collector is not publishing. Reading the
    second as the first silenced a real coverage loss on the only box where
    these probes can do their job at all, with a reason that asserted "not the
    manager box" as fact. *A checker must not consume the artifact it
    validates* (persistent_issues.md).

    The independent evidence is the box's crontab spool: the manager is the
    box that runs the collector. ``None`` (unobservable) is NEVER folded into
    ``False`` — "I could not look" is not "it is not here"
    (honest_failure_modes #2). The read itself lives in the shared base, which
    already owns the spool paths (see ``operator_cron_wired`` for the
    wrapper-script caveat: keep the token visible on the crontab line).
    """
    return operator_cron_wired(_DUPS_COLLECTOR_TOKEN)


def _classify_dups_unreachable(*, suffix: str = "") -> tuple:
    """Classify an UNREACHABLE ``/fleet/dups`` endpoint honestly (2026-08-09).

    RETURNS ``(disposition, reason)`` — it does not note it. That split is
    deliberate: the caller makes the literal ``note_disposition`` call inside
    its own except-handler, so MF027 can SEE the witness while the decision
    logic still lives in exactly one place. The first version of this helper
    noted the disposition itself and I widened MF027 to accept the indirection;
    attacking that widening took one fixture (a helper that notes on one branch
    and is silent on the other passes it and still goes dark), so the gate was
    restored and the helper reshaped instead. Change the code to fit the gate,
    not the gate to fit the code.

    Sibling of ``_note_dups_rollup_not_ok`` — and the branch that fix forgot.
    2026-07-28 taught the *payload* path to read the box's role from the
    declaration instead of inferring it from the missing artifact, which cured
    every box that ANSWERS on :5000. It left the transport path above it
    collapsing the same two opposite states, so the one box shape that never
    answers at all kept the old confidently-false reason:

        moc3 is role gateway-only with meshforge-map disabled BY DESIGN
        (the 07-24 deploy incident proved starting it there is itself a
        defect). No map → urlopen raises → "manager-map down or not the
        manager box" → permanent `indeterminate`. 13.5 days, on a box that
        can never observe a cross-gateway dup in the first place.

    The declaration answers this without the endpoint: the manager is the box
    that runs the collector cron. Whether a map happens to be listening is a
    PROXY that merely correlates — on moc3 it is absent by design, on the
    manager it being down is a real coverage loss. Only the cron says which.

      - collector NOT wired here → non-manager: ``inert``. It could never see
        a cross-gateway dup, with or without a map.
      - collector IS wired here → this IS the manager and its endpoint is
        unreachable: ``indeterminate``, real fleet-wide coverage loss.
      - crontab unreadable → indistinguishable: ``indeterminate``
        (honest_failure_modes #2 — "I could not look" is not "it is not here").
    """
    wired = _dups_collector_wired_here()
    if wired is True:
        return ("indeterminate",
                "the dup collector cron IS wired on this box but its "
                "/fleet/dups endpoint is unreachable — the manager's map "
                "is down, so cross-gateway dups are unobservable "
                "fleet-wide" + suffix)
    if wired is None:
        return ("indeterminate",
                "/fleet/dups unreachable and the crontab is unreadable — "
                "cannot tell a box that never serves it from a manager "
                "whose map died" + suffix)
    return ("inert",
            "no /fleet/dups endpoint here and the collector cron is not "
            "wired on this box — expected off the manager (a box may serve "
            "no map at all by design)")


def _note_dups_rollup_not_ok(cls: str, payload: dict, *,
                             suffix: str = "") -> None:
    """Classify a non-``ok`` ``/fleet/dups`` status honestly (2026-07-28).

    TWO different states share the not-``ok`` bucket, and collapsing them was
    the defect: every non-manager box reported "rollup indeterminate (<2
    gateways reachable)" — a reason that is simply FALSE there — so 6 of 9
    boxes emitted permanent `detector_blind_any` noise that a REAL coverage
    loss on the manager would have been indistinguishable from.

    ``unavailable`` — no rollup file at all. Which of two OPPOSITE things that
    means depends on whether this box runs the collector, so the role is read
    from independent evidence (``_dups_collector_wired_here``) rather than
    inferred from the missing file itself:

      - collector NOT wired here → a non-manager box, which can never observe
        a cross-gateway dup: ``inert``. Per the ``detector_blind_any`` rule's
        own annotation, "a legitimately-absent organ is not a blind one".
      - collector IS wired here → this is the manager and it is not
        publishing: a real coverage loss on the one box that can see dups at
        all. ``indeterminate``, so it stays visible.
      - crontab unreadable → the two are indistinguishable: ``indeterminate``.
        Unobservable is never excused as benign (honest_failure_modes #2).

    ``indeterminate`` — the rollup EXISTS but the JOIN reached <2 contributing
    gateways. That is real, actionable coverage loss on the manager and must
    stay visible. The JOIN publishes its own ``indeterminate_reason`` naming
    the boxes that went uncovered; prefer it over a hardcoded guess.

    Any OTHER non-ok value is an unknown state → ``indeterminate`` (an
    unrecognised status must not decay into a benign-looking inert).
    """
    status = payload.get("status")
    if status == "unavailable":
        why = payload.get("reason")
        detail = f" ({why})" if isinstance(why, str) and why else ""
        wired = _dups_collector_wired_here()
        if wired is True:
            note_disposition(
                cls, "indeterminate",
                reason=f"the dup collector cron IS wired on this box but no "
                       f"rollup is published — the manager is not publishing, "
                       f"so cross-gateway dups are unobservable fleet-wide"
                       f"{detail}{suffix}")
            return
        if wired is None:
            note_disposition(
                cls, "indeterminate",
                reason=f"no dup rollup here and the crontab is unreadable — "
                       f"cannot tell an absent organ from a manager that "
                       f"stopped publishing{detail}{suffix}")
            return
        note_disposition(
            cls, "inert",
            reason=f"no dup rollup published on this box and the collector "
                   f"cron is not wired here — expected off the manager"
                   f"{detail}")
        return
    reason = payload.get("indeterminate_reason")
    if not (isinstance(reason, str) and reason.strip()):
        # No reason from the JOIN → say only what is KNOWN. The old fallback
        # asserted "<2 gateways reachable" as fact for ANY unrecognized
        # status — the same confidently-false-reason defect this classifier
        # removed for "unavailable", surviving in its own fallback branch
        # (2026-07-28 review). Never guess a cause.
        if status == "indeterminate":
            reason = ("rollup indeterminate and the JOIN gave no reason — "
                      "coverage state unknown")
        else:
            reason = (f"unrecognized rollup status {status!r} — "
                      f"not asserting a cause")
    note_disposition(cls, "indeterminate", reason=reason + suffix)


def probe_gateway_dup_degraded(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout_s: float = 3.0,
    min_dup_pairs: int = 1,
    debounce_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional["Signal"]:
    """HUMAN-facing cross-gateway DUPLICATE delivery (dedup/identity arc STEP 6).

    Consumes the 4c cross-box rollup at ``/fleet/dups`` (the FIRST probe with
    a per-logical-message + cross-gateway dimension). A fleet DUPLICATE is the
    same ``(content_id, recipient)`` reaching CONFIRMED on >1 DISTINCT gateway
    — the same logical message delivered to a recipient from two gateways.
    The rollup only exists on the manager box that runs the collector cron, so
    this probe is naturally INERT elsewhere (the endpoint is absent/unavailable
    there → None).

    STEP 6 (honest paging): the JOIN classifies each dup recipient as INFRA
    (itself a gateway/peer hash — e.g. MeshAnchor ``58cecbd0``, in BOTH
    gateways' ``peer_gateway_destinations``, so both legitimately relay there)
    vs HUMAN (a real NomadNet inbox). The infra-to-infra dup is a real dup but
    benign AND structurally unsuppressable without a cross-gateway coordination
    substrate (the live dup-A measured here, ~0 human / a steady infra
    residual). This probe therefore pages ONLY on the HUMAN count; the infra
    residual is surfaced in the detail/extra but never pages.

    degraded only — a duplicate is a quality/cost defect, not an outage
    (delivery still happened). Honest self-guards (honest_failure_modes #2 —
    absence of evidence is NOT evidence of absence; the whole point of the 4c
    JOIN's indeterminate gate):
      - endpoint unreachable → None, HOLD; the DECLARATION (collector cron)
        decides the disposition, not the missing endpoint: off the manager
        ``inert`` (a gateway-only box serves no map BY DESIGN — moc3), on the
        manager ``indeterminate`` (its map is down = real coverage loss),
        crontab unreadable ``indeterminate``. See
        ``_note_dups_rollup_unreachable`` (2026-08-09)
      - non-dict / shape error → None (other probes own transport; the streak
        is HELD, not reset — unobservable ≠ healthy)
      - ``status == "unavailable"`` (no rollup file — every box runs a map, so
        a NON-manager answers 200 with this, it is not a transport error) →
        None, HOLD, disposition ``inert``: structurally unobservable here, and
        a legitimately-absent organ is not a blind one
      - ``status`` otherwise != ``"ok"`` (indeterminate: <2 contributing
        gateways reachable) → None, HOLD streak, disposition ``indeterminate``
        carrying the JOIN's OWN ``indeterminate_reason`` — you CANNOT observe a
        cross-gateway dup when you can't see ≥2 gateways, so this must NEVER
        read as a healthy "0 dups"
      - ``freshness.stale`` (collector cron dead → frozen verdict) → None, HOLD
      - ``fleet_human_duplicate_pairs`` missing (a pre-STEP-6 rollup) → fall
        back to the TOTAL ``fleet_duplicate_pairs`` so an un-upgraded manager
        never silently stops paging; present-but-garbage → None/HOLD
      - gated count ``< min_dup_pairs`` → healthy, RESET streak (explicit
        observed-clean tick)
      - ≥ threshold → 2-tick debounce streak before firing (rides a torn
        mid-write rollup / one transient overlap)

    MEASURE-ONLY upstream: the probe ALERTS but never suppresses a copy.
    Cross-gateway suppression of the residual infra dup was evaluated in STEP 6
    and is structurally impossible with zero added loss + no coordination
    substrate (two gateways confirm-deliver simultaneously; copy #2 cannot be
    safely cancelled after the fact) — deferred to a future coordination-
    substrate arc. issue_ref=None — the dedup/identity arc has no GitHub
    issue#; the class is documented inline in SIGNAL_CLASSES (MF012 cap).
    """
    sp = debounce_path or DEFAULT_GATEWAY_DUP_DEBOUNCE_PATH
    url = f"http://{host}:{port}/fleet/dups"
    try:
        with urlopen(url, timeout=timeout_s) as resp:
            payload = json.loads(resp.read())
    except (URLError, socket.timeout, json.JSONDecodeError, OSError, ValueError):
        # G1 (2026-07-18): on the manager box (the only observable one) a
        # crashed map / 5xx lands here too — "unobservable ≠ clean / HOLD"
        # means this cannot read as benign inert. Off the manager it is the
        # opposite claim, so the DECLARATION decides (2026-08-09).
        _disp, _reason = _classify_dups_unreachable()
        note_disposition("gateway_dup_degraded", _disp, reason=_reason)
        return None  # transport — HOLD streak (unobservable ≠ clean)
    if not isinstance(payload, dict):
        note_disposition("gateway_dup_degraded", "indeterminate",
                         reason="dups payload not a dict")
        return None

    # indeterminate / unavailable: <2 gateways covered, or no rollup yet.
    # Cannot observe a cross-gateway dup → HOLD streak, stay INERT. A wired
    # probe treating indeterminate as green would be the exact #2 trap.
    if payload.get("status") != "ok":
        _note_dups_rollup_not_ok("gateway_dup_degraded", payload)
        return None
    fresh = payload.get("freshness")
    if isinstance(fresh, dict) and fresh.get("stale") is True:
        note_disposition("gateway_dup_degraded", "indeterminate",
                         reason="rollup stale (collector cron dead)")
        return None  # frozen rollup (dead collector) → HOLD, INERT

    dup_pairs = payload.get("fleet_duplicate_pairs")
    if not isinstance(dup_pairs, int) or isinstance(dup_pairs, bool):
        note_disposition("gateway_dup_degraded", "indeterminate",
                         reason="fleet_duplicate_pairs missing/garbage")
        return None  # shape error → INERT (don't reset on a malformed read)

    # STEP 6 — page on HUMAN-facing dups only. A dup whose recipient is
    # itself a gateway/peer (e.g. MeshAnchor 58cecbd0, in BOTH gateways'
    # peer_gateway_destinations) is infra-to-infra: a real dup, but benign
    # AND structurally unsuppressable without a cross-gateway coordination
    # substrate — so it must not page hourly. The 4c JOIN splits the count
    # by recipient kind; gate on the human number when present. An OLD JOIN
    # that predates the split OMITS the field → fall back to the TOTAL so we
    # never SILENTLY stop paging (honest_failure_modes #2/#4 — an un-upgraded
    # manager is not a forged benign zero). A PRESENT-but-garbage field is a
    # shape error → INERT/hold (same discipline as the total above).
    human_pairs = payload.get("fleet_human_duplicate_pairs")
    infra_pairs = payload.get("fleet_infra_duplicate_pairs")
    if human_pairs is None:
        gate_pairs = dup_pairs            # old JOIN → total (pre-STEP-6)
    elif isinstance(human_pairs, int) and not isinstance(human_pairs, bool):
        gate_pairs = human_pairs          # new JOIN → human-only gate
    else:
        note_disposition("gateway_dup_degraded", "indeterminate",
                         reason="fleet_human_duplicate_pairs present but garbage")
        return None                        # present but garbage → INERT/hold

    if gate_pairs < min_dup_pairs:
        _save_gateway_dup_streak(sp, 0)  # explicit observed-clean
        note_disposition("gateway_dup_degraded", "clean")
        return None

    streak = min(_load_gateway_dup_streak(sp) + 1, debounce_ticks)
    _save_gateway_dup_streak(sp, streak)
    if streak < debounce_ticks:
        note_disposition("gateway_dup_degraded", "indeterminate",
                         reason="dup candidate held by debounce")
        return None

    deliveries = payload.get("fleet_duplicate_deliveries")
    human_deliveries = payload.get("fleet_human_duplicate_deliveries")
    dups = payload.get("fleet_duplicates")
    sample = ""
    if isinstance(dups, list):
        parts = []
        for d in dups[:3]:
            if not isinstance(d, dict):
                continue
            cid = str(d.get("content_id", "?"))[:12]
            rcp = str(d.get("recipient", "?"))[:10]
            kind = str(d.get("recipient_kind", "?"))
            parts.append(
                f"{cid}..→{rcp}({kind})×{d.get('distinct_hosts', '?')}gw")
        sample = "; ".join(parts)

    if human_pairs is None:
        # Pre-split rollup — page on the total, say the split is unavailable.
        headline = (
            f"Cross-gateway duplicate delivery: {gate_pairs} (content_id, "
            f"recipient) pair(s) confirmed by >1 gateway (~{deliveries} extra "
            f"copy/ies); infra/human split unavailable (old rollup)."
        )
    else:
        # The page is the HUMAN count; the infra residual rides its OWN
        # clause, never averaged into the paged number (#74 lesson).
        infra_n = infra_pairs if isinstance(infra_pairs, int) else "?"
        headline = (
            f"HUMAN-facing cross-gateway duplicate delivery: {gate_pairs} "
            f"(content_id, recipient) pair(s) reached a real inbox from >1 "
            f"gateway (~{human_deliveries} extra copy/ies); {infra_n} benign "
            f"infra-to-infra dup(s) NOT paged."
        )
    return Signal(
        cls="gateway_dup_degraded",
        subject="fleet-gateways",
        severity="degraded",
        detail=(
            f"{headline} {sample}. See /fleet/dups; the human dup is the "
            f"reliability defect — infra-to-infra dups are structurally "
            f"residual without a coordination substrate (arc STEP 6)."
        ),
        issue_ref=None,
        extra={
            "fleet_duplicate_pairs": dup_pairs,
            "fleet_duplicate_deliveries": deliveries,
            "fleet_human_duplicate_pairs": human_pairs,
            "fleet_infra_duplicate_pairs": infra_pairs,
            "covered_hosts": payload.get("covered_hosts"),
            "debounce_streak": streak,
        },
    )
# ─────────────────────────────────────────────────────────────────────
# Back-compat re-exports. The delivery-flow observer probes (synth soak,
# gateway delivery degraded, resource canary, oracle delivery) were split
# into watchdog_probes_gateway_flow (2026-07-14, MF025 size cap), but every
# consumer imports them from HERE (the watchdog_probes hub, the test suite).
# Re-export the moved surface so `from utils.watchdog_probes_gateway import
# <name>` keeps working; the split is API-preserving.
# ─────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────
# Dual-homed recipients — the LEADING indicator behind the row-8 accept
#
# Row 8 (cross-gateway duplicate suppression) was ACCEPTED-PERMANENT
# 2026-07-19 on cost asymmetry: a duplicate is redundancy, a yield-protocol
# bug is silence, and emergency-comms infrastructure must fail toward
# redundancy. What that accept does NOT claim is that duplicates are rare
# forever — three human recipients were already dual-homed on the day it was
# accepted, so the precondition is live and the rate is traffic-dependent.
#
# So we stopped instrumenting only the OUTCOME (a duplicate happened: rare,
# bursty, and on the mesh leg unobservable) and added the CONDITION THAT
# PERMITS IT (a recipient reachable from >1 gateway). The condition moves
# first and is always countable, which turns "will time change this?" from a
# wait into a number.
#
# Fires on a NEWLY-observed dual-homed recipient, never on the count: the
# count churns as the rollup window rolls, whereas "a recipient we have never
# seen dual-homed before now is" is a real change in fleet exposure. Once
# known, a recipient stays known, so this cannot re-fire on churn.
# ─────────────────────────────────────────────────────────────────────

DEFAULT_DUAL_HOMED_STATE_PATH = "/var/lib/meshforge/gateway_dual_homed_state.json"


def _load_known_dual_homed(state_path: str) -> set:
    """Recipients already known to be dual-homed. Any error → empty set.

    An unreadable state file means we cannot tell new from known, so the next
    tick re-announces what it sees. That is noisy-but-honest; the alternative
    (treating unreadable as "everything known") would silently swallow the
    first real exposure growth after a disk hiccup.
    """
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            known = json.load(fh).get("known")
        return {str(h) for h in known} if isinstance(known, list) else set()
    except (OSError, ValueError, TypeError):
        return set()


def _save_known_dual_homed(state_path: str, known: set) -> None:
    """Persist the known set (atomic-rename, never raises)."""
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"known": sorted(known)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError:
        logger.warning("watchdog: dual-homed state write failed at %s", state_path)


def probe_gateway_dual_homed_exposure(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout_s: float = 3.0,
    state_path: Optional[str] = None,
    payload: Optional[dict] = None,
) -> Optional["Signal"]:
    """A recipient became reachable from MORE THAN ONE gateway.

    Not a fault — an EXPOSURE change. Dual-homing is the precondition for a
    cross-gateway duplicate, so this is the leading indicator for a residual
    the fleet has deliberately accepted rather than coordinated away.

    Self-guards None: rollup unreachable (the collector-cron DECLARATION
    decides — ``inert`` off the manager even when no map runs here at all,
    ``indeterminate`` on it; 2026-08-09), not-a-dict (indeterminate — HOLD),
    ``status == unavailable`` (no rollup
    here at all — a non-manager box, so ``inert``, not blind), ``status``
    otherwise != ok (<2 gateways: you cannot observe dual-homing with one
    vantage — indeterminate, carrying the JOIN's own reason), stale rollup, the field
    ABSENT (an un-upgraded JOIN is indeterminate, never a forged zero —
    honest_failure_modes #2/#4), and no NEW recipient since last tick.

    ⚠️ Derived from the CONFIRMED set, so it inherits that blind spot: a mesh
    recipient never confirms and so never appears. This measures exposure
    within the CONFIRMABLE population; extending it to attempted/routing state
    is gateway-side work and remains the residual.
    """
    sp = state_path or DEFAULT_DUAL_HOMED_STATE_PATH
    if payload is None:
        url = f"http://{host}:{port}/fleet/dups"
        try:
            with urlopen(url, timeout=timeout_s) as resp:
                payload = json.loads(resp.read())
        except (URLError, socket.timeout, json.JSONDecodeError, OSError,
                ValueError):
            _disp, _reason = _classify_dups_unreachable(
                suffix=" — dual-homing needs two vantages to observe")
            note_disposition("gateway_dual_homed_exposure", _disp,
                             reason=_reason)
            return None
    if not isinstance(payload, dict):
        note_disposition("gateway_dual_homed_exposure", "indeterminate",
                         reason="dups payload not a dict")
        return None
    if payload.get("status") != "ok":
        _note_dups_rollup_not_ok(
            "gateway_dual_homed_exposure", payload,
            suffix=" — dual-homing needs two vantages to observe")
        return None
    fresh = payload.get("freshness")
    if isinstance(fresh, dict) and fresh.get("stale") is True:
        note_disposition("gateway_dual_homed_exposure", "indeterminate",
                         reason="rollup stale (collector cron dead)")
        return None

    hashes = payload.get("dual_homed_recipient_hashes")
    if not isinstance(hashes, list):
        # Pre-2026-07-19 JOIN: the field does not exist. Absent is NOT zero.
        note_disposition("gateway_dual_homed_exposure", "indeterminate",
                         reason="rollup predates dual_homed_recipient_hashes "
                                "— absent is not zero")
        return None

    current = {str(h) for h in hashes if h}
    known = _load_known_dual_homed(sp)
    new = sorted(current - known)
    if not new:
        note_disposition(
            "gateway_dual_homed_exposure", "clean",
            reason=f"{len(current)} dual-homed recipient(s), none new")
        return None

    _save_known_dual_homed(sp, known | current)
    shown = ", ".join(h[:8] for h in new[:6])
    return Signal(
        cls="gateway_dual_homed_exposure",
        subject=new[0][:8] if len(new) == 1 else f"{len(new)} recipients",
        severity="degraded",
        detail=(
            f"{len(new)} recipient(s) newly reachable from >1 gateway "
            f"({shown}); {len(current)} dual-homed in total. This is the "
            f"PRECONDITION for a cross-gateway duplicate, not a duplicate: "
            f"cross-gateway suppression is deliberately NOT built (a dup is "
            f"redundancy, a yield-protocol bug is silence), so this tracks the "
            f"exposure that decision accepts. Rising count = more recipients "
            f"where a duplicate becomes possible; check whether the routing "
            f"change was intended."
        ),
        extra={"new": new, "dual_homed_total": len(current),
               "confirmable_population_only": True},
    )


from utils.watchdog_probes_gateway_lxmf import (  # noqa: E402,F401 (back-compat re-export)
    DEFAULT_LXMF_PROPAGATION_DEBOUNCE_PATH,
    _PROPAGATION_FRESH_S,
    _PROPAGATION_CACHE_FRESH_S,
    _PROPAGATION_FUTURE_SLOP_S,
    _operator_home,
    _read_configured_propagation_node,
    _read_fresh_propagation_nodes,
    DEFAULT_LXMF_PROPAGATION_DARK_DEBOUNCE_PATH,
    _PROPAGATION_ANNOUNCE_INTERVAL_S,
    _PROPAGATION_DARK_AFTER_S,
    _normalize_rns_hash,
    _read_propagation_liveness,
    probe_lxmf_propagation_node_dark,
)
from utils.watchdog_probes_propagation import (  # noqa: E402,F401 (back-compat re-export)
    _worst_propagation_round,
    probe_propagation_soak_degraded,
)
from utils.watchdog_probes_gateway_flow import (  # noqa: E402,F401 (back-compat re-export)
    DEFAULT_SYNTH_SOAK_DEBOUNCE_PATH,
    _SYNTH_SOAK_CADENCE_S,
    _SYNTH_SOAK_STALE_AFTER_S,
    _resolve_synth_soak_dir,
    _load_synth_streak,
    _save_synth_streak,
    _newest_synth_file,
    _worst_synth_pair,
    probe_synth_soak_degraded,
    DEFAULT_PROPAGATION_SOAK_DEBOUNCE_PATH,
    _PROPAGATION_SOAK_CADENCE_S,
    _PROPAGATION_SOAK_STALE_AFTER_S,
    _resolve_propagation_soak_dir,
    _resolve_operator_state_dir,
    GATEWAY_DELIVERY_BLOCK_GREP,
    _GATEWAY_DELIVERY_BLOCK_RE,
    GATEWAY_RNS_ERROR_GREP,
    GATEWAY_R2M_SUPPRESSED_GREP,
    DEFAULT_GATEWAY_DELIVERY_STATE_PATH,
    _parse_delivery_block,
    _gateway_delivery_blocks,
    _gateway_r2m_suppressed,
    _window_delivery_gap,
    _load_gateway_delivery_streak,
    _save_gateway_delivery_streak,
    probe_gateway_delivery_degraded,
    RESOURCE_CANARY_STATE_LEAF,
    DEFAULT_RESOURCE_CANARY_DEBOUNCE_PATH,
    _RESOURCE_CANARY_CADENCE_S,
    _RESOURCE_CANARY_STALE_AFTER_S,
    _resolve_resource_canary_dir,
    _load_resource_canary_streak,
    _save_resource_canary_streak,
    probe_resource_canary_degraded,
    _ORACLE_SAMPLE_N,
    _ORACLE_FRESH_S,
    _ORACLE_RATE_THRESHOLD,
    _ORACLE_LOG_READ_BYTES,
    _ORACLE_TS_FUTURE_SLOP_S,
    DEFAULT_ORACLE_DELIVERY_DEBOUNCE_PATH,
    _resolve_oracle_log_path,
    _load_oracle_streak,
    _save_oracle_streak,
    _classify_oracle_record,
    _read_oracle_recent,
    probe_oracle_delivery_degraded,
)
