"""Watchdog probes — gateway delivery-path failure shapes.

Delivery write canary (#63), queue backlog (#74), delivery confirmation
stall (#74). Part of the ``watchdog_probes`` split (2026-06-09) — import
via the ``utils.watchdog_probes`` hub, not from here.
"""
from __future__ import annotations

import json
import os
import socket
from typing import List, Optional, Tuple
from urllib.request import urlopen
from urllib.error import URLError

from utils.watchdog_probe_core import Signal

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


