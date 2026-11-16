# Why Hawaiian LoRa Mesh (Meshtastic/MeshCore) Links May Have Diminished, 2025 to Nov 2026

> Research notes, 2026-11-16. Scope: Hawaii, 2025 to Nov 2026.
> **Bottom line up front:** I found **no public, Hawaii-specific report** (Reddit, news, hawaiimesh.net, Discord) that documents a decline. The decline is the operator's observation; this file ranks the candidate causes and says what each would look like in measurements. Each claim is tagged EVIDENCE (sourced), PHYSICS (computed here, reproducible), or SPECULATION.
> **Blind spots:** two primary Hawaii sources could not be fetched from this sandbox (`hamradiohawaii.com`, `earningswhispers.com` failed DNS: outside the allowed network). Discord is not searchable. meshmap.net and MQTT stats were not queried, so **there is no node-count time series for Hawaii**, and that is the single most useful missing measurement.

---

## 1. Hawaii-specific reports (community, news, infrastructure sites)

### Takeaway
There is no public corroboration of a decline, and none refuting one. The public record on Hawaii LoRa mesh is thin: one Oahu group (Oahu Mesh / Ham Radio in Hawaii) claims coverage of most of south Oahu as of fall 2025 using solar-powered repeaters, and one statewide group, "Hawaii Meshnet", is listed by Meshtastic. Nothing in the indexed web describes inter-island Meshtastic or MeshCore links, mountaintop router losses, or preset changes.

