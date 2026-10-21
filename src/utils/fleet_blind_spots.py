"""The domain's KNOWN structural blind spots — the registry /fleet renders.

Split out of fleet_truth.py 2026-10-20 (MF025 size cap) when each row gained
`status`, `since` and a one-line `dark`. Pure data plus the pure functions
that serve it (stdlib-only, so a collector's fallback can serve the registry
when fleet_truth itself failed to build); fleet_truth re-exports them.
"""
from typing import Any, Dict, List, Optional

# ── The domain's KNOWN structural blind spots (always dark, first-class) ──
# Harvested from the annotations in watchdog_probe_core.py + harness_map.md.
# These are the corners the domain KNOWS it is not watching — rendered as
# permanent dark rows so they can never quietly read healthy. Adding/closing a
# blind spot is a deliberate edit here (an SSOT), not an inference.
# `since` is the date of the row's LATEST status change; a `detail` that opens
# with a status word opens with the row's own status (test-pinned).
# status: open (nothing watches it) | narrowed (watched in part; `dark` is the
# residual) | accepted (unwatched by an operator decision) | closed (kept so a
# closure is never a silent deletion; `dark` names who owns what remains).
# `dark` is the ONE line /fleet shows; `detail` is the record behind it.
# `watched_in` (every row but `open`): the apps whose probes back what the row
# says IS watched. This registry is served by BOTH NOCs, so coverage earned by
# MeshForge's probes is not a claim about MeshAnchor's boxes — list an app only
# after measuring that its probe runs there (2026-10-20: none of the 9 probes
# these rows cite exist in MeshAnchor; each repo's test checks the probes cited
# by rows naming its own app). Accepted rows carry it too: the operator's
# decision holds on every NOC, the detection it rests on does not.
STRUCTURAL_DARK_STATUSES = ("open", "narrowed", "accepted", "closed")
STRUCTURAL_DARK: List[Dict[str, Any]] = [
    {"id": "oracle_rns_send_blind",
     "status": "narrowed", "since": "2026-07-20",
     "watched_in": ["meshforge"],
     "dark": "the mesh leg's delivery is confirmable only through ACK consumption (#74 T2 step 4)",
     "detail": "NARROWED 2026-07-20 (row 2) — the RNS leg's original gap is closed: "
               "send_to_rns returns an "
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
     "status": "accepted", "since": "2026-07-19",
     "watched_in": ["meshforge"],
     "dark": "cross-gateway duplicates are detected, never suppressed: redundancy over silence, by choice",
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
     "status": "accepted", "since": "2026-07-19",
     "watched_in": ["meshforge"],
     "dark": "stray copies of deps other than meshtastic/rns/lxmf are unwatched by design",
     "detail": "ACCEPTED-PERMANENT 2026-07-19 (row 3, operator decision on a fleet-wide "
               "survey): stray-env watch covers meshtastic (install-fragmentation) and "
               "rns/lxmf (env-coherence) — the only deps installed by COMPETING TOOLS. "
               "Other deps' stray copies stay unwatched BY DESIGN: for OS-shipped libs a "
               "venv copy shadowing system-dist is the designed state, so watching them "
               "would page on benign divergence or never fire against outgrown floors. "
               "Revisit only when a dep gains a second installer",
     "ref": ".claude/research/dep_stray_watch_scope_2026_07_19.md"},
    {"id": "user_unit_inactivity_blind",
     "status": "narrowed", "since": "2026-07-19",
     "watched_in": ["meshforge"],
     "dark": "conditional/nested wants and non-operator users' units are unwatched",
     "detail": "always-on user .service daemons now watched (invocation-marker probe, "
               "closed 2026-07-19: parked-failed, stopped, and user-manager-down modes); "
               "user timers ride the schedules/SLO staleness layer; conditional/nested "
               "wants and non-operator users remain unwatched",
     "ref": "watchdog_probes_service.py :: probe_user_unit_inactive"},
    {"id": "mesh_rf_ota_egress_unproven",
     "status": "narrowed", "since": "2026-07-30",
     "watched_in": ["meshforge"],
     "dark": "our RF egress is witnessed, delivery is not; unwatched nodes stay unwatched; per-node windows provisional",
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
     "status": "closed", "since": "2026-07-20",
     "watched_in": ["meshforge"],
     "dark": "adopted 2026-07-20 on MeshForge's gateway boxes; the node's liveness is owned by the next row",
     "detail": "CLOSED 2026-07-20 by adoption: rns.propagation_node was set on the gateway "
               "boxes in the same push as probe_lxmf_propagation_node_dark, which owns the "
               "configured node's liveness (next row). Re-measured 2026-10-20 on the "
               "MeshForge fleet only: both MeshForge gateway boxes' gateway.json carry "
               "it; MeshAnchor's boxes were not measured. The original record follows. "
               "OPENED 2026-07-20 by the optional-organ sweep, and watched from the same "
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
     # The original adoption watcher (lxmf_propagation_unused) was removed
     # 2026-08-08 in dfb862fc; the ref names what watches this corner NOW.
     "ref": "watchdog_probes_gateway_lxmf.py :: probe_lxmf_propagation_node_dark"},
    {"id": "lxmf_propagation_node_unwatched",
     "status": "narrowed", "since": "2026-07-21",
     "watched_in": ["meshforge"],
     "dark": "propagation-node liveness is passive: a dead node takes several announce periods (~18 h) to show",
     "detail": "NARROWED 2026-07-21 — the original gap CLOSED 2026-07-20, the same day it "
               "was opened, and deliberately so. Setting "
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
     "status": "narrowed", "since": "2026-07-21",
     "watched_in": ["meshforge"],
     "dark": "the drill proves one small client pull; node-to-node peering and size/volume failures are unexercised",
     "detail": "NARROWED 2026-07-21 — the original gap closed by propagation arc slice 3. "
               "probe_lxmf_propagation_node_dark "
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
     "status": "accepted", "since": "2026-08-08",
     "watched_in": ["meshforge"],
     "dark": "a box that neither configures nor declares AREDN is invisible to AREDN coverage",
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
     "status": "narrowed", "since": "2026-07-19",
     "watched_in": ["meshforge"],
     "dark": "a claw without its own mini is watched for liveness and battery, not its sensors",
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
     "status": "narrowed", "since": "2026-07-19",
     "watched_in": ["meshforge"],
     "dark": "a box with no local map has no federation vantage; a zero-peer federation is inert",
     "detail": "NARROWED 2026-07-19 (row 6): federation is now watched PER VANTAGE — every "
               "map-running box polls its OWN :5000 and escalates (never pages) an "
               "unhealthy peer, so a peer visible from one box and not another is "
               "distinguishable. Digest stays federator-scoped BY DESIGN (a federator "
               "artifact). RESIDUAL: a box running no local map has no vantage at all, "
               "and a box whose federation lists zero peers is inert — not covering",
     "ref": "mini_dudeai/presets/meshforge_fleet.py"},
]


