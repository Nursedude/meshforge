"""Cascade pre-failure fingerprints catalog (Track 0C of the
federation→DB pressure→wedge stability arc).

The recurring class across 5+ documented incidents is that subsystems
silently degrade WITHOUT systemd noticing — threads stuck in `D` state
report ``active (running)``, ``unix_wait_for_peer`` hangs forever,
``Type=oneshot`` services sit "activating start" indefinitely. The
operator catches these via traffic flows (tracer rollup fail%, slow
``/api/status``) rather than process state.

This module catalogs **machine-checkable pre-failure shapes** so the
cascade detector (``utils.cascade_detector``) can surface degraded-
but-not-dead state on ``/fleet/cascade`` before it cascades.

Each ``Fingerprint`` carries:
  * ``probe``: a read-only callable returning a ``ProbeHit`` on match,
    None on miss. Probes must never raise (catch broadly + return None).
  * ``cadence_s``: minimum seconds between fires for this fingerprint
    (the detector's outer loop is 30 s; this gates per-probe).
  * ``incident_refs``: memory entries the fingerprint maps to.
  * ``coupled_to``: what cascades next when this fires — used in the
    explanation block of the endpoint payload so operators see the
    *consequence*, not just the symptom.

See plan: ``~/.claude/plans/we-have-a-cycle-jolly-wadler.md`` Track 0C
and Track 3 for the full catalog roadmap.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Test-only escape hatch — pytest conftest sets this so the daemon
# detector thread (if ever started from inside a test) cannot leak
# `subprocess.run(...)` calls into a sibling test's globally-patched
# subprocess.run mock. CI red 79f5d7b series + see
# `project_ci_red_track0_followup.md` for the leak shape. Tests that
# explicitly exercise probe_rns_rpc_wedge unset this env var via
# `monkeypatch.delenv`.
_PROBE_DISABLED_ENV = "MESHFORGE_CASCADE_PROBE_DISABLED"


def _probes_disabled() -> bool:
    return bool(os.environ.get(_PROBE_DISABLED_ENV))


@dataclass(frozen=True)
class ProbeHit:
    """Returned by a fingerprint probe when the pre-failure shape matches."""
    evidence: str                           # short human-readable summary
    metric: dict = field(default_factory=dict)  # structured numbers


@dataclass(frozen=True)
class Fingerprint:
    """A pre-failure shape with a probe that returns ProbeHit on match."""
    name: str                               # stable id, e.g. "rns_rpc_wedge"
    severity: str                           # "degraded" | "pre_fail" | "wedged"
    probe: Callable[[], Optional[ProbeHit]]
    cadence_s: int                          # min seconds between this probe's fires
    incident_refs: Tuple[str, ...]          # memory entries this maps to
    coupled_to: Tuple[str, ...]             # what cascades next when this fires


# ── Fingerprint 1: rns_rpc_wedge ──────────────────────────────────────────


# A wedged client sits in `unix_wait_for_peer` until rnsd is restarted.
# A HEALTHY client passes through SYN-SENT in microseconds — and rnsd
# listens with a backlog of 0 (`ss -xnpl` shows `Send-Q 0` on the
# `@rns/*/rpc` LISTEN row on every fleet box), so any connect that
# arrives while another is being accepted legitimately queues there.
# A single sample therefore cannot tell "wedged" from "busy": we must
# re-sample and require the SAME socket to still be waiting.
#
# 0.4s is ~400x longer than a healthy connect and ~0.001x a real wedge,
# so it separates the two cleanly while keeping the probe well inside
# its 30s cadence. Only paid on the candidate path (a first sample that
# already matched), never on a healthy box.
_RPC_WEDGE_RESAMPLE_DELAY_S = 0.4


def _ss_syn_sent_rns_rpc(ss_timeout: float = 2.0) -> Optional[List[str]]:
    """One ``ss -xH state syn-sent`` sample, filtered to rnsd RPC sockets.

    Returns None when the table is UNOBSERVABLE (``ss`` timed out or
    exited non-zero) and ``[]`` when it was read and nothing matched.
    The caller must not collapse the two into "healthy" silently — we
    log the blind case so a swallow leaves a witness
    (honest_failure_modes #2 and #9).
    """
    try:
        result = subprocess.run(
            ["ss", "-xH", "state", "syn-sent"],
            capture_output=True, text=True, timeout=ss_timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("rns_rpc_wedge: ss sample unobservable (%s)", exc)
        return None
    if result.returncode != 0:
        logger.debug("rns_rpc_wedge: ss exited %d", result.returncode)
        return None
    return [
        ln.strip() for ln in result.stdout.splitlines()
        if "@rns/" in ln and "/rpc" in ln
    ]


def _socket_key(line: str) -> str:
    """Stable identity for one ``ss`` row, ignoring volatile queue depths.

    ``ss -xH`` columns are: netid state recv-q send-q local_addr
    local_inode peer_addr peer_inode. The inode pair uniquely identifies
    a socket for as long as it exists, so it is what makes "the same
    socket is STILL waiting" a decidable question. recv-q/send-q are
    dropped because they can move under load on a socket that never
    changed identity. A row we cannot parse falls back to the whole line
    — conservative: it can still match itself across samples, so an
    unexpected ``ss`` format degrades to the old behavior rather than to
    a silent miss.
    """
    fields = line.split()
    if len(fields) >= 8:
        return " ".join(fields[:2] + fields[4:])
    return line


def probe_rns_rpc_wedge() -> Optional[ProbeHit]:
    """Detect rnsd's @rns/*/rpc abstract Unix-socket listener stalling.

    The fingerprint (per ``project_rnsd_rpc_listener_wedge.md``): when
    rnsd's RPC listener wedges, new ``RNS.Reticulum()`` clients hang in
    ``unix_wait_for_peer`` on ``connect()`` — visible in ``ss`` as one or
    more peers stuck in ``SYN-SENT`` against the abstract socket name.

    Probe (no sudo needed): ``ss -xH state syn-sent`` lists all SYN-SENT
    Unix sockets system-wide; we filter for ``@rns/*/rpc``. **A match is
    only a CANDIDATE.** We re-sample after
    ``_RPC_WEDGE_RESAMPLE_DELAY_S`` and report only sockets present in
    BOTH samples, because a socket still waiting 0.4s later is wedged
    while one that cleared was just queued behind rnsd's zero-length
    accept backlog.

    Why the re-sample exists (measured 2026-09-08, moc). The
    single-sample form fired at :07:51-:08:34 past the hour and at no
    other time in 7 days, 50-72s after the ``7 * * * *`` ``kilo matrix``
    cron started on a 4-core Pi::

        13:07:01 CRON kilo matrix  -> 13:08:13 suspected -> 13:08:43 cleared
        14:07:01 CRON kilo matrix  -> 14:07:51 suspected -> 14:08:21 cleared
        15:07:01 CRON kilo matrix  -> 15:07:58 suspected -> 15:08:28 cleared

    Meanwhile the rnstatus-based ``rns_rpc_unresponsive`` probe — the
    direct RPC round-trip test, the authority on this exact claim —
    fired ZERO times on that box over the same 7 days. Two detectors of
    one claim disagreed and the more direct one said clean: the SYN-SENT
    count was measuring CPU contention, not rnsd.

    Returns ``None`` if ``ss`` isn't installed, if a sample is
    unobservable, if nothing matches, or if no matched socket survived
    the re-sample.

    Also returns ``None`` immediately when the test-only escape-hatch
    env var ``MESHFORGE_CASCADE_PROBE_DISABLED`` is set — prevents the
    probe's ``subprocess.run`` call from leaking into unrelated tests'
    globally-patched ``subprocess.run`` mocks (CI red 79f5d7b series).
    """
    if _probes_disabled():
        return None
    if shutil.which("ss") is None:
        return None

    first = _ss_syn_sent_rns_rpc()
    if not first:
        return None

    time.sleep(_RPC_WEDGE_RESAMPLE_DELAY_S)

    second = _ss_syn_sent_rns_rpc()
    if second is None:
        # We HAD a candidate and then went blind on the confirming read.
        # "Socket cleared" (healthy) and "ss failed" (unobservable) must
        # not collapse into one silent None — this is the only path that
        # could suppress a genuine wedge, so it gets a loud witness even
        # though the fingerprint has no indeterminate state to report
        # (honest_failure_modes #2 and #9). Rare by construction: it
        # needs a matching first sample.
        logger.warning(
            "rns_rpc_wedge: %d SYN-SENT candidate(s) seen but the "
            "confirming ss sample was UNOBSERVABLE — cannot tell a wedge "
            "from transient connect queueing this tick",
            len(first),
        )
        return None

    first_keys = {_socket_key(ln) for ln in first}
    persisted = [ln for ln in second if _socket_key(ln) in first_keys]
    if not persisted:
        logger.debug(
            "rns_rpc_wedge: %d SYN-SENT candidate(s) cleared within %.1fs "
            "— transient connect queueing, not a wedge",
            len(first), _RPC_WEDGE_RESAMPLE_DELAY_S,
        )
        return None

    return ProbeHit(
        evidence=(
            f"{len(persisted)} SYN-SENT connect to rnsd RPC socket still "
            f"waiting {_RPC_WEDGE_RESAMPLE_DELAY_S:.1f}s later — listener "
            "appears wedged in unix_wait_for_peer"
        ),
        metric={
            "syn_sent_count": len(persisted),
            "candidate_count": len(first),
            "resample_delay_s": _RPC_WEDGE_RESAMPLE_DELAY_S,
            "sample_line": persisted[0][:200],
        },
    )


# ── Fingerprint 2: tracer_stale_fire ──────────────────────────────────────

# Default: 25 min. tracer.timer fires every 10 min, so 2× = 20 min would
# be the minimum; +5 min slack absorbs jitter from the timer's
# RandomizedDelaySec and the ~30-60s tracer run itself. Override via
# env var so an operator can tighten/loosen without code change.
_DEFAULT_TRACER_STALE_THRESHOLD_S = 1500
_TRACER_STALE_THRESHOLD_ENV = "MESHFORGE_CASCADE_TRACER_STALE_S"


def _tracer_stale_threshold_s() -> int:
    """Resolve threshold each probe — env can change without restart."""
    raw = os.environ.get(_TRACER_STALE_THRESHOLD_ENV)
    if raw is None:
        return _DEFAULT_TRACER_STALE_THRESHOLD_S
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_TRACER_STALE_THRESHOLD_S
    return value if value > 0 else _DEFAULT_TRACER_STALE_THRESHOLD_S


def _tracer_state_dir() -> Path:
    """`$XDG_STATE_HOME/meshforge/tracer` (matches `lab.lxmf_tracer`).

    Pulled out so tests can monkeypatch the env var. We don't import
    `lab.lxmf_tracer._state_dir_default` directly because the cascade
    detector lives in `utils/` and we'd rather not pull `lab/` into the
    import graph of the map service.
    """
    from utils.paths import get_real_user_home

    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else get_real_user_home() / ".local" / "state"
    return base / "meshforge" / "tracer"


def probe_tracer_stale_fire() -> Optional[ProbeHit]:
    """Detect when the local tracer timer has stopped firing JSON files.

    Symptom shape (per `project_rnsd_rpc_listener_wedge.md` open follow-up
    #2): once rnsd's RPC listener wedges, the user-unit `meshforge-tracer`
    oneshot hangs in `activating start` indefinitely. Future timer fires
    are blocked, but systemd reports the timer itself as `active waiting`.
    The only operator-visible signal today is the cross-fleet rollup
    showing 100 % timeout in src→<this-host> rows ~2.5 h later — long
    after the wedge began.

    Probe: stat the newest `tracer-*.json` in
    `$XDG_STATE_HOME/meshforge/tracer/` (or `~/.local/state/meshforge/
    tracer/`). When its mtime is older than 2× the timer interval (+slack)
    we surface a `pre_fail` so `/fleet/cascade` flips while the operator
    still has the chance to restart rnsd before the rollup window
    accumulates failure samples.

    Miss conditions (intentional — must not false-alarm boxes where the
    tracer isn't installed or hasn't run yet):
        * State dir does not exist (tracer profile not installed)
        * State dir empty (tracer installed but hasn't fired once yet —
          we have no baseline to compare against)
        * Newest file's mtime is within threshold (healthy)

    Honors `MESHFORGE_CASCADE_PROBE_DISABLED` for the same test-isolation
    reason as `probe_rns_rpc_wedge`.
    """
    if _probes_disabled():
        return None
    try:
        sd = _tracer_state_dir()
    except Exception:
        return None
    try:
        if not sd.is_dir():
            return None
        newest_mtime: Optional[float] = None
        newest_name: Optional[str] = None
        for entry in sd.iterdir():
            if not entry.name.startswith("tracer-") or not entry.name.endswith(".json"):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if newest_mtime is None or mtime > newest_mtime:
                newest_mtime = mtime
                newest_name = entry.name
    except OSError:
        return None
    if newest_mtime is None:
        return None

    threshold = _tracer_stale_threshold_s()
    age = time.time() - newest_mtime
    if age < threshold:
        return None
    return ProbeHit(
        evidence=(
            f"newest tracer fire is {int(age)}s old (threshold {threshold}s) — "
            "tracer timer or rnsd RPC listener likely wedged"
        ),
        metric={
            "age_s": int(age),
            "threshold_s": threshold,
            "newest_file": newest_name,
        },
    )


# ── Fingerprint 3: tcp_4403_contention ────────────────────────────────────

# Regex extracting (pid, comm) from `ss -tnp` users:((...)) field.
# Example line tail: `users:(("python3",pid=12345,fd=8))`
_SS_PID_COMM_RE = re.compile(
    r'users:\(\(\s*"([^"]+)"\s*,\s*pid=(\d+)'
)


def _read_proc_cmdline(pid: int) -> str:
    """Return /proc/{pid}/cmdline as a space-joined string, '' on error.

    Used to distinguish meshforge-map's python from meshforge-gateway's
    python when both are otherwise visible only as ``comm=python3``.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
    except (OSError, FileNotFoundError):
        return ""
    # cmdline is NUL-separated; trailing NUL is normal.
    return raw.decode("utf-8", errors="replace").replace("\x00", " ").strip()


def _resolve_meshforge_map_main_pid() -> Optional[int]:
    """``systemctl show -p MainPID --value meshforge-map.service``.

    Returns the int pid when systemd reports a live MainPID (>1).
    Returns None on subprocess failure, when systemctl is missing,
    or when MainPID is 0/1 (service inactive). Mirrors the helper
    pattern in ``utils.watchdog_probes._resolve_main_pid``; kept
    local here so the cascade layer doesn't import the watchdog
    layer.
    """
    if shutil.which("systemctl") is None:
        return None
    try:
        proc = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value",
             "meshforge-map.service"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        pid = int(proc.stdout.strip())
    except (ValueError, TypeError):
        return None
    return pid if pid > 1 else None


def probe_tcp_4403_contention() -> Optional[ProbeHit]:
    """Detect a stale/foreign python process holding :4403 ESTABLISHED.

    Per Issue #53 in persistent_issues.md: when meshforge-map.service
    goes stale across a fleet-sync (Phase-2 migration left old
    daemons running on pre-fix code), the stale daemon holds a python
    socket to 127.0.0.1:4403 (meshtasticd's TCP API port).

    The legitimate steady-state shape on a fully-stacked box is:
    one ``utils.map_data_service`` python process — the current
    ``meshforge-map.service`` MainPID — holds a single client
    connection to local meshtasticd's :4403. The probe distinguishes
    that healthy shape from the Issue #53 stale-daemon shape by
    checking whether the map_data_service pid on :4403 matches
    systemd's MainPID for ``meshforge-map.service``.

    Probe (no sudo needed for own-user processes): ``ss -tnpH state
    established`` lists all established TCP connections with
    ``users:(("comm",pid=N,fd=K))`` annotation. We grep for
    127.0.0.1:4403, extract distinct (pid, comm) tuples, and surface a
    ProbeHit when:
      * 2+ distinct python pids are on :4403 (contention), OR
      * exactly 1 ``utils.map_data_service`` pid is on :4403 AND it is
        NOT systemd's MainPID for ``meshforge-map.service`` (orphan /
        stale signature). Includes the MainPID==0 case (service
        stopped, dangling pid still holding the socket).

    Miss conditions:
      * ``ss`` not installed
      * No :4403 connections (no gateway, benign)
      * Exactly one python pid AND its cmdline is NOT map_data_service
        (steady state — assumed to be the gateway)
      * Exactly one ``utils.map_data_service`` pid AND it equals
        systemd's MainPID for ``meshforge-map.service`` (steady-state
        client connect to local meshtasticd — was a long-standing
        false positive on every box running both services together)

    Honors ``MESHFORGE_CASCADE_PROBE_DISABLED``.
    """
    if _probes_disabled():
        return None
    if shutil.which("ss") is None:
        return None
    try:
        result = subprocess.run(
            ["ss", "-tnpH", "state", "established"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None

    pid_to_comm: dict = {}
    for line in result.stdout.splitlines():
        if ":4403" not in line:
            continue
        match = _SS_PID_COMM_RE.search(line)
        if not match:
            continue
        comm = match.group(1)
        try:
            pid = int(match.group(2))
        except ValueError:
            continue
        # Dedup: ss can list the same pid multiple times across fds.
        pid_to_comm.setdefault(pid, comm)

    if not pid_to_comm:
        return None

    # Annotate each pid with cmdline so we can detect the stale-daemon
    # signature even when it's the only consumer on the box.
    annotated = [
        (pid, comm, _read_proc_cmdline(pid))
        for pid, comm in pid_to_comm.items()
    ]
    map_service_pids = [
        (pid, comm) for pid, comm, cmd in annotated
        if "utils.map_data_service" in cmd
    ]

    if len(pid_to_comm) >= 2:
        sample = next(iter(pid_to_comm.items()))
        return ProbeHit(
            evidence=(
                f"{len(pid_to_comm)} distinct processes on 127.0.0.1:4403 — "
                "meshtasticd TCP API contention (only the gateway should "
                "consume :4403 locally)"
            ),
            metric={
                "pid_count": len(pid_to_comm),
                "pids": sorted(pid_to_comm.keys()),
                "sample_comm": sample[1],
                "map_service_pids": [pid for pid, _ in map_service_pids],
            },
        )

    if map_service_pids:
        pid, comm = map_service_pids[0]
        # Steady state: map_data_service legitimately holds a client
        # connection to local meshtasticd. The Issue #53 stale-daemon
        # shape is specifically an OLD pid that survived a restart —
        # i.e., a map_data_service pid that does NOT match the current
        # systemd MainPID. If MainPID matches → healthy, skip. If
        # systemctl is unavailable, we can't tell — preserve the
        # conservative original behavior and fire (better to surface a
        # false positive in that one edge than miss a real stale on a
        # box with broken systemctl).
        main_pid = _resolve_meshforge_map_main_pid()
        if main_pid is not None and main_pid == pid:
            return None
        return ProbeHit(
            evidence=(
                "meshforge-map.service stale-daemon signature on "
                "127.0.0.1:4403 — utils.map_data_service pid "
                f"{pid} does not match systemd MainPID "
                f"({main_pid if main_pid is not None else 'unresolved'}); "
                "restart meshforge-map.service to release the contention "
                "(Issue #53)"
            ),
            metric={
                "pid_count": 1,
                "pids": [pid],
                "sample_comm": comm,
                "map_service_pids": [pid],
                "systemd_main_pid": main_pid,
            },
        )

    return None


# ── Catalog ───────────────────────────────────────────────────────────────


FINGERPRINTS: List[Fingerprint] = [
    Fingerprint(
        name="rns_rpc_wedge",
        severity="pre_fail",
        probe=probe_rns_rpc_wedge,
        cadence_s=30,
        incident_refs=("project_rnsd_rpc_listener_wedge",),
        coupled_to=(
            "next lab tracer / echo fire wedges in RNS.Reticulum() init "
            "or LXMRouter.handle_outbound(); fleet rollup fail% spikes "
            "within one timer interval (~10 min)",
        ),
    ),
    Fingerprint(
        name="tracer_stale_fire",
        severity="pre_fail",
        probe=probe_tracer_stale_fire,
        cadence_s=60,
        incident_refs=("project_rnsd_rpc_listener_wedge",),
        coupled_to=(
            "cross-fleet tracer rollup row for this host begins accumulating "
            "100 % timeouts; downstream fingerprints (rns_rpc_wedge) may "
            "also fire, but this one trips first because it watches "
            "consequence (no fires) rather than cause (SYN-SENT socks)",
        ),
    ),
    Fingerprint(
        name="tcp_4403_contention",
        severity="pre_fail",
        probe=probe_tcp_4403_contention,
        cadence_s=60,
        incident_refs=("project_meshforge_map_stale_daemon_pattern",),
        coupled_to=(
            "/api/v1/fromradio returns size=0; R→M sends silently starve; "
            "gateway delivery counters show sent rising without confirmed; "
            "ECONNREFUSED bursts in meshforge-gateway journal",
        ),
    ),
    # Future fingerprints (Track 3): wal_oversize, oneshot_activating.
]


def get_fingerprint_by_name(name: str) -> Optional[Fingerprint]:
    """Lookup helper for tests and the detector loop."""
    for fp in FINGERPRINTS:
        if fp.name == name:
            return fp
    return None
