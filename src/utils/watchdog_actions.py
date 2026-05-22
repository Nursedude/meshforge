"""Per-box watchdog Phase 2 — auto-restart actions.

Companion to ``utils/watchdog_runner.py`` + ``utils/watchdog_probes.py``.
Phase 1 emits signals only; Phase 2 adds the ability to act on them by
issuing ``systemctl restart <service>``. The capability is gated behind
multiple independent safety layers:

1. **Default OFF**: The feature is disabled unless the per-box config
   file at ``~/.config/meshforge/watchdog.json`` (or ``/etc/meshforge/``)
   sets ``phase2_auto_restart.enabled: true``. Missing/malformed config
   means OFF.
2. **Allowlist only**: Restarts are gated on an explicit list of
   ``(signal_class, service)`` pairs. A signal that doesn't appear in
   the allowlist is observed-only, even with the master switch on.
3. **Consecutive-ticks gate**: A signal must persist for N ticks before
   action. Catches the today's-data class where ``http_local_unresponsive``
   self-cleared in 30 s — auto-restarting on a 1-tick signal would have
   killed a process that was recovering.
4. **Cooldown**: After a restart, the same service can't be restarted
   again for ``cooldown_s`` seconds. Prevents restart storms.
5. **Rate limit**: At most ``max_restarts_per_hour`` per service. Belt
   and suspenders with cooldown; catches the case where cooldown is
   misconfigured short.
6. **Operator-in-flight suppression**: ``service_inactive`` signals
   with ``extra.actual`` in ``{"activating", "deactivating", "reloading"}``
   are skipped — the operator is doing maintenance, don't fight them.
7. **Optional dry-run**: ``phase2_auto_restart.dry_run: true`` logs the
   decision but doesn't execute. Useful during soak — flip dry_run on
   first to see what WOULD restart, then flip dry_run off.

The decision logic is a pure function (``decide_restarts``) so it can
be exhaustively tested without subprocess mocking. ``execute_restart``
is the only side-effect site; it's a thin shell around
``subprocess.run`` with timeout + capture.

Restart history is in-memory (``RestartHistory``). Losing it on watchdog
restart is a safety feature: a watchdog that crash-loops and re-issues
its own remediation would create a restart storm. A fresh start means
cooldown clocks reset, which is the correct conservative behavior.
"""
from __future__ import annotations

import logging
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from utils.watchdog_probes import Signal


logger = logging.getLogger("watchdog.actions")


DEFAULT_CONSECUTIVE_TICKS = 5
DEFAULT_COOLDOWN_S = 600.0  # 10 minutes
DEFAULT_MAX_RESTARTS_PER_HOUR = 2
RATE_LIMIT_WINDOW_S = 3600.0

# States indicating the operator (or another orchestrator) is actively
# transitioning the service. Auto-restart must defer in these states or
# it fights live maintenance — exactly today's moc1 false-positive at
# 21:31 when ``systemctl restart meshforge-map.service`` was operator-
# initiated and the watchdog briefly saw 'deactivating'.
OPERATOR_IN_FLIGHT_STATES = frozenset({"activating", "deactivating", "reloading"})


@dataclass(frozen=True)
class AutoRestartRule:
    """One allowlist entry. Identity is (signal_class, service).

    All knobs have safe defaults — the operator only needs to declare
    the class+service pair to enable a remediation. Tighter values can
    be set per rule.
    """
    signal_class: str
    service: str
    consecutive_ticks: int = DEFAULT_CONSECUTIVE_TICKS
    cooldown_s: float = DEFAULT_COOLDOWN_S
    max_restarts_per_hour: int = DEFAULT_MAX_RESTARTS_PER_HOUR


