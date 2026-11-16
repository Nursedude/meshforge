# HawaiiMesh Meshtastic channel plan and decoupled MOC interop

> **Read this first: the primary source could not be fetched (2026-11-16).** Every request to
> `www.hawaiimesh.net` and `hawaiimesh.net` failed with `getaddrinfo ENOTFOUND`. The session's
> network allowlist blocks the host. It is not known whether the site itself is down. These hosts
> were blocked the same way: `meshtastic.org`, `www.hamradiohawaii.com` and `www.regionmesh.com`.
> `web.archive.org` was refused by the fetch tool. **No HawaiiMesh channel name, PSK, preset, slot,
> broker or rule below was read from hawaiimesh.net.** Nothing has been invented to fill the gaps.
> Facts about HawaiiMesh fall into two groups:
> (a) search-engine snippets, which are second-hand, and
> (b) MeshForge's own internal fleet records, which describe what the fleet does, not what HawaiiMesh publishes. These are labelled `[internal record]`.
> To close the gaps, allow `hawaiimesh.net`, `www.hawaiimesh.net` and `meshtastic.org` in the
> session network settings, then re-run this research.

## 1. What hawaiimesh.net publishes about its Meshtastic network (channels, PSKs, presets, slots, roles, rules, maps, Discord)

### Takeaway
UNKNOWN. The channel page and its sibling pages could not be fetched, and web search does not index
the hawaiimesh.net channel list. The only Hawaii-specific facts are indirect. MeshForge's own records
show a Meshtastic channel named **`HawaiiNet`** in use on the fleet, plus a mention of the community's
"wiki". A separate Oahu ham-radio page describes a solar repeater build-out on 915 MHz.

### Cited Findings
- `https://www.hawaiimesh.net/networks/meshtastic/meshtastic-channels` failed (ENOTFOUND, session
  allowlist). So did `https://www.hawaiimesh.net/networks/meshtastic` and the bare `hawaiimesh.net`
  path. Fetch attempts, 2026-11-16. No channel names, PSKs, presets or slots were obtained.