def resolve_self_app() -> Optional[str]:
    """This server's own app identity ("meshforge" / "meshanchor"), resolved at
    RUNTIME from __app_name__ — the same source /api/status.app uses — so this
    byte-locked file names the right app in each twin. None when unresolvable."""
    try:
        from __version__ import __app_name__ as _self_app_raw
        return (_self_app_raw or "").strip().lower() or None
    except Exception:
        return None


def blind_spot_scope(row: Dict[str, Any], self_app: Optional[str]) -> str:
    """Whether THIS NOC watches what a row says is watched.

    ``n/a`` — open: nothing watches it anywhere. ``here`` — this app is in the
    row's ``watched_in``. ``elsewhere`` — another app's probes back it, so on
    this NOC the corner is unwatched. ``unknown`` — no ``watched_in``, or this
    server cannot name its own app. Default-dark: any status but ``open`` —
    including one this code has never heard of — must EARN ``here``.
    """
    if row.get("status") == "open":
        return "n/a"
    watched_in = row.get("watched_in")
    if not watched_in or self_app is None:
        return "unknown"
    return "here" if self_app in watched_in else "elsewhere"


def structural_dark_rows(self_app: Optional[str]) -> List[Dict[str, Any]]:
    """The registry as served: COPIES (the truth document is cached and served,
    and editing it must never edit the registry), each with its ``scope``."""
    rows = []
    for row in STRUCTURAL_DARK:
        out = dict(row)
        if "watched_in" in out:
            out["watched_in"] = list(out["watched_in"])
        out["scope"] = blind_spot_scope(row, self_app)
        rows.append(out)
    return rows
