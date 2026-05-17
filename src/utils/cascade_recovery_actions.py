"""Cascade recovery actor — subscribes to ``utils.wedge_events`` and
turns each ``WedgeEvent`` into a durable audit row plus (optionally) a
real recovery side-effect such as ``systemctl restart rnsd``.

Fork B of the federation→DB pressure→wedge stability arc. The substrate
this module sits on:

* Fork A (``gateway.bounded_rpc``) — gateway hot-path RNS RPC calls run
  under a hard timeout. On wedge: bumps a counter, publishes a
  ``WedgeEvent``, then ``os._exit(2)``s so systemd restarts the
  gateway process. The wedge_events ring is the forensic trail; this
  module turns that trail into a *closed loop* (gateway restart catches
  the gateway thread, but the underlying rnsd RPC listener stays wedged
  until *something* — operator or this actor — restarts rnsd itself).
* Track 0C cascade detector — independent pre-failure fingerprinting
  on a 30 s cadence. Once it flips ``rns_rpc_wedge`` to ``pre_fail``
  there's hysteresis evidence the wedge is real. Recovery uses the
  cascade detector's snapshot as the *gate* on action, not the
  publish-event itself (a single watchdog fire isn't enough signal to
  justify restarting an upstream daemon).

Design decisions worth knowing before changing this:

* **Log-only is the default action**, controlled by the
  ``cascade_recovery_action`` field in ``map_settings.json``. Per-box
  opt-in to ``systemctl_restart_rnsd`` — restarting rnsd ejects every
  client (gateway, tracer, NomadNet, this map service itself). That's
  the right call when the alternative is sustained 100% timeout, but
  *not* something to default-on across the fleet without operator
  consent.
* **Subscriber callbacks fire on the publisher's thread**
  (``wedge_events`` contract). The audit-log append happens inline
  (fast — one open/write/close, bounded by FS latency). Any side-
  effect that could block — most notably the ``sudo systemctl
  restart rnsd`` subprocess — is dispatched on a daemon thread so
  the publisher returns promptly.
* **Cooldown is per-action, not per-event**. The wedge could fire
  repeatedly during a sustained outage; we restart rnsd at most once
  per cooldown window (default 300 s). After cooldown the actor will
  attempt again if the cascade detector still shows ``pre_fail``.
* **Audit log is append-only, JSON-per-line**. Lives under
  ``$XDG_STATE_HOME/meshforge/cascade_recovery.log`` to match the
  tracer state-dir convention (``cascade_fingerprints._tracer_state_dir``).
  Bounded by manual rotation — this is a low-volume log; a wedge a
  day plus log-only entries should fit comfortably in tens of KB per
  year. Operators rotate alongside their other meshforge logs.
* **One singleton per process**, started by ``map_data_service`` next
  to the cascade detector. ``get_singleton().snapshot()`` is what the
  HTTP handler folds into ``/fleet/cascade`` for cross-view visibility.

Open follow-ups (Fork B continued):

* The cascade-detector gate is currently a soft check inside the
  action handler — if the detector singleton hasn't been started yet
  (early-boot wedge before the detector's first tick) the actor falls
  back to "publish-event-only is sufficient signal". The narrow window
  is acceptable; if it grows it's worth a dedicated bootstrap path.
* Future ``recovery_action`` values for other fingerprints (e.g.
  ``wal_oversize`` → ``checkpoint_wal``) are designed as discrete
  actions handlers indexed by name, mirroring the
  ``diagnostic_engine._recovery_handlers`` shape. Adding one is:
  define handler, register it in ``_ACTION_HANDLERS``.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils import wedge_events
from utils.paths import get_real_user_home
from utils.wedge_events import WedgeEvent

logger = logging.getLogger(__name__)


# ── Action taxonomy ──────────────────────────────────────────────────


ACTION_LOG_ONLY = "log_only"
"""Default — write an audit-log row; no side-effect on the system."""

ACTION_RESTART_RNSD = "systemctl_restart_rnsd"
"""Per-box opt-in. Runs ``sudo systemctl restart rnsd`` via subprocess
in a worker thread, gated by cooldown + cascade-detector pre_fail.
Restarts every RNS client that's connected — gateway, tracer, NomadNet,
this map service. Use only when sustained wedges are the dominant
operator pain. Configure in ``~/.config/meshforge/map_settings.json``
as ``"cascade_recovery_action": "systemctl_restart_rnsd"``.
"""


KNOWN_ACTIONS: Tuple[str, ...] = (ACTION_LOG_ONLY, ACTION_RESTART_RNSD)


# ── Defaults (overridable via map_settings.json) ─────────────────────


DEFAULT_COOLDOWN_S = 300.0
"""Minimum seconds between successive non-log actions. 5 minutes
matches the rnsd-restart window — long enough that one operator click
doesn't compete with the actor; short enough that recovery from a
sustained wedge isn't delayed beyond the next tracer-rollup window."""

