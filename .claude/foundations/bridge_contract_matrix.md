# Bridge-Contract Matrix — what each bridge keeps, loses and translates

> **SSOT for what we ARE at the bridge layer** (operator 2026-10-16: *"goal is
> not a version race — a SOT for what we are"*). Versions of Meshtastic,
> MeshCore and RNS are upstream VARIABLES; this file is the CONSTANT they must
> satisfy. A new upstream release enters only if every contract below still
> holds, which is checked by the tests in the **Pinned by** lines, not by
> reading this page.
>
> **Evidence tier:** BELIEVED, from a code read of MF `be0408fa`+ and MA
> `df13cdf9` (two read-only explorers, 2026-10-16, every claim cited
> `file:line`; spot-checked by the author where a finding rests on it). A cell
> becomes VERIFIED only through its pinning test. A cell with no test is
> **UNPINNED**, and that is the worklist, not a footnote.
>
> Paths: `MF:` = `/opt/meshforge/src/gateway/`, `MA:` = `/opt/meshanchor/src/gateway/`.
> Line numbers drift; the test names are the durable anchor.

---

## The contract rules (apply to every edge)

1. **Decide on a wire FACT, never on text.** Slot index, identity hash and
   signature state are facts. A prefix in the body is a label. The 10-16
   DUPS incident was a text label (`[unverified] `) disarming a text guard.
2. **Refuse by default; log every decision.** Public MeshCore slot 0 is
   refused, unknown slots are refused, and RNS ingress defaults to OBSERVE
   (counted) before ENFORCE.
3. **Every loss is named here.** If an edge drops a field, this file says so.
   A field that silently disappears in translation is a contract break.
4. **Unverifiable is marked, never upgraded.** An RNS sender without a
   validated signature is `?`-tagged on RF and never relayed. A MeshCore
   channel sender has no key on the wire at all (see F13).
5. **Success means delivered, not queued.** Any edge that reports success at
   enqueue is flagged (see F5).
6. **The bridge tag is the loop invariant.** `[MC:` `[RNS:` `[Mesh:` `[ch0:`
   `[ch1:` `[MeshCore]` (MF `base_handler.py:36-62`, pinned by
   `test_bridge_tag_list_pinned_to_base_handler`). Each edge either adds a
   tag or carries a `content_id` that a guard reads.

## What each protocol gives us on the wire (the physics of the edges)

| | identity | addressing | text budget | delivery proof |
|---|---|---|---|---|
| **Meshtastic** | node id (`!hex8`), unauthenticated | channel **slot index** + broadcast/DM | 228 B handler (`utils/defaults.py:52`); 237 B in CanonicalMessage (F8) | `wantAck` on DMs; channel = none |
| **MeshCore** | DM: `pubkey_prefix`. Channel: **none** (sender name is IN the text) | `channel_idx` slot (never `channel`; 09-18 row) | 160 B | DM path ACK; channel = none |
| **RNS / LXMF** | identity hash + **signature** (`signature_validated`) | destination hash; no channels | no RF cap (bridges chunk/truncate) | LXMF DELIVERED proof |
| **MQTT** | inherits Meshtastic | topic root (`mqtt_root_drift`) | inherits | none (fleet transport only) |

Everything an edge "loses" is the difference between two rows of this table.
**No edge carries SNR, hops or rx_time across a protocol boundary** (both
twins: kept in local metadata, never on the wire). That is a deliberate
statement of what we are: radio metadata is measured at each end, never relayed.

---

## MeshForge edges

**MF-1 Meshtastic → RNS/LXMF** — on when `GatewayConfig.enabled`
(default False, `config.py:683`); ingress via MQTT by default (`bridge_mode`).
- *Identity:* the LXMF source is REPLACED by the gateway identity. The origin
  is TRANSLATED into the title `"{long} ({id}) via Meshtastic"` plus
  `meshforge_from_*` fields (`_rns_bridge_xform.py:210-247`). Uses `from`, not
  the last hop. Unverified: Meshtastic ids are unauthenticated (rule 4).
- *Addressing:* DM → `_get_rns_destination` (node tracker, then identity SSOT
  when `rns.cross_protocol_identity_enabled`); broadcast → the whole
  `rns.default_lxmf_destination` list (empty list = broadcasts DROPPED,
  `bridge_send_mixin.py:163-170`). The channel is KEPT as a numeric slot in
  `meshforge_channel`.
- *Text:* body unchanged, no prefix, no chunking.
- *Delivery:* LXMF delivered/failed callbacks; per-destination breaker 5/60 s;
  SQLite spill when the queue is full. **F4.**
- *Loop:* tag check + optional `content_id` (`rns.true_origin_downlink_enabled`,
  default False); packet-id dedup. **F6.**
- *Pinned by:* `test_rns_bridge.py::TestProcessMeshToRNS`, `TestSessionDmToGateway`,
  `test_mqtt_bridge_handler.py::TestIsAlreadyBridged`, `TestContentIdLoopGuardIngress`.

**MF-2 RNS/LXMF → Meshtastic** — on by default with the gateway.
- *Identity:* verified → `[RNS:<hash4>]`; unverified → `[RNS:?<hash4>]` with
  the internal `[unverified] ` mark STRIPPED and the LXMF fields emptied
  (`_rns_bridge_xform.py:717-723, 934-935`). A mesh-origin relay is labelled
  with its original node (short → long → id, ≤10 chars).
- *Addressing:* a leading `@!hex8` / `@short` makes a DM, for RNS-authored
  content only. Then reply routing (`rns.reply_routing_enabled`, default False),
  otherwise broadcast on `meshtastic.channel`.
- *Text:* the tag is on EVERY chunk (`chunk_for_mesh`, 228 B).
- *Delivery:* one SQLite item per chunk, 3 s spacing, `max_retries=3`; DM
  `wantAck`; ACK consumption is off by default.
- *Loop:* queue dedup 60 s; dual-path RF-TX registry (`rns.dual_path_dedup_enabled`,
  **default True**); never relays an unverified sender, a peer gateway's copy,
  or anything already `meshforge_relayed_by`.
- *Gate:* `rns.bridge_source_identities` + `bridge_source_policy`
  (default `""` = OPEN, `observe`). ENFORCE refuses the unlisted and the
  unverified; verified peer gateways are always admitted.
- *Pinned by:* `test_rns_bridge.py::TestProcessRNSToMesh`, `TestChunkForMeshPrefix`,
  `TestUnverifiedSourceOnRf`, `TestUnverifiedIsNeverRelayed`, `TestDualPathDedup`;
  `test_rns_ingress_signature.py::TestEnforce`.

**MF-3 RNS → peer gateways** (relay-on-receive) — gated by
`rns.peer_gateway_destinations` (default empty). Keeps the original body and
adds `meshforge_relayed_by` / `origin_*` / `content_id`. *Pinned by:*
`TestRelayOnReceive`.

**MF-4 MeshCore channel → Meshtastic / RNS** — `meshcore.enabled` (default False).
- *Gate:* slot allowlist `meshcore.bridge_source_channels`; default = every
  slot EXCEPT Public 0; an unparseable/None slot is refused
  (`meshcore_channel_path.py:51-81`). **DMs are never bridged.**
- *Identity:* `[MC:<src8>]`, unverified by construction (F13).
- *Addressing:* the slot is KEPT in metadata but LOST on the wire (`[ch:N]`
  appears in logs only).
- *Text:* truncated at 228 B toward Meshtastic, no chunking. RNS gets no title
  or fields.
- *Delivery:* no requeue on failure. *Dedup:* event-vs-poll sha256, 120 s.
  **F11.**
- *Pinned by:* `test_meshcore_channel_allowlist.py`, `test_meshcore_dm_stays_dm.py`,
  `test_canonical_message.py::TestFromMeshcore`.

**MF-5 Meshtastic / RNS → MeshCore** — fed for every MF-1/MF-2 message;
internet- and MQTT-origin messages are dropped. Tag `[Mesh:<8>]` / `[RNS:<8>]`,
160 B with an ellipsis. **F5** (effectively dead on real hardware, and reports
success). *Pinned by:* only the refusal
(`test_meshcore_handler.py::test_channel_broadcast_refused_never_public`).
The edge itself is **UNPINNED** (no test calls `_process_bridge_to_meshcore`).

**MF-6 Meshtastic LongFast ↔ SHORT_TURBO** (`mesh_bridge.enabled`, default False).
TEXT only; DM vs broadcast and the channel index are KEPT. Toradio path:
`[Mesh:<preset>]`. Downlink injection: untagged, as the origin node, `hop_limit=3`.
Dedup `source:md5(content)[:8]` 60 s plus the dual-path registry. SNR/RSSI
kept locally only. **F7.** *Pinned by:* `test_mesh_bridge_mqtt.py`
(`TestEchoLoopInvariant`, `TestDownlinkInjectionWiring`, `TestBroadcastDetectionShapes`).

**MF-7 Meshtastic → LXMF broadcast subscribers** (`meshtastic_broadcast.enabled`,
default False). Broadcasts on `channels=[0]` only; its own LXMF identity;
`[meshtastic ch{n}:{sender16}]`. Subscribe needs a valid signature. DELIVERED-aware.
**F10.** *Pinned by:* `test_meshtastic_broadcast_bridge.py`.

**MF-8 Operator line** (LXMF ↔ oracle; no RF path by construction). Off
unless `operator_line.enabled is True` AND an operators list exists.
Unverified → ignored; strangers → silence + counter; 10 s cooldown; replies
byte-capped; no relay verb. *Pinned by:* `test_operator_line.py`.

## MeshAnchor edges

**MA-1 MeshCore channel → Meshtastic / RNS** — slot allowlist
`meshcore.bridge_source_channels` (default refuses Public 0 and unknown slots,
`meshcore_ingress.py:214-228`). Label lifted from the text header (the
sender's NAME, 09-18 row). Tag `[MC:<label>]`. The slot is LOST on the wire.
228 B truncation to a local radio; the radioless egress does not truncate.
No requeue. Hops/SNR are logged (`reach_of`), never forwarded. *Pinned by:*
`test_meshcore_inbound_policy.py`, `test_rns_to_mesh_echo_guard.py::TestMeshCoreToMeshIsUnaffected`.

**MA-2 MeshCore DM** — **not bridged** (`bridge_dms` is never read). *Pinned
by:* `test_meshcore_dm_stays_dm.py`.

**MA-3 RNS/LXMF → Meshtastic** — same identity rule as MF-2 (`[RNS:?xxxx]`,
mark stripped, `rns_bridge.py:1158-1162`); same OPEN/observe default. The echo
guard strips our mark BEFORE matching (`ee404ae9`). Chunked, tag on every
chunk. *Pinned by:* `test_rns_to_mesh_echo_guard.py`, `test_lxmf_claim_is_not_identity.py`,
`test_rns_ingress_policy.py`.

**MA-4 RNS/LXMF → MeshCore** — needs `meshcore.bridge_target_channel ≥ 0`
(default -1 = drop). `[RNS:<8>]` or `[RNS:?<8>]`, 160 B, no chunking. `@contact`
directed replies become DMs (`dm_replies_enabled`) with path-ACK `✓` and a
not-found `✗` notice. Split-horizon matches tags by SUBSTRING. **F1.**
*Pinned by:* `test_rns_bridge.py` (`test_process_bridge_to_meshcore_*`),
`test_meshcore_dm_reply.py`.

**MA-5 Meshtastic → RNS / MeshCore** — `[Mesh:<4>]` to RNS, `[Mesh:<8>]` to
MeshCore. No echo guard on Mesh→RNS. **F2.** *Pinned by:*
`test_bridge_integration.py::test_process_mesh_to_rns_*`.

**MA-6 Re-emit: MF broadcast bridges → MeshCore** (`meshtastic_reemit.enabled`,
default False). Verified sources only, from `source_identities`; drops status
lines; strips the MF header; `[Mesh:{sender}]` on `target_channel` (default 0 =
Public, sent via `send_text` directly, bypassing the -1 guard). Identity
warm-up at every RNS connect (`30b535ff`). Fire-and-forget. *Pinned by:*
`test_meshtastic_reemit_bridge.py`, `test_rns_identity_warmup.py`.

**MA-7 MeshCore → LXMF broadcast subscribers** (`lxmf_broadcast.enabled`,
default False). `channels=[0,1]`, but Public only arrives if inbound policy
admits it. `[ch{n}:{sender16}]`. DELIVERED-aware. *Pinned by:*
`test_lxmf_broadcast_bridge.py`, `test_lxmf_broadcast_identity_refused.py`.

**MA-8 Oracle** (env `MESHANCHOR_ORACLE_ENABLED`, default off; live ON on the
MA box, allowlisted). A DM is answered directed (hex destinations only); a
channel query is answered on `_reply_slot`. **F3.** *Pinned by:*
`test_oracle*.py`, `TestOracleGateUsesTheDeviceName`.

## CanonicalMessage — the shared contract, and how far it reaches

`canonical_message.py` is **byte-identical MF↔MA** (`cmp` rc=0, 10-16).
Fields: `id content_id source_network source_address destination_address
destination_network content payload message_type is_broadcast hop_limit
hop_count via_internet origin timestamp metadata ack_required ack_of reply_to`.
**F8**: in production only the MeshCore edges construct it. Every
Meshtastic/RNS edge on MF uses `BridgedMessage` (`rns_bridge.py:133-147`).
`hop_count`, `ack_of` and `reply_to` are filled by no live edge.

---

## Findings — contract breaks and drift (the worklist)

Status: **VERIFIED** = the author checked it this session; **BELIEVED** = an
explorer's code read; **PLAUSIBLE** = needs a failing test before anyone
acts on it. Nothing below is fixed yet.

| # | Edge | Finding | Status | First move |
|---|---|---|---|---|
| F1 | MA-4 | `[unverified] ` is NOT stripped toward MeshCore → the text reads `[RNS:?abcd1234] [unverified] …`; `parse_directed_reply` on a marked `@contact` may demote a DM | BELIEVED (queued 10-16) | failing test → strip via the shared `strip_unverified_mark` |
| F2 | MA-5 | a Meshtastic **DM** pulled off Mesh→RNS was fanned onto the MeshCore queue and sent on the bridge channel; the disconnect branch persisted DMs AND `@contact` replies for CHANNEL replay | **FIXED 2026-10-16** (MA, test-first: red on old code): `private_to_channel()` refuses at both consumers after the directed-DM leg, counter `meshcore_bridge_private_drop`; honours the `dm_replies_enabled` kill switch. **Latent live** (meshanchor-server has no Meshtastic radio) | `tests/test_bridge_private_to_channel.py`, `test_bridge_loop_disconnect_refuses_private_dm` |
| F3 | MA-8 | `_reply_slot = int(env or bridge_target_channel) or 1` → **-1** at the default `bridge_target_channel=-1` (−1 is truthy) → `send_chan_msg(-1)` | VERIFIED by read; **latent** (live config = 1) | test at default config; treat <0 as "no channel reply" |
| F4 | MF-1 | Failed M→R DMs requeued with `destination_id` while `_queue_send_rns` reads `destination_hash` → every retry returned False; retries also dropped title/fields. **Wider than first read:** the RNS-DOWN branch (`rns_bridge.py` bridge loop) and the except path wrote the same shape, so every M→R message during an RNS outage dead-lettered | **FIXED 2026-10-17 (MF, test-first: 5 red on old code)**: a failed leg requeues as one `rns` row PER RESOLVED HASH (`destination` key so a fan-out survives the queue's dedup) carrying title+fields; with no resolved hash (RNS down / raised before send) the whole message replays through `_process_mesh_to_rns` (`rns_xform`), so F14 + broadcast fan-out decide again at retry; the replay WAITS (returns False) while RNS is disconnected; a raise AFTER a send never requeues (duplicate); a replay that raises again never re-enqueues. Mutants ×5 each killed by one test. **MA ported 2026-10-17 `1307beb6`** (adds the `rns_xform` sender MA lacked; rows carry the exact `[Mesh:xxxx]`-prefixed text; 6 red→green, 5 mutants killed, MA full suite 8373 passed rc=0). **Non-author read DONE 10-17 (Fable 5.1): PASS-with-notes both, no privacy leak; R1–R5 fixed test-first both twins (rows wait out an RNS outage via a queue `ready_fn` gate; partial fan-out legs retried). Fresh read of the FIX diffs queued; NOT deployed** | `tests/test_m2r_requeue_f4.py`. Residual: the queue's no-policy budget (3 tries, 5/15/45 s) still dead-letters an outage > ~65 s — honestly, with a DROPPED record |
| F5 | MF-5 / MA-4 | MeshCore `send_text` returns True on ENQUEUE (MF `meshcore_handler.py:1224`, MA `:1252-1261`); on MF the broadcast is then REFUSED on a real radio → the edge is dead and reports success | BELIEVED | decide: delete MF-5, or give it a target slot + delivered-not-queued |
| F6 | MF-1 | `config.py:273-277` documents a `gateway_node_id` self-echo drop that is not implemented (only sessions read it) | BELIEVED | delete the doc text or implement + pin |
| F7 | MF-1/6 | TCP ingress decided broadcast by `to_id == '!ffffffff'`, but the lib emits `'^all'` | **VERIFIED + FIXED 2026-10-16**: `meshtastic.BROADCAST_ADDR == '^all'` read from the installed lib on VolcanoAI + meshanchor-server, so every TCP broadcast was labelled a DM (and MF's two cross-box loop-guard registrations were inert on TCP). `packet_is_broadcast()` decides on numeric `to` in both twins. **Latent live**: moc/moc3 run `mqtt_bridge` (numeric check, correct) | `TestBroadcastWireFact` (MF), `TestPacketIsBroadcast` (MA) |
| F8 | shared | CanonicalMessage is the "shared contract" but reaches only MeshCore; two Meshtastic budgets (237 vs 228) | BELIEVED | one constant (hfm #5); decide whether BridgedMessage IS the contract |
| F9 | MA | the `content_id` field comment says "never used to suppress" while its docstring allows gated suppression | BELIEVED | reconcile in the byte-locked file (both twins) |
| F10 | MF-7 | the broadcast-bridge callback fires even when routing rules refuse | BELIEVED | decide: by design (subscribers opted in) → pin it; else gate |
| F11 | MF-4 | no tag loop guard on MeshCore ingress — relies on the slot allowlist + Public refusal | BELIEVED | pin "a tagged MeshCore line is not re-bridged" |
| F12 | MF-1 | `desired_method` never passed to `LXMessage` → the delivery method is the library default | UNKNOWN which default | read the pinned fork's LXMessage, then pin |
| F13 | MF-4/MA-1 | MeshCore channel senders are unverifiable but carry no `?` mark, while unverified RNS senders do — an asymmetry in rule 4 | design question | operator decision: mark, or document why not |
| F14 | MF-1/MA-1 | a Meshtastic DM with no single RNS recipient was fanned to `default_lxmf_destination` — a list that includes PEER GATEWAYS (measured 10-16: moc→`f68c2f56`, moc3→`3dfbdb5d`) which re-emit on RF; on MF this was the 06-03 'no session → broadcast fallback' (`0c21ff74`, ratified then) | **FIXED 2026-10-16 — operator REVERSED the fallback: "refuse + tell sender"**. MF+MA refuse (counter `mesh_to_rns_private_refused`, WARNING without content, health record); MF tells the sender once/600 s when the DM was to its own node (advice gated on sessions). Live exposure was moc (sessions ON, DM leg armed): 0 fallbacks in its journal since 10-10. Non-author read W1 (own-echo counted as failure) + W2 (impossible advice sessions-off) fixed test-first. Boundary: non-broadcast with NO destination keeps the fan-out | `TestSessionDmToGateway`, `TestF14ReviewFollowups` (MF); `TestMeshDmNeverFannedToDefaults` (MA) |
| F2-note | MA-5 | deliberate trade: a `@contact` DM reply arriving while MeshCore is DOWN is now DROPPED (counter) instead of replayed — the replay is a channel send. Healthy path still delivers it as an MC DM | by design 2026-10-16 | later: persist DM replies with a `destination` key so replay goes as a DM |

**Unpinned edges:** MF-5 (no test calls it), F7's TCP shape,
MA-4's double mark. Each is a contract nobody would notice breaking.

## How this file stays true

- A change to any edge updates its block **in the same commit**, or the
  reviewer treats the omission as a finding. The same rule applies to the
  twin port (MF lead, MA follows; `parity_check` covers only the
  byte-locked files).
- An upstream bump (meshtasticd, meshcore_py, rns/lxmf fork base) is accepted
  when the **Pinned by** tests pass on it AND the wire-physics table above is
  re-read against the new release's source. "Newer" is not an argument here.
- When a finding is fixed, it moves to the edge block as a pinned contract
  (with its test), and its row is deleted. The table only shrinks by fixing.
