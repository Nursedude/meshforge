# Gateway adversarial review — 2026-07-23

**Scope**: `src/gateway/*` (~30k lines, ~50 files), honest-failure-modes lens.
**Mechanism**: 4 parallel code-reviewers over four clusters (claim-producers,
node-state/models, queue/routing, bridges/transforms), then per-finding
adversarial verification against the actual consumer before claiming.
**Model tier**: frontier (per model_advisor — review-shaped work).

Every finding below is the same defect class: **a degraded/omitted internal
state maps to a valid-looking value that becomes a real-world claim.**

## Fixed this pass (CONFIRMED, regression-pinned)

Tests: `tests/test_gateway_honest_failure_fixes_2026_07_23.py` (12).

| # | File | Defect | Fix |
|---|------|--------|-----|
| **Pri-1** | `gateway_heartbeat.py:_check_peers` | Peer-liveness aging on wall-clock `time.time()` → an NTP forward step forged peer-down → a SECONDARY promoted to ACTIVE while PRIMARY was alive (**split-brain**); backward step forged liveness (missed failover). Un-ported twin of #74's monotonic fix. | **Fixed 2026-07-23** (follow-on commit). Added a `PeerInfo.last_heartbeat_mono` anchor; `_check_peers` + the reconnect-grace both measure on `time.monotonic()`; wall-clock `last_heartbeat` kept for TUI display only. Pins: `tests/test_gateway_heartbeat.py::TestClockStepDoesNotSplitBrain` (3) + 5 existing tests moved to the monotonic field. |
| A | `message_queue.py:get_stats` | `"failed"` overridden with `COUNT(status='failed')`, structurally always 0 (failures go straight to DEAD_LETTER) → masked the cumulative counter that `prometheus_exporter.py:347` / `influxdb_exporter.py:392` export → "0 failures forever" while dead-lettering. | Removed the override; `**self._stats` provides the real cumulative counter. |
| C | `node_models.py:from_meshtastic` | Hard-set `last_seen=now()/is_online=True`, ignoring the packet's real `lastHeard`; `_merge_node`'s staleness guard keys off `new.last_seen` so it was dead code → every historically-known node in the swept interface DB read "online / 0s ago" across get_online_nodes/get_stats/to_geojson. | Honour `lastHeard` (fall back to `now()` for a live packet with none); future-skew guard; `is_online` from age vs a test-pinned threshold. |
| D | `bridge_health.py` | `_uptime_seconds`/`_subsystem_states` omitted `"meshcore"` while `_connected`/`_connection_count`/`_enabled` included it → a meshcore disconnect raised KeyError mid-lock, leaving the bridge's view stuck "connected" (checklist #5 hardcode drift). | Connection-tracking trio derived from one `_services` tuple; unknown service self-registers with a warning; `_subsystem_states` left meshtastic/rns-only (already guarded; public surface). |
| I | `node_tracker.py:_merge_node` | Adopted a self-reported name but never set `name_is_self_reported` → serialized/API name carried a lying provenance flag (mirror of the 07-21 name-healing fix, checklist #3). | Propagate the provenance flag with the value. |

## Queued — CONFIRMED/PLAUSIBLE, not fixed this pass (ranked)

Deferred deliberately: each is either a moderate change deserving its own
focused pass, a design decision, or lower severity. Next gateway pass starts here.

| Pri | File:line | Sev | Status | Defect (one line) |
|-----|-----------|-----|--------|-------------------|
| ~~1~~ | ~~`gateway_heartbeat.py`~~ | high | **FIXED 2026-07-23** | Split-brain via wall-clock peer-liveness — moved to the fixed table above. |
| ~~2~~ | `meshtastic_broadcast_bridge.py` | high | **FIXED 2026-07-23** | Enqueue-read-as-delivery — moved to the fixed section below. |
| ~~3~~ | `node_tracker.py` (cache load) | med | **FIXED 2026-07-23** | Persisted-active-state contradicts forced `is_online=False` on reload — see fixed section below. |
| 4 | `gateway_heartbeat.py:521,563,604,630` | med | CONFIRMED | `_events` list appended/re-sliced from multiple threads with no lock → a real promotion/demotion audit event can be lost; `get_status` then serves an events list that lies about what happened. |
| 5 | `mqtt_bridge_handler.py:560` | med | CONFIRMED (opt-in) | Under `sessions_enabled` the JSON subscription is a `+` wildcard, but `_last_uplink_at` is stamped before the channel-scope filter → foreign-channel traffic refreshes the "bridge channel alive" heartbeat, masking a dark bridge channel. Fix: stamp only after channel match. |
| 6 | `reconnect.py:316,292` | med | PLAUSIBLE | `SlowStartRecovery` ramp measured with `time.time()` → NTP forward step ends slow-start early and blasts a just-recovered radio (the RATE_LIMIT burst class); backward step → negative multiplier / stuck recovering. |
| 7 | `message_queue.py:924` | med | PLAUSIBLE | A send that reaches the network followed by a failing `mark_delivered` (DB error) falls to the retry path with no dedup re-check → guaranteed duplicate on the wire, no witness distinguishing it from a real transient. |
| 8 | `message_queue.py:1110` | med-low | PLAUSIBLE | `cleanup_stale` resets `in_progress→pending` without bumping `retry_count` → a send_fn that wedges past STALE_TIMEOUT every attempt retries forever, never dead-letters, no per-message witness. |
| 9 | `message_queue.py:580,613` | low | PLAUSIBLE | `retry_after` scheduling/comparison via `datetime.now()` (wall clock) → NTP steps skew retry timing; queued-item "age" forgeable. Fix is a delay clamp, not a rework. |
| 10 | `bridge_health.py:418,261` | low | PLAUSIBLE | `_uptime_seconds` accumulation + `get_uptime_percent` use wall-clock deltas → NTP steps make uptime% arbitrarily wrong. Display/summary only (bounded 0–100), not a control decision. |
| 11 | `bounded_rpc.py:242,260` | low | CONFIRMED | Flag-ordering race leaks `_outstanding_wedges` +1 in the abort-suppressed path (`NO_EXIT`/`exit_on_wedge=False`) → phantom "wedged threads in flight" gauge. Only reachable when the abort backstop is engaged (not steady state). |
| 12 | `meshtastic_broadcast_bridge.py:408` | low | PLAUSIBLE | No bytes→str normalization on the inbound broadcast path; latent until a `CanonicalMessage` with bytes `content` (advertised second shape) flows through → TypeError swallowed as generic callback error, message silently fails to fan out. One-line decode fix. |
| — | `node_models.py:~1012` (`meshcore:` factory) | ? | UNVERIFIED | Sibling of finding C (also hard-sets `now()/True`); MeshCore's heard-time field not confirmed. Check next pass. |

## Pri-2 (finding 2) — RESOLVED 2026-07-23 (this session, both twins)

**Fix landed** in `meshtastic_broadcast_bridge.py` (MF) + `lxmf_broadcast_bridge.py`
(MA — see twin note; both shared the defect). Regression-pinned: MF
`tests/test_meshtastic_broadcast_bridge.py` (58, +14 this pass), MA
`tests/test_lxmf_broadcast_bridge.py` (90). Both suites + ack-first-wins green,
both repos lint exit 0, db_audit clean.

**Design chosen** — separate the two facts the code conflated, mirroring #74's
`compute_confirmation_view` confirmable-population framing (handoff options a+c;
option b deferred):
- `handle_outbound` return → `mark_fanout_enqueued` (stamps `last_delivery` for
  display **only**; no health/failure reset — an enqueue is not a delivery).
- async LXMF `on_delivered` with `state==DELIVERED` → new `mark_confirmed`
  (stamps new `last_confirmed_at`, resets failures, → HEALTHY — the ONLY path
  to HEALTHY). `state==SENT` (propagation-node hand-off, **not** a recipient
  proof) is held, not confirmed. The callback reads `lxm.state` to tell them
  apart — LXMF fires the same callback for both (`LXMessage.__mark_delivered`
  vs `__mark_propagated`).
- async `on_failed` (LXMF gave up, `fail_message`) → `mark_failed`.
- New `STATE_UNCONFIRMED`; `desired_state` gates STALE/DEAD on
  `last_confirmed_at is not None`, so a never-confirmed subscriber can **never**
  read HEALTHY (kills the false-HEALTHY) **and never auto-DEAD** (neutralizes
  the false-DEAD risk by construction — hold-and-surface, hfm #2). New
  subscribers are born UNCONFIRMED. `get_status` surfaces
  `delivery_confirmation.unconfirmed_subscribers` as a visible blind spot and
  reports LIVE-derived state so a stale stored value can't lie (hfm #5).

**Why option (b) — independent announce-liveness — was deferred**: the default
deployment uses direct/opportunistic delivery (`propagation_node=""`), where
real per-recipient LXMF proofs already exist, so (b) is an unneeded enrichment.
Queued as a follow-on only if a prop-node-heavy box makes the UNCONFIRMED
bucket uninformative.

### Original handoff context (for reference)

Deferred to a fresh session on purpose (2026-07-23): it is a **delivery-semantics
design decision**, not a wiring change, and getting it wrong marks a live-but-quiet
subscriber DEAD — the exact honest-failure-mode this review fights. Start with a
design pass, not code. What the review established:

- **Defect**: `meshtastic_broadcast_bridge.py:~909` calls `mark_delivered()`
  (resets `consecutive_failures=0`, `state=HEALTHY`, stamps `last_delivery=now`)
  immediately after `self._router.handle_outbound(lxm)` **returns** — but
  `handle_outbound` only *queues* the LXM; it does not confirm the subscriber
  received it. So `SubscriberStore.desired_state`'s STALE(24h)/DEAD(7d) tiers are
  unreachable via the send path, and `get_status()` reports a dead subscriber as
  HEALTHY with a fresh `last_delivery` forever.
- **Real delivery signal EXISTS but is decoupled**: `on_delivered`/`on_failed`
  LXMF callbacks are registered per-message (~lines 884–901) **only when
  `ack_msg_id` is set (ack_required)**, and they only drive ACK synthesis
  (`emit(...)`) — neither touches `mark_delivered`/`mark_failed`. `mark_failed`
  fires only on `invalid_hash`/`identity_recall_null`/a synchronous
  `handle_outbound` exception.

**The design question to answer FIRST (do not skip to code):** a *broadcast*
fan-out is not inherently ACK'd, so "delivered" may have no confirmation to wait
for. Options to weigh: (a) only advance HEALTHY/last_delivery on an actual
`on_delivered` callback, and treat "sent, no callback" as a distinct
*unconfirmed* state (not HEALTHY, not DEAD) — mirrors the #74 `unconfirmable_sent`
honesty; (b) drive the STALE/DEAD tiers off an independent liveness signal
(directed probe / announce-heard) rather than the send path at all; (c) hybrid.
Whatever is chosen, the invariant: **absence of a delivery callback must never
read as HEALTHY, and must never by itself read as DEAD** (hold-and-surface, per
honest_failure_modes #2). Cross-check against how the gateway's #74
`compute_confirmation_view` already handles the confirmable-vs-unconfirmable split
— reuse that framing, don't reinvent it.

**Files to deep-read fresh** (a subagent read these during the review; this
session did not hold them): `meshtastic_broadcast_bridge.py` (send path + callback
registration + SubscriberStore), `delivery_counters.py` `compute_confirmation_view`
(the #74 precedent to mirror). Also confirm the MA twin (`meshanchor` is
MeshCore-primary — broadcast delivery matters more there).

## Pri-3 (finding 3) — RESOLVED 2026-07-23 (both twins)

**Defect**: `_load_cache` forces `is_online=False` ("not heard since restart")
but then restores the persisted `NodeStateMachine` verbatim via `from_dict` —
including a live `ONLINE`/active state. `UnifiedNode.state` reads from the
machine when present, so a not-yet-heard node reads `state==ONLINE` while
`is_online==False` (honest_failure_modes #2: a persisted value outliving the
observation that justified it). The field-complete round-trip test masked it by
excluding `state` with the false justification "derived from is_online" — which
is untrue when a machine is present.

**Fix**: new `NodeStateMachine.mark_cache_restored()` — called right after
`from_dict` in `_load_cache` — resets **only an active** live-state to
`STALE_CACHE` (the state whose documented meaning is exactly "loaded from cache,
not yet verified"; `is_active()` is False, matching the forced `is_online=False`).
Non-active persisted states (OFFLINE/SUSPECTED_OFFLINE/UNREACHABLE) are already
consistent and preserved as the more-informative label; transition history and
`_last_response` survive; the first live `record_response` re-promotes
STALE_CACHE→ONLINE. The round-trip test's false justification is corrected and a
dedicated invariant test proves an ONLINE→save→reload node comes back
STALE_CACHE with `state.is_active() == is_online` (no contradiction).

Tests: MF `test_node_state.py` (+6 unit) + `test_node_tracker.py` (+2 integration)
= 125 pass; MA the same = 108 pass. Both twins carried the byte-identical defect
(node_tracker/node_state are Tier-3 twins) and are fixed identically.

## Twin note (MeshAnchor)

`bridge_health.py`, `node_models.py`, `node_tracker.py`, `message_queue.py` are
MF↔MA twins (some in the untracked-diverged tier per `reference_repo_twin_map`).
Finding **D especially** matters more on MeshAnchor (MeshCore is its *primary*
radio). The four fixes here should be checked against MA and ported where the
twin shares the defect — verify each at the source (MA may lack or differ).

**Pri-2 twin outcome (2026-07-23)**: the broadcast bridge is a mirror pair —
MF `meshtastic_broadcast_bridge.py` ↔ MA `lxmf_broadcast_bridge.py` (Meshtastic→
LXMF vs MeshCore→LXMF fan-out). The handoff guessed MA might already be fixed,
but MA had only **cosmetically** renamed `mark_delivered`→`mark_fanout_enqueued`
and documented the honesty caveat while its body still reset failures + state to
HEALTHY on enqueue — i.e. **both twins carried the substantive defect**, and MA's
own comment named the real fix as an open TODO ("wire true receipts to a separate
confirmed-delivery field"). The finding-2 fix resolves that TODO and was ported
to MA identically (same `mark_confirmed`/`STATE_UNCONFIRMED`/confirmable-gating
design). The two files are NOT byte-locked twins (separate mirror files, not in
`parity_check`'s set), so the port matched intent, not bytes.

## Clean bill

`delivery_counters.py`, `circuit_breaker.py`, `canonical_message.py`,
`node_state.py`, `contact_mapping.py`, `correlation_store.py`, `session_store.py`,
`_rns_bridge_connection.py`, `message_routing.py`, `message_queue_models.py`,
`message_queue_lifecycle_mixin.py`, and the reviewed hardened paths of
`rns_bridge.py`/`_rns_bridge_xform.py` were traced to ground and found correct
for this defect class (monotonic elapsed math, tri-stated unobservables, witnessed
swallows, field-complete round-trip guards).
