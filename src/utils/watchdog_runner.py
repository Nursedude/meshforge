"""Per-box watchdog daemon — ticks every 30s, runs probes, writes JSON.

Companion to ``utils/watchdog_probes.py``. Entry point: ``main()`` runs
the loop until SIGTERM/SIGINT. Writes its aggregated signal state to
``/var/lib/meshforge/watchdog.json`` (atomic-rename) so the map server's
``/api/status`` endpoint can read it without locking. Federation already
polls ``/api/status`` across peers — watchdog signals ride that channel
to the ``/fleet`` rollup with no new HTTP plumbing (Issue #54
peer_name correlation labels rows for free).

Phase 1 commitment per the approved plan:

* No service restarts. Signal only. The watchdog is the observability
  layer; whether to act on signals is a human call this week and a
  later opt-in flag (Phase 3).
* Edge-transition logging: a signal that stays active stays quiet on
  the journal; only first-seen and cleared transitions hit INFO/WARNING.
* Closed enum of failure classes — see ``watchdog_probes.SIGNAL_CLASSES``.

Configuration:

* Probe target list is hard-coded conservatively here (the services and
  endpoints that exist on every fleet box). A future refinement could
  read per-box config from ``~/.config/meshforge/watchdog.json``;
  Phase 1 keeps it minimal so the deployment is one ``systemctl enable``
  per box, no config writing.

Why root: ``/proc/<pid>/task/<pid>/stack`` requires CAP_SYS_PTRACE on
most kernels, and the wedge-detection probe is the highest-ROI signal.
The runner does no other privileged work and never writes outside
``/var/lib/meshforge/``.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.rns_status_parser import run_rnstatus
from utils.watchdog_probes import (
    Signal,
    probe_delivery_write_canary,
    probe_fd_exhaustion,
    probe_foundation_drift,
    probe_parity_drift,
    probe_rns_version_drift,
    probe_role_drift,
    probe_http_local,
    probe_lxmf_process_wedge,
    probe_main_thread_wedge,
    probe_rns_interface_down_peer_reachable,
    probe_rns_namespace_collision,
    probe_rns_rpc_responsive,
    probe_rns_shared_instance_responsive,
    probe_service_inactive,
    probe_tracer_peer_unreachable,
    signal_to_dict,
)
from utils.watchdog_actions import (
    Phase2Config,
    RestartHistory,
    decide_restarts,
    execute_restart,
    parse_phase2_config,
)


logger = logging.getLogger("watchdog")

DEFAULT_OUTPUT_PATH = Path("/var/lib/meshforge/watchdog.json")
DEFAULT_TICK_S = 30.0

# Optional per-box override file. JSON, lives in operator home next to
# the other meshforge config. Allows boxes with intentionally-different
# service topology (moc3 is gateway-only — no meshforge-map.service)
# to suppress false-positive signals without changing the closed-enum
# probe set. Schema:
#   {
#     "services_expected_active": ["rnsd.service"],       # optional, full replacement
#     "services_wedge_check": ["meshforge-map.service"],   # optional, full replacement
#     "http_port": 5000,                                   # optional
#     "phase2_auto_restart": {                             # optional, default OFF
#       "enabled": false,                                  # master switch
#       "dry_run": false,                                  # log decisions, skip execute
#       "allowlist": [
#         { "signal_class": "rns_shared_instance_unresponsive",
#           "service": "rnsd.service",
#           "consecutive_ticks": 5,
#           "cooldown_s": 600,
#           "max_restarts_per_hour": 2 }
#       ]
#     }
#   }
# Missing keys fall back to hardcoded defaults. Missing file = pure
# defaults. Malformed file logs a WARNING and falls back to defaults —
# never blocks the watchdog from starting.
#
# Phase 2 auto-restart: `enabled` MUST be literal JSON true (truthy
# non-bools rejected); see `watchdog_actions.parse_phase2_config` for
# the safety-gate inventory. Default state across the fleet on
# 2026-05-21 ship: Phase 2 OFF on every box.
DEFAULT_CONFIG_FILE = Path("~/.config/meshforge/watchdog.json")

# Services every fleet box has and that should be active in the
# standard ``full``/``gateway`` profiles. Boxes that intentionally don't
# run a unit (e.g. moc3 is gateway-only, meshforge-map is disabled
# there) will surface a ``service_inactive`` signal at severity=degraded
# — operator can suppress per-box via a future config flag, but in
# Phase 1 we want the signal so an unintentional inactive doesn't
# silently match an intentional one.
_DEFAULT_SERVICES_EXPECTED_ACTIVE: Tuple[str, ...] = (
    "rnsd.service",
    "meshforge-map.service",
)

# Services to probe for main-thread wedge (Issue #68). Limited to the
# ones most likely to wedge on a stuck rnsd Unix socket — these all
# call RNS.Reticulum() on their main thread.
_DEFAULT_SERVICES_WEDGE_CHECK: Tuple[str, ...] = (
    "meshforge-map.service",
)


# ─────────────────────────────────────────────────────────────────────
# State tracker — first-seen + cleared edge transitions
# ─────────────────────────────────────────────────────────────────────


class SignalTracker:
    """Tracks signal lifecycle so the runner can log edge transitions.

    Maps ``(class, subject)`` → first_seen unix ts. Each tick the runner
    passes the current signal set; we diff against the previous tick
    and surface the deltas for logging without re-logging steady state.
    """

    def __init__(self) -> None:
        self._active: Dict[Tuple[str, str], float] = {}

    def update(
        self, current: List[Signal], *, now: float,
    ) -> Tuple[List[Tuple[Signal, float]], List[Tuple[str, str]]]:
        """Diff against previous tick.

        Returns:
            (newly_active, newly_cleared) where:
              - newly_active: list of (signal, first_seen_ts) for signals
                that appeared this tick (or persisted but are reported
                for first time — first_seen carries the original ts).
              - newly_cleared: list of (class, subject) keys that were
                active before but absent now.
        """
        current_keys = {s.key() for s in current}
        previous_keys = set(self._active.keys())

        newly_cleared = list(previous_keys - current_keys)

        first_seen_ts_for: List[Tuple[Signal, float] ] = []
        for sig in current:
            key = sig.key()
            first_seen = self._active.get(key)
            if first_seen is None:
                # New transition
                self._active[key] = now
                first_seen_ts_for.append((sig, now))
            else:
                # Still active — preserve original first_seen
                first_seen_ts_for.append((sig, first_seen))

        for key in newly_cleared:
            self._active.pop(key, None)

        return first_seen_ts_for, newly_cleared


# ─────────────────────────────────────────────────────────────────────
# Probe dispatch
# ─────────────────────────────────────────────────────────────────────


def run_all_probes(
    *,
    rns_instance_name: Optional[str],
    services_expected_active: Tuple[str, ...] = _DEFAULT_SERVICES_EXPECTED_ACTIVE,
    services_wedge_check: Tuple[str, ...] = _DEFAULT_SERVICES_WEDGE_CHECK,
    http_port: int = 5000,
) -> List[Signal]:
    """Run every probe, return aggregated signals.

    Order is deliberate: cheap probes first so an expensive probe
    failing late doesn't delay the cheap ones' results in case of a
    future timeout/parallelization refactor.
    """
    signals: List[Signal] = []

    # Service-state probes — cheap, one subprocess each.
    for unit in services_expected_active:
        sig = probe_service_inactive(unit)
        if sig is not None:
            signals.append(sig)

    # RNS namespace collision — single ``ss -xnpl`` call.
    if rns_instance_name:
        sig = probe_rns_namespace_collision(rns_instance_name)
        if sig is not None:
            signals.append(sig)

    # RNS shared-instance responsive — Unix socket connect with timeout.
    # Catches today's (2026-05-21 moc1) wedge class where rnsd is alive
    # but new shared-instance connects hang. Cheap: 2s timeout worst case.
    if rns_instance_name:
        sig = probe_rns_shared_instance_responsive(rns_instance_name)
        if sig is not None:
            signals.append(sig)

    # RNS rnstatus-consuming probes share ONE bounded rnstatus call so a
    # wedged rnsd can't stall the 30s tick with two long-timeout
    # subprocesses. 8s is plenty for a healthy rnstatus (~1-2s) and well
    # under the tick. Not gated on rns_instance_name: rnstatus enumerates
    # interfaces regardless, and both probes self-guard when rnsd is
    # unreachable (binary lookup fails fast on RNS-less boxes — no
    # subprocess spawned).
    rns_status = run_rnstatus(timeout_s=8.0)

    # RNS RPC unresponsive (2026-05-30): rnstatus itself hung though the
    # shared-instance socket accepts — the wedged-rnsd-RPC class the
    # connect-only shared-instance probe can't see (#68/#69 family).
    sig = probe_rns_rpc_responsive(rnstatus_status=rns_status)
    if sig is not None:
        signals.append(sig)

    # RNS interface Down while peer reachable (2026-05-30 incident).
    # Bounded TCP-connect to any Down TCPInterface's peer. Reuses the
    # shared rnstatus result; self-guards on parse_error when rnsd is
    # unreachable. Catches the stuck-uplink class directly at the
    # interface layer (previously only via tracer_peer_unreachable).
    sig = probe_rns_interface_down_peer_reachable(rnstatus_status=rns_status)
    if sig is not None:
        signals.append(sig)

    # Main-thread wedge — /proc read, root only. Now scans ALL task
    # threads (not just main) so worker-thread wedges (today's class)
    # surface too.
    for unit in services_wedge_check:
        sig = probe_main_thread_wedge(unit)
        if sig is not None:
            signals.append(sig)

    # User-scope RNS-using processes — root can't easily query
    # `systemctl --user`, so walk /proc and match by cmdline pattern.
    # Catches meshforge-echo.service and similar user-scope units that
    # would otherwise need DBUS env setup to query by name.
    signals.extend(probe_lxmf_process_wedge())

    # HTTP local probe — only if the map service is supposed to be
    # active. A bound-but-wedged port is the Issue #61 class.
    if "meshforge-map.service" in services_expected_active:
        sig = probe_http_local(
            "meshforge-map.service",
            port=http_port,
            path="/healthz",
        )
        if sig is not None:
            signals.append(sig)

        # FD-exhaustion probe (Issue #73) — proactive companion to the
        # http_local wedge probe above. Catches a leaking fd count
        # climbing toward the soft RLIMIT_NOFILE *before* it starves
        # accept() and wedges :5000. Read-only /proc walk, root only.
        sig = probe_fd_exhaustion("meshforge-map.service")
        if sig is not None:
            signals.append(sig)

    # Permission-foundation drift (mf.4/#73 perms class) — only meaningful on a
    # box that runs rnsd. Derives the rnsd user from its unit, so it's correct in
    # this root context; surfaces a re-provision that recreated /etc/reticulum
    # root:root while rnsd is non-root (the moc1/moc2/moc recurrence) before the
    # next logfile rotation wedges RNS.log(). Cheap: one sudo stat of the tree.
    if "rnsd.service" in services_expected_active:
        sig = probe_foundation_drift()
        if sig is not None:
            signals.append(sig)

        # RNS/LXMF fork-pin version drift — runs the version-check tool in the
        # rnsd user's env (root's may carry a different rns → false drift). Only
        # where rnsd is expected; a slow-changing pin, but cheap to confirm.
        sig = probe_rns_version_drift()
        if sig is not None:
            signals.append(sig)

    # MeshForge<->MeshAnchor parity drift — self-guards on /opt/meshanchor being
    # present (only the box holding both repos checks; MeshForge-only boxes no-op).
    # Maintenance-hygiene signal so a forgotten port surfaces in the mini rollup.
    sig = probe_parity_drift()
    if sig is not None:
        signals.append(sig)

    # Declared-role drift — this box's live unit state vs its effective
    # declaration (fleet_roles.yaml base role + deployment.json overrides),
    # via provision_role.py's own dry-run plan (the converge SSOT). Self-guards:
    # no declared role → None. Documented service_overrides are honored (the
    # moc2 lesson, 2026-06-03) — only undeclared divergence fires, debounced
    # 2 ticks to ride out fleet-roll windows.
    sig = probe_role_drift()
    if sig is not None:
        signals.append(sig)

    # Delivery write canary — reads gateway's self-reported health.
    sig = probe_delivery_write_canary(port=http_port)
    if sig is not None:
        signals.append(sig)

    # Tracer peer unreachable — reads tracer-<unix>.json files.
    # Returns 0..N signals depending on peer count.
    signals.extend(probe_tracer_peer_unreachable())

    return signals


# ─────────────────────────────────────────────────────────────────────
# Output writer
# ─────────────────────────────────────────────────────────────────────


def write_state(
    output_path: Path,
    *,
    host: str,
    now: float,
    probe_count: int,
    active_signals: List[Tuple[Signal, float]],
) -> None:
    """Write atomic-rename JSON. Never raises — best-effort.

    Schema:
        {
          "host": "moc1",
          "ts": <unix float>,
          "probe_count": <int>,
          "ok": <bool: any wedge-severity signal>,
          "signals": [
            { "class": "...", "subject": "...", "severity": "...",
              "detail": "...", "issue_ref": <int|null>,
              "first_seen": <unix float>, "extra": {...} },
            ...
          ]
        }
    """
    has_wedge = any(s.severity == "wedge" for s, _ in active_signals)
    payload = {
        "host": host,
        "ts": now,
        "probe_count": probe_count,
        "ok": not has_wedge,
        "signals": [signal_to_dict(s, first_seen_ts=fs) for s, fs in active_signals],
    }

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("watchdog: state dir create failed: %s", exc)
        return

    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
            fh.write("\n")
        # 0644 so map server (running as operator user) can read.
        os.chmod(tmp, 0o644)
        os.replace(tmp, output_path)
    except OSError as exc:
        logger.warning("watchdog: state write failed: %s", exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────
# Loop
# ─────────────────────────────────────────────────────────────────────


def _operator_home_for_root() -> Optional[Path]:
    """Resolve the operator's home dir when this daemon runs as root.

    systemd starts us with ``User=root`` and ``LOGNAME=root`` — neither
    ``SUDO_USER`` nor a non-root ``LOGNAME`` is set, so the standard
    ``get_real_user_home()`` falls back to ``/root``. The watchdog's
    config file lives in the operator's home (consistent with
    ``~/.config/meshforge/`` everywhere else), so we use the same
    UID-1000 / pwd-lookup pattern ``tracer_fires._operator_home`` uses.
    Returns None when no operator user can be resolved.
    """
    import os
    if os.geteuid() != 0:
        try:
            from utils.paths import get_real_user_home
            return get_real_user_home()
        except Exception:
            return None
    try:
        from utils.fleet_test_runner import _find_operator_user
    except ImportError:
        return None
    op = _find_operator_user()
    if op is None:
        return None
    op_uid, _ = op
    try:
        import pwd
        return Path(pwd.getpwuid(op_uid).pw_dir)
    except (KeyError, ImportError, OSError):
        return None


def _resolve_config_candidates(
    explicit_path: Optional[Path],
) -> List[Path]:
    """Build the ordered candidate list for the config file.

    Order:
      1. Explicit ``--config`` path (CLI override) — taken as-is.
      2. ``<operator_home>/.config/meshforge/watchdog.json`` — primary
         per-box location. Same dir as ``~/.config/meshforge/lab_peers``,
         settings.json, etc.
      3. ``/etc/meshforge/watchdog.json`` — system fallback for boxes
         where the operator home isn't resolvable (mirrors how
         ``_read_rns_instance_name`` falls back to ``/etc/reticulum/config``).
    """
    if explicit_path is not None:
        return [Path(str(explicit_path)).expanduser()]

    candidates: List[Path] = []
    op_home = _operator_home_for_root()
    if op_home is not None:
        candidates.append(op_home / ".config" / "meshforge" / "watchdog.json")
    candidates.append(Path("/etc/meshforge/watchdog.json"))
    return candidates


def load_config_file(
    config_path: Optional[Path] = None,
) -> Dict[str, object]:
    """Load per-box override config from operator home or /etc.

    Returns an empty dict on any read/parse failure — the watchdog must
    keep starting even if the override file is bad. Logs a WARNING so
    the operator sees the failure without burying it.

    Tries multiple candidate paths (see ``_resolve_config_candidates``).
    The first existing-and-parseable file wins; subsequent candidates
    are not consulted.
    """
    candidates = _resolve_config_candidates(config_path)
    last_attempted: Optional[Path] = None

    for path in candidates:
        last_attempted = path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue  # try next candidate
        except OSError as exc:
            logger.warning(
                "watchdog: config file %s unreadable: %s — trying next candidate",
                path, exc,
            )
            continue

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "watchdog: config file %s malformed: %s — falling back to defaults",
                path, exc,
            )
            return {}

        if not isinstance(data, dict):
            logger.warning(
                "watchdog: config file %s root is not a JSON object — "
                "falling back to defaults", path,
            )
            return {}
        logger.info("watchdog: loaded per-box overrides from %s", path)
        return data

    # No candidate existed — quiet (the file is optional).
    return {}


def resolve_probe_targets(
    config: Dict[str, object],
) -> Tuple[Tuple[str, ...], Tuple[str, ...], int]:
    """Layer config-file overrides on top of hardcoded defaults.

    Returns ``(services_expected_active, services_wedge_check, http_port)``.

    A list override is a *full replacement* of the default list. We
    deliberately don't do "add to default" or "subtract from default"
    semantics — the operator sees exactly what's probed by reading the
    file. Cuts ambiguity at the cost of slightly more typing.
    """
    services_expected = config.get("services_expected_active")
    if isinstance(services_expected, list) and all(
        isinstance(s, str) for s in services_expected
    ):
        sea: Tuple[str, ...] = tuple(services_expected)
    else:
        if services_expected is not None:
            logger.warning(
                "watchdog: services_expected_active override is not a list "
                "of strings — ignoring",
            )
        sea = _DEFAULT_SERVICES_EXPECTED_ACTIVE

    services_wedge = config.get("services_wedge_check")
    if isinstance(services_wedge, list) and all(
        isinstance(s, str) for s in services_wedge
    ):
        swc: Tuple[str, ...] = tuple(services_wedge)
    else:
        if services_wedge is not None:
            logger.warning(
                "watchdog: services_wedge_check override is not a list "
                "of strings — ignoring",
            )
        swc = _DEFAULT_SERVICES_WEDGE_CHECK

    port_raw = config.get("http_port")
    if isinstance(port_raw, int) and 1 <= port_raw <= 65535:
        port = port_raw
    else:
        if port_raw is not None:
            logger.warning(
                "watchdog: http_port override %r is not a valid port — "
                "ignoring", port_raw,
            )
        port = 5000
    return sea, swc, port


def _read_rns_instance_name() -> Optional[str]:
    """Best-effort lookup of this box's RNS instance_name.

    Reads ``~<operator>/.reticulum/config`` (the canonical fleet path).
    Returns None if not readable or not configured.
    """
    candidates: List[Path] = []
    try:
        from utils.paths import get_real_user_home
        candidates.append(get_real_user_home() / ".reticulum" / "config")
    except Exception:
        pass
    candidates.append(Path("/etc/reticulum/config"))

    for cfg_path in candidates:
        try:
            text = cfg_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("instance_name"):
                _, _, rhs = line.partition("=")
                name = rhs.strip()
                if name:
                    return name
    return None


def run_loop(
    *,
    output_path: Path,
    tick_s: float,
    stop_event: threading.Event,
    services_expected_active: Tuple[str, ...] = _DEFAULT_SERVICES_EXPECTED_ACTIVE,
    services_wedge_check: Tuple[str, ...] = _DEFAULT_SERVICES_WEDGE_CHECK,
    http_port: int = 5000,
    phase2_config: Optional[Phase2Config] = None,
) -> None:
    """Main probe loop. Returns on stop_event.

    ``phase2_config`` defaults to disabled — Phase 2 auto-restart is
    only invoked when the parsed config sets ``enabled=True``. The
    runner enforces this independently of the per-decision check in
    ``decide_restarts`` (belt-and-suspenders: two independent layers).
    """
    host = socket.gethostname().split(".")[0] or "unknown"
    tracker = SignalTracker()
    probe_count = 0
    rns_instance = _read_rns_instance_name()
    if rns_instance:
        logger.info("watchdog: rns instance_name resolved to %r", rns_instance)
    else:
        logger.info(
            "watchdog: no rns instance_name in config; namespace-collision "
            "probe disabled until config readable"
        )

    phase2_config = phase2_config or Phase2Config()
    restart_history = RestartHistory()
    if phase2_config.enabled:
        logger.warning(
            "watchdog: PHASE 2 auto-restart ENABLED dry_run=%s rules=%d",
            phase2_config.dry_run, len(phase2_config.rules),
        )
        for rule in phase2_config.rules:
            logger.info(
                "watchdog: phase2 rule %s/%s ticks=%d cooldown=%.0fs rate=%d/h",
                rule.signal_class, rule.service,
                rule.consecutive_ticks, rule.cooldown_s, rule.max_restarts_per_hour,
            )

    while not stop_event.is_set():
        now = time.time()
        probe_count += 1
        try:
            signals = run_all_probes(
                rns_instance_name=rns_instance,
                services_expected_active=services_expected_active,
                services_wedge_check=services_wedge_check,
                http_port=http_port,
            )
        except Exception as exc:
            # Never let a probe bug kill the watchdog. Log and continue.
            logger.error("watchdog: probe dispatch failed: %s", exc, exc_info=True)
            signals = []

        active_with_first_seen, newly_cleared = tracker.update(signals, now=now)

        # Log only edge transitions.
        for sig, fs_ts in active_with_first_seen:
            if fs_ts == now:
                # First-seen transition.
                level = logging.WARNING if sig.severity == "wedge" else logging.INFO
                logger.log(
                    level,
                    "watchdog: signal NEW class=%s subject=%s severity=%s detail=%s",
                    sig.cls, sig.subject, sig.severity, sig.detail,
                )
        for cls, subject in newly_cleared:
            logger.info(
                "watchdog: signal CLEARED class=%s subject=%s",
                cls, subject,
            )

        write_state(
            output_path,
            host=host,
            now=now,
            probe_count=probe_count,
            active_signals=active_with_first_seen,
        )

        # Phase 2 — auto-restart actions. Skipped entirely when
        # phase2_config.enabled is False (the default and only state
        # this codebase ships in as of 2026-05-21). The decision logic
        # also re-checks enabled internally; that's intentional belt-
        # and-suspenders.
        if phase2_config.enabled:
            decisions = decide_restarts(
                signals_with_first_seen=active_with_first_seen,
                config=phase2_config,
                history=restart_history,
                now=now,
                tick_s=tick_s,
            )
            for d in decisions:
                logger.warning(
                    "watchdog: PHASE 2 %s service=%s reason=%s",
                    "DRY-RUN" if d.dry_run else "RESTART",
                    d.service, d.reason,
                )
                if d.dry_run:
                    continue
                if execute_restart(d.service):
                    restart_history.record(d.service, now)
                    logger.info("watchdog: restart succeeded service=%s", d.service)
                else:
                    logger.error("watchdog: restart FAILED service=%s", d.service)

        stop_event.wait(tick_s)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MeshForge per-box watchdog daemon")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_PATH,
        help=f"Output JSON path (default {DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--tick", type=float, default=DEFAULT_TICK_S,
        help=f"Seconds between probe ticks (default {DEFAULT_TICK_S})",
    )
    parser.add_argument(
        "--http-port", type=int, default=None,
        help=(
            "Local map server HTTP port. Defaults to 5000 unless overridden "
            "by the config file's http_port field. CLI takes precedence over "
            "config."
        ),
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help=(
            f"Per-box override file (JSON). Defaults to "
            f"{DEFAULT_CONFIG_FILE} resolved against the operator home. "
            f"Schema: services_expected_active (list[str]), "
            f"services_wedge_check (list[str]), http_port (int). "
            f"Missing file = pure defaults; malformed file logs WARNING "
            f"and falls back."
        ),
    )
    parser.add_argument(
        "--loglevel", default="INFO",
        help="Python logging level (default INFO)",
    )
    parser.add_argument(
        "--one-shot", action="store_true",
        help="Run probes once, write state, exit. For ops debugging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.loglevel.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    stop_event = threading.Event()

    def _on_signal(signum, _frame):
        logger.info("watchdog: received signal %d, stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # Resolve probe targets: hardcoded defaults → config file → CLI args.
    config = load_config_file(args.config)
    services_expected, services_wedge, config_port = resolve_probe_targets(config)
    # CLI --http-port wins over config file when explicitly set.
    http_port = args.http_port if args.http_port is not None else config_port
    phase2_config = parse_phase2_config(config)

    if args.one_shot:
        host = socket.gethostname().split(".")[0] or "unknown"
        tracker = SignalTracker()
        now = time.time()
        rns_instance = _read_rns_instance_name()
        signals = run_all_probes(
            rns_instance_name=rns_instance,
            services_expected_active=services_expected,
            services_wedge_check=services_wedge,
            http_port=http_port,
        )
        active_with_first_seen, _ = tracker.update(signals, now=now)
        write_state(
            args.output, host=host, now=now, probe_count=1,
            active_signals=active_with_first_seen,
        )
        for sig, _ in active_with_first_seen:
            print(
                f"  [{sig.severity}] {sig.cls} subject={sig.subject} "
                f"detail={sig.detail}"
            )
        if not active_with_first_seen:
            print("  (no signals — system looks healthy)")
        return 0

    try:
        run_loop(
            output_path=args.output,
            tick_s=args.tick,
            stop_event=stop_event,
            services_expected_active=services_expected,
            services_wedge_check=services_wedge,
            http_port=http_port,
            phase2_config=phase2_config,
        )
    except Exception as exc:
        logger.error("watchdog: loop crashed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
