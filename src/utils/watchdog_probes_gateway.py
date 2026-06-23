"""Watchdog probes — gateway delivery-path failure shapes.

Delivery write canary (#63), queue backlog (#74), delivery confirmation
stall (#74), synth-soak watch (2026-06-15), gateway-delivery-degraded
(2026-06-20, the gateway-reliability arc A2). Part of the
``watchdog_probes`` split (2026-06-09) — import via the
``utils.watchdog_probes`` hub, not from here.
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
        return None  # don't false-alarm on transport problems

    health = payload.get("health") if isinstance(payload, dict) else None
    if not isinstance(health, dict):
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
        return None
    if not isinstance(payload, dict) or "queue_depth" not in payload:
        return None

    try:
        queue_depth = int(payload.get("queue_depth") or 0)
        max_queue_size = int(payload.get("max_queue_size") or 0)
        dead_letter = int(payload.get("dead_letter") or 0)
    except (TypeError, ValueError):
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
        return None
    if not isinstance(payload, dict):
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
        return None

    recent = payload.get("recent")
    if not isinstance(recent, list):
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
        return None

    ring_rate = ring_confirmed / terminal
    if ring_rate > rate_degraded:
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
# Probe: synth soak degraded / silent (2026-06-15)
# ─────────────────────────────────────────────────────────────────────

DEFAULT_SYNTH_SOAK_DEBOUNCE_PATH = "/var/lib/meshforge/synth_soak_debounce.json"

# The synth soak fires hourly (meshforge-synth-soak.timer OnCalendar=*:07:00).
# Treat it as DARK only after ~2.5 cadences with no fresh result — two missed
# runs, so a single skipped/slow fire (RandomizedDelaySec, a long run, a
# Persistent=true catch-up after brief downtime) never false-alarms.
_SYNTH_SOAK_CADENCE_S = 3600.0
_SYNTH_SOAK_STALE_AFTER_S = 9000.0


def _resolve_synth_soak_dir() -> Optional[str]:
    """Resolve the operator's synth_soak state dir, root-context safe.

    The synth soak runs as the operator's systemd --user timer, so its output
    lives under the operator home — the watchdog (sandboxed root) derives that
    home from the operator UID and reads it directly, never escalating (the
    rns_version_drift / cron_verdict lesson). None when no operator user is
    resolvable (indeterminate — never a false alarm).
    """
    try:
        from utils.fleet_test_runner import _find_operator_user
        op = _find_operator_user()
    except Exception:
        op = None
    if not op:
        return None
    try:
        import pwd
        home = pwd.getpwuid(op[0]).pw_dir
    except (KeyError, OSError):
        return None
    return os.path.join(home, ".local", "state", "meshforge", "synth_soak")


def _load_synth_streak(state_path: str) -> int:
    """Read the consecutive-degraded streak. Any error → 0 (favour silence on
    uncertainty — a missing/garbage state suppresses a first-seen fire)."""
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_synth_streak(state_path: str, streak: int) -> None:
    """Persist the streak counter (atomic-rename, never raises)."""
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


def _newest_synth_file(state_dir: str) -> Optional[Tuple[str, float]]:
    """``(path, mtime)`` of the newest ``synth-*.json`` in ``state_dir``, else None.

    None distinguishes 'no synth output exists' (inert — box never produced a
    result) from a stale-but-present file (the silence signal). A listing error
    after the dir existed is a transient race → None (caller holds the streak).
    """
    try:
        entries = [
            e for e in os.listdir(state_dir)
            if e.startswith("synth-") and e.endswith(".json")
        ]
    except OSError:
        return None
    newest_path: Optional[str] = None
    newest_mtime = -1.0
    for name in entries:
        p = os.path.join(state_dir, name)
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if m > newest_mtime:
            newest_mtime = m
            newest_path = p
    if newest_path is None:
        return None
    return newest_path, newest_mtime


def _worst_synth_pair(pair_results) -> Optional[str]:
    """Compact ``<user>-><peer> ok/samples (N% fail)`` for the worst pair.

    None when pair_results is absent/empty/misshaped or nothing failed
    (never raises — a degraded summary must not itself crash the probe)."""
    if not isinstance(pair_results, list):
        return None
    worst = None
    worst_fail = -1.0
    for pr in pair_results:
        if not isinstance(pr, dict):
            continue
        try:
            fail = float(pr.get("fail_pct", 0) or 0)
        except (TypeError, ValueError):
            continue
        if fail > worst_fail:
            worst_fail = fail
            worst = pr
    if worst is None or worst_fail <= 0:
        return None
    return (
        f"{worst.get('user', '?')}->{worst.get('peer', '?')} "
        f"{worst.get('ok', '?')}/{worst.get('samples', '?')} ok "
        f"({worst_fail:.0f}% fail)"
    )


def probe_synth_soak_degraded(
    *,
    state_dir: Optional[str] = None,
    now: Optional[float] = None,
    stale_after_s: float = _SYNTH_SOAK_STALE_AFTER_S,
    debounce_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """The synth soak's delivery envelope failed, or the soak went DARK.

    The hourly LXMF multi-user synth soak (``meshforge-synth-soak.timer`` ->
    ``lab_synth_soak_fire.sh``) exercises the gateway's REAL round-trip delivery
    path and writes a pass/fail envelope per run — but the fire script always
    exits 0 and nothing consumed the result, so a delivery regression (envelope
    below its ok-ratio threshold) OR the timer going silent was invisible to the
    fleet. This closes that gap: the synth canary is now itself watched.

    Two legs, ``degraded`` only — a synth dip is a warning, the gateway may still
    be serving real traffic, and queue_backlog / delivery_confirmation_stall /
    delivery_write_canary own the hard-failure surface:

      - SILENCE: newest ``synth-*.json`` older than ``stale_after_s`` (~2.5x the
        hourly cadence) — the exerciser stopped (timer dead, fire script broken,
        box wedged). Here silence IS the failure mode (a fixed-cadence generator
        going quiet is unambiguous — the inverse of delivery_confirmation_stall).
      - ENVELOPE: newest result has ``pass_envelope`` false — round-trip delivery
        dropped below the run's ok-ratio threshold; the worst pair is surfaced.

    Honest-failure self-guards (favour silence on uncertainty):
      - state dir unresolvable / absent → None (INERT: this box doesn't run the
        synth soak — the common case; unobservable != degraded).
      - no ``synth-*.json`` present → None (never ran / freshly installed) — held,
        distinct from a stale present file which fires.
      - newest file unreadable/garbage → a degraded candidate, but RIDDEN OUT by
        the debounce: a torn mid-write file is whole again by the next 30s tick,
        so only a persistently-unreadable result fires.
      - ``pass_envelope`` absent on a parseable fresh file → indeterminate (held;
        neither fires nor resets) — a shape regression must not read as healthy.
      - a candidate must persist ``debounce_ticks`` consecutive ticks before
        firing; only an explicit healthy+fresh observation resets the streak;
        indeterminate observations HOLD it.
    """
    import time as _time
    now = _time.time() if now is None else now

    sdir = state_dir or _resolve_synth_soak_dir()
    if not sdir or not os.path.isdir(sdir):
        return None  # INERT: box doesn't run the synth soak

    sp = debounce_path or DEFAULT_SYNTH_SOAK_DEBOUNCE_PATH

    newest = _newest_synth_file(sdir)
    if newest is None:
        return None  # no result file / transient listing race — hold streak

    newest_path, newest_mtime = newest
    age = now - newest_mtime
    extra: dict = {"newest": os.path.basename(newest_path), "age_s": round(age, 1)}

    candidate_detail: Optional[str] = None
    definitively_healthy = False

    if age > stale_after_s:
        candidate_detail = (
            f"synth soak went DARK: newest result is {age / 3600.0:.1f}h old "
            f"(cadence ~1h) — the LXMF round-trip exerciser stopped producing "
            f"output. Check meshforge-synth-soak.timer (systemd --user) + its "
            f"fire log."
        )
    else:
        try:
            with open(newest_path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            doc = None
        if not isinstance(doc, dict):
            candidate_detail = (
                f"synth soak newest result unreadable "
                f"({os.path.basename(newest_path)}) — the run wrote no "
                f"parseable envelope."
            )
        elif doc.get("pass_envelope") is True:
            definitively_healthy = True
        elif doc.get("pass_envelope") is False:
            ok_ratio = doc.get("ok_ratio")
            threshold = doc.get("ok_ratio_threshold")
            total_ok = doc.get("total_ok")
            total = doc.get("total_samples")
            worst = _worst_synth_pair(doc.get("pair_results"))
            extra.update({
                "ok_ratio": ok_ratio, "ok_ratio_threshold": threshold,
                "total_ok": total_ok, "total_samples": total,
                "worst_pair": worst,
            })
            try:
                ratio_s = f"{float(ok_ratio):.2f}" if ok_ratio is not None else "?"
            except (TypeError, ValueError):
                ratio_s = "?"
            candidate_detail = (
                f"synth soak FAILED its delivery envelope: ok_ratio={ratio_s} "
                f"(threshold {threshold}); {total_ok}/{total} round-trips OK"
                + (f". Worst pair: {worst}" if worst else "")
                + ". Gateway/LXMF round-trip delivery is degrading — check RNS "
                "paths to the fan-out peers + /api/gateway/delivery drop_reasons."
            )
        # else: pass_envelope absent/None on a parseable file → indeterminate
        #       (held below — neither a fire candidate nor a healthy reset).

    if candidate_detail is not None:
        streak = _load_synth_streak(sp) + 1
        _save_synth_streak(sp, streak)
        if streak < debounce_ticks:
            return None
        return Signal(
            cls="synth_soak_degraded",
            subject="meshforge-gateway",
            severity="degraded",
            detail=candidate_detail,
            extra=extra,
        )

    if definitively_healthy:
        _save_synth_streak(sp, 0)  # explicit healthy → reset the streak
    return None


# ─────────────────────────────────────────────────────────────────────
# Probe: gateway delivery degraded (2026-06-20; the gateway-reliability
# arc A2 — OUTCOME-based monitoring, not shape-enumeration)
# ─────────────────────────────────────────────────────────────────────
#
# The spine was SHAPE-based (probes for KNOWN failure shapes) + LIVENESS-
# based. The 2026-06-20 wx-total-loss was a NEW shape (RNS Resource EROFS:
# the gateway, a shared-instance RNS client, tried to write an assembled
# multi-chunk Resource under /etc/reticulum/storage/resources/ but
# ProtectSystem=strict omitted that path → [Errno 30] EROFS → the reply was
# silently dropped while the gateway read "active / RNS: connected"). The
# error HAD a witness — the EROFS line was in moc's journal the whole time —
# but NO probe consumed it (honest_failure_modes #9 at the spine level).
# Enumerating shapes is a treadmill. A2's lever is to prove the gateway DOES
# ITS JOB from its OWN self-report, shape-agnostically:
#
#   Leg 1 (delivery gap): the att/del/drop counter block bridge_cli prints
#     to the journal every 30s. A WINDOWED delivered/attempted ratio (delta
#     across the window, NOT lifetime — recent-sensitive, the operator's
#     "things fall silent") below a conservative floor with real volume.
#   Leg 2 (RNS error-spike): a count of the gateway's own RNS resource/
#     forward error lines (EROFS / resource-assembly / forward-to-secondary)
#     — the witness class that today had no consumer. This is the leg that
#     directly catches the EROFS shape: those failures happen during RNS
#     Resource assembly, BEFORE the message reaches the att/del counters, so
#     leg 1 cannot see them — only the error channel can.
#
# Deliberately additive, not a duplicate: probe_delivery_confirmation_stall
# judges the CLEAN reason-split confirmation rate of CONFIRMABLE protocols
# (RNS only, recent-ring) via the API; probe_delivery_write_canary watches
# the SQLite write health; probe_queue_backlog watches depth/dead-letter.
# A2 watches the GROSS journal self-report (both directions, Meshtastic
# included) + the RNS error channel none of those see.

# Matches the att/del/drop line in BOTH bridge_cli formats — single-bridge
# ("  attempted/delivered/dropped — M->R: a/d/x  R->M: a/d/x") and
# multi-bridge ("      att/del/drop — M->R: a/d/x  R->M: a/d/x"). The
# slash triplet after "M->R:" is the discriminator: the "Messages bridged:
# N (M->R: a, R->M: b)" line uses a COMMA, so it never matches. ERE for
# journalctl -g.
GATEWAY_DELIVERY_BLOCK_GREP = r"M->R: [0-9]+/[0-9]+/[0-9]+"
_GATEWAY_DELIVERY_BLOCK_RE = re.compile(
    r"M->R:\s*(\d+)/(\d+)/(\d+)\s+R->M:\s*(\d+)/(\d+)/(\d+)")

# The RNS error-channel witnesses (ERE for journalctl -g). EROFS is the
# 2026-06-20 wx class; the other two are the adjacent resource/forward
# failure shapes. Deliberately concrete strings — NOT a bare "Resource"
# match, which would false-fire on benign "Resource" log lines.
GATEWAY_RNS_ERROR_GREP = (
    r"EROFS|Error while assembling received resource|"
    r"Failed to forward to secondary")

DEFAULT_GATEWAY_DELIVERY_STATE_PATH = (
    "/var/lib/meshforge/gateway_delivery_debounce.json")


def _parse_delivery_block(
    line: str,
) -> Optional[Tuple[float, int, int, int, int, int, int]]:
    """Parse one ``-o short-unix`` att/del/drop journal line.

    Returns ``(ts, m2r_att, m2r_del, m2r_drop, r2m_att, r2m_del, r2m_drop)``
    or None when the epoch or the six counters don't parse (a torn line, a
    format that doesn't match) — None is dropped by the caller, never read as
    a zeroed block.
    """
    ts = _short_unix_ts(line)
    if ts is None:
        return None
    m = _GATEWAY_DELIVERY_BLOCK_RE.search(line)
    if m is None:
        return None
    try:
        n = [int(x) for x in m.groups()]
    except (ValueError, TypeError):
        return None
    return (ts, n[0], n[1], n[2], n[3], n[4], n[5])


def _gateway_delivery_blocks(
    unit: str,
    lookback: str,
    journalctl_path: str = "journalctl",
) -> Optional[List[Tuple[float, int, int, int, int, int, int]]]:
    """All att/del/drop counter blocks for ``unit`` within ``lookback``.

    Returns the parsed block list (``[]`` = the gateway printed no att/del
    block in the window — idle / just-started, a genuine *observed* state),
    or **None** on journalctl unavailable / timeout / rc∉(0,1) — the honest
    *unobservable* answer. The caller must never read None as ``[]`` (empty ≠
    error — honest_failure_modes #1), or a journalctl wedge would mask the
    very delivery collapse this measures.
    """
    try:
        proc = subprocess.run(
            [
                journalctl_path, "-u", unit, "--since", f"-{lookback}",
                "-g", GATEWAY_DELIVERY_BLOCK_GREP, "-o", "short-unix",
                "-q", "--no-pager",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode not in (0, 1):
        return None
    out = proc.stdout
    if not out:
        return []
    blocks: List[Tuple[float, int, int, int, int, int, int]] = []
    for ln in out.splitlines():
        if not ln:
            continue
        parsed = _parse_delivery_block(ln)
        if parsed is not None:
            blocks.append(parsed)
    return blocks


def _window_delivery_gap(
    blocks: List[Tuple[float, int, int, int, int, int, int]],
    *,
    min_volume: int,
    ratio_floor: float,
) -> List[Tuple[str, int, int, float]]:
    """Per-direction windowed delivered/attempted gap.

    Returns ``[(label, d_att, d_del, ratio), ...]`` for each direction whose
    WINDOWED delivery (newest counter minus oldest in the window) fell below
    ``ratio_floor`` with at least ``min_volume`` attempts — the recent-drop
    lens, not the lifetime-cumulative one (which would mask a fresh collapse
    on a long-uptime box). A counter going BACKWARD across the window means
    the gateway restarted mid-window (counters are in-memory); the earliest
    baseline is then taken as zero so we measure since-the-restart rather than
    reading a bogus negative delta.

    Needs ≥2 blocks to form a delta; fewer → ``[]`` (can't judge — the caller
    treats that as *no finding*, not *healthy*, and the volume gate keeps a
    quiet box silent regardless).

    NOTE (calibrated): the journal exposes only the TOTAL dropped count, which
    on the Mesh→RNS direction folds in benign best-effort broadcast-to-no-peer
    misses alongside real failures (RNS→Mesh dropped is clean — failures only).
    That is why the floor is conservative (a true majority-failure collapse,
    far below any benign-broadcast steady state) and why the precise,
    reason-split moderate-gap detection is delivery_confirmation_stall's job,
    not this leg's. Leg 1 is the gross-collapse backstop.
    """
    if len(blocks) < 2:
        return []
    ordered = sorted(blocks, key=lambda b: b[0])
    earliest, latest = ordered[0], ordered[-1]
    findings: List[Tuple[str, int, int, float]] = []
    # tuple indices: ts=0; M->R att/del/drop = 1/2/3; R->M att/del/drop = 4/5/6
    for label, att_i, del_i in (("Mesh->RNS", 1, 2), ("RNS->Mesh", 4, 5)):
        att_l, del_l = latest[att_i], latest[del_i]
        att_e, del_e = earliest[att_i], earliest[del_i]
        if att_l < att_e:                 # counter reset → measure since reset
            base_att, base_del = 0, 0
        else:
            base_att, base_del = att_e, del_e
        d_att = att_l - base_att
        d_del = del_l - base_del
        if d_att < min_volume:
            continue
        ratio = max(0.0, min(1.0, d_del / d_att))
        if ratio < ratio_floor:
            findings.append((label, d_att, d_del, ratio))
    return findings


def _load_gateway_delivery_streak(state_path: str) -> int:
    """Consecutive-candidate streak; any error → 0 (favour silence)."""
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_gateway_delivery_streak(state_path: str, streak: int) -> None:
    """Persist the debounce streak (atomic-rename, never raises).

    A persistent write failure pins the streak below the debounce floor → the
    probe would silently never fire during a real collapse, so a swallowed
    OSError leaves a WITNESS in the watchdog journal (honest_failure_modes #9).
    """
    try:
        parent = os.path.dirname(state_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"streak": int(streak)}, fh, separators=(",", ":"))
        os.replace(tmp, state_path)
    except OSError as exc:
        logger.warning(
            "gateway_delivery_degraded: could not persist debounce streak to "
            "%s (%s) — the probe may not advance past its debounce floor; "
            "check %s is writable.",
            state_path, exc, os.path.dirname(state_path) or state_path,
        )


def probe_gateway_delivery_degraded(
    *,
    unit: str = "meshforge-gateway.service",
    lookback: str = "30min",
    journalctl_path: str = "journalctl",
    systemctl_path: str = "systemctl",
    main_pid: Optional[int] = None,
    blocks_fn=None,
    error_count_fn=None,
    min_volume: int = 20,
    ratio_floor: float = 0.50,
    error_degraded_n: int = 3,
    error_wedge_n: int = 10,
    state_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """Fire when the gateway's OWN self-report shows it is NOT delivering —
    the gateway-reliability arc A2 (2026-06-20). OUTCOME monitoring: prove the
    gateway does its job, don't enumerate how it fails.

    Two legs, max severity wins (see the module-level block comment for the
    arc rationale and the EROFS-class origin):

    * **Leg 1 — delivery gap** (degraded): the windowed delivered/attempted
      ratio from the att/del/drop block ``bridge_cli`` prints every 30s falls
      below ``ratio_floor`` (default 0.50) with ≥ ``min_volume`` attempts in
      the window — a recent, high-volume collapse. Conservative by design
      (the journal's total-dropped count folds in benign Mesh→RNS broadcast
      misses; the precise moderate-gap lens is delivery_confirmation_stall).
    * **Leg 2 — RNS error-spike** (degraded ≥ ``error_degraded_n``, wedge ≥
      ``error_wedge_n``): the count of the gateway's own RNS resource/forward
      error lines (EROFS / resource-assembly / forward-to-secondary) in the
      window. THIS is the leg that catches the 2026-06-20 EROFS shape, whose
      failures never reach the att/del counters (they fail during Resource
      assembly, before the message becomes bridgeable).

    Self-guards (honest_failure_modes):

    * gateway not running on this box (``_resolve_main_pid`` → None) → None
      (INERT — the "does this box run the gateway" gate; moc/moc3 only).
    * BOTH legs unobservable (journalctl wedged/absent for the block fetch AND
      the error count) → None, HOLDING the debounce streak — unobservable ≠
      healthy, and a journalctl hiccup must not erase a real in-progress
      signal (#1/#2). An OBSERVED-clean tick (≥1 leg read, nothing crossed a
      threshold) resets the streak; a candidate must persist ``debounce_ticks``
      consecutive ticks before firing, so a torn block / one slow window can't
      flap it. Never raises.

    Recovery: read the gateway journal for the failing destination /
    ``EROFS``; an EROFS spike is the #60 sandbox class — confirm
    ``meshforge-gateway.service`` ``ReadWritePaths`` includes
    ``/etc/reticulum/storage`` (the 2026-06-20 fix).
    """
    try:
        sp = state_path or DEFAULT_GATEWAY_DELIVERY_STATE_PATH

        gw_pid = main_pid if main_pid is not None else _resolve_main_pid(
            unit, systemctl_path=systemctl_path)
        if gw_pid is None:
            return None  # INERT: this box doesn't run the gateway

        if blocks_fn is None:
            def blocks_fn():
                return _gateway_delivery_blocks(
                    unit, lookback, journalctl_path=journalctl_path)
        if error_count_fn is None:
            def error_count_fn():
                return _journal_count_match(
                    unit, GATEWAY_RNS_ERROR_GREP, lookback,
                    journalctl_path=journalctl_path)

        blocks = blocks_fn()           # Optional[List]: None=unobservable
        err = error_count_fn()         # Optional[int]: None=unobservable

        if blocks is None and err is None:
            # Fully unobservable — hold the streak (do NOT reset to a healthy
            # 0, do NOT fire). honest_failure_modes #2.
            return None

        findings: List[Tuple[str, str]] = []  # (severity, fragment)
        extra: dict = {
            "lookback": lookback, "min_volume": min_volume,
            "ratio_floor": ratio_floor,
            "error_degraded_n": error_degraded_n,
            "error_wedge_n": error_wedge_n,
        }

        # Leg 1 — windowed delivery gap (needs ≥2 blocks; observed = blocks
        # is not None, i.e. journalctl worked).
        if blocks is not None:
            for label, d_att, d_del, ratio in _window_delivery_gap(
                blocks, min_volume=min_volume, ratio_floor=ratio_floor
            ):
                findings.append((
                    "degraded",
                    f"{label} delivered {d_del}/{d_att} ({ratio:.0%}) over the "
                    f"last {lookback}",
                ))
                extra[f"gap_{label.replace('->', '_')}"] = {
                    "attempted": d_att, "delivered": d_del,
                    "ratio": round(ratio, 3),
                }

        # Leg 2 — RNS error-channel spike (the EROFS catcher).
        if err is not None:
            extra["rns_error_count"] = err
            if err >= error_degraded_n:
                sev = "wedge" if err >= error_wedge_n else "degraded"
                findings.append((
                    sev,
                    f"{err} RNS resource/forward errors (EROFS / "
                    f"resource-assembly / forward-to-secondary) in the last "
                    f"{lookback}",
                ))

        if not findings:
            # Observed at least one leg and nothing crossed a threshold →
            # reset the debounce streak (explicit healthy observation).
            _save_gateway_delivery_streak(sp, 0)
            return None

        streak = min(_load_gateway_delivery_streak(sp) + 1, debounce_ticks)
        _save_gateway_delivery_streak(sp, streak)
        if streak < debounce_ticks:
            return None

        severity = "wedge" if any(s == "wedge" for s, _ in findings) else "degraded"
        extra["debounce_streak"] = streak
        return Signal(
            cls="gateway_delivery_degraded",
            subject="meshforge-gateway",
            severity=severity,
            detail=(
                "Gateway self-report shows degraded delivery: "
                + "; ".join(frag for _, frag in findings)
                + ". OUTCOME monitor (gateway-reliability arc A2) — the gateway "
                "may read 'active / RNS: connected' while replies silently "
                "drop. Check the gateway journal for the failing destination + "
                "any EROFS lines; an EROFS spike is the #60 sandbox class — "
                "confirm meshforge-gateway.service ReadWritePaths includes "
                "/etc/reticulum/storage (the 2026-06-20 fix)."
            ),
            extra=extra,
        )
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# Probe: resource canary degraded / silent (2026-06-20; the gateway-
# reliability arc A1 — the OUTCOME source of truth)
# ─────────────────────────────────────────────────────────────────────
#
# A2 (above) consumes the gateway's OWN self-report; A1 is the active
# exerciser that PROVES the gateway delivers a RESOURCE-sized round-trip —
# the multi-chunk RNS Resource path the 2026-06-20 wx-total-loss EROFS broke
# while single-packet replies kept working (so every shape/liveness probe and
# the single-packet gateway_rt_canary read green). src/lab/gateway_resource_canary
# fires a control PING + a PINGBIG whose reply is resource-sized, on a timer,
# and writes a verdict envelope (last.json); this probe consumes it. The
# canary's own FAIL verdict — "control back, resource NOT" — is the EROFS
# signature; the probe simply surfaces it (and the canary going DARK) into
# mini/+/fleet, the same "the canary itself must be watched" pattern as
# synth_soak_degraded. degraded only: a resource-canary dip is a warning, and
# gateway_delivery_degraded / delivery_confirmation_stall own the gateway's
# self-reported hard-failure surface.

# The verdict-envelope dir leaf, kept byte-identical to the canary's
# STATE_DIR_LEAF and the wrapper's STATE_DIR default (TestStateDirContract pins
# the trio — honest_failure_modes #5: two consumers of one path WILL drift).
RESOURCE_CANARY_STATE_LEAF = "gateway_resource_canary"
DEFAULT_RESOURCE_CANARY_DEBOUNCE_PATH = (
    "/var/lib/meshforge/resource_canary_debounce.json")

# The canary fires hourly (meshforge-gateway-resource-canary.timer
# OnCalendar=*:43:00). DARK only after ~2.5 cadences with no fresh envelope —
# two missed fires, so one slow/skipped run never false-alarms.
_RESOURCE_CANARY_CADENCE_S = 3600.0
_RESOURCE_CANARY_STALE_AFTER_S = 9000.0


def _resolve_resource_canary_dir() -> Optional[str]:
    """The operator's resource-canary state dir, root-context safe.

    The canary runs as the operator's systemd --user timer, so its envelope
    lives under the operator home — the sandboxed-root watchdog derives that
    home from the operator UID and reads it directly, never escalating (the
    same pattern as _resolve_synth_soak_dir). None when no operator user is
    resolvable (indeterminate — never a false alarm)."""
    try:
        from utils.fleet_test_runner import _find_operator_user
        op = _find_operator_user()
    except Exception:
        op = None
    if not op:
        return None
    try:
        import pwd
        home = pwd.getpwuid(op[0]).pw_dir
    except (KeyError, OSError):
        return None
    return os.path.join(home, ".local", "state", "meshforge",
                        RESOURCE_CANARY_STATE_LEAF)


def _load_resource_canary_streak(state_path: str) -> int:
    """Read the consecutive-candidate streak. Any error → 0 (favour silence)."""
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_resource_canary_streak(state_path: str, streak: int) -> None:
    """Persist the streak counter (atomic-rename, never raises)."""
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


def probe_resource_canary_degraded(
    *,
    state_dir: Optional[str] = None,
    now: Optional[float] = None,
    stale_after_s: float = _RESOURCE_CANARY_STALE_AFTER_S,
    debounce_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """The gateway resource round-trip canary FAILED, or went DARK.

    The hourly resource canary (meshforge-gateway-resource-canary.timer →
    gateway_resource_canary.py) drives a control PING + a PINGBIG whose reply is
    RESOURCE-sized through the real gateway path and writes ``last.json``. This
    probe consumes it — closing the same "the canary itself is unwatched" gap
    synth_soak_degraded closed for the LXMF soak.

    Two legs, ``degraded`` only:

      - SILENCE: ``last.json`` older than ``stale_after_s`` (~2.5x the hourly
        cadence) — the exerciser stopped (timer dead, fire script broken, box
        wedged). Silence IS the failure mode for a fixed-cadence canary.
      - VERDICT: the envelope's ``verdict`` is FAIL or CONCERN — the canary's
        own honest classification. A FAIL whose reason carries "EROFS
        signature" (control back, resource NOT) is the 2026-06-20 class.

    Honest-failure self-guards (favour silence on uncertainty):
      - state dir unresolvable / absent → None (INERT: this box doesn't run the
        canary — the common case; unobservable != degraded).
      - no ``last.json`` present → None (never ran / freshly installed) — held,
        distinct from a stale present file which fires.
      - file unreadable/garbage → a degraded candidate RIDDEN OUT by the
        debounce (a torn mid-write is whole by the next tick — though the canary
        writes atomically, so this should never happen).
      - ``verdict`` OK on a fresh file → definitively healthy (resets streak).
      - ``verdict`` absent/unknown on a parseable fresh file → indeterminate
        (held; neither fires nor resets — a shape regression must not read as
        healthy).
      - a candidate must persist ``debounce_ticks`` consecutive ticks before
        firing; only an explicit healthy+fresh observation resets the streak.
    """
    import time as _time
    now = _time.time() if now is None else now

    sdir = state_dir or _resolve_resource_canary_dir()
    if not sdir or not os.path.isdir(sdir):
        return None  # INERT: box doesn't run the resource canary

    envelope = os.path.join(sdir, "last.json")
    try:
        mtime = os.path.getmtime(envelope)
    except OSError:
        return None  # no last.json yet (never fired) / transient race — hold

    sp = debounce_path or DEFAULT_RESOURCE_CANARY_DEBOUNCE_PATH
    age = now - mtime
    extra: dict = {"age_s": round(age, 1)}

    candidate_detail: Optional[str] = None
    definitively_healthy = False

    if age > stale_after_s:
        candidate_detail = (
            f"resource canary went DARK: last.json is {age / 3600.0:.1f}h old "
            f"(cadence ~1h) — the gateway resource round-trip exerciser stopped "
            f"producing a verdict. Check "
            f"meshforge-gateway-resource-canary.timer (systemd --user) + its "
            f"fire log."
        )
    else:
        try:
            with open(envelope, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            doc = None
        if not isinstance(doc, dict):
            candidate_detail = (
                "resource canary newest envelope unreadable (last.json) — "
                "the fire wrote no parseable verdict."
            )
        else:
            verdict = doc.get("verdict")
            extra.update({
                "verdict": verdict,
                "seq": doc.get("seq"),
                "reply_bytes": doc.get("reply_bytes"),
                "control_back": doc.get("control_back"),
                "resource_back": doc.get("resource_back"),
                "peer": doc.get("peer"),
            })
            if verdict == "OK":
                definitively_healthy = True
            elif verdict in ("FAIL", "CONCERN"):
                reason = doc.get("reason") or "(no reason recorded)"
                candidate_detail = (
                    f"resource round-trip canary {verdict}: {reason}"
                )
            # else: verdict absent/unknown on a parseable file → indeterminate
            #       (held below — neither a fire candidate nor a healthy reset).

    if candidate_detail is not None:
        streak = min(_load_resource_canary_streak(sp) + 1, debounce_ticks)
        _save_resource_canary_streak(sp, streak)
        if streak < debounce_ticks:
            return None
        extra["debounce_streak"] = streak
        return Signal(
            cls="resource_canary_degraded",
            subject="meshforge-gateway",
            severity="degraded",
            detail=candidate_detail,
            extra=extra,
        )

    if definitively_healthy:
        _save_resource_canary_streak(sp, 0)  # explicit healthy → reset streak
    return None


# ─────────────────────────────────────────────────────────────────────
# Probe: oracle delivery degraded (2026-06-22; the mesh-oracle health
# leg). The read-only "ask dude-AI over the mesh" responder (src/oracle)
# answers a NOC-state query over the mesh and appends one JSONL audit
# record per handled query to ~/.local/share/meshforge/mesh_oracle_log.jsonl
# (oracle.oracle_log_path; rotates at 2 MB). It had NO automated probe — a
# blind spot for a service whose whole ethos is "silence is the failure
# mode." v1 watches the DELIVERY-RATE leg only — the unambiguous one — over
# a recent ts window:
#
#     rate = delivered_true / (delivered_true + real_failures)
#
# real_failures counts ONLY records whose `reason` marks a real send
# EXCEPTION (reason startswith "send_error"). Three buckets are
# DELIBERATELY EXCLUDED from the rate — each would false-alarm:
#   - declines (reason in {cooldown, not_allowlisted}): the oracle CORRECTLY
#     refused (rate-limit / not on the allowlist), not a failure — THE trap
#     (honest_failure_modes #1).
#   - benign non-delivery (delivered=false, NO send_error reason): the RNS
#     leg's no-path-to-an-unannounced-ephemeral-identity (a 32-hex hash with
#     no announce) + the MeshCore first-send-post-restart race. Expected,
#     not defects. Counted + surfaced in `extra`, never hidden (#9).
#
# WITNESSED v1 blind spot: the RNS leg's send_fn (bridge_send_mixin.
# send_to_rns) catches real send EXCEPTIONS internally and returns a bare
# False, so an RNS *send error* lands in the benign bucket, not send_error —
# v1 cannot yet tell an RNS no-path from an RNS crash. Closing that needs
# send_to_rns to distinguish the two (a change to the LIVE RNS send path,
# deliberately deferred out of the mf.5 RNS-fork soak). The Meshtastic /
# MQTT / MeshCore legs DO surface send_error (their send_fn lets the
# exception reach the responder), so v1 already covers 3 of the 4 legs; the
# benign-bucket count makes the RNS gap visible rather than silent.
#
# degraded ONLY (a low oracle rate is a warning — the oracle is read-only,
# low-traffic, operator-test-only today; the hard delivery surface is owned
# by gateway_delivery_degraded / synth_soak / resource_canary). Fires when
# rate < threshold AND a MINIMUM confirmable sample exists (else pass@small-N
# noise; #6/#2). Silence (no queries) is NOT a failure for a reactive service
# — nobody asked — so there is no silence leg in v1 (the min-sample guard
# absorbs a quiet window; a v2 silence leg ties to channel_feed_dark, not a
# naive "no log for Xh" that false-alarms every quiet night). INERT (None)
# off a box where the oracle never wrote a log (disabled / never queried).
# Reads the operator home directly (root-context safe); 2-tick debounce.
# Mirrors synth_soak_degraded.

_ORACLE_LOG_WINDOW_S = 6 * 3600.0           # rate over the last ~6h of ts
_ORACLE_MIN_SAMPLE = 8                       # ≥8 confirmable queries or hold (small-N)
_ORACLE_RATE_THRESHOLD = 0.8                 # fire when the confirmable rate < 0.8
_ORACLE_LOG_READ_BYTES = 4 * 1024 * 1024     # bounded tail read (log rotates at 2 MB)
_ORACLE_TS_FUTURE_SLOP_S = 300.0             # tolerate small forward clock skew on ts
DEFAULT_ORACLE_DELIVERY_DEBOUNCE_PATH = (
    "/var/lib/meshforge/oracle_delivery_debounce.json")


def _resolve_oracle_log_path() -> Optional[str]:
    """Operator-home path to the oracle audit log, root-context safe.

    The oracle runs inside meshforge-gateway as the operator and appends to
    ``~/.local/share/meshforge/mesh_oracle_log.jsonl`` (== ``oracle.oracle_log_path``
    = ``MeshForgePaths.get_data_dir()``). The watchdog (sandboxed root) derives the
    operator home from the operator UID and reads it directly — never escalating
    (the synth_soak / rns_version_drift lesson). None when no operator is
    resolvable (indeterminate — never a false alarm).
    """
    try:
        from utils.fleet_test_runner import _find_operator_user
        op = _find_operator_user()
    except Exception:
        op = None
    if not op:
        return None
    try:
        import pwd
        home = pwd.getpwuid(op[0]).pw_dir
    except (KeyError, OSError):
        return None
    return os.path.join(home, ".local", "share", "meshforge", "mesh_oracle_log.jsonl")


def _load_oracle_streak(state_path: str) -> int:
    """Read the consecutive-degraded streak. Any error → 0 (favour silence on
    uncertainty — a missing/garbage state suppresses a first-seen fire)."""
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            streak = int(json.load(fh).get("streak", 0))
        return streak if streak >= 0 else 0
    except (OSError, ValueError, TypeError):
        return 0


def _save_oracle_streak(state_path: str, streak: int) -> None:
    """Persist the streak counter (atomic-rename, never raises)."""
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


def _classify_oracle_record(rec: dict) -> Optional[str]:
    """Bucket one audit record, or None if misshaped (skipped, never counted).

    Order matters — a delivered record never carries a reason, and a decline is
    a non-delivery WITH a known reason, so check the specific cases first:
      - ``delivered is True``                       → 'delivered'  (success)
      - ``reason`` startswith 'send_error'          → 'send_error' (real failure)
      - ``reason`` in {cooldown, not_allowlisted}   → 'decline'    (excluded)
      - ``delivered is False`` (any other/absent reason) → 'benign' (excluded, counted)
    """
    if not isinstance(rec, dict):
        return None
    delivered = rec.get("delivered")
    reason = rec.get("reason")
    if delivered is True:
        return "delivered"
    if isinstance(reason, str) and reason.startswith("send_error"):
        return "send_error"
    if reason in ("cooldown", "not_allowlisted"):
        return "decline"
    if delivered is False:
        return "benign"
    return None  # not a record we recognise (neither delivered nor a non-delivery)


def _read_oracle_window(
    log_path: str, now: float, window_s: float, read_bytes: int,
) -> Optional[Tuple[dict, int]]:
    """Parse the audit log's recent tail into per-bucket counts over the ts
    window. Returns ``(counts, total_in_window)`` or None when the file is
    unreadable (caller HOLDS the streak — unobservable ≠ healthy).

    The log rotates at 2 MB; read at most ``read_bytes`` from the END so a busy
    log stays bounded and the window is by ``ts``, never "all history". ``ts`` is
    wall-clock (RTC-less Pis, NTP steps) — a non-numeric / negative / far-future
    ts is skipped (clamp the forgeable clock).
    """
    try:
        size = os.path.getsize(log_path)
        with open(log_path, "rb") as fh:
            if size > read_bytes:
                fh.seek(size - read_bytes)
                fh.readline()  # discard the partial line after the byte-seek
            raw = fh.read()
    except OSError:
        return None
    counts = {"delivered": 0, "send_error": 0, "decline": 0, "benign": 0}
    total = 0
    lo = now - window_s
    hi = now + _ORACLE_TS_FUTURE_SLOP_S
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            rec = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        try:
            ts = float(rec.get("ts"))
        except (TypeError, ValueError):
            continue
        if ts < lo or ts > hi:
            continue
        bucket = _classify_oracle_record(rec)
        if bucket is None:
            continue
        counts[bucket] += 1
        total += 1
    return counts, total


def probe_oracle_delivery_degraded(
    *,
    log_path: Optional[str] = None,
    now: Optional[float] = None,
    window_s: float = _ORACLE_LOG_WINDOW_S,
    min_sample: int = _ORACLE_MIN_SAMPLE,
    threshold: float = _ORACLE_RATE_THRESHOLD,
    debounce_path: Optional[str] = None,
    debounce_ticks: int = 2,
) -> Optional[Signal]:
    """The mesh oracle's confirmable delivery rate fell below threshold.

    Over the last ``window_s`` of audit-log ``ts``, compute the #74 confirmation
    view: ``rate = delivered / (delivered + send_errors)``, where declines
    (cooldown / not_allowlisted) and benign non-deliveries (reason-less
    delivered:false — RNS no-path / MeshCore restart race) are EXCLUDED from the
    failure set. Fire ``degraded`` when ``rate < threshold`` and at least
    ``min_sample`` confirmable queries exist, after a ``debounce_ticks`` streak.

    Honest self-guards (favour silence on uncertainty):
      - operator unresolvable / log file absent → None (INERT: the oracle is
        disabled or never answered on this box — unobservable ≠ unhealthy).
      - log tail unreadable (transient/torn) → None, HOLDING the streak (neither
        fires nor resets).
      - confirmable sample < ``min_sample`` (incl. a quiet window — nobody asked)
        → None: a rate over a handful of queries is pass@small-N noise.
      - rate ≥ threshold on a real sample → explicit healthy → reset the streak.
      - a degraded candidate must persist ``debounce_ticks`` consecutive ticks.
    """
    import time as _time
    now = _time.time() if now is None else now

    lp = log_path or _resolve_oracle_log_path()
    if not lp:
        return None  # operator unresolvable — indeterminate, never a false alarm
    if not os.path.exists(lp):
        return None  # INERT: the oracle never wrote a log here (disabled/never queried)

    sp = debounce_path or DEFAULT_ORACLE_DELIVERY_DEBOUNCE_PATH

    parsed = _read_oracle_window(lp, now, window_s, _ORACLE_LOG_READ_BYTES)
    if parsed is None:
        return None  # unreadable tail — HOLD the streak (don't reset, don't fire)
    counts, _total = parsed

    delivered = counts["delivered"]
    real_failures = counts["send_error"]
    confirmable = delivered + real_failures
    if confirmable < min_sample:
        return None  # small-N (incl. a quiet window) — can't judge a rate honestly

    rate = delivered / confirmable  # confirmable >= min_sample >= 1, safe
    extra = {
        "window_h": round(window_s / 3600.0, 1),
        "confirmable": confirmable,
        "delivered": delivered,
        "send_errors": real_failures,
        "declines_excluded": counts["decline"],
        "benign_nondeliveries_excluded": counts["benign"],
        "rate": round(rate, 3),
        "threshold": threshold,
    }

    if rate >= threshold:
        _save_oracle_streak(sp, 0)  # explicit healthy observation → reset
        return None

    streak = min(_load_oracle_streak(sp) + 1, debounce_ticks)
    _save_oracle_streak(sp, streak)
    if streak < debounce_ticks:
        return None
    extra["debounce_streak"] = streak
    detail = (
        f"oracle delivery degraded: {rate:.2f} rate (threshold {threshold:.2f}) "
        f"over ~{extra['window_h']}h — {delivered} delivered / {real_failures} "
        f"send-error of {confirmable} confirmable queries "
        f"({counts['decline']} declines + {counts['benign']} benign "
        f"non-deliveries excluded). The oracle answered but its replies are not "
        f"landing — check the gateway's RNS/Meshtastic send path + the oracle "
        f"audit log (~/.local/share/meshforge/mesh_oracle_log.jsonl)."
    )
    return Signal(
        cls="oracle_delivery_degraded",
        subject="mesh-oracle",
        severity="degraded",
        detail=detail,
        extra=extra,
    )

