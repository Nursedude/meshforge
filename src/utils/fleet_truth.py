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

#: Subsystems whose ONLY source is the box's HTTP truth surface (/api/status +
#: /fleet/slo, both served by meshforge-map). On a box whose declared role runs
#: no map, these are unobservable BY DESIGN. ``watchdog`` is deliberately NOT
#: here: the spool reads /var/lib/meshforge/watchdog.json as a raw file, so it
#: stays genuinely observable on a map-less box and must keep tainting when it
#: goes dark — that is the one core signal a gateway-only box still owes us.
_HTTP_SURFACE_SUBSYSTEMS = ("mini", "services", "claw", "cascade", "ci",
                            "radio", "schedules", "rns_paths")


def _subsystem_taints_verdict(name: str, c: Dict[str, Any]) -> bool:
    """FAILED always taints. A DARK cell taints only if it's a core-observability
    subsystem we could not see (not a benign role-absence). HEALTHY never taints.

    ``accepted_blind`` (2026-07-20) is the third case, and it is NOT the same as
    ``absent``. A gateway-only box declares ``meshforge-map: disabled`` (too
    heavy for a ~1GB board), which removes /api/status and /fleet/slo — the only
    windows onto its mini and services cells. Those subsystems may well be
    running perfectly; we simply gave up the ability to see them ON PURPOSE.
    Calling that ``absent`` would assert they are not there, which we do not
    know and would be a lie. So it gets its own flag: the cell still renders
    DARK for the human and is listed in ``accepted_blind_spots``, it just does
    not raise an alarm about a configuration the fleet deliberately chose.
    Set ONLY from the box's own declared role — never inferred from silence.
    """
    st = c.get("state")
    if st == FAILED:
        return True
    if st == DARK and name in _CORE_OBSERVABILITY and not c.get("absent") \
            and not c.get("accepted_blind"):
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


