"""Cascade detector — periodic loop that evaluates the fingerprint catalog
and surfaces pre-failure state via ``GET /fleet/cascade`` (Track 0C).

Runs a single daemon thread that iterates ``cascade_fingerprints.FINGERPRINTS``
every ``check_interval_s`` (default 30). Per-fingerprint hysteresis prevents
flapping: one hit ⇒ ``suspected``, two consecutive hits ⇒ the fingerprint's
declared ``severity`` (``pre_fail`` / ``wedged`` / ``degraded``).

State is in-memory only (read-only health check, must not become a load
source). Exposed via ``get_snapshot()`` for the HTTP handler.

Lifecycle:
    detector = CascadeDetector()
    detector.start()
    ...
    snap = detector.get_snapshot()
    detector.stop()

See plan: ``~/.claude/plans/we-have-a-cycle-jolly-wadler.md`` Track 0C +
Track 3.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from utils.cascade_fingerprints import (
    FINGERPRINTS,
    Fingerprint,
    ProbeHit,
)

logger = logging.getLogger(__name__)


# State labels emitted on /fleet/cascade.
# clean        — last probe returned None
# suspected    — one consecutive hit (hysteresis: not yet escalated)
# pre_fail     — N consecutive hits, fingerprint declared "pre_fail"
# wedged       — N consecutive hits, fingerprint declared "wedged"
# degraded     — N consecutive hits, fingerprint declared "degraded"
_CONSECUTIVE_HITS_TO_ESCALATE = 2


@dataclass
class _FingerprintState:
    """Per-fingerprint runtime tracking."""
    name: str
    state: str = "clean"
    consecutive_hits: int = 0
    first_hit_ts: Optional[float] = None
    last_evaluated_ts: Optional[float] = None
    last_hit_ts: Optional[float] = None
    last_evidence: Optional[str] = None
    last_metric: dict = field(default_factory=dict)


class CascadeDetector:
    """Background thread that evaluates the fingerprint catalog.

    Constructor accepts optional ``fingerprints`` to override the module
    default (used by tests). ``clock`` is injectable so hysteresis tests
    don't need real time.
    """

    def __init__(
        self,
        fingerprints: Optional[List[Fingerprint]] = None,
        check_interval_s: int = 30,
        clock: callable = time.time,
    ):
        self._fingerprints: List[Fingerprint] = list(
            fingerprints if fingerprints is not None else FINGERPRINTS
        )
        self._check_interval_s = max(5, int(check_interval_s))
        self._clock = clock
        self._states: Dict[str, _FingerprintState] = {
            fp.name: _FingerprintState(name=fp.name) for fp in self._fingerprints
        }
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── public API ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Idempotent. Safe to call from any thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="cascade-detector",
        )
        self._thread.start()
        logger.info(
            f"CascadeDetector started: {len(self._fingerprints)} fingerprint(s), "
            f"interval={self._check_interval_s}s"
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def evaluate_once(self) -> None:
        """Run one pass over all fingerprints. Public for tests + manual
        triggers; the daemon thread loops this.
        """
        now = self._clock()
        for fp in self._fingerprints:
            self._evaluate_fingerprint(fp, now)

    def get_snapshot(self) -> Dict:
        """Return a thread-safe snapshot of the current detector state."""
        with self._lock:
            generated_at = self._clock()
            fingerprints: List[Dict] = []
            clean: List[str] = []
            counts: Dict[str, int] = {}
            for fp in self._fingerprints:
                s = self._states[fp.name]
                if s.state == "clean":
                    clean.append(fp.name)
                    continue
                counts[s.state] = counts.get(s.state, 0) + 1
                fingerprints.append({
                    "name": fp.name,
                    "state": s.state,
                    "consecutive_hits": s.consecutive_hits,
                    "first_hit_ts": s.first_hit_ts,
                    "last_evaluated_ts": s.last_evaluated_ts,
                    "last_hit_ts": s.last_hit_ts,
                    "evidence": s.last_evidence,
                    "metric": dict(s.last_metric),
                    "severity_if_pre_fail": fp.severity,
                    "incident_refs": list(fp.incident_refs),
                    "coupled_to": list(fp.coupled_to),
                })
            return {
                "generated_at": generated_at,
                "summary": {
                    "total_fingerprints": len(self._fingerprints),
                    "clean": len(clean),
                    **counts,
                },
                "fingerprints": fingerprints,
                "clean": clean,
            }

    def summary(self) -> Dict[str, int]:
        """Compact `{state: count}` dict for inclusion in /api/status."""
        with self._lock:
            counts: Dict[str, int] = {"clean": 0}
            for s in self._states.values():
                counts[s.state] = counts.get(s.state, 0) + 1
            return counts

    # ── internals ─────────────────────────────────────────────────────────

    def _evaluate_fingerprint(self, fp: Fingerprint, now: float) -> None:
        """Probe one fingerprint and update its state. Never raises."""
        state = self._states[fp.name]

        # Per-fingerprint cadence gate (cheap probes can still be expensive
        # in aggregate; respect each entry's declared cadence_s).
        if state.last_evaluated_ts is not None:
            if now - state.last_evaluated_ts < fp.cadence_s:
                return

        try:
            hit: Optional[ProbeHit] = fp.probe()
        except Exception as e:
            logger.warning(
                f"cascade fingerprint {fp.name}: probe raised {type(e).__name__}: {e} "
                "— treating as miss"
            )
            hit = None

        with self._lock:
            state.last_evaluated_ts = now
            if hit is None:
                # Miss — reset to clean. Log the recovery if we were
                # previously suspected/escalated.
                if state.state != "clean":
                    logger.info(
                        f"cascade fingerprint {fp.name}: cleared after "
                        f"{state.consecutive_hits} consecutive hit(s)"
                    )
                state.state = "clean"
                state.consecutive_hits = 0
                state.first_hit_ts = None
                state.last_evidence = None
                state.last_metric = {}
                return

            # Hit — escalate per hysteresis.
            state.consecutive_hits += 1
            state.last_hit_ts = now
            state.last_evidence = hit.evidence
            state.last_metric = dict(hit.metric)
            if state.first_hit_ts is None:
                state.first_hit_ts = now
            prev_state = state.state
            if state.consecutive_hits >= _CONSECUTIVE_HITS_TO_ESCALATE:
                state.state = fp.severity
            else:
                state.state = "suspected"
            if state.state != prev_state:
                logger.warning(
                    f"cascade fingerprint {fp.name}: {prev_state} → {state.state} "
                    f"({state.consecutive_hits} consecutive hit(s)): {hit.evidence}"
                )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.evaluate_once()
            except Exception as e:
                logger.error(
                    f"CascadeDetector evaluate_once crashed: {e}", exc_info=True
                )
            if self._stop_event.wait(self._check_interval_s):
                return


# ── Process-wide singleton (created on demand by map_data_service) ──────

_singleton: Optional[CascadeDetector] = None
_singleton_lock = threading.Lock()


def get_singleton() -> CascadeDetector:
    """Return the process-wide CascadeDetector, creating it on first call.

    The map service constructs (and start()s) this. The HTTP handler
    calls this to serve /fleet/cascade.
    """
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = CascadeDetector()
        return _singleton
