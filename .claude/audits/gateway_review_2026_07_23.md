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
| A | `message_queue.py:get_stats` | `"failed"` overridden with `COUNT(status='failed')`, structurally always 0 (failures go straight to DEAD_LETTER) → masked the cumulative counter that `prometheus_exporter.py:347` / `influxdb_exporter.py:392` export → "0 failures forever" while dead-lettering. | Removed the override; `**self._stats` provides the real cumulative counter. |
| C | `node_models.py:from_meshtastic` | Hard-set `last_seen=now()/is_online=True`, ignoring the packet's real `lastHeard`; `_merge_node`'s staleness guard keys off `new.last_seen` so it was dead code → every historically-known node in the swept interface DB read "online / 0s ago" across get_online_nodes/get_stats/to_geojson. | Honour `lastHeard` (fall back to `now()` for a live packet with none); future-skew guard; `is_online` from age vs a test-pinned threshold. |
| D | `bridge_health.py` | `_uptime_seconds`/`_subsystem_states` omitted `"meshcore"` while `_connected`/`_connection_count`/`_enabled` included it → a meshcore disconnect raised KeyError mid-lock, leaving the bridge's view stuck "connected" (checklist #5 hardcode drift). | Connection-tracking trio derived from one `_services` tuple; unknown service self-registers with a warning; `_subsystem_states` left meshtastic/rns-only (already guarded; public surface). |
| I | `node_tracker.py:_merge_node` | Adopted a self-reported name but never set `name_is_self_reported` → serialized/API name carried a lying provenance flag (mirror of the 07-21 name-healing fix, checklist #3). | Propagate the provenance flag with the value. |

## Queued — CONFIRMED/PLAUSIBLE, not fixed this pass (ranked)

Deferred deliberately: each is either a moderate change deserving its own
focused pass, a design decision, or lower severity. Next gateway pass starts here.

| Pri | File:line | Sev | Status | Defect (one line) |
|-----|-----------|-----|--------|-------------------|
| 1 | `gateway_heartbeat.py:724,737,536` | high | CONFIRMED | Peer-liveness aging uses `time.time()` not `monotonic()` → an NTP forward step forges peer-down → **both gateways promote to ACTIVE (split-brain)**; backward step forges liveness → missed failover. Un-ported twin of #74's circuit_breaker monotonic fix. Fix wants a monotonic liveness anchor kept separate from the wall-clock stamp that display/serialization use. Dual-gateway failover is Beta (needs dual meshtasticd) — real but not yet field-critical. |
| 2 | `meshtastic_broadcast_bridge.py:909` | high | CONFIRMED | `mark_delivered()` called right after `handle_outbound()`, which only *queues* the LXM — a dead subscriber reads HEALTHY with fresh `last_delivery` forever; STALE/DEAD tiers unreachable via the send path. **Design change** (wire `on_delivered`/`on_failed` to the health machinery) with a real risk of false-DEAD on legitimately ack-less broadcast — needs design, not a quick patch. |
| 3 | `node_tracker.py:818` | med | CONFIRMED | On cache load `is_online=False` but the state machine is restored to persisted `ONLINE` → `node.state=="ONLINE"` contradicts `is_online==False`; the roundtrip test pops `state` as "derived from is_online", a justification that's false when a machine is present. |
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

## Twin note (MeshAnchor)

`bridge_health.py`, `node_models.py`, `node_tracker.py`, `message_queue.py` are
MF↔MA twins (some in the untracked-diverged tier per `reference_repo_twin_map`).
Finding **D especially** matters more on MeshAnchor (MeshCore is its *primary*
radio). The four fixes here should be checked against MA and ported where the
twin shares the defect — verify each at the source (MA may lack or differ).

## Clean bill

`delivery_counters.py`, `circuit_breaker.py`, `canonical_message.py`,
`node_state.py`, `contact_mapping.py`, `correlation_store.py`, `session_store.py`,
`_rns_bridge_connection.py`, `message_routing.py`, `message_queue_models.py`,
`message_queue_lifecycle_mixin.py`, and the reviewed hardened paths of
`rns_bridge.py`/`_rns_bridge_xform.py` were traced to ground and found correct
for this defect class (monotonic elapsed math, tri-stated unobservables, witnessed
swallows, field-complete round-trip guards).
