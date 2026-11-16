# Emerging application/tooling ecosystem around community LoRa meshes (Meshtastic, MeshCore, Reticulum), as of Nov 2026

> Research method note (calibrated claims): web search + WebFetch on 2026-11-16. Two collection
> blind spots, each listed separately:
> (1) `cascadiamesh.org` and a Reddit mirror (`r.datuan.dev`) did not resolve in this sandbox
> (outside the network allowlist), so CascadiaMesh facts come from **search-result snippets of its own
> pages**, not full-page reads.
> (2) The GitHub REST API (`gh api`) was refused for repos outside this session, so star counts come from
> a WebFetch of each repo's HTML page as of 2026-11-16. Last-commit dates were mostly NOT visible.
> Every row the report writer uses should keep its "as fetched 2026-11-16" qualifier.

## Landscape table (summary; details and sources in sections below)

| Project | What it is | Protocol(s) | Architecture | Maturity (as fetched 2026-11-16) | Licence | Relevance to MeshForge |
|---|---|---|---|---|---|---|
| **CoreScope** (Kpa-clawbot/CoreScope) | Self-hosted MeshCore packet analyzer / observer network | MeshCore | Observer radios (packet-logging firmware) → USB → `meshcoretomqtt` → MQTT `meshcore/{IATA}/{PUBKEY}/packets` → Go ingestor → SQLite → Go HTTP + WebSocket → vanilla JS | 211★, 79 forks, 3,162 commits, 50 open issues; releases v3.5.0 and v3.10.0 seen on newreleases.io | GPL-3.0-or-later | Multi-observer packet tracing, observer-health monitoring, hash-collision matrix, IATA-scoped federation via multiple brokers. No explicit channel-utilization metric found |
| **MeshMonitor** (Yeraze/meshmonitor) | Multi-source mesh monitoring dashboard | Meshtastic + MeshCore + MQTT | Node/TS (Express 5) + React 19; SQLite/Postgres/MySQL; optional embedded MQTT broker with bidirectional bridges | 731★, 114 forks; v4.17.0 release seen | BSD-3-Clause | **Closest analog to a multi-protocol MOC.** Has an *airtime-utilization cutoff that pauses bot traffic when the mesh is busy* (congestion-aware automation), plus geo bounding-box purge of MQTT junk |
| **PotatoMesh** (l5yth/potato-mesh) | Federated community node dashboard | Meshtastic, MeshCore, Reticulum (announce-only, experimental) | Ruby/Sinatra + SQLite web app; Python ingestors talk to radios (serial/TCP/BLE/LAN multicast) and POST to the app; **instances federate by announcing and crawling peers**; no MQTT | 558★, 67 forks, 785 commits; ~v1.0 | Apache-2.0 | **Decoupled-federation model without MQTT**, Prometheus `/metrics`, `PRIVATE=1` mode, retention limits |
| **Meshview** (pablorevilla-meshtastic/meshview, fork of armooo/meshview) | MQTT-fed Meshtastic web analyzer | Meshtastic | MQTT → DB writer → SQLite/Postgres → web UI + HTTP API | 150★, 61 forks, 939 commits; v4.0.0 listed as Nov 2026 (dates internally inconsistent) | COPYING file present; licence name not read | Packet pages show `channel_utilization` device metric; "top" routers/senders; reliability page; predicted coverage |
| **Malla** (zenitraM/malla) | MQTT-fed Meshtastic web analyzer ("AI-built") | Meshtastic | MQTT → SQLite → web UI (two processes sharing one DB) | 132★, 29 forks, 131 commits; no releases | MIT | Top-talkers, gateway-compare matrix, hop distribution; powers a Florida telemetry dashboard |
| **CascadiaMesh** (cascadiamesh.org) | Pacific NW community **MeshCore** network + website | MeshCore | RF-native repeaters; region-scoped flooding; city bots on hashtag channels | ~2,876–3,886 live nodes shown across its pages | n/a (community org) | Published operator rules: flood-advert ≥48 h, zero-hop advert 0, per-city region tag chains, firmware ≥1.15.0 |
| **meshcoretomqtt** | Bridge: MeshCore observer → MQTT | MeshCore | USB-attached observer → MQTT publish | not measured | not read | The ingest leg CoreScope depends on |

