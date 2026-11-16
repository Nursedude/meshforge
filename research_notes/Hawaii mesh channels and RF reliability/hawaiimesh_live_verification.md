# hawaiimesh.net live verification (2026-11-16) + diff vs 2026-10-28 prior + firmware 2.8 preset conflict

Method note: all hawaiimesh.net pages were fetched with `curl` on 2026-11-16 (about 20:35–20:45 UTC) and returned HTTP 200. The site runs **Wiki.js 3** (Vue SPA under `/_site`, `/_assets`). It serves a `#wiki-prerender` block holding the page body as static HTML, and all quotes below come from that block. `POST /graphql` returns **404** because Wiki.js 3 has no v2 GraphQL. `/_api/sites` returns 401 and `/sitemap.xml` returns 404, so the nav tree could not be listed. Guessed paths `/about`, `/emergency`, `/rules`, `/mqtt`, `/bridges`, `/faq`, `/community`, `/resources`, `/networks/reticulum` all return the wiki's 404. Those were **guesses, not links taken from the nav**. CoreScope JSON was fetched at server time ≈ 2026-11-16T20:40Z. meshtastic.org could **not** be reached (`ENOTFOUND`, outside the session allowlist), so the 2.8 question was settled from GitHub release pages and firmware source. Credentials are not recorded anywhere in these notes.

## 1. What hawaiimesh.net says today: channels, presets, MQTT, node counts, and every emergency/off-grid claim

### Takeaway
The live wiki matches the 2026-10-28 snapshot almost line for line:
- The same 4 Meshtastic channels, all on `AQ==`.
- The same 4 MeshCore channels.
- The same LONG_FAST and MeshCore 910.525/62.5/SF7/CR5 settings.
- The same "Network Size (2026-10)" counts of 130 and 65.
- The same "OK to MQTT" advice.

The only resilience claims are the tagline "off-grid network for everyone", the MQTT-bridging note, and "#alert … emergency alerts". The site has no emergency-use disclaimer, no named Meshtastic broker, and no LongTurbo warning.

