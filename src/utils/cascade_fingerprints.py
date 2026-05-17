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


def probe_rns_rpc_wedge() -> Optional[ProbeHit]:
    """Detect rnsd's @rns/*/rpc abstract Unix-socket listener stalling.

    The fingerprint (per ``project_rnsd_rpc_listener_wedge.md``): when
    rnsd's RPC listener wedges, new ``RNS.Reticulum()`` clients hang in
    ``unix_wait_for_peer`` on ``connect()`` — visible in ``ss`` as one or
    more peers stuck in ``SYN-SENT`` against the abstract socket name.

    Probe (no sudo needed): ``ss -xH state syn-sent`` lists all
    SYN-SENT Unix sockets system-wide. We grep for ``@rns/`` in the
    output — any match is a candidate wedge.

    Returns ``None`` if ``ss`` isn't installed, if the command times
    out, or if no matching lines are found.

    Also returns ``None`` immediately when the test-only escape-hatch
    env var ``MESHFORGE_CASCADE_PROBE_DISABLED`` is set — prevents the
    probe's ``subprocess.run`` call from leaking into unrelated tests'
    globally-patched ``subprocess.run`` mocks (CI red 79f5d7b series).
    """
    if _probes_disabled():
        return None
    if shutil.which("ss") is None:
        return None
    try:
        result = subprocess.run(
            ["ss", "-xH", "state", "syn-sent"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    lines = [
        ln.strip() for ln in result.stdout.splitlines()
        if "@rns/" in ln and "/rpc" in ln
    ]
    if not lines:
        return None
    return ProbeHit(
        evidence=(
            f"{len(lines)} SYN-SENT connect to rnsd RPC socket — listener "
            "appears wedged in unix_wait_for_peer"
        ),
        metric={"syn_sent_count": len(lines), "sample_line": lines[0][:200]},
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
    # Future fingerprints (Track 3): wal_oversize, tcp_4403_contention,
    # oneshot_activating. See plan file.
]


def get_fingerprint_by_name(name: str) -> Optional[Fingerprint]:
    """Lookup helper for tests and the detector loop."""
    for fp in FINGERPRINTS:
        if fp.name == name:
            return fp
    return None
