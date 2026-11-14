# Community, press and independent-builder view of LoRa / Meshtastic on 2 m (144 MHz)

Researched 2026-11-14. Method note: about 18 search/fetch calls. GitHub pages were fetched and read in full. nodakmesh.org, hamradio.my, meshcore.at, electronics-lab.com and wiki.lilygo.cc could not be fetched from this session (DNS/allowlist block), so every claim from those hosts below comes from **search-result summaries only** and is marked [snippet]. Reddit, QRZ, Discord and YouTube did not come up in any search, so the community-forum view is under-sampled (see Gaps).

Evidence labels: **MEASURED** = someone reports a field or bench observation. **CLAIM** = vendor or author statement with no data. **CODE** = what was merged or written.

---

## 1. Has anyone run LoRa on 2 m with SX127x or other chips? Range/SNR, antennas, power, BW, SF?

### Takeaway
Yes, but almost all public evidence is "it transmits and decodes" anecdote. I found no published 2 m LoRa field result (range, SNR, link budget) from any builder. The only quantified 2 m LoRa numbers I found are a 2024 blog post that appears to give *estimates*. Meshtastic's own 2 m regions (June 2026) report regression testing only, with no range data.

### Cited Findings
- **2025-02-12/13, SQ6RDP (GitHub, LoRa_APRS_iGate discussion #250).** SQ6RDP reports that a LILYGO T3 V1.6.1 "144-148MHz" board (SX1278) "works and transmits" with only a frequency change ("load&play"). He notes the board has **no balun or filter**. He says he is deliberately *not* sharing 144 MHz details publicly because he believes amateur rules restrict 144 MHz activity; his goal was a 1296 MHz transverter IF. The maintainer (richonguzman) said he had never tried the 144 MHz version. No SF/BW/CR, range, SNR, power or antenna data was posted. — [GitHub discussion #250](https://github.com/richonguzman/LoRa_APRS_iGate/discussions/250) (MEASURED only as "it TXes"; anecdotal)
- **2024-09, Hamradio.my, "Exploring LoRa APRS: VHF vs UHF Performance" [snippet; older than 2025].** Gives a VHF LoRa APRS configuration of 144.415 MHz, SF8, 10.4 kHz BW and 20 dBm, with range figures of about 15–20 km line-of-sight, about 10 km semi-rural and about 3 km urban. The search summary characterises these as **estimates**. I could not fetch the page to confirm whether they were measured. — [Hamradio.my](https://hamradio.my/2024/09/exploring-lora-aprs-vhf-vs-uhf-performance/)
- **2022, VECTOR (vectorradio.ca) [snippet; old].** Planned a range comparison of LoRa 433 MHz at 0.1 W against conventional APRS at 144 MHz and 5 W. The comparison is not LoRa *on* 2 m, and no results were surfaced. — [VECTOR](https://vectorradio.ca/post/2022/loratest/)
- **LR2021 reverse-engineering repo (beala/lr2021-re, undated).** Claims "The chip's raw IQ TX path can transmit 2 m APRS that a Kenwood handheld decodes". This is **AFSK/FM APRS synthesized via IQ, not LoRa modulation**, and the LR2021 is rated only down to 150 MHz (see §2). It gives no measurement detail; it points to `docs/rate-bench.md`, which I did not read. — [beala/lr2021-re](https://github.com/beala/lr2021-re) (CLAIM, out-of-spec)
- **2026-06-03, Meshtastic PR #10623 (vidplace7).** The test attestation covers only `tlora-v2-1-1_6` 144 MHz and `t-beam-bpf` "for regressions". It contains no range or SNR data. The 2 m channel design is **15.6 kHz BW (TINY_FAST default), padded to 20 kHz** "to fit cleanly into ham band plans". — [PR #10623](https://github.com/meshtastic/firmware/pull/10623) (CODE)
- **Chip fact.** Semtech's table lists the SX1278 as covering 137–525 MHz, which includes 144–148 MHz. — [Semtech LoRa product guide PDF](https://www.semtech.com/uploads/design-support/SEMTECH_LORA_PG.pdf)
- **Background, not 2 m.** An IOP conference paper reports an SX1278 reaching about 1 km in its test (433 MHz band). It is not a 2 m datum. — [IOP paper](https://iopscience.iop.org/article/10.1088/1757-899X/318/1/012053/pdf)

### Inferences
- The hardware path exists: SX1278-based 144–148 MHz LilyGO boards ship and transmit. The **public** record of what LoRa actually achieves on 2 m is essentially empty. Any range claim for 2 m Meshtastic in late 2026 should be treated as an extrapolation from physics, not field data.
- Meshtastic's 15.6 kHz TINY presets on 2 m mean very low data rates. Physics is the floor here: better free-space path loss at 144 MHz versus 915 MHz (about 16 dB, from 20·log10(915/145)) has to be weighed against antenna size and the higher VHF noise floor. *This is my physics inference, not a sourced measurement.*

### Gaps
- No Reddit, QRZ, YouTube or Discord field reports surfaced in searches. Whether operators have posted T-Beam BPF range tests since the July 2026 merge is **UNKNOWN**.
- I could not fetch the Hamradio.my page to confirm measured versus estimated.

---

## 2. Announced or shipping VHF LoRa boards and modules, and Meshtastic on them

### Takeaway
Two LilyGO 144–148 MHz SX1278 boards are shipping and now have Meshtastic support: the T-Beam BPF (with a high-power PA) and the T3/TLora V1.6.1 144 MHz. Ebyte's 170 MHz E22 modules use the SX1262 and sit *above* 2 m (150–173 MHz). Semtech's LR2021 starts at 150 MHz. MeshCore's April 2026 floor is also 150 MHz. So in 2026 the 2 m LoRa ecosystem is effectively **SX1278-only**.

### Cited Findings
- **LilyGO T-Beam BPF.** An ESP32-S3 board with an SX1278 and a 144–148 MHz band-pass filter. The vendor FAQ says the filter reduces out-of-band harmonics from "the high-power LoRa transmitter". Sources disagree on the chip: the quick-start page says SX1262, while the wiki and product listing say SX1278. — [LilyGO product page](https://lilygo.cc/products/t-beam-bpf); [OpenELAB listing "SX1278 BPF (144-148 MHZ)"](https://openelab.com/products/lilygo-t-beam-bpf-lora); [LilyGO wiki [snippet]](https://wiki.lilygo.cc/products/t-beam-series/t-beam-bpf/); [Quick start [snippet]](https://wiki.lilygo.cc/products/t-beam-series/t-beam-bpf/quick-start.html)
- **Meshtastic PR #10558, "Add T-Beam BPF - 144-148 Mhz LoRa".** By vidplace7 with caveman99. Opened 2026-05-26, merged to `develop` 2026-07-27 (commit a640d44), approved by thebentern. The code summary says ESP32-S3 + SX1278 (RF95). The **power figures conflict within the PR**: the description says "+27 dBm radio", a commit says "This is a 5-6W radio!! Add TX_GAIN_LORA", and the original title said "~37 dBm (5 Watts)" until it was renamed on 2026-08-30. The PR contains no field test data. — [PR #10558](https://github.com/meshtastic/firmware/pull/10558)
- **Meshtastic 2.8 punchlist (#10462).** Opened by NomDeTom on 2026-05-12 and closed as complete on 2026-10-06. A 2026-07-22 develop snapshot (AI-generated comment by Nullvoid3771) lists "~144 MHz 2 m, 1.25 m, 70 cm" regions and "T-Beam Supreme 144 MHz" as new LilyGO hardware. — [Issue #10462](https://github.com/meshtastic/firmware/issues/10462)
- **Hamvention 2026 web flasher.** Meshtastic's hamvention.meshtastic.org flasher lists the T-Beam BPF as "Amateur radio band — an amateur radio license is required to transmit". This suggests an event-tied push. No talk transcript was found. — [hamvention.meshtastic.org [snippet]](https://hamvention.meshtastic.org/)
- **Ebyte E22-170 series.** The E22-170T30S/T33S cover 150.125–173.125 MHz (default 170.125 MHz). The E22-170M33S is SPI SX1262, 150.125–170.125 MHz. All are **above 2 m**. — [Ebyte news 1260](https://www.cdebyte.com/news/1260); [ebyteiot E22-170M33S](https://ebyteiot.com/products/ebyte-e22-170m33s-23dbm-16km-170-125mhz-spi-sx1262-module)
- **Ebyte E32-170 (UART SX1278).** An old Meshtastic Discourse thread (about 2020, flagged old) reports a user using an E32-170T30D for a cave link. It says there was "no actual Meshtastic support" for the UART module. — [Meshtastic Discourse](https://meshtastic.discourse.group/t/antenna-improved-range/227?page=5)
- **Semtech LR2021 (announced 2025-03).** The datasheet gives sub-GHz coverage of **150–960 MHz**, so 144–148 MHz is out of spec. Semtech markets it as Meshtastic-compatible "with third-party stack offerings". Community projects (TheRaviolimeister/LR2021-Node) run it at 868/433/2.4 GHz, not 2 m. A Meshtastic discussion (#8769) covers multi-SF on the LR2021. — [Semtech press release](https://www.semtech.com/company/press/semtech-launches-lora-plus-lr2021-transceiver-with-lora-gen-4-technology); [LR2021 datasheet (DigiKey)](https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/8945/600_LR202.pdf); [CNX Software 2025-03-13](https://www.cnx-software.com/2025/03/13/semtech-lr2021-lora-plus-transceiver-supports-lora-gen-4-amazon-sidewalk-meshtastic-wm-bus-wi-sun-fsk-and-z-wave/); [LR2021-Node](https://github.com/TheRaviolimeister/LR2021-Node); [Discussion #8769](https://github.com/meshtastic/firmware/discussions/8769)
- **SX1262 floor.** A third-party write-up notes that Semtech rates the SX1262 (most current nodes) from 150 MHz, below 2 m. This is why the 2 m work keys on SX1278 boards. — [NodakMesh ham-mode post [snippet]](https://nodakmesh.org/blog/meshtastic-ham-mode-amateur-regions)
- **NiceRF / HopeRF.** NiceRF publishes SX1278 module material (the chip covers 137–525 MHz). I found no NiceRF or HopeRF module marketed specifically for 144 MHz with Meshtastic. — [NiceRF SX1278 page](https://www.nicerf.com/company/lora-module-sx1278.html)

### Inferences
- The only paths to 2 m today are SX1276/77/78-family boards, which are legacy chips relative to the SX1262 and LR11xx/LR20xx. That limits the 2 m Meshtastic hardware pool to LilyGO's SX1278 variants unless Semtech ships a part with a lower floor.
- The 5–6 W (37 dBm) figure would make the T-Beam BPF far hotter than any ISM Meshtastic node. At that power, the BPF is a regulatory necessity rather than a nicety. Because the PR's own text conflicts (27 dBm vs 37 dBm), the actual output is **UNKNOWN** until someone measures it.

### Gaps
- No independent RF measurement (output power, harmonics) of the T-Beam BPF was found.
- I found no Heltec or RAK 2 m product. The search did not cover their catalogs exhaustively.
- Whether "T-Beam Supreme 144 MHz" and "TLora32 144 MHz" actually ship as SKUs is **UNKNOWN** beyond the AI-generated punchlist snapshot and PR #10623's mention.

---

## 3. What Meshtastic team members and the press say about 2 m plans (dates, quotes)

### Takeaway
The team's "statements" on 2 m are in code and PRs (vidplace7, caveman99, thebentern, NomDeTom; May–July 2026) plus a Hamvention 2026 flasher. I found no talk, podcast or interview quote. The press and blog coverage found is enthusiast mesh-community blogs (NodakMesh), not ARRL, QST or Hackaday.

### Cited Findings
- vidplace7, PR #10623 (2026-06-03): 2 m regions were split from the BPF PR because "It was getting pretty large", to "unblock work on T-Beam Supreme and TLora32 144mhz versions". Default slots are "(hopefully) appropriate": ITU1 slot 26 = 144.510 MHz, ITU2 slot 51 = **145.010 MHz**, ITU3 slot 33 = 144.650 MHz. NomDeTom approved with "LGTM". The PR itself contains **no discussion of the band plan** (APRS, satellites, repeaters). — [PR #10623](https://github.com/meshtastic/firmware/pull/10623)
- 2.8 snapshot (2026-07-22): "Licensed nodes are prevented from rebroadcasting traffic to/from unlicensed users." Ham Mode work was also added in BaseUI. — [Issue #10462](https://github.com/meshtastic/firmware/issues/10462)
- NodakMesh blog [snippet]: Ham Mode sets the callsign to the placeholder N0CALL with the transmitter kept off; entering a real callsign does not re-enable TX, so the operator must also turn Transmit back on. The blog says Meshtastic 2.8 changes the fact that US hams were previously stuck in crowded 902–928 MHz. It also concedes that for local repeaters and simplex on 2 m/70 cm, "a dual-band handheld is still the tool". — [NodakMesh ham-mode](https://nodakmesh.org/blog/meshtastic-ham-mode-amateur-regions); [NodakMesh 2.8 what's new](https://nodakmesh.org/blog/meshtastic-2-8-whats-new)
- Meshtastic blog "Let's talk about 2.8" [snippet]: 2.8.0 was revoked and replaced by a 2.8.1 alpha, "not ready for wide deployment". — [meshtastic.org blog](https://meshtastic.org/blog/lets-talk-about-2-8/)
- Hackaday (2023-06-26, old): its Meshtastic article notes that amateurs can't use encryption. I found no Hackaday piece on 2 m LoRa. — [Hackaday](https://hackaday.com/2023/06/26/meshtastic-for-the-greater-good/)

### Inferences
- The 2 m capability was driven by a small set of contributors (vidplace7 primarily) as a hardware bring-up for a LilyGO board. That is a different provenance from a project-level announcement.

### Gaps
- No DEF CON, DCG, podcast, YouTube or Discord quote from team members on 2 m was found.
- No ARRL news or QST coverage of Meshtastic on 2 m was found.
- No Reddit r/meshtastic or r/amateurradio threads surfaced in searches; community reaction is **UNKNOWN**.

---

## 4. Stated objections and concerns

### Takeaway
The concerns that are actually *stated* in what I found are: encryption is banned in ham mode, ham-mode nodes are incompatible with the normal mesh, licensing is required, and (from one European ham) a belief that 144 MHz rules restrict this kind of activity. I found no published objection about APRS, satellite or repeater interference specific to 2 m LoRa. The band-plan fit of the default 145.010 MHz slot is undiscussed in the PR.

### Cited Findings
- Licensed/ham mode disables encryption ("encryption is illegal on amateur bands"), and HAM mode is not compatible with the normal mesh. — [lorameshatlas.com guide [snippet]](https://lorameshatlas.com/guides/meshtastic-legal-usa/); [Meshtastic LoRa config docs](https://meshtastic.org/docs/configuration/radio/lora/)
- Meshtastic docs say to check your national band plan; the docs give 2 m region spans of 144–146 MHz (ITU1) and 144–148 MHz (ITU2/3). — [Meshtastic LoRa config](https://meshtastic.org/docs/configuration/radio/lora/)
- SQ6RDP (2025-02) withheld 144 MHz details publicly, citing a belief that amateur rules restrict 144 MHz activity. — [Discussion #250](https://github.com/richonguzman/LoRa_APRS_iGate/discussions/250)
- A congestion argument (keep emissions narrow; use an experimental sub-band) exists on GitHub for **70 cm**, not 2 m (loraham issue #14, travisgoodspeed). — [loraham #14 [snippet]](https://github.com/travisgoodspeed/loraham/issues/14)
- An older Arduino Forum opinion (about 2020, flagged old) argues that 2 m APRS already gives better range and works with existing ham gear. It is opinion, not measurement. — [Arduino Forum](https://forum.arduino.cc/t/talking-to-a-kiss-tnc-ham-radio-aprs/675525/9)
- Region APRS frequencies are 144.39 MHz (US) and 144.800 MHz (Europe, cited in the Reticulum portable-server project). — [Ham Radio School](https://www.hamradioschool.com/post/what-frequency-do-i-use-on-2-meters); [meezenest Reticulum field server](https://meezenest.nl/mees/projects/reticulum_field_server/build_doc/index.html)

### Inferences
- These points are **unsourced background, my knowledge; verify against the ARRL band plan**. The US default 145.010 MHz sits in the 145.0x area traditionally used for packet/digital in many US local band plans. The ITU1 default of 144.510 MHz and Region 1's 145.8–146.0 satellite segment would need IARU-R1 band-plan checks. The PR does not engage with any of this, so band-plan fit is an open question rather than a resolved one.
- With a 5 W-class PA on SX1278 boards, the antenna is roughly 50 cm (quarter-wave) and harmonic or interference concerns are real. The only mitigation in the record is the vendor BPF claim.

### Gaps
- No ARRL, IARU, AMSAT or repeater-council statement on LoRa/Meshtastic at 2 m was found.
- No reported interference incident was found. This is absence of evidence, not evidence of absence.
- ID requirements (callsign transmission interval) for Meshtastic ham mode on 2 m were not found in community sources.

---

## 5. RNode / Reticulum and MeshCore on VHF

### Takeaway
I found no RNode build or doc that runs LoRa on 144 MHz. Community material lists RNode bands as 433/868/915/2.4 GHz, and the one 2 m Reticulum project uses a separate conventional 2 m radio for APRS. MeshCore 1.15.0 (April 2026) lowered its floor to **150 MHz** (aimed at 170 MHz E22 modules), which is still above 2 m. I found no MeshCore 144 MHz build.

### Cited Findings
- The RNode community wiki says RNodes "generally operate in the 433 MHz, 868 MHz, 915 MHz and 2.4 GHz bands". — [Reticulum community wiki](https://reticulum.miraheze.org/wiki/RNode)
- The Reticulum manual (release 1.6.0, dated 2026-11-07 per the search result) calls RNode suitable for VHF and UHF in general terms. Its example config uses 867.2 MHz. — [Reticulum manual PDF](https://reticulum.network/manual/Reticulum%20Manual.pdf); [Interfaces doc](https://reticulum.network/manual/interfaces.html)
- The meezenest Reticulum portable server pairs a LoRa32 RNode (433.775 MHz) with a separate 2 m transceiver for APRS on 144.800 MHz. The 2 m link there is not LoRa. — [meezenest](https://meezenest.nl/mees/projects/reticulum_field_server/build_doc/index.html)
- MeshCore 1.15.0 (2026-04-19) [snippet]: "Extended Frequency Range", lowered to 150 MHz with modules like Ebyte's E22-170M22S in mind; the release notes tell users that regulatory compliance is their own responsibility. MeshCore CLI docs list a 300–2500 MHz tempradio range, which conflicts with this and may predate 1.15.0. — [meshcore.at 1.15.0 news [snippet]](https://meshcore.at/en/news/fw-1-15-0); [NodakMesh MeshCore 1.15 [snippet]](https://nodakmesh.org/blog/meshcore-firmware-v1150-release); [MeshCore CLI docs [snippet]](https://docs.meshcore.io/cli_commands/)
- Other LoRa ham meshes found are on other bands: QMesh (KG5VBY, LoRa voice mesh) on 70 cm 410–493 MHz, and WARS Birdhouse on 33 cm. — [QMesh hackaday.io](https://hackaday.io/project/161491-qmesh-a-lora-based-voice-mesh-network); [WARS-Birdhouse](https://github.com/brucemack/WARS-Birdhouse)

### Inferences
- RNode firmware's SX127x support makes a 144 MHz RNode plausible on the LilyGO T3 144 MHz board in principle. I found **no evidence** anyone has done it or that RNode_Firmware lists that board. As of late 2026, Meshtastic appears to be the only one of the three stacks with an explicit 2 m path.
- I found nothing on Disaster Radio at VHF. The project is not known to target 2 m (unverified).

### Gaps
- I did not search RNode_Firmware issues for "144" or "BPF". An RNode 2 m port is **UNKNOWN**, not ruled out.
- I could not fetch the MeshCore 1.15 changelog to confirm the exact floor (150 MHz vs 137 MHz).
