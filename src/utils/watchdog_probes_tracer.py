"""Watchdog probe — tracer peer unreachable (split from
``watchdog_probes_service`` 2026-07-19 under the MF025 1,500-line cap;
same precedent as the drift/liveness/env split). Import via the
``utils.watchdog_probes`` hub, not from here.

Reads recent ``tracer-<unix>.json`` fire files and classifies peers with
recurring no-route/timeout into transient vs persistent (absent /
unresponsive tiers) — every observable condition is REMOTE, so severity
never exceeds degraded (the 2026-07-01 moc2-offline lesson).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional

from utils.watchdog_probe_core import Signal, note_disposition


def probe_tracer_peer_unreachable(
    *,
    tracer_dir: Optional[Path] = None,
    persistent_cycles: int = 3,
    lookback_s: float = 1800.0,
    now: Optional[float] = None,
) -> List[Signal]:
    """Classify peers with recurring no-route/timeout into transient vs persistent.

    Reads recent ``tracer-<unix>.json`` files from ``tracer_dir`` (or
    ``~/.local/state/meshforge/tracer`` per the lab tooling convention),
    walks the last ``lookback_s`` seconds of fires, groups results by
    peer, and emits one Signal per peer whose recent fires are dominated
    by no-route/timeout.

    Tier-1 (transient): peer failed in the most recent fire but had
    at least one success in the lookback window. Severity: info.

    Tier-2 (persistent): peer failed in the last ``persistent_cycles``
    consecutive fires with no successes between. Severity is **degraded**
    (2026-07-01, the moc2-offline lesson): every condition this probe can
    observe is a REMOTE one — nothing on THIS observer is wedged and no
    observer-side action fixes it — so one deliberately powered-off box
    must not fail the fleet gate as N phantom wedges on its healthy
    peers. The failure KIND still classifies the tier for the operator:

    - all leading failures ``no-route`` → tier ``absent``: the peer has
      no RNS path at all — off the mesh (maintenance, power, crash).
      Check the box.
    - any ``timeout`` among them → tier ``unresponsive``: an RNS path
      exists but nothing answers. AMBIGUOUS by construction (detectors
      never map ambiguous → definitive): either the box is off while
      observers still hold a stale path (live 07-01: moc1/moc5 read the
      powered-off moc2 as timeout while moc/moc3 read no-route), or the
      box is up with a wedged echo / evicted identity (the 06-29 class —
      recipe: restart the peer's meshforge-echo / re-announce).

    A crashed box still surfaces (degraded on every peer + unreachable in
    fleet rollups) — unobservable is never silently healthy — just at the
    honest severity (honest_status: WARN, not FAIL).

    Why a list return: one tracer_dir can report on many peers in one
    pass; flattening to N Signals keeps the probe API uniform.
    """
    if tracer_dir is None:
        tracer_dir = _default_tracer_dir()
    if tracer_dir is None or not tracer_dir.is_dir():
        note_disposition(
            "tracer_peer_unreachable", "inert",
            reason="no tracer state dir; tracer not run on this box",
        )
        return []

    if now is None:
        now = time.time()
    since_unix = now - lookback_s

    fires = _load_recent_fires(tracer_dir, since_unix=since_unix)
    if not fires:
        note_disposition(
            "tracer_peer_unreachable", "indeterminate",
            reason="no parseable tracer fires within lookback window",
        )
        return []

    # Fires sorted newest-first inside _load_recent_fires.
    # Group results by peer: list of (fire_at_unix, result) newest-first.
    by_peer: dict = {}
    skipped_rows = 0
    for fire in fires:
        for r in fire.get("results", []):
            if not isinstance(r, dict):
                skipped_rows += 1
                continue
            peer = r.get("peer")
            if not isinstance(peer, str) or not peer:
                skipped_rows += 1
                continue
            by_peer.setdefault(peer, []).append(
                (fire["fire_at_unix"], r.get("result"))
            )

    if not by_peer and skipped_rows:
        # Fires were present but EVERY result row was unparseable — no peer
        # was actually observed. The final not-signals note would otherwise
        # claim all peers clean (honest_failure_modes #1: a degraded input
        # must never read as a healthy observation).
        note_disposition(
            "tracer_peer_unreachable", "indeterminate",
            reason="tracer result rows unparseable — peers unobservable",
        )
        return []

    signals: List[Signal] = []
    for peer, history in by_peer.items():
        # history is newest-first thanks to fires being newest-first.
        if not history:
            continue
        latest_result = history[0][1]
        if latest_result == "ok":
            continue  # peer reachable right now → nothing to report

        # Count leading non-ok results.
        leading_fail = 0
        for _, result in history:
            if result == "ok":
                break
            leading_fail += 1

        if leading_fail >= persistent_cycles:
            leading_kinds = {result for _, result in history[:leading_fail]}
            if leading_kinds == {"no-route"}:
                # Peer ABSENT from the RNS mesh — no path at all. An
                # offline/maintenance box, not an RNS wedge on this
                # observer (the moc2-offline lesson, 2026-07-01).
                signals.append(Signal(
                    cls="tracer_peer_unreachable",
                    subject=peer,
                    severity="degraded",
                    detail=(
                        f"{leading_fail} consecutive no-route tracer fires "
                        f"— {peer} is ABSENT from the RNS mesh (no path). "
                        f"Off-line/maintenance or crashed: check the box "
                        f"itself; nothing on this observer is wedged and "
                        f"no observer-side action fixes it."
                    ),
                    extra={
                        "leading_fail": leading_fail,
                        "latest_result": latest_result,
                        "tier": "absent",
                    },
                ))
                continue
            # Timeout(s) among the leading failures: an RNS path exists
            # but nothing answers. Ambiguous — off box behind a stale
            # path, OR a live box with a wedged echo / evicted identity
            # (06-29 class). A remote condition either way: degraded,
            # never a wedge on this observer.
            signals.append(Signal(
                cls="tracer_peer_unreachable",
                subject=peer,
                severity="degraded",
                detail=(
                    f"{leading_fail} consecutive failed tracer fires to "
                    f"{peer} ({latest_result!r} latest, "
                    f"kinds={sorted(leading_kinds)}) with an RNS path "
                    f"present — the box is UNRESPONSIVE: either off-line "
                    f"behind a stale path, or up with a wedged echo / "
                    f"evicted identity. Check the box; if it is up, "
                    f"restart its meshforge-echo / re-announce (06-29 "
                    f"recipe)."
                ),
                extra={
                    "leading_fail": leading_fail,
                    "latest_result": latest_result,
                    "tier": "unresponsive",
                },
            ))
        else:
            # Has at least one ok in the lookback window OR not enough
            # leading fails for tier-2. Either way, transient.
            signals.append(Signal(
                cls="tracer_peer_unreachable",
                subject=peer,
                severity="info",
                detail=(
                    f"{leading_fail} recent failed fire(s) to {peer} "
                    f"({latest_result!r} latest). Transient — cold-start "
                    f"or single blip; resolves with retries."
                ),
                extra={
                    "leading_fail": leading_fail,
                    "latest_result": latest_result,
                    "tier": "transient",
                },
            ))
    if not signals:
        note_disposition("tracer_peer_unreachable", "clean")
    return signals


def _default_tracer_dir() -> Optional[Path]:
    """Resolve the tracer state dir per the same XDG/operator-home rules
    as ``utils/tracer_fires.py::_tracer_dir``. Kept local to avoid
    pulling in fleet_test_runner during watchdog startup."""
    import os
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "meshforge" / "tracer"
    # Watchdog runs as root. Fall back to the operator's home via the
    # same lookup tracer_fires uses.
    try:
        from utils.tracer_fires import _operator_home  # noqa: WPS433
    except ImportError:
        return None
    home = _operator_home()
    if home is None:
        return None
    return home / ".local" / "state" / "meshforge" / "tracer"


def _load_recent_fires(
    tracer_dir: Path, *, since_unix: float,
) -> List[dict]:
    """Read all tracer-<unix>.json files in ``tracer_dir`` newer than
    ``since_unix``. Returns newest-first. Skips malformed files."""
    fires: List[dict] = []
    try:
        entries = list(tracer_dir.iterdir())
    except OSError:
        return []
    for fp in entries:
        name = fp.name
        if not name.startswith("tracer-") or not name.endswith(".json"):
            continue
        try:
            file_unix = float(name[len("tracer-"):-len(".json")])
        except (IndexError, ValueError):
            file_unix = None
        if file_unix is not None and file_unix < since_unix - 30:
            continue
        try:
            with fp.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        fire_at = data.get("fire_at_unix")
        if not isinstance(fire_at, (int, float)) or fire_at < since_unix:
            continue
        if not isinstance(data.get("results"), list):
            continue
        fires.append(data)
    fires.sort(key=lambda d: d["fire_at_unix"], reverse=True)
    return fires

