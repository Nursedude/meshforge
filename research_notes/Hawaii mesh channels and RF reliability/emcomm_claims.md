# EmComm Claims: hawaiimesh.net and Community LoRa Mesh as Emergency Communications

> **Method caveat (read first):** This session's egress proxy refused direct connections to `hawaiimesh.net`, `www.arrl.org`, and other hosts (`curl` returned `CONNECT tunnel failed, response 403`; WebFetch returned `ENOTFOUND`). **No page was fetched directly.** Everything below comes from web-search result summaries/snippets. The quotes attributed to hawaiimesh.net are search-engine extracts and are **not verified verbatim**. To verify them, allow `hawaiimesh.net` in the session network settings and re-fetch. Tier: BELIEVED, not VERIFIED.

## 1. What hawaiimesh.net claims about emergency use

### Takeaway
The only claim that surfaced describes "Hawaii Meshnet" as a volunteer, hobbyist-built, **in-progress**, "secure, off grid, emergency communication system using Meshtastic nodes". No partnerships with HI-EMA, county EMAs, ARES/RACES, CERT, or Red Cross turned up. No MeshCore page turned up either. As far as the search shows, the claim is aspirational ("building") rather than operational, which reads as a reasonable supplemental-comms framing and not an overclaim. One wording issue: "secure" applies to Meshtastic's default channel encryption, and it is incompatible with ham-mode operation (see §4).

