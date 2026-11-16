# Default-PSK (AQ==) channels, shared frequency slots, and Meshtastic airtime / channel utilization

> Researcher notes, 2026-11-16. Method: read the firmware source directly rather than summaries.
> - **Firmware**: `meshtastic/firmware` `develop` @ `68f47efd` (2026-11-16, version.properties = 2.8.3), plus the release tags v2.5.0.9ac0e26, v2.6.0.f7afa9a, v2.6.11.60ec05e, v2.7.0.195b7cc, v2.7.15.567b8ea, v2.8.0.47db0e3, and v2.8.2.d5f1619.
> - **Tag dates** (from `git log`): v2.5.0 2024-08-30, v2.6.0 2025-03-10, v2.7.0 2025-06-21, v2.8.0 2026-08-31. The newest tags seen are v2.7.26 and v2.8.2.
> - **Docs and blog**: read from the docs source repo `meshtastic/meshtastic` @ `0c02d499` (2026-11-15).
> - **Access**: meshtastic.org, yycmesh.com and meshenvy.org could not be fetched (outside the session's allowed network domains). raw.githubusercontent.com, git, and web search did work.
> - **Airtime numbers** are COMPUTED with the Semtech SX127x/SX126x time-on-air formula, using the firmware's own preset table and its 16-symbol preamble. They are not measured on air. The firmware's own comment ("preambleTimeMsec = 165 … default for LongFast") matches the calculation: (16+4.25)·8.19 ms = 165.9 ms.
>
> Abbreviations: FW = firmware source, DOC = official docs/blog, CALC = my own calculation, COMM = community claim.

FW base URLs used below:
- develop: `https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/`
- v2.7.15: `https://github.com/meshtastic/firmware/blob/v2.7.15.567b8ea/`
- docs: `https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/`

---

## Q1. Shared RF channel, channel hash, and relaying of packets a node cannot decrypt (by role and rebroadcast mode)

### Takeaway
All Meshtastic "channels" (up to 8 per node) share **one** LoRa modem configuration and **one** frequency. Every node on the same preset and frequency slot therefore shares a single physical RF channel, and airtime from every channel name and key adds up. Each node's ChUtil counts every packet it hears, decryptable or not.

The 1-byte channel hash is only a decryption hint. With the default `rebroadcast_mode = ALL`, a CLIENT / ROUTER / ROUTER_LATE / CLIENT_BASE relays packets it **cannot** decrypt (foreign channel names or keys) using only the plaintext header. Many channel names therefore do not create separate relay domains. Only LOCAL_ONLY and KNOWN_ONLY, and CLIENT_MUTE / NONE, stop a node relaying foreign-channel traffic.

### Cited Findings
- **One modem per node.** Modem preset settings "are identical for all channels and can **not** be unique per channel." — [DOC channels.mdx](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/docs/configuration/radio/channels.mdx)
- **ROUTER_LATE blog wording.** It refers to "the single frequency that is shared by the entire mesh" and warns that ROUTER_LATE "adds a notable amount of traffic to the single, shared frequency." — [DOC demystifying-router-late (2025-09-25)](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2025/September/demystifying-router-late.mdx)
- **What ChUtil counts.** The receiver is promiscuous. `logAirtime(RX_LOG)` runs "one per well-formed reception … this counts packets not addressed to us, and every duplicate relay copy." `RX_ALL_LOG` covers receptions that could not be parsed (CRC failure, collision). `channelUtilizationPercent()` is the "% of the last 60s busy, all three types" (TX+RX+RX_ALL). — [FW src/airtime.h (develop)](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/airtime.h)
- **Channel hash formula.** `hash = xor(name bytes) ^ xor(expanded key bytes)`, plus `^ 0xAE` if the channel uses AEAD (a new experimental per-channel option in develop). The result is 8 bits (0–255). — [FW src/mesh/Channels.cpp `Channels::hashFor`](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/Channels.cpp)
- **Hash is a hint only.** The header byte 0x0D is the "Channel hash. Used as hint for decryption for the receiver." — [DOC mesh-alg.mdx](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/docs/about/overview/mesh-alg.mdx)
- **Decryption is only attempted on matching channels.** `Channels::decryptForHash` skips any channel whose hash does not match the packet. — [FW Channels.cpp](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/Channels.cpp)
- **Relay of undecryptable packets in 2.7.x.** These packets reach `RoutingModule`, which drops encrypted non-PKI packets only when `rebroadcast_mode` is LOCAL_ONLY or KNOWN_ONLY. Otherwise it calls `router->sniffReceived()` → `perhapsRebroadcast()`. — [FW v2.7.15 src/modules/RoutingModule.cpp](https://github.com/meshtastic/firmware/blob/v2.7.15.567b8ea/src/modules/RoutingModule.cpp)
- **Relay of undecryptable packets in 2.8.x (v2.8.2 and develop).** A new `NextHopRouter::relayOpaquePacket()` relays "only from the immutable outer routing header". It relays in modes ALL, ALL_SKIP_DECODING, CORE_PORTNUMS_ONLY, LOCAL_ONLY and KNOWN_ONLY. For LOCAL_ONLY and KNOWN_ONLY, only a PKI-shaped unicast with a known party is relayed. It refuses when `!isRebroadcaster()`, when hop_limit is 0, or when the node is licensed (ham mode). It adds an "opaque seen-set" dedup: without it, "a dense mesh re-relays every copy of every frame, multiplying at each hop into an unbounded broadcast storm." — [FW develop NextHopRouter.cpp](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/NextHopRouter.cpp); also present in [v2.8.2](https://github.com/meshtastic/firmware/blob/v2.8.2.d5f1619/src/mesh/NextHopRouter.cpp). It was introduced by commit [d6b12ea3f "feat(security): enforce packet authenticity policies" (2026-07-09)](https://github.com/meshtastic/firmware/commit/d6b12ea3f), first contained in tag v2.8.0 (`git log -S relayOpaquePacket` + `git tag --contains`).
- **Which roles relay.** `isRebroadcaster()` = role ≠ CLIENT_MUTE AND rebroadcast_mode ≠ NONE. — [FW FloodingRouter.cpp](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/FloodingRouter.cpp)
- **Duplicate cancellation by role.**
  - ROUTER and ROUTER_LATE never cancel a pending relay after hearing someone else relay it.
  - CLIENT_BASE behaves the same, but only for packets from or to favorited nodes.
  - Every other role (e.g. CLIENT) cancels its own rebroadcast if it hears another node rebroadcast first.
  - ROUTER_LATE is clamped to a "late rebroadcast window."
  - Source: [FW FloodingRouter.cpp `roleAllowsCancelingDupe`/`perhapsCancelDupe`](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/FloodingRouter.cpp)
- **Rebroadcast mode definitions** (develop config.pb.h):
  - ALL: "Rebroadcast any observed message, if it was on our private channel or from another mesh with the same lora params."
  - ALL_SKIP_DECODING: REPEATER role only.
  - LOCAL_ONLY: "Ignores observed messages from foreign meshes that are open or those which it cannot decrypt."
  - KNOWN_ONLY: LOCAL_ONLY plus nodes in the NodeDB only.
  - NONE: SENSOR / TRACKER / TAK_TRACKER only.
  - CORE_PORTNUMS_ONLY: drops non-standard portnums (TAK, RangeTest, PaxCounter…).
  - Source: [FW config.pb.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/generated/meshtastic/config.pb.h)
- **CORE_PORTNUMS_ONLY allow-list** (develop): TEXT, TEXT_COMPRESSED, POSITION, NODEINFO, ROUTING, TELEMETRY, ADMIN, ALERT, KEY_VERIFICATION, WAYPOINT, STORE_FORWARD, TRACEROUTE, STORE_FORWARD_PLUSPLUS. — [FW Router.cpp](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/Router.cpp)
- **Mode and role history.** Present in each tag's generated config.pb.h:
  - ROUTER_LATE and CORE_PORTNUMS_ONLY first appear in v2.6.0 (absent in v2.5.0).
  - CLIENT_BASE first appears in v2.8.0 (absent in v2.7.0).
  - NextHopRouter.cpp first appears in v2.6.0.
  - REPEATER is marked "Deprecated in v2.7.11 because it creates 'holes' in the mesh rebroadcast chain."
  - Sources: [FW v2.6.0 config.pb.h](https://github.com/meshtastic/firmware/blob/v2.6.0.f7afa9a/src/mesh/generated/meshtastic/config.pb.h); [develop config.pb.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/generated/meshtastic/config.pb.h)
- **Managed flooding.** A node "listens a short while to see if another node has already rebroadcasted the packet. If so, it will not rebroadcast." The contention window scales with SNR: low-SNR (far) receivers rebroadcast first. ROUTER and ROUTER_LATE rebroadcast even after hearing another. An originator's packet counts as acknowledged "once a rebroadcast from any Meshtastic node (whether or not it has the same encryption key) is received." — [DOC mesh-alg.mdx](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/docs/about/overview/mesh-alg.mdx)
- **Contention-window timing.**
  - CW size = `map(chUtil, 0,100, CWmin, CWmax)`, with random(0, 2^CW) slot times.
  - Non-router relays are offset by `2·CWmax·slotTime`; ROUTER uses random(0, 2·CWsize)·slotTime, with no offset.
  - slotTime = CAD duration + 7.6 ms propagation/turnaround/MAC.
  - Source: [FW RadioInterface.cpp `getTxDelayMsec*`, `computeSlotTimeMsec`](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/RadioInterface.cpp)
- **Packet counts.** "A reliable broadcast is sent a maximum of three times in total … and a reliable direct message a maximum of five." — [DOC mesh-alg.mdx](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/docs/about/overview/mesh-alg.mdx)

### Inferences
- **Same-slot channels share airtime (high confidence, from FW).** Packets on channel "Oahu"/AQ== and on "LongFast"/AQ== that use the same preset and slot are the same RF events. Every node in range spends receive time on them and counts them in ChUtil. Channel separation is **cryptographic and UI-level only, not RF-level**.
- **Each extra channel adds load, but not a full extra flood (from FW).** In ALL mode a node that does not hold "Oahu" still relays "Oahu" packets. Each channel's traffic is flooded by the same set of relays as default traffic, so adding channels adds traffic **linearly with what those channels carry**. A second channel does **not** double the relay structure. What does add up is all the originated traffic.
- **LOCAL_ONLY/KNOWN_ONLY are double-edged.** They reduce relaying of foreign traffic. If widely deployed among channel members, they can also fragment delivery for channels a relay does not hold. Relays would need to hold every regional channel to carry them.
- **A wrong hash is not free.** A packet whose hash matches no local channel is still fully received (airtime spent). The node attempts no decryption beyond hash matching, then relays it opaquely in ALL mode.

### Gaps
- No measured data (SDR captures or per-node ChUtil logs) found that compares one-channel and many-channel load on the same slot. The airtime-additivity conclusion is from code, not measurement.
- I did not trace whether 2.8's "nodes on firmware too old to track hops aren't relayed" rule (stated in the 2.8 blog) also applies to opaque relays.

---

## Q2. What AQ== means; multiple AQ== channels with different names; whether the primary name or key moves the frequency slot

### Takeaway
`AQ==` is base64 for the single byte `0x01`. A 1-byte PSK is an index: 0 = no encryption, and 1..255 expand to the publicly known 16-byte `defaultpsk`, with its last byte bumped by (index−1). So `AQ==` is exactly `defaultpsk`, and any 1-byte key is public.

Several AQ== channels with different names share one key but almost always have different 8-bit hashes, because the name bytes are XORed in. Name collisions in the 8-bit space are possible.

The **primary channel's name** (not its key) sets the frequency slot through a djb2 hash, unless `channel_num` / frequency slot is set explicitly. Secondary channel names and keys never move the frequency.

"Default channel" protections (interval floors, map flags) only apply when a channel is AQ== **and** its name equals the preset name (empty name ⇒ preset name). Custom-named AQ== channels are "public" for privacy rules but are **not** "default" for traffic floors.

### Cited Findings
- **Key expansion.** A 1-byte PSK index 0 turns encryption off. Otherwise the firmware copies `defaultpsk` and sets the last byte to `last + pskIndex − 1` ("index of 1 means no change vs defaultPSK"). — [FW Channels.cpp `getKey`](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/Channels.cpp)
- **Default key bytes.** `defaultpsk = {d4 f1 bb 3a 20 29 07 59 f0 bc ff ab cf 4e 69 01}` (AES-128). — [FW Channels.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/Channels.h)
- **Public keys.** `simple0`–`simple254` and `default` "use publicly known encryption keys … shipped with Meshtastic source code and thus, anyone can listen to messages encrypted by them." — [DOC channels.mdx](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/docs/configuration/radio/channels.mdx)
- **"Public" in 2.8.** Firmware treats any 1-byte PSK, or the full defaultpsk family, as public (`usesPublicKey`/`cryptoKeyIsPublic`). On public-key channels the docs say "the firmware limits precision to 15 bits." — [FW Channels.cpp](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/Channels.cpp); [DOC channels.mdx](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/docs/configuration/radio/channels.mdx)
- **"Default channel" test.** `isDefaultChannel(ch)` is true only if `psk.size==1 && psk[0]==1` **and** `name == preset display name`. `hasDefaultChannel()` also requires `use_preset`, the default frequency slot, and no `override_frequency`. — [FW Channels.cpp](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/Channels.cpp)
- **Floors, 2.7.15.** The telemetry floor applies only if the **primary** channel `isDefaultChannel`. — [FW v2.7.15 NodeDB.cpp](https://github.com/meshtastic/firmware/blob/v2.7.15.567b8ea/src/mesh/NodeDB.cpp)
- **Floors, develop.** The telemetry floor applies if `hasDefaultChannel()` is true. The position floor applies only if the channel position is sent on `isDefaultChannel`. — [FW develop NodeDB.cpp](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/NodeDB.cpp)
- **Frequency slot.**
  - Slot = `djb2(primaryChannelName) % numFreqSlots` (`hash*33+c`, seed 5381) when `channel_num == 0`.
  - Some regions override this with a preset-name hash or a fixed slot.
  - `uses_default_frequency_slot` is false when the user sets a slot that does not match the default.
  - Source: [FW RadioInterface.cpp](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/RadioInterface.cpp)
- **Docs on slot selection.** "a hash of the PRIMARY channel's name sets the LoRa frequency slot … To ensure devices with different PRIMARY channel name transmit on the same frequency, you must explicitly set the LoRa frequency slot." — [DOC channels.mdx](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/docs/configuration/radio/channels.mdx)
- **Official guidance (2026-10).** "renaming your primary channel is the wrong way … Its name sets your frequency slot … Put private groups on secondary channels 1 through 7 instead. They share your LoRa settings, use their own keys, and leave your frequency slot alone." It also gives US slots: Long Fast 906.875 MHz (slot 20), Long Turbo 908.750 MHz (slot 14), Medium Fast 913.125 MHz (slot 45). — [DOC blog finding-your-local-mesh (2026-10)](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2026/October/finding-your-local-mesh.mdx)
- **CALC: channel hashes and US slots.** I reproduced the hash and slot algorithms. xor(defaultpsk) = 0x02.
  - My slot results match the official US slots for LongFast, LongTurbo and MediumFast, which validates the implementation.
  - AQ== hashes: LongFast 0x08 (slot 20/104), MediumFast 0x1f (slot 45), MediumSlow 0x18 (slot 52), ShortFast 0x70 (slot 68), LongTurbo 0x76 (slot 14/52).
  - Illustrative custom primary names, if used as the **primary** name on 250 kHz US: "Hawaii" → 0x3d / slot 1 (902.125 MHz); "Oahu" → 0x31 / slot 35; "Maui" → 0x32 / slot 26; "Kauai" → 0x55 / slot 89; "HawaiiMesh" → 0x0e / slot 30.
  - Script: scratch `hash.py`, using the FW algorithm.

### Inferences
- **Many AQ== secondaries on the default slot.** All their traffic lands on the slot of the primary name or preset. Each gets a distinct hash (barring an 8-bit collision), so nodes without that channel cannot decode it. They still hear it and, in ALL mode, relay it.
- **Collisions.** With ~N custom names the chance that two share an 8-bit hash grows quickly (birthday bound: ~50% at ~19 names). A collision only costs a failed decryption attempt. It is not a routing problem.
- **A custom-named AQ== primary is a different case.** It moves the node to another slot (e.g. "Hawaii" → 902.125 MHz in US-region math). That **splits the RF channel**, cutting shared airtime but partitioning the mesh. Since `hasDefaultChannel()` becomes false, the default-channel interval floors also stop applying.
- **Custom-named AQ== channels are "public but not default."**
  - 2.8 privacy rules apply: 15-bit position cap, positions off by default after the 2.8 migration.
  - The **default-channel minimum intervals do not apply** to a channel used for position unless it is named after the preset.
  - From a traffic standpoint, a regional plan of many named AQ== channels can carry **more** periodic traffic per node than the bare default channel would.

### Gaps
- I did not verify which Meshtastic regions use `OVERRIDE_SLOT_PRESET_HASH` or fixed override slots (the `RDEF` table has an `overrideSlot` column). US, EU_868, ANZ and ANZ_433 show 0 = channel-name hash. The Hawaii operator should confirm US-region behaviour on their firmware version.
- Region slot math (902–928 MHz / 250 kHz = 104 slots) is my calculation from the RDEF table, cross-checked against three official frequencies.

---

## Q3. Firmware changes 2.5 → 2.7 → 2.8 (through Nov 2026)

### Takeaway
- **2.6 (Mar 2025):** next-hop routing for DMs, the ROUTER_LATE role, and the CORE_PORTNUMS_ONLY mode.
- **2.7.x (Jun 2025 → 2.7.26):** zero-cost hops for favorited routers (blog Nov 2025), REPEATER deprecated in 2.7.11, and a congestion-scaling coefficient that also *shortens* intervals for small meshes.
- **2.8 (2.8.0 Aug 31 2026, revoked; 2.8.1 alpha Oct 2026; tags to 2.8.2; develop 2.8.3):**
  - a Traffic Management module, on by default on most hardware (6-hour position dedup);
  - a congestion-gated HopScaling module that lowers hop limits when smoothed ChUtil ≥ 20%;
  - an SF/BW-derived throttling factor;
  - position and telemetry broadcasts opt-in (off) after upgrade;
  - a 15-bit precision cap on public-key channels;
  - opaque-packet relay with dedup;
  - packet signing;
  - LongTurbo as the default for *new* US nodes configured on-device.

The ChUtil thresholds (40% / 25% polite) and the 50%-of-duty-cycle AirUtilTX gate have not changed from v2.5.0 through develop.

### Cited Findings
- **Throttle thresholds, unchanged v2.5.0 → develop.** `max_channel_util_percent = 40`, `polite_channel_util_percent = 25`, `polite_duty_cycle_percent = 50`. Develop: "The three thresholds are hard-coded members with no config binding." — [FW v2.5.0 airtime.h](https://github.com/meshtastic/firmware/blob/v2.5.0.9ac0e26/src/airtime.h); [develop airtime.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/airtime.h)
- **What the gates cover.** They gate only **originated periodic** traffic:
  - Polite 25% (non-tracker/sensor roles): position, device/environment/air-quality/health telemetry, neighbor-info, range test, store-and-forward.
  - Hard 40%: NodeInfo.
  - **Relays and user text are not gated by ChUtil.**
  - Source: call sites of `isTxAllowedChannelUtil` in [FW develop PositionModule.cpp, NodeInfoModule.cpp, DeviceTelemetry.cpp, NeighborInfoModule.cpp](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/modules/PositionModule.cpp)
- **ChUtil measurement window.** ChUtil is a 60 s window (6×10 s buckets). There is also a ~21-min EMA "smoothed" value. — [FW airtime.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/airtime.h)
- **Next-hop routing for DMs (2.6, preview 2025-02-26).** DMs start with managed flooding. A next hop is learned when the reply's relayer was also a relayer of the original. Learning is per hop, and the node falls back to flooding on the last retry. Broadcast routing is unchanged ("it has been proven hard to beat the current method"). — [DOC blog 2.6 preview](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2025/February/meshtastic-2.6-preview.mdx); [DOC mesh-alg.mdx](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/docs/about/overview/mesh-alg.mdx)
- **Interval scaling, 2.5.0.** `congestionScalingCoefficient` = 1.0 for ≤40 online nodes, else `1 + (n−40)·0.075`, regardless of preset. — [FW v2.5.0 Default.h](https://github.com/meshtastic/firmware/blob/v2.5.0.9ac0e26/src/mesh/Default.h)
- **Interval scaling, 2.7.15.** Factor 0.6 for ≤10 nodes, 0.7 for ≤20, 0.8 for ≤30, 1.0 for ≤40 (small meshes broadcast **more** often). Above 40, per-node factors are 0.075 LongFast, 0.04 MediumSlow, 0.02 MediumFast, 0.01 Short*. Event mode uses 0.04. — [FW v2.7.15 Default.h](https://github.com/meshtastic/firmware/blob/v2.7.15.567b8ea/src/mesh/Default.h)
- **Interval scaling, 2.8.x / develop.** 1.0 for ≤40 nodes (the small-mesh speed-up was removed). Above 40, the factor is `2^SF/(BW_kHz·100)`: LongFast 0.08192, MediumSlow 0.04096, MediumFast 0.02048, ShortFast 0.00512. Docs example: "a mesh of 62 online nodes on Long Fast stretches a 1-hour interval to about 168 minutes." ROUTER, ROUTER_LATE, SENSOR, TRACKER and TAK_TRACKER are not scaled. — [FW develop Default.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/Default.h); [DOC mesh-alg.mdx](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/docs/about/overview/mesh-alg.mdx)
- **Default and minimum intervals, 2.7.15.**
  - Position broadcast default: 15 min for clients, 12 h for ROUTER.
  - Telemetry floor on default channel: 30 min.
  - NodeInfo default: 3 h, minimum 1 h.
  - NeighborInfo minimum: 4 h.
  - Source: [FW v2.7.15 Default.h](https://github.com/meshtastic/firmware/blob/v2.7.15.567b8ea/src/mesh/Default.h)
- **Default and minimum intervals, develop.**
  - Position default: 1 h (12 h for ROUTER/ROUTER_LATE).
  - On the default channel, position minimum is 1 h and smart-broadcast minimum is 5 min.
  - Telemetry floor on default channel: 30 min (12 h for routers).
  - Stationary/fixed position floor: 6 h + 6 min.
  - Source: [FW develop Default.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/Default.h)
- **Traffic Management (2.8; absent from v2.7.15, present in v2.8.0).**
  - "Packet inspection and traffic shaping: position dedup, per-node rate limiting, unknown-packet filtering, NodeInfo direct response."
  - It is "Enabled by default; TINY parts (STM32WL) are excluded."
  - Only position dedup ships on (6 h window, 19-bit ≈ 90 m grid). Rate limiting, hop-exhaustion, drop-unknown and NodeInfo direct response "stay opt-in."
  - Config fields: `position_min_interval_secs`, `nodeinfo_direct_response_max_hops`, `rate_limit_window_secs` / `rate_limit_max_packets`, `unknown_packet_threshold` ("Maximum unknown/undecryptable packets per 5 minutes before the source is dropped").
  - Sources: [FW TrafficManagementModule.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/modules/TrafficManagementModule.h); [mesh-pb-constants.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/mesh-pb-constants.h); [NodeDB.cpp `installTrafficManagementDefaults`](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/NodeDB.cpp); [module_config.pb.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/generated/meshtastic/module_config.pb.h)
  - Note: the proto comment says the position default is "18000 (5 hours)", while Default.h and NodeDB install 6 h. This is an internal inconsistency in source.
- **HopScaling (2.8; `HAS_VARIABLE_HOPS=1` except STM32WL).**
  - Builds a sampled hop-distance histogram and recommends the hop at which cumulative node count reaches 40 (extension ceiling 80).
  - Engages only when smoothed ChUtil ≥ 20% for 3 consecutive 5-minute samples. Releases at ≤ 12%.
  - Above 25%, the one-hop extension is cut to its strictest setting.
  - Infrastructure-role hop floor: 3.
  - Sources: [FW Default.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/Default.h); [HopScalingModule.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/modules/HopScalingModule.h)
  - The 2.8 blog says "Hop limits drop while the channel is congested and recover when it clears." — [DOC lets-talk-about-2-8 (2026-10-08)](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2026/October/lets-talk-about-2-8.mdx)
- **2.8 release status.** "2.8.0 is revoked and 2.8.1 replaces it … **2.8.1 is an alpha and isn't ready for wide deployment.**"
  - "Position and telemetry broadcasts are off … Precise position isn't sent on public channels."
  - "Nodes on firmware too old to track hops aren't relayed."
  - Long Turbo (500 kHz, SF11) is the default for new US nodes set up on the device screen, citing the FCC §15.247 500 kHz minimum. Upgrades keep their preset.
  - Source: [DOC lets-talk-about-2-8](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2026/October/lets-talk-about-2-8.mdx)
- **2.8 one-time migration.** Sets `position_precision=0` only on PUBLIC/default-PSK channels and disables all mesh-broadcast telemetry flags. Map reporting stays on but without location. — [FW NodeDB.cpp `optInDisablePositionSharing`/`optInDisableTelemetryBroadcast`](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/NodeDB.cpp); present in v2.8.0 and v2.8.2, absent in v2.7.15 (grep of tag files).
- **Zero-cost hops (2.7.x, blog 2025-11-17).** A hop is not decremented when:
  - the relayer is ROUTER, ROUTER_LATE or CLIENT_BASE;
  - it is not the first hop; and
  - the previous relay is a favorited ROUTER or ROUTER_LATE.
  - Max hop limit is still 7.
  - Source: [DOC zero-cost-hops](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2025/November/zero-cost-hops-for-favorite-routers.mdx)
- **Public MQTT broker policy (server-side, not firmware).**
  - On 2024-08-21 the project removed subscribe-to-all (`#`) on mqtt.meshtastic.org.
  - It added server-level filtering so "Only position packets with imprecise location information will be passed … **This filtering is only applied to the default key.**"
  - "over 50% of MQTT traffic [was] position data."
  - Source: [DOC blog recent-public-mqtt-broker-changes (2024-08-24)](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2024/August/recent-public-mqtt-broker-changes.mdx)
- **MQTT in firmware (develop).**
  - Map reports clamp precision to 12–15 bits.
  - Range-test and detection-sensor packets are never uplinked to the default server.
  - Other nodes' packets are not uplinked to a public (non-RFC1918) broker unless the sender set `ok_to_mqtt` (default false).
  - `ignore_mqtt` lets a node drop packets that crossed MQTT. NeighborInfo is not sent over LoRa on the default channel or default slot.
  - Sources: [FW MQTT.cpp](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mqtt/MQTT.cpp); [NeighborInfoModule.cpp](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/modules/NeighborInfoModule.cpp); [NodeDB.cpp](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/NodeDB.cpp)

### Inferences
- **No firmware zero-hop for MQTT downlink.** I found no firmware rule that zero-hops MQTT-downlinked default-key traffic in develop's `onReceiveProto`. Downlinked packets keep the envelope's `hop_limit`/`hop_start`. Any "zero-hop" behaviour comes from the public server's policy, or from 2.8 TrafficManagement's opt-in `exhaust_hop` features. Treat the "zero-hop on MQTT downlink" claim as UNVERIFIED in firmware.
- **Mixed fleets.** For a fleet on 2.7.x, what matters is: 40/25% gates, 2.7 scaling with small-mesh *speed-up*, and no TrafficManagement. 2.8 behaviour (dedup, HopScaling, opt-in positions) applies only to nodes that run 2.8.1+, which the project calls alpha as of 2026-10-08.

### Gaps
- I could not fetch the official release notes or GitHub releases API (403). Feature-to-version mapping is from file presence in tags and from blog posts, not from changelogs.
- I could not confirm the exact 2.7.x point release that introduced zero-cost hops, or the source of the DEF CON 2024 "traffic management policies" (an X/Twitter link).

---

## Q4. Airtime numbers per preset, saturation, and the hop-limit multiplication effect

### Takeaway
Computed time-on-air for a ~60-byte frame (16-byte header plus a typical position or telemetry payload):

| Preset | ToA (~60 B) |
|---|---|
| ShortTurbo | ≈ 30 ms |
| ShortFast | ≈ 61 ms |
| ShortSlow | ≈ 111 ms |
| MediumFast | ≈ 201 ms |
| MediumSlow | ≈ 382 ms |
| LongFast | ≈ 682 ms |
| LongModerate | ≈ 2.3 s |
| LongSlow | ≈ 4.1 s |

Saturation scale:
- LongFast reaches the 25% polite gate at about **1,320 transmissions/hour** heard by a node (all channels, originals plus relays). The 40% gate is ~2,100/h.
- MediumFast tolerates ~3.4× more transmissions; ShortFast ~11×.

Hop limit does not multiply traffic exponentially. Dedup means each relay transmits a given packet at most once (plus retries). Each originated broadcast costs roughly (1 + number of relays that actually rebroadcast within the hop horizon) transmissions. ROUTER / ROUTER_LATE never suppress, and hop_limit sets how far the horizon reaches.

### Cited Findings
- **Preset table** (`modemPresetToParams`), sub-GHz:
  - ShortTurbo SF7/500 kHz/CR4/5; ShortFast SF7/250/4/5; ShortSlow SF8/250/4/5.
  - MediumFast SF9/250/4/5; MediumSlow SF10/250/4/5; MediumTurbo SF9/500/4/5.
  - LongFast SF11/250/4/5; LongTurbo SF11/500/4/8; LongModerate SF11/125/4/8; LongSlow SF12/125/4/8.
  - New in 2.8: LiteFast/LiteSlow (125 kHz), Narrow* (62.5 kHz), Tiny* (15.6 kHz).
  - Preamble 16 symbols.
  - Sources: [FW MeshRadio.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/MeshRadio.h); [RadioInterface.h](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/RadioInterface.h)
- **Official preset table.** LongFast 1.07 kbps / 153 dB link budget; MediumSlow 1.95 / 150.5 dB; MediumFast 3.52 / 148 dB; ShortSlow 6.25 / 145.5 dB; ShortFast 10.94 / 143 dB; ShortTurbo 21.88 / 140 dB. — [DOC blog why-your-mesh-should-switch-from-longfast (2025-04-22)](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2025/April/why-your-mesh-should-switch-from-longfast.mdx)
- **CALC: time-on-air (ms)** by total PHY bytes. Semtech formula, explicit header, CRC on, LDRO when Ts > 16 ms, 16-symbol preamble. Bitrates match the DOC table exactly.

  | Preset | Ts (ms) | 30 B | 60 B | 100 B | 150 B | 255 B |
  |---|---|---|---|---|---|---|
  | ShortTurbo | 0.26 | 20 | 30 | 46 | 64 | 102 |
  | ShortFast | 0.51 | 40 | 61 | 91 | 127 | 204 |
  | ShortSlow | 1.02 | 70 | 111 | 162 | 223 | 362 |
  | MediumFast | 2.05 | 130 | 201 | 293 | 406 | 642 |
  | MediumSlow | 4.10 | 259 | 382 | 546 | 751 | 1181 |
  | LongFast | 8.19 | 477 | 682 | 1010 | 1378 | 2157 |
  | LongModerate | 16.38 | 1380 | 2298 | 3478 | 4919 | 7934 |
  | LongSlow | 32.77 | 2499 | 4071 | 6169 | 8790 | 14295 |

  (scratch `toa.py`)
- **CALC: transmissions per hour to fill a node's ChUtil (60 B frames).**

  | Preset | 25% | 40% | 100% |
  |---|---|---|---|
  | LongFast | 1,320 | 2,111 | 5,279 |
  | MediumSlow | 2,356 | 3,770 | 9,425 |
  | MediumFast | 4,473 | 7,156 | 17,891 |
  | ShortFast | 14,865 | 23,784 | 59,461 |

- **CALC: toy LongFast model (60 B).** N nodes × 4 originated packets/h × (1 + R relays heard per packet):
  - N=50, R=3 → 15%
  - N=100, R=3 → 30%
  - N=100, R=6 → 53%
  - N=200, R=6 → 106% (saturated)
  - Illustrative only. The model ignores retries, ACKs, nodeinfo exchanges, and hidden-terminal collisions, which are all additional load.
- **Dedup.** Packets already seen are dropped as "dupe". A relay waiting in its TX queue is cancelled if another relay is heard (non-router roles). A copy with more hops remaining replaces the queued one. — [FW FloodingRouter.cpp](https://github.com/meshtastic/firmware/blob/68f47efddd10c4ed383adf90eace094e47779cac/src/mesh/FloodingRouter.cpp)
- **What hop limit does.** "Any packet that is received with a remaining hop limit greater than zero is eligible for rebroadcast. Packets with a hop limit of zero are discarded after processing." — [DOC demystifying-router-late](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2025/September/demystifying-router-late.mdx)
- **ROUTER_LATE guidance.** "ROUTER_LATE will rebroadcast every single packet that it can, it can add significantly to the overall airtime." Operators should stop using it as a roof node "If ChUtil gets higher than 25%, or AirUtilTX higher than around 7-8%." — [DOC demystifying-router-late](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2025/September/demystifying-router-late.mdx)
- **Managed flooding at scale.** It "has been proven to be effective even in large meshes of more than 100 nodes with proper traffic control." — [DOC blog why-meshtastic-uses-managed-flood-routing (2024-08-18)](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2024/August/why-meshtastic-uses-managed-flood-router.mdx)
- **ChUtil bands (community).** Below 25% "optimal", above 50% problematic for sustained periods. — [COMM adrelien.com, via search snippet only, not fetched](https://adrelien.com/understanding-and-optimizing-channel-utilization-in-meshtastic-devices/)

### Inferences
- **Every extra AQ== channel adds load on the shared slot.** In a mesh where 2.7 relays run ALL, each channel's traffic is relayed by the whole relay fabric. A chatty secondary therefore costs the same airtime per packet as default-channel traffic: ~0.7 s per relay hop-copy on LongFast.
- **Hop-limit effect:** load ≈ originated packets × (1 + count of distinct rebroadcasting nodes reachable within hop_limit). The count of rebroadcasting nodes is driven by ROUTER/ROUTER_LATE count (never suppressed) and topology, not by 2^hops.
  - Raising hop_limit from 3 to 7 matters most in long, linear archipelago topologies, where each extra hop adds a new set of relays.
  - In dense clusters it matters little, because duplicates are suppressed.
- **Gates protect telemetry, not chat or relays.** At ChUtil ≥ 25% a node's own position and telemetry stop. Relays and text continue, so a saturated mesh keeps degrading through collisions rather than self-limiting.

### Gaps
- No measured on-air capture of actual Meshtastic packet-size distributions. 30–100 B are typical-size assumptions, not measured.
- No measured ToA from firmware logs (`getPacketTime`). The calculation matches the firmware's 165 ms LongFast preamble comment, but full-packet ToA was not observed.

---

## Q5. Regional evidence: meshes that left LongFast, and outcomes

### Takeaway
Official Meshtastic material records two detailed migrations:
- **Bay Area Group → MediumSlow:** 150+ nodes, a qualitative testimonial.
- **Wellington NZ → ShortFast, Aug 31 2024:** 150+ active nodes; ChUtil peaks above 65% observed on LongFast; afterwards "very reliable", with a 254 km link still working.

The 2.8 post cites YYCMesh Calgary at 500 kHz/SF11 with 300+ nodes. A 2026-10 scan screenshot showed 60 nodes on MediumFast vs 6 on LongFast in one locale. Community posts (not verified) report Minnesota (MediumFast slot 45), Switzerland, and Jacksonville FL (MEDIUM_FAST as of 2026-03-28) moving.

No source gave controlled before/after ChUtil or delivery-rate numbers except Wellington's ">65%" pre-move peak.

### Cited Findings
- **Bay Area.** "Our mesh of over 150 nodes is currently thriving after switching to the MediumSlow preset … an experiment to escape the saturated default LongFast channel that had a lot of nodes using old firmware or misconfigured nodes spamming the mesh." Success factors given: coordination, less legacy clutter, modern firmware, routers reserved for high elevation. Testimonial, no numbers. — [DOC blog why-your-mesh-should-switch-from-longfast (2025-04-22)](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2025/April/why-your-mesh-should-switch-from-longfast.mdx)
- **Wellington NZ.**
  - Before: "over 150 active nodes … observed channel utilisation peaks at busy sites hitting over 65% … largely composed of nodeinfo, location, and telemetry updates from clients."
  - Migration: to SHORT_FAST on the default slot, Aug 31 2024, after a 2-hour test and a 24-hour test.
  - After: "very reliable performance, including for our longest permanent link (254km)." The headroom was later used for ROUTER_LATE blackspot coverage.
  - Source: [same DOC blog](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2025/April/why-your-mesh-should-switch-from-longfast.mdx)
- **Official guidance.** Consider leaving LongFast at ">60 nodes, especially if they are in relatively close proximity." MediumFast/MediumSlow for dense urban/suburban; ShortFast/ShortSlow for extremely dense. A new default preset is being considered for Meshtastic 3.0. — [same DOC blog](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2025/April/why-your-mesh-should-switch-from-longfast.mdx)
- **YYCMesh Calgary.** "runs mostly the same settings as Long Turbo, 500 kHz at SF11 with a different coding rate, and sees more than 300 nodes on its public channel." — [DOC lets-talk-about-2-8](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2026/October/lets-talk-about-2-8.mdx). The field report itself (yycmesh.com/blog/500khz-field-report) was not fetchable.
- **Discovery scan.** "This scan heard 60 nodes on Medium Fast and six on Long Fast." Location unstated. — [DOC finding-your-local-mesh](https://github.com/meshtastic/meshtastic/blob/0c02d499bdad36db9bd685532e3c8c9fc3d4c1a7/blog/2026/October/finding-your-local-mesh.mdx)
- **Unverified community reports** (seen in web-search summaries only; pages not fetched):
  - [MSP (Minnesota) mesh transitioning to MediumFast](https://r.darklab.sh/r/meshtastic/comments/1l794n1/msp_mesh_transitioning_to_medium_fast)
  - [Jacksonville/Duval "MEDIUM_FAST as of 3/28/26"](https://areyoumeshingwith.us/docs/regional-lora-settings)
  - [Swiss switch to MediumFast "due to congestion in cities"](https://lemmy.manganiello.tech/post/220188/112653)
  - [Sagebrush Tri-Cities MediumFast](https://sagebrush-www.pages.dev/meshtastic/mediumfast)
  - [Bay Area Meshview ChUtil samples ~6–18% (preset unspecified)](https://meshview.bayme.sh/packet_list/1431909870)

### Inferences
- **The evidence is mostly testimony.** One quantitative pre-move figure exists (Wellington >65% ChUtil peaks on LongFast). There are no controlled post-move ChUtil or delivery numbers.
- **The reported benefit has three parts:** (a) shorter airtime (2–11× by the CALC table), (b) escaping legacy and misconfigured traffic on the default slot, and (c) coordinated configuration. The Bay Area testimonial attributes much of its gain to (b) and (c), not just modulation.
- **For a Hawaii regional plan:** a preset change attacks per-packet airtime. A many-AQ==-channel plan on one slot adds packets. These are independent levers.

### Gaps
- No primary before/after ChUtil or packet-delivery-ratio dataset found for any region (meshtastic.org, yycmesh.com and meshenvy.org were unreachable from this session).
- No Hawaii-specific evidence was found in this pass; that is out of this researcher's scope or needs live node data.
