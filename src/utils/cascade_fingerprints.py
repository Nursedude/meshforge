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
            "within one timer interval (~10 min)"
        ),
    ),
    # Future fingerprints (Track 3): wal_oversize, tracer_timer_dead,
    # tcp_4403_contention, oneshot_activating. See plan file.
]


def get_fingerprint_by_name(name: str) -> Optional[Fingerprint]:
    """Lookup helper for tests and the detector loop."""
    for fp in FINGERPRINTS:
        if fp.name == name:
            return fp
    return None
