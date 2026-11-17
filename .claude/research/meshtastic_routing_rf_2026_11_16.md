# Meshtastic Routing vs RF Physics vs Atmosphere: why one device can miss what the mesh carried

**Audience:** a General-class ham and infrastructure engineer running a mixed Meshtastic / RNS / MeshCore fleet on the Big Island (Volcano / Kīlauea area).
**Firmware read:** meshtastic/firmware tag **`v2.7.26.54e0d8d`** (commit `54e0d8d0ab2ff56b3a9ce967e53f79e49af560fb`, 2026-06-23). That is the fleet's exact build, not a nearby one. Section 11 compares it with tag **`v2.8.2.d5f1619`** (commit `d5f16191c`, 2026-11-03), the newest 2.8 tag, and with `develop` at `68f47efdd` (2026-11-16).
**Method tags:** `[SOURCE: file::function @tag]` means read in the pinned source this session. `[CITED: ref]` means an external reference; refs marked *(not fetched)* come from the author's prior knowledge of the literature and were not re-opened this session. `[PHYSICS: …]` means derived here, with the working shown. `[ASSERTION — uncited]` means a reasoned claim with no source.
**Progress (one home for this topic, operator 2026-11-16: combine, don't add):**
| Date | What | Next |
|---|---|---|
| 2026-11-16 | Written by a research agent from the pinned firmware; 5 load-bearing code claims + both ToA figures re-checked by the session (RadioInterface.cpp:628/632, FloodingRouter.cpp:121-122/157, PacketHistory.cpp:10-12/221-260). §11 is now the REPO home of the 2.8 facts first raised on the 11-14 artifact S3q4BVaULGGQbRFYvsaWAV. | Per-release upstream diff appends to §11 (FW28-0). |

**Constraints honoured:** no fleet device, radio, or local service was contacted. Only web and GitHub reads, plus local arithmetic.

---

## 1. TL;DR

1. **Managed flooding is "rebroadcast unless you overhear someone else do it first."** A CLIENT-class node puts a received broadcast in its TX queue with an SNR-dependent delay. If it hears any other copy of the same `(from, id)` before its own timer fires, it deletes its copy. ROUTER and ROUTER_LATE never delete theirs. CLIENT_MUTE never queues one. `[SOURCE: FloodingRouter.cpp::perhapsCancelDupe, roleAllowsCancelingDupe, isRebroadcaster @v2.7.26]`
2. **Weak-SNR receivers rebroadcast first, by design.** The contention window is `CWsize = map(rx_snr, −20, +10, 3, 8)`. A CLIENT waits `16 + random(0, 2^CW)` slots. On LongFast (slot ≈ 28 ms) that is 448–644 ms after a −20 dB reception and 448–7,588 ms after a +10 dB reception. A ROUTER waits only `random(0, 2·CW)` slots, about 0–420 ms. `[SOURCE: RadioInterface.cpp::getTxDelayMsecWeighted, getCWsize, computeSlotTimeMsec @v2.7.26]` `[PHYSICS: §2.3]`
3. **Suppression is decided by what the RELAYER hears, not by what the end device hears.** This is the textbook hidden-terminal failure of managed flooding. Relay A cancels because it heard relay B, but the end device E can hear only A. E then gets nothing, while every gateway in range of B gets the message. `[SOURCE: FloodingRouter.cpp::shouldFilterReceived → perhapsCancelDupe @v2.7.26]` `[PHYSICS: geometry, §7]`
4. **On LongFast, two low-SNR relays that cannot hear each other will almost certainly collide at a receiver that hears both.** Their start times spread over at most 196 ms, but a ~62-byte frame lasts ~723 ms. The overlap probability is 1.00 at CW 3–4. On SHORT_TURBO (frame ~32 ms) it is 0.69 at CW 3 and 0.03 at CW 8. `[PHYSICS: §4.4, enumerated]` Whether the receiver still decodes one copy depends on LoRa capture. `[CITED: Bor et al. 2016 (not fetched)]`
5. **"Implicit ACK" for a broadcast means "the sender heard at least one rebroadcast."** It says nothing about any particular end device. `want_ack` is stripped from broadcasts on air. The originator retries up to 3 transmissions in total until it overhears one relay. `[SOURCE: Router.cpp::send; ReliableRouter.cpp::send, shouldFilterReceived; NextHopRouter.h NUM_RELIABLE_RETX=3 @v2.7.26]`
6. **Channel-utilization gates do not throttle relays or user text in 2.7.26.** The 40% / 25% "polite" gates (`isTxAllowedChannelUtil`) are called only by periodic modules: position, telemetry, nodeinfo, neighborinfo, range test and S&F. Relays and text messages pass through ungated. In the US region the duty-cycle gate is a no-op (dutyCycle = 100). `[SOURCE: airtime.cpp::isTxAllowedChannelUtil; grep of callers @v2.7.26]`
7. **Firmware channel utilization counts only LoRa frames the radio detected at its own SF/BW.** Your SDR's 0–2% energy-detect figure and the firmware's `channel_utilization` therefore measure different quantities. Both are lower bounds on interference, for different reasons. `[SOURCE: RadioLibInterface.cpp::handleReceiveInterrupt, airtime.cpp::logAirtime @v2.7.26]`
8. **At 915 MHz the atmosphere is almost never the reason one device missed one packet.** Rain is about 0.002 dB/km even at 100 mm/h. Gaseous absorption is about 0.005–0.01 dB/km. Vog is Rayleigh-negligible. Terrain diffraction (6–27 dB per edge) and wet forest (~13–30 dB over 50–200 m of canopy) dominate. Ducting needs both ends coupled into a layer that sits near 2 km on the Big Island. `[CITED: ITU-R P.838-3, P.676, P.526, P.453 (not fetched)]` `[PHYSICS: §5–6]`
9. **The cheapest mechanism to rule out first is not RF at all.** It is the device-to-display path. Classic ESP32 boards hold only **8** packets for the phone and discard the oldest text when full. Other causes are a BLE/app disconnect, a channel-index or PSK mismatch (packet relayed but not decodable), the sender being ignored, or a power-save sleep. `[SOURCE: mesh-pb-constants.h MAX_RX_TOPHONE; MeshService.cpp::sendToPhone @v2.7.26]`
10. **A bridged copy with a new packet id is an independent second chance, but only if the end device is on the preset the bridge transmits into.** If the device missed both the original and the bridged copy, suspect device-side deafness or the display path more than a single RF collision. `[ASSERTION — uncited; follows from SOURCE §2.5 dedupe-by-(from,id)]`
11. **The honest UI vocabulary is three-valued.** "Heard by X" (X logged it), "relayed by X" (X's TX completed), and "unobserved." "Delivered to the mesh" cannot be claimed for a broadcast. "Cannot hear" can only be claimed from a positive measurement such as a link test or SNR history, never from absence. `[ASSERTION — uncited; design consequence of 1–5]`
12. **2.8 changes the substrate under you.** These were checked against v2.8.2 source:
    - MQTT JSON output is **removed**: confirmed.
    - NodeNum becomes **crc32(public key)**, so node ids change on first boot: confirmed.
    - **DMShell** is compiled into every Linux meshtasticd and enabled by default at v2.8.2. PR #12337 makes it opt-in, but that is merged to `develop` only and is in no tag: confirmed.

    See §11. `[SOURCE: v2.8.2.d5f1619, PR #12337 / e8347ca1b]`

---

## 2. Managed flooding, from the code (v2.7.26)

### 2.1 Class structure

`Router` ← `FloodingRouter` ← `NextHopRouter` ← `ReliableRouter`. Broadcasts go through `FloodingRouter::send`. Unicasts go through `NextHopRouter::send`. Both paths first stamp `relay_node = last byte of our NodeNum` and call `wasSeenRecently(p)` so we ignore echoes of our own transmission. `[SOURCE: FloodingRouter.cpp::send; NextHopRouter.cpp::send; ReliableRouter.cpp::send @v2.7.26]`

The over-the-air header is 16 bytes: `to`, `from`, `id` (4 B each), then `flags` (bits 0–2 hop_limit, plus hop_start and want_ack bits), `channel` (a 1-byte channel hash), `next_hop` (1 B) and `relay_node` (1 B). `[SOURCE: RadioInterface.h PacketHeader @v2.7.26]` Two consequences follow:
- `relay_node` and `next_hop` are **only the last byte** of a NodeNum, so they are ambiguous across nodes that share it.
- `hops_away = hop_start − hop_limit` is computable for any packet from post-hop firmware. `HOP_MAX = 7` and `HOP_RELIABLE = 3`. `[SOURCE: MeshTypes.h @v2.7.26]`

### 2.2 The receive pipeline and the rebroadcast decision

1. **Radio IRQ → `RadioLibInterface::handleReceiveInterrupt`.**
   - A CRC or read error logs `Ignore received packet due to error=… (maybe id=0x… fr=0x…)`, increments `rxBad` and books the airtime as `RX_ALL_LOG`.
   - A good frame increments `rxGood`. Its `hop_limit` and `hop_start` are unpacked from `flags`. `rx_snr` and `rx_rssi` are attached.

   `[SOURCE: RadioLibInterface.cpp::handleReceiveInterrupt @v2.7.26]`
2. **`Router::perhapsHandleReceived`.** This step drops frames from ignored nodes, from the broadcast address, and (if `ignore_mqtt` is set) frames with `via_mqtt`. It then calls `shouldFilterReceived`. `[SOURCE: Router.cpp::perhapsHandleReceived @v2.7.26]`
3. **`ReliableRouter::shouldFilterReceived`.**
   - If `p->from == our NodeNum`, someone is rebroadcasting our own packet. If we have a pending retransmission for it, we generate an **implicit ACK** to our client and stop retransmitting (LoRa-heard copies only).
   - It then pushes every other pending retransmission later by this frame's airtime, because we could not have heard an ACK while receiving.
   - Finally it dispatches to Flooding or NextHop filtering.

   `[SOURCE: ReliableRouter.cpp::shouldFilterReceived @v2.7.26]`
4. **`FloodingRouter::shouldFilterReceived`** (for broadcasts):
   - `wasSeenRecently(p, withUpdate=true, …, &wasUpgraded)` looks up `(sender, id)` in the packet history.
   - **Upgrade path.** If this copy carries a higher `hop_limit` than the best seen so far and we are a rebroadcaster, the queued lower-hop copy is removed (`removePendingTXPacket`) and this one is queued instead. `[SOURCE: FloodingRouter.cpp::perhapsHandleUpgradedPacket @v2.7.26]`
   - **Duplicate path.** It logs `Ignore dupe incoming msg` and increments `rxDupe`. If the copy is the *originator's own retry* (`hop_start == hop_limit`, i.e. zero hops used) and our copy is no longer in the TX queue, we re-queue a relay; this keeps a lost implicit ACK from stalling the originator. Otherwise it calls **`perhapsCancelDupe`**.
   - `perhapsCancelDupe`: if the copy arrived **over LoRa** and the role allows cancelling, `Router::cancelSending(from, id)` deletes our queued rebroadcast and `txRelayCanceled++`. ROUTER_LATE (and CLIENT_BASE for favourites) instead **re-times** the queued copy into the "late window" (`clampToLateRebroadcastWindow`).

   `[SOURCE: FloodingRouter.cpp::shouldFilterReceived, perhapsCancelDupe @v2.7.26]`
5. **First copy → `handleReceived` → decode → modules → `RoutingModule::handleReceivedProtobuf` → `router->sniffReceived` → `perhapsRebroadcast`.**
   - In `NextHopRouter::perhapsRebroadcast` the conditions for a relay are: not to us, not from us, `hop_limit > 0`, `id != 0`, `isRebroadcaster()`, and `next_hop` is either "no preference" or our last byte. If they hold, a copy is made and `hop_limit` is decremented (unless favourite-router-to-router preservation applies; see `Router::shouldDecrementHopLimit`).
   - The copy is sent via `FloodingRouter::send` → `Router::send` → `iface->send` → TX queue.

   `[SOURCE: NextHopRouter.cpp::perhapsRebroadcast; Router.cpp::shouldDecrementHopLimit @v2.7.26]`
6. **Delivery to the local client** happens in the same `RoutingModule` handler. `service->handleFromRadio()` is called for broadcasts and for packets to us. `[SOURCE: RoutingModule.cpp::handleReceivedProtobuf @v2.7.26]`

**Undecodable packets** are those on a channel whose PSK we lack, but with the same preset and frequency.
- Under `rebroadcast_mode = ALL` (the default) they are still rebroadcast, because `perhapsRebroadcast` checks no decode state.
- Under `LOCAL_ONLY` or `KNOWN_ONLY` (and CLIENT_HIDDEN, which forces LOCAL_ONLY) the RoutingModule returns early for encrypted non-PKI packets, so **they are not relayed**.
- `KNOWN_ONLY` additionally fails decode for senders not in the NodeDB.

`[SOURCE: RoutingModule.cpp::handleReceivedProtobuf; Router.cpp::perhapsDecode; NodeDB.cpp role defaults @v2.7.26]`

### 2.3 The SNR-weighted contention window: actual numbers

From `RadioInterface.h/.cpp` @v2.7.26:

| Constant | Value | Source |
|---|---|---|
| `CWmin`, `CWmax` | 3, 8 | RadioInterface.h |
| `SNR_MIN`, `SNR_MAX` | −20, +10 dB | RadioInterface.cpp::getCWsize |
| slot time | `max(2.25, 2+0.5)·Tsym + 0.2 + 0.4 + 7` ms (CAD + 30 km RTT + turnaround + MAC) | computeSlotTimeMsec |
| preamble | 16 symbols | RadioInterface.h `preambleLength` |
| `PROCESSING_TIME_MSEC` | 4500 | RadioInterface.h |
| TX queue length | 16 (`MAX_TX_QUEUE`) | RadioInterface.h |

The delay functions are:
- **Origination** (`getTxDelayMsec`): `random(0, 2^CW) · slot`, where `CW = map(channelUtil%, 0, 100, 3, 8)`. Below 20% utilization CW = 3, giving 0–7 slots. `[SOURCE]`
- **Rebroadcast, non-ROUTER** (`getTxDelayMsecWeighted`): `(2·CWmax)·slot + random(0, 2^CW(snr))·slot`. This is a fixed 16-slot offset, which lets ROUTERs go first, plus the SNR-scaled window. `[SOURCE]`
- **Rebroadcast, ROUTER only** (`shouldRebroadcastEarlyLikeRouter` is true only for `ROUTER`): `random(0, 2·CW(snr))·slot`. Note that this is **2·CW, not 2^CW**: at most 15 slots. `[SOURCE]`
- **ROUTER_LATE late window** (`getTxDelayMsecWeightedWorst`): `(16 + 2^CW)·slot` from the moment a dupe is heard. `[SOURCE: RadioLibInterface.cpp::clampToLateRebroadcastWindow]`

`[PHYSICS]` Slot times: LongFast Tsym = 2^11/250 kHz = 8.192 ms, so slot = 2.5·8.192 + 7.6 = 28.1 ms (integer 28). SHORT_TURBO Tsym = 2^7/500 kHz = 0.256 ms, so slot = 8.24 ms (integer 8). `CW(snr)` uses Arduino integer `map`: `(snr+20)·5/30 + 3`.

| rx SNR | CW | LongFast CLIENT delay | LongFast ROUTER | SHORT_TURBO CLIENT | SHORT_TURBO ROUTER |
|---|---|---|---|---|---|
| −20 dB | 3 | 448–644 ms | 0–140 ms | 128–184 ms | 0–40 ms |
| −10 dB | 4 | 448–868 ms | 0–196 ms | 128–248 ms | 0–56 ms |
| 0 dB | 6 | 448–2,212 ms | 0–308 ms | 128–632 ms | 0–88 ms |
| +10 dB | 8 | 448–7,588 ms | 0–420 ms | 128–2,168 ms | 0–120 ms |

So a node that heard the packet **weakly** (probably far from the previous transmitter) wins the race. A node that heard it **strongly** (probably close to the previous transmitter, so it would add little coverage) waits up to ~7.6 s on LongFast and almost always hears someone else first, then cancels. That is the intent of the scheme.

Two caveats:
- `getCWsize` does not clamp. An SNR above +10 dB maps to CW ≥ 9 (≥ 512 slots, which is over 14 s on LongFast) unless some outer clamp applies. One exists only on the `tx_after` re-timing path in `setTransmitDelay`: `min(…, now + 2·WeightedWorst)`. `[SOURCE: RadioInterface.cpp::getCWsize; RadioLibInterface.cpp::setTransmitDelay @v2.7.26]` I believe this from reading the code, but have not tested it; a log line `rx_snr found in packet. Setting tx delay:%d` for a > +10 dB reception would confirm it.
- A packet the node generated itself (rx_snr = rx_rssi = 0) uses the origination delay. `[SOURCE: RadioLibInterface.cpp::setTransmitDelay]`

**Listen-before-talk is present.** When the delay expires, `isChannelActive()` (CAD) is run. If a LoRa preamble is detected, the node switches to receive and re-randomizes the delay. If the node is mid-reception (`isActivelyReceiving`), it also defers. `[SOURCE: RadioLibInterface.cpp::onNotify TRANSMIT_DELAY_COMPLETED, canSendImmediately @v2.7.26]` CAD detects only chirps at the node's own SF/BW. `[CITED: Semtech AN1200.48 "CAD performance" (not fetched); RadioInterface.h comment cites AN1200.48]`

### 2.4 hop_limit / hop_start

- The originator sets `hop_start = hop_limit` (config default 3; `HOP_MAX` 7). Each relay decrements `hop_limit`, and nothing is relayed once it reaches 0. `[SOURCE: Router.cpp::send; NextHopRouter.cpp::perhapsRebroadcast @v2.7.26]`
- **Exception:** if this node is ROUTER, ROUTER_LATE or CLIENT_BASE, and the previous relay's last byte matches a **favourite** node whose role is ROUTER, ROUTER_LATE or CLIENT_BASE, `hop_limit` is *not* decremented (except on the first hop). `[SOURCE: Router.cpp::shouldDecrementHopLimit @v2.7.26]`
- `hops_away` as reported to clients is `hop_start − hop_limit` *of the copy this node accepted first*. Because the first copy wins and later copies are discarded as dupes (unless they carry more hops left), `hops_away` describes **the path that won the race**, not the shortest or best path. `[SOURCE: FloodingRouter.cpp::shouldFilterReceived; PacketHistory.cpp::wasSeenRecently @v2.7.26]`

### 2.5 Duplicate detection: packet history

- The key is `(sender NodeNum, 32-bit packet id)`. Each record also stores `next_hop`, the highest `hop_limit` seen, our own TX `hop_limit`, and up to **6 relayer bytes** (`NUM_RELAYERS = 6`). `[SOURCE: PacketHistory.h @v2.7.26]`
- **Size:** `PACKETHISTORY_MAX = max(MAX_NUM_NODES × 2, 100)`. `MAX_NUM_NODES` is 80 on nRF52, 10 on STM32WL, 100 by default, dynamic on ESP32-S3, and `portduino_config.MaxNodes` on meshtasticd. That gives 160 records on nRF52, for example. `[SOURCE: PacketHistory.cpp #define; mesh-pb-constants.h; variants/native/portduino/variant.h @v2.7.26]`
- **No time-based expiry.** `insert()` uses an empty slot, else the matching slot, else **the oldest slot (LRU by rx time)**. A warning fires if the evicted record is younger than 10 min (`RECENT_WARN_AGE`). So the effective dedupe horizon is *capacity ÷ packet rate*. It is not a fixed time. `[SOURCE: PacketHistory.cpp::insert @v2.7.26]` The header comment in `FloodingRouter.h` still describes "entries older than X seconds… discarded". **Comment and code disagree; the code is authoritative.**
- A second, text-only dedupe: `TextMessageModule::recentlySeen(p->id)` is consulted when the history misses. `[SOURCE: FloodingRouter.cpp::shouldFilterReceived @v2.7.26]`
- **Bridged copies get a new id (fleet fact),** so they are *never* deduped against the original. A device can show the same text twice, once from the original sender and once as the bridge's re-origination. `[SOURCE: dedupe key above + fleet fact]`

### 2.6 What each role actually does (v2.7.26 code, docs in parentheses where they differ)

| Role | Relays? | Cancels on hearing dupe? | Rebroadcast delay | Other routing effects | Source |
|---|---|---|---|---|---|
| CLIENT | yes | **yes** | 16 + rand(2^CW(snr)) slots | sends own NodeInfo to newly heard nodes if ch-util < 25% | FloodingRouter::roleAllowsCancelingDupe; RadioInterface::getTxDelayMsecWeighted; MeshService.cpp |
| CLIENT_MUTE | **no** | n/a | n/a | still receives, decodes, displays, ACKs DMs to itself, originates its own traffic | FloodingRouter::isRebroadcaster |
| CLIENT_HIDDEN | yes, but forced `LOCAL_ONLY` (undecodable foreign packets not relayed) | yes | as CLIENT | periodic broadcasts set to MAX_INTERVAL; no NodeInfo reply | NodeDB.cpp role defaults; RoutingModule.cpp |
| CLIENT_BASE | yes | **no** for packets from/to favourites (late window); yes otherwise | as CLIENT, late-clamped for favourites | hop preserved between favourite routers | FloodingRouter::roleAllowsCancelingDupe; Router::shouldDecrementHopLimit |
| ROUTER | yes | **no** | **0 … 2·CW−1 slots** (first) | hop preservation between favourite routers | RadioInterface::shouldRebroadcastEarlyLikeRouter |
| ROUTER_LATE | yes | **no**; re-times to late window on dupe | as CLIENT, then `16 + 2^CW` slots after a dupe | hop preservation | FloodingRouter::perhapsCancelDupe; RadioLibInterface::clampToLateRebroadcastWindow |
| REPEATER | **deprecated v2.7.11**; setting it via admin converts to CLIENT (docs still list "always rebroadcast once") | — | — | — | config.pb.h comment; AdminModule.cpp |
| TRACKER / SENSOR | yes (no routing special-case) | yes | as CLIENT | may use `rebroadcast_mode = NONE`; in power-save they sleep and **hear nothing** (docs: "Awake Only") | FloodingRouter (no role branch); AdminModule.cpp |

Docs vs code:
- Docs list `ALL_SKIP_DECODING` as a mode. In v2.7.26 no `.cpp` references that enum outside generated headers; it has no behaviour of its own. `[SOURCE: grep @v2.7.26]`
- Docs say a reliable DM is sent "at most five times". In v2.7.26 the originator uses `NUM_RELIABLE_RETX = 3` for both DMs and broadcasts. Five attempts for unicast arrives in 2.8 (§11). `[SOURCE: ReliableRouter.cpp::send; NextHopRouter.h @v2.7.26]` `[CITED: meshtastic.org/docs/overview/mesh-algo (fetched)]`

---

## 3. Next-hop routing (DMs) vs flooding (broadcasts); ACKs and retransmission

### 3.1 Broadcasts

Broadcasts are always flooded (`getNextHop` returns "no preference" for broadcast). **`want_ack` is cleared on air** for any broadcast (`Router::send`). However, `ReliableRouter::send` has already registered a pending retransmission if the client asked for want_ack. The originator therefore repeats until it overhears **any single relay** of its own `(from, id)`. That overheard relay is the implicit ACK. At most 3 transmissions are made in total; then the client gets `MAX_RETRANSMIT` as a NAK. `[SOURCE: Router.cpp::send; ReliableRouter.cpp::send, shouldFilterReceived; NextHopRouter.cpp::doRetransmissions @v2.7.26]` Intermediate relays **do not** retransmit broadcasts. `NextHopRouter::send` starts a retransmission record only when `next_hop` is set. `[SOURCE: NextHopRouter.cpp::send]`

**Meaning for "delivered":** a green check on a broadcast proves only that one neighbour of the sender relayed it.

### 3.2 DMs: next-hop learning (2.6+)

- **First contact floods.** On an ACK or reply travelling back, a node learns `next_hop[orig_sender] = relay_node` when either (a) the ACK's relayer was also a relayer of the original, or (b) we were the sole relayer and the ACK came directly from the destination. `[SOURCE: NextHopRouter.cpp::sniffReceived @v2.7.26]`
- **Subsequent DMs** carry `next_hop = <last byte>`. Only the node whose last byte matches relays it. `[SOURCE: NextHopRouter.cpp::perhapsRebroadcast, getNextHop]`
- **Relay retransmission.** A relay with a set next hop keeps a retransmission record. `NUM_INTERMEDIATE_RETX = 2`, which means 1 retransmission. The record stops when the relay overhears the next hop relaying it, or when an ACK passes. `[SOURCE: NextHopRouter.h; NextHopRouter.cpp::send, shouldFilterReceived]`
- **Fallback.** On the originator's *last* retransmission the packet is sent with `next_hop` cleared (flood), and `next_hop` is reset in the NodeDB. `[SOURCE: NextHopRouter.cpp::doRetransmissions]`
- **Last-byte ambiguity is real in a dense mesh.** With ~N nodes, the probability that some other node in earshot shares your last byte is ≈ 1 − (255/256)^(N−1). That is about 11% at N = 30 and about 32% at N = 100. `[PHYSICS: birthday-style estimate, uniform last bytes assumed]`

### 3.3 ACKs

- **Who ACKs.** The destination ACKs a DM with want_ack. Text DMs are ACKed *with want_ack themselves* (`shouldSuccessAckWithWantAck`). A destination that received the DM directly, or via next-hop, sends a 0-hop ACK so the immediate relayer stops. `[SOURCE: ReliableRouter.cpp::sniffReceived, shouldSuccessAckWithWantAck @v2.7.26]`
- **Retransmission timeout** (`getRetransmissionMsec`): `2·airtime + (2^CW(util) + 2·CWmax + 2^5)·slot + 4500 ms`. `[SOURCE]` `[PHYSICS]` For a ~62-byte frame:
  - LongFast: 2·723 + (8+16+32)·28 + 4500 ≈ **7.5 s**.
  - SHORT_TURBO: ≈ **5.0 s**.
  - The worst-case originator give-up is therefore ~3 × 7.5 ≈ 22 s on LongFast before the NAK.
- **Overheard ACKs** for DMs not addressed to us cancel our queued relay of that DM. They also stop our retransmission of it. `[SOURCE: NextHopRouter.cpp::sniffReceived]`
- **MQTT-borne implicit ACKs** do not stop retransmissions. `[SOURCE: ReliableRouter.cpp::sniffReceived; NextHopRouter.cpp::stopRetransmission comment]`

---

## 4. Airtime and congestion

### 4.1 Preset parameters (from code)

| Preset | SF | BW | CR | Source |
|---|---|---|---|---|
| LONG_FAST (default) | 11 | 250 kHz | 4/5 | MeshRadio.h::modemPresetToParams |
| SHORT_TURBO | 7 | 500 kHz | 4/5 | same |
| (RNS RNode, separate) | 7 | 250 kHz | per RNode config | fleet fact |

Both presets use preamble 16, explicit header, CRC on and sync word 0x2B. `[SOURCE: RadioInterface.h; SX126xInterface.cpp; RadioLibInterface.h @v2.7.26]`

**Centre frequencies.** The code computes `freq = freqStart + BW/2000 + channel_num·BW/1000` with US `freqStart = 902.0` and 0-based `channel_num`. `[SOURCE: RadioInterface.cpp::applyModemConfig @v2.7.26]` `[PHYSICS]`
- LongFast slot 20 (channel_num 19): 902 + 0.125 + 4.75 = **906.875 MHz**, occupying 906.75–907.00 MHz.
- SHORT_TURBO slot 8 (channel_num 7): 902 + 0.25 + 3.5 = **905.750 MHz**, occupying 905.50–906.00 MHz.
- RNode: 903.625 MHz ± 125 kHz.

The three are non-overlapping. Each Meshtastic preset is blind to the other because of both frequency and chirp rate.

### 4.2 Time on air (Semtech formula)

`[CITED: Semtech AN1200.13 "LoRa Modem Designer's Guide"; SX1261/2 datasheet §6.1.4 (not fetched)]` `[PHYSICS: computed]`

`T_sym = 2^SF / BW`; `T_pre = (n_pre + 4.25)·T_sym`; `n_payload = 8 + max(⌈(8PL − 4SF + 28 + 16·CRC − 20·IH) / (4(SF − 2·DE))⌉·(CR+4), 0)`, with low-data-rate optimisation `DE = 1` only when T_sym > 16 ms (off for both presets).

A "~40-byte text" becomes: 40 B text + ~6 B of Data protobuf framing (portnum, field tags) + 16 B header ≈ **PL = 62 B**. The framing estimate is an `[ASSERTION — uncited]`; the exact size varies with optional fields.

| PL (bytes) | LongFast T_sym | LongFast ToA | SHORT_TURBO T_sym | SHORT_TURBO ToA | ratio |
|---|---|---|---|---|---|
| 16 (header only) | 8.192 ms | 354 ms | 0.256 ms | 14.9 ms | 24× |
| **62 (~40 B text)** | | **723 ms** | | **31.6 ms** | **23×** |
| 80 | | 846 ms | | 38.0 ms | 22× |
| 237 (max) | | 2,034 ms | | 95.6 ms | 21× |

Preamble alone is 166 ms on LongFast. Firmware also computes `preambleTimeMsec = 16·T_sym` = 131 ms for LongFast, excluding the 4.25-symbol sync. `[SOURCE: RadioInterface.cpp::applyModemConfig]`

### 4.3 How firmware measures channel utilization and airUtilTx

- `logAirtime(type, ms)` adds every **TX** frame (`TX_LOG`), every **good RX** frame (`RX_LOG`) and every **bad or unparseable RX** frame (`RX_ALL_LOG`) into a 6 × 10 s ring. `channelUtilizationPercent() = Σ / 60 s`. **Airtime is computed from the frame length via the ToA formula; it is not measured RF energy.** `[SOURCE: airtime.cpp::logAirtime, channelUtilizationPercent; RadioLibInterface.cpp::handleReceiveInterrupt @v2.7.26]`
- `airUtilTx` = own TX over a 60 × 1 min ring, as % of the hour. `[SOURCE: airtime.cpp::utilizationTXPercent]`
- **Blind spots.** The figure misses:
  - other-SF and other-BW LoRa;
  - FSK and other ISM users;
  - frames below sensitivity, which still raise the noise floor;
  - frames that arrive while this node is transmitting.

  Your SDR energy-detect figure catches some of these (non-LoRa energy above its threshold) but misses sub-threshold LoRa, which is decodable below the noise floor. **Neither number is the interference experienced at another receiver.** `[PHYSICS + SOURCE]`

**Thresholds** (hard-coded members):

| Gate | Threshold | Callers | Source |
|---|---|---|---|
| `isTxAllowedChannelUtil(false)` | ch-util < **40%** | NodeInfo | airtime.h |
| `isTxAllowedChannelUtil(true)` ("polite") | ch-util < **25%** | position, telemetry, neighbour info, S&F, range test, new-node NodeInfo reply | grep @v2.7.26 |
| `isTxAllowedAirUtil()` | airUtilTx < dutyCycle × 50% | telemetry, neighbour info | airtime.cpp |
| hourly duty-cycle abort | airUtilTx > dutyCycle | `Router::send` | Router.cpp |

US `dutyCycle = 100`, so the last two never trigger in the fleet's region. **Relays and user text are not throttled by any of these.** `[SOURCE @v2.7.26]` Congestion does widen origination contention: CW steps every 20% utilization. Above 40 online nodes, position and telemetry intervals are also stretched by `Default::congestionScalingCoefficient`: interval × (1 + (N − 40)·2^SF/(BW·100)). `[SOURCE: Default.h @v2.7.26]` `[CITED: mesh-algo docs, same formula]`

### 4.4 Why node count bites LongFast harder

`[PHYSICS]`
1. **Load.** Each node's periodic traffic costs ~23× more airtime on LongFast than on SHORT_TURBO. For the same population and intervals, channel utilization scales by about the same factor. Each flood costs every relaying node one frame time.
2. **Vulnerability window.** In ALOHA terms, a frame is lost to an unsynchronised interferer arriving within ±ToA. Against background load G (fraction of time busy), P(loss) ≈ 1 − e^{−2G}. At your measured G ≈ 0.02 that is ≈ 4% per reception per interferer class, on *either* preset. Low background load is not the main threat.
3. **Correlated flood collisions (the important one).** Every relay of one packet is triggered by the *same* reception, so start times are synchronised to within the contention window. Two relays that **cannot hear each other** (CAD can't help) but are both heard by a receiver overlap whenever |Δstart| < ToA. With start slots uniform on [0, 2^CW):

   | CW (SNR bucket) | LongFast P(overlap) | SHORT_TURBO P(overlap) |
   |---|---|---|
   | 3 (≤ −14 dB) | **1.00** | 0.69 |
   | 4 | **1.00** | 0.39 |
   | 6 (≈ 0 dB) | 0.64 | 0.11 |
   | 8 (≥ +10 dB) | 0.19 | 0.03 |

   This was enumerated over all slot pairs with ToA/slot = 723/28 (LongFast) and 32/8 (SHORT_TURBO). On LongFast the contention window was sized in **slot units (CAD detection time)**, but the frame is ~26 slots long. Hidden relays that heard the source weakly are therefore effectively simultaneous. On SHORT_TURBO a frame is only ~4 slots, so the window actually de-correlates them.
4. **Capture.** LoRa receivers often decode the stronger of two co-SF frames if it is several dB stronger and its preamble timing is favourable. Two comparable-power relays (a receiver roughly equidistant from two ridge nodes) typically lose both. `[CITED: Bor, Roedig, Voigt, Alonso, "Do LoRa Low-Power Wide-Area Networks Scale?", ACM MSWiM 2016; Rahmadhani & Kuipers, "When LoRaWAN Frames Collide", WiNTECH 2018 (not fetched)]`
5. **CAD helps only for mutually audible relays.** Those don't collide anyway: the second one hears the first, sees a dupe, and cancels. CAD therefore protects exactly the case that cancellation already handles. `[SOURCE: §2.3 + PHYSICS]`

---

## 5. RF physics at 915 MHz

All figures are `[PHYSICS]` unless tagged otherwise.

### 5.1 Free-space path loss

`FSPL(dB) = 20·log10(d_km) + 20·log10(f_MHz) + 32.44 = 20·log10(d_km) + 91.7` at 915 MHz.

| d | 1 km | 5 km | 10 km | 20 km | 40 km |
|---|---|---|---|---|---|
| FSPL | 91.7 dB | 105.6 | 111.7 | 117.7 | 123.7 |

### 5.2 Noise floor and demodulation limits

> Authority for demod/SNR limits per SF is `lora_physical_layer.md` §3 — this section keeps only what §7's synthesis needs; if they disagree, that doc wins.

Thermal noise is `−174 dBm/Hz + 10·log10(BW) + NF`. With NF ≈ 6 dB (a typical SX126x front end without LNA): 250 kHz gives **−114 dBm** and 500 kHz gives **−111 dBm**. `[ASSERTION — NF of 6 dB is typical; uncited]` The required demod SNR is roughly SF7 −7.5, SF8 −10, SF9 −12.5, SF10 −15, SF11 −17.5, SF12 −20 dB. `[CITED: Semtech SX1261/2 datasheet, SNR-per-SF table; AN1200.22 (not fetched)]`

| Preset | Noise floor | Demod SNR | Sensitivity (approx.) |
|---|---|---|---|
| LongFast (SF11/250) | −114 dBm | −17.5 dB | **≈ −131.5 dBm** |
| SHORT_TURBO (SF7/500) | −111 dBm | −7.5 dB | **≈ −118.5 dBm** |

The difference is ~13 dB. In free space that is a range factor of 10^(13/20) ≈ 4.5×. In cluttered terrain with path-loss exponent ~3.5 it is about 2.3×. **SHORT_TURBO coverage is therefore structurally sparser,** so each SHORT_TURBO end device depends on fewer relays. That increases single-device-miss exposure even though it suffers fewer collisions.

The firmware's SNR range assumption (−20 … +10 dB) is anchored to SF12-class limits. On SHORT_TURBO no packet is ever received below about −7.5 dB, so the CW never drops below ≈ 5 (map(−7) = 5). The "weak-SNR goes first" ordering is compressed on SHORT_TURBO. `[PHYSICS from SOURCE getCWsize]`

### 5.3 Fresnel zones

`r₁ = 17.32·√(d₁d₂ / (f_GHz·d))` m, with distances in km. At the midpoint:

| Path | r₁ | 60% clearance |
|---|---|---|
| 2 km | 12.8 m | 7.7 m |
| 10 km | 28.6 m | 17.2 m |
| 20 km | 40.5 m | 24.3 m |
| 40 km | 57.3 m | 34.4 m |

A 10 km hop between rooftop nodes over a 20–30 m ʻōhiʻa canopy is therefore **always** partially obstructed unless one end sits on a ridge or tower.

### 5.4 Knife-edge diffraction over volcanic terrain

`[CITED: ITU-R P.526 (single knife-edge J(ν)) (not fetched)]`
`ν = h·√(2/λ·(1/d₁ + 1/d₂))` and `J(ν) ≈ 6.9 + 20·log10(√((ν−0.1)² + 1) + ν − 0.1)` for ν > −0.78. With λ = 0.328 m:

| obstruction h above LOS | d₁/d₂ | ν | J |
|---|---|---|---|
| 0 m (grazing) | 5/5 km | 0 | 6.0 dB |
| 10 m | 5/5 km | 0.49 | 10.2 dB |
| 30 m | 5/5 km | 1.48 | 16.7 dB |
| 50 m | 2/8 km | 3.09 | 22.7 dB |
| 100 m | 5/5 km | 4.94 | 26.7 dB |

Kīlauea's caldera rim, pit craters, lava-flow levees and the Mauna Loa shoulder are effectively multiple knife edges. Each adds roughly 6–27 dB. Bullington or Deygout multi-edge methods (P.526) would stack them.

**Implication:** a link 10 dB inside the margin can be lost to a 1–2 m shift in an end-node antenna height, or to a parked vehicle. Atmosphere plays no part in that.

### 5.5 Foliage and rain forest

The Weissberger model gives `L = 1.33·f^0.284·d^0.588` dB (f in GHz, 14 m < d < 400 m in foliage). `[CITED: Weissberger 1982, ECAC-TR-81-101; cf. ITU-R P.833 (not fetched)]`

| Foliage depth | 20 m | 50 m | 100 m | 200 m | 400 m |
|---|---|---|---|---|---|
| Loss | 7.5 dB | 12.9 dB | 19.4 dB | 29.2 dB | 43.9 dB |

Wet leaves and tree-fern understory increase attenuation compared with dry foliage. Ground-level nodes in Volcano-village forest can lose 20–40 dB compared with a clearing. `[ASSERTION — the wet-vs-dry delta is reported in the vegetation-loss literature but not cited precisely here]`

### 5.6 Multipath and fading

With no line of sight, the envelope is Rayleigh-like. P(fade deeper than F dB below mean) ≈ 1 − exp(−10^(−F/10)): about 10% for a 10 dB fade and about 1% for a 20 dB fade. `[PHYSICS: Rayleigh CDF]`

LoRa's chirp spread spectrum resists *frequency-selective* fading within 250–500 kHz. It does not resist flat fading of the whole channel. Coherence bandwidth in hilly forest is ~1–5 MHz for 50–200 ns delay spread, so LoRa channels are flat-faded. `[ASSERTION — delay-spread figures are typical, uncited]`

Fading is time-varying (people, vehicles, swaying trees in trades). Two receptions of the same flood seconds apart are partly decorrelated. A device that misses one relay may hear another, but only if one exists (see §2 cancellation).

---

## 6. Atmosphere over the Big Island

### 6.1 Refractivity and the k-factor

`N = 77.6/T·(P + 4810·e/T)` (T in K, P and e in hPa). `[CITED: ITU-R P.453 (not fetched)]` The standard atmosphere has dN/dh ≈ −40 N/km, so k = 4/3. Classes:
- sub-refraction: dN/dh > 0;
- super-refraction: −79 to −157 N/km;
- ducting: dN/dh < −157 N/km (that is, dM/dh < 0).

`[CITED: ITU-R P.453 / P.834 (not fetched)]`

Radio horizon is `√(2kRh)`: 13 km for a 10 m mast, 22.6 km for 30 m, and 143 km for a 1,200 m site over the sea at k = 4/3. `[PHYSICS]` Volcano (~1,100–1,200 m elevation) looks far down the slopes and out to sea. Most mesh hops are terrain- and foliage-limited long before they are horizon-limited.

### 6.2 Trade-wind inversion (TWI)

The TWI caps the moist marine layer over Hawaiʻi. It is present roughly 80–90% of the time, with a base near ~2,200 m. It shows a diurnal and seasonal cycle at Hilo: in summer the base is higher at night and lower in the afternoon. Its frequency is modulated by ENSO. `[CITED: Cao, Giambelluca et al., J. Climate 20:1145–1160, 2007, doi:10.1175/JCLI4033.1 (abstract seen via search); Longman, Diaz & Giambelluca, J. Climate 2015, doi:10.1175/JCLI-D-15-0006.1 (abstract seen); PI-CASC project page (search snippet)]`

Across the inversion, temperature rises and humidity collapses. The resulting dN/dh can be strongly negative over a thin layer, enough to form an **elevated duct** near the inversion base. `[PHYSICS: e falls from ~15 to ~3 hPa across ~100–200 m, which by itself gives ΔN ≈ −50 to −60 N over that layer, i.e. −250 to −600 N/km; P.453 formula]`

**Can 915 MHz be trapped?** Trapping requires grazing angle θ ≤ √(2·ΔM·10⁻⁶) and thickness d ≳ λ/(2θ):

| duct thickness | ΔM | θ_max | λ_max | f_min |
|---|---|---|---|---|
| 50 m | 10 | 4.5 mrad | 0.45 m | 671 MHz |
| 100 m | 10 | 4.5 mrad | 0.89 m | 335 MHz |
| 200 m | 20 | 6.3 mrad | 2.5 m | 119 MHz |

`[PHYSICS: ray-trapping + waveguide-cutoff order of magnitude; compare ITU-R P.834 / P.453 duct discussions]`

So yes: a 50–100 m inversion layer *can* duct 915 MHz. But **both terminals must couple into a layer at ~2 km.**
- Volcano sits at ~1.2 km, below the typical base.
- Mauna Loa and Mauna Kea summit sites sit above it.
- Coastal nodes sit far below it.

Elevated ducting is therefore a plausible cause of *anomalous long-range reception*: a summit or upper-slope node suddenly hearing a far island, which then injects foreign packets and adds flood load. It is **not** a plausible cause of one in-town device missing a local hop. `[ASSERTION — reasoning from geometry; no Big Island 915 MHz ducting measurement found]`

### 6.3 Rain attenuation: tiny at 915 MHz

`γ_R = k·R^α`. At 1 GHz, ITU-R P.838-3 gives k_H ≈ 2.59×10⁻⁵, α_H ≈ 0.969; k_V ≈ 3.08×10⁻⁵, α_V ≈ 0.859. `[CITED: ITU-R P.838-3 Table (not fetched — coefficients from memory; treat to ±50%)]`

| Rain rate | 10 mm/h | 50 mm/h | 100 mm/h | 150 mm/h |
|---|---|---|---|---|
| γ (H) | 0.0002 dB/km | 0.0011 | 0.0022 | 0.0033 |

Even 20 km of 100 mm/h rain costs < 0.05 dB. `[PHYSICS]`

What rain *does* change is (a) water films on radomes and nearby foliage, worth a few dB locally, and (b) human behaviour (people go indoors, devices go into pockets). `[ASSERTION — uncited]`

### 6.4 Gases, humidity, cloud, vog

- **Gases.** Oxygen and water-vapour absorption at 1 GHz are about 0.005–0.01 dB/km at sea level. `[CITED: ITU-R P.676 Fig. 1 (not fetched)]`
- **Cloud and fog.** Liquid-water attenuation scales roughly as f² in the Rayleigh regime. At 1 GHz, K_l is about 10⁻³ (dB/km)/(g/m³). A dense orographic cloud at 0.5 g/m³ therefore costs about 0.0005 dB/km. `[CITED: ITU-R P.840 (not fetched); PHYSICS: f² scaling]`
- **Humidity.** Its main effect is through N (the refractive gradient), not absorption.
- **Vog.** Volcanic smog is sulphate aerosol and SO₂. Particle sizes are about 0.1–1 µm, 10⁶ times smaller than λ, and liquid content is far lower than fog. Its RF effect is negligible. `[PHYSICS: Rayleigh scattering ∝ (a/λ)⁴]`

### 6.5 Diurnal effects

On the Big Island's windward slopes, nocturnal downslope (katabatic) drainage flow and daytime upslope or sea-breeze flow are well documented. Clear nights can build a shallow **surface radiation inversion**, which brings mild super-refraction and occasionally weak surface ducts over smooth terrain or sea. `[CITED: Chen & Nash 1994, "Diurnal variation of surface airflow and rainfall frequencies on the island of Hawaii", Mon. Wea. Rev. (not fetched)]` At mesh distances of 1–20 km over rough forested terrain this shifts SNR by perhaps a few dB, which matters only for links already at the margin. `[ASSERTION — magnitude uncited]`

**The operationally bigger diurnal effect is the mesh itself.** Phones get plugged in, people move, and traffic peaks in the evening. `[ASSERTION — uncited]`

### 6.6 What actually matters at mesh distances (ranked)

| Factor | Typical magnitude |
|---|---|
| Terrain diffraction | 6–27 dB per edge |
| Foliage and wet forest | 10–40 dB |
| Fading | 10–20 dB tails, 1–10% of the time |
| Antenna height and placement | equivalent to the above |
| Refractive anomalies (super-refraction, ducting) | rare, mainly increase range |
| Rain, gas, cloud, vog | < 0.1 dB total |

`[PHYSICS: §5–6]`

---

## 7. Synthesis: why a single device misses a message

The context: the message reached every gateway, both presets and MeshCore, but one end device never displayed it.

Likelihoods below are `[ASSERTION — engineering judgement]` for *this* fleet context (low background load, dense mixed-role community, two presets, forest and volcanic terrain). Mechanisms and log signatures are `[SOURCE]` where marked.

| # | Mechanism | Rough likelihood | What it looks like in logs / evidence | Distinguishing test |
|---|---|---|---|---|
| 1 | **Display path, not RF.** App disconnected or BLE reconnect; ToPhone queue overflow (8 on classic ESP32, 32 otherwise); app thread placement (bridged copy arrives *from the gateway* with a new id, so it may sit in a different conversation); sender ignored; channel muted | High (must be ruled out first) | Device log *has* `handleReceived(REMOTE)` / `Delivering rx packet` for the id; or `ToPhone queue is full, discard oldest` `[SOURCE: MeshService.cpp::sendToPhone; RoutingModule.cpp]` | Pull the end device's serial or debug log for the packet id. RF is cleared if it was received |
| 2 | **Hidden-terminal suppression.** The neighbours that serve the end device each cancelled after hearing *another* relay the end device cannot hear | High on LongFast in dense CLIENT areas | Neighbour logs: `Ignore dupe incoming msg` before their own TX; `txRelayCanceled` increments; no `Completed sending` for that id `[SOURCE: FloodingRouter.cpp; RadioLibInterface.cpp::completeSending]` | Neighbour shows dupe and cancel, end device shows nothing |
| 3 | **Correlated relay collision at the end device.** Two hidden relays fire within one frame time | High on LongFast (P ≈ 1 at low CW), low on SHORT_TURBO | End device logs `Ignore received packet due to error=…` with plausible header bytes near the expected time; `rxBad`++; SDR shows two overlapping bursts | Both relays' logs show `Completed sending` at overlapping times |
| 4 | **Marginal link plus fade or foliage** (no collision) | Medium | End device's historical SNR from the relevant neighbours sits near the demod floor (≤ −15 dB LF, ≤ −5 dB ST); nothing logged at all (below preamble detect) | SNR history, plus a repeat test at the same hour |
| 5 | **End device deaf at that moment.** Transmitting (half duplex: its own telemetry or relay), or in power-save sleep (TRACKER/SENSOR/`is_power_saving`), or mid-reception of another frame | Medium-low | End device `Completed sending` / `Packet TX: %ums` overlapping the window; or sleep logs | Device TX timestamps vs relay TX times |
| 6 | **Hop exhaustion.** Every path that won the race had used all hops by the time it neared the end device (default hop_limit 3; "first copy wins" path may be long) | Medium in sprawling meshes | Nearest relays heard it with `hop_limit = 0` (they log the reception but do not relay) | Neighbour reception records with hop_limit 0 |
| 7 | **Decode or policy failure.** Channel hash or PSK mismatch, wrong channel index on the bridged copy, KNOWN_ONLY unknown sender | Medium if the device is configured differently | `packet decoding failed or skipped (no PSK?)` `[SOURCE: Router.cpp::handleReceived]` | Device log line for that id |
| 8 | **Preset mismatch.** End device on the preset the message reached only via a lossy bridge path, or on SHORT_TURBO's sparser coverage | Context-dependent | Device's preset vs where gateways logged the original vs the bridged id | Check config |
| 9 | **TX queue overflow at a key relay** (16 entries; lower priority evicted) | Low at 0–2% busy | `TX queue is full…` `[SOURCE: MeshPacketQueue.cpp::enqueue]` | Relay log |
| 10 | **Dedupe false positive** (32-bit id collision in a ≥100-entry LRU) | Negligible (~10⁻⁸ per packet) | — | — |
| 11 | **Atmospheric fade** (rain, gas, vog) | Negligible | — | — |
| 12 | **Ducting-induced foreign load** briefly raising collisions | Very low, mainly for summit-facing nodes | Bursts of never-seen node ids with low hops and odd SNR | Correlate with the inversion sounding (Hilo 00Z/12Z) |

**Where the operator's hypothesis fits.**
- *Dense nearby nodes plus managed-flood suppression*: rows 2 and 3 cover this, and the code supports it strongly.
- *CLIENT_MUTE roles*: these reduce the relay set without contributing cancellations. A cluster of MUTE nodes around the end device means fewer candidate relays, which raises reliance on the one CLIENT that may cancel.
- *Preset-dependent channel utilization*: this works through collision geometry (row 3), not through utilization-based throttling. Relays are not throttled (§4.3), and at 0–2% the CW is already at its minimum.

---

## 8. Testable predictions (using data the fleet already has)

Each item gives the measurement, then what would confirm or refute the hypothesis.

1. **Display path vs RF (rule out row 1).**
   - *Measure:* the end device's own radio log, searched for the packet id (and the bridged id).
   - *Confirm RF loss:* neither id appears in any `Received`/`handleReceived`/`error=` line.
   - *Refute (display path):* the id appears, so the loss was downstream of the radio.
2. **Hidden-terminal suppression.**
   - *Measure:* for each gateway or box neighbouring the end device, the order of `Ignore dupe` vs `Completed sending` for that id, with timestamps.
   - *Confirm:* every node the end device can hear (per its historical neighbour SNR list) cancelled, and the copies they heard came from nodes absent from the end device's neighbour list.
   - *Refute:* at least one audible neighbour completed a TX of that id.
3. **Correlated-collision model, LongFast vs SHORT_TURBO.** Predicted: P(overlap of hidden relays) is ~1.0 vs ~0.4–0.7 at low CW.
   - *Measure:* over a week, the ratio `rxBad / (rxGood + rxBad)` on each preset at boxes with ≥ 2 mutually hidden upstream relays. Use the counters logged in `LOG_DEBUG("txGood=…,rxBad=…")` or device-metrics telemetry.
   - *Confirm:* the LongFast bad-frame ratio clusters within ~0.2–1 s after receptions of *other* frames with the same id.
   - *Refute:* rxBad is uncorrelated with flood events.
4. **SNR-ordered relay timing.**
   - *Measure:* for many floods, Δt between a relay's reception and its `Completed sending`, against its `rx_snr`.
   - *Confirm:* on LongFast Δt ≈ 448 ms + U(0, 2^CW(snr))·28 ms (plus ToA), with mean rising sharply above 0 dB.
   - *Refute:* no SNR dependence, which would indicate role overrides or a firmware difference.
5. **Cancellation rate vs neighbour density.**
   - *Measure:* `txRelayCanceled / (txRelay + txRelayCanceled)` per box, against the number of distinct relay bytes heard per hour.
   - *Confirm:* a monotone increase with density.
   - *Implication:* the dense-cluster boxes are the ones most likely to leave a fringe device unserved.
6. **ROUTER_LATE (or CLIENT_BASE with favourite) as a cure, as an A/B experiment on one box.**
   - *Measure:* the fringe device's reception rate of fleet broadcasts with its best-SNR fleet neighbour as CLIENT vs ROUTER_LATE, a week each, same message cadence.
   - *Confirm:* the reception rate rises and the neighbour's `txRelayCanceled` falls to ~0.
   - *Refute:* no change, which means the miss is a link-budget or collision problem rather than suppression.
7. **Hop exhaustion.**
   - *Measure:* `hop_limit` on the copies that the end device's neighbours accepted, using hops_away in their journals.
   - *Confirm:* copies arrive at the neighbours with hop_limit 0 on miss events and ≥ 1 on hit events.
   - *Refute:* hop_limit is never 0 near the device.
8. **Half-duplex deafness.**
   - *Measure:* the end device's own TX start/end times (`Packet TX: %ums` with timestamps) against neighbours' relay times for the missed id.
   - *Confirm:* overlap on miss events, significantly more often than the base TX duty would predict.
9. **Atmosphere null test.**
   - *Measure:* hourly link SNR for 2–3 fixed fleet-to-fleet links, against the Hilo radiosonde inversion base and strength (00Z/12Z) and rain-gauge rate.
   - *Prediction:* rain shows no measurable SNR change (< 0.1 dB expected); diurnal variation is a few dB at most; and anomalous long-range receptions (foreign node ids) correlate with strong, low inversions.
   - *Refute physics:* SNR drops of > 3 dB tracking rain rate would point to wet foliage or antenna effects rather than path attenuation, and are worth investigating locally.
10. **SDR vs firmware utilization.**
    - *Measure:* SDR energy-detect busy % per channel against the firmware `channel_utilization` reported by co-located nodes, over the same 60 s windows.
    - *Prediction:* firmware ≤ SDR when non-LoRa energy is present; firmware > SDR when sub-noise LoRa frames are being decoded.
    - Either outcome bounds the blind spot of each instrument.

---

## 9. Implications for the software: what a display can honestly claim

Given §2–4, these follow from the code semantics. They are `[ASSERTION — design inference]` unless tagged.

| Claim on screen | Can it be honest? | Evidence that justifies it |
|---|---|---|
| "Heard by box X" | Yes, for the specific `(from, id)` | X's log or uplink shows reception of that id. A bridged copy is a *different* id; correlate by content and timing and label it "bridged copy heard" |
| "Relayed by box X" | Yes | X logged `Completed sending` / `txRelay++` for that id `[SOURCE: RadioLibInterface.cpp::completeSending]`. **Not** inferable from another node's `relay_node` byte alone (last byte only, ambiguous) |
| "Delivered" (broadcast) | **No.** A broadcast's implicit ACK means "one neighbour of the sender relayed it" `[SOURCE: ReliableRouter.cpp]` | Show "sender saw a relay" or "N fleet boxes heard it"; never "delivered to mesh" |
| "Delivered" (DM with ACK) | Only "the destination's firmware ACKed". The ACK is channel-PSK only, so it can be forged on public channels (2.8 adds AckProof for PKI DMs, §11). It is not "displayed to the human" | ACK with matching request_id; in 2.8 a verified AckProof |
| "Device Y did not receive" | **Only if Y's own log is available and shows no id.** Absence at the fleet's receivers is not evidence about Y | Y's log; otherwise "unobserved at Y" |
| "Y cannot hear X" | Only from a *positive* measurement: a failed directed probe, or an SNR history below the demod floor over a window. Not from a single miss (fading, collision and suppression all produce misses) | Link test or SNR history; state the window |
| hops_away | It is the hop count of **the first copy accepted**, not the shortest path `[SOURCE §2.4]` | Label "hops (winning copy)" |
| Channel utilization | Firmware: decoded LoRa airtime at that node's SF/BW over 60 s. SDR: energy above threshold. Neither is interference at a third receiver | Label the instrument and window |

Tri-state every per-device cell as **heard / not heard (device log present) / unobservable (no log)**. The third state must never render as the second.

---

## 10. Sources

### Firmware (pinned)

- Tag `v2.7.26.54e0d8d` (commit 54e0d8d0ab2f…) — https://github.com/meshtastic/firmware/tree/v2.7.26.54e0d8d
  - `src/mesh/FloodingRouter.cpp`, `.h` — https://github.com/meshtastic/firmware/blob/v2.7.26.54e0d8d/src/mesh/FloodingRouter.cpp
  - `src/mesh/NextHopRouter.cpp`, `.h` — https://github.com/meshtastic/firmware/blob/v2.7.26.54e0d8d/src/mesh/NextHopRouter.cpp
  - `src/mesh/ReliableRouter.cpp` — https://github.com/meshtastic/firmware/blob/v2.7.26.54e0d8d/src/mesh/ReliableRouter.cpp
  - `src/mesh/Router.cpp` — https://github.com/meshtastic/firmware/blob/v2.7.26.54e0d8d/src/mesh/Router.cpp
  - `src/mesh/PacketHistory.cpp`, `.h` — https://github.com/meshtastic/firmware/blob/v2.7.26.54e0d8d/src/mesh/PacketHistory.cpp
  - `src/mesh/RadioInterface.cpp`, `.h` — https://github.com/meshtastic/firmware/blob/v2.7.26.54e0d8d/src/mesh/RadioInterface.cpp
  - `src/mesh/RadioLibInterface.cpp`, `.h` — https://github.com/meshtastic/firmware/blob/v2.7.26.54e0d8d/src/mesh/RadioLibInterface.cpp
  - `src/mesh/MeshPacketQueue.cpp` — https://github.com/meshtastic/firmware/blob/v2.7.26.54e0d8d/src/mesh/MeshPacketQueue.cpp
  - `src/airtime.cpp`, `.h` — https://github.com/meshtastic/firmware/blob/v2.7.26.54e0d8d/src/airtime.cpp
  - `src/mesh/MeshRadio.h` (preset table) — https://github.com/meshtastic/firmware/blob/v2.7.26.54e0d8d/src/mesh/MeshRadio.h
  - `src/mesh/MeshTypes.h`, `src/mesh/Default.h/.cpp`, `src/mesh/mesh-pb-constants.h`, `src/mesh/MeshService.cpp`, `src/modules/RoutingModule.cpp`, `src/modules/AdminModule.cpp`, `src/mesh/NodeDB.cpp`, `src/mesh/generated/meshtastic/config.pb.h` (same tag)
- Tag `v2.8.2.d5f1619` — https://github.com/meshtastic/firmware/tree/v2.8.2.d5f1619 (files cited in §11)
- PR #12337 / squash commit e8347ca1b on develop — https://github.com/meshtastic/firmware/pull/12337
- Commit f86cb7781 "Remove fragile JSON libraries…" (#10152) — https://github.com/meshtastic/firmware/pull/10152
- Commit 389559bdd "re-derive my_node_num when ensurePkiKeys() mints the identity keypair" (#11426) — https://github.com/meshtastic/firmware/pull/11426
- Commit 7e9525ad8 "default US to LongTurbo on first region selection" (#11637) — https://github.com/meshtastic/firmware/pull/11637
- Release notes v2.8.0 (revoked) — https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3

### Meshtastic docs and blog (secondary)

- Mesh algorithm — https://meshtastic.org/docs/overview/mesh-algo/
- Device config / roles / rebroadcast modes — https://meshtastic.org/docs/configuration/radio/device/
- "Let's talk about 2.8" (2026-10-08) — https://meshtastic.org/blog/lets-talk-about-2-8/
- Third-party summaries (not relied on): https://www.sdrstore.eu/meshtastic-2-8-packet-signing-location-privacy-node-migration/ ; https://newreleases.io/project/github/meshtastic/firmware/release/v2.8.0.47db0e3

### Radio and propagation (not fetched this session unless noted)

- Semtech AN1200.13, LoRa Modem Designer's Guide (ToA formula); SX1261/2 datasheet (SNR per SF, ToA); AN1200.22 (LoRa modulation basics); AN1200.48 (CAD performance).
- ITU-R P.453 (radio refractive index; refractivity formula; gradient classes).
- ITU-R P.834 (tropospheric refraction effects; ducting).
- ITU-R P.526 (propagation by diffraction; knife-edge J(ν)).
- ITU-R P.838-3 (specific attenuation model for rain).
- ITU-R P.676 (attenuation by atmospheric gases).
- ITU-R P.840 (attenuation due to clouds and fog).
- ITU-R P.833 (attenuation in vegetation).
- Weissberger, M.A., "An initial critical summary of models for predicting the attenuation of radio waves by trees", ECAC-TR-81-101, 1982.
- Bor, M., Roedig, U., Voigt, T., Alonso, J., "Do LoRa Low-Power Wide-Area Networks Scale?", ACM MSWiM 2016.
- Rahmadhani, A., Kuipers, F., "When LoRaWAN Frames Collide", ACM WiNTECH 2018.

### Hawaiʻi atmosphere

- Cao, G., Giambelluca, T.W., Stevens, D.E., Schroeder, T.A., "Inversion Variability in the Hawaiian Trade Wind Regime", J. Climate 20, 1145–1160 (2007), doi:10.1175/JCLI4033.1 (abstract via search).
- Longman, R.J., Diaz, H.F., Giambelluca, T.W., "Sustained Increases in Lower-Tropospheric Subsidence over the Central Tropical North Pacific Drive a Decline in High-Elevation Rainfall in Hawaii", J. Climate (2015), doi:10.1175/JCLI-D-15-0006.1 (abstract via search).
- PI-CASC, Monitoring and analysis of climate variability and change in Hawaiʻi — https://pi-casc.soest.hawaii.edu/research/research-projects/monitoring-climate-variability/ (TWI ~2,200 m; search snippet).
- Chen, Y.-L., Nash, A.J., "Diurnal variation of surface airflow and rainfall frequencies on the island of Hawaii", Mon. Wea. Rev. 122 (1994) (not fetched).

---

## 11. What changes in 2.8 (from 2.7.26)

Compared at `v2.7.26.54e0d8d` → `v2.8.2.d5f1619`. Tags `v2.8.0.47db0e3` (revoked), `v2.8.0.7239fe8`, `v2.8.1.8e6a88d` and `v2.8.2.4c89b69` also exist. `develop` was checked at `68f47efdd` (2026-11-16). The routing and airtime diff alone is about 8,200 lines across 8 files. This section reports what bears on the questions above. It is not an exhaustive changelog.

### 11(a) Routing, flooding, role and airtime changes, and what they predict

| Change (v2.8.2) | Evidence | Prediction for single-device misses |
|---|---|---|
| **Contention constants unchanged.** CWmin/CWmax 3/8, SNR −20…+10, the 16-slot client offset and ROUTER `2·CW` are all identical | `[SOURCE: RadioInterface.h/.cpp @v2.8.2]` | The LongFast hidden-relay collision geometry of §4.4 is unchanged |
| **Airtime gates unchanged.** 40% / 25% / ½ duty cycle, 60 s window. 2.8 adds a ~21-min EMA "smoothed" utilization and documents the blind spots (five drop paths log neither RX nor RX_ALL) | `[SOURCE: airtime.h header doc @v2.8.2]` | No new relay throttling. Utilization semantics are better documented and still decoded-airtime-only |
| **Unicast reliability raised.** `NUM_RELIABLE_UNICAST_ATTEMPTS = 5` (broadcast stays 3); `NUM_INTERMEDIATE_RETX` 2 → 3 | `[SOURCE: NextHopRouter.h; ReliableRouter.cpp::send @v2.8.2]` | DMs survive more transient loss. Broadcasts are not helped, and docs' "five" now matches code for DMs |
| **Next-hop route health.** `ROUTE_TTL_MSEC` = 30 min; `ROUTE_FAILURE_THRESHOLD` = 3 consecutive un-ACKed deliveries marks a route dead; refuses to learn from ambiguous or unknown relay bytes | `[SOURCE: NextHopRouter.h/.cpp @v2.8.2]` | Fewer DMs black-holed by stale or ambiguous next hops |
| **Traffic Management Module (TMM)** compiled in by default (except tiny parts) | `[SOURCE: mesh-pb-constants.h HAS_TRAFFIC_MANAGEMENT; NodeDB.cpp::installTrafficManagementDefaults]` | Default installs only position dedup (6 h window). Hop-exhaust, rate-limit, drop-unknown and nodeinfo direct-response stay **opt-in**. If someone enables `exhaust_hop_*`, relays force `hop_limit = 0` (`perhapsRebroadcast` logs `Traffic management: exhaust hops`), which is a **new single-device-miss mechanism** for nodes 2+ hops away |
| **HopScalingModule** (variable hops) on by default | `[SOURCE: mesh-pb-constants.h HAS_VARIABLE_HOPS=1; Router.cpp send path @v2.8.2]` | Applies only to **own-originated POSITION / TELEMETRY / NODEINFO / NEIGHBORINFO** broadcasts, and never raises above the configured limit. **Text is unaffected.** The blog's "hop limits drop while the channel is congested" is broader than the code for text messages |
| **Pre-hop firmware drop** (`MESHTASTIC_PREHOP_DROP 1`): packets whose hop_start is missing are kept out of modules, phone delivery, MQTT and rebroadcast | `[SOURCE: configuration.h; Router.cpp::handleReceived @v2.8.2]` | Very old community nodes' traffic disappears from 2.8 displays |
| **Packet authentication policy.** New `DECODE_POLICY_REJECT` and `RoutingAuthVerdict {ACCEPT, OPAQUE_RELAY_ONLY, REJECT}`; upgraded-copy handling now checks the auth gate before replacing a queued copy | `[SOURCE: Router.h; FloodingRouter.cpp::perhapsHandleUpgradedPacket @v2.8.2]` | New failure classes: a mis-signed or policy-rejected packet is relayed opaquely or not at all, and **not displayed**. The blog states 2.8.0 and 2.8.1 signatures are mutually incompatible, so do not mix them |
| **Opaque relay dedup** (`OPAQUE_SEEN_MAX` = 32) for packets on unknown channels | `[SOURCE: NextHopRouter.cpp::relayOpaquePacket @v2.8.2]` | Foreign-channel traffic is still relayed but deduped in a small, separate table |
| **Own-NodeNum spoof drop** (#12270, in v2.8.2.4c89b69) | `[SOURCE: tag commit message]` | Packets claiming our NodeNum are dropped |

### 11(b) Fleet-breaking changes: verification of the three prior-research claims

1. **"2.8 removes MQTT JSON output."** **CONFIRMED.**
   - In v2.8.2, `src/mqtt/MQTT.cpp` and `MQTT.h` contain **zero** occurrences of `json` or `serializ`.
   - The protobuf field now reads: `/* Deprecated: JSON packet support on MQTT was removed, and this field is ignored. */ bool json_enabled;`.
   - The removal is commit f86cb7781 (#10152, "Remove fragile JSON libraries from the firmware while retaining Meshtasticd JSON support", dated 2026-06-03 on develop), which deleted 166 lines from MQTT.cpp.
   - "Retaining meshtasticd JSON support" refers to the **portduino JSON file and trace output** (`portduino_config.JSONFilename`, `MeshPacketSerializer::JsonSerialize` in `Router.cpp`), not MQTT.
   - `[SOURCE: v2.8.2 MQTT.cpp, module_config.pb.h:175; git show f86cb7781]` The release notes say "full JSON support is kept in meshtasticd". `[CITED: v2.8.0 release notes]` That is **true for the file/trace output and false for MQTT**: docs and code diverge on the point that matters to the inbox.
2. **"NodeNum is re-derived from the public key (node ids change)."** **CONFIRMED.**
   - In v2.8.2, `NodeDB::createNewIdentity()` sets `my_node_num = crc32Buffer(public_key)`. If that differs from the current number, it **removes the old self entry** and bumps `identityGeneration`.
   - It is called from `generateCryptoKeyPair()` on every boot (unless config decode failed), via `ensurePkiIdentity()`.
   - In v2.7.26 the number is MAC-derived (`pickNewNodeNum`) and no crc32-of-key assignment exists in NodeDB.cpp.
   - meshtasticd (`variants/native/portduino.ini` @v2.8.2) does not exclude PKI keygen.
   - Related commits: 389559bdd (#11426), 73c411052 (#11732, "resend my_info when the node num moves mid-session").
   - The blog confirms that other nodes see a new node and that MQTT topics and dashboards need updating. `[SOURCE: NodeDB.cpp::createNewIdentity, generateCryptoKeyPair, setup ~L612–640 @v2.8.2]` `[CITED: blog 2026-10-08]`
   - A community "identity preservation" PR was mentioned in a news summary. It is **not** reflected in v2.8.2 code.
3. **"2.8.2 builds DMShell ON by default on Linux; PR #12337 (develop, ~2026-11-10) makes it opt-in."** **CONFIRMED (both halves).**
   - In v2.8.2, `src/modules/DMShell.h` does `#if defined(ARCH_PORTDUINO) && defined(__linux__) → #define MESHTASTIC_HAS_DMSHELL 1`, and `Modules.cpp` instantiates `DMShellModule` unconditionally under that define. The constructor logs "DMShell enabled on Portduino: remote shell access is dangerous…".
   - Authorization at v2.8.2 (`isAuthorizedPacket`) accepts:
     - local API packets (`from == 0`) unless `is_managed`;
     - **any packet on a channel named "admin" if `admin_channel_enabled`**;
     - PKI DMs from AdminKeys.
   - PR #12337 (branch head 75540ed51, 2026-11-10 15:03 UTC; squash-merged to develop as **e8347ca1b**, 2026-11-10 20:22 UTC) adds `Security.DMShell` (default false) and restricts auth to AdminKeys PKI DMs only. Its message says the module "was built into every Linux build and on by default".
   - `git tag --contains e8347ca1b` returns **no tags**, so no released 2.8 build carries the fix.
   - `[SOURCE: v2.8.2 DMShell.h/.cpp, Modules.cpp; git log origin/develop]`
   - **Note:** that PR commit is authored by `Claude <noreply@anthropic.com>`. That is an upstream fact, not a judgement on its quality.

Further breaking items worth knowing (`[CITED: v2.8.0 release notes; blog]`, spot-checked in source where noted):
- Position and telemetry broadcasts become **opt-in** after upgrade (#10929). Precise position is blocked on known-key or public channels.
- The filesystem format changed (#10171). Long names are limited to 24–25 bytes.
- The US first-region default is **LongTurbo**, applied only on first region selection via the device UI (#11637, commit 7e9525ad8). The `NodeDB` default preset is still LONG_FAST. `[SOURCE: NodeDB.cpp L1192 @v2.8.2]`
- 2.8.0 is **revoked**. Its signatures are incompatible with 2.8.1+.

### 11(c) New capabilities the fleet could use

- **AckProof.** An HMAC-based authenticated delivery receipt for PKI DMs. It lets "delivered" mean "the real recipient's firmware got it". Its own header states the honest limit: it does **not** protect the retransmission loop, and relays can't verify it. `[SOURCE: src/mesh/AckProof.h @v2.8.2]` This is directly usable for §9's "Delivered (DM)" row.
- **Next-hop route-health state** (TTL, failure counts) with explicit log lines (`Next hop … stale (age/fails); flood and clear`). This is a scrape-able per-destination routing signal. `[SOURCE: NextHopRouter.cpp @v2.8.2]`
- **Noise-floor tracking** with a sliding-window estimate (#9347). This gives a firmware-side complement to the SDR busy %. The `develop` head adds a FEM LNA AUTO mode chosen from the noise floor (68f47efdd). `[CITED: release notes; develop log]`
- **meshtasticd JSON packet file** (`JSONFilename`, optional portnum filter). It was already present in 2.7.26 and is retained in 2.8. It is the most direct **replacement for MQTT JSON** for a local inbox, but it only sees what that box decodes. `[SOURCE: Router.cpp @v2.7.26 and @v2.8.2]`
- **TMM counters and policies.** Dedup, rate-limit and drop-unknown give observable, per-box traffic-policing decisions, if logged.
- **Signed broadcasts** (XEdDSA). A "Signed · verified" display distinguishes authentic sender attribution from merely channel-PSK-decoded text. This matters for a public HawaiiNet-style mesh. `[CITED: blog]`

### 11(d) Before rolling 2.8: checklist

1. **Inbox consumer.** Replace the MQTT-JSON reader with the protobuf `ServiceEnvelope` on `/2/e/`, or with the meshtasticd JSON file. Prove the new reader on one box *before* any box upgrades (§11b-1).
2. **Identity map.** Record old NodeNum → public key for every fleet node *now*. After upgrade, map new NodeNum = crc32(pubkey). Update favourites (they drive ROUTER hop-preservation and CLIENT_BASE behaviour), MQTT topic subscriptions, dashboards, ignore lists and any `next_hop`-dependent tooling (§11b-2).
3. **DMShell.** Do not deploy v2.8.2 meshtasticd on internet-adjacent or shared-channel boxes as-is.
   - Either wait for a tag containing e8347ca1b,
   - or ensure `admin_channel_enabled` is false, no channel is named "admin", AdminKeys hold only fleet keys, and local API exposure is firewalled (§11b-3).
   - Verify with the startup log line ("DMShell enabled on Portduino…").
4. **Never 2.8.0.** Check every fleet and community neighbour for 2.8.0 because of signature incompatibility.
5. **Signing policy.** Keep `Compatible` while 2.7 nodes remain. Expect some community packets to show as unsigned.
6. **Re-enable position and telemetry** on fleet boxes that consumers rely on, since they become opt-in. Check that public-channel precision clamps don't break maps.
7. **Preset discipline.** Make sure no re-provisioned node picks up the first-region LongTurbo default. Pin LongFast slot 20 and SHORT_TURBO slot 8 explicitly.
8. **TMM defaults.** Confirm that only position dedup is active; leave hop-exhaust **off** unless deliberately measured.
9. **Filesystem format change.** Back up config. Expect a full erase on some boards.
10. **Baseline first.** Capture a week of the §8 measurements on 2.7.26: rxBad ratio, cancel rate, per-device reception. This lets 2.8 effects be measured rather than asserted.

### YouTube video

**Not found.** Searches for a Meshtastic 2.8 YouTube walkthrough returned only release notes, blog and third-party articles. The official blog (2026-10-08) says "The Meshtastic team is planning a livestream to walk through the features and changes in 2.8. Watch for the announcement", with no link. No video claims are listed, because none could be sourced. `[CITED: blog]`

---

*Blind spots (stated, not averaged away):*
- No fleet logs were examined, so the §7 likelihoods are judgement, not measurement.
- External physics references marked *(not fetched)* were not re-verified this session. The P.838 coefficients in particular are from memory; the "rain is negligible" conclusion holds even if they are off by 10×.
- The 2.7.26 → 2.8.2 diff was read selectively: routing, airtime, history, MQTT, identity and DMShell. Other files (PhoneAPI, NodeDB storage, radio drivers) may carry further behaviour changes not covered here.
- The Arduino `map()` no-clamp behaviour above +10 dB SNR is inferred from code reading, not observed.