DEFAULT_DEDUP_WINDOW_S = 60.0
"""Two ``WedgeEvent`` arrivals with the same ``(label, target)`` inside
this window collapse to a single audit-log row. The watchdog fires
once per wedge in the production path (because the process aborts),
but tests + future use-cases may publish bursts."""

DEFAULT_GATE_REQUIRES_DETECTOR = True
"""When True, ``systemctl_restart_rnsd`` actions require the cascade
detector to currently show the ``rns_rpc_wedge`` fingerprint at
``pre_fail`` or worse. A single watchdog fire isn't sufficient signal
on its own — the detector's hysteresis is the durable evidence. Tests
flip this off to exercise the action path without spinning up the
detector."""


# ── Public dataclasses ───────────────────────────────────────────────


@dataclass(frozen=True)
class AuditEntry:
    """One audit-log row.

    ``outcome`` is the post-decision label, one of:

    * ``logged`` — action=log_only, no side-effect
    * ``dedup_skipped`` — same ``(label, target)`` within dedup window
    * ``cooldown_skipped`` — action blocked by cooldown timer
    * ``gate_not_satisfied`` — cascade detector not in pre_fail (or
      detector singleton not started yet for non-test paths)
    * ``dispatched`` — action thread queued; outcome unknown yet
    * ``succeeded`` — subprocess completed with returncode == 0
    * ``failed`` — subprocess raised or returned non-zero
    """

    ts: float
    event: Dict[str, Any]
    action: str
    outcome: str
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "ts": self.ts,
            "event": dict(self.event),
            "action": self.action,
            "outcome": self.outcome,
        }
        if self.error:
            d["error"] = self.error
        return d


# ── Subprocess seam (so tests can mock) ──────────────────────────────


SubprocessRunner = Callable[[List[str], float], "subprocess.CompletedProcess[str]"]


def _default_subprocess_runner(
    cmd: List[str], timeout_s: float
) -> "subprocess.CompletedProcess[str]":
    """Production subprocess invocation for the restart action.

    Pulled out as a seam so tests can substitute a deterministic
    ``CompletedProcess`` without exec()ing real systemctl.
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )


# ── Cascade-detector gate seam (so tests don't need the daemon) ──────


CascadeStateGetter = Callable[[], Dict[str, Any]]


def _default_cascade_state_getter() -> Dict[str, Any]:
    """Read the cascade detector singleton's current snapshot.

    Wrapped so tests pass a stub that returns whatever fingerprint
    state they want. Returns an empty snapshot when the detector has
    never been constructed — the actor treats that as "gate not
    satisfied" rather than crashing.
    """
    try:
        from utils.cascade_detector import _singleton  # type: ignore

        if _singleton is None:
            return {"fingerprints": [], "summary": {}, "clean": []}
        return _singleton.get_snapshot()
    except Exception:
        return {"fingerprints": [], "summary": {}, "clean": []}


# ── Audit-log path resolution ────────────────────────────────────────


_AUDIT_LOG_ENV = "MESHFORGE_CASCADE_RECOVERY_LOG"
"""Env override for the audit-log path. Tests set this to a tmp_path
so the production log isn't touched."""


def default_audit_log_path() -> Path:
    """Resolve the audit-log path.

    Env var ``MESHFORGE_CASCADE_RECOVERY_LOG`` wins when set (used by
    tests; can also be useful for ops who want the log on a different
    volume). Otherwise:
    ``$XDG_STATE_HOME/meshforge/cascade_recovery.log`` (matching the
    tracer state-dir convention so an operator inspecting one finds
    the other in the same directory).
    """
    override = os.environ.get(_AUDIT_LOG_ENV)
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else get_real_user_home() / ".local" / "state"
    return base / "meshforge" / "cascade_recovery.log"