def _claw_cell(claw_block: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Classify the claw subsystem across EVERY dude-claw on the box, not just
    the primary.

    ``_read_claw_state_block`` puts the primary claw at the top level and any
    ADDITIONAL claws (dudeclaw-02, …) under ``secondaries`` (W5.1). The old
    single-block path classified ONLY the top-level primary, so a dead or
    degraded second claw was invisible in the fleet verdict — a degraded state
    mapped to a valid-looking value, the honest_failure_modes class this repo
    exists to close. Here the cell is ``worst_of`` the primary + every
    secondary: a stale/unanswered claw renders DARK (visible, informational)
    instead of vanishing under a healthy primary.

    ⚠️ SCOPE, stated honestly (2026-07-24 adversarial audit): against the REAL
    producer this cell is DISPLAY-ONLY. Every not-ok reason ``_parse_claw_tick``
    can emit — ``no_state_file``, ``read_error``, ``malformed_json``, ``stale:``,
    ``claw_unreachable:``, ``no_capture_timestamp: … unobservable`` — matches
    ``_UNOBSERVABLE_MARKERS``, so a claw block classifies DARK, and DARK claw
    never taints (claw is not in ``_CORE_OBSERVABILITY``). The FAILED path here
    is wiring that only a future producer emitting an observed-fault reason
    outside that vocabulary would exercise. A dead claw is PAGED by
    ``probe_claw_device_dark`` (watchdog), not by this verdict — do not read a
    healthy fleet verdict as "every claw answered".

    Per-claw detail rides on the cell as ``claws`` (device, state, reason, ok,
    battery, age_s) + ``claw_count`` so the fleet monitor can show BOTH claws,
    not a single summarised light. Reader/writer move together
    (honest_failure_modes #4): the display list is built from the same blocks
    the state is derived from. A claw-less box (no secondaries) keeps the exact
    single-block classification — its benign absent-dark cell is unchanged.

    ``claw_present`` (bool, both paths) is the display contract: True only on
    POSITIVE evidence that at least one tick file exists on that box. A box we
    could not reach, or whose /api/status carries no claw key at all, yields a
    dark cell with ``claw_present: False`` so the NOC's claw panel cannot
    invent a claw out of an unobservable box (honest_failure_modes #2).
    """
    source = "/api/status.claw"
    absent_reason = "no claw edge node on this box"
    secondaries: List[Dict[str, Any]] = []
    if isinstance(claw_block, dict):
        secs = claw_block.get("secondaries")
        if isinstance(secs, list):
            secondaries = [s for s in secs if isinstance(s, dict)]
    def _installed(block: Optional[Dict[str, Any]]) -> bool:
        # POSITIVE evidence only: the producer sets installed True exactly when
        # it read a tick file. A missing key / None block / empty dict is "we
        # have no idea", which must never render as a claw.
        return isinstance(block, dict) and block.get("installed") is True

    if not secondaries:
        # Single claw (or none): unchanged classification, backward-compatible.
        c = classify_block(claw_block, source=source,
                           absent_reason=absent_reason)
        c["claw_present"] = _installed(claw_block)
        return c

    def _one(block: Optional[Dict[str, Any]], device_hint: str) -> Dict[str, Any]:
        c = classify_block(block, source=source, absent_reason=absent_reason)
        blk = block if isinstance(block, dict) else {}
        return {
            "device": blk.get("device") or device_hint,
            "state": c["state"],
            "reason": c.get("reason"),
            "ok": blk.get("ok"),
            "battery": blk.get("battery"),
            "age_s": c.get("age_s"),
            "absent": bool(c.get("absent")),
        }

    per_claw = [_one(claw_block, "primary")]
    for i, s in enumerate(secondaries):
        per_claw.append(_one(s, f"secondary-{i + 1}"))

    # A benign-absent claw (installed:False) must not drag a real claw's health,
    # but it stays visible in the list. Roll up over the real (non-absent) claws.
    ranked = [p for p in per_claw if not p["absent"]]
    absent_claws = [p for p in per_claw if p["absent"]]
    state = worst_of([p["state"] for p in ranked]) if ranked else DARK
    worst = max(ranked or per_claw,
                key=lambda p: _STATE_RANK.get(p["state"], _STATE_RANK[DARK]))
    # COUNT only the claws we have evidence for. An absent entry is a claw that
    # is NOT there (its tick file is missing/unreadable) — counting it turned
    # "primary tick missing + one healthy secondary" into `2 claws healthy`,
    # rendering a MISSING OBSERVATION as a healthy device: the exact
    # honest_failure_modes #2 class this cell exists to close (07-24 audit).
    # Absent claws stay in the roster (visible, inert) and are reported in
    # their own field so the count can never absorb them.
    n = len(ranked)
    plural = "" if n == 1 else "s"
    extra = f" (+{len(absent_claws)} not present)" if absent_claws else ""
    if not ranked:
        reason = (f"no claw present — {len(absent_claws)} tick file(s) "
                  f"absent/unreadable")
    elif state == HEALTHY:
        reason = f"{n} claw{plural} healthy{extra}"
    else:
        detail = f" ({worst['reason']})" if worst.get("reason") else ""
        reason = (f"{n} claw{plural}; worst: {worst['device']} "
                  f"{worst['state']}{detail}{extra}")
    # All-absent is a BENIGN role-absence (no claw on this box), not a blind
    # spot — same disposition the single-claw path already gives it.
    c = cell(state, reason=reason, source=source, age_s=worst.get("age_s"),
             absent=not ranked)
    c["claws"] = per_claw
    c["claw_count"] = n
    c["claw_absent_count"] = len(absent_claws)
    c["claw_present"] = bool(ranked)
    return c


# ── The domain's KNOWN structural blind spots (always dark, first-class) ──
# Harvested from the annotations in watchdog_probe_core.py + harness_map.md.
# These are the corners the domain KNOWS it is not watching — rendered as
# permanent dark rows so they can never quietly read healthy. Adding/closing a
# blind spot is a deliberate edit here (an SSOT), not an inference.
STRUCTURAL_DARK: List[Dict[str, str]] = [
    {"id": "oracle_rns_send_blind",
     "detail": "CLOSED for the RNS leg 2026-07-20 (row 2): send_to_rns returns an "
               "RnsSendResult — bool-compatible, so every truthiness call site is "
               "unchanged, plus a .reason (no_path | circuit_open | not_connected | "
               "no_lxmf_source | broadcast_unsupported | send_error) the oracle records "
               "into its audit log. A real send crash is now filed as send_error instead "
               "of landing in the benign bucket, so oracle_delivery_degraded no longer "
               "under-counts real failures, and benign_rns_ambiguous counts only "
               "reason-LESS non-deliveries — it shrinks as the blind spot closes rather "
               "than holding steady. RESIDUAL: oracle_delivery_degraded's failure set now "
               "covers all four transports, but the MESH leg's confirmability is still "
               "bounded by ACK consumption (#74 T2 step 4), not by this row",
     "ref": "watchdog_probes_gateway_flow.py :: probe_oracle_delivery_degraded"},
    {"id": "cross_gateway_dups_unsuppressed",
     "detail": "ACCEPTED-PERMANENT 2026-07-19 (operator): keep the detector, do NOT build "
               "cross-gateway coordination. The reason is COST ASYMMETRY, not a low rate — "
               "a duplicate is redundancy (delivered twice), while a yield-protocol bug is "
               "SILENCE (A yields, B fails, message lost). On emergency-comms infrastructure "
               "failures must land on the redundancy side. Explicitly NOT accepted because "
               "dups are rare: three HUMAN recipients are dual-homed across both gateways "
               "right now, so the precondition is live and the rate is traffic-dependent. "
               "RESIDUAL/leading indicator: dual-homed-recipient COUNT is observable "
               "(routing state) even on the mesh leg where confirmation-based dup detection "
               "structurally cannot see",
     "ref": ".claude/research/cross_gateway_dup_design_inputs_2026_07_19.md"},
    {"id": "dep_version_drift_strays_blind",
     "detail": "ACCEPTED-PERMANENT 2026-07-19 (row 3, operator decision on a fleet-wide "
               "survey): stray-env watch covers meshtastic (install-fragmentation) and "
               "rns/lxmf (env-coherence) — the only deps installed by COMPETING TOOLS. "
               "Other deps' stray copies stay unwatched BY DESIGN: for OS-shipped libs a "
               "venv copy shadowing system-dist is the designed state, so watching them "
               "would page on benign divergence or never fire against outgrown floors. "
               "Revisit only when a dep gains a second installer",
     "ref": ".claude/research/dep_stray_watch_scope_2026_07_19.md"},
    {"id": "user_unit_inactivity_blind",
     "detail": "always-on user .service daemons now watched (invocation-marker probe, "
               "closed 2026-07-19: parked-failed, stopped, and user-manager-down modes); "
               "user timers ride the schedules/SLO staleness layer; conditional/nested "
               "wants and non-operator users remain unwatched",
     "ref": "watchdog_probes_service.py :: probe_user_unit_inactive"},
    {"id": "mesh_rf_ota_egress_unproven",
     "detail": "NARROWED 2026-07-19 (row 9): the claws now witness the air from a SEPARATE "
               "radio on separate silicon (claw_rf_silent), so channel-wide RF silence is "
               "observable independently of any box's self-report — the first mesh-RF "
               "evidence no box can fabricate about itself. NARROWED AGAIN 2026-07-29: the "
               "per-source half of the old residual is now watched — each claw reports, per "
               "WATCHED NODE ID, whether it has heard that specific transmitter, and "
               "claw_watched_node_silent consumes it. That closes the mute-transmitter blind "
               "spot heard_age_s structurally could not see (a busy channel reads healthy at "
               "~5 s while OUR PA is dead). NARROWED AGAIN 2026-07-30, after that closure "
               "turned out to have a hole the size of a whole segment: the claws listen on "
               "LONG_FAST/ch20 and this fleet is deliberately two-preset, so moc2+moc3 on "
               "SHORT_TURBO/ch8 — the pair carrying the RNS leg — were undemodulable by every "
               "claw and had NO RF witness at all, while the watch list pointed at them and "
               "reported `silent`. segment_peer_silent covers them from a witness that already "
               "existed and was never read: those two boxes hear EACH OTHER (-17 dBm), and a "
               "box reporting on a DIFFERENT box is independent in the way self-report is not. "
               "RESIDUAL: it proves our TX reaches a claw's "
               "receiver, which is egress evidence but not delivery — mesh ACK consumption "
               "(#74 T2 step 4) is still the only end-to-end proof; unwatched nodes stay "
               "unwatched (the list is explicit, by design); and both windows are "
               "PROVISIONAL — no real per-node `silent` verdict has been observed on this "
               "fleet yet — so both signals escalate and neither pages",
     "ref": "watchdog_probes_liveness.py :: probe_claw_rf_silent + "
            "watchdog_probes_claw_watch.py :: probe_claw_watched_node_silent + "
            "watchdog_probes_peer_rf.py :: probe_segment_peer_silent"},
    {"id": "lxmf_propagation_unadopted",
     "detail": "OPENED 2026-07-20 by the optional-organ sweep, and watched from the same "
               "day. Structural finding behind it: of ~50 signal classes, only ONE watched "
               "for an available-but-UNADOPTED capability — the domain is excellent at "
               "watching what it was TOLD about. This is the second such watcher. LXMF "
               "propagation nodes announce on the RNS network and the gateway already "
               "parses them into its own node cache (14-15 heard within 6h on both gateway "
               "boxes, 2026-07-20) while gateway.json rns.propagation_node is empty, so "
               "nothing is stored and forwarded: LXMF to a peer that is OFFLINE right now "
               "fails outright instead of waiting for it to return. DELIBERATELY "
               "REPORT-ONLY: adoption is a TRUST decision (a propagation node sees "
               "stored-traffic metadata) and standing one up on our own rnsd beats "
               "pointing at a stranger's, so the probe names the gap and never prescribes "
               "a node. RESIDUAL until adopted: offline-peer delivery stays best-effort, "
               "and once adopted this leg goes INERT — nothing yet checks that a "
               "CONFIGURED propagation node still answers (that would be a shape-A leg)",
     "ref": "watchdog_probes_gateway.py :: probe_lxmf_propagation_unused"},
    {"id": "lxmf_propagation_node_unwatched",
     "detail": "CLOSED 2026-07-20 the same day it was opened, and deliberately so. Setting "
               "gateway.json rns.propagation_node makes lxmf_propagation_unused go INERT by "
               "design (one fault, one owner), so adoption ALONE would have traded a WATCHED "
               "gap for an UNWATCHED dependency — strictly worse than leaving it unadopted, "
               "because offline-peer delivery would then rest on a node nobody checked. "
               "probe_lxmf_propagation_node_dark is the shape-A companion, shipped in the same "
               "push as adoption for exactly that reason. Two legs with different fixes: STALE "
               "(in the RNS node cache but silent for several announce periods — it answered "
               "and stopped) and UNHEARD (the hash is absent from the cache entirely, i.e. "
               "wrong or truncated — the failure adoption itself introduces). Evidence is the "
               "same durable operator-owned rns_nodes.json, never the journal (Storage=volatile). "
               "The guard that makes it honest: it fires ONLY when some OTHER propagation "
               "announce reached the box inside the window, which is positive proof the box can "
               "hear the class at all — so an RNS-wide wedge HOLDS here and stays with its own "
               "probes instead of being relabelled as this node's death. RESIDUAL: liveness is "
               "passive announce observation, so detection is bounded below by several announce "
               "periods (~18h). Its OTHER residual — a node that answers announces but "
               "refuses to STORE would still read clean — was CLOSED 2026-07-21 by "
               "propagation_soak_degraded (see the next row); this probe deliberately keeps "
               "owning announce-liveness only",
     "ref": "watchdog_probes_gateway.py :: probe_lxmf_propagation_node_dark"},
    {"id": "lxmf_store_and_forward_unproven",
     "detail": "CLOSED 2026-07-21 (propagation arc slice 3). probe_lxmf_propagation_node_dark "
               "watches whether the configured propagation node ANNOUNCES; it cannot see "
               "whether that node STORES AND FORWARDS. A node announcing perfectly while "
               "silently dropping every stored message reads clean forever — announce-liveness "
               "standing in as a proxy for the property actually being depended on. The gap "
               "was structural, not theoretical: in a 2-person lab, traffic to an OFFLINE peer "
               "essentially never occurs organically, so the realistic failure was adopting the "
               "organ and having it be quietly useless for months with every gate green. The "
               "pre-existing exerciser could not close it either — lxmf_multi_user_synth builds "
               "every LXMessage without desired_method, so all synth traffic is DIRECT. Cure: "
               "an hourly drill that MANUFACTURES the missing traffic — a receiver identity "
               "that has never announced (so no direct path to it can exist) is the target of a "
               "PROPAGATED send, then comes up and pulls the message back; delivery proves "
               "store-and-forward end to end. probe_propagation_soak_degraded consumes the "
               "envelope with two legs: ENVELOPE (pass_envelope false — accepted but never "
               "returned) and SILENCE (newest prop-*.json older than ~2.5 cadences, because "
               "for a fixed-cadence generator going quiet IS the failure). Synthetic traffic is "
               "isolated from delivery_counters by construction (its own LXMRouter instances "
               "and throwaway identities) so the #74 confirmation_rate work stays honest. "
               "RESIDUAL: the drill proves the node serves a CLIENT that pulls; it does not "
               "exercise node-to-node peering, and it measures one small message, so a "
               "size- or volume-dependent failure would still read clean",
     "ref": "watchdog_probes_gateway.py :: probe_propagation_soak_degraded"},
    {"id": "aredn_configured_source_only",
     "detail": "REOPENED BY CHOICE 2026-08-08 (signal-class yield audit). Was CLOSED "
               "2026-07-20 by probe_aredn_organ_undeclared, the positive-evidence leg "
               "that caught a box sitting on an AREDN node's LAN having neither "
               "configured aredn_node_ips NOR declared organ_expectations.aredn. That "
               "probe was removed: it read INERT on all 8 boxes (4 with no AREDN LAN, "
               "3 where the configured-source legs own the box) and had fired exactly "
               "once in 49 days, at bring-up. Its only firing state is a NEW box with "
               "an AREDN LAN and no config -- a state a human is present for. The two "
               "surviving legs (configured-source dark, declared-but-unconfigured) "
               "still cover every box that made a STATEMENT. ACCEPTED GAP: a box that "
               "makes NEITHER statement is invisible again, exactly as it was before "
               "2026-07-20. Recorded here rather than dropped, so the coverage claim "
               "stays honest -- an audit that silently deletes a row is the defect "
               "this file exists to prevent",
     "ref": "removed 2026-08-08; see .claude/audits/signal_class_yield_2026_08_08.md"},
    {"id": "claw_edge_rf_coverage_partial",
     "detail": "NARROWED 2026-07-19 (row 7): claw edge nodes now have their OWN signals — "
               "claw_device_dark (fresh capture tick says the DEVICE didn't answer) and "
               "claw_battery_low (reachable pack under the LiPo floor), read from the "
               "tick files the capture already writes. Deliberately NOT a second NATS "
               "poll from the fleet preset: one poller, one threshold set. RESIDUAL: "
               "thresholded live sensing (temp/anomaly/LoRa-ears) still runs only in the "
               "per-device claw mini, so a claw without its own instance is watched for "
               "liveness and battery but not for its sensors",
     "ref": "watchdog_probes_liveness.py :: probe_claw_device_dark"},
    {"id": "federation_mapless_box_unwatched",
     "detail": "NARROWED 2026-07-19 (row 6): federation is now watched PER VANTAGE — every "
               "map-running box polls its OWN :5000 and escalates (never pages) an "
               "unhealthy peer, so a peer visible from one box and not another is "
               "distinguishable. Digest stays federator-scoped BY DESIGN (a federator "
               "artifact). RESIDUAL: a box running no local map has no vantage at all, "
               "and a box whose federation lists zero peers is inert — not covering",
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
    # Gated on the SAME observability test as signals[] (2026-07-21
    # re-review): a frozen watchdog's stale block carries its last
    # coverage{} too, and honoring it rendered current green for exactly
    # the classes that were firing when it froze — the stale signal was
    # suppressed above, then fell through to its stale "clean" disposition.
    reported: Dict[str, str] = {}
    reported_reason: Dict[str, str] = {}
    if watchdog_observable and isinstance(watchdog_block, dict):
        cov = watchdog_block.get("coverage")
        if isinstance(cov, dict):
            for cls, disp in cov.items():
                if isinstance(disp, str):
                    reported[cls] = disp
                elif isinstance(disp, dict) and isinstance(disp.get("disp"), str):
                    reported[cls] = disp["disp"]
                    if isinstance(disp.get("reason"), str):
                        reported_reason[cls] = disp["reason"]

    # Classes the BOX reported that this server's SIGNAL_CLASSES doesn't know.
    #
    # 2026-07-20, the #79 deploy-restart gap in its truth-API skin: the server
    # imports SIGNAL_CLASSES once at process start, so after a `fleet_pull`
    # that adds a class, every box's watchdog reports it while a long-running
    # meshforge-map still holds the OLD list. Iterating the server's list alone
    # SILENTLY DROPPED those classes and still published `total` as though the
    # map were complete — a stale enum rendered as authoritative coverage
    # (honest_failure_modes #1). Measured that day: 9 boxes reporting 52
    # classes rendered as 49, hiding two probes shipped hours earlier, with
    # nothing anywhere saying the view was partial.
    #
    # Cure: the box's own report WINS. Never drop a class someone observed —
    # render it with its real disposition and name the discrepancy, because a
    # box knowing classes the server doesn't IS the signal that this server is
    # running older code than the fleet.
    # ⚠️ Guarded on a NON-EMPTY enum. An empty ``signal_classes`` is its own
    # distinct condition — MeshAnchor's documented honest-zero posture, and the
    # fallback its collector uses when the blackout-kind import FAILS — not
    # evidence of code skew. You cannot be "behind" a list you do not have.
    # Ungated, a failed enum import on the twin would render every reported
    # class as unknown, pin the verdict permanently DARK, and blame it on a
    # stale deploy: one degraded state wearing another's diagnosis, which is
    # the exact defect this whole fix exists to remove.
    unknown_to_server = sorted(
        (set(reported) | set(active_by_class)) - set(signal_classes)
    ) if signal_classes else []

    green = red = dark = 0
    for cls in list(signal_classes) + unknown_to_server:
        if cls in active_by_class:
            sig = active_by_class[cls]
            _cell = {"disp": "active", "severity": sig.get("severity"),
                     "subject": sig.get("subject"), "detail": sig.get("detail")}
            # Preserve structured per-target verdicts so build_fleet_truth can
            # fan the out-of-band host-probe witness (host_frozen) onto the row
            # of the box it DESCRIBES — the collector runs on one box but
            # reports on others. Only a {name, verdict, ...} list rides along;
            # classes without one keep their textual detail unchanged.
            _extra = sig.get("extra")
            if isinstance(_extra, dict) and isinstance(_extra.get("targets"), list):
                _cell["targets"] = _extra["targets"]
            # B4: a HELD signal (tracker kept it because its observation
            # channel went dark) is last-observed data, not a live reading —
            # carry the marker so the UI can dim it instead of rendering
            # stale detail as fresh.
            if isinstance(_extra, dict) and _extra.get("unobserved_hold"):
                _cell["unobserved_hold"] = True
            classes[cls] = _cell
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
        # Counts the classes actually RENDERED, not the server's enum length —
        # otherwise `total` under-reports the very classes we just recovered.
        "total": len(signal_classes) + len(unknown_to_server),
        "green": green, "red": red, "dark": dark,
        "classes": classes,
        # Empty is the healthy steady state; non-empty means THIS server's code
        # is older than the box's (restart it / check the deploy).
        "unknown_to_server": unknown_to_server,
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


def _services_cell_from_spool(services: Dict[str, Any]) -> Dict[str, Any]:
    """Services cell for a map-less box from the spool's ``systemctl`` map
    (``{unit: {active, enabled}}``). The source label is HONEST —
    ``ssh_spool.systemctl``, NOT ``/fleet/slo.services`` — because this is the
    narrower "the box's declared-enabled units are active" check, not the map's
    full SLO ``overall_status=ready`` (which asserts more).

    An enabled-but-inactive unit is a real fault on ANY box (FAILED, named). A
    box where no enabled unit was observed is DARK (never green — absence of
    evidence is not health, honest_failure_modes #2)."""
    src = "ssh_spool.systemctl"
    if not isinstance(services, dict) or not services:
        return cell(DARK, reason="no services observed", source=src)
    enabled = {u: s for u, s in services.items()
               if isinstance(s, dict) and s.get("enabled") == "enabled"}
    if not enabled:
        return cell(DARK, reason="no enabled services observed", source=src)
    down = sorted(u for u, s in enabled.items() if s.get("active") != "active")
    # A unit caught mid-start is TRANSIENT, not a proven fault: the 2-min
    # spool cron can land during a deploy restart, and rendering `activating`
    # as FAILED flashed "enabled but inactive: meshforge-gateway" for a cycle
    # with no debounce anywhere on this path (07-23 audit). In the tri-state
    # vocabulary a starting unit's health is not-yet-determined → DARK
    # (visible, honest, not an alarm); real inactive/failed stays FAILED.
    # LIMITATION (B5): `activating` is ambiguous at a point-in-time read — a
    # crashlooping unit spends most of its life in `activating` (auto-restart
    # backoff, the #82 shape), indistinguishable here from a deploy restart.
    # The spool has no history to debounce on, so this cell only refuses to
    # call the unit healthy; probe-side detectors own crashloop paging.
    transient = sorted(u for u in down
                       if (enabled[u].get("active") or "")
                       in ("activating", "reloading"))
    hard_down = [u for u in down if u not in transient]
    if hard_down:
        return cell(FAILED,
                    reason=f"enabled but inactive: {', '.join(hard_down)}",
                    source=src)
    if transient:
        return cell(DARK,
                    reason=f"activating: {', '.join(transient)} (transient "
                           f"start OR crash-loop backoff — if this persists "
                           f"across re-checks it is a crashloop; probe-side "
                           f"detectors page that)",
                    source=src)
    return cell(HEALTHY, source=src)


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

    # A map-less gateway's mini/radio/services arrive via the ssh-spool's raw
    # non-HTTP reads (2026-07-22), not the HTTP surface. Where the spool DID
    # observe a cell, it is genuinely observed — so it must not later be marked
    # accepted_blind (a stale spool-observed mini is a real "wedged" signal that
    # must taint, not a suppressed no-surface blind spot).
    spool_services = snap.get("spool_services")
    spool_observed = set()
    if isinstance(mini_block, dict):
        spool_observed.add("mini")
    if isinstance(slo, dict) and isinstance(slo.get("radio"), dict):
        spool_observed.add("radio")
    if isinstance(spool_services, dict):
        spool_observed.add("services")

    subsystems = {
        "watchdog": classify_block(watchdog_block, source="/api/status.watchdog"),
        "mini": classify_block(mini_block, source="/api/status.mini_dudeai",
                               absent_reason="mini-dudeai not installed on this box"),
        "claw": _claw_cell(claw_block),
        "services": (_services_cell_from_spool(spool_services)
                     if isinstance(spool_services, dict)
                     else _subsystem_from_slo(slo, "overall_status",
                                              "/fleet/slo.services", ok_value="ready")),
        "cascade": _cascade_cell(slo),
        "ci": _ci_cell(slo),
    }
    # radio / schedules / rns_paths: present in slo when observable
    subsystems["radio"] = _radio_cell(slo)
    subsystems["schedules"] = _schedules_cell(slo)
    subsystems["rns_paths"] = _generic_present_cell(
        slo, "path_table", "/fleet/slo.path_table")

    # Mark the cells whose ONLY window is the HTTP surface this box's declared
    # role deliberately does not run. Strictly gated: the flag is applied only
    # when the collector decided ``False`` from the box's OWN deployment.json
    # role against the role catalog. None (undeclared / unknown role / catalog
    # unreadable) leaves every cell exactly as dark and as tainting as before —
    # an undeclared blind spot is still an alarm, which is what keeps this from
    # becoming a silence-manufacturing switch.
    if snap.get("http_surface_expected") is False:
        for _name in _HTTP_SURFACE_SUBSYSTEMS:
            if _name in spool_observed:
                continue  # genuinely observed via the spool — not a blind spot,
                          # so a dark (e.g. stale) cell here must still taint.
            _c = subsystems.get(_name)
            if isinstance(_c, dict) and _c.get("state") == DARK:
                _c["accepted_blind"] = True
                _c["reason"] = (
                    f"{_c.get('reason') or 'unobservable'} — accepted: this "
                    "box's declared role runs no map server, so it has no "
                    "HTTP truth surface")

    # A role that declares NO watchdog makes its dark watchdog cell `absent`
    # (the organ is not here) rather than an unobserved blind spot. Deliberately
    # `absent`, not `accepted_blind`: accepted_blind claims "it may well be
    # running, we gave up SEEING it", which would be a lie here — the unit is
    # not installed. Both stop tainting; only this one is true.
    #
    # Never inferred from silence: set ONLY when the box's own declared role
    # says so (watchdog_expected is False, never None) AND no watchdog block
    # arrived at all. Gating on the BLOCK, not on `spool_observed`, is
    # load-bearing: spool_observed only ever carries mini/radio/services, so a
    # `"watchdog" not in spool_observed` guard is vacuously true and would also
    # swallow the case that matters — a box that HAS a watchdog whose file went
    # STALE produces a present-but-dark block, and that must keep tainting.
    if snap.get("watchdog_expected") is False and watchdog_block is None:
        _w = subsystems.get("watchdog")
        if isinstance(_w, dict) and _w.get("state") == DARK:
            _w["absent"] = True
            _w["reason"] = (
                f"{_w.get('reason') or 'no data'} — this box's declared role "
                "runs no watchdog (local probe coverage given up by design; "
                "reachability, mini, services and radio still taint)")

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
        # Out-of-band verdicts about THIS box produced by a claw witness running
        # on ANOTHER box (the claw-brain). Filled by build_fleet_truth's fan-out
        # once every box row exists; display-only (never taints box_state — an
        # external witness has its own blind spots / cross-subnet routing gaps).
        "witnessed": [],
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
    # Every COMPLETED-with-failure conclusion gh can emit, not just the common
    # three: timed_out/startup_failure/action_required are observed terminal
    # states too — leaving them in the "not judgeable yet" bucket parked a red
    # CI as permanent non-tainting DARK with a reason implying it would
    # resolve (2026-07-21 re-review).
    failing = [r for r in repos if r.get("state") in (
        "failure", "error", "cancelled", "timed_out", "startup_failure",
        "action_required")]
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

    # Fan the out-of-band host-probe witness (host_frozen) onto the row of the
    # box it DESCRIBES. The claw collector runs on ONE box (the claw-brain) and
    # reports on OTHERS, so its per-target verdicts otherwise ride only under
    # the collector's coverage chip — a box witnessed frozen/unreachable shows
    # nothing at its own row. A target whose name isn't a fleet box alias (e.g.
    # a mesh bot) has no row to land on and stays in the collector's detail.
    # Display-only: this never changes box_state — the witness can be blind or
    # cross-subnet-routing-gapped (a failover UNREACHABLE is already UNKNOWN
    # upstream), so it annotates, never overrides, a box's own verdict.
    _by_alias = {b["alias"]: b for b in boxes}
    for b in boxes:
        _cell = ((b.get("coverage") or {}).get("classes") or {}).get("host_frozen")
        if not isinstance(_cell, dict) or _cell.get("disp") != "active":
            continue
        for _t in _cell.get("targets") or []:
            if not isinstance(_t, dict):
                continue
            _wb = _by_alias.get(str(_t.get("name")))
            if _wb is None or _wb is b:
                continue
            _wb.setdefault("witnessed", []).append({
                "verdict": _t.get("verdict"),
                "witness_device": _t.get("witness_device"),
                "failover": bool(_t.get("failover")),
                "source_box": b.get("alias"),
            })

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

    # Server-vs-fleet code skew (2026-07-20). Rolled up to the top because a
    # per-box `unknown_to_server` list is exactly the kind of true-but-buried
    # detail nobody reads. If ANY box reports a signal class this process
    # doesn't know, this server is running older code than the fleet — the
    # #79 deploy-restart gap — and every coverage map it publishes is partial.
    #
    # It taints the verdict as DARK rather than FAILED on purpose: nothing is
    # broken out there, we simply cannot see all of it from here, and
    # unobservable must never read healthy (honest_failure_modes #2).
    # ⚠️ SAME-APP boxes only — caught live on first deploy 2026-07-20.
    # meshanchor-server immediately reported no_data/http_dead/frozen/
    # daemon_dead: MeshAnchor's OWN blackout-kind vocabulary, not classes this
    # NOC is behind on. Ungated, a heterogeneous fleet pins itself DARK forever
    # on a false "your code is stale" diagnosis. Those classes still RENDER
    # (per-box coverage keeps them — never drop what a box observed); they just
    # do not accuse this server of being out of date.
    #
    # Rule: only a box running the SAME app can prove this server's enum is
    # behind. A different app knowing different classes is expected
    # heterogeneity (MeshForge and MeshAnchor are sister NOCs by design).
    def _app_name(b: Dict[str, Any]) -> Optional[str]:
        app = ((b.get("reachable") or {}).get("app")) or {}
        name = app.get("name") if isinstance(app, dict) else None
        return name.strip().lower() if isinstance(name, str) else None

    # This server's own app identity. Resolved at RUNTIME from __app_name__ —
    # the same source /api/status.app uses — so this byte-locked file yields
    # "meshforge" here and "meshanchor" in the twin without diverging. An
    # unresolvable identity disables the accusation entirely (below), which is
    # the safe direction: no diagnosis beats a wrong one.
    try:
        from __version__ import __app_name__ as _self_app_raw
        self_app = (_self_app_raw or "").strip().lower() or None
    except Exception:
        self_app = None
    skew: Dict[str, List[str]] = {}
    for b in boxes:
        extra = (b.get("coverage") or {}).get("unknown_to_server") or []
        if not extra:
            continue
        peer_app = _app_name(b)
        # Unknown app on EITHER side → do NOT accuse. An unidentifiable peer
        # (or an unresolvable self) is a blind spot, and blind spots must not
        # manufacture a confident diagnosis.
        if self_app is None or peer_app is None or peer_app != self_app:
            continue
        for cls in extra:
            skew.setdefault(cls, []).append(b.get("alias") or "?")
    if skew:
        all_states.append(DARK)

    # Accepted blind spots — surfaced as their own line, never averaged into a
    # healthy-looking summary (honest_failure_modes #5). We stopped ALARMING on
    # these; we do not get to stop DISCLOSING them. A fleet reading healthy
    # while carrying accepted blind spots must say so out loud, so the count
    # can be argued with — and so nobody later mistakes "no alarm" for
    # "fully observed".
    accepted: Dict[str, List[str]] = {}
    for b in boxes:
        for name, c in (b.get("subsystems") or {}).items():
            if isinstance(c, dict) and c.get("accepted_blind"):
                accepted.setdefault(b.get("alias") or "?", []).append(name)
    for v in accepted.values():
        v.sort()

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
        # {box: [subsystems]} unobservable because that box's DECLARED role
        # runs no map server. Not alarms — disclosures. Empty is fully observed.
        "accepted_blind_spots": accepted,
        # {signal_class: [boxes reporting it]} — empty in the steady state.
        # Non-empty = this NOC server's code is older than the fleet's; its
        # coverage maps are partial until the serving process is restarted.
        "server_class_skew": skew,
    }
