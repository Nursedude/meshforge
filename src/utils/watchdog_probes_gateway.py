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
    _resolve_main_pid,
    _short_unix_ts,
    note_disposition,
)

# Same logger name the runner uses (watchdog_runner.py) so a swallowed
# state-write failure lands in the one "watchdog" namespace the operator
# already greps — honest_failure_modes #9 ("every swallow gets a witness").
logger = logging.getLogger("watchdog")

# ─────────────────────────────────────────────────────────────────────
# Probe: delivery counters write canary (Issue #63)
# ─────────────────────────────────────────────────────────────────────


def probe_delivery_write_canary(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout_s: float = 3.0,
    error_threshold: int = 3,
) -> Optional[Signal]:
    """Read ``/api/gateway/delivery.health`` and surface preflight/runtime errors.

    Reuses the gateway's existing self-reported health block (Issue #63).
    The watchdog itself doesn't re-probe SQLite — that's the gateway's
    job; we just amplify the signal so an operator skimming /fleet sees
    "delivery counters can't write" the same way they see "RNS wedged".

    Severities:
      - wedge: preflight_ok=False (every write attempt is failing)
      - degraded: consecutive_write_errors >= error_threshold
      - None: healthy, or endpoint unreachable (a different probe surfaces that)
    """
    url = f"http://{host}:{port}/api/gateway/delivery"
    try:
        with urlopen(url, timeout=timeout_s) as resp:
            payload = json.loads(resp.read())
    except (URLError, socket.timeout, json.JSONDecodeError, OSError, ValueError):
        note_disposition("delivery_write_canary", "indeterminate",
                         reason="delivery API unreachable/unparseable")
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
    SERVICE). Transport/shape errors → None (http_local /
    service_inactive own those).
    """
    url = f"http://{host}:{port}/api/gateway/queue"
    try:
        with urlopen(url, timeout=timeout_s) as resp:
            payload = json.loads(resp.read())
    except (URLError, socket.timeout, json.JSONDecodeError, OSError, ValueError):
        note_disposition("queue_backlog", "indeterminate",
                         reason="queue API unreachable/unparseable")
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


def probe_delivery_confirmation_stall(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    timeout_s: float = 3.0,
    min_terminal: int = 20,
    rate_degraded: float = 0.50,
    rate_wedge: float = 0.10,
) -> Optional[Signal]:
    """A confirmable protocol's deliveries are failing instead of confirming
    (Issue #74).

    Closes the honest-signal gap where the bridge's own status reads HEALTHY
    while nothing confirms. Judges a WINDOWED rate from the snapshot's
    recent-events ring (last 50, newest-last) — the lifetime-cumulative rate
    would mask a recent collapse.

    CRUCIAL: judges ONLY protocols that actually have a confirmation mechanism
    (record `confirmed` events — RNS today; Meshtastic too once ROUTING_APP
    ACK consumption lands), and compares that protocol's two REAL terminal
    outcomes — `confirmed` vs a failed-delivery `dropped` — NOT the meaningless
    cross-population `confirmed/sent` ratio. The counters use disjoint lifecycle
    states per protocol (RNS: queued→confirmed, never `sent`; Meshtastic:
    queued→sent, never `confirmed`), so `confirmed/sent` was (RNS-confirmed ÷
    Meshtastic-sent) — two different message populations that never measured a
    coherent rate and false-alarmed ~50% on every mesh-heavy gateway.

    Self-guards (silence is NOT failure here — the explicit inversion of
    channel_feed_dark):
      - transport/shape errors → None (other probes own those)
      - no confirmable protocol → None (nothing tracks confirmation; e.g. an
        RNS-less box — Meshtastic has no ACK)
      - confirmable terminal events < min_terminal → None (low-traffic /
        small-sample box: one failure tanks a tiny denominator; on a
        mesh-heavy box the 50-event ring holds few RNS events, so None is the
        correct, honest answer over too small a sample)

    Severities: rate ≤ 10% → wedge, ≤ 50% → degraded.
    """
    url = f"http://{host}:{port}/api/gateway/delivery"
    try:
        with urlopen(url, timeout=timeout_s) as resp:
            payload = json.loads(resp.read())
    except (URLError, socket.timeout, json.JSONDecodeError, OSError, ValueError):
        note_disposition("delivery_confirmation_stall", "indeterminate",
                         reason="delivery API unreachable/unparseable")
        return None
    if not isinstance(payload, dict):
        note_disposition("delivery_confirmation_stall", "indeterminate",
                         reason="delivery payload not a dict")
        return None

    # Confirmable = protocols that have ever recorded a `confirmed` event.
    # Meshtastic isn't here until ACK consumption exists, so its
    # structurally-unconfirmable sends never drag the rate.
    confirmed_by_proto = (payload.get("state_by_protocol") or {}).get("confirmed") or {}
    confirmable = {
        p for p, c in confirmed_by_proto.items()
        if isinstance(c, (int, float)) and not isinstance(c, bool) and c > 0
    }
    if not confirmable:
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
      - endpoint unreachable / non-dict / shape error → None (other probes own
        transport; the streak is HELD, not reset — unobservable ≠ healthy)
      - ``status != "ok"`` (indeterminate: <2 contributing gateways reachable)
        → None, HOLD streak — you CANNOT observe a cross-gateway dup when you
        can't see ≥2 gateways, so this must NEVER read as a healthy "0 dups"
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
        # means this cannot read as benign inert.
        note_disposition("gateway_dup_degraded", "indeterminate",
                         reason="dups rollup unreachable — manager-map down "
                                "or not the manager box")
        return None  # transport — HOLD streak (unobservable ≠ clean)
    if not isinstance(payload, dict):
        note_disposition("gateway_dup_degraded", "indeterminate",
                         reason="dups payload not a dict")
        return None

    # indeterminate / unavailable: <2 gateways covered, or no rollup yet.
    # Cannot observe a cross-gateway dup → HOLD streak, stay INERT. A wired
    # probe treating indeterminate as green would be the exact #2 trap.
    if payload.get("status") != "ok":
        note_disposition("gateway_dup_degraded", "indeterminate",
                         reason="rollup indeterminate (<2 gateways reachable)")
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
    GATEWAY_DELIVERY_BLOCK_GREP,
    _GATEWAY_DELIVERY_BLOCK_RE,
    GATEWAY_RNS_ERROR_GREP,
    DEFAULT_GATEWAY_DELIVERY_STATE_PATH,
    _parse_delivery_block,
    _gateway_delivery_blocks,
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
    _ORACLE_LOG_WINDOW_S,
    _ORACLE_MIN_SAMPLE,
    _ORACLE_RATE_THRESHOLD,
    _ORACLE_LOG_READ_BYTES,
    _ORACLE_TS_FUTURE_SLOP_S,
    DEFAULT_ORACLE_DELIVERY_DEBOUNCE_PATH,
    _resolve_oracle_log_path,
    _load_oracle_streak,
    _save_oracle_streak,
    _classify_oracle_record,
    _read_oracle_window,
    probe_oracle_delivery_degraded,
)