### Cited Findings
- Oahu Mesh (hamradiohawaii.com/mesh) reports coverage "throughout most of Southern – East to West Oahu as of fall 2025", built from "a number of solar-powered radio repeaters / Nodes on Oahu"; it describes the network as decentralized and open. The page itself could not be fetched; this is from the search snippet. — [Ham Radio in Hawaii – Mesh](https://www.hamradiohawaii.com/mesh)
- The only Hawaii entry in Meshtastic's official local-groups list is "Hawaii Meshnet". — [Meshtastic Local Groups](https://meshtastic.org/docs/community/local-groups/); also in [awesome-meshtastic](https://github.com/ShakataGaNai/awesome-meshtastic/)
- On Kauai, KauaiInternet describes Meshtastic as experimental, with North Shore pilot tests planned. — [Kauai Resilient Communications Network](https://kauaiinternet.com/)
- Other Hawaii mesh activity is non-LoRa or proprietary: a goTenna Oahu-wide military demo in 2023 used fewer than 15 devices ([PRNewswire](https://prnewswire.com/news-releases/gotennas-mesh-network-demonstrates-oahu-connectivity-for-us-military-301851115.html)); Rajant after the Maui fires ([Rajant](https://rajant.com/sitech-deploys-rajant-kinetic-mesh-network-following-maui-wildfires)); and AREDN hubs planned for Oahu and Maui ([HEARDn AREDN standards PDF](https://wh6av.net/Hawaii-AREDN-MESH-Standards.pdf)). None of these share the 915 MHz LoRa channel. AREDN uses 2.4/3.4/5.8 GHz (PHYSICS/domain knowledge, not sourced here).
- Searches for MeshCore in Hawaii (Oahu, Maui, 2026) returned **nothing**. — search log, this session

### Inferences
- The decline cannot be corroborated or refuted from the public web. Any ranking has to rest on the operator's own measurements (see §6). (SPECULATION about cause; EVIDENCE of the absence.)
- Fall 2025 coverage was described as "most of south Oahu" and not inter-island. If inter-island links did exist, they were probably MQTT-bridged or opportunistic rather than engineered RF links. This is a weak inference from absence.

### Gaps
- No Reddit threads were found (r/meshtastic, r/Hawaii, r/amateurradio) about Hawaii range or congestion. The search engine indexes Reddit poorly, so this is not proof of absence.
- hawaiimesh.net did not appear in any search, so its existence is unconfirmed. HawaiiMesh Discord is unreachable.
- No meshmap.net or MQTT-stat node counts for Hawaii over time were retrieved. **This is the key missing dataset.**
- No status found for Haleakala, Mauna Kea, Kaʻala, or Diamond Head LoRa sites.

---

## 2. Network-side causes (congestion, roles, MQTT, firmware, presets, MeshCore split)

### Takeaway
These are the **most probable class** if the mesh grew in 2025–26. That is a BELIEVED inference: the mechanisms are well documented elsewhere, but there is no Hawaii data. Meshtastic's own docs and 2026 field reports show channel utilization of 25–50% on busy meshes, and they show that config and traffic-management fixes measurably cut it. The 2.7.x firmware also changed ROUTER_LATE behaviour, exempting it from congestion scaling.

### Cited Findings
- Meshtastic uses CSMA/CA with a contention window sized by current channel utilization, so busier channels lead to longer waits and more collisions. — [Mesh Broadcast Algorithm](https://meshtastic.org/docs/overview/mesh-algo/)
- ROUTER_LATE "tries to rebroadcast everything it hears" and "adds a notable amount of traffic to the single, shared frequency… can cause problematic congestion, collisions, and lost packets". Meshtastic recommends CLIENT_BASE for rooftop nodes instead. — [Demystifying ROUTER_LATE (Meshtastic blog)](https://meshtastic.org/blog/demystifying-router-late/)
- Firmware 2.7.20 release notes list "add ROUTER_LATE and TAK_TRACKER to congestion scaling exemption", ROUTER_LATE uses "the same rebroadcast rules as ROUTER", and it is treated like ROUTER for power and defaults. — [v2.7.20 release notes (newreleases mirror)](https://newreleases.io/project/github/meshtastic/firmware/release/v2.7.20.6658ec2). A GitHub discussion reports that "scaling does not apply anymore for ROUTER_LATE and other device roles" — [firmware discussion #9437](https://github.com/meshtastic/firmware/discussions/9437). I did not verify the PRs (#9815/#9816/#9818) directly.
- 2026 field report (a non-Hawaii city mesh of about 250–350 active nodes): channel utilization typically 25–35% and up to about 50%, with "spammers"; enabling the proposed traffic-management module on 3 nodes cut it from 40–50% to 20–30%. The same user kept MEDIUM_FAST because SHORT_FAST "lost range and reliability". PR #9476, which drops NodeInfo from old firmware sending it every minute, was reported merged on 2026-05-07. — [firmware discussion #9437](https://github.com/meshtastic/firmware/discussions/9437)
- Island precedent for MQTT bridging: Cayman ARS bridges its island mesh via the public Meshtastic broker. — [caymanhams.org](https://caymanhams.org/meshtastic/)

### Inferences
- **Congestion signature (what to measure):** rising `channel_utilization` and `air_util_tx` in device telemetry, more packets heard but fewer ACKs or traceroutes completing, and *SNR unchanged on links that still work*. Degradation correlates with time of day (human activity) rather than weather. Range "shrinks" because multi-hop packets die in collisions, not because path loss changed. (SPECULATION applied to Hawaii; mechanism EVIDENCE above.)
- **ROUTER/ROUTER_LATE misuse on well-sited nodes** also shows up as high channel utilization and duplicate rebroadcasts. A 2.7.20 upgrade that removed congestion scaling for ROUTER_LATE would show a *step* in channel utilization on the upgrade date. Compare node firmware versions against the onset date.
- **MQTT downlink floods:** packets with `via_mqtt` flags and nodes in NodeDB that are not RF-reachable (e.g., mainland nodes) point here. Their signature is utilization spikes with no matching local RF activity.
- **Mixed presets / MeshCore split:** if part of the community or the infrastructure moved to MeshCore (a different packet format, often different freq/SF/BW) or a different Meshtastic preset/slot, the Meshtastic mesh loses relays *without any RF change*. The signature is a falling count of distinct relay/neighbor nodes and longer traceroutes, while noise floor and per-link SNR stay flat. No Hawaii evidence of a MeshCore migration was found (SPECULATION, but a commonly reported pattern elsewhere; not sourced here).
- **Hop limit:** inter-island paths via relays easily exceed the default hop limit of 3. A default hop-limit change, or nodes set to 0 (e.g., the traffic-management "hop exhaustion" for telemetry), would cut reach without RF change. The signature is traceroutes terminating at a fixed hop count.

### Gaps
- No Hawaii channel utilization data, firmware version distribution, or role census.
- I did not confirm what 2.6's NextHop routing changed for DMs (direct messages) vs broadcasts in the official docs this session.

---

## 3. RF / physical causes (noise floor, ducting, sea fading, humidity, solar cycle, vegetation, hardware)

### Takeaway
Of the physical causes, **(a) remote-node power or hardware failure** and **(b) a rising local 915 MHz noise floor** are the plausible step-change explanations. Tropospheric variability is real over Hawaii but is a long-term trend plus day-to-day fluctuation, not a 2025–26 step. Humidity and solar cycle are **negligible at 915 MHz** on line-of-sight paths (PHYSICS below).

### Cited Findings
- **Hawaiian Electric AMI:** the current grid modernization uses Landis+Gyr **Gridstream Connect on Wi-SUN-certified networks**. — [Landis+Gyr press release](https://landisgyr.com/news/hawaiian-utility-selects-landisgyrs-gridstream-technology-for-smart-grid-deployment). The older Sensus FlexNet plan used **FCC-licensed** spectrum with 19 towers on Oahu, Maui, and Hawaii Island, so it is *not* in the ISM band. — [T&D World](https://www.tdworld.com/smart-utility/article/20957585/hawaiian-electric-selects-sensus-flexnet-ami), [Sensus](https://sensus.com/news-events/news-releases/hawaiian-electric-selects-sensus-flexnet-ami/). Silver Spring ran the earlier phase-1 pilot in select Oahu neighborhoods. — [Utility Dive](https://utilitydive.com/news/heco-completes-first-phase-of-smart-grid-roll-out/313681). Kauai's KIUC is a separate Gridstream deployment from 2011 (per the same search; Landis+Gyr).
- **Trade-wind inversion (TWI) trend:** TWI frequency over Hawaii rose about 6%/decade (dry season) and 5%/decade (wet season) over 1979–2013, with a step change in the early 1990s (a ~20% higher mean frequency for 1991–2013 vs 1973–90). The cause is increased subsidence, *not* explained by ENSO or PDO phase. — [Longman/Giambelluca, UH ScholarSpace](https://scholarspace.manoa.hawaii.edu/items/e1c8fbb4-3a45-43f1-a9cd-9c9f8e91d6c5); [Diaz & Giambelluca, NOAA repository](https://repository.library.noaa.gov/view/noaa/14575/noaa_14575_DS1.pdf); [PI-CASC](https://pi-casc.soest.hawaii.edu/research/research-projects/monitoring-climate-variability/)
- **Ducting near Hawaii:** ship radiosondes on Los Angeles–Honolulu runs show elevated ducts with a remarkably consistent layer thickness of about 110 m. ERA5 underestimates duct height and strength in the trade-cumulus region near Hawaii. — [Winning et al., AMT 2024](https://amt.copernicus.org/articles/17/6851/2024/amt-17-6851-2024.pdf)
- The marine-boundary-layer inversion base ranges from about 500 m (east of the subtropical high) to about 2000 m (west/equatorward) across the basin. — [Chen et al. 2016](https://www.soest.hawaii.edu/MET/Faculty/chen/YLChen_2016_AtmRes.pdf)
- **Evaporation duct** heights increase from about 7 m off California to about 15 m near Hawaii. — [Alappattu et al. (NPS Calhoun)](https://calhoun.nps.edu/entities/publication/780fceae-cab4-4cc7-a8e1-555190173aa3)
- A 1960 NRL/NEL "Project Tradewinds" study measured trade-inversion duct propagation from San Diego to Hawaii at 220 and 445 MHz, and found cloud height and type indicate duct altitude and strength. — (cited in the same search result set; [AMT 2024](https://amt.copernicus.org/articles/17/6851/2024/amt-17-6851-2024.pdf) discussion)

### Inferences (PHYSICS: computed here, reproducible)
**Humidity at 915 MHz is negligible.** Gaseous absorption near 1 GHz (ITU-R P.676) is dominated by oxygen at about 0.005–0.006 dB/km; water vapour adds ~10⁻⁴ dB/km. Over a 200 km path the *total* is about 1 dB, and a humid-vs-dry swing changes it by about 0.1 dB. Rain attenuation at 915 MHz (ITU-R P.838) is < 0.001 dB/km even in heavy rain. Vog sulfate aerosol is sub-micron particles at a 33 cm wavelength, so it has no measurable extinction. *Humidity matters only indirectly*, through the refractivity gradient (N depends strongly on water vapour), which drives ducting and super-refraction. Coefficients are from memory of the ITU-R recommendations; I did not fetch them this session. BELIEVED.

**Solar cycle 25 is irrelevant to these links.** The F2-layer critical frequency peaks around 10–15 MHz, so 915 MHz is ~60× above it and passes straight through. Terrestrial line-of-sight paths never reach ionospheric height (≥ 90 km). Low-latitude scintillation affects *satellite* L-band links (GPS), not surface-to-surface 915 MHz. The only solar link is indirect: GNSS-disciplined timing or position on nodes, which has no effect on LoRa decoding. Expected effect: ~0 dB.

**Ducting and inversion: real variability, wrong time-shape.** Mountaintop nodes such as Haleakalā (~3055 m) and Mauna Kea (~4205 m) sit *above* a typical ~2 km TWI. Kaʻala (~1220 m) and coastal nodes sit *below* it. Links that cross the inversion see the strongest refractivity gradient, giving multipath and defocusing. Links within the marine layer can be enhanced by ducting, which produces *fade AND enhancement* episodes on hour-to-day timescales. Signature: SNR on a fixed link swinging by ±10–20 dB and correlating with the Hilo/Lihue radiosonde inversion height, *not* a permanent drop. The documented TWI trend is decadal (+5–6%/decade), so it cannot explain a 2025–26 step. SPECULATION for Hawaii-specific magnitudes.

**Sea-path two-ray fading.** Over water, the specular sea reflection (coefficient near −1 at grazing incidence) creates deep nulls whose positions move with tide, swell, and refractivity (k-factor). A link that worked at k = 4/3 can sit in a null when k drops (sub-refraction). Signature: SNR that varies with tide or time of day on over-water links only.

**Inter-island geometry (approx. distances from standard geography; not sourced this session):**

| Path (channel) | ~Distance | FSPL @915 MHz | 1st Fresnel radius, midpoint | Earth bulge, midpoint (k=4/3) |
|---|---|---|---|---|
| Molokaʻi–Maui (Pailolo) | ~14 km | 114.6 dB | 33.9 m | 2.9 m |
| Oʻahu–Molokaʻi (Kaʻiwi) | ~42 km | 124.1 dB | 58.7 m | 25.9 m |
| Maui–Hawaiʻi (ʻAlenuihāhā) | ~48 km | 125.3 dB | 62.7 m | 33.8 m |
| Oʻahu–Kauaʻi (Kauaʻi Ch.) | ~116 km | 133.0 dB | 97.5 m | 198 m |
| Kaʻala–Haleakalā (mountain-to-mountain) | ~200 km | 137.7 dB | 128 m | 588 m (but both ends ≫ this) |

Formulas: FSPL = 32.44 + 20·log₁₀(f MHz) + 20·log₁₀(d km); r₁ = 17.32·√(d/(4f GHz)) m; bulge = d₁d₂/(12.74·k) m. Radio horizon with k=4/3: d ≈ 4.12(√h₁+√h₂) km. Kaʻala(1220 m)+Haleakalā(3055 m) gives about 372 km, so that path is geometrically clear. Two 10 m-high coastal nodes give only about 26 km, so **beach-to-beach links across the Kaʻiwi or ʻAlenuihāhā channels do NOT have line of sight**. They work only during ducting/super-refraction. That is a structural reason such links appear and disappear (PHYSICS).

**Link budget:** a typical SX1262 node gives +22 dBm TX with 3 dBi antennas each end, so about 28 dB of gain before path loss. Approximate datasheet-class sensitivities: LONG_FAST (SF11/250 kHz) about −134 dBm; MEDIUM_FAST (SF9/250) about −128 dBm; SHORT_FAST (SF7/250) about −122 dBm. At 42 km clear LOS the received level is about −96 dBm, giving ~38 dB margin on LONG_FAST. At 200 km it is about −110 dBm, giving ~24 dB margin on LONG_FAST but only ~12 dB on SHORT_FAST. **Clear-LOS inter-island paths have ample margin.** A loss means obstruction (Fresnel/horizon), a fade/null, a relay disappearing, interference, or a preset change to a faster/less sensitive preset (each SF step down costs about 2.5–3 dB). Sensitivities are from general SX126x knowledge and not fetched this session (BELIEVED). Thermal floor at 250 kHz is −174+54 = −120 dBm, about −114 dBm with a 6 dB NF.

**Noise floor growth (most testable physical cause):** Wi-SUN FAN in the US operates in 902–928 MHz (BELIEVED; domain knowledge, not sourced this session). If HECO's Gridstream/Wi-SUN meters were mass-deployed on Oahu, Maui, and Hawaii Island in 2023–26, every neighborhood gains a dense, chatty 915 MHz mesh. The signature is the node's measured noise floor / RSSI-with-no-packet rising by several dB in populated areas, with rooftop and urban nodes affected and remote mountaintop receivers much less so, plus a loss of weak links only (SNR margin eaten). Same signature for Amazon Sidewalk (900 MHz FSK/LoRa), Helium/LoRaWAN, and UHF RFID. **Measure:** SX126x `GetRssiInst` / meshtasticd noise-floor stats, or an RTL-SDR waterfall at 902–928 MHz, compared against a remote site.

**Remote-node attrition (probably the single most likely cause of "inter-island links lost"):** solar or battery failure (LiPo/18650 capacity fade from heat, typically 2–3 years of life outdoors in the tropics), salt corrosion of N/SMA connectors, and UV-degraded radomes and coax. Signature: specific relay node IDs that go silent, or whose battery telemetry trends down or browns out at night (dropouts that start before dawn), with no change on links that do not route through them. SPECULATION for Hawaii, but it matches the "router nodes going offline" framing. Vegetation growth hurts mostly low-elevation links. A wet canopy adds loss at 915 MHz, so it shows rain-correlated and seasonal SNR drops.

### Gaps
- HECO Gridstream deployment status, meter count, and dates were **not confirmed**: no completion report was found, and the PUC docket was not searched.
- No Hawaii radiosonde (PHTO Hilo / PHLI Lihue) inversion statistics were retrieved for 2025–26. ENSO state for 2025–26 was not searched.
- Amazon Sidewalk, Helium, and LoRaWAN gateway density in Hawaii were not quantified.
- Kīlauea vog levels for 2025–26 were not sourced (irrelevant to attenuation anyway, per physics above).

---

## 4. Regulatory / spectrum changes in 902–928 MHz (2025–Nov 2026)

### Takeaway
**No regulatory change in 2025–Nov 2026 could have affected Hawaiian LoRa links.** NextNav's Lower 900 MHz proposal was still pre-rulemaking (a draft NPRM was at OMB/OIRA in March 2026), with field coexistence tests in Colorado. No Report & Order was found. Rank: lowest.

### Cited Findings
- The FCC approved NextNav's acquisition of 128 more Lower 900 MHz licenses in June 2025; the deal closed in September 2025. — [Business Wire](https://www.businesswire.com/news/home/20250925880846/en); [Location Business News](https://locationbusinessnews.com/nextnav-adds-128-lower-900-mhz-licenses-to-bolster-terrestrial-positioning-assets)
- The FCC sent a draft NPRM on the proposal to OMB/OIRA in March 2026. — [POWER Magazine](https://www.powermag.com/what-utilities-need-to-know-about-the-900-mhz-nextnav-fcc-proceeding/)
- In April 2026 NextNav was cleared for more coexistence field tests (Pueblo, CO, through Oct 2026). — [Broadband Breakfast](https://broadbandbreakfast.com/nextnav-cleared-for-more-coexistence-tests/)
- SpaceX asked the FCC to include the lower 900 MHz band in satellite-to-mobile rules; this is pending. — [Stocktwits](https://stocktwits.com/news-articles/markets/equity/nn-stock-soars-as-space-x-backs-900-m-hz-for-satellite-rules/cZU3QjWRBzE)
- Unlicensed-device coalitions (Connected Devices for America, etc.) oppose the plan over interference. — [Docket Rocket](https://docketrocket.substack.com/p/on-the-record-nextnavs-campaign-for); dockets WT 24-240 / RM-11989

### Inferences
- Even with an order, a 5G/PNT buildout in Hawaii would take years. No signature is expected in 2025–26 data. If NextNav's existing M-LMS TerraPoiNT beacons were present in Hawaii, they would show as fixed, periodic in-band carriers on an SDR. No evidence of Hawaii beacons was found (SPECULATION).

### Gaps
- Docket status after about April 2026 could not be confirmed; the Q3 2026 earnings transcript host was blocked. The report writer should state "no order found as of these sources", not "no order exists".

---

## 5. Inter-island path physics summary

### Takeaway
High-site to high-site links (Kaʻala, Haleakalā, Mauna Kea, Molokaʻi ridges) are geometrically clear with 20–40 dB of margin on LONG_FAST. Low coastal links across the 40–50 km channels are **beyond the radio horizon** and depend on anomalous propagation. Losing even one well-sited relay, or moving to a faster preset, removes a whole inter-island hop. See the table and budget in §3 (PHYSICS; formulas given so they can be re-derived).

### Cited Findings
- The duct environment near Hawaii is documented (elevated duct about 110 m thick; evaporation duct about 15 m). — [Winning et al. 2024](https://amt.copernicus.org/articles/17/6851/2024/amt-17-6851-2024.pdf); [Alappattu et al.](https://calhoun.nps.edu/entities/publication/780fceae-cab4-4cc7-a8e1-555190173aa3)
- In a 2026 non-Hawaii city mesh, SHORT_FAST lost range and reliability compared with MEDIUM_FAST. — [discussion #9437](https://github.com/meshtastic/firmware/discussions/9437)

### Inferences
- A 15 m evaporation duct can, at times, carry 915 MHz between low coastal nodes well beyond the horizon. Such links are intermittent by nature, so "it used to work" may have been the best case.

### Gaps
- Exact Hawaii node coordinates and heights are unknown, so the table uses generic channel widths.

---

## 6. Evidence-ranked candidate causes and their measurement signatures

### Takeaway
Ranking, given **zero Hawaii-specific corroboration**. Ranks rest on mechanism strength and fit to "diminished recently", not on local evidence:

| Rank | Cause | Evidence tier | Signature that confirms it | Signature that refutes it |
|---|---|---|---|---|
| 1 | Relay/infrastructure attrition (solar/battery/corrosion, node removed) | SPECULATION; mechanism common | specific relay IDs silent; battery voltage trending down; dawn dropouts | all relays still heard with same SNR |
| 2 | Congestion / role misuse / MQTT flood / 2.7.x ROUTER_LATE scaling exemption | Mechanism EVIDENCE (Meshtastic docs, #9437); no Hawaii data | ChUtil >25–30%; step at a firmware upgrade date; `via_mqtt` share up; SNR flat | ChUtil <10–15% |
| 3 | Community/infrastructure split (MeshCore migration, preset/slot change, faster preset) | SPECULATION | fewer distinct neighbors and relays, traceroutes shorter or failing, SNR and noise flat | neighbor census unchanged |
| 4 | 915 MHz noise-floor rise (Wi-SUN AMI, Sidewalk, LoRaWAN) | HECO Gridstream/Wi-SUN EVIDENCE; deployment timing UNKNOWN | noise floor up several dB at urban nodes vs remote; weak links lost first | noise floor flat vs a 2024–25 baseline |
| 5 | Tropospheric/ducting variability, sea-path fading | Climatology EVIDENCE; decadal trend, not a step | SNR swings correlate with radiosonde inversion, tide, and diurnal cycle | stable SNR on a fixed over-water link |
| 6 | Vegetation, antenna/coax degradation | SPECULATION | slow monotonic SNR loss at one node; VSWR up | stable SNR |
| 7 | Regulatory (NextNav) | EVIDENCE that no order exists in these sources | — | — |
| 8 | Humidity/vog, solar cycle | PHYSICS: ~0.1 dB and ~0 dB | — | — |

### Cited Findings
- (See §§1–5; every row's mechanism is sourced there.)

### Inferences
- The fastest triage uses the operator's own instruments: (1) a per-node last-heard and battery census, (2) channel utilization and air-util history, (3) noise-floor history, (4) SNR time series on one fixed inter-island link against Hilo radiosonde inversion height. Measure at BOTH ends of the link.

### Gaps
- All ranks are UNVERIFIED for Hawaii. Upgrading any row requires the measurements above, or a Hawaii node-count time series (meshmap.net / MQTT archive), which was not retrieved.
