# Gateway Traffic Flow Analysis — moc & moc3

**Date**: 2026-05-08
**Investigator**: Claude (Opus 4.7) on fleet management box
**Scope**: Why bridged traffic through the `meshforge` channel between ShortTurbo (moc3) and LongFast (moc) doesn't flow smoothly. `wh6gxzTRDEV` running `meshing_around_meshforge` produces full output via moc2 but degraded via LongFast peers.

---

## TL;DR

Two independent issues, both real, neither catastrophic:

1. **moc's R→M counter is misleading.** `R→M: 18/3/15 (83% drop)` reads alarming, but the 15 "drops" are LXMF redelivery retries hitting the dedup check — not real losses. Actual unique deliveries on moc match what moc3 forwarded (3 unique LXMFs in the trace window, all reached the radio).
2. **Every R→M on moc fails its first `toradio_put` and retries.** `urlopen error [Errno 111] Connection refused` after 0.002s, then a fresh socket succeeds ~20ms later. moc3 doesn't have this because its busy M→R traffic keeps the protobuf session warm. The first message after startup hit a 4-second cold-cache penalty (protobuf session re-handshake reading 200 nodes); subsequent messages recover in ~20ms.

The cross-preset architecture is **working as designed** — ShortTurbo and LongFast meshes never meet over RF; RNS LXMF is the only crossing point, and it is crossing.

---

## Symptom (operator's report, 2026-05-08)

> Traffic routes from shorturbo <> meshforge <> rns <> meshforge <> longfast - meshforge is the channel that routes all this traffic which does not flow smoothly. wh6gxzTRDEV runs meshing_around_meshforge - output varies from full output in moc2 to depends on a longfast node like wh6gxz mini mesh. :9443 does not sync/ack - which we know about - meshforge owns it.

Bridge counter snapshot, both gateways, 2026-05-08 07:14 HST:

| Box  | M→R (att/del/drop)     | R→M (att/del/drop)     | RNS nodes |
|------|-------------------------|-------------------------|-----------|
| moc  | 6 / 6 / 0               | **18 / 3 / 15**         | 87        |
| moc3 | 18 / 18 / 0             | 6 / 6 / 0               | 1297      |

---

## Flow Diagram

```
                      ===  Cross-preset NEVER over RF  ===
                                       |
   ShortTurbo RF                       |                       LongFast RF
   (moc3 hears these)                  |               (moc hears these)
   ------------------                  |               ------------------
   wh6gxzTRDEV / wx bot                |               wh6gxz mini mesh,
   meshforge moc3                      |               LongFast neighbors
        |                              |                       ^
        v                              |                       |
  meshtasticd@moc3                     |                meshtasticd@moc
  (SHORT_TURBO preset)                 |                (LONG_FAST preset)
        |                              |                       ^
        | local mosquitto              |                       | local mosquitto
        | msh/2/json/meshforge/        |                       | msh/2/json/meshforge/
        v                              |                       |
  meshforge-gateway@moc3               |                meshforge-gateway@moc
  (mqtt_bridge_handler.py:253)         |                (_rns_bridge_xform.py:196)
        |                              |                       ^
        | M→R                          |                       | R→M
        | _rns_bridge_xform.py:32-124  |                       | _rns_bridge_xform.py:196-326
        | log: "Bridge Mesh→RNS:"      |                       | log: "Bridge RNS→Mesh (queued):"
        v                              v                       |
  RNS.Transport.handle_outbound  -->  rnsd <--TCPClient/Server--> rnsd
  (rns_bridge.py:868)                  |                       |
                                       |                       |
                                  LXMF over RNS                |
                                       |                       |
                                   destinations:               |
                                   - moc gateway hash 3dfbdb5d (peer)
                                   - 4x NomadNet operator hashes
                                   - peer relay if no meshforge_relayed_by
                                       |                       |
                                       v                       |
                                                       _on_lxmf_receive()
                                                       (rns_bridge.py:1286-1360)
                                                              |
                                                              v
                                                      send_text_direct() HTTP PUT
                                                      https://localhost:9443/api/v1/toradio
                                                      channel=2 (meshtastic.channel)
                                                      (meshtastic_protobuf_client.py:103-171)
                                                              ^
                                                              |
                                                       FIRST attempt: urlopen 111 refused (0.002s)
                                                       RETRY: ok 0.018s
```

