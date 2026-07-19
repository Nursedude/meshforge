"""Honest fleet-truth SSOT — the tri-state data layer under the /fleet NOC.

The MeshForge fleet monitor has two consumers with different needs: a HUMAN who
needs an at-a-glance visual, and an incoming Claude session that needs the truth
in data — re-derivable, and honest about what it cannot see. This module is that
truth layer. The visual (`web/fleet.html`) is a faithful projection of it; both
read the SAME bytes so they can never disagree.

The one invariant that makes the NOC honest: **every leaf is a tri-state cell**
``{state, reason, ...}`` — never a bare bool — where ``state`` is one of:

    healthy  — observed, and observed OK (a fresh positive observation)
    failed   — observed, and observed BAD
    dark     — UNOBSERVABLE: source absent, not-installed, stale, unreachable,
               or a subsystem this box structurally doesn't run

``classify_cell`` is **default-dark**: there is no code path where a missing,
stale, or ``None`` source yields ``healthy``. "No data" can never read green
(honest_failure_modes #1/#2: unobservable != healthy != resolved). Because a cell
is an object and never a bool, a downstream renderer has nothing to truthy-coerce.

Pure + stdlib-only (mirrors ``fleet_snapshot.py``): ``build_fleet_truth`` takes a
list of per-peer snapshots and returns the whole-domain schema; no HTTP here. The
HTTP fan-out that produces the snapshots lives in ``fleet_truth_collector`` (Phase
2); the coverage per-class disposition is enriched by a watchdog producer change
(Phase 0). Until Phase 0 lands, ``merge_coverage`` honestly marks every
non-active class ``dark`` rather than inferring green.

SCHEMA: ``fleet_truth/v1`` (see build_fleet_truth docstring).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── Cell states ──────────────────────────────────────────────────────────
HEALTHY = "healthy"   # observed OK
FAILED = "failed"     # observed BAD
DARK = "dark"         # unobservable / not-watched — NEVER "just missing = green"
_VALID_STATES = (HEALTHY, FAILED, DARK)

# Fleet-state precedence (worst wins). failed is the loudest; a dark fan-out is
# worse than a healthy one (we cannot claim health we didn't observe), so dark
# outranks healthy. This ordering is the "worst-of" roll-up.
_STATE_RANK = {HEALTHY: 0, DARK: 1, FAILED: 2}


def cell(
    state: str,
    *,
    reason: Optional[str] = None,
    age_s: Optional[float] = None,
    source: Optional[str] = None,
    observed_at: Optional[float] = None,
    absent: bool = False,
) -> Dict[str, Any]:
    """Build one tri-state cell. ``state`` must be healthy|failed|dark.

    ``absent=True`` marks a DARK cell that is dark because the subsystem is
    LEGITIMATELY not present on this box (inert / role-appropriate N-A), as
    opposed to dark because we could not observe something we should
    (indeterminate / a blind spot). An absent-dark cell never taints the fleet
    verdict; an unobservable-dark cell taints ONLY for the core-observability
    subsystems (see ``_CORE_OBSERVABILITY`` — slo-derived cells cannot express
    benign absence today, so name-gating keeps role-appropriate gaps from
    screaming). Both still render dark/grey for the human; the distinction
    only governs the roll-up.
    """
    if state not in _VALID_STATES:
        # A programming error must not silently become a healthy-looking cell.
        raise ValueError(f"invalid cell state {state!r}")
    return {
        "state": state,
        "reason": reason,
        "age_s": round(age_s, 1) if isinstance(age_s, (int, float)) else None,
        "observed_at": observed_at,
        "source": source,
        "absent": absent,
    }


# Subsystems whose DARK state means "we are blind to this box's core health" and
# therefore taints the fleet verdict. Every subsystem's FAILED always taints;
# but a DARK *optional/role-dependent* subsystem (claw, radio on a radio-less
# box, ci off the dev box) is informational — shown in the matrix, but it does
# not flip the top-line "is the fleet healthy?" verdict, because today's slo
# cells cannot express benign absence (absent=True) and would otherwise taint
# every role-appropriate gap. Reachability is handled separately (an
# unreachable box always taints). ``services`` is core (2026-07-19 adversarial
# review): every box that serves the fan-out serves /fleet/slo, so a dark
# services cell means the slo LEG is dead — without it a fleet-wide dead slo
# leg read green while half of every box's fan-out was dark.
_CORE_OBSERVABILITY = ("watchdog", "mini", "services")


def _subsystem_taints_verdict(name: str, c: Dict[str, Any]) -> bool:
    """FAILED always taints. A DARK cell taints only if it's a core-observability
    subsystem we could not see (not a benign role-absence). HEALTHY never taints."""
    st = c.get("state")
    if st == FAILED:
        return True
    if st == DARK and name in _CORE_OBSERVABILITY and not c.get("absent"):
        return True
    return False


def worst_of(states: List[str]) -> str:
    """Return the worst cell state in the list (failed > dark > healthy).
    Empty list → dark (we observed nothing, so we cannot claim health)."""
    if not states:
        return DARK
    return max(states, key=lambda s: _STATE_RANK.get(s, _STATE_RANK[DARK]))


# ── The default-dark classifier ─────────────────────────────────────────
# Substrings in a block's ``reason`` that mean "we could not observe" (dark),
# as opposed to "we observed a real fault" (failed). Sourced from the producer
# vocabulary in _map_status_endpoints.py (_read_watchdog_block etc.).
_UNOBSERVABLE_MARKERS = (
    "no_state_file", "read_error", "malformed", "stale", "not_installed",
    "no state", "unobservable", "unreachable", "timeout", "no_file",
)


def _reason_is_unobservable(reason: Optional[str]) -> bool:
    if not reason:
        return False
    low = reason.lower()
    return any(m in low for m in _UNOBSERVABLE_MARKERS)


def classify_block(
    block: Optional[Dict[str, Any]],
    *,
    source: str,
    absent_reason: str = "subsystem not present on this box",
) -> Dict[str, Any]:
    """Classify a producer block of the ``{installed, ok, age_s, reason, ...}``
    shape (the contract emitted by ``_read_watchdog_block`` /
    ``_read_mini_state_block`` / ``_read_claw_state_block``) into a tri-state
    cell. DEFAULT-DARK — healthy is returned ONLY on an explicit fresh positive.

    - block is None / missing            → dark (never observed)
    - installed is False                 → dark (subsystem not on this box)
    - reason marks unobservable (stale/  → dark (frozen/blind, NOT healthy)
      malformed/read_error/no_state_file)
    - ok is False with a real reason     → failed (observed a fault)
    - ok is True                         → healthy (the only green path)
    """
    if not isinstance(block, dict):
        return cell(DARK, reason="no data (source absent)", source=source)

    age_s = block.get("age_s")
    reason = block.get("reason")

    if block.get("installed") is False:
        # legitimately not present on this box — benign, does not taint verdict
        return cell(DARK, reason=reason or absent_reason, source=source, absent=True)

    # A block that is present but flags an unobservable condition is DARK, not
    # healthy and not failed — we could not see it (a frozen watchdog serving
    # old-but-ok JSON lands here via the stale reason).
    if _reason_is_unobservable(reason):
        return cell(DARK, reason=reason, age_s=age_s, source=source,
                    observed_at=block.get("ts"))

    ok = block.get("ok")
    if ok is True:
        return cell(HEALTHY, reason=None, age_s=age_s, source=source,
                    observed_at=block.get("ts"))
    if ok is False:
        return cell(FAILED, reason=reason or "observed not-ok", age_s=age_s,
                    source=source, observed_at=block.get("ts"))

    # ok is absent/None and nothing said it was unobservable — we cannot claim
    # health we didn't positively observe.
    return cell(DARK, reason=reason or "state indeterminate (no ok flag)",
                age_s=age_s, source=source)


# ── The domain's KNOWN structural blind spots (always dark, first-class) ──
# Harvested from the annotations in watchdog_probe_core.py + harness_map.md.
# These are the corners the domain KNOWS it is not watching — rendered as
# permanent dark rows so they can never quietly read healthy. Adding/closing a
# blind spot is a deliberate edit here (an SSOT), not an inference.
STRUCTURAL_DARK: List[Dict[str, str]] = [
    {"id": "oracle_rns_send_blind",
     "detail": "send_to_rns swallows real send exceptions to bare False — RNS-leg "
               "send errors land in the benign bucket, invisible to delivery-rate math",
     "ref": "watchdog_probe_core.py :: oracle_delivery_degraded"},
    {"id": "cross_gateway_dups_unsuppressed",
     "detail": "two gateways confirm-deliver the same content; copy #2 can't be "
               "cancelled — surfaced but not paged/fixed",
     "ref": "watchdog_probe_core.py :: gateway_dup_degraded"},
    {"id": "dep_version_drift_strays_blind",
     "detail": "stray-env watch covers meshtastic (install-fragmentation probe) and "
               "rns/lxmf (env-coherence probe, closed 2026-07-19); other deps' stray "
               "system/pipx/user-site copies remain unwatched",
     "ref": "watchdog_probes_drift.py :: probe_rns_env_coherence"},
    {"id": "user_unit_inactivity_blind",
     "detail": "always-on user .service daemons now watched (invocation-marker probe, "
               "closed 2026-07-19: parked-failed, stopped, and user-manager-down modes); "
               "user timers ride the schedules/SLO staleness layer; conditional/nested "
               "wants and non-operator users remain unwatched",
     "ref": "watchdog_probes_service.py :: probe_user_unit_inactive"},
    {"id": "mesh_rf_ota_leg_unwatched",
     "detail": "bot output can stop reaching nodes over-the-air while the RNS round-trip "
               "canary stays green — the mesh-RF leg is unwatched",
     "ref": "watchdog_probe_core.py :: meshtasticd_phoneapi_wedge"},
    {"id": "calibration_drift_not_paging",
     "detail": "calibration_drift is propose_escalation only (not a pager) until the "
               "re-derivation soaks low-false-positive",
     "ref": "mini_dudeai_rules.federator.json :: calibration_drift"},
    {"id": "aredn_configured_source_only",
     "detail": "AREDN organ only sees a CONFIGURED source; a box that should run AREDN but "
               "has it declared-absent isn't covered; slow != dark",
     "ref": "watchdog_probes_env.py :: probe_aredn_source_dark"},
    {"id": "live_claw_nats_not_wired_to_mini",
     "detail": "nats_sensor/http_json source kinds exist but the fleet preset wires none; "
               "claw is seen only via the host_frozen verdict file, not live sensor reads",
     "ref": "mini_dudeai/presets/meshforge_fleet.py"},
    {"id": "federation_digest_federator_only",
     "detail": "federation + digest sources are federator-only; a gateway box does not "
               "locally watch peer/federation health",
     "ref": "mini_dudeai/presets/meshforge_fleet.py"},
]


# ── Coverage: the per-class disposition map (mini's awareness, per box) ──
def merge_coverage(
    watchdog_block: Optional[Dict[str, Any]],
    signal_classes: List[str],
) -> Dict[str, Any]:
    """Build the per-class coverage map for one box from its watchdog block.

    disposition ∈ {active, clean, inert, indeterminate, unknown}:
      - active        — a Signal is currently present for the class
      - clean/inert/  — reported by the watchdog producer (Phase 0). Until Phase
        indeterminate    0 lands, the producer does NOT report these, so every
                         non-active class is ``unknown`` and counts as DARK.
      - unknown       — disposition not reported (pre-Phase-0) → DARK

    Honest default: a class we can't positively call ``clean`` is DARK, not green.
    ``green`` counts only classes the producer explicitly called ``clean``.
    """
    classes: Dict[str, Dict[str, Any]] = {}

    watchdog_observable = (
        isinstance(watchdog_block, dict)
        and watchdog_block.get("installed") is not False
        and not _reason_is_unobservable(watchdog_block.get("reason"))
    )

    # Active signals — from the watchdog block's signals[]. ONLY honored when
    # the block is observable: a FROZEN watchdog serving a stale block still
    # carries its last signals, and presenting those as currently-active would
    # pass a stale observation off as a current one (2026-07-19 adversarial
    # review). Unobservable → those classes fall into the dark bucket below.
    active_by_class: Dict[str, Dict[str, Any]] = {}
    if watchdog_observable and isinstance(watchdog_block, dict):
        for sig in watchdog_block.get("signals") or []:
            if isinstance(sig, dict) and sig.get("class"):
                active_by_class[sig["class"]] = sig

    # Per-class disposition reported by the producer (Phase 0 enrichment).
    # ``reported_reason`` carries the producer's own wording (e.g. WHY a
    # class is inert on this box) through to the rendered cell.
    reported: Dict[str, str] = {}
    reported_reason: Dict[str, str] = {}
    if isinstance(watchdog_block, dict):
        cov = watchdog_block.get("coverage")
        if isinstance(cov, dict):
            for cls, disp in cov.items():
                if isinstance(disp, str):
                    reported[cls] = disp
                elif isinstance(disp, dict) and isinstance(disp.get("disp"), str):
                    reported[cls] = disp["disp"]
                    if isinstance(disp.get("reason"), str):
                        reported_reason[cls] = disp["reason"]

    green = red = dark = 0
    for cls in signal_classes:
        if cls in active_by_class:
            sig = active_by_class[cls]
            classes[cls] = {"disp": "active", "severity": sig.get("severity"),
                            "subject": sig.get("subject"), "detail": sig.get("detail")}
            red += 1
            continue
        disp = reported.get(cls)
        if disp == "clean":
            classes[cls] = {"disp": "clean"}
            green += 1
        elif disp == "inert":
            classes[cls] = {"disp": "inert",
                            "reason": reported_reason.get(cls) or "organ not present on this box"}
            dark += 1
        elif disp == "indeterminate":
            classes[cls] = {"disp": "indeterminate",
                            "reason": reported_reason.get(cls) or "probe could not observe"}
            dark += 1
        elif not watchdog_observable:
            classes[cls] = {"disp": "unknown", "reason": "watchdog unobservable on this box"}
            dark += 1
        else:
            # Watchdog is up but doesn't report per-class disposition yet
            # (pre-Phase-0). Honest: we can't call it clean, so it's dark.
            classes[cls] = {"disp": "unknown",
                            "reason": "disposition not reported by watchdog (pre-coverage)"}
            dark += 1

    return {
        "watchdog_observable": watchdog_observable,
        "total": len(signal_classes),
        "green": green, "red": red, "dark": dark,
        "classes": classes,
    }


# ── Per-box + whole-fleet builders ──────────────────────────────────────
def _subsystem_from_slo(slo: Optional[Dict[str, Any]], key: str, source: str,
                        *, ok_value: Any = True) -> Dict[str, Any]:
    """Classify a /fleet/slo sub-block that reports a status string."""
    if not isinstance(slo, dict):
        return cell(DARK, reason="slo unobservable", source=source)
    val = slo.get(key)
    if val is None:
        return cell(DARK, reason=f"{key} absent from slo", source=source)
    return cell(HEALTHY if val == ok_value else FAILED,
                reason=None if val == ok_value else f"{key}={val}", source=source)


def build_box_truth(
    snap: Dict[str, Any],
    *,
    now: float,
    signal_classes: List[str],
) -> Dict[str, Any]:
    """Build the tri-state truth for one box from its fan-out snapshot.

    ``snap`` = ``{alias, resolution_method, status, slo, error, answered_at}``
    where ``status`` is the peer's /api/status body (or None), ``slo`` its
    /fleet/slo body (or None), ``error`` a fan-out error string (or None).
    A peer that could not be reached (both None, or error) is a DARK box.
    """
    alias = snap.get("alias", "?")
    status = snap.get("status") if isinstance(snap.get("status"), dict) else None
    slo = snap.get("slo") if isinstance(snap.get("slo"), dict) else None
    answered_at = snap.get("answered_at")
    age_s = (now - answered_at) if isinstance(answered_at, (int, float)) else None

    # Box reachability cell — dark if the fan-out got nothing.
    if status is None and slo is None:
        reach = cell(DARK, reason=snap.get("error") or "peer did not answer fan-out",
                     age_s=age_s, source="fanout")
        app = None
    else:
        reach = cell(HEALTHY, age_s=age_s, source="fanout", observed_at=answered_at)
        app = (status or {}).get("app") if status else None

    watchdog_block = (status or {}).get("watchdog") if status else None
    mini_block = (status or {}).get("mini_dudeai") if status else None
    claw_block = (status or {}).get("claw") if status else None

    subsystems = {
        "watchdog": classify_block(watchdog_block, source="/api/status.watchdog"),
        "mini": classify_block(mini_block, source="/api/status.mini_dudeai",
                               absent_reason="mini-dudeai not installed on this box"),
        "claw": classify_block(claw_block, source="/api/status.claw",
                               absent_reason="no claw edge node on this box"),
        "services": _subsystem_from_slo(slo, "overall_status", "/fleet/slo.services",
                                        ok_value="ready"),
        "cascade": _cascade_cell(slo),
        "ci": _ci_cell(slo),
    }
    # radio / schedules / rns_paths: present in slo when observable
    subsystems["radio"] = _radio_cell(slo)
    subsystems["schedules"] = _schedules_cell(slo)
    subsystems["rns_paths"] = _generic_present_cell(
        slo, "path_table", "/fleet/slo.path_table")

    coverage = merge_coverage(watchdog_block, signal_classes)

    # Per-box roll-up (2026-07-19 adversarial review): the box tile / counts
    # layer previously expressed REACHABILITY only, so a reachable box with an
    # observed-FAILED subsystem summarized green. box_state is the worst of
    # reachability + every verdict-tainting subsystem cell — the honest
    # at-a-glance answer to "is this box OK?".
    box_states = [reach["state"]]
    for name, c in subsystems.items():
        if _subsystem_taints_verdict(name, c):
            box_states.append(c["state"])
    box_state = worst_of(box_states)

    return {
        "alias": alias,
        "box_state": box_state,
        "reachable": {**reach, "resolution_method": snap.get("resolution_method"),
                      "app": app},
        "subsystems": subsystems,
        "coverage": coverage,
        "escalations": _extract_escalations(mini_block),
        "source_errors": _extract_source_errors(status),
    }


def _cascade_cell(slo: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(slo, dict) or not isinstance(slo.get("cascade"), dict):
        return cell(DARK, reason="cascade unobservable", source="/fleet/slo.cascade")
    c = slo["cascade"]
    pre, wed = c.get("pre_fail", 0), c.get("wedged", 0)
    if wed or pre:
        return cell(FAILED, reason=f"cascade pre_fail={pre} wedged={wed}",
                    source="/fleet/slo.cascade")
    return cell(HEALTHY, source="/fleet/slo.cascade")


def _ci_cell(slo: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """CI tri-state. FAILED only on an OBSERVED failure state; a repo whose
    state is in_progress/pending/unknown/None is DARK (cannot judge yet),
    never healthy and never an invented fault (2026-07-19 adversarial review:
    the old cell flipped the fleet verdict to FAILED on a merely in-progress
    run, and let a None state slip through to healthy)."""
    if not isinstance(slo, dict) or not isinstance(slo.get("ci_status"), dict):
        return cell(DARK, reason="ci_status unobservable", source="/fleet/slo.ci_status")
    repos = [r for r in (slo["ci_status"].get("repos") or []) if isinstance(r, dict)]
    failing = [r for r in repos if r.get("state") in ("failure", "error", "cancelled")]
    if failing:
        names = ", ".join(f"{r.get('name')}:{r.get('state')}" for r in failing[:3])
        return cell(FAILED, reason=f"ci failing: {names}", source="/fleet/slo.ci_status")
    unjudged = [r for r in repos if r.get("state") not in ("success", "clean")]
    if unjudged:
        names = ", ".join(f"{r.get('name')}:{r.get('state')}" for r in unjudged[:3])
        return cell(DARK, reason=f"ci not judgeable yet: {names}",
                    source="/fleet/slo.ci_status")
    if not repos:
        return cell(DARK, reason="no ci repos reported", source="/fleet/slo.ci_status")
    return cell(HEALTHY, source="/fleet/slo.ci_status")


def _radio_cell(slo: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(slo, dict) or not isinstance(slo.get("radio"), dict):
        return cell(DARK, reason="radio unobservable", source="/fleet/slo.radio")
    r = slo["radio"]
    conn = r.get("connected")
    if conn is True:
        return cell(HEALTHY, source="/fleet/slo.radio")
    if conn is False:
        return cell(FAILED, reason="radio not connected", source="/fleet/slo.radio")
    return cell(DARK, reason="radio connection state unknown", source="/fleet/slo.radio")


def _schedules_cell(slo: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(slo, dict) or slo.get("schedules") is None:
        return cell(DARK, reason="schedules unobservable", source="/fleet/slo.schedules")
    return cell(HEALTHY, source="/fleet/slo.schedules")


def _generic_present_cell(slo: Optional[Dict[str, Any]], key: str, source: str) -> Dict[str, Any]:
    if not isinstance(slo, dict) or slo.get(key) is None:
        return cell(DARK, reason=f"{key} unobservable", source=source)
    return cell(HEALTHY, source=source)


def _extract_escalations(mini_block: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Active mini rules → the escalations feed. ONLY from an observable mini
    block: a frozen/stale mini's last active_rules are a stale observation and
    must not present as live escalations (2026-07-19 adversarial review). The
    blindness itself is surfaced by the box's dark mini subsystem cell."""
    if not isinstance(mini_block, dict):
        return []
    if (mini_block.get("installed") is False
            or _reason_is_unobservable(mini_block.get("reason"))):
        return []
    out = []
    for r in (mini_block.get("active_rules") or mini_block.get("active") or []):
        if isinstance(r, dict):
            out.append({"rule_id": r.get("rule_id") or r.get("id"),
                        "subject": r.get("subject"), "detail": r.get("detail")})
    return out


