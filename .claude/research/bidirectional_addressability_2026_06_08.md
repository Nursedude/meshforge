> MeshForge deep-research report — generated 2026-06-08 by a multi-agent research workflow (web sweep -> primary-source fetch -> adversarial claim verification -> synthesis). Feeds the bidirectional-addressability feature thread; see .claude/plans/tui_honesty_and_domain_arc_2026_06_08.md

# Bidirectional Addressability Across Heterogeneous Mesh Transports: A Reply-Routing Design for the MeshForge Gateway

*MeshForge research report — 2026-06-08. Codebase paths are absolute; external claims are inline-cited `[source: URL]` and flagged verified/unverified against the primary-source verification array.*

---

## 1. Executive Summary

The goal is plain English: **if a client receives a message, it should be able to reply, and the reply should land on the original sender — across the Meshtastic↔Reticulum boundary, in both directions.** A NomadNet/Sideband user who receives a bridged message from a mesh node should be able to hit "reply" and reach that node; a Meshtastic operator who receives `[RNS:Alice] ...` should be able to answer Alice.

Today's MeshForge gateway can do this only partially, and only behind default-off flags. The structural reason is **Issue #35**: every mesh node a gateway bridges onto RNS collapses to the gateway's single LXMF source identity (`send_to_rns`, `/opt/meshforge/src/gateway/rns_bridge.py` L1218-1229), and inbound LXMF arrives with `destination_id=None` ("LXMF doesn't have destination in received messages," L1888/L1908). Per-node attribution survives only as a `[Mesh:xxxx]`/`[RNS:xxxx]` **text prefix in the body**, not as a routable address. Reply routing back to a specific mesh node works only when (a) the RNS client manually types `@!nodeid`, or (b) reply-routing is enabled *and* the in-memory, restart-lossy `ReplyContextStore` still holds the mapping, or (c) the durable `SessionStore`/`IdentityBinder` rungs are enabled. Out of the box, RNS→Mesh is broadcast-only.

