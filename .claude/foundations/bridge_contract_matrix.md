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
160 B with an ellipsis. **F5, fixed 10-26**: dead on real hardware BY
DESIGN (no outbound slot routing) and now SAYS so — the fan-out never
enqueues when `can_broadcast` is False and records `dropped, unsendable`;
health no longer counts an enqueue as sent. *Pinned by:*
`test_meshcore_fanout_truth.py`, the refusal
(`test_meshcore_handler.py::test_channel_broadcast_refused_never_public`).

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
MeshCore fan-out now applies the same predicate on its own leg (**F16,
fixed 10-26**). *Pinned by:* `test_meshcore_fanout_truth.py`,
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
| F5 | MF-5 / MA-4 | MeshCore `send_text` returns True on ENQUEUE and the bridge recorded health *sent* there; on MF every real-radio broadcast is then refused (`dropped, unsendable`) — two views of one message disagreed (rule 5) | **FIXED 10-26 (both twins, test-first, non-author read)**: MF fan-out reads `MeshCoreHandler.can_broadcast` (simulator only) and records `dropped, unsendable` on the ledger WITHOUT enqueuing (never requeued); health counts only the TX worker's outcome (`to_meshcore` sent / failed) — the enqueue-time `mesh_to_meshcore`/`rns_to_meshcore` *sent* are gone in both twins (MA: generic + DM-reply sites). Pinned by `tests/test_meshcore_fanout_truth.py` (both repos). `stats['messages_*_to_meshcore']` still counts enqueues (no reader treats it as delivery) | — |
| F6 | MF-1 | `config.py` documented a `gateway_node_id` self-echo drop that was never implemented; `test_own_sender_no_rns_prefix_passes` pins the opposite; stale comment in `mqtt_bridge_handler.py`; `docs/GATEWAY_DEPLOYMENT.md` (2 passages) and the test file's headers claimed it too | **FIXED 10-26 (docs/comments only, MF)**: all now say `gateway_node_id` is this box's IDENTITY (read by the DM/session legs and the MQTT handler), never a filter; the echo is stopped by the bridge-TAG guard (`is_already_bridged`), whoever sent it. MA has no such field | — |
| F8 | shared | three Meshtastic budget literals: 237 `canonical_message.py`, 228 `utils/defaults.py`, 228 `utils/validation.py`; oracle `MAX_REPLY_BYTES` 237 | **FIXED 10-26 (both twins), MEASURED**: they were distinct quantities and one was stale. `MESHTASTIC_MAX_PAYLOAD` = **233** = `DATA_PAYLOAD_LEN`, the Data.payload BUFFER bound (firmware v2.7.26 `mesh.proto:1819`; lib 2.7.11) — not the on-air budget (only consumer `to_meshtastic_text`, no production caller). Egress text budget 228 has ONE home (`utils.defaults`); oracle + `validate_message_length` import it. The oracle's 237 sat above the egress cut (229-237 B replies would lose their tail at the radio with an ERROR: 0 in 30 d on moc/moc3); its existing pin test pinned it EQUAL to the stale wire constant — the wrong pair — now `<`. Pinned by `tests/test_meshtastic_payload_budget.py` (both). The "is BridgedMessage the contract" half → with F10/F13 | — |
| F9 | shared (byte-locked) | the `content_id` field comment said "never used to suppress" while its docstring allows gated suppression | **FIXED 10-26 (MEASURED)**: comment now matches the docstring — suppresses only inside the gated intra-box dual-path dedup window (`_rns_bridge_xform.py` dedup_cid) | — |
| F10 | MF-7 | the broadcast-bridge callback fires even when routing rules refuse | BELIEVED | decide: by design (subscribers opted in) → pin it; else gate |
| F11 | MF-4 | no tag loop guard on MeshCore ingress — relies on the slot allowlist + Public refusal | BELIEVED | pin "a tagged MeshCore line is not re-bridged" |
| F12 | MF-1 | `desired_method` never passed to `LXMessage` → the delivery method is the library default | **FIXED 10-26 (both twins)**: every production construction passes `desired_method=LXMF.LXMessage.DIRECT` (MF: 2× `bridge_send_mixin`, `meshtastic_broadcast_bridge`, `operator_line`; MA: 3× `bridge_send_mixin`, `lxmf_broadcast_bridge`). No behaviour change — installed `1.0.1+mf.13` maps None→DIRECT in `pack()`, and `handle_outbound` only branches on PROPAGATED (Fable read). AST guard `tests/test_lxmf_desired_method_explicit.py` (both repos) refuses a construction without `LXMessage.DIRECT` outside `src/lab/` (blind to aliases / `getattr` construction — none exist). **Operator decision 10-26: keep DIRECT** for every site, incl. operator-line replies and broadcast-subscriber fan-out (PROPAGATED for offline peers was considered and declined for now). | — |
| F15 | shared (byte-locked) | `CanonicalMessage.from_meshtastic` decided broadcast on the TEXT `toId` — a lib-shaped `toId='^all'` became a DM to `^all`; the copy the F7 fix missed | **FIXED 10-26 (both twins, latent — still no production caller)**: `packet_is_broadcast` + `MESHTASTIC_BROADCAST_NUM` now LIVE in the locked `canonical_message.py`; `meshtastic_handler` and `mesh_bridge` import it (Fable found `mesh_bridge`'s OR-rule copy, which read a numeric DM with a stray `'^all'` as a broadcast, and the handler's `store_incoming` text marker). Pinned by `tests/test_canonical_broadcast_rule.py` (both repos; 5/9 red on old code, incl. numeric broadcast + node-id `toId` read as a DM). Remaining text-only copies, consistent today, NOT folded: `utils/message_listener.py` (4 sites), `utils/inbox_api.py:25` (UI/API layer reading stored ids); numeric egress constants (`ack_tracker`, `mqtt_downlink_inject`, `mqtt_bridge_handler`) agree | — |
| F16 | MA-5 (and MF, same loop) | the bridge loop put EVERY Mesh→RNS / RNS→Mesh message on the MeshCore queue, whatever the echo guard decided; a full queue dropped at DEBUG, no counter. **Present in BOTH twins** (MF `rns_bridge.py` had the identical two sites), but live only on MA: MF's Meshtastic/MQTT ingress already drops bridge-tagged text before the Mesh→RNS queue (`meshtastic_handler.py`, `mqtt_bridge_handler.py`, `mesh_bridge.py` `is_bridge_loop`) | **FIXED 10-26 (both twins)**: one entry `_fan_out_to_meshcore` — origin filter, then `is_already_bridged` on Meshtastic-sourced text (`meshcore_fanout_echo_suppressed`), then (MF) `can_broadcast`, then enqueue; queue-full = `meshcore_fanout_dropped_queue_full` + WARNING once per 300 s per reason. Decided on the MeshCore leg's own facts, NOT on what Mesh→RNS admitted (the prescribed fix would have starved MeshCore whenever RNS was down). ⚠️ Trade-off kept: an already-bridged line (e.g. an MF-6 `[Mesh:<preset>]` forward) now reaches this box's MeshCore only via MA-4 | — |
| F17 | MF-2/MF-6 unicast + MA queue egress | the 228 B egress text budget exceeded what fits on air as a **PKI-encrypted DM**: measured max text 232 B broadcast, **220 B PKI DM, 215 B with `reply_id`** (installed protobufs + firmware v2.7.26 `Router.cpp`: encoded+16 > 255 → TOO_LARGE; PKI +12) | **FIXED 10-26 (both twins, test-first)**: `utils.defaults.MAX_MESHTASTIC_DM_MSG_LENGTH = 215`; `base_handler.mesh_text_budget(destination)` (broadcast decided by the one rule) feeds MF RNS→Mesh `@address` chunking, MA's persistent-queue Meshtastic egress chunking, and all four `_truncate_if_needed` sites per twin. Live consequence before the fix UNKNOWN (`Use PKI` is DEBUG; no TOO_LARGE line in 30 d on moc/moc3). **Operator 10-26: fleet radios are NOT in licensed (HAM) mode and modes stay** — licensed mode disables encryption and loses LongFast public — so the fix is the budget, never radio config. Pinned by `tests/test_meshtastic_dm_budget.py` + `test_meshtastic_payload_budget.py` (both). **F17b OPEN (now observable, F18):** `mesh_bridge` preset forward (MF-6) sends `[Mesh:<preset>] ` + the original with no size policy — an over-limit forward is now REFUSED loudly at `send_text_direct` (WARNING + counter) instead of dropped silently by the radio — a max-size DM (220) or broadcast (232) plus the tag exceeds the on-air limit; chunk / truncate / refuse is a design call, measure first (`Bridged … ->` lines vs radio refusals) | F17b: decide the forward's oversize policy |
| F18 | operator sends (both twins) | Meshtastic DM paths with NO byte budget: `send_text_direct` reported success on HTTP 200 — which only means meshtasticd took the bytes; the firmware's TOO_LARGE refusal happens afterwards, inside the radio — so the web inbox answered **`success: true, "Sent via radio"`** for a 221-500 B DM that never aired; the CLI's direct send fell back to a `meshtastic --sendtext` subprocess that would air it anyway; `commands/messaging.py` chunked at 160 **chars** (one multibyte "word" left as 900 B); `validate_message_length` had 0 callers | **FIXED 10-26 (both twins, test-first; 8 red on MA's old code)**: the REFUSAL line is the radio's measured limit — `utils.defaults.MESHTASTIC_ONAIR_MAX_BROADCAST = 232`, `MESHTASTIC_ONAIR_MAX_DM = 220`, `base_handler.mesh_onair_limit(dest)` (pinned to the encoding: one byte more does not fit) — kept distinct from the chunking TARGET (`mesh_text_budget` 228/215): refusing at the chunk target would newly drop a 229-232 B broadcast that airs today (caught before commit; a test pins it). `send_text_direct_with_id` refuses before the PUT with a WARNING + `oversize_refused_count()`; the inbox checks bytes via `validate_message_length` before EITHER send path (400 naming the limit); the CLI refuses before the subprocess fallback; messaging chunks Meshtastic by bytes to the destination's budget. Pinned by `tests/test_meshtastic_send_budget.py` (both). **Still open:** MA's radio-less queue egress sends a DM row as a channel broadcast (documented); `messaging` broadcast fallback sends channel 0 as channel 1 (`channel if channel > 0 else 1`) — pre-existing, unexplained, not touched | — |
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
