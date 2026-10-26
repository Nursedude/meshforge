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
> **2026-10-26 sync** from an outside re-read (Bridge Contract Matrix artifact,
> another session: two independent code reads at MF `176d7296` / MA `bca8b795`).
> Its claims enter here as BELIEVED unless re-measured in THIS repo at MF
> `738e4fd3` / MA `bca8b795` — those are marked **MEASURED 10-26** with what was
> run (F5, F6, F8, F12, F15, F16, MA-5's guard test). Not re-measured: the F1
> refutation, F3, F9.
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
- *Send method:* no `desired_method` is passed; the pinned fork (lxmf
  `1.0.1+mf.13`) turns `None` into **DIRECT** (link) and never falls back to
  PROPAGATED. **F12.**
- *Pinned by:* `test_rns_bridge.py::TestProcessMeshToRNS`, `TestSessionDmToGateway`,
  `TestF14ReviewFollowups`, `test_m2r_requeue_f4.py`,
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
160 B with an ellipsis. **F5** (effectively dead on real hardware: the
health counter still says *sent* while, since 10-24, the delivery-leg ledger
records the same message as *dropped, unsendable* —
`meshcore_handler.py:1021`). *Pinned by:* only the refusal
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
MeshCore. A Mesh→RNS echo guard landed 10-19 (a bridge-tagged line is not
re-sent to RNS). A DM never goes out on a MeshCore channel (F2, fixed). The
MeshCore fan-out is NOT gated by that guard. **F16.** *Pinned by:*
`test_bridge_integration.py::test_process_mesh_to_rns_*`,
`test_mesh_to_rns_echo_guard.py`, `test_bridge_private_to_channel.py`,
`test_m2r_requeue_f4.py`.

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
`from_meshtastic` still decides broadcast on the TEXT `toId` (**F15**, the copy
the F7 fix missed); no production code calls it today.

---

## Findings — contract breaks and drift (the worklist)

Status: **VERIFIED** = the author checked it; **MEASURED 10-26** = re-read
at HEAD in this repo during the 10-26 sync; **BELIEVED** = a code read
(explorer or the outside artifact); **PLAUSIBLE** = needs a failing test
before anyone acts on it. Fixed rows moved to "Fixed and pinned" below.

| # | Edge | Finding | Status | First move |
|---|---|---|---|---|
| F1 | MA-4 | `[unverified] ` is NOT stripped toward MeshCore → the text reads `[RNS:?abcd1234] [unverified] …` (the mark twice, 13 of 160 B) | BELIEVED; **narrowed 10-26** (artifact, measured there, not here): the "demotes a marked `@contact` DM" half is REFUTED — `parse_directed_reply` peels `[unverified]` like any leading tag | failing test → `strip_unverified_mark` where `src_label` gets its `?` |
| F3 | MA-8 | `_reply_slot = int(env or bridge_target_channel) or 1` → **-1** at the default `bridge_target_channel=-1` (−1 is truthy) → `send_chan_msg(-1)` | VERIFIED by read; **latent** (live config = 1) | test at default config; treat <0 as "no channel reply" |
| F5 | MF-5 / MA-4 | MeshCore `send_text` returns True on ENQUEUE (MF `meshcore_handler.py:1224`, MA `:1252-1261`); on MF the broadcast is then REFUSED on a real radio → the edge is dead and its health counter reports success | **MEASURED 10-26, narrowed**: the delivery-leg ledger now records the refusal (`meshcore_handler.py:1021` → `dropped, unsendable`) while health still says sent — two views of one message disagree (rule 5). Still no test calls `_process_bridge_to_meshcore` | delete MF-5, or make health follow the delivery outcome, not the enqueue |
| F6 | MF-1 | `config.py:280-285` documents a `gateway_node_id` self-echo drop that is not implemented (only the DM/session legs read it); `test_mqtt_bridge_handler.py:157 test_own_sender_no_rns_prefix_passes` pins the OPPOSITE; a stale comment at `mqtt_bridge_handler.py:735` still cites the filter | **MEASURED 10-26** (all three present) | delete the doc text + the stale comment; the tag loop guard covers the echo |
| F8 | shared | CanonicalMessage is the "shared contract" but reaches only MeshCore; THREE Meshtastic budget literals: 237 `canonical_message.py:32`, 228 `utils/defaults.py:52`, 228 `utils/validation.py:73` | **MEASURED 10-26** (three, not two) | one constant (hfm #5); decide whether BridgedMessage IS the contract |
| F9 | MA | the `content_id` field comment says "never used to suppress" while its docstring allows gated suppression | BELIEVED | reconcile in the byte-locked file (both twins) |
| F10 | MF-7 | the broadcast-bridge callback fires even when routing rules refuse | BELIEVED | decide: by design (subscribers opted in) → pin it; else gate |
| F11 | MF-4 | no tag loop guard on MeshCore ingress — relies on the slot allowlist + Public refusal | BELIEVED | pin "a tagged MeshCore line is not re-bridged" |
| F12 | MF-1 | `desired_method` never passed to `LXMessage` → the delivery method is the library default | **RESOLVED, MEASURED 10-26**: installed lxmf `1.0.1+mf.13` (fork `892047d5`) sets `desired_method = LXMessage.DIRECT` when None (`LXMessage.py:391-393`); no PROPAGATED fallback (FAILED after 5 attempts, per the artifact) | pass `desired_method=DIRECT` explicitly + pin, so a fork merge cannot change it silently |
| F15 | shared (byte-locked) | `CanonicalMessage.from_meshtastic` decides broadcast as `to_id is None or to_id == '!ffffffff'` (`canonical_message.py:270`) — a lib-shaped packet with `toId='^all'` becomes a DM to `^all` even when numeric `to` is broadcast: the copy the F7 fix missed | **MEASURED 10-26** (line read; `cmp` MF↔MA identical); **latent**: no production caller of `CanonicalMessage.from_meshtastic` (grep: only `UnifiedNode.from_meshtastic`) | reuse the `packet_is_broadcast` rule in the locked file, both twins one commit, a `^all` case in `TestFromMeshtastic` |
| F16 | MA-5 | the bridge loop puts EVERY Mesh→RNS message on the MeshCore queue right after `_process_mesh_to_rns`, whatever the 10-19 echo guard decided (MA `rns_bridge.py:877-881`); MeshCore split-horizon looks only for MeshCore markers, so an `[RNS:…]`-tagged Meshtastic line can reach MeshCore as `[Mesh:x] [RNS:…] …`; a full queue drops with `logger.debug` only, no counter | **MEASURED 10-26** (the fan-out + DEBUG-only drop read at MA `bca8b795`); the double-tag outcome is PLAUSIBLE until a test shows it | failing test with an RNS-tagged Meshtastic broadcast → fan out only what Mesh→RNS admitted; a counter on the queue-full drop |
| F13 | MF-4/MA-1 | MeshCore channel senders are unverifiable but carry no `?` mark, while unverified RNS senders do — an asymmetry in rule 4 | design question | operator decision: mark, or document why not |
| F2-note | MA-5 | deliberate trade: a `@contact` DM reply arriving while MeshCore is DOWN is now DROPPED (counter) instead of replayed — the replay is a channel send. Healthy path still delivers it as an MC DM | by design 2026-10-16 | later: persist DM replies with a `destination` key so replay goes as a DM |

### Fixed and pinned (moved out of the worklist; history in git)

- **F2** (MA-5) fixed 10-16 — a Meshtastic DM is refused off the MeshCore
  queue, counter `meshcore_bridge_private_drop`. `test_bridge_private_to_channel.py`.
- **F4** (MF-1 / MA-5) fixed 10-17, both twins — a failed leg requeues per
  resolved hash with title+fields; no hash → replay that waits out an RNS
  outage. Residual: an outage > ~65 s still dead-letters, with a DROPPED
  record. `test_m2r_requeue_f4.py` (MF + MA).
- **F7** (MF-1/6) fixed 10-16, both twins — `packet_is_broadcast()` decides on
  numeric `to`. `TestBroadcastWireFact` (MF), `TestPacketIsBroadcast` (MA).
  Its missed copy is F15.
- **F14** (MF-1 / MA-1) fixed 10-16, both twins — a DM with no single RNS
  recipient is refused, never fanned to `default_lxmf_destination`.
  `TestSessionDmToGateway`, `TestF14ReviewFollowups` (MF);
  `TestMeshDmNeverFannedToDefaults` (MA).
- **F2-note** stays a deliberate trade (see above).

**Unpinned edges:** MF-5 (no test calls it), MA-4's double mark (F1),
`CanonicalMessage.from_meshtastic`'s broadcast rule (F15), MA-5's MeshCore
fan-out (F16). Each is a contract nobody would notice breaking.

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