---

## Root Cause #1 — Misleading R→M counter (cosmetic)

**Code path**: `src/gateway/_rns_bridge_xform.py:196-326`. The counter `rns_to_mesh_attempted` increments each time `_process_rns_to_mesh()` enters; the counter `rns_to_mesh_dropped` increments when the persistent-queue enqueue returns `False`. `_is_duplicate()` returning True is the same code path as a real enqueue failure — both surface as "Failed to enqueue RNS→Mesh to persistent queue" at WARNING level.

LXMF delivery is best-effort with retries. After moc receives a unique LXMF and queues it, the LXMF source layer redelivers within seconds (no ack visible to source). Each redelivery re-enters `_process_rns_to_mesh()`, gets dedup'd, increments `dropped`. Result: 1 real message → 1 attempt + N redelivery-dedup attempts → counter inflates "drops".

Observed in moc journal, 2026-05-08 06:51-06:53 HST:

```
06:51:42.998  Bridge RNS→Mesh (queued): [RNS:f68c] test
06:51:43.265  rpc[meshtasticd.toradio_put[4e959161]] raised after 0.002s
06:51:47.078  Failed to enqueue RNS→Mesh to persistent queue   ← LXMF retry #1, dedup
06:51:53.321  Failed to enqueue RNS→Mesh to persistent queue   ← retry #2
06:52:09.267  Failed to enqueue RNS→Mesh to persistent queue   ← retry #3
06:52:20.996  Failed to enqueue RNS→Mesh to persistent queue   ← retry #4
06:52:42.199  Failed to enqueue RNS→Mesh to persistent queue   ← retry #5
06:52:47.222  Bridge RNS→Mesh (queued): [RNS:f68c] inch possible.
[...]
```

3 unique messages delivered over 3 minutes ("test", "inch possible.", "possible.") = matches moc3's 3 outbound + the 3 directed deliveries to moc's hash. The 15 "drops" are 5 dedup retries × 3 unique messages.

**Fix**: log-level downgrade — when enqueue-failure cause is `_is_duplicate=True`, log at DEBUG, not WARNING, and don't bump `rns_to_mesh_dropped`. Track dedup separately as `rns_to_mesh_dedup_skipped`. Already noted in `~/.claude/projects/-opt-meshforge/memory/project_rns_to_mesh_queue_drop.md`.

**Severity**: cosmetic. Operator dashboards mislead but no traffic is lost.

---

## Root Cause #2 — First-toradio_put always Connection-refused on moc

**Code path**: `src/gateway/meshtastic_protobuf_client.py:103-171`, `send_text_direct()`. Uses `urllib.request.urlopen` against `https://localhost:9443/api/v1/toradio`. On every R→M attempt observed, the first call raises `urlopen error [Errno 111] Connection refused`; the retry from the persistent queue succeeds immediately after.

Trace from moc journal:

```
06:51:42.998  Bridge RNS→Mesh (queued): [RNS:f68c] test
06:51:43.265  send_text_direct: <urlopen error [Errno 111] Connection refused>   ← 1st attempt FAIL (0.002s)
[4-second protobuf session re-handshake; reads 200 NodeInfo records]
06:51:47.537  Protobuf session established (node_num=848703248, nodes=200, channels=8)
06:51:47.556  rpc[meshtasticd.toradio_put] ok 0.019s                              ← retry SUCCESS
06:51:47.556  Sent text via HTTP protobuf (id=1202559522): [RNS:f68c] test...
```

Subsequent messages: same pattern, but without the 4-second re-handshake hit (session stays warm). Failure → ~20ms → success.

moc3 baseline shows zero `rpc raised` warnings — its busy M→R traffic (18/18/0 in the same window: weather queries, ack chatter) keeps the underlying TCP keep-alive alive. moc's protobuf session goes idle between sparse R→M arrivals; meshtasticd's HTTPS server appears to RST the keep-alive socket; first attempt finds a closed FD; retry opens a fresh one and works.

**Connection to operator-flagged ":9443 sync/ack" issue**: this is the surface of it. `GET /api/v1/fromradio?all=true` returns `200 size=0` cleanly (sync OK); `PUT /api/v1/toradio` against an idle session returns Connection-refused (ack/send broken until socket re-open). Same defect, different verb.