### Cited Findings
- Search extract of the site's self-description: volunteer radio enthusiasts building "a secure, off grid, emergency communication system using Meshtastic nodes" — [Hawaii Meshnet](https://www.hawaiimesh.net/) (via search summary, not fetched)
- A domain-restricted search summary says the group calls itself radio hobbyists, says the project "is still in progress", and asks people to use contact links. It gives no node locations, coverage, or volunteer process — [Hawaii Meshnet](https://www.hawaiimesh.net/)
- "Hawaii Meshnet" is listed as a community in the Meshtastic local-groups directory and in a curated Meshtastic resource list. Neither listing ties it to a government or volunteer drill — [Meshtastic Local Groups](https://meshtastic.org/docs/community/local-groups/); [awesome-meshtastic](https://github.com/ShakataGaNai/awesome-meshtastic/)
- A separate Oahu project, Ham Radio in Hawaii / Oahu Mesh, makes stronger claims. Per the search summary, its nodes are "solar powered and need no internet or cell phone coverage, so they will work even if the power is out", and "as of fall 2025 we have coverage throughout most of Southern - East to West Oahu" — [Ham Radio in Hawaii – Mesh](https://www.hamradiohawaii.com/mesh)
- No hawaiimesh.net page claiming MeshCore operation was found. The search summary said: "I found nothing showing a Hawaii group running MeshCore" — [search over hawaiimesh.net + related](https://www.hawaiimesh.net/)

### Inferences
- "Will work even if the power is out" (Oahu Mesh) is a design property of solar nodes. It is not a measured outage result: no battery-autonomy figures (days without sun), delivery rates, or drill results were found. Claims like this need evidence of that kind before they count as operational.
- No served-agency partnership was found, so either Hawaii group's network is at best a community or auxiliary channel. It is not part of any agency PACE plan.

### Gaps
- The verbatim text of the hawaiimesh.net home, about, FAQ, and any EmComm page is unavailable because the host was blocked.
- Whether hawaiimesh.net has a MeshCore page, a node map, or an MQTT bridge is unknown.
- I could not confirm the date of the site's claims.

## 2. Hawaii EmComm bodies' posture and the Maui (Lahaina) 2023 lessons

### Takeaway
No evidence was found that HI-EMA, any county EMA, Hawaii ARES, or RACES has adopted Meshtastic or LoRa mesh, run a drill with it, or used it in ICS-213 traffic. Hawaii ARES's documented digital/mesh direction is **Winlink and AREDN**. In Lahaina the cellular network failed and the radio repeaters held but were overwhelmed. The lesson points to coordination and capacity problems on the channels that survived, more than to a lack of off-grid hardware.

### Cited Findings
- An older Hawaii ARES page on a national spring drill describes HF message passing and plans to support **Winlink and AREDN mesh** in future exercises. It does not mention Meshtastic — [Hawaii ARES (old site)](https://old.hawaiiares.net/)
- A Hawaii AREDN statewide deployment standard describes a hub node on each island — [Hawaii AREDN MESH Deployment](https://wh6av.net/Hawaii-AREDN-MESH-Standards.pdf)
- HI-EMA's annual Makani Pahili hurricane exercise: the coverage found does not mention mesh radios — [dod.hawaii.gov Makani Pahili](https://dod.hawaii.gov/blog/makani-pahili); [GovTech](https://www.govtech.com/em/disaster/hawaii-national-guard-disaster-exercise.html)
- During the 2023 Maui fires, ARRL reported that most amateur traffic, including relayed messages, ran on the existing statewide repeater system — [ARRL](https://www.arrl.org/news/view/devastating-hawaii-wildfires-prompt-response-from-amateur-radio-emergency-service)
- The Maui Fire Department after-action report (per news coverage) says a repeater kept radio up while cell towers and fiber were down, but radio was "overwhelmed" for "a variety" of reasons. Winds also made radio use hard and 911 got hundreds of calls. The report recommends an analysis of the island's cellular system — [NPR](https://www.npr.org/2024/04/17/1245396324/maui-fire-report-communications-breakdown); [NBC News](https://www.nbcnews.com/news/us-news/maui-fire-department-report-deadly-wildfire-details-need-equipment-mut-rcna148141); [KOAT](https://www.koat.com/article/maui-fire-department-release-after-action-report-deadly-hawaii-wildfires/60513443)
- The Hawaii AG preliminary report describes a breakdown among police, fire, and emergency officials after cell networks failed — [NPR/HPR](https://www.hawaiipublicradio.org/npr-news/2024-04-17/a-new-report-on-the-maui-wildfires-cites-communications-breakdowns)
- The Maui Police Department's preliminary after-action report has 32 recommendations, including equipment and a senior officer in the comms center — [NBC News](https://www.nbcnews.com/news/amp/rcna137402)
- A UH Press book says West Maui residents "constructed lifesaving networks in real time" while sirens were silent and comms failed. These were improvised mutual-aid networks, and Meshtastic is not named — [UH Press, After the Fire](https://uhpress.hawaii.edu/title/after-the-fire-lahaina-mutual-aid-and-community-organizing/)
- Hawaii County AuxComm, Vibrant Hawaii, and Civil Defense use the county Emergency Alerts service. That is the official alert channel, not mesh — [Hawaii Tribune-Herald](https://www.hawaiitribune-herald.com/?p=344797) (search summary)
- Related but distinct: goTenna demonstrated an island-wide Oahu mesh for the US military in 2023, built in under 4 hours for under $30K. This was proprietary hardware, not Meshtastic — [PR Newswire](https://www.prnewswire.com/news-releases/gotennas-mesh-network-demonstrates-oahu-connectivity-for-us-military-301851115.html)

### Inferences
- In Lahaina, failure was partly saturation of the surviving radio and 911 channels. A community mesh sharing one LoRa channel would face the same surge dynamics at much lower capacity (see §4).
- Community-to-community messaging, the West Maui mutual-aid pattern, is where community LoRa mesh plausibly adds value. Agency command-and-control is not.

### Gaps
- No Hawaii RACES, HI-EMA, Maui/Honolulu/Hawaii County EMA statement on Meshtastic or MeshCore was found, either for or against.
- I found no ICS-213 or after-action references to LoRa mesh in Hawaii.
- I could not open the Hawaii Tribune-Herald article (p=344797) to check whether it discusses mesh.

## 3. Evidence from real disasters

### Takeaway
Documented evidence is thin and almost entirely anecdotal: forum posts, blogs, vendor and advocacy pages. No FEMA, state, or peer-reviewed after-action report quantifies Meshtastic delivery in Helene, Milton, the LA fires, or Ukraine. The best-sourced failure mode is congestion from newcomer chatter, reported for western NC, but that source is a secondary guide.

### Cited Findings
- **Helene (2024):** A NC ham forum thread describes members setting up Meshtastic nodes, using the NCMesh config, to reach non-ham family in western NC — [CARC-NC forum](https://www.carc-nc.us/forums/threads/meshtastic.463/)
- **Helene:** A guide reports the WNC mesh "briefly became unusable when newcomers spammed status updates every 30 seconds". The radios still worked, but the traffic crowded out emergency messages, and a small core of hams acted as informal net control — [Pamfinds guide](https://pamfinds.com/guides/disaster-comms-meshtastic-meshcore/) (secondary, not first-hand)
- **Helene:** A claim that Meshtastic was "among the first communication systems back online" — [Brainstamped](https://brainstamped.com/emergency-readiness/guides/best-meshtastic-devices-off-grid-communication-2026/) (marketing/guide, unsourced)
- **Helene context:** Residents reported "no internet, no cell phone" for two days — [Monocle](https://monocle.com/affairs/asheville-recovery-post-hurricane-helene/). Post-Helene volunteers became interested in Meshtastic-type networks — [Floyd Times](https://www.floydtimes.com/a-new-layer-of-resilience-community-members-gather-to-discuss-secondary-communication-network/)
- **Milton (2024):** No reports of Meshtastic use were found.
- **LA fires (Jan 2025):** No documented Meshtastic use during the Eaton or Palisades fires was found. Reviews cite chaotic interagency comms and delayed warnings — [CalMatters](https://calmatters.org/environment/wildfires/2026/10/palisades-eaton-fires-report-failure-newsom/). A mesh-advocacy page says cell towers and fiber "melted" — [regionmesh](https://www.regionmesh.com/mesh-network-los-angeles/) (advocacy). SoCal Mesh exists, but its activity during the fires is unconfirmed — [SoCal Mesh](https://socalmesh.org/)
- **Ukraine:** A forum thread donated units because of power and comms outages, and a developer comment says air-raid warnings are relayed over the mesh (anecdotal) — [Meshtastic forum](https://meshtastic.discourse.group/t/using-in-ukraine/5685). Military mesh interest is about mesh generally, not Meshtastic — [Euromaidan Press](https://euromaidanpress.com/2026/06/26/in-the-worlds-most-jammed-battlefield-a-kyiv-company-keeps-the-signal-alive/). Claims of "hundreds of nodes" in frontline use come from a low-quality, auto-generated page — [ukraine-war-analytics](https://ukraine-war-analytics.com/cyber/mesh-networking-ukraine.html) (treat with caution)
- **Exercise, not a disaster:** A military border-conflict exercise reported about 150 Meshtastic nodes with ATAK, described as "reliable, self-healing" — [defencebay](https://defencebay.com/en/blog/off-grid-tactical-comms-on-the-battlefield-meshtastic-and-atak-integration-in-a-border-conflict-exercise) (vendor/blog)
- **Hawaii anecdote:** A personal blog on a Hawaii outage (apparently Big Island) said the mesh was "the most reliable of everything" — [urbansurvival](https://urbansurvival.com/thurs-datafication-overthehorizon-lala-hank-on-prepping-irl/) (anecdotal, undated)
- An OSINT review: "no independent research on large networks with many hops, on power outages or on peak load, and for MeshCore there is nothing at all" — [OSINT Team](https://osintteam.blog/meshcore-and-meshtastic-a-place-but-in-the-experimental-corner-c7a6c7a1a168?gi=fefe1a3f7589)
- In New Zealand, Eastern Bays emergency coordination tested Meshtastic, then built MeshCore after testing found technical limitations — [Scoop NZ](https://www.scoop.co.nz/stories/AK2509/S00669/back-up-comms-crucial-in-emergencies.htm); [eastbourne.nz](https://eastbourne.nz/eastern-bays-emergency-response-network/)

### Inferences
- The evidence supports "people used it and some messages got through". It does not support any measured reliability claim. An EmComm claim citing "proven in Helene" overstates what is documented.
- Surge congestion from newcomers is the recurring failure mode. It is exactly the "everyone turns on at once" scenario.

### Gaps
- No primary NC Mesh (ncmesh.net) after-action write-up was found.
- No delivery-rate, latency, or node-uptime data from any real disaster was found.
- 2025–2026 events beyond those above: none found with documented LoRa mesh use.

## 4. Technical limits relevant to EmComm

### Takeaway
LoRa mesh is low-throughput, best-effort, flood-routed, and capacity-bounded, with no guaranteed end-to-end delivery. Meshtastic's own team concedes flooding is inefficient and capacity-limited. Under FCC Part 97, ham-mode Meshtastic must drop encryption, which conflicts with any "secure" claim for licensed operation and splits licensed nodes from the encrypted public mesh.

### Cited Findings
- Meshtastic's official blog says managed flood routing is not very efficient, but nothing has been found that consistently outperforms it for current use cases. The mesh has capacity limits — [Meshtastic blog](https://meshtastic.org/blog/why-meshtastic-uses-managed-flood-routing/)
- Firmware PR #11826 adds congestion-gated hop scaling that engages at 20% channel utilization and releases at 12%. This is evidence that congestion is an active engineering problem — [meshtastic/firmware PR #11826](https://github.com/meshtastic/firmware/pull/11826)
- Meshtastic performance reportedly degrades "around 100 nodes on a single channel". This comes from a secondary source and is not confirmed in the official docs — [DEV Community](https://dev.to/nexttechworld/meshtastic-vs-meshcore-flooding-and-routing-explained-1kcd); [openelab](https://openelab.io/blogs/getting-started/meshcore-vs-meshtastic-which-lora-mesh-protocol-is-right-for-you)
- Typical hop limits are 3–7, with roughly 33% airtime limits per node (secondary) — [irab.github.io](https://irab.github.io/mesh/technical.html)
- A 2026 arXiv paper on Meshtastic resilience lists managed-flooding scalability in dense NLoS as **future work**, so it remains unevaluated — [arXiv 2605.17063](https://arxiv.org/pdf/2605.17063)
- MeshCore is characterized as infrastructure/role-based (fixed repeaters, routed paths) and better for blanket coverage and EmComm than Meshtastic's ad-hoc flooding (characterization, not measured) — [getupandgocamping](https://getupandgocamping.com/meshcore-vs-meshtastic-and-which-mesh-network-actually-scales/)
- Meshtastic is "under active development and not a life-critical service" — [Freq51 (Intermountain Mesh)](https://freq51.net/print.html)
- **Part 97 vs ISM:** Setting a ham ID or licensed mode turns encryption off and requires self-certification. Encrypted amateur transmissions are barred by §97.113(a)(4). Default Meshtastic runs under Part 15 (ISM) with encryption on and no callsign — [Meshtastic ham docs mirror](https://meshtastic.vercel.app/docs/software/device/ham); [Meshtastic forum](https://meshtastic.discourse.group/t/ham-radio-and-encryption/8008)
- Licensed-mode nodes cannot participate in an encrypted public channel (BayMesh policy) — [BayMesh Licensed Operations](https://bayme.sh/docs/licensed-operations/)
- Meshtastic uses AES-128 channel encryption (PSK). Open-source mesh tools have a different threat model from military-grade systems — [ukraine-war-analytics](https://ukraine-war-analytics.com/cyber/mesh-networking-ukraine.html) (low-quality source; the AES detail is consistent with the project's docs)
- **Solar autonomy:** One builder logged winter charging that never exceeded 1 W on a 4 W panel. Panel and battery sizing for worst-case insolation is therefore a real design constraint — [kaspars.net](https://kaspars.net/blog/solar-lora-meshtastic/md)
- The MeshCore repeater firmware observed by one builder cannot turn off repeat (operational-control quirk) — [search summary, hobbyist blog](https://mesh.oppedahl.com/?p=216)

### Inferences
- **No guaranteed delivery:** Meshtastic DMs carry hop-limited implicit or explicit acks, but channel broadcasts have no end-to-end confirmation. That fails the ICS-213 expectation of accountable message handling unless operators add a manual acknowledgement protocol. (Inferred from the protocol design, not from a cited test.)
- **Single points of failure:** MQTT bridges and web maps depend on the internet. Any "network map" or cross-island reach that rides MQTT disappears exactly when the internet is down, so it should not count toward the disaster-mode claim. (Inference; no Hawaii-specific MQTT dependency was confirmed.)
- **Inter-island Hawaii:** 915 MHz LoRa cannot cross channels like the Kaiwi or ʻAlenuihāhā without very high sites or an internet/MQTT bridge, so a "statewide" claim almost certainly rests on internet links. (Physics inference; not measured.)
- **"Secure"** holds only for Part 15 operation with a private PSK. A shared default or public key is not secure, and ham-mode nodes are unencrypted by law.

### Gaps
- No independent measured surge-load test of Meshtastic or MeshCore was found.
- No Hawaii node-level backup-power specs were found.

## 5. Criteria that would make an EmComm claim credible

### Takeaway
Federal guidance (CISA/SAFECOM PACE, AUXCOMM) frames auxiliary systems as redundancy tiers that must be planned, documented, and **practiced**. A credible community-mesh EmComm claim would therefore show drills, measured delivery, power specs, and a served-agency relationship. None of these were found for hawaiimesh.net.

### Cited Findings
- The PACE (Primary/Alternate/Contingency/Emergency) plan exists to establish redundancy among comms capabilities. It is triggered when the primary becomes unavailable — [CISA NCSWIC PACE](https://cisa.gov/news-events/news/ncswic-pte-committee-releases-leveraging-pace-plan-emergency-communications-ecosystem); [PACE plan PDF](https://cybersecurity.gov/sites/default/files/2023-05/23_0426_ncswic_PACE-Plan_508.pdf)
- SAFECOM recommends putting "as much detail in your PACE plan as possible and practicing it before you need it" — [CISA SAFECOM meeting summary](https://www.cisa.gov/sites/default/files/video/22_0608_s-n_bi-annual%20meeting_summary_final_508.pdf)
- CISA built standardized AUXCOMM training because of the role auxiliary communicators play in a crisis — [SAFECOM Resource Library](https://cisa.gov/safecom-resource-library-n-through-q)
- MITRE (2026) on building PACE capabilities for the current threat environment — [MITRE PDF](https://www.mitre.org/sites/default/files/2026-01/PR-25-2966-building-pace-capabilities-for-the-current-threat-environment_0.pdf) (not opened)

### Inferences
Proposed credibility rubric, synthesized from the above:
1. **Documented drills.** Dated exercises with a scenario, participants, and results, ideally with ARES/RACES/CERT.
2. **Measured delivery.** For example, N messages sent, percentage acked, and median latency across hop counts, including a surge test where many nodes transmit at once.
3. **Backup-power specs.** Battery Wh, panel W, and days of autonomy at worst-case insolation, published per infrastructure node.
4. **Dependency disclosure.** Which coverage or features need internet or MQTT, with that coverage excluded from disaster-mode claims.
5. **Served-agency relationship.** An MOU or written role in a county EMA or HI-EMA PACE/AUXCOMM plan, or explicit language saying "community/supplemental, not agency-integrated".
6. **Message-handling discipline.** A net-control procedure, ICS-213-style logging, and channel etiquette to prevent the Helene-style flooding.
7. **Honest disclaimer.** Echo of the project's own "not life-critical" framing.

Fair verdict: framing a network as "building an off-grid emergency comms system" is a **reasonable supplemental-comms claim** for a volunteer project. It becomes an **overclaim** if the network is presented as dependable when the grid is down, or as part of official response, without items 1–5.

### Gaps
- I did not open the full CISA AUXCOMM or PACE PDFs, so I found no explicit federal statement on Meshtastic or ISM LoRa mesh specifically.
- No Hawaii county PACE plan was found to compare against.
