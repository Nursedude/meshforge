# moc-lf Visibility Dig — Session Record (2026-06-03, afternoon)

> Continuation of the Theme-A radio-smoke agenda ("moc does not get messages on
> the meshforge channel from the bot or other nodes; volcanoai nomadnet no bot
> output"). Operator steer: "start with moc lf — this is the node I'm using."
> All journal forensics on moc/moc3; four fixes shipped same session
> (`6253d53` / `0ce2a65` / `1a34699` + fleet config edits).

## The answer to "moc doesn't hear the gang"

**moc lf (`!32962f10`, owner "meshforge moc", the MeshAdv HAT) is the
SPEAKER of all bridged content, not a listener.** Nothing from the bot or
meshanchor reaches moc as RF RX (known partition; moc's mosquitto is
localhost-only, no broker bridging). Everything arrives via RNS/LXMF and is
re-transmitted BY moc lf itself on ch2 — verified live (wx replies ids
2708786-88, `busyRx` congestion but real TX). A client UI on moc lf renders
genuine RF RX (B424) but not the gateway's API-originated self-TX, so the
gang is invisible *on that node* while every OTHER radio in range (B424)
receives it all as normal RX. The NOC message store (`commands.messaging`,
"Stored message NNNN") carries every message — the in-app view is the
listener surface on moc.

**Topology decode**: `!a2e95ba4` = the BOT's radio node id, RF-local to
**moc3** — moc3 is the bot-site box and bridges all bot/far-site traffic
M→R. The `[RNS:a2e95ba4]` tag seen on moc is *originator attribution*
(9554f06), not a gateway hash. (Fleet-gateway-hashes memory updated mentally:
tags carry mesh originators, not gateway identities.)

## Fix 1 — bot → NomadNet (config, FIELD-VERIFIED same hour)

"nomad no bot output" was **not** the loop guard (that's correct behavior) —
it was moc3's fan-out: 4 legacy hashes + moc, **no operator nomad**. The
nomad hash was added to moc during the smoke session (finding #2) but the bot's
content is bridged by moc3. Added `9217147e24d1640d204dde3f413eb521` to
moc3's `rns.default_lxmf_destination` → restart. **Proof at 12:05:36**: bot
sent `@RNSP 🎙Testing [RF]` → moc3 `6/6 dest(s)` incl. `9217147e`,
`handle_outbound ok`, path 0 hops. Bot output + bot-site chatter now land in
the operator's NomadNet inbox directly.

## Fix 2 — http_port 443→9443 (`6253d53`)

`config.py` default was 443; baked into every rendered fleet gateway.json
(moc had it 1×, moc3 3× incl. mesh_bridge primary/secondary, moc1/moc2 1×
each). meshtasticd's web API is :9443 → the primary stateless TX path was
DEAD fleet-wide (connection refused; moc's circuit breaker flapped open all
day, 121 skips/6h) and every send rode the legacy session fallback (#17
contention class). #62 pattern: default bump + load-time migration of saved
443 + all fleet configs edited live. Verified: 0 circuit-open since the moc
restart; sends log "stateless HTTP protobuf".

## Fix 3 — node-tracker sender→from (`0ce2a65`)

`_update_nodeinfo/_update_position/_update_telemetry/_update_node_from_mqtt`
keyed on `sender` (the MQTT uplinker — ALWAYS the box's own radio on a
localhost broker). Every heard node's nodeinfo/position was written onto
`!32962f10`'s tracker entry: name churned to whichever node was heard last
(the "hawaii_gaz" phantom — moc's own M→R attributions displayed a foreign
node's name), positions teleported between islands. New `_originator_id()`
helper (from → fallback sender), 5 tests.

## Fix 4 — loop-guard echo amplifier (`1a34699`)

`is_already_bridged` knew only `[RNS:` (MeshCore tags deliberately excluded —
scoping disproven live): at 11:23 bare `[MC:p4] hey you` entered the mesh
through moc lf itself and was re-bridged M→R to the full 6-dest fan-out AND
Phase-1 relayed to 2 peer gateways. New `BRIDGE_TAG_PREFIXES` mirrors
MeshAnchor's `ECHO_LOOP_INVARIANT_PREFIXES` exactly, with a parity-pin test.
(The injector of the bare tag is some second injection path — likely the
broadcast-subscription leg; the guard now makes it loop-safe regardless.)

## Port debt → MeshAnchor (lead-repo rule)

MeshAnchor has BOTH sibling bugs live: `http_port: int = 443`
(`src/gateway/config.py:225`) and sender-keyed `_update_nodeinfo`/
`_update_position` (`src/gateway/mqtt_bridge_handler.py`). Its prefix list
is already correct (it's the source of ours). `scripts/parity_check.py`: in
sync (tracked files unaffected).

## Still open (carry-forward)

- **Leg-B close-range DM** (operator), relay `…24` identification, ghost
  `!a2ebdd94` cleanup — unchanged from the smoke record.
- **Fan-out audit**: the 4 legacy hashes (`522c4ac1`, `d1df31d3`,
  `6b1a0120`, `7cda0fab`) appear in BOTH moc and moc3 fan-outs — prune pass
  pending.
- **meshtasticd instability worsening**: 3 crashes today (09:31 ABRT,
  11:16 ×2 `result=signal`).
- **meshforge.service crash-looping on moc** (auto-restart, observed in
  service list — separate from the gateway, undiagnosed).
- Verification asks for the operator: nomad inbox shows `@RNSP 🎙Testing
  [RF]` (12:05 HST); B424's meshforge channel shows the full gang.

Tests: 5659 passed, 1 skipped; lint --all exit 0; fleet_sync 5 ok; both
gateways restarted on new code, clean.