def _extract_source_errors(status: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(status, dict):
        return []
    errs: List[str] = []
    sd = status.get("source_diagnostics")
    if isinstance(sd, dict):
        for src, info in sd.items():
            state = info.get("state") if isinstance(info, dict) else info
            if state and state not in ("ok", "healthy", "present"):
                errs.append(f"{src}: {state}")
    mini = status.get("mini_dudeai")
    if isinstance(mini, dict):
        for e in (mini.get("source_errors") or []):
            errs.append(str(e))
    return errs


def build_fleet_truth(
    peer_snapshots: List[Dict[str, Any]],
    *,
    now: float,
    signal_classes: List[str],
    noc_host: str = "?",
    hosts_declared: Optional[int] = None,
    ttl_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Assemble the whole-domain tri-state truth (schema ``fleet_truth/v1``).

    Pure: caller supplies the fan-out snapshots + a monotonic-ish ``now``. The
    fleet verdict is worst-of over every box's reachability AND every subsystem
    cell, and is forced non-healthy when the fan-out itself was incomplete
    (hosts_answered < hosts_declared) — a dark fan-out can never read green.
    """
    boxes = [build_box_truth(s, now=now, signal_classes=signal_classes)
             for s in peer_snapshots]

    answered = sum(1 for b in boxes if b["reachable"]["state"] != DARK)
    declared = hosts_declared if hosts_declared is not None else len(boxes)
    fanout_stale = answered < declared

    # Worst-of across every reachability + subsystem cell. counts{} uses the
    # per-box roll-up (box_state), not bare reachability — a reachable box
    # with an observed fault counts failed, not healthy (2026-07-19 review).
    all_states: List[str] = []
    counts = {HEALTHY: 0, FAILED: 0, DARK: 0}
    for b in boxes:
        bs = b.get("box_state") or b["reachable"]["state"]
        counts[bs] = counts.get(bs, 0) + 1
        all_states.append(b["reachable"]["state"])  # unreachable = blind spot
        for name, c in b["subsystems"].items():
            if _subsystem_taints_verdict(name, c):
                all_states.append(c["state"])
    if fanout_stale:
        all_states.append(DARK)  # incomplete fan-out taints the verdict
    fleet_state = worst_of(all_states)

    return {
        "schema": "fleet_truth/v1",
        "generated_at": now,
        "noc_host": noc_host,
        "fanout": {
            "hosts_declared": declared,
            "hosts_answered": answered,
            "ttl_s": ttl_s,
            "stale": fanout_stale,
        },
        "fleet_state": fleet_state,
        "counts": {"healthy": counts.get(HEALTHY, 0),
                   "failed": counts.get(FAILED, 0),
                   "dark": counts.get(DARK, 0)},
        "boxes": boxes,
        "structural_dark": STRUCTURAL_DARK,
    }