# ── The actor ────────────────────────────────────────────────────────


@dataclass
class _RecoveryStats:
    """In-memory counters surfaced via ``snapshot()``. The audit log is
    the durable record; this is just what the operator scrapes when
    checking ``/fleet/cascade.recovery``."""

    events_received: int = 0
    actions_dispatched: int = 0
    actions_succeeded: int = 0
    actions_failed: int = 0
    cooldown_skipped: int = 0
    dedup_skipped: int = 0
    gate_not_satisfied: int = 0
    last_event_ts: Optional[float] = None
    last_action_ts: Optional[float] = None
    last_action_outcome: Optional[str] = None
    last_error: Optional[str] = None


class CascadeRecoveryActor:
    """Subscribes to ``wedge_events`` and runs the configured action.

    Construct directly only in tests; the production path uses
    ``get_singleton()`` so ``map_data_service`` can register the
    subscriber once per process.

    The constructor does NOT subscribe automatically — call ``start()``.
    Idempotent — repeat ``start()`` calls leave only one subscription.
    """

    def __init__(
        self,
        *,
        action: str = ACTION_LOG_ONLY,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        dedup_window_s: float = DEFAULT_DEDUP_WINDOW_S,
        audit_log_path: Optional[Path] = None,
        subprocess_runner: SubprocessRunner = _default_subprocess_runner,
        cascade_state_getter: CascadeStateGetter = _default_cascade_state_getter,
        clock: Callable[[], float] = time.time,
        gate_requires_detector: bool = DEFAULT_GATE_REQUIRES_DETECTOR,
    ) -> None:
        if action not in KNOWN_ACTIONS:
            logger.warning(
                "cascade_recovery: unknown action=%r, falling back to %s",
                action, ACTION_LOG_ONLY,
            )
            action = ACTION_LOG_ONLY
        self._action = action
        self._cooldown_s = float(cooldown_s)
        self._dedup_window_s = float(dedup_window_s)
        self._audit_log_path = audit_log_path or default_audit_log_path()
        self._subprocess_runner = subprocess_runner
        self._cascade_state_getter = cascade_state_getter
        self._clock = clock
        self._gate_requires_detector = bool(gate_requires_detector)

        self._lock = threading.Lock()
        self._started = False
        self._last_action_dispatch_ts: float = 0.0
        # Most recent (label, target) → ts. Cheap LRU isn't needed —
        # the production fingerprint catalog has 2 entries today and
        # ~10 plausible labels even after Track 3 expansion.
        self._dedup: Dict[Tuple[str, Optional[str]], float] = {}
        self._stats = _RecoveryStats()

    # ── lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Register the wedge_events subscriber. Idempotent."""
        with self._lock:
            if self._started:
                return
            self._started = True
        wedge_events.subscribe(self._on_wedge)
        logger.info(
            "CascadeRecoveryActor started: action=%s cooldown=%.0fs "
            "audit_log=%s",
            self._action, self._cooldown_s, self._audit_log_path,
        )

    def stop(self) -> None:
        """Unsubscribe. Safe to call without start()."""
        with self._lock:
            if not self._started:
                return
            self._started = False
        wedge_events.unsubscribe(self._on_wedge)

    # ── operator/HTTP surface ────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Read-only state for /fleet/cascade.recovery.

        Includes config (action / cooldown_s), live counters, and
        cooldown_remaining_s so the operator can see how long until the
        next action attempt is permitted. The audit log is the durable
        side; this is the fast in-memory peek.
        """
        with self._lock:
            now = self._clock()
            elapsed = now - self._last_action_dispatch_ts \
                if self._last_action_dispatch_ts else None
            cooldown_remaining = (
                max(0.0, self._cooldown_s - elapsed)
                if elapsed is not None and self._action != ACTION_LOG_ONLY
                else 0.0
            )
            stats = self._stats
            return {
                "started": self._started,
                "action": self._action,
                "cooldown_s": self._cooldown_s,
                "dedup_window_s": self._dedup_window_s,
                "audit_log_path": str(self._audit_log_path),
                "cooldown_remaining_s": cooldown_remaining,
                "last_action_dispatch_ts": (
                    self._last_action_dispatch_ts or None
                ),
                "stats": {
                    "events_received": stats.events_received,
                    "actions_dispatched": stats.actions_dispatched,
                    "actions_succeeded": stats.actions_succeeded,
                    "actions_failed": stats.actions_failed,
                    "cooldown_skipped": stats.cooldown_skipped,
                    "dedup_skipped": stats.dedup_skipped,
                    "gate_not_satisfied": stats.gate_not_satisfied,
                    "last_event_ts": stats.last_event_ts,
                    "last_action_ts": stats.last_action_ts,
                    "last_action_outcome": stats.last_action_outcome,
                    "last_error": stats.last_error,
                },
            }

    # ── subscriber entry point ───────────────────────────────────

    def _on_wedge(self, event: WedgeEvent) -> None:
        """wedge_events subscriber callback.

        Must be fast — fires on the publisher's thread. Inline work:
        stats bump, dedup check, audit-log append, dispatch decision.
        The actual subprocess for ``systemctl_restart_rnsd`` runs in a
        worker thread so this method returns immediately.

        Wrapped in a broad try/except because ``wedge_events.publish``
        already protects publishers from subscriber exceptions, but we
        also want to stamp a synthetic audit row when we crash so the
        operator can see why the actor went quiet.
        """
        try:
            self._handle_event(event)
        except Exception as e:
            logger.exception(
                "cascade_recovery: handler crashed on event %r — "
                "writing failure audit row", event,
            )
            try:
                self._append_audit(AuditEntry(
                    ts=self._clock(),
                    event=event.to_dict(),
                    action=self._action,
                    outcome="failed",
                    error=f"handler_crash: {e!r}",
                ))
            except Exception:
                # Audit write failed too — nothing left to do; the
                # logger.exception above is the operator's signal.
                pass

    def _handle_event(self, event: WedgeEvent) -> None:
        with self._lock:
            self._stats.events_received += 1
            self._stats.last_event_ts = self._clock()

            # Dedup — same label/target inside the window collapses to
            # a single audit row + a single dispatch decision.
            key = (event.label, event.target)
            last = self._dedup.get(key)
            now = self._clock()
            if last is not None and (now - last) < self._dedup_window_s:
                self._stats.dedup_skipped += 1
                outcome = "dedup_skipped"
                action = self._action
                entry = AuditEntry(
                    ts=now, event=event.to_dict(),
                    action=action, outcome=outcome,
                )
                # Refresh the dedup timestamp so a long sustained
                # wedge collapses cleanly instead of re-firing every
                # window_s seconds.
                self._dedup[key] = now
                self._append_audit_unlocked(entry)
                return
            self._dedup[key] = now

        # Decide: log-only stops here; non-log actions run through the
        # cooldown + gate filter then dispatch.
        if self._action == ACTION_LOG_ONLY:
            self._append_audit(AuditEntry(
                ts=now, event=event.to_dict(),
                action=ACTION_LOG_ONLY, outcome="logged",
            ))
            return

        # Non-log action — check cooldown.
        with self._lock:
            cooldown_elapsed = (
                now - self._last_action_dispatch_ts
                if self._last_action_dispatch_ts else float("inf")
            )
            if cooldown_elapsed < self._cooldown_s:
                self._stats.cooldown_skipped += 1
                self._append_audit_unlocked(AuditEntry(
                    ts=now, event=event.to_dict(),
                    action=self._action, outcome="cooldown_skipped",
                ))
                return

        # Gate: cascade detector must show the related fingerprint at
        # pre_fail (or worse). Skipped when the operator explicitly
        # disables the gate (tests, or boxes where the detector hasn't
        # been wired in).
        if self._gate_requires_detector and not self._cascade_gate_satisfied(event):
            with self._lock:
                self._stats.gate_not_satisfied += 1
            self._append_audit(AuditEntry(
                ts=now, event=event.to_dict(),
                action=self._action, outcome="gate_not_satisfied",
            ))
            return

        # Dispatch — record the timestamp NOW so a wedge storm doesn't
        # race past the cooldown check while the worker is running.
        with self._lock:
            self._last_action_dispatch_ts = now
            self._stats.actions_dispatched += 1
        self._append_audit(AuditEntry(
            ts=now, event=event.to_dict(),
            action=self._action, outcome="dispatched",
        ))
        worker = threading.Thread(
            target=self._run_action_worker,
            args=(event,),
            name=f"cascade-recovery-{self._action}",
            daemon=True,
        )
        worker.start()

    # ── action handlers ──────────────────────────────────────────

    def _run_action_worker(self, event: WedgeEvent) -> None:
        """Worker thread for non-log actions. Runs the subprocess and
        writes the final-outcome audit row."""
        handler = _ACTION_HANDLERS.get(self._action)
        if handler is None:
            # Shouldn't happen — guarded in __init__ — but keep the
            # audit trail honest if a future patch adds a partial
            # registration.
            self._append_audit(AuditEntry(
                ts=self._clock(), event=event.to_dict(),
                action=self._action, outcome="failed",
                error=f"no handler registered for {self._action!r}",
            ))
            with self._lock:
                self._stats.actions_failed += 1
                self._stats.last_action_outcome = "failed"
                self._stats.last_error = "no_handler"
                self._stats.last_action_ts = self._clock()
            return

        try:
            handler(self, event)
        except Exception as e:
            logger.exception("cascade_recovery worker raised on %s", self._action)
            self._append_audit(AuditEntry(
                ts=self._clock(), event=event.to_dict(),
                action=self._action, outcome="failed",
                error=repr(e),
            ))
            with self._lock:
                self._stats.actions_failed += 1
                self._stats.last_action_outcome = "failed"
                self._stats.last_error = repr(e)
                self._stats.last_action_ts = self._clock()

    def _action_restart_rnsd(self, event: WedgeEvent) -> None:
        """Run ``sudo systemctl restart rnsd`` and record outcome.

        Uses the same ``-n`` non-interactive flag convention as
        ``utils.service_check._sudo_cmd`` so a misconfigured sudoers
        fails loudly rather than hanging on a TTY prompt the actor
        can't satisfy.
        """
        cmd = ["sudo", "-n", "systemctl", "restart", "rnsd"]
        try:
            result = self._subprocess_runner(cmd, 30.0)
        except subprocess.TimeoutExpired as e:
            self._append_audit(AuditEntry(
                ts=self._clock(), event=event.to_dict(),
                action=self._action, outcome="failed",
                error=f"timeout: {e}",
            ))
            with self._lock:
                self._stats.actions_failed += 1
                self._stats.last_action_outcome = "failed"
                self._stats.last_error = "timeout"
                self._stats.last_action_ts = self._clock()
            return
        except Exception as e:
            self._append_audit(AuditEntry(
                ts=self._clock(), event=event.to_dict(),
                action=self._action, outcome="failed",
                error=repr(e),
            ))
            with self._lock:
                self._stats.actions_failed += 1
                self._stats.last_action_outcome = "failed"
                self._stats.last_error = repr(e)
                self._stats.last_action_ts = self._clock()
            return

        if result.returncode == 0:
            self._append_audit(AuditEntry(
                ts=self._clock(), event=event.to_dict(),
                action=self._action, outcome="succeeded",
            ))
            with self._lock:
                self._stats.actions_succeeded += 1
                self._stats.last_action_outcome = "succeeded"
                self._stats.last_error = None
                self._stats.last_action_ts = self._clock()
            logger.warning(
                "cascade_recovery: restarted rnsd in response to wedge "
                "label=%s target=%s", event.label, event.target,
            )
        else:
            err_tail = (result.stderr or "")[-400:]
            self._append_audit(AuditEntry(
                ts=self._clock(), event=event.to_dict(),
                action=self._action, outcome="failed",
                error=f"rc={result.returncode}: {err_tail}",
            ))
            with self._lock:
                self._stats.actions_failed += 1
                self._stats.last_action_outcome = "failed"
                self._stats.last_error = f"rc={result.returncode}"
                self._stats.last_action_ts = self._clock()

    # ── gate ────────────────────────────────────────────────────

    def _cascade_gate_satisfied(self, event: WedgeEvent) -> bool:
        """True if the cascade detector currently has at least one
        non-clean fingerprint at ``pre_fail`` or worse.

        Today the only fingerprint that maps to a wedge event is
        ``rns_rpc_wedge``. We don't *require* the fingerprint name to
        match the event label — any active pre_fail is sufficient,
        which keeps this future-proof when Track 3 adds more
        fingerprints (wal_oversize, tcp_4403_contention, etc.).
        """
        try:
            snap = self._cascade_state_getter()
        except Exception:
            return False
        fps = snap.get("fingerprints") or []
        for fp in fps:
            state = fp.get("state")
            if state in ("pre_fail", "wedged", "degraded"):
                return True
        return False

    # ── audit log ───────────────────────────────────────────────

    def _append_audit(self, entry: AuditEntry) -> None:
        """Append one JSON line to the audit log.

        Single open/write/close per call. Best-effort — failure to
        write the audit row never crashes the subscriber path; the
        outcome is logged but the actor keeps running. mkdir is
        idempotent.
        """
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry.to_dict(), separators=(",", ":"))
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            logger.warning(
                "cascade_recovery: audit append failed (%s) — entry=%s",
                e, entry,
            )

    def _append_audit_unlocked(self, entry: AuditEntry) -> None:
        """Variant called from inside ``self._lock`` — same behavior,
        named distinctly to make the locking discipline at call sites
        easy to grep."""
        self._append_audit(entry)


# ── Action registry ──────────────────────────────────────────────────


_ActionHandler = Callable[[CascadeRecoveryActor, WedgeEvent], None]


_ACTION_HANDLERS: Dict[str, _ActionHandler] = {
    # log_only is handled inline in _handle_event; no worker needed.
    ACTION_RESTART_RNSD: CascadeRecoveryActor._action_restart_rnsd,
}
"""Maps action name → method on the actor. ``log_only`` deliberately
absent — it never spawns a worker. Adding a future action means:
implement an ``_action_*`` method on ``CascadeRecoveryActor`` + add a
row here + extend ``KNOWN_ACTIONS``."""


# ── Process-wide singleton ───────────────────────────────────────────


_singleton: Optional[CascadeRecoveryActor] = None
_singleton_lock = threading.Lock()


def get_singleton() -> CascadeRecoveryActor:
    """Return the process-wide ``CascadeRecoveryActor``, constructing
    it from current ``map_settings.json`` on first call.

    Settings read once at construction time — operator changes require
    a service restart to take effect (matches the federation_timeout
    pattern; map_settings is not hot-reloaded). The map_data_service
    constructs and ``start()``s this in the same hook that starts the
    cascade detector.
    """
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = _construct_from_settings()
        return _singleton


def _construct_from_settings() -> CascadeRecoveryActor:
    """Build an actor from ``map_settings.json``. Best-effort — any
    parse error falls back to log-only defaults so the singleton
    construction can never crash the map service startup."""
    try:
        from utils.common import SettingsManager
        settings = SettingsManager(
            "map_settings",
            defaults={
                "cascade_recovery_enabled": True,
                "cascade_recovery_action": ACTION_LOG_ONLY,
                "cascade_recovery_cooldown_s": DEFAULT_COOLDOWN_S,
                "cascade_recovery_dedup_window_s": DEFAULT_DEDUP_WINDOW_S,
            },
        )
        enabled = bool(settings.get("cascade_recovery_enabled", True))
        action = settings.get("cascade_recovery_action", ACTION_LOG_ONLY)
        cooldown_s = float(settings.get(
            "cascade_recovery_cooldown_s", DEFAULT_COOLDOWN_S
        ))
        dedup_window_s = float(settings.get(
            "cascade_recovery_dedup_window_s", DEFAULT_DEDUP_WINDOW_S
        ))
        if not enabled:
            # Honor the explicit disable: construct a log-only actor
            # but don't auto-start it (caller's start() becomes a no-op
            # for an unsubscribed instance). The singleton still exists
            # so /fleet/cascade.recovery can answer "started=false".
            actor = CascadeRecoveryActor(
                action=ACTION_LOG_ONLY,
                cooldown_s=cooldown_s,
                dedup_window_s=dedup_window_s,
            )
            return actor
        return CascadeRecoveryActor(
            action=action,
            cooldown_s=cooldown_s,
            dedup_window_s=dedup_window_s,
        )
    except Exception as e:
        logger.warning(
            "cascade_recovery: settings load failed (%s) — falling back "
            "to log-only defaults", e,
        )
        return CascadeRecoveryActor(action=ACTION_LOG_ONLY)


def _reset_singleton_for_tests() -> None:
    """Test-only: drop the singleton so the next ``get_singleton()`` call
    rebuilds from the (test-patched) settings. Production code must
    never call this."""
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            try:
                _singleton.stop()
            except Exception:
                pass
        _singleton = None
