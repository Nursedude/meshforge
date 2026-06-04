# MeshForge Fleet Architecture — Diagnosis & Roadmap

> **Date**: 2026-06-03 · **Last verified**: 2026-06-04 (read-only fleet sweep; added
> §7.6 dual-radio/downlink data plane, §7.7 config matrix + drift findings) · **Author**: Dude AI + WH6GXZ
> **Method**: live probe (`/api/status`, `systemctl`, `ss -xnpl`) cross-checked
> against code/config (`fleet_roles.yaml`, `templates/systemd/`, `src/gateway/`,
> `src/utils/map_federation.py`). Every claim below is evidence-backed from the
> running fleet on this date — treat as a point-in-time snapshot, not a permanent spec.
> **Scope**: the 5-box Pi fleet + adjacent MeshAnchor host + the `meshing_around`
> bot, the gateway/bridge cornerstone question, and where the architecture should go.

---

## 1. Executive summary — center of gravity

MeshForge is a **Network Operations Center (NOC)** that bridges otherwise-incompatible
mesh ecosystems (Meshtastic ↔ Reticulum/RNS ↔ MeshCore). Asking "what is the
cornerstone?" has two honest answers depending on the plane you look at:

- **Data plane** — the **gateway/bridge is the cornerstone.** Every cross-protocol
  message funnels through `src/gateway/rns_bridge.py` → `CanonicalMessage` →
  `MessageRouter`. Nothing crosses ecosystems without it. It is also the one contract
  shared byte-for-byte with the sister NOC (MeshAnchor).
- **Control plane** — the **TUI handler registry** (`src/launcher_tui/`, ~96 handlers)
  is the hub the operator actually touches; it orchestrates the gateway as one
  subsystem among many (maps, monitoring, RF, diagnostics).

**Dependency direction settles it:** the TUI imports the gateway for status; the
gateway runs headless via `bridge_cli.py` without the TUI. **The gateway is the
load-bearing technical cornerstone; MeshForge is NOC-shaped around it.** That framing
matters for the roadmap (§7): investment in the gateway compounds across the whole
domain, while TUI work is leaf work.

---

## 2. Fleet topology map

```
                         ┌──────────────────────────────────────────────┐
                         │            CLOUD (public)                     │
                         │   meshforge-maps.ddns.net  (VPS webroot)      │
                         │   data.geojson · space_weather · alerts       │
                         └───────────────▲──────────────────────────────┘
                                         │ rsync atomic push, ~120s pull cadence
                                         │ (scripts/cloud/push_snapshot.sh)
                         ┌───────────────┴──────────────┐
                         │ moc1  — CLOUD-PUBLISHER        │
                         │ map+maps :8808 cloud map       │
                         │ cloud-push.timer (120s)        │  gateway OFF (overridden)
                         └───────────────▲────────────────┘
                                         │ pulls local GeoJSON :5000
        federation (per-box pull, 60s, exp-backoff #59/#65, WAL-gated #64)
        each box polls peers' /api/nodes/directory — NO central aggregator
    ┌──────────────┬───────────────┬──────────────┬──────────────────────────┐
    │              │               │              │                          │
┌───┴────┐   ┌─────┴────┐    ┌─────┴────┐   ┌─────┴─────┐          ┌─────────┴────────┐
│VolcanoAI│   │  moc      │    │  moc2    │   │  moc3      │          │ meshanchor-server │
│PRIMARY  │   │FULL-GW    │    │COLLECTOR │   │GATEWAY-ONLY│          │ MESHANCHOR-NOC    │
│:5000 fed│   │:5000      │    │:5000     │   │(no :5000)  │          │ (no /opt/meshforge)│
│manager  │   │gateway ✅ │    │map only  │   │gateway ✅  │          │ RNS↔MQTT bridge   │
│map+mini │   │map+maps   │    │map+maps  │   │Pi3B        │          │ = federation peer │
│x86      │   │Pi4        │    │Pi        │   │/dev/ttyUSB0│          │   (NULL name, 49k)│
└────┬────┘   └────┬──────┘    └────┬─────┘   └─────┬──────┘          └──────────────────┘
     │             │                │               │
   ──┴─────────────┴────────────────┴───────────────┴──  per box: shared transports
        meshtasticd (LoRa, TCP :4403/:9443)  ·  rnsd (@rns/default Unix socket, 1/box)
        mosquitto (MQTT :1883)               ·  map HTTP :5000 / :8808 / WS :5001

   ┌─────────────────────────────────────────────────────────────────────────┐
   │  bot (.32 / wh6gxzTRDEV) — Pi Zero W — meshing_around (ADJACENT, not fleet)│
   │  mesh_bot.service (autoresponder, TCP → .203 AREDN) · mesh-client.service │
   │  no meshtasticd / no rnsd / not in fleet_hosts — MQTT-consumed, not owned  │
   └─────────────────────────────────────────────────────────────────────────┘
```