---

## CascadiaNet / Cascadia Mesh (Pacific Northwest): identity, channel plan/presets, governance, tools, congestion data

### Takeaway
The name resolves to **CascadiaMesh (cascadiamesh.org)**, a community-built **MeshCore** (not Meshtastic) network spanning British Columbia, Western Washington and Western Oregon, reportedly expanding into Eastern Washington and North Idaho. It uses the stock "USA/Canada (Recommended)" MeshCore preset and manages congestion through operator discipline (long flood-advert intervals, zero-hop adverts off) and MeshCore **region scoping** with per-city tag chains. I found no source for "CascadiaNet" as a distinct entity, and no published congestion/airtime dataset.

### Cited Findings
- The CascadiaMesh homepage describes it as "a community-built, MeshCore based, mesh communication network that spans the Pacific Northwest"; the About page lists coverage as British Columbia, Western Washington and Western Oregon; it is decentralized with "no central server or tower" — [CascadiaMesh Home](https://cascadiamesh.org/), [About](https://cascadiamesh.org/about/) (search snippet; full page not fetchable from this sandbox)
- Live node counts shown on its pages ranged from ~2,876 to ~3,886 (different pages captured at different times) — [CascadiaMesh](https://cascadiamesh.org/) (snippet)
- Onboarding tells users to select the **"USA/Canada (Recommended)"** radio preset in the MeshCore app — [Getting Started on the Mesh](https://cascadiamesh.org/getting-on-the-mesh/) (snippet)
- Repeater guidance: "Zero Hop advert interval should be set to 0" and "Flood Advert Interval should be set to at LEAST 48 hrs" — [CascadiaMesh (repeater/region pages)](https://cascadiamesh.org/regions/) (snippet)
- Regions page recommends firmware **1.15.0+** ("on 1.15.0+, each region put enables flooding for that region automatically"), gives per-city region tag chains, and a default-region command example `region default sea`; it notes the default region does not affect what traffic you forward — [Regions](https://cascadiamesh.org/regions/) (snippet)
- Channels page lists public hashtag channels, mostly city-specific testing bots plus general topics; a `#r2-name` convention uses the same key as `#name` but lives on "radio2" only — [Channels](https://cascadiamesh.org/channels/), [Regions](https://cascadiamesh.org/regions/) (snippets)
- Other site content: Bot Commands, "The 99/91 Project", DIY and commercial repeater builds, MediaMesh Flasher / Wadamesh-TAK / MeshCore Open TAK (doc dated Oct 19, 2026), offering "250 kHz · fast (default)" or "125 kHz · range" MediaMesh presets — [MediaMesh Flasher](https://cascadiamesh.org/mediamesh-flasher/), [What's New PDF](https://cascadiamesh.org/wp-content/uploads/2026/10/MeshCore-Open-TAK-and-Wadamesh-tak-Whats-New.pdf), [Bot Commands](https://cascadiamesh.org/bot-commands/), [The 99/91 Project](https://cascadiamesh.org/the-99-91-project/) (snippets)
- Events: e.g. a Decentralized Tech Meetup in Olympia, WA — [Event](https://cascadiamesh.org/event/decentralized-tech-meetup-olympia-wa/) (snippet)
- An r/meshcore post (41 upvotes; date unclear) reports Cascadia Mesh coverage now incorporates Eastern Washington and North Idaho — seen only via a Reddit aggregator in search results ([r.datuan.dev/r/meshcore/top](https://r.datuan.dev/r/meshcore/top)); **weak source, unverified**
- Distinct but overlapping: **PugetMesh** runs MeshCore repeaters in western Washington; one blogger reports seeing 400+ nodes — [Segment7 Blog, 2026-01-11](https://blog.segment7.net/2026-01-11-meshcore-and-meshtastic/). A crowd-sourced wardrive coverage map tracks the Puget MeshCore mesh — [mesh-map howto](https://mesh-map.pages.dev/howto)
- **Vashon Mesh** (Vashon Island, WA) recommends MeshCore for urban/suburban community networks and Meshtastic for backcountry — [Vashon Mesh: Mesh Technologies](https://vashonmesh-github-io.pages.dev/mesh-tech/)
- Ripple Radios (MeshCore's author) lists the Pacific North-West among fast-growing MeshCore regions — [MeshCore Update #6](https://buymeacoffee.com/ripplebiz/meshcore-update-6)

### Inferences
- "CascadiaNet" in the brief most likely refers to CascadiaMesh. I found no evidence of a separate project by that name. Treat the mapping as **likely but not confirmed**.
- CascadiaMesh's congestion management is administrative and protocol-level: long flood-advert intervals plus region-scoped floods. It does not appear to rely on a measurement dashboard. For a Hawaii-scale mesh, the transferable lesson is "cap advert airtime by policy and scope floods geographically," which a MOC can then *audit* (e.g., detect repeaters advertising more often than 48 h).
- How CascadiaMesh relates to PugetMesh is unclear: it may be an umbrella over PugetMesh, a peer, or a rebrand. The sources do not settle it.

### Gaps
- I could not read the governance structure (board, nonprofit status, admins), the exact preset parameters (freq/BW/SF/CR behind "USA/Canada (Recommended)"), or whether CascadiaMesh runs CoreScope or an analyzer, because `cascadiamesh.org` was not fetchable here. That domain needs to be on the session allowlist to close this.
- I found no published CascadiaMesh congestion, airtime or channel-utilization data.
- Name collisions: "Cascadia Rising" (the 2016 FEMA Cascadia earthquake exercise) did not surface in any mesh-tooling result, and nothing here links it to the mesh. I did not search further.

---

## CoreScope: what it is, who builds it, data collection, what it shows

### Takeaway
CoreScope is a self-hosted, GPL-3.0 **MeshCore packet analyzer** fed by a network of USB-attached "observer" radios that publish raw packets over MQTT, keyed by IATA airport code and observer pubkey. It is maintained under the GitHub account **Kpa-clawbot**, and no individual author is named. It is very active (3,162 commits) and deployed by regional MeshCore communities (MeshCore.ca, ScotMesh).

### Cited Findings
- Self-description: "Self-hosted, open-source MeshCore packet analyzer"; owner account Kpa-clawbot; 211★, 79 forks, 3,162 commits, 50 open issues, 13 open PRs (fetched 2026-11-16) — [Kpa-clawbot/CoreScope](https://github.com/Kpa-clawbot/CoreScope)
- Data path: observer node runs firmware with a packet-logging build flag → USB → host running `meshcoretomqtt` → MQTT topic `meshcore/{IATA}/{PUBKEY}/packets`. The server can subscribe to **multiple external brokers with per-source IATA filtering**. Raw hex packets can also be POSTed to an API endpoint — [Kpa-clawbot/CoreScope](https://github.com/Kpa-clawbot/CoreScope), [jjkroell fork README](https://worker-github.zxx930427.workers.dev/jjkroell/CoreScope/blob/master/README.md)
- Architecture: Go ingestor decodes and writes to SQLite; Go HTTP server holds an in-memory packet store and pushes over WebSocket; vanilla JS frontend; prebuilt Docker image on GHCR, optional Caddy HTTPS — [Kpa-clawbot/CoreScope](https://github.com/Kpa-clawbot/CoreScope), [SaarMesh-Bot/CoreScope](https://github.com/SaarMesh-Bot/CoreScope)
- Features: live trace map with VCR-style replay; filterable packet feed with byte-level breakdown; per-node analytics (peer graph, hop counts, SNR); channel chat decryption (AES-128-ECB, hashtag key derivation); 17 analytics tabs (RF, topology, channels, route patterns); **packet tracing across observers; observer health monitoring; hash-collision matrix**; optional accounts and crowdsourced mobile RX coverage — [Kpa-clawbot/CoreScope](https://github.com/Kpa-clawbot/CoreScope)
- The fetched README does not explicitly name channel-utilization or airtime metrics — [Kpa-clawbot/CoreScope](https://github.com/Kpa-clawbot/CoreScope)
- Release v3.10.0 was flagged "high urgency" for map operators and adds a WebSocket origin allowlist; v3.5.0 also exists — [newreleases.io v3.10.0](https://newreleases.io/project/github/Kpa-clawbot/CoreScope/release/v3.10.0), [v3.5.0](https://newreleases.io/project/github/Kpa-clawbot/CoreScope/release/v3.5.0)
- Multiple forks exist (SaarMesh-Bot, Saarlandpower, jjkroell). MeshCore.ca runs a live CoreScope instance, and ScotMesh lists CoreScope among its tools — [SaarMesh-Bot/CoreScope](https://github.com/SaarMesh-Bot/CoreScope), [Saarlandpower/CoreScope](https://github.com/Saarlandpower/CoreScope), [MeshCore-ca](https://gh.r-mj.com/MeshCore-ca), [ScotMesh](https://scotmesh.mm7roq.compute.oarc.uk/)

### Inferences
- CoreScope's **multi-observer** design (the same packet heard by N observers) is the MeshCore equivalent of a distributed spectrum/airtime sensor. With packet timestamps plus a LoRa time-on-air formula, airtime per repeater could in principle be derived, but the README does not claim it.
- The `meshcore/{IATA}/{PUBKEY}/...` topic scheme is a de facto federation namespace. A MOC that wants to consume or contribute MeshCore observer data should speak this topic shape.
- The "hash collision matrix" reflects a MeshCore-specific failure mode: short path-hash prefixes collide as repeater counts grow. That would be a relevant health signal for any dense MeshCore deployment.

### Gaps
- Exact v3.10.0 release date, and whether Kpa-clawbot is an individual, a bot account or an org: not determinable from fetched pages.
- Whether CoreScope computes channel utilization / duty cycle: not stated. It would need a check of the code or a live instance.

---

## Other ecosystem projects (MeshCore, Meshtastic observability, Reticulum apps, regional meshes)

### Takeaway
Observability tools split into three architectures. (a) **MQTT-fed web analyzers** (Meshview, Malla, CoreScope) see only what gateways uplink. (b) **Direct-radio ingest with federation** (PotatoMesh) uses no MQTT; instances crawl each other. (c) **Multi-source operations dashboards** (MeshMonitor) combine radios plus MQTT with automation. MeshMonitor and PotatoMesh are the closest analogs to MeshForge's multi-protocol MOC positioning.

### Cited Findings
- **MeshMonitor**: Meshtastic (TCP/Serial/BLE) + MeshCore (USB/TCP) + MQTT in one deployment; per-source dashboards; unified telemetry page (15 min–7 day windows); optional embedded MQTT broker with bidirectional bridges and topic/channel/portnum/geo filters; bounding-box auto-purge of out-of-area nodes; **airtime-utilization cutoff that automatically pauses bot traffic when the mesh is busy**; Apprise/Web Push notifications; REST API; OIDC/RBAC; BSD-3-Clause; 731★/114 forks — [Yeraze/meshmonitor](https://github.com/Yeraze/meshmonitor). v4.17.0 added a Live Mesh Activity widget — [newreleases.io](https://newreleases.io/project/github/Yeraze/meshmonitor/release/v4.17.0). Also packaged for Unraid — [Unraid app](https://ca.unraid.net/apps/meshmonitor-0r4ey730h7f8ur)
- **PotatoMesh**: "A federated Meshtastic, Meshcore, and Reticulum node dashboard… No MQTT clutter, just local LoRa aether"; Python ingestors push to a Ruby/Sinatra + SQLite app; instances announce and crawl peers (federation can be disabled; `PRIVATE=1`); Prometheus `/metrics`; Reticulum support is announce-only/experimental; WIP Matrix bridge; Apache-2.0; 558★ — [l5yth/potato-mesh](https://github.com/l5yth/potato-mesh)
- **Meshview**: "watches a MQTT topic for meshtastic messages, imports them to a database and has a web UI"; SQLite/Postgres; top senders/routers stats with CSV; reliability page; predicted coverage via `pyitm`; v4.0.0 (page says Nov 2026, but dates conflict); 150★ — [pablorevilla-meshtastic/meshview](https://github.com/pablorevilla-meshtastic/meshview). Live instance packet pages show `channel_utilization` device metrics — [meshview.meshtastic.es/top](https://meshview.meshtastic.es/top)
- **Malla**: "a web analyzer for Meshtastic networks based on MQTT data", described as AI-built; dashboard, packet browser, traceroutes, network graph, gateway-compare matrix, top talkers, hop distribution; MIT; 132★ — [zenitraM/malla](https://github.com/zenitraM/malla). It powers the Florida Mesh telemetry site — [areyoumeshingwith.us, Jan 2026](https://areyoumeshingwith.us/blog/2026/01/florida-mesh-telemetry-is-now-live/)
- **Meshtastic Network Management Client** (official desktop client for analyzing large low-bandwidth meshes) exists — [meshtastic/network-management-client](https://github.com/meshtastic/network-management-client)
- **Regional MeshCore communities with published tooling**: MeshCore.ca (CoreScope + analyzer) — [MeshCore-ca](https://gh.r-mj.com/MeshCore-ca); ScotMesh — [ScotMesh](https://scotmesh.mm7roq.compute.oarc.uk/); WPAMesh (Western PA) — [Technical.ly](https://technical.ly/workforce/wpamesh-western-pa-off-grid-radio-network.md); EastMesh AU wiki on routing — [wiki.eastmesh.au](https://wiki.eastmesh.au/meshcore/routing/); Dutch MeshCore MQTT/CLI docs — [dutch-meshcore](https://dutch-meshcore.github.io/DutchMeshCore.nl-MQTT/cli_commands.html); Colorado mesh-client — [colorado-mesh](https://colorado-mesh.github.io/mesh-client/credits/)
- **MeshCore protocol-level congestion controls** (relevant to the CascadiaMesh rules above):
  - `flood.advert.interval` (hours, range 3–168, default 12), where "shorter intervals put more advert traffic on the air for every repeater that relays them"; zero-hop adverts 60–240 min or 0 to disable — [areyoumeshingwith.us MeshCore config](https://areyoumeshingwith.us/docs/meshcore/configuration/), [MeshCore CLI reference](https://github.com/meshcore-dev/MeshCore/wiki/Repeater-&-Room-Server-CLI-Reference)
  - Region filtering: since v1.10.0, repeaters won't forward scoped flood packets lacking a matching region; `region denyf *` to block legacy unscoped floods is "NOT advised at this stage" — [MeshCore blog, 2026-01-20 Region filtering](https://blog.meshcore.io/2026/01/20/region-filtering)
  - Default scope (`region default {name|<null>}`, v1.15.0) to confine adverts to, e.g., a city — [MeshCore blog, 2026-04-17 Default scope](https://blog.meshcore.io/2026/04/17/default-scope)
  - Issue #1223 argues that large networks suffer severe congestion from advertisement floods — [MeshCore issue #1223 mirror](https://gh.hhj520.top/meshcore-dev/MeshCore/issues/1223)
  - A fork ("MeshCoreNG") documents dense-mesh probabilistic forwarding (`flood.advert.base`); this is not mainline — [MeshCoreNG dense mesh](https://michtronics.github.io/MeshCoreNG/docs/dense-mesh.html)
- MeshCore firmware is MIT-licensed; main repo `meshcore-dev/MeshCore` — [MeshCore (Wikipedia)](https://en.wikipedia.org/wiki/MeshCore), [meshcore-dev](https://github.com/meshcore-dev)

### Inferences
- **Decoupled federation**: two production patterns exist. CoreScope federates through *shared MQTT namespaces*, with the broker as the hub. PotatoMesh federates through *instance-to-instance crawl*, with no broker. MeshForge's RNS-as-hub design is a third pattern. PotatoMesh's crawl model plus `PRIVATE=1` is the closest prior art for "federate without a central broker."
- MeshMonitor's congestion-aware bot pause is a concrete, shipped example of a MOC *acting* on airtime, not just displaying it. It is directly relevant to MeshForge's gateway bridge, which should back off TX when channel utilization is high.
- MQTT-fed analyzers have a structural blind spot: they see only packets that some gateway uplinked. In MeshForge's terms, "unobservable ≠ quiet." A low count on Meshview/Malla can mean no uplink rather than no traffic.

### Gaps (not verified this session; from prior knowledge only, so the report writer should treat these as UNVERIFIED leads, not facts)
- Not fetched this session: MeshSense, meshmap.net, Meshtasticator (official discrete-event simulator, useful for modelling congestion), Meshing-Around bot (SpudGunMan), mesh-bbs, Mesh Mapper, meshcore.co.uk map, MeshCore Analyzer, room servers. Their licences, stars and Nov 2026 activity are **UNKNOWN**.
- Reticulum apps (Sideband, MeshChat by liamcottle, NomadNet, Columba): not researched due to the tool budget. Stars, last release and licence are **UNKNOWN** here. Only PotatoMesh's Reticulum announce-only support is cited.
- Bay Mesh, Austin Mesh, Utah Mesh: no tooling sources retrieved.
- GitHub last-commit/last-release dates for most repos: the GitHub API was blocked in this sandbox and the HTML captures omitted them.

---

## Trend: migration from Meshtastic to MeshCore (evidence and rationale)

### Takeaway
There is a visible 2025–2026 trend of communities moving from Meshtastic to MeshCore, most clearly in the Pacific NW, Netherlands, UK and Australia. The stated rationale is that **repeater-only infrastructure routing** (clients don't rebroadcast; learned paths after the first flood) wastes less airtime than Meshtastic's client-rebroadcast managed flooding, and that MeshCore allows far more hops (64 vs Meshtastic's default 3). The evidence is mostly community testimony and vendor or blog comparisons, not measured airtime studies.

### Cited Findings
- Xebia team switched in Jan 2026, citing MeshCore's 64-hop default vs Meshtastic's 3 and more reliable long-path delivery — [Xebia: Meshtastic to MeshCore](https://xebia.com/blog/meshtastic-to-meshcore/)
- MeshEnvy says MeshCore "consistently outperforms Meshtastic" in speed, reliability and ease of use (their own testing, not independent) — [MeshEnvy: Migrating to MeshCore](https://meshenvy.org/news/migrating-to-meshcore), [MeshEnvy migration](https://meshenvy.org/migration/)
- Dutch ham PE4BAS documents moving from Meshtastic to MeshCore (Dec 2025) — [PE4BAS blog](https://pe4bas.blogspot.com/2025/12/moved-from-meshtastic-to-meshcore.html)
- A comparison argues that MeshCore's lower airtime overhead benefits duty-cycle-restricted regions proportionally more — [SimeonOnSecurity comparison](https://simeononsecurity.com/articles/meshcore-vs-meshtastic-comparison-guide/)
- Forum anecdote: "everyone I've spoken to that uses meshcore started on meshtastic, got frustrated and made the switch" — [myGMRS forum](https://forums.mygmrs.com/topic/11413-meshtastic-or-meshcore-%F0%9F%A4%94)
- Counterpoints: the two protocols are not over-the-air compatible; MeshCore depends on someone having built a repeater backbone; switching without better network planning doesn't reliably help; MeshCore tooling is less polished — [Little Bird Electronics](https://littlebirdelectronics.com.au/blogs/news/meshcore-vs-meshtastic-what-s-actually-different-and-which-should-you-flash), [OpenELAB](https://openelab.io/blogs/news/meshtastic-or-meshcore-it-depends-on-your-use-case)
- Vashon Mesh's split recommendation (MeshCore for urban/suburban, Meshtastic for backcountry) — [Vashon Mesh](https://vashonmesh-github-io.pages.dev/mesh-tech/)
- PNW growth is called out by MeshCore's author — [MeshCore Update #6](https://buymeacoffee.com/ripplebiz/meshcore-update-6); CascadiaMesh shows ~3–4k nodes — [CascadiaMesh](https://cascadiamesh.org/)

### Inferences
- MeshCore's own scaling pain (advert floods, issue #1223; region scoping in v1.10–v1.15) shows that moving to MeshCore *moves* the congestion problem rather than eliminating it. Large MeshCore meshes needed administrative caps (CascadiaMesh's ≥48 h advert rule) and new protocol features.
- For Hawaii: migration is a community-governance decision with a hard interoperability cost. A MOC that bridges both (MeshForge already has a MeshCore gateway handler) lowers that cost.

### Gaps
- I found no controlled, published airtime measurement comparing the same region before and after migration.
- No quantitative share-of-nodes trend data (e.g., Meshtastic vs MeshCore node counts over time).

---

## Which tools expose channel utilization / airtime / per-node offender metrics (for a Hawaii-style congestion diagnosis)?

### Takeaway
On Meshtastic, the raw signal exists because nodes self-report `channel_utilization` (and `air_util_tx`) in device telemetry. **Meshview** surfaces it per packet and ranks "top" routers/senders. **Malla** offers top talkers and a gateway-compare matrix. **MeshMonitor** consumes airtime utilization to gate bot TX. On MeshCore, **CoreScope**'s multi-observer packet capture is the richest data source, but no fetched tool explicitly charts per-node airtime share. A true "offender" ranking (bytes × time-on-air by originator/relayer) appears to be an unfilled niche.

### Cited Findings
- Meshview packet pages display device metrics, including `channel_utilization`; its "Times Seen" value is described as the closest proxy for per-node mesh utilization; stats include top senders/routers — [meshview.meshtastic.es](https://meshview.meshtastic.es/top), [pablorevilla-meshtastic/meshview](https://github.com/pablorevilla-meshtastic/meshview)
- Malla: top talkers, hop distribution, RSSI distribution, gateway-compare matrix; no channel-utilization chart mentioned — [zenitraM/malla](https://github.com/zenitraM/malla)
- MeshMonitor: airtime-utilization cutoff pauses bot traffic; unified telemetry graphs; hop-distribution widget — [Yeraze/meshmonitor](https://github.com/Yeraze/meshmonitor)
- CoreScope: per-node analytics, RF tab, route patterns, cross-observer tracing, observer health; no explicit airtime metric — [Kpa-clawbot/CoreScope](https://github.com/Kpa-clawbot/CoreScope)
- PotatoMesh: telemetry views + Prometheus `/metrics` exporter (makes ingested telemetry graphable in Grafana) — [l5yth/potato-mesh](https://github.com/l5yth/potato-mesh)
- MeshCore-side levers for reducing airtime: flood-advert interval, region scoping — [MeshCore CLI reference](https://github.com/meshcore-dev/MeshCore/wiki/Repeater-&-Room-Server-CLI-Reference), [MeshCore blog: region filtering](https://blog.meshcore.io/2026/01/20/region-filtering)

### Inferences
- **Feature opportunity for MeshForge**: combine (1) per-node self-reported `channel_utilization`/`air_util_tx` over time, (2) observed packet counts by originator and by relayer from its own radios plus MQTT, and (3) LoRa time-on-air computed from the preset. Rank the top airtime consumers ("offenders"), and flag nodes with excessive telemetry/position/NodeInfo intervals or rebroadcast roles. None of the tools surveyed advertises this combination.
- Keep source tri-state per MeshForge's honest-failure rules. An MQTT-only view is blind to non-uplinked traffic, so a per-node ranking should label its observation coverage (number of gateways/observers that could have heard it).

### Gaps
- No source confirms that any tool charts per-node `channel_utilization` as a time series with alerting; this would need checking against live instances or code.
- No Hawaii-specific tool or dataset was found in this sub-scope; other researchers may cover it.