**Fixes** (ordered by intrusiveness):

1. **Treat `Errno 111` as transient and retry inline** — already happens via the persistent queue, but with a 4-second cold-start penalty on the first message after long idle. Adding an explicit one-shot inline retry on `Errno 111` inside `send_text_direct()` would absorb the failure transparently and would not block the persistent queue.
2. **Periodic protobuf keep-alive** — emit a no-op `getMyNodeInfo` (or similar) every 30-60s when R→M traffic is idle. Keeps the meshtasticd-side socket from being reaped. Costs one HTTP round-trip per minute.
3. **Disable HTTP keep-alive** — force a fresh TCP socket per `send_text_direct()` call. Eliminates the failure mode entirely at the cost of TLS handshake per message. Acceptable on a Pi-class box at meshforge cadence (~1 R→M/minute peak).
4. **Restart meshtasticd to clear stale state** — palliative; recurs after the next idle stretch. (NB: moc's meshtasticd was restarted at 2026-05-08 07:19:12 HST, after this trace window; effect on the cold-start retry pattern is not yet measured.)

**Severity**: latency, not loss. Messages reach the radio. First-message-after-idle has a 4-second cold-start delay; steady-state messages have a ~20ms delay. Operator-perceived "doesn't flow smoothly" tracks this.

---

## End-to-End Trace — One Real Message (06:53 HST, "[RNS:f68c] possible.")

| Hop | Time (HST)        | Where                                                                | What                                                          |
|-----|-------------------|----------------------------------------------------------------------|---------------------------------------------------------------|
| 1   | 06:53:47.5xx      | `meshtasticd@moc3` (SHORT_TURBO)                                     | wx-bot reply on `meshforge` ch from `!ebfa1b11`              |
| 2   | 06:53:47.5xx      | `mosquitto@moc3` topic `msh/2/json/meshforge/!ebfa1b11`              | JSON publish (text="possible.")                              |
| 3   | 06:53:47.571      | `meshforge-gateway@moc3` (`_rns_bridge_xform.py:98`)                 | `Bridge Mesh→RNS: meshforge moc3 (!ebfa1b11) → 4/5 dest(s) — possible.` |
| 4a  | 06:53:47.5–.7     | `RNS.Transport` over rnsd / TCP path                                 | LXMF outbound to 4 destinations including `3dfbdb5d…` (moc)   |
| 4b  | 06:53:47.723      | `meshforge-gateway@moc` (`rns_bridge.py:1286`, `_on_lxmf_receive`)   | `Bridge RNS→Mesh (queued): [RNS:f68c] possible....`           |
| 5a  | 06:53:48.067      | `_rns_bridge_xform.py:300` → `meshtastic_protobuf_client.py:103`     | `send_text_direct: <urlopen error [Errno 111] Connection refused>`  ← FAIL |
| 5b  | 06:53:48.089      | persistent-queue retry path                                          | `rpc[meshtasticd.toradio_put] ok 0.021s` ← SUCCESS            |
| 6   | 06:53:48.089      | `meshtasticd@moc` → `mosquitto@moc` echo                             | `JSON publish msh/2/json/meshforge/!32962f10` (id=1202559524) |
| 7   | 06:53:48.087      | `meshforge-gateway@moc` MQTT subscriber                              | `Self-echo filtered (RNS→Mesh loopback): [RNS:f68c] possible.` ← prevents M→R loop |

End-to-end latency moc3 send → moc radio TX: **~518ms** in steady state (would have been ~4s if first-after-idle).

---

## Why the user sees "depends on a longfast node like wh6gxz mini mesh"

LongFast and SHORT_TURBO physical layers do not interop over RF. The only path from a wh6gxzTRDEV ShortTurbo packet to a LongFast peer is:

```
wh6gxzTRDEV → moc3 mesh → moc3 gateway M→R → RNS → moc gateway R→M → moc mesh → wh6gxz mini mesh
```

When this path is partially broken (e.g., LXMF retries cluttering moc's counter, first-attempt urlopen failures, or — relatedly — :9443 sync/ack instability), bridged content drops or arrives late on the LongFast side. Direct moc2 ↔ wh6gxzTRDEV "full output" works because moc2 is presumably on SHORT_TURBO too (or hears it directly), bypassing the bridge altogether.

---

## Recommendation

**Operator-actionable now (no code change):**

```bash
ssh moc 'sudo systemctl restart meshtasticd'   # operator already did this at 07:19:12 HST
ssh moc 'sleep 5 && sudo systemctl restart meshforge-gateway'   # re-handshake protobuf cleanly
```

Then watch counters for 30 min:
```bash
ssh moc  'sudo journalctl -u meshforge-gateway --since "30 min ago" | grep -E "Bridge|toradio_put.*raised" | tail -40'
ssh moc3 'sudo journalctl -u meshforge-gateway --since "30 min ago" | grep -E "Bridge|toradio_put.*raised" | tail -10'
```

If the `rpc[meshtasticd.toradio_put] raised after 0.002s` pattern returns within 30-60 minutes of idle, the issue is meshtasticd HTTPS keep-alive reaping, not transient state — ratchet to fix #1 or #3 below.

**Code follow-ups (separate commits, not this task):**

1. `src/gateway/meshtastic_protobuf_client.py` — inline retry on `Errno 111` inside `send_text_direct()`, single attempt, cap latency. Smallest blast radius. Closes the cold-socket gap.
2. `src/gateway/_rns_bridge_xform.py` — split dedup-skip from real-drop. Don't increment `rns_to_mesh_dropped` for `_is_duplicate=True`; track separately as `rns_to_mesh_dedup_skipped`. Log at DEBUG. Per `project_rns_to_mesh_queue_drop.md`.
3. (Optional) `src/utils/prometheus_exporter.py` — expose `mesh_to_rns_attempted/delivered/dropped` and `rns_to_mesh_*` counters as gauges. Today the exporter only publishes service up/down. Lets us catch this trend on Grafana before the operator notices.

**Smoke-test for any of the above**: re-pull `R→M` counter on moc 30 min after deploy. With fix #1, expect `dropped` to stop accumulating against new traffic. With fix #2, expect `attempted` to stop double-counting LXMF retries.

---

## Open Questions

- **Why does moc track only 87 RNS nodes vs moc3's 1297?** RNS path-table propagation from moc3's hub-role to moc's TCPClient role may be lossy. Cross-reference `project_fleet_rns_topology.md`. Worth a separate pull when the moc-uptime side stabilizes.
- **Does meshtasticd's HTTPS server have a configurable keep-alive idle timeout?** If yes, raising it on moc would absorb fix #1 without code change. (If no, recommendation #2 or #3 from the code follow-up list is the only path.)
- **Is the LXMF redelivery cadence operator-visible anywhere?** Today the 5-retry pattern is only observable by grepping the journal for "Failed to enqueue". A counter on the LXMF source side would let us distinguish a noisy R→M (lots of dedup) from a quiet one.
- **wh6gxz mini mesh path:** depends on which LongFast nodes are within RF range of moc — outside this analysis. Worth a coverage map check.

---

## Cross-References

- **Issue #34** (2026-04-18) — MQTT topic shape. Relevant: confirms `msh/+/2/json/meshforge/#` subscription is sound.
- **Issue #40** (2026-04-21) — bytes payload + wrong-topic MQTT downlink. Closed; this analysis is downstream of those fixes.
- **Issue #41** (2026-04-21) — rpc_key pinning. Verified during this analysis: both moc and moc3 have matching `rpc_key` between `/etc/reticulum/config` and `/tmp/meshforge_rns_client/config`.
- **Issue #42** (channel resolver) — ran clean at startup on both boxes; no `ChannelResolutionError` in journals.
- **Issue #53** (2026-05-02) — `:4403` contention. Verified clear during this analysis (`ss -tnp | grep 4403` empty on both boxes).
- `project_rns_to_mesh_queue_drop.md` — names the exact dedup-misclassification we observed.
- `project_fleet_radio_heterogeneity.md` — confirms ShortTurbo/LongFast split is by design; bridge is the only crossing.
- `project_gateway_fleet_state.md` — moc3 hash `f68c2f56…` confirmed in moc's announce-discovery logs.

---

*This analysis is read-only diagnostics. No code or config was modified during the investigation.*