This is the textbook signature of a **"bridgebot" bridge**, where "all the metadata about the messages and senders is lost" [source: https://matrix.org/docs/older/types-of-bridging/] (verified). The cross-protocol-bridging literature — Matrix, XMPP, Twilio Proxy, DTN, SSB — converges on a clear answer: to make replies route, you must climb to at least a **virtual-user / ghost** model (a stable, addressable per-sender identity on the far side) plus a **message-correlation table** (remote-id ↔ local-id), optionally augmented by **stateless self-describing reply tokens**. MeshForge already owns most of the primitives needed — `ContactMappingTable`, `SessionStore`, `IdentityBinder`, `DownlinkInjector`, the `format_reply_token` shape, and the `@id` directed-downlink parser. The work is **composition and promotion**, not green-field construction.

The key asymmetry that shapes the whole design: **RNS's address space is huge** (a 16-byte destination hash per identity, cheap to mint), while **Meshtastic's is constrained** (a 4-byte NodeNum, scarce DM-able handles, PSK-gated channels, ~200-byte payloads). So the right architecture is **asymmetric**: *namespace-per-node toward RNS* (mint a stable LXMF-visible identity per mesh node — the SSB/Matrix model), and *small alias-pool + correlation-token toward Meshtastic* (the Twilio Proxy model). This report justifies that split from primary sources and maps it onto the existing code.

---

## 2. The Reply-Routing Problem, Stated Precisely

### 2.1 Two incompatible address spaces

| | Meshtastic | Reticulum / LXMF |
|---|---|---|
| Canonical address | `NodeNum`, a 4-byte `fixed32`; `!hex8` is its hex rendering [source: https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/mesh.proto] (verified) | 16-byte (128-bit) destination hash, truncated SHA-256 over the dotted aspect name with the identity's hash folded in [source: https://reticulum.network/manual/understanding.html] (size verified; see §3.1 correction) |
| Broadcast | `to = 0xFFFFFFFF` [source: https://deepwiki.com/meshtastic/protobufs/2-message-architecture] (secondary) | No true "all-RNS"; LXMF is per-SINGLE-destination |
| Reply target | reverse `from`/`to`; ACK/reply correlate via `request_id == original id` [source: https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/mesh.proto] (verified) | swap LXMF Source↔Destination; Source *is* the reply address [source: https://raw.githubusercontent.com/markqvist/LXMF/master/LXMF/LXMessage.py] (verified) |
| Reachability gate | channel `(name, PSK)` decode-gate + LoRa flooding [source: https://github.com/meshtastic/firmware/blob/master/src/mesh/Channels.cpp] (verified, with correction §3.2) | announce/path-table; must have heard the peer's announce to encrypt to it [source: https://markqvist.github.io/Reticulum/manual/understanding.html] (verified) |

The two spaces are not isomorphic. A 16-byte RNS hash **cannot** be rendered into a 4-byte Meshtastic NodeNum (the VERP "embed the foreign address in the local one" trick — `[source: https://cr.yp.to/proto/verp.txt]`, verified — fails here because there is no rich Meshtastic address field to embed it into). This forces a **mapping table or a payload-carried correlation token** rather than a stateless self-routing address on the Meshtastic leg.

### 2.2 The #35 aggregate-under-gateway-hash limitation

On the LXMF wire, a message is `Destination(16) + Source(16) + Ed25519 signature(64) + msgpack[timestamp, title, content, fields]`, and the signature is signed *by the Source identity* [source: https://raw.githubusercontent.com/markqvist/LXMF/master/LXMF/LXMessage.py] (verified). Because the gateway signs every bridged-out message with its own single `_lxmf_source`, **all N mesh nodes collapse to one RNS source hash** — exactly what the verification array flags: "bridged messages aggregate under the gateway's source hash unless per-peer identities are used (matches MeshForge Issue #35)" (verified). The RNS recipient therefore has no per-node address to reply *to*; "reply" goes to the gateway, and the gateway must demultiplex by some side channel.

Symmetrically, inbound LXMF at `_on_lxmf_receive` has no usable destination field, so the gateway cannot tell *which* mesh node a reply was "for" without having recorded the outbound leg. **Reply routing is fundamentally a state problem** the moment one side's address space can't carry the other's address.

---

## 3. How Each Ecosystem Addresses & Replies

### 3.1 Reticulum / LXMF

**Addressing.** A destination is a 16-byte hash. The user-facing simplification "SHA-256 of the dotted name with the public key appended as an aspect" is *materially imprecise*: the verification array (high confidence, **claim not supported as stated**) found the real mechanism in `RNS/Destination.py` is two-stage — `name_hash = SHA-256(expanded_name)[:10]`, then `SHA-256(name_hash + identity.hash)[:16]`, where `identity.hash` is itself a truncated-SHA-256 *of the public key*, not the raw key [source: https://raw.githubusercontent.com/markqvist/Reticulum/master/RNS/Destination.py]. The 16-byte size and the uniqueness property both hold; the literal recipe differs. **For the gateway this distinction is immaterial** — we treat the 16-byte hash as the canonical RNS address and never recompute it.

**The reply primitive is a Source↔Destination swap.** The LXMF Source field is the sender's own `lxmf.delivery` SINGLE destination hash; a reply is literally `LXMessage(destination=original.source, source=original.destination, ...)` [source: https://raw.githubusercontent.com/markqvist/LXMF/master/LXMF/LXMessage.py] (verified, high confidence — the source/destination swap and `RNS.Destination(identity, OUT, SINGLE, "lxmf", "delivery")` reconstruction were confirmed verbatim). **No separate reply-address field is needed: Source IS the reply address.** This is the single most important fact for the design — the gateway must capture and persist each inbound LXMF Source hash.

**Resolution.** To *encrypt* to a hash you still need its public key, learned from a prior announce; `RNS.Identity.recall(dest_hash)` returns the cached Identity and `recall_app_data` returns the last-announced display name [source: https://reticulum.network/manual/reference.html] (verified). So the gateway can reply only to peers it has *heard announce*; it must persist `(hash → Identity)` because RNS's in-memory tables age out, bounded by the 128-hop announce horizon (`PATHFINDER_M`) [source: https://markqvist.github.io/Reticulum/manual/understanding.html] (verified).

**Delivery methods & path-gating.** LXMF constants: `OPPORTUNISTIC=0x01` (single packet, best-effort), `DIRECT=0x02` (default, over an RNS Link, with delivery confirmation), `PROPAGATED=0x03` (store-and-forward via a propagation node), `PAPER=0x05` [source: https://raw.githubusercontent.com/markqvist/LXMF/master/LXMF/LXMRouter.py] (verified). The LXMRouter already handles `has_path`/`request_path`/retries internally — for OPPORTUNISTIC it fires `request_path()` and waits `PATH_REQUEST_WAIT` (7s) before queueing [source: same] (verified). **MeshForge does not need to hand-roll path discovery for replies**, but must tolerate up-to-7s reply latency and consume the delivery callbacks. `PROPAGATED` is the native way to reach an offline party — directly analogous to the gateway's own SQLite retry queue.

**Threading.** LXMF carries explicit field constants in the msgpack `fields` dict, *not* in the address: `FIELD_THREAD=0x08`, `FIELD_REPLY_TO=0x30` (the full `LXMessage.hash` being replied to), `FIELD_REPLY_QUOTE=0x31`, and a custom range `0xFB–0xFD` [source: https://raw.githubusercontent.com/markqvist/LXMF/master/LXMF/LXMF.py] (verified). MeshForge's existing `meshforge_*` keys live correctly in this custom space.

**Destination types.** SINGLE (multi-hop, per-peer ECDH), PLAIN (never multi-hop), LINK (ephemeral channel). **Correction (verification, not supported as stated):** the claim that GROUP is "multi-hop but bootstrapped through a single destination" is wrong — the primary source says GROUP is "not currently transported over multiple hops" (multi-hop GROUP is a planned future upgrade) [source: https://markqvist.github.io/Reticulum/manual/understanding.html]. LXMF uses SINGLE only, so this does not affect the gateway, but it rules out GROUP as a fan-out mechanism for "broadcast to all RNS."

### 3.2 Meshtastic

**Addressing.** Canonical address is the `NodeNum` (`fixed32`), derived from MAC bytes 2–5; `!hex8` is its rendering and `0x12345678`/`!deadbeef`/decimal forms are interchangeable (parse the last 8 hex digits) [source: https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/mesh.proto] (verified — note the array's "vendor-unique upper byte via IEEE OUI" phrasing is imprecise; the OUI is 3 bytes, the uniqueness is de-facto). **The gateway's address layer must key on the integer NodeNum, treating `!hex8` as display** — which `CanonicalMessage.from_meshtastic` already does (`/opt/meshforge/src/gateway/canonical_message.py` L150-163).

**Correlation envelope.** `MeshPacket{from, to, id}`: `id` is reused for both dedup and ACK matching; a reply/ACK correlates via `request_id == original id` [source: https://deepwiki.com/meshtastic/protobufs/2-message-architecture] (secondary). **The gateway must persist `(packet_id, from, channel)` at RX** so a downlinked reply can target `to=originator` and so duplicate flooded copies aren't re-bridged.

**Channel decode-gate.** **Correction (verification, not supported as stated):** the `MeshPacket.channel` byte is *not* a hash of the name alone — `generateHash()` computes `h = xorHash(name) ^ xorHash(psk.bytes)`, a combined name+PSK hash [source: https://github.com/meshtastic/firmware/blob/master/src/mesh/Channels.cpp] (verified correction, high confidence). The frequency-slot selection uses **DJB2, not xorHash**. The PSK rules (0/16/32 bytes; default primary key `0x01`) are correct. **Operational consequence for MeshForge:** the gateway only sees channels whose `(name, PSK)` it holds — this is exactly the `channel_feed_dark` / `mqtt_root_drift` lesson, and the bridge must match by channel **name**, not the box-local slot index (Issue #77, the 06-06 false-alarm).

**Delivery confirmation — the honest signal.** `want_ack` opt-in: DMs get a real end-to-end ACK; broadcasts use an *implicit* ACK (sender overhears any rebroadcast of its own packet) [source: https://meshtastic.org/docs/overview/mesh-algo/] (verified). ACK/NAK ride `ROUTING_APP` (PortNum 5): an ACK is an empty `Routing` with `error_reason=NONE` + `request_id`; a NAK sets a non-zero `Error` enum (`NO_ROUTE`, `MAX_RETRANSMIT`, `NO_CHANNEL`, `PKI_FAILED`, …) [source: https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/mesh.proto] (verified). **This is THE delivery proof the gateway should relay across the boundary** — watch for an inbound `ROUTING_APP` whose `request_id` equals the bridged packet's `id`; this maps directly onto the `delivery_confirmation_stall` probe (#74). Managed flooding means transmitting is *not* delivery — only the ACK is honest (verified) — which validates MeshForge's "Sent (not guaranteed)" wording (#16).

**PKC DMs bound what "reply to a DM" can mean.** Since fw 2.5, DMs use per-node Curve25519 + AES-256, signed by the sender; `pki_encrypted`/`public_key` distinguish PKC from legacy channel-key DMs [source: https://meshtastic.org/docs/development/reference/encryption-technical/] (verified). **A gateway cannot decrypt a PKC DM addressed to a third-party node** — it can only bridge DMs addressed to its own NodeNum, or channel-keyed traffic it holds the PSK for. To inject a reply *to* a specific node it must either send PKC (needs the target's `public_key` from NodeInfo) or fall back to channel-keyed addressing with `to` set.

**MQTT downlink — the concrete inject primitive.** Publish JSON to `msh/REGION/2/json/mqtt/` with `from`, optional `to` (NodeNum; omit for broadcast), `type=sendtext`, against a channel named `mqtt` with `downlink_enabled` [source: https://meshtastic.org/docs/software/integrations/mqtt/] (verified). This is exactly MeshForge's `DownlinkInjector` (`/opt/meshforge/src/gateway/mqtt_downlink_inject.py`) and the #34/#40/#77 topic work; the `to` NodeNum is how a bridged RNS reply gets addressed back.

**Store & Forward** (PSRAM ESP32 servers, ~11k records, off on the default channel) [source: https://meshtastic.org/docs/configuration/module/store-and-forward-module/] (verified) is server-dependent and unreliable as a gap-filler — **the gateway's own SQLite queue is the one durable replay buffer**.

---

## 4. Proven Cross-Protocol Bridge Patterns — What Transfers

### 4.1 Matrix bridge taxonomy & the Application Service namespace

Matrix classifies bridges by how much sender identity survives: **bridgebot** (identity lost), **virtual-user / Bot-API** (per-sender ghosts, but no native DM/presence), **simple-puppeting**, and **double-puppeting** ("the holy-grail … with all user metadata intact") [source: https://matrix.org/docs/older/types-of-bridging/] (the four-way summary is **not fully supported** — the doc actually lists eight categories and the official name is "Bot-API (aka Virtual user)"; the bridgebot/double-puppet quotes are verbatim-correct). **MeshForge today is bridgebot-style** (the #35 collapse). The lift to make replies route is to reach at least **virtual-user**: a stable per-sender identity on the far side.

The load-bearing primitive is the **exclusive namespace**: the bridge claims a regex range of user IDs (`@_irc_.*`, `exclusive=true`) and the homeserver routes any event in that range to the bridge, which provisions ghosts lazily [source: https://spec.matrix.org/v1.3/application-service-api/] (mechanism **mostly verified**; corrections: the spec says "POSIX regular expression" not "POSIX extended," and `exclusive` blocks *both* humans and other services). **What transfers:** toward RNS, mint a deterministic per-mesh-node LXMF identity under a gateway-owned namespace, so "a reply to that identity *is* the address." **What doesn't:** MeshForge has no homeserver to route on a regex — RNS routing is by destination hash, so the "namespace" is realized as *N actual LXMF identities the gateway holds keys for*, not a regex claim. That is cheap on RNS (16-byte hashes) and is the correct direction.

**Double-puppeting** — the bridge holding your real account token so your native activity attributes to *you* on the far side [source: https://docs.mau.fi/bridges/general/double-puppeting.html] (verified) — is the conceptual ceiling but **not achievable**: MeshForge can't grant tokens. The realistic mode is **relay mode**: one relay login carries everyone, identity preserved by a display-name prefix, with the documented limitation that "reactions from relayed users will not be bridged at all" [source: https://docs.mau.fi/bridges/general/relay-mode.html] (verified). **This is MeshForge's actual operating mode** — mesh nodes never "log in" to RNS. MeshForge already does the prefix half (`[Mesh:xxxx]`); relay mode's lesson is that **prefix-only relaying cannot round-trip replies without a side mapping table.**

### 4.2 The mautrix persistence schema — the table MeshForge should mirror

mautrix bridgev2 defines `Ghost`, `Portal`, `Message`, `Reaction`, `UserLogin`, `UserPortal`, keyed by stable `networkid` types, with `PortalKey={ID, Receiver}` disambiguating the same remote chat for different local receivers [source: https://pkg.go.dev/maunium.net/go/mautrix@v0.25.0/bridgev2/database] (**verified** — minor: `PortalKey.Receiver` is a `UserLoginID`, and a `User`/`BackfillTask` table also exist). The **Message** table stores *both* the remote `MessageID/PartID` and the local `EventID`, indexed both ways (`GetPartByID` / `GetPartByMXID`), with `ReplyTo`/`ThreadRoot` stored as remote IDs and a `SendTxnID` for idempotency. **This is precisely the correlation table that makes reply/edit/reaction route** — and it maps onto MeshForge's `MF013` `connect_tuned`/`db_inventory` discipline. MeshForge already has the *shape* in `ContactMappingTable` (`/opt/meshforge/src/gateway/contact_mapping.py`, persistent cross-protocol identity rows) and `SessionStore`; what's missing is the **per-message** `(meshtastic_packet_id, lxmf_message_hash, channel, sender)` correlation row.

### 4.3 XMPP transports & escaping — deterministic addressing

XEP-0100 is the oldest codified pattern: a legacy contact is rendered as `<LegacyUserAddress@gateway.example.com>`, so the gateway domain + encoded foreign address *is* the reply route, with first-presence login + queued-event buffering [source: https://xmpp.org/extensions/xep-0100.html] (verified). XEP-0106 gives a reversible `\hexhex` escape (`@`→`\40`) explicitly so "non-XMPP addresses … can be packed into one JID localpart" [source: https://xmpp.org/extensions/xep-0106.html] (verified). XEP-0114 gives the **security boundary**: a component "MAY send stanzas from any user at its hostname" but the `from` domain "MUST match the hostname of the component" — it can speak for its own namespace, never spoof outside it [source: https://xmpp.org/extensions/xep-0114.html] (verified). **What transfers:** a *reversible, deterministic* encoding (hex/base32 of the node id) removes the need to persist the alias for the addressing half — and the trust boundary mirrors MeshForge's MF019 chokepoint + foreign-`@rns`-owner guard (#69): the gateway must be the *sole* authority minting its ghost namespace. **What doesn't:** the Meshtastic side has no localpart to escape *into* (4-byte address, ~4-char `short_name`), so deterministic-address-only works toward RNS, not toward Meshtastic.

### 4.4 Proxy-number pools — the model for the constrained Meshtastic side

Twilio Proxy multiplexes a *small pool* of shared proxy identities across many `(A,B)` pairs, disambiguating by the `(proxy-number, real-sender-number)` tuple → Session; the hard constraint is **one participant cannot be in two concurrent sessions on the same proxy number** [source: https://www.twilio.com/docs/proxy/understanding-phone-number-management] (verified). Pool sizing is driven by **max concurrent sessions per participant**, not total count — a sequential-reuse worker needs one number [source: https://www.twilio.com/docs/proxy/phone-numbers-needed] (verified). It has a sticky/non-sticky policy (`prefer-sticky` default — same alias across sessions for recognizability) [source: same first URL] (verified — exact enum is `numberSelectionBehavior: prefer-sticky`), a **sliding TTL that resets on each interaction** + delayed GC of closed rows [source: https://www.twilio.com/docs/proxy/api/session] (verified), and an **out-of-session callback** carrying prior history when a reply hits a stale mapping [source: https://www.twilio.com/docs/proxy/out-session-callback-response-guide] (verified). **This is the right model for the Meshtastic egress side**: a small pool of DM-presentable gateway-owned aliases, keyed `(alias, mesh-recipient-node)`, sized to *max concurrent conversations a single mesh node is having* (a handful on a quiet mesh), `prefer-sticky` for UX, sliding TTL, and an honest "this conversation expired" bounce instead of a silent drop (the #16 anti-pattern). **What doesn't transfer:** VERP-style self-describing addresses — they require *one outbound copy per recipient* [source: https://docs.mailman3.org/projects/mailman/en/latest/src/mailman/mta/docs/verp.html] (verified), prohibitive on LoRa airtime, so VERP is usable only for the 1:1 DM leg (one copy anyway), never for fan-out.

### 4.5 DTN & SSB — the store-and-forward and key-as-address lessons

DTN BPv7 is a store-carry-forward overlay on **retention constraints**: a bundle can't be discarded while `Forward-pending` is set, released only on convergence-layer success or abandonment [source: https://www.rfc-editor.org/rfc/rfc9171.html] (verified — minor: a third removal path exists when the node is itself the destination). Bundles carry a **Lifetime** + a **Bundle Age** block for clock-less nodes [source: same] (verified — Age is a running counter updated each hop). **This formalizes MeshForge's existing retry queue**, and the Bundle Age pattern matters because mesh radios have bad RTCs → **track elapsed-since-enqueue, not absolute radio timestamps**. DTN's **EID late binding** (dtn:/ipn: schemes, name→route resolved at source/transit/destination) [source: same] (verified) directly informs the `CanonicalMessage` addressing contract — a scheme-tagged address (`mesh:!a2e95ba4` vs `lxmf:<hash>`) resolved at egress is exactly what `format_reply_token` already produces. **DTN dropped custody transfer** from BPv7 because "estimating suitable timer values can be difficult" [source: https://datatracker.ietf.org/doc/html/rfc4838] (verified) — the *same* lesson as MeshForge's RNS RPC-timeout saga (#68/#72), validating best-effort + retry + honest UI over promised end-to-end custody. DTN **status reports** route to a separate `report-to` EID independent of source [source: RFC 9171] (verified) — the model for `CanonicalMessage` carrying a return/reply EID so a confirmation routes back asynchronously.

SSB makes the **Ed25519 key the address** (`@pubkey.ed25519`), content-addresses messages by hash (`%hash.sha256`), and — crucially — **a reply is a new message in your own feed referencing the target by ID**, because you can't write to another's feed [source: https://ssbc.github.io/scuttlebutt-protocol-guide/] (verified). This is the RNS/LXMF model exactly, and confirms that **carrying an explicit in-reply-to message reference (LXMF `FIELD_REPLY_TO=0x30`) is the durable way to thread across asynchronous links.** SSB's `createHistoryStream` delta-sync (request only messages newer than a sequence number) [source: same] (verified), and Briar's BSP **OFFER/REQUEST/ACK/MESSAGE** 3-way reconciliation over a causal DAG [source: https://code.briarproject.org/briar/briar-spec/-/raw/master/protocols/BSP.md] (verified), are the efficient peer-gateway sync primitives — far cheaper than epidemic full-exchange [source: https://arxiv.org/pdf/1210.0965] (secondary) — and Spray-and-Wait's bounded-L replication [source: http://conferences.sigcomm.org/sigcomm/2005/paper-SpyPso.pdf] (verified) is the principled cap for multi-gateway relay airtime. **Loop prevention is non-negotiable:** Matrix tags its own bridged messages so re-ingested copies are dropped [source: https://matrix.org/docs/older/types-of-bridging/] (verified) — MeshForge already lives this (`via_mqtt` loop guard, `meshforge_relayed_by`); **any reply feature must ship with an explicit loop-tag invariant** or an R→M delivery re-uplinks as a fresh M→R message and bounces forever.

### 4.6 What transfers — summary

| Pattern | Transfers? | To which leg |
|---|---|---|
| Virtual-user / ghost per sender | **Yes** | Toward RNS (cheap address space) |
| Deterministic encoded address (XMPP) | Partial | Toward RNS only |
| Proxy-number pool + sticky + sliding TTL + out-of-session bounce | **Yes** | Toward Meshtastic (scarce address space) |
| Message-correlation table (mautrix) | **Yes** | Both legs (the load-bearing store) |
| Double-puppeting (token delegation) | **No** | — (no token mechanism) |
| VERP self-describing address | DM only | Meshtastic 1:1 (airtime-prohibitive for fan-out) |
| DTN retention/lifetime/report-to | **Yes** | Queue semantics + return-path |
| SSB/LXMF reply-by-reference + FIELD_REPLY_TO | **Yes** | Threading, both legs |
| Loop-tag invariant | **Mandatory** | Both legs |

---

## 5. Concrete Design Proposal for MeshForge

The design is **asymmetric** and **composes existing modules**. It does not change any wire format (§6).

### 5.1 Two-layer model

**Layer A — durable identity & correlation (the mautrix `Message`+`Ghost` analogue).** A SQLite store, registered in `utils.db_inventory` (MF013), via `connect_tuned` (MF013). Reuse `ContactMappingTable` for the identity half and extend with a **per-message correlation table**:

```
bridge_correlation(
  corr_id            TEXT PRIMARY KEY,   -- gateway-minted, opaque
  mesh_node          TEXT,              -- !hex8 (NodeNum-derived)
  mesh_packet_id     INTEGER,           -- for ACK request_id match
  channel            TEXT,              -- by NAME (#77), not slot
  rns_peer_hash      TEXT,              -- 32-hex lxmf.delivery source
  lxmf_msg_hash      BLOB,              -- for FIELD_REPLY_TO threading
  direction          TEXT,              -- 'm2r' | 'r2m'
  created_ts         REAL,
  last_interaction_ts REAL              -- sliding-TTL anchor
)
```
Indexed both ways (`by mesh_packet_id`, `by lxmf_msg_hash`, `by rns_peer_hash`) — the bidirectional `GetPartByID`/`GetPartByMXID` pattern.

**Layer B — Meshtastic-side alias pool (the Twilio model).** A bounded pool of gateway-owned, DM-presentable handles. Realistically this is **NODEINFO-injected virtual origins** via the existing `DownlinkInjector.inject_nodeinfo` — the gateway already mints true-origin identities on the mesh side, which is the puppeting primitive. Pool sized to *max concurrent conversations per mesh node*, `prefer-sticky`, sliding TTL on `last_interaction_ts`, out-of-session honest bounce.

### 5.2 Mapping onto `CanonicalMessage`

No new fields are strictly required — the existing #66 fields cover it:
- `reply_to` already holds a protocol-qualified token; `format_reply_token`/`parse_reply_token` (`/opt/meshforge/src/gateway/canonical_message.py` L445-469) are the cross-protocol address shape. **Promote it from "optional hint" to "populated on every bridged message."**
- `ack_of` correlates ACKs; reuse it to carry the `corr_id` round-trip.
- `metadata` carries the LXMF `fields` — add `meshforge_corr_id` (custom range `0xFB–0xFD` on the wire, §3.1) alongside the existing `meshforge_reply_to`. This is the SSB/LXMF "reply by reference" — set LXMF `FIELD_REPLY_TO=0x30` to the original `lxmf_msg_hash` so stock NomadNet/Sideband thread it natively.

### 5.3 Reply flow (the precedence ladder, made durable)

**M→R (mesh node messages an RNS peer):** at `_process_mesh_to_rns` (`/opt/meshforge/src/gateway/_rns_bridge_xform.py` L184-361), in addition to today's `meshforge_reply_to` field (L219/L235), **insert a `bridge_correlation` row** and set LXMF Title/`fields` as now. The RNS Source the peer sees is still the gateway's hash (#35 unchanged at wire level) — but the gateway now has a durable row to demux the reply.

**R→M (RNS peer replies):** extend the existing precedence rungs in `_process_rns_to_mesh` (L567-601). Current order: `@addr` (ungated, always wins) > echoed `meshforge_reply_to` field > **in-memory `ReplyContextStore`** > `IdentityBinder` contact > broadcast. **Change:** swap the volatile `ReplyContextStore` (in-memory, restart-lossy, 24h — `/opt/meshforge/src/gateway/reply_context.py` L12-14) rung for a **durable `bridge_correlation` lookup keyed by `rns_peer_hash`** (and `SessionStore`, which is already durable). Keep `ReplyContextStore` as a fast in-process cache in front of the DB. The `@id`/`@short_name` parser (`_resolve_mesh_destination`, L462-483) stays the ungated top rung and the re-validation gate for DB rows (it already re-validates `IdentityBinder` output, L591-597 — apply the same to correlation rows).

**Egress to a specific node:** route through `DownlinkInjector.inject` (`to=NodeNum`) in `mqtt_bridge` mode, or `send_to_meshtastic(destination=!hex8)` otherwise, with `want_ack=True`; watch `ROUTING_APP` `request_id == mesh_packet_id` to confirm and feed the #66/#74 ACK synthesis. **Tag every downlink** (`meshforge_relayed_by` / a downlink marker) so the loop guard drops the re-ingested copy.

### 5.4 Changes by file

- **`/opt/meshforge/src/gateway/canonical_message.py`** — populate `reply_to` on every bridged message via `format_reply_token`; add `meshforge_corr_id` to the LXMF `fields` serialization (L295-331). No new dataclass fields needed.
- **`/opt/meshforge/src/gateway/_rns_bridge_xform.py`** — at `_process_mesh_to_rns`: write a `bridge_correlation` row + set `FIELD_REPLY_TO`. At `_process_rns_to_mesh`: replace the memory rung (L581-586) with a durable correlation lookup; keep the `@addr` top rung and re-validation discipline.
- **`/opt/meshforge/src/gateway/message_routing.py`** — the router already reads both `BridgedMessage`/`CanonicalMessage` shapes via `getattr` fallbacks (L131-132); extend `destination_network`/`destination_address` resolution to consult the correlation table when `destination_address is None`.
- **New `bridge_correlation` table** — co-locate with `SessionStore`/`ContactMappingTable` patterns; `connect_tuned`, lazy DB open, `DBSpec` in `utils.db_inventory`, sliding-TTL sweep on access (no daemon thread — MF010), oldest-evict cap.
- **`/opt/meshforge/src/gateway/mqtt_downlink_inject.py`** — already the alias/puppet primitive; wire the pool allocator (sticky policy + TTL) on top.

### 5.5 Collision handling

Projecting an RNS display name into Meshtastic's ~4-char `short_name` space risks colliding with a real node. Follow the IRC-bridge discipline: a **deterministic suffix/prefix marking bridge origin** + an exclusive gateway-owned alias namespace so no inbound RNS name can impersonate a real mesh node [source: https://matrix-org.github.io/matrix-appservice-irc/latest/usage.html] (verified). `_resolve_mesh_destination` already returns `None` on ambiguous `short_name` (L476-483) — keep that fail-to-broadcast-not-misdeliver default.

---

## 6. Constraints & Risks

1. **Wire-compat invariant (non-negotiable).** The MeshForge fork SSOT forbids changing RNS crypto primitives or the packet/announce wire format — "that forks the *network*, not the code." This design **adds nothing to the wire**: it uses LXMF's existing `fields` custom range (`0xFB–0xFD`) and `FIELD_REPLY_TO` (`0x30`), and Meshtastic's existing `from`/`to`/`id`/`ROUTING_APP` — all already-standard. The correlation lives entirely in the gateway's SQLite, not on the wire.
2. **LoRa airtime & MTU.** Meshtastic payloads are ~200 bytes; per-recipient VERP copies are out (§4.4). Fan-out M→R stays a static `default_lxmf_destination` list; the new addressability is **DM-only**. Reply tokens must be tiny (an opaque `corr_id`, not a 16-byte hash echoed in-band).
3. **PSK / channel decode-gate.** The gateway only sees `(name, PSK)` channels it holds; a missed PSK rotation makes a channel silently dark (Issue #77 / `channel_feed_dark`). Reply routing inherits this — a reply on a channel the gateway lost the key for cannot be bridged. Match by **channel name**, never the box-local slot.
4. **PKC DM ceiling.** The gateway can only inject channel-keyed downlinks or PKC DMs to nodes whose public key it has; it cannot decrypt third-party PKC DMs (§3.2). "Reply to a DM" therefore means channel-keyed or gateway-targeted, documented honestly.
5. **Honest delivery (#16).** Transmitting ≠ delivered. The only honest signal is the Meshtastic `ROUTING_APP` ACK and the LXMF DIRECT delivery callback. DTN's removal of custody transfer because timers are unestimable is the cautionary precedent — **promise best-effort + retry, surface the real ACK/NAK, bounce honestly on stale mappings.**
6. **Loop hazard (bidirectional).** Every downlink must be loop-tagged or replies bounce forever; this is the single highest-risk regression and must ship with a test.
7. **Default-off rollout.** All identity/reply/session machinery is currently gated off; the durable correlation should ship the same way and soak per-box before fleet-wide enable.

---

## 7. Phased Implementation Sketch

Each step is independently shippable and testable, slice-able across sessions:

1. **Durable correlation table (no behavior change).** Add `bridge_correlation` (DBSpec, `connect_tuned`, lazy-open) and *write* rows on M→R and R→M, but don't yet read them for routing. Pure observability. Tests: row written, TTL sweep, cap eviction.
2. **Promote the read rung.** Replace the in-memory `ReplyContextStore` rung in `_process_rns_to_mesh` with a durable correlation lookup; keep the in-memory store as a front cache. Gated by the existing `reply_routing_enabled`. Tests: reply routes after a simulated restart (the property the in-memory store fails).
3. **LXMF native threading.** Set `FIELD_REPLY_TO=0x30` to the original `lxmf_msg_hash` on bridged-out messages so stock clients thread; read it on inbound. Tests against a real LXMF payload shape (per the §3.1 verified field constants).
4. **ACK consumption → honest confirmation.** Watch `ROUTING_APP` `request_id` for bridged DMs; feed the #66/#74 ACK synthesis with real NAK reasons (`MAX_RETRANSMIT`, `NO_CHANNEL`, …) instead of `[delivered]` text guesses.
5. **Meshtastic alias pool (Twilio model).** Pool allocator over `DownlinkInjector.inject_nodeinfo`, `prefer-sticky`, sliding TTL, out-of-session honest bounce. Tests: concurrency constraint (one node not in two sessions on one alias), sticky reuse, expired-bounce.
6. **Loop-tag hardening.** Explicit downlink loop-tag invariant + regression test that an R→M delivery is *not* re-uplinked as a fresh M→R.
7. **Per-node RNS identity (the #35 structural fix, optional/advanced).** Mint a deterministic per-mesh-node LXMF identity (the virtual-user lift) so replies address the node directly. Highest-value, highest-risk; needs a field test and an interop proof. Defer until 1–6 are field-proven.

---

## 8. Open Questions / Field-Test Needs

- **Does a stock NomadNet/Sideband client echo `FIELD_REPLY_TO`/reply correctly to the gateway's hash, and does a Source-swap reply from the gateway land?** The LXMF source-swap reply mechanic is verified in *library source*, but the end-to-end behavior through real clients + the gateway's single identity is **unverified in the field** — this is the #1 field test.
- **Alias-pool sizing on a real mesh.** Twilio's "max concurrent per participant" heuristic is verified for telephony; the actual concurrency on a quiet HAM mesh is unknown. Field-measure before fixing the pool size.
- **PKC vs channel-key downlink reliability.** Whether `DownlinkInjector`-injected NODEINFO + text downlinks attribute correctly at the receiving radio for *reply* DMs (not just display) is unproven for the reply path; the existing inject arc proves display, not bidirectional DM.
- **Propagation-node store-and-forward for offline mesh users.** LXMF `PROPAGATED` is verified as a mechanism, but whether MeshForge should run/point at a propagation node vs rely solely on its own queue is an architecture decision needing a soak.
- **Thin spots in the research.** The mautrix schema, LXMF/RNS internals, Twilio Proxy, and DTN RFCs are all primary-source verified. The **Meshtastic MeshPacket envelope** detail (`request_id`/`reply_id` correlation) rests partly on a *secondary* DeepWiki source — confirm against `mesh.proto` directly before relying on `reply_id` semantics. The epidemic-routing survey is also secondary (directional, not load-bearing here).

---

## Sources

- LXMF source (LXMessage, LXMRouter, field constants): https://raw.githubusercontent.com/markqvist/LXMF/master/LXMF/LXMessage.py · https://raw.githubusercontent.com/markqvist/LXMF/master/LXMF/LXMRouter.py · https://raw.githubusercontent.com/markqvist/LXMF/master/LXMF/LXMF.py
- Reticulum manual & Destination source: https://reticulum.network/manual/understanding.html · https://markqvist.github.io/Reticulum/manual/understanding.html · https://reticulum.network/manual/reference.html · https://raw.githubusercontent.com/markqvist/Reticulum/master/RNS/Destination.py
- Meshtastic protobufs, firmware, docs: https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/mesh.proto · https://github.com/meshtastic/firmware/blob/master/src/mesh/Channels.cpp · https://meshtastic.org/docs/overview/mesh-algo/ · https://meshtastic.org/docs/development/reference/encryption-technical/ · https://meshtastic.org/docs/software/integrations/mqtt/ · https://meshtastic.org/docs/configuration/module/store-and-forward-module/ · https://deepwiki.com/meshtastic/protobufs/2-message-architecture (secondary)
- Matrix / mautrix / IRC bridge: https://matrix.org/docs/older/types-of-bridging/ · https://spec.matrix.org/v1.3/application-service-api/ · https://pkg.go.dev/maunium.net/go/mautrix@v0.25.0/bridgev2/database · https://docs.mau.fi/bridges/general/double-puppeting.html · https://docs.mau.fi/bridges/general/relay-mode.html · https://matrix-org.github.io/matrix-appservice-irc/latest/usage.html
- XMPP transports: https://xmpp.org/extensions/xep-0100.html · https://xmpp.org/extensions/xep-0106.html · https://xmpp.org/extensions/xep-0114.html
- Twilio Proxy: https://www.twilio.com/docs/proxy/understanding-phone-number-management · https://www.twilio.com/docs/proxy/phone-numbers-needed · https://www.twilio.com/docs/proxy/api/session · https://www.twilio.com/docs/proxy/out-session-callback-response-guide
- VERP / email threading: https://cr.yp.to/proto/verp.txt · https://docs.mailman3.org/projects/mailman/en/latest/src/mailman/mta/docs/verp.html · https://www.rfc-editor.org/rfc/rfc5322 · https://www.mailgun.com/blog/product/building-an-sms-to-email-gateway/ (secondary)
- DTN / SSB / Briar: https://www.rfc-editor.org/rfc/rfc9171.html · https://datatracker.ietf.org/doc/html/rfc4838 · https://datatracker.ietf.org/doc/html/rfc6693 · https://ssbc.github.io/scuttlebutt-protocol-guide/ · https://code.briarproject.org/briar/briar-spec/-/raw/master/protocols/BSP.md · https://code.briarproject.org/briar/briar-spec/-/raw/master/protocols/BTP.md · http://conferences.sigcomm.org/sigcomm/2005/paper-SpyPso.pdf · https://arxiv.org/pdf/1210.0965 (secondary)
- MeshForge codebase (verified against source): `/opt/meshforge/src/gateway/canonical_message.py`, `_rns_bridge_xform.py`, `rns_bridge.py`, `message_routing.py`, `reply_context.py`, `session_store.py`, `identity_binding.py`, `contact_mapping.py`, `mqtt_downlink_inject.py`

---

*Codebase verification note: I confirmed the baseline against the actual source. Beyond the files the baseline named, the gateway already ships `contact_mapping.py` (`ContactMappingTable` — a persistent SQLite cross-protocol identity map with `resolve_destination`) and `mqtt_downlink_inject.py` (`DownlinkInjector` with `inject`/`inject_nodeinfo` — the true-origin puppeting primitive). The design in §5 therefore composes existing modules rather than building new ones; the genuinely missing piece is the per-message `bridge_correlation` table (the mautrix `Message`-table analogue) and the Meshtastic-side alias pool atop the existing injector.*


---

## Appendix A — MeshForge gateway addressing baseline (as-is, code-grounded)

I have everything needed. Here is the baseline.

# MeshForge Gateway Addressing & Reply-Routing — "What We Have" Baseline

All paths absolute. Core files: `canonical_message.py`, `rns_bridge.py`, `_rns_bridge_xform.py` (the actual conversion logic, extracted from rns_bridge), `message_routing.py`, `message_queue.py`, plus `reply_context.py`, `session_store.py`, `identity_binding.py`.

## (1) CanonicalMessage fields relevant to sender/recipient/threading

`/opt/meshforge/src/gateway/canonical_message.py`:
- `id` (L71) — UUID, message identity.
- `source_network` (L74) / `source_address` (L75) — sender. For Meshtastic this is `!hex8` (L150); for RNS it's the LXMF `source_hash.hex()` (L273-276); for MeshCore a pubkey prefix.
- `destination_address` (L78, `None`=broadcast) / `destination_network` (L79, set by router).
- `content` (L82) / `payload: Optional[bytes]` (L83) / `message_type` (L84).
- `is_broadcast` (L87), `via_internet` (L90), `origin` (L91).
- `metadata: Dict` (L97) — the catch-all; LXMF `fields`+`title` land here (L295-299), and Meshtastic stashes `channel`/`packet_id`/`raw_packet` (L183-190).
- **Issue #66 threading fields** (L99-116): `ack_required: bool` (L105), `ack_of: Optional[str]` (L110, correlates an ACK to the acked msg), `reply_to: Optional[str]` (L116, protocol-qualified reply address).
- **Reply token helpers** (L445-469): `format_reply_token(protocol, address)` produces `"{protocol}:{address}"` (e.g. `meshtastic:!abcd1234`); `parse_reply_token` splits it back. This is the canonical cross-protocol address shape.
- Important: the **legacy `BridgedMessage`** (`rns_bridge.py` L117-165) is what actually flows through the runtime bridge loop, not CanonicalMessage. It has `source_id`/`destination_id` (not `source_address`). The ack/reply fields round-trip through `BridgedMessage.metadata` under `meshforge_ack_*`/`meshforge_reply_to` keys (`canonical_message.py` L311-331, L368-378). The router reads both shapes via `getattr` fallbacks (`message_routing.py` L131-132).

## (2) Mesh→RNS addressing, and whether the RNS recipient can reply to the original Mesh sender

Addressing — `_rns_bridge_xform.py::_process_mesh_to_rns` (L184-361):
- Identity is carried in the **LXMF title + `fields` dict**, body stays clean. Title = `"{long_name} ({source_id}) via Meshtastic"` (L211-214). `fields` (L229-236) carries `meshforge_from_id`, `meshforge_from_long`, `meshforge_from_short`, `meshforge_channel`, `meshforge_source_network="meshtastic"`, and **`meshforge_reply_to` = `format_reply_token("meshtastic", source_id)`** (L219, L235).
- Destination resolution (L285-298): a directed DM (`destination_id` + not broadcast) resolves via `_get_rns_destination()` (L363-389: node_tracker `rns_hash` first, then the IdentityBinder SSOT if gated). A broadcast **fans out to every `config.rns.get_lxmf_destinations()`** (L293-298). There is no per-recipient RNS addressing for broadcast — it's a static config list.
- Actual LXMF send is `send_to_rns()` (`rns_bridge.py` L1106-1252): builds `RNS.Destination(dest_identity, OUT, SINGLE, "lxmf", "delivery")` (L1202-1208) after `has_path`/`request_path`/`Identity.recall`, all wrapped in `bounded_call` (#57/#74 wedge guard).

Can the RNS recipient reply back to the original Mesh sender? **Yes, but gated and best-effort** (this is "Theme-A reply routing", default OFF via `rns.reply_routing_enabled`, `_rns_bridge_xform.py` L48-57). Three precedence rungs in `_process_rns_to_mesh` (L567-601):
1. **`@addr` in body** (L553-565) — always wins, ungated.
2. **Echoed `meshforge_reply_to` field** (L576-580, route="field") — if the RNS client echoes the field back, resolves directly.
3. **Reply-context memory** (L581-586, route="memory") — `ReplyContextStore` (`reply_context.py`) maps `peer LXMF hash → reply token` of the mesh node that last messaged them, recorded on M→R send (L300-319). **In-memory only, 24h TTL, lost on restart** (reply_context.py L12-14).
4. **Identity SSOT contact** (L587-597, route="contact") — `IdentityBinder.resolve('rns', src, 'meshtastic')`, also gated.
5. Else broadcast.

So a stock NomadNet/Sideband reply auto-routes back to the originating mesh node only when reply-routing is enabled AND (the field was echoed OR the in-memory context survived). Otherwise it falls back to broadcast. There is **also a durable session layer** (`session_store.py`) for the DM-to-gateway private-reply leg (gated `rns.sessions_enabled`), but it stores `(mesh_id, rns_peer_hash)` pairs in SQLite, not a general address book.

## (3) RNS→Mesh addressing/downlink to a specific node

`_rns_bridge_xform.py::_process_rns_to_mesh` (L485-804):
- Directed downlink via a **leading `@!xxxxxxxx` or `@shortname` token in the body** (L553-565), resolved by `_resolve_mesh_destination` (L462-483): accepts `!hex8` directly, or a `short_name` uniquely resolved through `node_tracker.get_node_by_short_name`. Unresolvable/ambiguous → `None` → broadcast.
- If no `@addr`, the reply-routing rungs above (L567-601) may supply `destination`.
- Attribution prefix `[RNS:{label}] ` prepended to every chunk (L636-647); label is the original mesh node's short/long name when `meshforge_source_network=="meshtastic"` (peer-gateway relay case), else the RNS source hash prefix.
- Content is chunked to Meshtastic byte limits (`chunk_for_mesh`, L660) — each chunk carries the prefix.
- Delivery: in `mqtt_bridge` mode each chunk is enqueued to the **persistent queue** with `destination=` set to the resolved node id (L687-754); else direct `send_to_meshtastic(chunk, destination=destination, channel=...)` (L756-795). A DM uses `destination=!hex8`; broadcast uses `destination=None`.
- Underlying TX is `MeshtasticHandler.queue_send`/`send_text` → `meshtastic_protobuf_client.py` `send_text_direct`, which sets `want_ack=True` by default (L110, L162, L826) — but note the queue's "delivered" counter means "radio accepted the packet," not LoRa-ACK confirmed (`rns_bridge.py` L256-260).
- Relay-on-receive (`_maybe_relay_to_peers`, L806-909): a NomadNet-origin LXMF is forwarded to each `rns.peer_gateway_destinations` hash so one send reaches every RF preset; loop-guarded by `meshforge_relayed_by`.

## (4) Known limitations / TODOs around bidirectional addressability

- **#35 aggregate-under-gateway-hash**: This is the structural root limitation. Inbound LXMF received at `_on_lxmf_receive` (`rns_bridge.py` L1845-1923) builds `BridgedMessage(destination_id=None, ...)` — comment L1888/L1908: "LXMF doesn't have destination in received messages." All messages a gateway bridges out onto RNS use the **gateway's single `_lxmf_source`** identity (`send_to_rns`, L1218-1229), so from the RNS side every bridged mesh node collapses to one LXMF source hash. The `[Mesh:xxxx]`/`[RNS:xxxx]` text prefix is the only per-node attribution. The `label` logic at L636-645 exists precisely because "every node a given gateway relays collapses to that gateway's RNS hash" — attribution is in the body text, not in addressable RNS identity.
- **#39 @id/@short_name directed downlink**: Implemented as the `@addr` parse (L553-565) + `_resolve_mesh_destination` (L462-483) + the `meshforge_*` field namespace (L229-236). Limitation: relies on the operator/client manually typing `@id`, OR on the reply-routing memory which is **in-memory, default-off, restart-lossy** (reply_context.py L12-14). `short_name` resolution fails on ambiguity (multiple nodes share a name — node_tracker L494-496).
- **#40 bytes payload**: Resolved defensively — `BridgedMessage.__post_init__` (`rns_bridge.py` L141-148) and `_process_rns_to_mesh` entry (L512-518) and `_requeue_failed_message` (L410-414) all decode `bytes→str`. Originally an xform-local fix, centralized to close the requeue double-crash window.
- **#66 (the forward-looking arc, not in your list but central)**: application-layer ACK synthesis is built (`canonical_message` ack fields; `rns_bridge.py` `_maybe_emit_ack_for_msgid`/`_emit_ack_to_origin`/`_sweep_overdue_acks` L907-1086; `message_queue.py` pending-ack table L997-1202). Synthesizes `[delivered:/[failed:/[timeout:` text back to origin. Limitation: requires `register_pending_ack` to have been called, and the ACK is **text in the chat thread**, not a protocol-native receipt.
- Broadcast Mesh→RNS has **no addressing** beyond a static `default_lxmf_destination` list (L293-298, L1209-1216); a true broadcast can't be sent to "all RNS" — `send_to_rns` with no `destination_hash` is explicitly dropped (L1209-1216).
- All cross-protocol identity binding (`IdentityBinder`), reply memory, and sessions are **gated OFF by default** (`reply_routing_enabled`, `cross_protocol_identity_enabled`, `sessions_enabled` all default False, `_rns_bridge_xform.py` L48-96) — so out of the box, RNS→Mesh is broadcast-only unless the sender types `@addr`.

**Net baseline**: addressing in is fully native (mesh `!hex8`, LXMF source hash); addressing out collapses to (a) a gateway-owned single LXMF identity with text-prefix attribution for Mesh→RNS, and (b) `@addr`-or-broadcast for RNS→Mesh, with optional gated reply-memory/session/identity rungs to recover the original sender. True bidirectional addressability (a stable, routable per-node identity on the far side) does not yet exist — #35 is the open structural gap.