Data-plane vs control-plane: messages flow through the **gateway** boxes (moc, moc3);
the operator drives everything through the **TUI** on any box. Maps/federation are a
separate read-only telemetry plane layered on top.

---

## 3. Per-box role table (live-verified 2026-06-03)

| Box | HW | Role | Active MeshForge svcs | Transports | Gateway | Map | Telemetry |
|-----|----|----|--------------------|-----------|--------|----|----------|
| **VolcanoAI** | x86 | **primary / federation manager** | map, watchdog, mini-dudeai | meshtasticd (local :9443), rnsd (@rns owner), mosquitto | ❌ delegates | federator :5000 | 2,675 local nodes / 22.2M obs; canonical memory writer |
| **moc** | Pi4 | **full-gateway** | gateway, map, maps, watchdog | meshtasticd (30d), rnsd (@rns), mosquitto | ✅ bridge_cli | :5000 collector | 3,940 nodes |
| **moc1** | Pi4/5 | **cloud-publisher** | map, maps, watchdog, cloud-push.timer | meshtasticd (10d), rnsd (@rns), mosquitto | ❌ overridden off | :8808 cloud map | 2,747 nodes |
| **moc2** | Pi | **collector** (ratified 2026-06-03; was full-gateway + per-box override) | map, maps, watchdog | meshtasticd (**301d — oldest**), rnsd (@rns), mosquitto | ❌ deliberately off — RF-sparse site (~4 nodes heard vs moc's 126) | :5000 collector | 1,175 nodes |
| **moc3** | Pi3B | **gateway-only** | gateway, watchdog | meshtasticd (/dev/ttyUSB0), rnsd (@rns, **CPU 10.8%**) | ✅ bridge_cli | none (by design) | no node_history.db |
| **meshanchor-server** | x86 | **meshanchor-noc** | (no /opt/meshforge) | rnsd only, mosquitto | RNS↔MQTT | n/a | federation peer (≈49k nodes, NULL name, ~3.6s latency) |
| **bot (.32)** | Pi0 W | **meshing_around bot** | mesh_bot.service, mesh-client.service | TCP → AREDN node; no meshtasticd/rnsd | autoresponder | n/a | legacy, not in fleet_hosts |

Role definitions and singleton invariants live in `docs/fleet_roles.yaml`; convergence
via `scripts/provision_role.py`; permission foundation via `src/utils/fleet_foundation.py`.

---

## 4. Capability dimensions (what each Pi does)

- **Routing** — `MessageRouter` (`src/gateway/message_routing.py`): confidence-scored
  classifier → `BRIDGE_RNS / BRIDGE_MESH / BRIDGE_MESHCORE / DROP / QUEUE`. **Only the
  gateway boxes route** (moc full-gateway, moc3 gateway-only). The map "federation
  routing" is a separate read-only pull, not message routing.
- **Messaging** — Meshtastic ↔ RNS/LXMF ↔ MeshCore, all normalized through
  `CanonicalMessage` (`src/gateway/canonical_message.py`) at ingress and de-normalized
  at egress (eliminates N×(N−1) translation paths). Persistent SQLite retry queue +
  `DeliveryTracker` (#66 ack semantics). **This contract is byte-shared with MeshAnchor.**
- **Telemetry** — `src/monitoring/` (`mqtt_subscriber`, `node_monitor`,
  `traffic_inspector`, `packet_dissectors`, `rns_sniffer`) → `node_history.db`
  (time-series), `watchdog.json` (per-unit health), `delivery_counters.db`. Consumed by
  the map GeoJSON builder, mini-dudeai rules, and the tracer SLO leaderboard.
- **Utilization / health** — `meshforge-watchdog` per box emits Signals →
  mini-dudeai briefs + ntfy. Covered classes: fd-exhaustion (#73), RNS-wedge (#68/#72),
  parity/foundation/version-drift. This is the proactive-reliability spine.
- **RF** — meshtasticd LoRa radio (attached only where a device is bound — moc3 =
  `/dev/ttyUSB0`); `src/utils/rf.py` link budgets; `commands/propagation.py` space
  weather (NOAA primary); coverage maps.
- **TCP / transport** — meshtasticd TCP :4403 / :9443; rnsd shared-instance Unix socket
  `@rns/default` (**exactly one RNS host per box** — #69 invariant, owned by rnsd);
  MQTT :1883; map HTTP :5000 / :8808 + WebSocket :5001.

---

## 5. Map → federation → cloud data flow

1. **Collect** — each box's `MapDataCollector` merges local sources (meshtasticd, MQTT,
   RNS tracker, AREDN) into `node_history.db` and serves `/api/nodes/{directory,geojson}`.
2. **Federate** — every box runs a `FederationCollector` (`src/utils/map_federation.py`)
   that polls peers' `/api/nodes/directory` every 60s. **No central aggregator** —
   VolcanoAI is "manager" by *convention* (canonical memory writer), not by protocol.
   Resilience: exp-backoff (#59 tier-1 10×, #65 tier-2 60×), 30s timeout for big bodies
   (#56), gzip negotiation (#64), WAL backpressure gate (#64), response byte-caches for
   the GIL-bound serialization wedge class (#70/#71). Conflict resolution: local wins,
   then `_origin_priority()`.
3. **Publish** — moc1's `meshforge-cloud-push.timer` (120s) runs
   `scripts/cloud/push_snapshot.sh`: pulls local GeoJSON + NOAA space weather/alerts →
   atomic rsync to the VPS webroot → browser. **Pull-based; the cloud is ~120s behind.**

---

## 6. Adjacent systems

- **MeshAnchor** (`Nursedude/meshanchor`, sister NOC, MeshCore-primary, extracted
  2026-04-01). Parity contract enforced by `scripts/parity_check.py`: byte-identical
  `canonical_message.py`, `rns_init.py`, `rns_tree_perms.py`, and the `requirements/rns.txt`
  fork-pin block; shape-parity on `rns_status_parser.timed_out`, lint MF009/MF019,
  `rns_version_check.py`, and the RNS-wedge probes. **MeshForge is the lead repo for the
  RNS-reliability arc** — land here, port there. Both NOCs share one rnsd per box, so the
  RNS substrate must be identical. `meshanchor-server` also appears as a **federation
  peer** of the fleet (the ~49k-node, NULL-name entry — see watch-item §8.3).
- **bot (.32)** — a 1st-gen Pi Zero W running the third-party `meshing_around` project
  (`mesh_bot.service` autoresponder over TCP to an AREDN node + `mesh-client.service`).
  **Adjacent, not integrated**: MeshForge consumes its bot/alert data via MQTT; it does
  not run the MeshForge stack and is deliberately excluded from `fleet_hosts`.

---

## 7. Roadmap — "do it better, think different"

Four themes, each: *what is* → *the reframe* → *concrete steps*. No code shipped in this
diagnosis; these are sequenced proposals.

### Theme A — Gateway deepening (the cornerstone arc)

> **STATUS 2026-06-04: all three steps SHIPPED 2026-06-03** (`2b1be20` reply-to,
> `2f8af9b` identity SSOT, `0c21ff7` sessions) — full record in §7.5. Flags ON on the
> full-gateway canary box only; **field validation is the remaining handoff** before
> widening. The dual-radio mesh_bridge + true-origin downlink injection work (§7.6)
> extended this theme the same day. The text below is the original diagnosis, kept
> for the reasoning record.

- **Is**: best-effort delivery + `DeliveryTracker` (#66); **one-way** addressability —
  mesh→RNS carries `long_name`, `@id`/`@short_name` parses for directed downlink (#39).
- **Reframe**: stop treating the gateway as a *pipe* and build it as an **addressability
  fabric** — *"if a client receives a message, they can reply; it just works"*
  (the 12-month sync/ack arc). The deeper goal is bidirectional **routing**, not delivery
  proof.
- **Steps**: (1) **canonical reply-to** round-tripped inside `CanonicalMessage` so a reply
  auto-resolves its return path across protocols; (2) **cross-protocol identity binding** —
  one canonical entity spanning Meshtastic node-id / RNS hash / MeshCore address, extending
  `node_tracker`'s topology graph into an identity SSOT; (3) a **conversation/session**
  abstraction above per-message ack. Arc, not a sprint — but every step compounds because
  the gateway is the data-plane cornerstone (§1).

### Theme B — Fleet consolidation / SSOT
- **Is**: `fleet_roles.yaml` + `provision_role.py` + `fleet_foundation.py` (shipped); RNS
  drift covered by parity/foundation/version probes feeding mini.
- **Gap**: capability→service mapping is partly implicit, and **gateway/bridge config
  drift is unprobed** — moc (full-gateway) and moc3 (gateway-only) should share canonical
  routing config, but nothing asserts it. **Live case study (this date)**: moc2 carried
  role `full-gateway` with a per-box `service_overrides` deliberately disabling the bridge
  (RF-sparse site, documented reason) — the declared model was *correct*, but the fleet
  role check surfaces only the base role name, so the topology read "full-gateway" while
  the box intentionally doesn't bridge. Two diagnoses misread it before anyone opened
  `deployment.json`. Resolution: ratified a **`collector` role** (inherits full-gateway,
  bridge disabled) so the role *name* says what the box *is*; overrides stay for true
  one-off exceptions. The future drift probe must compare **effective state vs effective
  declaration (base role + overrides)** and surface override reasons — not just role
  names. The unrealized "rns-meshtastic-gateway" env-management tool (single owner of
  the RNS+meshtastic substrate setup) also belongs here.
- **Steps**: (1) a **gateway-config drift probe** following the proven
  audit-organ → Signal → mini pattern (same shape as `probe_parity_drift`); (2)
  provisioner `--apply` soak (currently dry-run default); (3) a **declarative capability
  manifest** so role → capability → service is one SSOT instead of YAML + implicit unit
  state.

### Theme C — Map / federation / cloud
- **Is**: per-box pull federation; moc1 cloud-push (120s); MeshAnchor peer contributing
  ~49k nodes with NULL `peer_name`.
- **Gap**: cross-NOC ingest is **unfiltered** (a 49k-node directory merged wholesale,
  ~3.6s latency); 120s cloud lag is fixed regardless of change rate; "federator vs
  consumer" is convention, not a declared role attribute.
- **Steps**: (1) **cross-NOC ingest policy** — tier/filter MeshAnchor's directory (and
  fix `peer_name` plumbing, #54, so the entry isn't NULL); (2) evaluate **event-driven
  cloud push** (push on significant delta) vs the fixed 120s timer; (3) **formalize
  federator/consumer** as a role attribute in `fleet_roles.yaml`.

### Theme D — RF / transport / utilization
- **Is**: meshtasticd LoRa + rnsd + MQTT per box; `RadioLoadBalancer` / dual-radio
  failover exists in the gateway.
- **Findings to act on**: (1) **moc3** — rnsd at 10.8% CPU and meshtasticd logging
  `ToPhone queue is full, drop packet`: the Pi3B gateway-only box may be **saturated**
  under translation load. Characterize, then consider offload via the load balancer or a
  hardware tier bump. (2) **moc2** — meshtasticd PID running **301 days**: wire the
  existing `meshtasticd-restart.timer` (template already in `templates/systemd/`) to clear
  VSZ/swap accumulation. (3) Establish per-box **headroom budgets** for constrained
  hardware (Pi3B / Pi0) so role assignment respects real capacity.

---

## 7.5 Gateway goals vs current configuration (assessment 2026-06-03)

Operator question after ratifying moc2=collector: *are the gateway/bridge goals met
with this configuration?* Assessed live against the three goal tiers:

- **Tier 1 — predictable best-effort bridging** (shipped substrate, #66): ✅ **MET.**
  Two bridges, both green over the live window: moc 46 msgs (M→R 10/10, R→M 36/36,
  0 dropped, 3,782 nodes), moc3 47 msgs (M→R 25/25, R→M 22/22, 0 dropped, **4,430
  nodes** — the Pi3B pulls real weight; its ToPhone queue-full warnings are the
  meshtasticd phone API, not bridge drops). Flows are complementary: moc is R→M-heavy,
  moc3 balanced. Bridge placement matches RF reality (bridges at RF-rich sites;
  collector/cloud-publisher where bridging adds nothing).
- **Tier 2 — resilience/redundancy**: ✅ **RESOLVED BY DECLARATION (2026-06-03).**
  Investigation flipped the framing: moc+moc3 are **complementary-coverage** bridges
  (different RF neighborhoods, different flow profiles), not a redundant pair — and
  `GatewayHeartbeat` gates no TX in `rns_bridge.py` (instantiated `:379`, state never
  consulted), so wiring it buys only peer-liveness telemetry the watchdog/mini already
  provide, at the cost of a cross-box broker dependency. Operator ratified
  **active-active independent bridges as the design** — `independent_bridges` invariant
  added to `fleet_roles.yaml`. Per-bridge resilience = systemd `Restart=` + watchdog +
  mini. (Original finding kept for the record: heartbeat machinery exists in code,
  deliberately unwired.) Also one config-shape difference found —
  initially misread as `true` vs `null` (a `d.get()` probe artifact): moc sets
  `rns_bridge_enabled: true` **explicitly**, moc3 left the key **absent** (documented
  default-true applies, `GatewayConfig` config.py:541). Benign — normalized to explicit
  `true` on moc3 2026-06-03 for cross-box comparability. Lesson for the §7-B drift
  probe: **compare effective config (parsed through `GatewayConfig`), not raw JSON** —
  raw-key diffs can't distinguish absent / null / default.
- **Tier 3 — "it just works" bidirectional addressability** (12-month arc): ❌ **NOT
  YET, by design** — this is code work (§7-A reply-to / identity SSOT / sessions); no
  box configuration can meet it.

**Verdict**: the configuration is right-shaped for today's goals; the gaps are not
placement gaps. Sequenced next steps: (1) ~~normalize moc3 `rns_bridge_enabled`~~
**done 2026-06-03** (explicit true; was absent-key/default-true, not null);
(2) ~~decide GatewayHeartbeat~~ **done 2026-06-03** — independence declared
(`independent_bridges` invariant in `fleet_roles.yaml`); (3) ~~build the §7-B drift
probe~~ **done 2026-06-03** — `probe_role_drift` (`f4cf904`): provision_role's own
dry-run `plan()` as a continuously-monitored signal, overrides honored, 2-tick
debounce, `role_drift_any` mini rule promoted on all 5 boxes, verified clean live
(streak=0 fleet-wide). Remaining in the effective-config family: cross-box
gateway.json comparison through `GatewayConfig` (fleet-level organ, future);
(4) the §7-A arc — **step (1) shipped 2026-06-03**: canonical reply-to round-trip +
gateway reply-context memory. `meshforge_reply_to` (protocol-qualified token,
`format_reply_token`/`parse_reply_token` in `canonical_message.py`, byte-parity-ported
to MeshAnchor) emitted on every M→R send; R→M honors **explicit `@addr` > echoed
field > reply-context memory > broadcast**, so a stock NomadNet reply auto-directs
back to the mesh node that messaged them. Gated by `rns.reply_routing_enabled`
(default off; **enabled on moc only** for observation — watch `(reply:field)`/
`(reply:ctx)` journal tags + `reply_routed_from_*` stats). Honor excludes anything
carrying bridge-origin markers (`meshforge_source_network`/`meshforge_relayed_by`),
`[RNS:`/synthetic-ACK content, and peer-gateway sources — peer fan-outs stay
broadcasts. Memory = in-memory TTL'd LRU (`reply_context.py`), restart loses context
by design this step. **Step (2) shipped same day** (`2f8af9b`): cross-protocol identity
SSOT — the dormant `ContactMappingTable` wired via new `identity_binding.py`
(`IdentityBinder`: lazy DB, throttled population from live traffic, conservative
name-based auto-bind — unambiguous exact-match only, unverified; operator links via
`scripts/gateway_contacts.py` are verified). Reply chain is now **@addr > field >
memory > contact > broadcast**; M→R DMs fall back to contact mapping on node_tracker
miss. Gate `rns.cross_protocol_identity_enabled` (default off; **moc only**). Peer
gateways/bridge copies never become contacts (reuses the step-1 exclusion guard).
**Step (3) shipped same day (`0c21ff7`) — ARC COMPLETE (code-side)**: durable session
layer. `session_store.py` (`gateway_sessions.db`, DBSpec-registered, lazy, idle-TTL 24h,
~30s sweep) records active (mesh ↔ RNS peer) conversations on directed traffic; a
Meshtastic **DM to the gateway's own node** (`meshtastic.gateway_node_id`) now routes
privately to the sender's session peer — previously that DM was silently broadcast-fanned
to every RNS inbox. No-session fallback = the old broadcast (zero regression). Gate
`rns.sessions_enabled` (default off; **moc only** — all three Theme-A flags now on
there). CLI `scripts/gateway_sessions.py`. Bidirectional "it just works" is now
software-complete; field validation (radio legs both directions) is the remaining
operator handoff before widening beyond moc.

---

## 7.6 Dual-radio mesh_bridge + true-origin downlink injection (added 2026-06-04)

Shipped 2026-06-03, the same day as the Theme-A arc — a **spatial** extension of
gateway deepening (bridging two RF presences on one box) plus a **visibility** fix
(cross-bridge traffic rendered as genuinely incoming on the radio's own web UI).

```
          moc (full-gateway) — the canary box
  ┌─────────────────────────────────────────────────────────────┐
  │  LONG_FAST mesh                       SHORT_TURBO mesh      │
  │  ┌─────────────┐                      ┌──────────────┐      │
  │  │ HAT radio   │                      │ USB Heltec   │      │
  │  │ meshtasticd │                      │ /dev/ttyUSB0 │      │
  │  │ :4403/:9443 │                      │ (serial, no  │      │
  │  └──────┬──────┘                      │  2nd daemon) │      │
  │         │ MQTT RX (json)              └──────┬───────┘      │
  │         │ TX: ┌─ toradio (default) ──────────┤ serial RX/TX │
  │         │     └─ DOWNLINK (LAB): true-origin │              │
  │         ▼        AES-CTR protobuf ServiceEnv ▼              │
  │  ┌──────────────────── mesh_bridge ────────────────────┐    │
  │  │ channels allow-list · dedup 60s · [Mesh: tag        │    │
  │  │ invariant (echo-loop guard, 0a4ce92)                │    │
  │  └──────────────────────────┬───────────────────────────┘    │
  │                             │ composes with rns_bridge       │
  │                             ▼  (CanonicalMessage → LXMF)     │
  └─────────────────────────────────────────────────────────────┘
```

- **Dual-radio serial leg**: `mesh_bridge.secondary.connection_type="serial"` talks
  to a USB radio directly — no second meshtasticd. The `channels` allow-list is the
  safety rail (serial RX hears every channel; without it a secondary ch0 text would
  re-TX on the primary's public ch0).
- **True-origin downlink injection** (`e98612a` + broadcast-detect fix `543c99b` +
  tag-strip `72f3411` + NodeInfo injection `a3d6960`): with
  `injection_mode="downlink"` + `downlink_psk`, broadcast forwards onto the primary
  leg are published as encrypted protobuf MQTT downlinks carrying the REAL origin
  node id; meshtasticd attributes them correctly (`:9443` shows "moc2: text", not
  self-TX), and a one-time NODEINFO_APP downlink teaches the HAT each origin's name.
  Any failure falls back to toradio — never drops. **Canary live on moc, soaking
  since 2026-06-03 evening** (synth traffic + soak cron; verdict via
  `~/downlink_soak_summary.sh` on the manager box). Fleet default OFF.
- **Echo-loop invariant stands**: downlink injects RAW content (loop-free path,
  verified — meshtasticd does not re-uplink `-e-` downlinks to json); the toradio
  fallback still carries the `[Mesh:` tag. The `[Mesh:`/`[RNS:` provenance tags
  remain non-negotiable on every toradio leg.
- **Open threads**: RNS-origin leg (no native node id — needs synthetic-id mapping);
  downlink-health watchdog probe (mini is blind to downlink-specific degradation —
  the soak cron covers it short-term); HAT nodedb 200-cap eviction churns injected
  NodeInfo on busy meshes (cosmetic for live web sessions).
- **Operator docs**: `docs/GATEWAY_BRIDGE_CONFIG_GUIDE.md` + validated templates in
  `docs/gateway_config_templates/` (anti-rot test
  `tests/test_gateway_config_templates.py` loads each through the real
  `GatewayConfig` parser — beta schema churn breaks CI, not the operator).

---

## 7.7 Live gateway config matrix + drift findings (read-only sweep 2026-06-04)

Snapshot of effective gateway/bridge config per box (sanitized: PSKs redacted,
LXMF hashes truncated). Re-verify live before acting — the fleet moves.

| Box | Role | bridge_mode | rns_bridge | mesh_bridge | meshcore | broadcast | Theme-A flags | LXMF fan-out | peer gateways |
|-----|------|-------------|------------|-------------|----------|-----------|---------------|--------------|---------------|
| VolcanoAI | primary | (dormant config) | — | — | — | — | — | — | — |
| moc | full-gateway | mqtt_bridge | ✅ | ✅ **dual-radio, downlink LAB ON** (primary HAT downlink+PSK; secondary serial ST) | ❌ | ✅ ch2 | **all 3 ON** (canary) | 4 dests: `6b1a0120…`, `7cda0fab…`, `f68c2f56…`, `9217147e…` | `f68c2f56…`, `58cecbd0…` |
| moc1 | cloud-publisher | mqtt_bridge | (key absent → default true; unit shape minimal) | ❌ | ❌ | ❌ | off | 1 dest: `522c4ac1…` | — |
| moc2 | collector | mqtt_bridge | (key absent) | ❌ | ❌ | ❌ | off | 1 dest: `d1df31d3…` | — |
| moc3 | gateway-only | mqtt_bridge | ✅ explicit | wired, **off** (hot spare) | wired, off (`/dev/ttyUSB1` ready) | ✅ ch2 | off | 4 dests: `6b1a0120…`, `7cda0fab…`, **`3dfbdb5d…`**, `9217147e…` | **`3dfbdb5d…`**, `58cecbd0…` |

**Drift findings** (fix runbook: `docs/gateway_config_templates/HARMONIZATION_2026_06_04.md`;
**items 1–4 resolved/executed 2026-06-04 AM** — record in the runbook):

1. ~~moc3 `peer_gateway_destinations` ≠ moc's~~ **FALSE ALARM (2026-06-04)** —
   hash ownership verified live: moc's own `lxmf.delivery` = `3dfbdb5d…`, moc3's
   = `f68c2f56…` (see `reference_fleet_gateway_hashes` memory). The lists are
   correct **directional mirrors**: moc lists [moc3, meshanchor-server], moc3
   lists [moc, meshanchor-server] — the intended 2026-05-18 wiring. Lesson for
   the future effective-config drift organ: peer/mirror-valued keys must be
   compared by *referent*, not by raw value.
2. ~~LXMF destination sets inconsistent~~ **INTENTIONAL (2026-06-04)** —
   moc/moc3 4-entry lists differ only by the peer-mirror entry (position 3);
   moc1's single `522c4ac1…` and moc2's `d1df31d3…` are each box's **own
   NomadNet inbox** (derived from `~/.nomadnetwork/storage/identity`, exact
   match). **Canonical convention**: bridging boxes carry the shared operator
   inbox set + peer mirror; non-bridging boxes carry their local NomadNet
   inbox (inert while the bridge is off; sane default at promotion).
3. ~~`rns_bridge_enabled` schema shape~~ **DONE 2026-06-04** — explicit `true`
   added on moc1/moc2 (backups `gateway.json.bak-harmonize-2026-06-04`; units
   inactive, no restart). Fleet now uniformly explicit.
4. ~~Theme-A flags moc-only undeclared~~ **DECLARED 2026-06-04** — hold
   ratified: trio added as explicit `false` on moc3 (clean gateway restart
   verified); widening gates on Theme-A field validation, not on config work.
5. ⚠️ **`downlink_psk` sits in plaintext in ~12 `gateway.json.bak*` files on moc** —
   rotate post-soak + backup hygiene (the config file is secret-bearing once the
   PSK is set).
6. ⚠️ **VolcanoAI `broker_profiles.json` carries dormant plaintext MQTT creds**
   (incl. the public meshtastic default pair) — rotate/relocate; dormant, low
   priority.

**SSOT already harmonized** (good): broker localhost:1883, root_topic `msh`,
region `""`, json_enabled, meshtasticd localhost:4403, http_port 9443, telemetry
defaults — identical fleet-wide.

---

## 8. Watch-items (carry forward)

1. **moc2 meshtasticd age ~44d** (started 2026-04-21; the earlier "301d" reading was
   wrong — corrected against `ActiveEnterTimestamp` 2026-06-04). Still the oldest
   daemon on the fleet alongside its mosquitto; restart-timer candidate (§7-D).
2. **moc3 rnsd CPU 10.8% + ToPhone queue-full drops** — gateway-only Pi3B saturation signal;
   characterize before it becomes an incident.
3. **Federation peer = meshanchor-server (NULL name, ~49k nodes)** — known cross-NOC pull;
   decide ingest policy and fix peer_name (§7-C). A bare-IP NULL-name peer also periodically
   re-escalates in mini as "unexpected" — it is a known member.
4. **moc2 role-name visibility (RESOLVED 2026-06-03)** — initially read as drift; in fact
   `deployment.json` carried a documented `service_overrides` disabling the bridge
   (RF-sparse site). Operator ratified intent ("not meant to bridge at this time") →
   new `collector` role added to `fleet_roles.yaml` + moc2 re-declared. Residual lesson
   for §7-B: role names must be legible at fleet-check level; drift probes must reason
   over base-role + overrides, and surface override *reasons*.

---

*Snapshot taken 2026-06-03; §7.6/§7.7 + watch-item corrections added from the
2026-06-04 read-only sweep. Re-verify live before acting on any §3/§7.7/§8 number —
the fleet moves.*