@dataclass(frozen=True)
class Phase2Config:
    """Parsed phase2 section of the watchdog config.

    ``enabled=False`` is the default and is the only state in which
    ``decide_restarts`` will ever produce decisions. Operators flip
    this to True only after a soak window.
    """
    enabled: bool = False
    dry_run: bool = False
    rules: Tuple[AutoRestartRule, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RestartDecision:
    """One concrete decision to restart a service. Carries the matched
    rule and a human-readable reason so journal logging is precise."""
    service: str
    rule: AutoRestartRule
    reason: str
    dry_run: bool


class RestartHistory:
    """In-memory restart history per service.

    Loss on watchdog restart is intentional: see module docstring. The
    history is bounded — entries older than ``RATE_LIMIT_WINDOW_S`` are
    pruned on every record call so the dict doesn't grow unbounded over
    days of uptime.
    """

    def __init__(self) -> None:
        self._restarts: Dict[str, List[float]] = defaultdict(list)

    def record(self, service: str, ts: float) -> None:
        bucket = self._restarts[service]
        bucket.append(ts)
        # Prune anything older than the rate-limit window — entries
        # that age out of the window can never participate in a future
        # rate-limit decision.
        cutoff = ts - RATE_LIMIT_WINDOW_S
        self._restarts[service] = [t for t in bucket if t >= cutoff]

    def last_restart_ts(self, service: str) -> Optional[float]:
        bucket = self._restarts.get(service)
        if not bucket:
            return None
        return max(bucket)

    def restarts_in_window(self, service: str, since_ts: float) -> int:
        bucket = self._restarts.get(service, [])
        return sum(1 for t in bucket if t >= since_ts)


# ─────────────────────────────────────────────────────────────────────
# Pure decision logic
# ─────────────────────────────────────────────────────────────────────


def decide_restarts(
    *,
    signals_with_first_seen: List[Tuple[Signal, float]],
    config: Phase2Config,
    history: RestartHistory,
    now: float,
    tick_s: float,
) -> List[RestartDecision]:
    """Pure: which services should be restarted *this tick*.

    Returns an empty list when ``config.enabled`` is False (the default).
    Each decision passes every safety gate listed in the module
    docstring; this function is the central enforcement point.

    The caller is responsible for executing the decision and recording
    success in ``history``. Separating decision from execution keeps
    the logic testable without subprocess mocking.
    """
    if not config.enabled:
        return []

    decisions: List[RestartDecision] = []
    rule_by_pair: Dict[Tuple[str, str], AutoRestartRule] = {
        (r.signal_class, r.service): r for r in config.rules
    }

    for sig, first_seen_ts in signals_with_first_seen:
        rule = rule_by_pair.get((sig.cls, sig.subject))
        if rule is None:
            continue

        # Operator-in-flight: never restart a service that's already
        # mid-transition. The transient deactivating/activating states
        # produced today's moc1 false positive.
        if sig.cls == "service_inactive":
            actual = sig.extra.get("actual", "")
            if actual in OPERATOR_IN_FLIGHT_STATES:
                continue

        # Consecutive-ticks gate. We approximate "ticks elapsed" from
        # the first_seen wall-clock delta; close enough at 30s ticks
        # and tolerant to ±1 drift from probe latency. The +1 fudge in
        # the floor() converts "at least N ticks of observation" into
        # the inclusive comparison the operator expects.
        elapsed_s = now - first_seen_ts
        ticks_observed = int(elapsed_s // tick_s) + 1
        if ticks_observed < rule.consecutive_ticks:
            continue

        # Cooldown gate — distance since the last restart of this
        # specific service, regardless of which rule matched.
        last = history.last_restart_ts(rule.service)
        if last is not None and (now - last) < rule.cooldown_s:
            continue

        # Rate limit — count restarts in the trailing hour.
        recent = history.restarts_in_window(rule.service, now - RATE_LIMIT_WINDOW_S)
        if recent >= rule.max_restarts_per_hour:
            continue

        decisions.append(RestartDecision(
            service=rule.service,
            rule=rule,
            reason=(
                f"{sig.cls}/{sig.subject} severity={sig.severity} "
                f"persisted {ticks_observed} ticks (>= {rule.consecutive_ticks})"
            ),
            dry_run=config.dry_run,
        ))

    # Dedup by service — multiple signals could match the same service
    # via different rules; we only need one restart per service per tick.
    seen_services: set = set()
    deduped: List[RestartDecision] = []
    for d in decisions:
        if d.service in seen_services:
            continue
        seen_services.add(d.service)
        deduped.append(d)
    return deduped


# ─────────────────────────────────────────────────────────────────────
# Config parsing — lenient, default-OFF on any malformed input
# ─────────────────────────────────────────────────────────────────────


def parse_phase2_config(raw: dict) -> Phase2Config:
    """Parse the ``phase2_auto_restart`` section. Default OFF on bad input.

    Schema:

        {
          "phase2_auto_restart": {
            "enabled": false,
            "dry_run": false,
            "allowlist": [
              {
                "signal_class": "rns_shared_instance_unresponsive",
                "service": "rnsd.service",
                "consecutive_ticks": 5,
                "cooldown_s": 600,
                "max_restarts_per_hour": 2
              }
            ]
          }
        }

    A missing section, missing ``enabled``, or non-True ``enabled`` all
    yield ``Phase2Config(enabled=False)``. Any malformed rule is silently
    dropped (logged at WARNING) — better than failing the whole config
    parse and falling back to *everything* default.
    """
    section = raw.get("phase2_auto_restart")
    if not isinstance(section, dict):
        return Phase2Config()

    enabled = section.get("enabled") is True
    dry_run = section.get("dry_run") is True

    raw_rules = section.get("allowlist", [])
    rules: List[AutoRestartRule] = []
    if isinstance(raw_rules, list):
        for idx, r in enumerate(raw_rules):
            rule = _parse_rule(r, idx)
            if rule is not None:
                rules.append(rule)

    return Phase2Config(enabled=enabled, dry_run=dry_run, rules=tuple(rules))


def _parse_rule(raw: object, idx: int) -> Optional[AutoRestartRule]:
    if not isinstance(raw, dict):
        logger.warning("watchdog: phase2 allowlist entry %d is not an object — ignoring", idx)
        return None
    sig_class = raw.get("signal_class")
    service = raw.get("service")
    if not isinstance(sig_class, str) or not sig_class:
        logger.warning("watchdog: phase2 allowlist entry %d missing signal_class — ignoring", idx)
        return None
    if not isinstance(service, str) or not service:
        logger.warning("watchdog: phase2 allowlist entry %d missing service — ignoring", idx)
        return None

    ticks = _coerce_positive_int(raw.get("consecutive_ticks"), DEFAULT_CONSECUTIVE_TICKS)
    cooldown = _coerce_positive_float(raw.get("cooldown_s"), DEFAULT_COOLDOWN_S)
    max_rate = _coerce_positive_int(raw.get("max_restarts_per_hour"), DEFAULT_MAX_RESTARTS_PER_HOUR)

    return AutoRestartRule(
        signal_class=sig_class,
        service=service,
        consecutive_ticks=ticks,
        cooldown_s=cooldown,
        max_restarts_per_hour=max_rate,
    )


def _coerce_positive_int(raw: object, default: int) -> int:
    if isinstance(raw, bool):
        return default  # bool is a subclass of int — guard explicitly
    if isinstance(raw, int) and raw > 0:
        return raw
    return default


def _coerce_positive_float(raw: object, default: float) -> float:
    if isinstance(raw, bool):
        return default
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    return default


# ─────────────────────────────────────────────────────────────────────
# Execution — the one side-effect site
# ─────────────────────────────────────────────────────────────────────


def execute_restart(
    service: str,
    *,
    systemctl_path: str = "systemctl",
    timeout_s: float = 30.0,
) -> bool:
    """Run ``systemctl restart <service>``. Return True on rc=0.

    The watchdog runs as root (StateDirectory + /proc access), so no
    sudo is needed. Errors are logged at WARNING — the caller (runner)
    decides whether to retry next tick or escalate.
    """
    cmd = [systemctl_path, "restart", service]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        logger.warning("watchdog: restart timeout service=%s", service)
        return False
    except (FileNotFoundError, OSError) as exc:
        logger.warning("watchdog: restart failed to invoke service=%s err=%s", service, exc)
        return False

    if result.returncode == 0:
        return True
    logger.warning(
        "watchdog: restart non-zero exit service=%s rc=%d stderr=%s",
        service, result.returncode, (result.stderr or "").strip()[:200],
    )
    return False


__all__ = [
    "AutoRestartRule",
    "Phase2Config",
    "RestartDecision",
    "RestartHistory",
    "decide_restarts",
    "parse_phase2_config",
    "execute_restart",
    "DEFAULT_CONSECUTIVE_TICKS",
    "DEFAULT_COOLDOWN_S",
    "DEFAULT_MAX_RESTARTS_PER_HOUR",
    "RATE_LIMIT_WINDOW_S",
    "OPERATOR_IN_FLIGHT_STATES",
]
