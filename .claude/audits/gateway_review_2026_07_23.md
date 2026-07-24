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
| ~~4~~ | `gateway_heartbeat.py` `_events` | med | **FIXED 2026-07-23** | Unlocked multi-thread `_events` append/re-slice/read — see fixed section below. |
| ~~5~~ | `mqtt_bridge_handler.py:560` | med | **FIXED 2026-07-23** | Wildcard-subscription foreign-channel traffic forged the bridge-channel liveness heartbeat — see fixed section below. |
| ~~6~~ | `reconnect.py:316,292` | med | **FIXED 2026-07-23** | `SlowStartRecovery` ramp on wall-clock — monotonic anchor, both twins. See fixed section below. |
| ~~7~~ | `message_queue.py:924` | med | **FIXED 2026-07-23** (was PLAUSIBLE → CONFIRMED) | Post-send `mark_delivered` DB error re-queued an already-sent message — duplicate on the wire. See fixed section below. |
| ~~8~~ | `message_queue.py:1110` | med-low | **FIXED 2026-07-23** | `cleanup_stale` reset `in_progress→pending` without bumping `retry_count` → endless retry, never dead-letters. See fixed section below. |
| ~~9~~ | `message_queue.py` | low | **FIXED 2026-07-23** | retry_after wall-clock strand — get_pending ceiling release. Both twins. |
| ~~10~~ | `bridge_health.py` | low | **FIXED 2026-07-23** | uptime wall-clock deltas — clamped ≥0, percent bounded. Both twins. |
| ~~11~~ | `bounded_rpc.py` | low | **FIXED 2026-07-23** (MF-only) | `_outstanding_wedges` count/finally race — `completed` flag. No MA twin (no bounded_rpc.py). |
| ~~12~~ | `meshtastic_broadcast_bridge.py` (`_msg_content`) | low | **FIXED 2026-07-23** | bytes `content` → str normalization. MA twin `lxmf_broadcast_bridge.py` shared it, fixed too. |
| ~~13~~ | `radio_failover.py:542` | med | **FIXED 2026-07-23** | Whole failover state machine on wall-clock — monotonic anchors, both twins. See fixed section below. |
| ~~—~~ | `node_models.py` (`meshcore:` factory) | low | **FIXED 2026-07-23** (was UNVERIFIED → CONFIRMED-latent) | Finding-C sibling: honour a heard-time (contact-data `last_seen`), else now()/True. Both twins. See section below. |

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

## Pri-4 (finding 4) — RESOLVED 2026-07-23 (both twins)