### Cited Findings
- **Meshtastic channels**, verbatim table under the heading "Meshtastic channels currently in use (self-reported)". Each row gives Channel Name | Key | Notes:
  - `LongFast | AQ== | bridged with neighbor islands`
  - `HawaiiNet | AQ== | Bot interaction with "wx" run by WH6GXZ`
  - `hawaiimesh | AQ== | Bridge with Meshcore run by pmow`
  - `alerts | AQ== | Bridge with Meshcore run by WH6EPS`

  Source: [Meshtastic Channels](https://www.hawaiimesh.net/networks/meshtastic/meshtastic-channels)
- The same page has a 5-step "Guide on how to add these channels to your node" ("Enter the name and key from the above table … Hit Save, and then Send"). It also has a **new-looking live widget, "Mesh Chat Activity"**, with tiles for "Active channels / Messages / Senders / Last message", a "Messages per hour" sparkline, and a per-channel table. The widget is filled by JavaScript. Its data source was not found in the page HTML; the `cs-` class prefix suggests CoreScope. — [Meshtastic Channels](https://www.hawaiimesh.net/networks/meshtastic/meshtastic-channels)
- **Meshtastic radio setting**, verbatim: "Region: United States / Preset: LONG_FAST". The Note reads: "At this time, Hawiai makes use of MQTT bridging to ensure packets arrive. This can help messages deliver, especially to neighbor island links. Because of this, it's suggested to disable "Ignore MQTT" and enabled "OK to MQTT" on this screen." The typos "Hawiai" and "enabled" are in the source. The page does not mention LongTurbo, firmware 2.8, or the preset mismatch. — [Meshtastic Radio Setting](https://hawaiimesh.net/networks/meshtastic/meshtastic-radio-setting)
- **MeshCore channels**, verbatim rows (Channel Name | Key | notes):
  - `Public | n/a | (blank)`
  - `#testing | n/a, derived | test messages and responses`
  - `#hawaiimesh | n/a, derived | bridge testing (pmow)`
  - `#alert | n/a, derived | emergency alerts (Delta-EDC)`

  Source: [MeshCore Channels](https://hawaiimesh.net/networks/meshcore/meshcore-channels)
- **MeshCore radio setting**, verbatim: "Region: United States / Frequency: 910.525 / Bandwidth: 62.5 kHz / Spread Factor: 7 / Coding Rate: 5". The Note reads: "Some operators report improved connections using a coding rate of 8." — [MeshCore Radio Setting](https://hawaiimesh.net/networks/meshcore/meshcore-radio-setting)
- **Getting Started**, verbatim in full: "Buy a radio. See recommended hardware. / Flash software / We are using default radio settings for MT/MC. / ??? / Win". The phrase "default radio settings for MT/MC" conflicts with the radio-setting pages, which prescribe values that are not defaults for new 2.8 US nodes set up on-device (see Q4). — [Getting Started](https://hawaiimesh.net/getting-started)
- **Home page** (`/landing-page/home`):
  - The meta description and og:description read "Hawaii's off-grid network for everyone". The tagline is "Powered by Aloha". Footnote 1 reads "Accuracy is not guaranteed, implied, or, in some cases, attempted."
  - The page has tabbed Pros/Cons for **Meshtastic, MeshCore and Reticulum**. For Reticulum: "Supports multiple simultaneously: Lora, Internet, packet radio, etc."; "Crowdsourced nodes can deliver your messages natively". Its cons are "Not many nodes", "Software can be difficult to work with", and "No default channel for "village square" chat".
  - The "Leading Network Comparison" table gives Meshtastic: Max Hops 7, Maturity 2023, flood, automatic adverts, GPS ✅, **Network Size (2026-10) 130**. MeshCore: 64 hops, 2025, learned-path, manual, GPS ✅, **65**.
  - Footnote 2 reads "Reticulum supports voice calls, images, and small videos. This statement is true for Lora meshing as these types of data packets are often better over faster links."

  Source: [home](https://hawaiimesh.net/landing-page/home)
- **Recommended Hardware** shows only category headings: "Card-style ultra-portable, Personal Companion, Personal Keyboard Companion, Base Station Repeater, Repeater, Solar Repeater". No device models appear in the prerender. Vendors listed are Rokland (FL), Aliexpress, and Seeed Studio, plus a link to "Recommended Antennas". The model lists may sit in JS-rendered tabs, which was not checked. — [Recommended Hardware](https://hawaiimesh.net/recommended-hardware)
- **Every emergency, off-grid, EmComm or resilience phrase found** in the 8 fetched pages:
  - "Hawaii's off-grid network for everyone" (meta description on every page)
  - "emergency alerts (Delta-EDC)" (MeshCore #alert)
  - "MQTT bridging to ensure packets arrive … especially to neighbor island links" (Meshtastic radio setting)

  The words "EmComm", "disaster", "resilien*", and "disclaimer" do not appear in any fetched page. The only hedge is the accuracy footnote. — pages above

### Inferences
- "Off-grid" in the tagline sits beside an explicit instruction to depend on internet MQTT for inter-island delivery. The internal tension the prior analysis flagged still stands, in the site's own words.
- The site prescribes LONG_FAST and says "default radio settings". A newcomer on 2.8 who picks US on the device screen lands on LongTurbo (see Q4), and the wiki gives no warning about this.

### Gaps
- The full nav tree, and whether About, Rules, Emergency or MQTT pages exist under other paths. The Wiki.js 3 API needs auth and the guessed paths returned 404.
- The data source of the "Mesh Chat Activity" widget, which is loaded by JS and not traced.

## 2. Diff vs the 2026-10-28 prior analysis

### Takeaway
On the pages the prior covered, nothing material has changed. Channels, preset, MQTT note, counts, and the "???" in Getting Started are identical. The additions are a live chat-activity widget, a Reticulum tab on the home page, and Recommended Hardware headings. The tab and the headings may simply be things the prior did not mention. The real news is in CoreScope's live data, which the wiki has not caught up with (see Q3).

### Cited Findings
- Meshtastic channel table: **unchanged**. The same 4 rows and operators (WH6GXZ, pmow, WH6EPS), all `AQ==`. — [Meshtastic Channels](https://www.hawaiimesh.net/networks/meshtastic/meshtastic-channels)
- MeshCore channel table: **unchanged**. Public, #testing, #hawaiimesh (pmow), #alert (Delta-EDC). — [MeshCore Channels](https://hawaiimesh.net/networks/meshcore/meshcore-channels)
- Preset guidance: **unchanged**. It is still LONG_FAST, and **no LongTurbo note has been added**. — [Meshtastic Radio Setting](https://hawaiimesh.net/networks/meshtastic/meshtastic-radio-setting)
- MeshCore settings: **unchanged**, including the CR8 note. — [MeshCore Radio Setting](https://hawaiimesh.net/networks/meshcore/meshcore-radio-setting)
- Node counts: **unchanged** at 130 / 65 and still labelled "(2026-10)". — [home](https://hawaiimesh.net/landing-page/home)
- Disclaimer: **none added**. There is only the accuracy footnote, as before. — [home](https://hawaiimesh.net/landing-page/home)
- Getting Started: **unchanged**, still "??? / Win". — [Getting Started](https://hawaiimesh.net/getting-started)
- **New or not in the prior:**
  - A "Mesh Chat Activity" widget on the Meshtastic channels page.
  - Reticulum Pros/Cons on the home page. The prior's plan said "don't introduce RNS", but the community already lists Reticulum itself.
  - Recommended Hardware now has category headings and a vendor list. The prior called it "empty"; it is still empty of models in the prerender.

  Sources: pages above
- **Live data vs the wiki (MeshCore):**
  - CoreScope counts **72 MeshCore nodes** against the wiki's 65.
  - CoreScope's channel list includes channels **absent from the wiki table**: `#wardriving` (1252 msgs), `#test`, `#emergency` (1 msg), `#wx`, `#public`, `#bot`, `#420`.
  - Two observers run **CR8** rather than the wiki's CR5.

  Sources: [CoreScope /api/stats](https://corescope.hawaiimesh.net/api/stats); [CoreScope /api/channels](https://corescope.hawaiimesh.net/api/channels); [CoreScope /api/observers](https://corescope.hawaiimesh.net/api/observers)

### Inferences
- The wiki is not being maintained as a live registry. The "self-reported" tables lag what is actually on air. An `#emergency` channel already exists on MeshCore, separate from the documented `#alert`.

### Gaps
- No revision history was available (Wiki.js 3 history needs auth). Whether the Reticulum tab or the hardware headings were added after 2026-10-28 is UNKNOWN.

## 3. Meshtastic MQTT broker, and CoreScope live numbers (including the MeshAnchor / "rs1" question)

### Takeaway
- **No Meshtastic broker or topic is named anywhere on the wiki.** The radio-setting page says only to enable OK-to-MQTT and disable Ignore-MQTT. This implies the default public broker, but that is not stated.
- The only named broker is the **MeshCore observer feed**: `corescope.hawaiimesh.net`, port 1884, with one shared username and password printed on the page.
- At about 20:40Z on 2026-11-16, CoreScope showed **8 observers, 72 nodes (46 repeaters / 20 companions / 6 rooms), 447 packets in the last hour, and 8,354 in the last 24 h**.
- **No observer named "rs1" or MeshAnchor exists.** A *repeater* named **"meshanchor R1"** is in the node list, and a sender **"meshanchor p4"** posted on `#alert`.

### Cited Findings
- **Observer setup**, verbatim steps: "set mqtt1.preset custom / set mqtt1.server corescope.hawaiimesh.net / set mqtt1.port 1884 / set mqtt1.username <redacted, published on page> / set mqtt1.password <redacted, published on page>".
  - The page also requires "set mqtt.iata use the nearby IATA airport code (required)" and "set mqtt.interval 10".
  - Firmware comes from observer.gessaman.com: "Flash either a repeater firmware, or a room server firmware". For a silent room server, "set both intervals to 0".
  - No TLS is mentioned.

  Source: [run a corescope observer](https://hawaiimesh.net/networks/meshcore/run-a-corescope-observer)
- The Meshtastic radio-setting page gives **no broker host, topic root, or credentials** for the "MQTT bridging". — [Meshtastic Radio Setting](https://hawaiimesh.net/networks/meshtastic/meshtastic-radio-setting)
- **CoreScope `/api/stats`** (fetched ≈20:40Z 2026-11-16): `totalNodes 72`, `totalObservers 8`, `packetsLastHour 447`, `packetsLast24h 8354`, `totalPackets 16589`, `totalObservations 53075`, `counts {repeaters 46, rooms 6, companions 20, sensors 0}`, `signatureDrops 9`. Engine: Go, `v3.17.0` (build 2026-11-10). — [CoreScope /api/stats](https://corescope.hawaiimesh.net/api/stats)
- **CoreScope `/api/health`**: `status ok`, uptime "60h 58m" (restarted about 2026-11-14 07:40Z), 4 websocket clients. — [CoreScope /api/health](https://corescope.hawaiimesh.net/api/health)
- **The 8 observers** (name, IATA, hardware, radio, packets in the last hour, first seen):

  | Observer | IATA | Hardware | Radio | Last hour | First seen |
  |---|---|---|---|---|---|
  | Maui Corescope Observer | OGG | T-Beam | CR5 | 106 | 2026-09-30 |
  | Delta-Obs | HNL | Heltec V3 | CR5 | 95 | 2026-09-30 |
  | HawaiiMesh.Net TCP | OGG | Heltec V3 | **CR8** | 87 | 2026-09-23 |
  | KH7BR Observer | ITO | ThinkNode M7 | CR5 | 71 | 2026-10-09 |
  | Makani Observer | ITO | Heltec V4.3 | CR5 | 58 | 2026-10-31 |
  | MQTT Observer ECG4 | NGF | Heltec V3 | CR5 | 16 | 2026-11-10 |
  | MQTT Observer ECG6 | NGF | Heltec V3 | CR5 | 14 | 2026-11-08 |
  | Waikoloa Solar | MUE | Heltec V4 | **CR8** | **0** | 2026-09-01 |

  - HawaiiMesh.Net TCP runs `meshcore-packet-capture`.
  - Waikoloa Solar was last seen at 16:45Z, about 4 h stale at fetch time.
  - Islands covered: Oahu (HNL, NGF), Maui (OGG), Hawaii Island (ITO, MUE). No Kauai/LIH observer.

  Source: [CoreScope /api/observers](https://corescope.hawaiimesh.net/api/observers)
- **"meshanchor R1"** in `/api/nodes`:
  - role `repeater`, `first_seen 2026-09-01T01:57:48Z`, `last_seen 2026-11-16T19:43:43Z`
  - `relay_count_24h 50`, `foreign: true`, `region_placement ITO (fewest_hops, 1 hop)`
  - `usefulness_grade "D"` (score 0.25), `coverage_score 0.60`, `scope_config_state "none"`

  Source: [CoreScope /api/nodes](https://corescope.hawaiimesh.net/api/nodes). Only the first page of 50 was read (`has_more: true`, total 72), so a node named rs1 on a later page is not ruled out. The **observer** list is complete (8) and has no rs1/MeshAnchor entry.
- **CoreScope `/api/channels`**, message counts: Public 982, #wardriving 1252, #testing 388, #test 55, #alert 18, #wx 7, #emergency 1, #public 1, #bot 1, #420 1. The last `#alert` sender was "meshanchor p4" on 2026-11-09. — [CoreScope /api/channels](https://corescope.hawaiimesh.net/api/channels)
- **CoreScope `/api/scope-audit`** (24 h window): the listed repeaters all show `configState "no-scopes"` with `declaredWildcard true`, meaning no region scoping is configured. — [CoreScope /api/scope-audit](https://corescope.hawaiimesh.net/api/scope-audit)

### Inferences
- The operator's MeshAnchor appears on CoreScope as a **relaying repeater, "meshanchor R1"**, heard from the ITO/Hawaii Island side, and not as an observer. "rs1" may be a local hostname that differs from the advert name. This is BELIEVED, not verified; checking the advert name on the box would confirm it. `foreign: true` with grade D suggests CoreScope thinks it is out of region or of low value. This is worth checking before any engagement.
- The hourly rate is about 447 packets across 8 observers, roughly 7/min mesh-wide after dedupe. This shows a small but real live MeshCore network. One observer was dark and two observers are off-spec on CR, which reinforces the "individuals, not infrastructure" reading.
- The observer feed is plain MQTT on 1884 with a shared published credential and no TLS mentioned. The spoofing concern from the prior analysis still stands.

### Gaps
- The Meshtastic-side broker, topic root, and who runs the Meshtastic inter-island MQTT bridge are **not named anywhere on the site**.
- `/api/nodes` pages 2+ were not fetched (offset pagination not explored).

## 4. Firmware 2.8: default preset conflict, default-key restrictions, traffic management, MQTT

### Takeaway
**Both prior claims are partly right. The precise answer:**
- 2.8's compiled default preset is still **LONG_FAST**. The landscape note's source check is correct.
- 2.8 adds a rule: on the **first region selection made on the device screen**, choosing **US** switches the preset to **LONG_TURBO**. The release notes say "LongTurbo and LongFast can't hear each other."
- All 2.8 builds are **Pre-release/Alpha**. The current one is 2.8.2.d5f1619, dated 04 Nov.

So "Meshtastic 2.8 defaults new US nodes to LongTurbo" is accurate only for **alpha firmware, set up on-device with a screen**. It is overstated as a blanket claim. "Refuted at source" is also overstated: NodeDB is not the only place the preset is set.

### Cited Findings
- 2.8.1 release notes, verbatim:
  - "New US nodes now default to LongTurbo, not LongFast."
  - "Only applies to first-time region selection on the device screen."
  - "LongTurbo and LongFast can't hear each other."
  - "You will need to switch the preset back to LongFast to join an existing mesh on the prior default."

  The release is marked "Pre-release" with "Alpha" in the title, dated "01 Oct". — [firmware v2.8.1.8e6a88d](https://github.com/meshtastic/firmware/releases/tag/v2.8.1.8e6a88d) (read via WebFetch summarizer; the wording was quoted twice consistently)
- Releases page: 2.8.0.7239fe8 (30 Aug, revoked); 2.8.0.47db0e3 (01 Sep, Alpha, revoked); 2.8.1.8e6a88d (01 Oct, Alpha); 2.8.2.4c89b69 (02 Nov, revoked); 2.8.2.d5f1619 (04 Nov, Alpha). **None is stable.** — [firmware releases](https://github.com/meshtastic/firmware/releases). The repo's landscape note records stable as 2.7.26: [landscape_2026_11_09.md:50](file:///home/user/meshforge/.claude/research/landscape_2026_11_09.md)
- `src/mesh/NodeDB.cpp` at `v2.8.2.d5f1619` contains `config.lora.modem_preset = meshtastic_Config_LoRaConfig_ModemPreset_LONG_FAST;` unless `USERPREFS_LORACONFIG_MODEM_PRESET` is defined. Region defaults to `RegionCode_UNSET`. — [NodeDB.cpp @ v2.8.2.d5f1619](https://raw.githubusercontent.com/meshtastic/firmware/v2.8.2.d5f1619/src/mesh/NodeDB.cpp)
- `src/graphics/draw/MenuHandler.cpp` at `v2.8.2.d5f1619`, `menuHandler::presetForRegionSelection`: `if (lora.region == …RegionCode_UNSET && selected == …RegionCode_US && lora.use_preset && lora.modem_preset == …LONG_FAST) { return …LONG_TURBO; }`. This is called from `applyLoraRegion` via the `LoraRegionPicker` screen menu and logs "First region is %s, default preset to %s". — [MenuHandler.cpp @ v2.8.2.d5f1619](https://raw.githubusercontent.com/meshtastic/firmware/v2.8.2.d5f1619/src/graphics/draw/MenuHandler.cpp) (read via WebFetch summarizer)
- Other 2.8.0/2.8.1 notes, verbatim:
  - "Completely new allocation of LoRa Regions and presets, including ham specific carve-outs"
  - "TinyFast and TinySlow modem presets added to config and menu"
  - "**Precise position no longer allowed on known-keys (public mesh. Please use private channels for this)**"
  - "**Traffic Management Module for packet forwarding — dedup, rate limiting, role-aware policing**"

  Source: [firmware v2.8.1.8e6a88d](https://github.com/meshtastic/firmware/releases/tag/v2.8.1.8e6a88d)
- MQTT note in 2.8.2, verbatim: "MQTT gateways now uplink PKI direct messages they relay but cannot decrypt." — [firmware releases](https://github.com/meshtastic/firmware/releases) (WebFetch summary of the releases list)
- The meshtastic.org blog "Let's talk about 2.8" could **not be fetched** (`ENOTFOUND meshtastic.org`, session allowlist). — attempted [blog](https://meshtastic.org/blog/lets-talk-about-2-8/)

### Inferences
- **Hawaii impact:** all 4 hawaiimesh Meshtastic channels use the known key `AQ==`. On 2.8 nodes, precise position is no longer allowed on them, which matters for any SAR/EmComm use of position. A 2.8-alpha node with a screen, set up fresh with region US, will land on LongTurbo and be **deaf to the LONG_FAST mesh the wiki prescribes**, and the wiki does not warn about this. Exposure is limited while 2.8 stays alpha, so impact grows when it ships stable.
- Phone-app and CLI region setting go through the admin path, not `MenuHandler`. From the release note ("device screen") I believe these do *not* trigger LongTurbo. This is BELIEVED, not verified; `AdminModule.cpp` at the same tag is the check.
- The landscape note should be amended. It is correct that NodeDB's default is LONG_FAST and that 2.8 is pre-release. It is wrong to call the LongTurbo default "refuted", because the on-device first-region path does it.

### Gaps
- The meshtastic.org blog text (host blocked). The exact wording of the prior's citation could not be verified against it.
- Exact release years are not shown on the GitHub page; 2026 is BELIEVED.
- Whether the Traffic Management Module is on by default and its rate limits were not read in source.
- `AdminModule` region-change behaviour (phone/CLI path) was not verified.


## Operator confirmation (2026-11-16)
Operator (WH6GXZ) confirmed in chat: "rs1" IS the CoreScope repeater "meshanchor R1" (Hilo region, first seen 2026-09-01, flagged foreign:true, usefulness grade D, 50 relays/24h at fetch). The fleet is therefore already a contributing MeshCore repeater on the HawaiiMesh MeshCore network, not only an observer-candidate. Note it is a REPEATER, which updates the 2026-10-31 Cascadia study statement "we run no MeshCore repeaters".

## Why meshanchor R1 is "foreign" and grade D (coordinator, CoreScope /api/nodes + served frontend JS, 2026-11-16 ~20:50Z)
- CoreScope lists TWO fleet nodes at the same spot (19.4355,-155.2137, ITO region): "meshanchor R1" role=repeater, and "meshanchor rs1" role=room (room server, grade F, 0 relays/24h, foreign:false, last heard 16:12Z). Operator said rs1 = R1; the analyzer treats them as separate keys/roles.
- foreign: CoreScope map.js FOREIGN_ADVERT_DESCRIPTION verbatim: "An advert from this node was flagged outside the configured area at ingestion. This historical flag does not indicate its current location." It is sticky/historical. Every foreign:true node seen had first_seen 2026-08-29..2026-09-27 (instance's first month); none first seen after that is foreign. BELIEVED: area/geofence config changed after early ingest, or early adverts carried absent/odd coords. Not a fault in R1 today.
- Grade D: usefulness_score 0.251 (composite, #672: traffic share, bridge, redundancy/coverage axes). R1 axes: traffic_share 0.0139 (1.4%, "Redundant" band <0.1), bridge 0.030 ("Marginal", >0 <0.05), coverage 0.657, redundancy 0.26; 50 relays/24h, all unscoped; scope_config_state none. Letter-grade cutoffs are server-side, not in served JS (UNKNOWN exact thresholds).
- Context: graded nodes on instance ≈ A1, B2, C12, D24, F12 — D is the modal grade. Grade measures current traffic through it and graph centrality from 8 observers, not RF quality.