- Web searches for `hawaiimesh meshtastic channels`, `"hawaiimesh" meshtastic OR meshcore` and
  `"HawaiiNet" meshtastic channel` returned **no** hawaiimesh.net page. The search tool reported that
  "nothing … mentions" hawaiimesh. [search results incl. Ham Radio in Hawaii – Mesh](https://www.hamradiohawaii.com/mesh)
- An Oahu group (Ham Radio in Hawaii) says it has "set up a number of solar-powered radio repeaters /
  Nodes on Oahu". It operates "on the 915 mHz frequency in the US, using the LoRa protocol" with
  Meshtastic. It also says "as of fall 2025 we have coverage throughout most of Southern - East to West
  Oahu". Per the search snippet, the page lists no channel names or keys and no MQTT set-up. The
  snippet does not say whether this group is HawaiiMesh. The quote is **as of fall 2025, so stale by
  about a year**. [Ham Radio in Hawaii – Mesh](https://www.hamradiohawaii.com/mesh) (snippet only; the page fetch failed)
- `[internal record]` The MeshForge fleet carries a Meshtastic channel named **`HawaiiNet`** at
  **ch0 (primary)** on several boxes: moc, moc1, moc4, kiai and moc5. On moc2/moc3 the fleet uses the
  `SHORT_TURBO` preset at **slot 8** and carries **no HawaiiNet channel**. These are fleet settings.
  They do not show what HawaiiMesh publishes, and the HawaiiNet PSK/preset was not recorded there.
  [/home/user/meshforge/.claude/audits/review_provenance.md, entry 2026-10-13 23:12 HST](file:///home/user/meshforge/.claude/audits/review_provenance.md);
  [/home/user/meshforge/scripts/tui_journeys.py line 164](file:///home/user/meshforge/scripts/tui_journeys.py)
- `[internal record]` The fleet also runs an RNS RNode (Heltec V4) "on HawaiiNet". This suggests the
  label is used beyond the Meshtastic channel name. [/home/user/meshforge/.claude/research/axiometa_2026_11_15.md](file:///home/user/meshforge/.claude/research/axiometa_2026_11_15.md)
- Meshtastic background, from search snippets of meshtastic.org (direct fetch blocked):
  - A node has 8 channel indexes. Index 0 is PRIMARY and 1–7 are SECONDARY or DISABLED.
  - A SECONDARY channel "shares the PRIMARY channel's LoRa settings". It adds a group and a key, not a
    separate frequency or modem.
  - The public default is LongFast in slot 0 with the short key `AQ==`.
  [Channel Configuration](https://meshtastic.org/docs/configuration/radio/channels/); [Android Messages & Channels](https://meshtastic.org/docs/software/android/user/messages-and-channels/)

### Inferences
- The fleet has used `HawaiiNet` as its primary channel name, so HawaiiMesh likely publishes a named
  primary channel, not stock LongFast. This is BELIEVED, not verified. If the preset uses the default
  frequency-slot hash, the name sets the slot, so it would differ from LongFast's slot. A non-default
  name cannot be assumed to mean a non-default PSK, though.
- A full inventory needs one of two things: the page itself, or reading a fleet radio's channel URL
  (`meshtastic --host localhost --info`) together with the operator's knowledge of where it came from.

### Gaps
- Every field of the channel table: name, PSK (`AQ==` or custom), preset, slot or frequency, purpose,
  and uplink/downlink guidance. **Not obtained.** The primary page was unreachable and no secondary
  source reproduces it.
- Published node-role guidance, hop-limit rules, etiquette, emergency/ARES channel, map URL, Discord
  invite and terms of use. **Not obtained.**
- Whether `hamradiohawaii.com` and `hawaiimesh.net` belong to the same organisation. Unknown.

## 2. Does HawaiiMesh run its own MQTT broker / map / bot infrastructure? Topic roots? Terms?

### Takeaway
Partly answered from internal records. A community **CoreScope (MeshCore analyzer) instance at
`corescope.hawaiimesh.net` takes MQTT on port 1884**, "per their wiki". No HawaiiMesh-run *Meshtastic*
broker was found. Fleet nodes publish to the public `mqtt.meshtastic.org` under root `msh/US/HI`, or to
a third-party broker `gt.wildc.net:1884`. That root is a fleet choice, not a published HawaiiMesh
standard.

### Cited Findings
- `[internal record]` "feed the community's existing `corescope.hawaiimesh.net` (MQTT 1884, per their
  wiki) and propose per-observer credentials + TLS — not a rival self-hosted instance (corrected 11-04;
  see the Hawaii Meshnet analysis)". Second-hand: the wiki page was not re-fetched this session.
  [/home/user/meshforge/.claude/research/landscape_2026_11_04.md](file:///home/user/meshforge/.claude/research/landscape_2026_11_04.md)
- CoreScope is "Self-hosted, open-source MeshCore packet analyzer. Collects MeshCore packets via MQTT,
  decodes them in real time…". It observes and never transmits.
  [Kpa-clawbot/CoreScope](https://github.com/Kpa-clawbot/CoreScope)
- `[internal record]` Fleet survey from 2026-10-13. kiai uplinks HawaiiNet to **public
  `mqtt.meshtastic.org` root `msh/US/HI`**, encrypted. moc4 uplinks to **`gt.wildc.net:1884`**. moc5
  was moved to a **localhost-only mosquitto** with root `msh` and JSON enabled.
  [/home/user/meshforge/.claude/audits/review_provenance.md](file:///home/user/meshforge/.claude/audits/review_provenance.md)
- `[internal record]` The Meshtastic v2 topic shape seen on the fleet is
  `msh/US/HI/2/e/<channel>/!nodeid`. [/home/user/meshforge/docs/substack/2026-04-10-pair-debugging-mesh-bot.md](file:///home/user/meshforge/docs/substack/2026-04-10-pair-debugging-mesh-bot.md)
- The public broker default is `mqtt.meshtastic.org` with root `msh`, and the firmware appends the
  region, giving `msh/US`. The MQTT docs warn that the default LongFast channel on the public server
  "usually has a lot of traffic". [MQTT Module Configuration](https://meshtastic.org/docs/configuration/module/mqtt/) (snippet); [NodakMesh MQTT guide](https://nodakmesh.org/blog/meshtastic-mqtt-setup-guide)
- Third-party maps such as Liam Cottle's Meshtastic Map need a node, or a node that hears it, to
  "uplink to our MQTT server". That server is uplink-only and "Downlink is disabled".
  [Meshtastic Map](https://meshtastic.liamcottle.net/)

### Inferences
- `msh/US/HI` is a regional sub-root that some Hawaii operators use on the public broker. Whether
  HawaiiMesh *prescribes* it is unknown.
- `corescope.hawaiimesh.net` is the one item of HawaiiMesh-hosted infrastructure seen so far, and it is
  on the MeshCore side. Any port-1884 Meshtastic broker they may run is unconfirmed.

### Gaps
- A HawaiiMesh Meshtastic broker hostname, credentials policy, topic root and terms of use: not found.
- Any HawaiiMesh-run Meshtastic map or bots: not found.

## 3. What a third party can observe passively vs. what needs participation; the costs a participant imposes

### Takeaway
Passive observation needs only the channel name and key, and a listen-only radio adds no airtime. Once
a node rebroadcasts, beacons, or downlinks MQTT traffic into their mesh, it spends HawaiiMesh airtime.
Per-packet consent rules (`ok_to_mqtt`) apply only to default-key channels.

### Cited Findings
- A channel is shared when name and key match. Anyone with the default `AQ==` key can decrypt the
  default channel. [NodakMesh – Channels and MQTT](https://nodakmesh.org/meshtastic/channels); [LoRaMeshDevices – public vs private keys](https://www.lorameshdevices.com/blog/meshtastic/meshtastic-meshcore-channels-explained-public-vs-private-keys.html)
- **OK to MQTT** is a per-packet flag, default false. It "only applies to Channels configured with the
  defaultpsk and eventpsk keys … Channels with custom keys ignore this setting". On a public broker a
  gateway uplinks other nodes' packets only when the sender has it on.
  [LoRa Configuration](https://meshtastic.org/docs/configuration/radio/lora/); [Android MQTT](https://meshtastic.org/docs/software/android/user/mqtt/) (snippets)
- **`ignore_mqtt`** makes the device drop packets that "passed via MQTT anywhere on the path".
  [Config_LoRaConfig protobuf](https://js.meshtastic.org/types/Protobuf.Config.Config_LoRaConfig.html)
- **CLIENT_MUTE** "does not repeat or route messages". It is meant for dense areas or for a node near
  a stronger node. The guidance is one CLIENT and the rest CLIENT_MUTE. Too many CLIENT_MUTE nodes
  reduce resilience.
  [Choosing The Right Device Role](https://meshtastic.org/blog/choosing-the-right-device-role/); [Device Configuration](https://meshtastic.org/docs/configuration/radio/device/); [Role enum](https://js.meshtastic.org/enums/Protobuf.Config.Config_DeviceConfig_Role.html)
- Rebroadcast mode `NONE` is allowed only on SENSOR, TRACKER and TAK_TRACKER, and inhibits all
  rebroadcasts. [RebroadcastMode enum](https://js.meshtastic.org/enums/Protobuf.Config.Config_DeviceConfig_RebroadcastMode.html)
- Meshtastic's default hop limit is 3 (max 7). [NodakMesh – protocols](https://nodakmesh.org/protocols)

### Inferences
- **Passive, zero-cost observation.** Use CLIENT_MUTE with position and telemetry broadcasts minimised
  or disabled, and MQTT uplink only. Downlink must be off, so nothing from the internet is injected back
  into their RF. This can be done on any HawaiiMesh channel whose name and PSK MeshForge legitimately
  holds. Reading their MQTT feed, where one exists, costs them nothing on RF.
- **Costs of participating.** Every rebroadcast, NodeInfo, position or telemetry beacon, and every
  MQTT downlink spends shared airtime and hop budget. A ROUTER-role node, or an MQTT-to-RF downlink
  gateway, adds the most load and can carry unrelated internet traffic onto Hawaii RF.
- **Etiquette.** Uplinking HawaiiNet traffic to a public broker republishes community traffic.
  Custom-key channels ignore `ok_to_mqtt`, so the community's consent is not encoded per packet. This
  makes it a policy question to settle with HawaiiMesh, which is relevant because kiai already uplinks
  to the public broker.

### Gaps
- HawaiiMesh's own stated policy on uplinking its channel to public MQTT, and on ROUTER placement, is
  unknown because the page was not fetched.

## 4. Decoupling patterns for MeshForge, and what breaks if hawaiimesh.net infra goes down

### Takeaway
MeshForge can interoperate without depending on HawaiiMesh on two conditions:
- it keeps its own primary channel, preset and slot plus a local broker on every box;
- it treats HawaiiNet as an optional secondary or observer leg that is ingested read-only.

The fleet already does part of this: moc2/moc3 run SHORT_TURBO slot 8 with no HawaiiNet, and moc5 runs
a localhost mosquitto. Uplinks from kiai and moc4 to third-party brokers are the remaining coupling.

### Cited Findings
- A SECONDARY channel shares the primary's LoRa settings (preset and slot). It cannot put HawaiiNet on
  a different frequency from the MOC's own primary. [Channel Configuration](https://meshtastic.org/docs/configuration/radio/channels/) (snippet)
- MQTT can join meshes: gateways publish to a broker and remote subscribed gateways inject the traffic
  into their mesh. [MQTT Module Configuration](https://meshtastic.org/docs/configuration/module/mqtt/) (snippet)
- `[internal record]` moc5: broker set to localhost, ch0 HawaiiNet uplink true, **downlink false**.
  moc2/moc3: separate preset and slot with no HawaiiNet. kiai: public broker. moc4: `gt.wildc.net:1884`.
  [/home/user/meshforge/.claude/audits/review_provenance.md](file:///home/user/meshforge/.claude/audits/review_provenance.md)
- `[internal record]` MeshForge's `mqtt_root_drift` probe compares the observed root with the root
  declared in `gateway.json`. Pointing at a third-party root is therefore already a watched
  configuration. [/home/user/meshforge/src/launcher_tui/handlers/meshtasticd_mqtt.py](file:///home/user/meshforge/src/launcher_tui/handlers/meshtasticd_mqtt.py)

### Inferences (assessment; BELIEVED, not measured)
- **Pattern A: separate RF plane (the strongest decoupling).** The MOC's own private primary
  channel, custom PSK, its own preset and slot (as on moc2/moc3). A *separate* radio sits on HawaiiNet's
  preset and slot. One radio cannot sit on two slots, and a secondary channel inherits the primary's
  slot. So "private primary plus public secondary" gives key separation only. It does **not** give RF
  separation, and if the MOC's primary is HawaiiNet's slot, it shares their airtime.
- **Pattern B: observer leg.** The HawaiiNet radio runs CLIENT_MUTE with beacons minimised. It uplinks
  to the box's **local** mosquitto only, with downlink off and `ignore_mqtt` on. MeshForge consumes the
  local broker. HawaiiMesh never sees MeshForge traffic and MeshForge never needs their broker.
- **Pattern C: read-only remote subscription.** Where a HawaiiMesh or public feed exists
  (`msh/US/HI/#`, `corescope.hawaiimesh.net`), subscribe through a local-broker bridge in the `in`
  direction only. Treat it as an enrichment feed with a tri-state status (ok / unobservable / error),
  never as a liveness source. Following `honest_failure_modes` #2, if the feed is dark, do not report
  "no HawaiiNet traffic".
- **Pattern D: contribute rather than rebuild.** For MeshCore, feed `corescope.hawaiimesh.net` as an
  observer, as the HI-OBS item proposes. This is outbound-only, so their outage only stops MeshForge's
  contribution.
- **Avoid:** MQTT downlink of HawaiiNet into fleet RF, or of fleet traffic into HawaiiNet. Also avoid
  ROUTER role on the HawaiiNet radio unless HawaiiMesh asks for it, and any critical path that runs
  through `mqtt.meshtastic.org`, `gt.wildc.net` or HawaiiMesh hosts.
- **If hawaiimesh.net infra goes down, under patterns A–D:**
  - Lost: the CoreScope view, any remote subscription feed, and the channel-plan reference page.
  - Not lost: MOC RF on its own slot, local brokers, RNS hub routing, and RF-level reception of HawaiiNet
    packets. LoRa does not need their servers.
- **Under the current fleet state**, an outage of the public broker or `gt.wildc.net` silently drops
  uplinks from kiai and moc4. These need a "broker unreachable" witness, not an absent-data-equals-quiet
  reading.

### Gaps
- HawaiiNet's actual preset and slot: needed to confirm that Pattern A truly separates RF. Unknown.
- Whether HawaiiMesh objects to its channel being uplinked to public brokers: unknown.

## 5. HawaiiMesh MeshCore presence and channel conventions

### Takeaway
There is a Hawaii MeshCore presence. A community CoreScope instance runs at `corescope.hawaiimesh.net`
(internal record), and a regionmesh.com "MeshCore Hawaii" page exists. MeshCore channel conventions
(frequency, spreading factor, bandwidth, public channel, IATA region code) were **not obtained**.

### Cited Findings
- "MeshCore Hawaii is growing island by island, and progress varies based on local participation and
  placement conditions". The page also notes it is not a 911 replacement. It is a third-party aggregator
  page; the fetch failed, so this is a snippet only. [MeshCore Hawaii – regionmesh.com](https://www.regionmesh.com/mesh-network-hawaii/)
- `[internal record]` The `corescope.hawaiimesh.net` instance takes MQTT on 1884 "per their wiki".
  [/home/user/meshforge/.claude/research/landscape_2026_11_04.md](file:///home/user/meshforge/.claude/research/landscape_2026_11_04.md)
- CoreScope ingest is per-IATA filtered (`meshcore/{IATA}/{PUBKEY}/packets`).
  [/home/user/meshforge/.claude/research/landscape_2026_11_04.md](file:///home/user/meshforge/.claude/research/landscape_2026_11_04.md); [Kpa-clawbot/CoreScope](https://github.com/Kpa-clawbot/CoreScope)
- MeshCore relies on dedicated repeaters with up to 64 hops, and it cannot talk to Meshtastic even on
  the same hardware. [NodakMesh – protocols](https://nodakmesh.org/protocols)

### Inferences
- The Hawaii IATA code is likely `HNL` (or per-island codes), but this is unconfirmed. Do not hardcode it
  until it is read from the wiki.

### Gaps
- The `/networks/meshcore` page was not attempted separately because the whole host is blocked. The
  MeshCore radio parameters, public channel and repeater naming are unknown.