**Defect**: `_events` (the promotion/demotion/peer-up/down audit log) is written
from ≥3 threads — the MQTT callback thread (`_handle_peer_status`→`_handle_peer_down`,
`_handle_peer_recovered`), the per-detection daemon threads `_monitor_loop`
spawns (`_handle_peer_down_safe`), and `_record_promotion`/`_record_demotion` —
and read by the TUI's `get_status`, all with **no lock**. The
`self._events = self._events[-max:]` truncation is a read-modify-write: a
concurrent append to the old list is dropped when another thread rebinds to a
slice of the pre-append list, so `get_status` can serve an audit log that lost a
real failover event (honest_failure_modes #9 — a swallowed event with no witness).

**Fix**: a single writer path `_append_event()` holds a new leaf lock
`_events_lock` across the append AND the truncating rebind; all four call sites
route through it; `get_status` snapshots the last-10 under the same lock.
`_events_lock` is a **leaf** — never held while acquiring another lock (the two
callers already holding `_state_lock` nest `_state_lock → _events_lock`
consistently), so no deadlock. Tests: `TestEventHistoryThreadSafety` — a
bounds/ordering unit test + a 12-writer × 200-append concurrency test with a
concurrent `get_status` reader asserting the list settles at exactly the cap
with no lost/torn entries. MF + MA heartbeat suites 42 pass each.

## Pri-5 (finding 5) — RESOLVED 2026-07-23 (MeshForge-only)

**Defect**: `_last_uplink_at` (the "bridge channel alive" heartbeat that
`_channel_diagnostic_loop` watches to catch a dark bridge channel) was stamped
in `_handle_json_message` on *any* well-formed JSON arrival, before the
channel-scope filter. When the DM-to-gateway leg is armed (`sessions_enabled`),
the json subscription widens to a `+` wildcard, so foreign-channel traffic (the
primary, which carries DMs) also reaches `_handle_json_message` and refreshed
the heartbeat — masking exactly the dark bridge channel this signal exists to
surface (honest_failure_modes #2: absence of bridge-channel traffic read as
presence).

**Fix**: new `_counts_as_bridge_uplink(topic)` gates the stamp. Leg dormant
(scoped subscription) → passes unchanged, every arrival is already on-channel.
Leg armed (wildcard) → a *confidently foreign* channel is excluded, while an
unparseable topic or unconfigured channel is held as counting so ambiguity never
manufactures a false "dark". The Hardening-A intent (non-text nodeinfo/telemetry/
position broadcasts still count as deployment evidence) is preserved — only the
channel scope changed. Tests: `TestBridgeUplinkHeartbeatChannelScope` (8) — 5
decision-helper cases + 3 through `_handle_json_message` including the regression
guard (foreign-channel arrival under the wildcard leaves `_last_uplink_at` None).
MF `test_mqtt_bridge_handler.py` 91 pass, lint exit 0.

**No MA twin port**: MeshAnchor's `mqtt_bridge_handler.py` has no
`_last_uplink_at`, no DM-to-gateway wildcard, and always subscribes
channel-scoped (`.../json/{channel}/#`) — the heartbeat + wildcard + channel
diagnostic loop are MeshForge-only. Verified at the source; nothing to port.

## Pri-6 (finding 6) — RESOLVED 2026-07-23 (both twins)

**Defect**: `SlowStartRecovery` (post-reconnect throughput ramp, NGINX
slow-start pattern) anchored `_recovery_start` with `time.time()` and computed
every elapsed via `time.time() - _recovery_start` (5 sites). A slow-start ramp
is a **duration**, and wall-clock durations are forgeable on the fleet's
RTC-less Pis (honest_failure_modes #6, the #74 class). An NTP **forward** step
jumped elapsed past `slow_start_seconds` → `get_throughput_multiplier` returned
`max_multiplier` early → full throughput blasted at a just-recovered radio (the
RATE_LIMIT burst this class exists to prevent). A **backward** step drove
elapsed negative → `progress<0` → sub-`min_multiplier`/negative multiplier and
`is_recovering()` stuck True.

**Fix**: `_recovery_start` is now a `time.monotonic()` anchor; all five elapsed
computations use `time.monotonic()`. Monotonic never steps or reverses, so the
ramp is immune by construction; elapsed is always ≥ 0 so the multiplier stays in
`[min, max)`. In-memory only (never persisted/serialized), so no cross-restart
concern. Un-ported sibling of Pri-1's `gateway_heartbeat` monotonic fix.

Tests: `TestSlowStartMonotonicClockSteps` (3) — forward step doesn't complete
early, backward step keeps the multiplier bounded, and completion is driven by
real monotonic elapsed with the wall clock frozen. A pre-existing
`test_zero_multiplier_safety_cap` that seeded `_recovery_start = time.time()`
(now a clock mismatch against the monotonic reader) was corrected to
`time.monotonic()`. MF `test_reconnect.py` 48 pass; MA the same 48 pass.

**MA twin ported**: `reconnect.py` `SlowStartRecovery` is a mirror (byte-close,
not byte-locked — line numbers differ) and carried the identical 5-site defect.
Fixed identically in both repos, tests ported verbatim.

## Pri-7 (finding 7) — RESOLVED 2026-07-23 (both twins; PLAUSIBLE → CONFIRMED)

**Confirmed at source**: `_get_connection` re-raises after rollback
(`raise  # Re-raise after rollback`), so a `mark_delivered` DB error propagates
out of `process_once`'s `if success:` block into the outer `except`, which calls
`mark_failed` → retry → the message is re-dispatched by the next `process_once`
with **no dedup re-check** — a guaranteed duplicate on the wire, recorded with a
DB-error `error_message` indistinguishable from a real send failure. The trigger
(disk I/O / readonly-DB errors on SD-card Pis) is real and fleet-observed — the
same failure the delivery_counters preflight logs surface.

**Fix**: once `send_fn` returns True the payload is on the wire — a point of no
return. The `mark_delivered` + success-callback bookkeeping is now wrapped in its
own `try/except` so its errors can never reach the outer `except`/`mark_failed`.
On a bookkeeping failure the row is **left in_progress** (`get_pending` selects
only `pending`, so it is NOT re-dispatched immediately — no duplicate) and a
distinct witness is surfaced: a new `stats.delivered_unrecorded` counter
(honest_failure_modes #9) plus a loud ERROR naming it "SENT but mark_delivered
failed". Success callbacks fire only when `mark_delivered` didn't raise (no false
success). The `in_progress`→`cleanup_stale` re-send path is Pri-8's separate
concern, tracked there.

Tests: `TestDeliveredBookkeepingFailurePri7` (4) — no re-send on the second pass,
witness increments while failed/dead_letter stay 0 and the row is in_progress,
success callbacks skipped, happy path unaffected. MF `test_message_queue.py` 118
pass; MA the same 111 pass. `message_queue.py` is an untracked-diverged twin —
verified MA carried the byte-identical defect and fixed it identically (MA's
`enqueue` rejects senderless destinations, Issue #67, but the test registers the
sender first, so the pattern ports cleanly).

## Pri-8 (finding 8) — RESOLVED 2026-07-23 (both twins)

**Defect**: `cleanup_stale` reset stale `in_progress` rows to `pending` with a
bare `UPDATE` that never touched `retry_count`. A dispatch that wedges past
STALE_TIMEOUT on *every* attempt (a permanently stuck `send_fn`, a crash-loop,
or — after Pri-7 — a delivered-but-unrecorded row parked in_progress) was reset
to pending endlessly: retried forever, never reaching `max_retries`, never
dead-lettered, with no per-message witness (honest_failure_modes: an unbounded
loop mapped to a valid-looking `pending`, no terminal state).

**Fix**: each stale reset now **bumps `retry_count`**, and a row that has
reached `max_retries` is moved to `dead_letter` (terminal) with a witness —
`_dc.record(DROPPED, RETRIES_EXHAUSTED)` + `stats.failed` + a
`STALE_RESET_ERROR` `error_message` tag — instead of being reset again. Two
scoped UPDATEs in one transaction (dead-letter the exhausted first, then reset
the rest), so it stays a bulk op. `retry_after` is deliberately **not** set: a
recovered stale message is immediately re-eligible (a crashed attempt shouldn't
also serve a backoff), which preserves the existing reset-to-pending contract
and every prior cleanup_stale test. Closes the Pri-7 residual too: a
delivered-but-unrecorded row is now bounded (re-sent at most `max_retries` times
then dead-lettered, not looping) — the best achievable while the bookkeeping DB
is degraded.

Tests: `TestCleanupStaleBoundsRetriesPri8` (4) — reset bumps retry_count + stamps
the reason and stays immediately pending, a perpetually-wedging message
dead-letters after max_retries instead of looping, the terminal drop increments
the witness, and a fresh in_progress is untouched. MF `test_message_queue.py`
198 pass (incl. all pre-existing cleanup_stale tests); MA the same 176 pass.
Byte-identical twin defect, fixed identically.

## Pri-13 (finding 13) — RESOLVED 2026-07-23 (both twins)

**Defect**: the dual-radio failover state machine ran every timing decision on
`now = time.time()` — the split-brain risk class (like Pri-1). Wall-clock is
forgeable on RTC-less Pis (honest_failure_modes #6): an NTP **forward** step
could jump `now - _recovery_start` past `recovery_duration` and force an early
**switch-back** to a primary that isn't actually stable (split-brain), or jump
`now - _overload_start` past `utilization_duration` to **forge a failover**; the
per-hour rate windows (`_failover_count_window`, `_restart_timestamps`), the
cooldown gate (`_last_state_change`), the restart cooldown
(`_last_restart_attempt`), and the LB slow-start ramp (`_failover_recovery_at`,
`:1113`) were all equally forgeable.

**Fix**: every duration anchor now measures on `time.monotonic()`. Because all
anchors are stamped from and compared to the three `now` sources
(`_track_reachability`, `_run_watchdog`, `_evaluate_state`) plus the
`_transition` stamp and the class-2 `_failover_recovery_at` stamp/read pair,
converting those six sites makes the whole machine internally consistent on
monotonic. **Only genuine durations changed** — the absolute *display*
timestamps stay wall-clock: `radio.last_check` (write-only for logic) and the
class-2 `_last_state_change` stamp (never read as a duration), plus the
`datetime.now()` log lines. Two boot-edge guards added: the `0.0`-init cooldown
sentinels (`_last_state_change`, `_last_restart_attempt`) are now `> 0`-guarded
so monotonic-`now` (= uptime) can't spuriously gate for the first cooldown
seconds after boot — preserving the pre-fix "never-transitioned = no cooldown"
behavior.

Tests: `TestFailoverMonotonicClockSteps` (3: switch-back not forged early,
failover not forged, boot-cooldown guard) + `TestLBSlowStartMonotonicClockSteps`
(1: ramp not completed early). Existing tests that seeded anchors with
`time.time() - N` were corrected to `time.monotonic() - N` (the same clock-
mismatch class as Pri-6's `test_zero_multiplier_safety_cap`): MF
`test_radio_failover.py` (24) + `test_failover_lb_coordination.py` (19); MA the
same 26 + 19 = 45. Byte-identical twin, fixed identically.

## Pri-9 / 10 / 11 / 12 (findings 9–12) — RESOLVED 2026-07-23 (low-severity tail)

- **Pri-9** (`message_queue.py`, both twins): `retry_after` is a wall-clock
  schedule; a large **backward** NTP step after scheduling would strand a queued
  message far in the future. Fix: `get_pending` also releases rows whose
  `retry_after` exceeds `now + RETRY_AFTER_CEILING_S` (3600s) — beyond any
  legitimate backoff, so it can only be a clock artifact (hfm #6 absurd-delta
  release, not a reschedule rework). Tests: `TestRetryAfterCeilingPri9` (2).
- **Pri-10** (`bridge_health.py`, both twins): uptime accounting uses wall-clock
  deltas; a backward step could subtract from accumulated uptime. Fix: clamp
  each delta `max(0.0, now - connected_at)` and the percent
  `max(0.0, min(100.0, …))`. Kept wall-clock (display-only, `_last_connected`
  is also a shown absolute timestamp) — a clamp, not a monotonic rework.
  Tests: `TestUptimeClockStepClampPri10` (1).
- **Pri-11** (`bounded_rpc.py`, **MF-only** — MA has no such file): the
  `_outstanding_wedges` gauge leaked +1 when the watchdog counted a wedge
  (`exit_on_wedge=False`) in the window after the wrapped fn already returned —
  the finally keyed its decrement off `fired["wedge"]` (set early) but the
  increment landed later. Fix: a lock-protected `completed` flag — the watchdog
  skips the increment if the call already completed, and the finally always
  marks completed and decrements only if counted. Net 0, no leak. Tests:
  `TestOutstandingWedgeGaugePri11` (1, real watchdog-fire + late return).
- **Pri-12** (`meshtastic_broadcast_bridge.py` `_msg_content`, both twins):
  `CanonicalMessage`'s bytes `content` shape flowed unnormalized into str ops →
  `TypeError` swallowed → silent fan-out failure. Fix: `_msg_content` decodes
  bytes→str (mirroring the already-correct inbound `_on_lxmf_delivery` path).
  MA's `lxmf_broadcast_bridge.py` shared the defect inline (no helper) — added
  the same `_msg_content` helper and routed the format + synth-ACK-guard sites
  through it. Tests: `TestMsgContentBytesNormalizationPri12` (2 each twin).

**This closes the entire ranked queue** — Pri-1 through 13 all resolved, plus
the finding-C `meshcore:` factory sibling (below). Nothing outstanding.

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

## `node_models.py` `meshcore:` factory (finding-C sibling) — RESOLVED 2026-07-23 (both twins)

**Verified before fixing** (was UNVERIFIED): `UnifiedNode.from_meshcore` hard-set
`last_seen=now()/is_online=True` (1042-1043), the same shape as finding C's
`from_meshtastic`. Its docstring accepts BOTH a live advertisement (heard now →
correct) AND stored contact data. Confirmed a heard-time field **exists** — the
MeshCore contact shape carries `last_seen` (the handler's `get_contacts()` /
sim `_generate_fake_contacts`) — and confirmed `UnifiedNode.from_meshcore` has
**no production caller today** on either repo (only `CanonicalMessage.from_meshcore`
does), so the defect is CONFIRMED-**latent**: it fires the moment a contact-sweep
caller exists, mass-marking stale contacts "online / 0s ago" (honest_failure_modes
#1). Matters more on MeshAnchor (MeshCore-primary; `get_contacts()` is a ready
sweep source).

**Fix**: new shared `UnifiedNode._resolve_heard(heard)` — `None` → (now, True)
for a live event; a `datetime` or epoch heard-time is honoured, deriving
`is_online` from age vs the single NOC online window (`MESHTASTIC_ONLINE_THRESHOLD_S`,
test-pinned to `NodeTracker.OFFLINE_THRESHOLD`, hfm #5), with the same future-skew
guard as `from_meshtastic`. `from_meshcore` reads `last_seen`/`last_heard` off the
advertisement/contact and routes through it. Tests: `TestMeshcoreHeardTime` (5) —
live advert online-now, stale contact offline, recent online, epoch honoured,
future stamp not "online forever". MF `test_node_tracker.py` 104 pass; MA 87 pass.
Byte-identical twin defect, fixed identically.

## Clean bill

`delivery_counters.py`, `circuit_breaker.py`, `canonical_message.py`,
`node_state.py`, `contact_mapping.py`, `correlation_store.py`, `session_store.py`,
`_rns_bridge_connection.py`, `message_routing.py`, `message_queue_models.py`,
`message_queue_lifecycle_mixin.py`, and the reviewed hardened paths of
`rns_bridge.py`/`_rns_bridge_xform.py` were traced to ground and found correct
for this defect class (monotonic elapsed math, tri-stated unobservables, witnessed
swallows, field-complete round-trip guards).
