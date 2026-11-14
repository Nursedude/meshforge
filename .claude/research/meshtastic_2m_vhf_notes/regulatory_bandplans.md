# Regulatory & Band-Plan Landscape: LoRa Mesh (Meshtastic) on the 2 m Amateur Band (144-148 MHz)

> Research date 2026-11-14. Not legal advice. **Method caveat:** In this session WebFetch couldn't reach any host (ecfr.gov, law.cornell.edu, federalregister.gov, meshtastic.org and nodakmesh.org all failed DNS in the sandbox), so I never opened a primary document in full. Every finding below comes from search-result snippets and summaries of the cited pages. Treat quoted rule text as **BELIEVED, not VERIFIED**. Re-check against the eCFR / regulator PDF before putting it in firmware or operator guidance. One search, on 97.113/97.119/97.221, errored and returned nothing, so those rules are cited from general knowledge and flagged.

---

## Q1. Allocations by ITU region and country

### Takeaway
ITU Region 1 has 144-146 MHz. Regions 2 and 3 generally have 144-148 MHz, with national exceptions; Japan, for example, is believed to be 144-146 MHz. A single "2 m" firmware region therefore has to be split per ITU region, as Meshtastic 2.8 now does. Meshtastic's docs list ITU R1 2 m as 144.0-146.0 and ITU R2/R3 as 144.0-148.0.

### Cited Findings
- Meshtastic's LoRa configuration docs list the ITU Region 1 amateur 2 m entry as 144.0-146.0 MHz and ITU Regions 2 and 3 as 144.0-148.0 MHz — [Meshtastic LoRa Configuration](https://meshtastic.org/docs/configuration/radio/lora/) (via search summary)
- The RSGB/UK plan covers "144 MHz Band: 144.0 – 146.0 MHz" (Region 1) — [RSGB 144 MHz band plan](https://rsgb.org/main/operating/band-plans/vhf-uhf/144mhz-band/)
- US: 144.1-148.0 MHz is listed in §97.305(c), with emission standards in §97.307(f)(2), (5) and (8). 144.0-144.1 is CW-only (see Q2/Q3) — [govinfo CFR 2025 ed. §97.307](https://www.govinfo.gov/content/pkg/CFR-2025-title47-vol5/pdf/CFR-2025-title47-vol5-sec97-307.pdf); [FCC 23-93](https://docs.fcc.gov/public/attachments/FCC-23-93A1.pdf)
- Canada: RBR-4 Issue 3 (July 2022) lists **144.000-148.000 MHz, 30 kHz maximum bandwidth**, Basic qualification. The older Issue 1 listed 144.000-146.000 MHz at 30 kHz — [RBR-4 Issue 3 excerpt (UNB mirror)](https://www.ece.unb.ca/petersen/amateur-radio-files/RBR04e-Operating-Standards-2022-Jul.pdf); [RBR-4 excerpt](https://www.ece.unb.ca/petersen/amateur-radio-files/supplement-1-doc-pages-from-B-Petersen-regulations-bands-regions-excerpt-from-RBR04e-2022-Jul.pdf)
- Canada: a 2021 CARAB record shows that ISED's first-priority item was revising RBR-4, and RAC later reviewed a final draft. I found no text for a post-2022 issue — [CARAB meeting 2021](https://www.rac.ca/mivahih/2021/07/CARAB-Meeting-JA2021TCA.pdf); [RAC Regulatory Priorities](https://www.rac.ca/?p=34635)
- Australia: ACMA says amateurs have primary status in "the 52–54 and 144–148 MHz bands" — [ACMA amateur operating procedures](https://www.acma.gov.au/amateur-radio-operating-procedures)
- Australia: a 2018 WIA submission to the ACMA LCD review lists 144-148 MHz with "any emission mode whose necessary bandwidth is no greater than 100 kHz". This is a 2018 proposal table, **not confirmed as the current LCD** — [WIA LCD submission 2018](https://www.wia.org.au/newsevents/news/2018/20180612-1/documents/2018-06-12%20WIA%20-%20LCD%20Submission%20Part%204%20(Advanced)%20V3.3.pdf)
- Australia: the current WIA band plan is the 2026 revision. Its 2 m section was not visible in the results — [WIA Australian Band Plan 2026](https://www.wia.org.au/members/bandplans/data/documents/WIA%20Australian%20Band%20Plan%202026.pdf)
- Australia: the ACMA calls the band plan "a voluntary agreement between amateur radio operators" — [ACMA amateur operating procedures](https://www.acma.gov.au/amateur-radio-operating-procedures)

### Inferences
- A firmware region table needs at least three 2 m entries: R1 (144-146), R2 (144-148, and in the US effectively 144.1-148 for data), and R3 (144-148 by default, with a Japan override at 144-146 if confirmed).
- Per-country bandwidth caps differ a lot: US 20 kHz for unspecified digital codes; Canada 30 kHz; Australia possibly 100 kHz (unconfirmed). The firmware should carry the per-country bandwidth cap, not only the frequency edges.

### Gaps
- **UK Ofcom licence terms**: not retrieved. Region 1 gives 144-146 MHz. The licence's bandwidth and encryption clauses were not confirmed this session.
- **Germany / CEPT (AFuV, BNetzA)**: not retrieved. Assumed to be 144-146 MHz (Region 1), not verified.
- **Japan's legal allocation**: I believe it is 144-146 MHz (MIC 使用区別). The search showed JARL plan segments only up to 145.x and didn't state the band edge.
- **New Zealand (RSM GURL)**: nothing found. This is an open gap.
- **Australian LCD 2024/2025 current text**: not retrieved. The 100 kHz figure is from 2018.

---

## Q2. Band plans: where a LoRa channel could fit

### Takeaway
In every region the only plausible homes are the existing narrowband or "wideband" digital/packet/experimental segments: ~144.9-145.1 in the US, 145.59-145.79 "Wide Band Digital" in Canada, 144.794-144.990 (12 kHz) in UK/R1, and 144.60-144.70 "wideband data" in Japan. Keep clear of 144.0-144.3 weak-signal, the APRS frequencies 144.390 (NA) and 144.800 (R1), the 145.8-146.0 satellite segment, and repeater/FM-simplex channels. A 125 kHz or 62.5 kHz channel doesn't fit any of the R1/UK segments. It would only fit a wideband digital segment where national rules allow the bandwidth, which is not the US.

### Cited Findings
- **Satellite**: IARU coordinates the amateur-satellite service at 145.800-146.000 MHz — [IARU satellite coordination in 2 m](https://www.iaru.org/wp-content/uploads/2019/12/satellite_frequency_coordination_in_the_two.pdf). Region 1 confirmed AMSAT use of 145.8-146.0 (reconfirmed 1981) — [UBA band planning 144-146](https://old.uba.be/en/organisations/iaru/iaru-bandplanning/bandplanning-between-144-and-146-mhz)
- **Canada (RAC plan)**: "145.590 – 145.790 • Wide Band Digital Modes"; "145.790 – 145.800 • No transmissions. Guard Band to protect Satellite Sub-band"; 145.500-145.590 reserved for "ARISS Links – Space Communication Exclusive" — [RAC 2 m band plan](https://www.rac.ca/?p=4072)
- **US**: APRS sits on 144.390 MHz in North America, plus Colombia, Chile, Indonesia, Malaysia and Thailand — [sigidwiki APRS](https://sigidwiki.com/wiki/APRS)
- **US**: unofficial copies of the ARRL plan give 144.9-145.1 to weak signal / FM simplex, with 145.01-145.09 "widely used for packet". TARPN notes that this packet use dates from the 1990s and may be less common now — [TARPN FAQ](https://tarpn.net/t/faq/faq_bands_for_packet.html); [qsl.net kc0mdi](https://www.qsl.net/kc0mdi/2mbanpl.html)
- **US**: an unofficial list labels 145.50-145.80 "Miscellaneous and experimental modes". This conflicts with RAC's ARISS reservation at 145.5-145.59. I did not retrieve the ARRL's own page — [ARRL band plan](https://arrl.org/band-plan)
- **ARRL**: a band plan is a voluntary division of the band, and "locally coordinated plans always take precedence" over national recommendations — [ARRL band plan](https://arrl.org/band-plan)
- **UK / R1 (RSGB 2024 plan)**: 144.794-144.990 MHz, necessary bandwidth 12 kHz, for "machine-generated modes / digital communications". A sub-entry, 144.800-144.9875, covers digital modes "including unattended". Repeater inputs start at 144.9900 MHz. This came from an unofficial mirror of the RSGB plan — [m7spi RSGB 2m plan mirror](https://www.m7spi.co.uk/united-kingdom-band-plan-2m); [RSGB RadCom 2024 Band Plans](https://rsgb.org/main/files/2024/02/RadCom_2024-BandPlans.pdf)
- **UK**: APRS is on 144.800 MHz with narrow deviation (2.5 kHz peak) requested, and 144.8125 is assigned to DV gateways — [RSGB "Check your APRS deviation"](https://rsgb.org/main/?p=13878)
- **IARU R1 VHF Handbook**: v10.02 (March 2024) is the latest edition found. Its 144-146 table could not be read — [VHF Handbook V10.02](https://erau.ee/images/ULL/VHF_Handbook_V10_02.pdf)
- **Japan (JARL 2023 plan)**: 144.10-144.20 is narrowband all-mode (EME allowed) and 144.20-144.50 is narrowband all-mode (FT8/FT4). JARL keeps "144.60-144.70MHz…「広帯域データ」区分" (wideband data), although the MIC notice permits all modes there. 145.00 is the calling/emergency frequency. 145.30 MHz is recommended as the digital calling frequency — [JARL band plan 2023-09-25](https://www.jarl.org/Japanese/A_Shiryo/A-3_Band_Plan/bandplan20230925.pdf); [JARL summary](https://www.jarl.org/Japanese/A_Shiryo/A-3_Band_Plan/bandplan20230925_gaiyou.pdf). A 2025-07-17 revision exists, but its 144 MHz text wasn't visible — [JARL 2025-07-17](https://www.jarl.org/Japanese/A_Shiryo/A-3_Band_Plan/bandplan20250717.pdf)
- **Meshtastic 2.8 default**: the 2 m region (R2) uses 144-148 MHz with the TinyFast preset at **145.010 MHz**. Per a third-party blog, the 2 m presets "TinyFast and TinySlow" are **15.6 kHz LoRa bandwidth, padded to 20 kHz channels** — [NodakMesh ham-mode post](https://nodakmesh.org/blog/meshtastic-ham-mode-amateur-regions) (snippet only; 145.010 not verified against firmware source)

### Inferences
- 145.010 MHz in the US falls on the legacy 145.01 packet channel inside the 144.9-145.1 simplex/packet area. That's a reasonable band-plan fit for a 20 kHz channel. Local coordinators can override it.
- R1/UK: a 15.6 kHz LoRa signal already exceeds the RSGB 12 kHz "necessary bandwidth" for 144.794-144.990. Only LoRa BW 7.8 or 10.4 kHz would fit that segment as written. 15.6 kHz would need a different spot, such as an FM simplex channel used as 12.5/25 kHz, or national-society agreement. **This conflicts with a single "R1 = TinyFast 15.6 kHz" default.**
- Canada's 145.59-145.79 "Wide Band Digital" segment (200 kHz) could physically hold a 125 kHz channel, but RBR-4 caps emissions at 30 kHz. Regulation, not the plan, limits it to LoRa BW ≤ 20.8 kHz (31.25 kHz is marginally over 30 kHz).
- Japan's 144.60-144.70 "wideband data" segment (100 kHz) is the natural JA home. 62.5 kHz could fit physically there. Japanese licensing (station licence listing emission designators, 技適) is a separate hurdle I didn't research.

### Gaps
- I couldn't read the current ARRL 2 m plan text directly. Its 145.5-145.8 "experimental" label is from secondary sources and conflicts with RAC.
- I couldn't read the IARU R1 VHF Handbook 10.02 144 MHz table, so I couldn't check whether R1 has a "wideband/experimental" slot above 12 kHz.
- No Region 3 (other than Japan) or WIA 2026 2 m segment detail.

---

## Q3. US Part 97: is a 125 kHz LoRa signal legal on 2 m, and what changed in 2023-2026?

### Takeaway
No. As of the latest text found (CFR 10-1-25 edition, with no later VHF order found), 144.1-148.0 MHz data is governed by §97.307(f)(5). Specified digital codes are capped at **19.6 kilobaud**, and unspecified digital codes, which is where LoRa falls, are capped at **20 kHz bandwidth**. In practice this forces LoRa BW ≤ 15.6 kHz. 20.8 kHz nominally exceeds 20 kHz. The Nov 2023 FCC order (FCC 23-93) removed baud limits **only on HF** (replaced with 2.8 kHz). VHF/UHF removal was only *proposed* in the accompanying FNPRM, and no final order was found.

### Cited Findings
- Prior and current rule wording as described by the FCC: "in the 6 meter (50-54 MHz) and 2 meter (144-148 MHz) bands, the specified digital codes may be used with a symbol rate not exceeding 19.6 kilobauds, and unspecified digital codes may be used with a bandwidth not exceeding 20 kilohertz" — [FCC 16-96 NPRM (2016)](https://docs.fcc.gov/public/attachments/FCC-16-96A1.pdf); [FR 2016-19085](https://www.govinfo.gov/content/pkg/FR-2016-08-12/pdf/2016-19085.pdf); restated in [FCC 23-93](https://docs.fcc.gov/public/attachments/FCC-23-93A1.pdf)
- Current eCFR/CFR §97.307(f)(5) summary: the specified digital codes listed in §97.309(a) may be transmitted with symbol rate not exceeding 19.6 kilobauds, and unspecified digital codes under §97.309(b) may be transmitted with authorized bandwidth 20 kHz. §97.305(c) lists 144.1-148.0 MHz with standards §97.307(f)(2), (5), (8) — [govinfo CFR 2025 §97.307](https://www.govinfo.gov/content/pkg/CFR-2025-title47-vol5/pdf/CFR-2025-title47-vol5-sec97-307.pdf); [eCFR §97.307](https://www.ecfr.gov/current/title-47/chapter-I/subchapter-D/part-97/subpart-D/section-97.307) (search summary, not opened)
- **FCC 23-93, Report & Order + FNPRM, adopted 2023-11-13, WT Docket 16-239**: replaced the baud limit with a 2.8 kHz bandwidth limit in the HF bands. It did **not** remove the limit in 2200 m, 630 m or the VHF/UHF bands. The FNPRM proposed removing those and sought comment on "the appropriate bandwidth limitation for … the VHF/UHF bands". The VHF bands with baud rates were listed as "6 meter band (50.1–51.0 MHz), (51.0–54.0 MHz); 2 meter band (144.1–148.0 MHz); and the 1.25 meter band (222–225 MHz)" — [FCC 23-93](https://docs.fcc.gov/public/attachments/FCC-23-93A1.pdf); [FCC news release DOC-398409](https://docs.fcc.gov/public/attachments/DOC-398409A1.pdf); [ARRL: Hails FCC action](https://www.arrl.org/news/arrl-hails-fcc-action-to-remove-symbol-rate-restrictions); [Radio World](https://www.radioworld.com/news-and-business/headlines/fcc-replaces-amateur-radio-symbol-rate-limits). Federal Register publications: [FR 2023-26769 (R&O)](https://public-inspection.federalregister.gov/2023-26769.pdf), [FR 2023-26770 (FNPRM)](https://public-inspection.federalregister.gov/2023-26770.pdf)
- Commentary: "current bandwidth limits are 20 kHz for the 6m and 2m bands, 100 kHz for the 1.25m and 70 cm bands" — [AmateurRadio.com "Adios Symbol Rate Limit"](https://www.amateurradio.com/adios-symbol-rate-limit/) (secondary and older)
- In April 2025 ARRL asked the FCC, in its "Delete, Delete, Delete" proceeding, to delete or amend the LF and VHF/UHF symbol-rate and bandwidth limits. That was a request, not an order — [ARRL Files Comments](https://www.arrl.org/news/arrl-files-comments-responding-to-fcc-request-for-input); [ARRL Responds to FCC Proposals](https://www.arrl.org/news/view/arrl-responds-to-fcc-proposals)
- No final FCC order removing the 2 m baud/bandwidth rule was found in searches run 2026-11-14 — (negative finding; see Gaps)
- Meshtastic 2.8 picked 15.6 kHz "Tiny" presets in 20 kHz channels "so they fit ham band plans" — [NodakMesh](https://nodakmesh.org/blog/meshtastic-ham-mode-amateur-regions)

### Inferences
- LoRa (CSS) is not one of the §97.309(a) specified codes (Baudot, ASCII, AMTOR, CLOVER, PacTOR, etc., from general knowledge). So it is an **unspecified digital code** under §97.309(b). That carries the 20 kHz bandwidth cap rather than the 19.6 kbaud cap. §97.309(b) also requires (BELIEVED from memory, unverified this session) that the technical characteristics be publicly documented and that the code not be used to obscure meaning. Semtech's public LoRa modem documentation probably satisfies the first condition.
- LoRa occupied bandwidth is roughly the configured BW. Compliant US 2 m choices: **7.8, 10.4, 15.6 kHz**. 20.8 kHz is over 20 kHz on its face, so firmware should treat it as non-compliant (a guard band from crystal error also argues for ≤15.6). **62.5 and 125 kHz are not permitted on US 2 m.** On US 1.25 m / 70 cm the cap is reportedly 100 kHz (secondary source). That would allow 62.5 kHz but still not 125 kHz. Verify the 1.25 m / 70 cm rules separately.
- If the FCC eventually adopts the FNPRM, the 2 m bandwidth cap could change. A firmware region should hold the cap as a data value, not a hardcode.

### Gaps
- **No primary text opened this session** (ecfr.gov fetch failed). Exact current wording of §97.307(f)(5) and (f)(8) needs a direct check.
- Whether the FCC acted on the FNPRM (WT 16-239) or the "Delete, Delete, Delete" docket (GN 25-133) between mid-2025 and 2026-11 is **UNKNOWN**. The search found nothing, which is not proof that nothing happened.
- §97.307(f)(8) content (likely the 2 m multiplexed / RTTY-data emission clause) was not retrieved.

---

## Q4. Encryption, station ID, automatic control, third-party traffic and unlicensed relays

### Takeaway
Part 97 bans messages "encoded for the purpose of obscuring their meaning", so a ham-band mesh has to run in plaintext. Meshtastic 2.8's licensed mode implements this: it strips channel keys, disables the admin channel, signs packets without encrypting them, requires a callsign before transmitting, and **refuses to relay packets to or from unlicensed users**. That last point matters most for third-party traffic and control-operator rules.

### Cited Findings
- Meshtastic docs: the ITU amateur regions can only be selected in licensed mode. "Encryption is not permitted" and the callsign must be transmitted — [Meshtastic LoRa Configuration](https://meshtastic.org/docs/configuration/radio/lora/) (search summary)
- Ham mode: "Every channel's key is removed, and the admin channel is disabled. FCC Part 97 doesn't allow messages encoded to obscure their meaning, so a ham-band mesh runs in the clear." The node stays silent until a callsign is set and transmit is enabled — [NodakMesh](https://nodakmesh.org/blog/meshtastic-ham-mode-amateur-regions)
- Meshtastic 2.8: licensed operators get "signed plaintext. Messages are authenticated without being encrypted." Also: "Licensed operators are now prevented from rebroadcasting packets to/from unlicensed users, for regulatory compliance (#9958)." 2.8.0 was revoked and replaced by 2.8.1 — [Meshtastic blog "Let's talk about 2.8"](https://meshtastic.org/blog/lets-talk-about-2-8/); [firmware v2.8.0 release notes](https://newreleases.io/project/github/meshtastic/firmware/release/v2.8.0.47db0e3)
- Apple client docs: enable Licensed Operator and set the call sign in User config before transmitting. The amateur bands accept only a subset of presets — [Meshtastic Apple settings](https://meshtastic.org/docs/software/apple/user/settings/); [LoRa Region → Preset Compatibility](https://meshtastic.org/docs/software/apple/developer/lora-region-presets/)

### Inferences
- **97.113(a)(4)** (BELIEVED, general knowledge; search failed): prohibits "messages encoded for the purpose of obscuring their meaning". Signing (authentication) is widely read as permitted, since it doesn't obscure meaning. The FCC hasn't ruled on Ed25519/PKI signatures specifically; I found no source.
- **97.119(a)** (BELIEVED): station ID at least every 10 minutes during communication and at the end. Meshtastic NodeInfo carries the callsign as long name. Whether a periodic NodeInfo broadcast in a CSS (non-CW, non-phone) digital mode satisfies "in a digital code" ID is an inference. 97.119(b)(3) allows ID by a digital code listed in 97.309(a), and LoRa is **not** listed. An operator may need a separate CW/phone ID, or must rely on the code being "publicly documented". **This is a real compliance ambiguity**; flag it in operator guidance.
- **97.221 / 97.109(d)** (UNKNOWN this session): these rules govern automatic control of digital stations and message forwarding. I'm not confident which 2 m segments, if any, are restricted for automatically controlled digital stations, so I'm not stating a segment. A relaying mesh node with no live control operator is "automatic control". Its US-legal 2 m operation must be checked in §97.221 and §97.109(d) directly before firmware enables unattended relay.
- **Third-party traffic (97.115)**: an unlicensed user's message relayed by a ham node would be third-party traffic, and the unlicensed person can't be control operator. Meshtastic's #9958 block on relaying to/from unlicensed users avoids this. Firmware for MeshForge-style gateways (MQTT/RNS ↔ 2 m) needs the same gate. **A gateway that injects internet or ISM-mesh traffic into 2 m is the highest-risk design**, because it combines third-party traffic, automatic control and foreign-origin rules.
- UK/EU licences generally also prohibit encryption except narrow cases (e.g., remote control of the station). Not verified this session.

### Gaps
- Exact 97.113, 97.115, 97.119, 97.221 text: the search errored. Needs a direct eCFR check.
- No regulator ruling on whether LoRa NodeInfo satisfies 97.119 ID.
- Ofcom and CEPT encryption clauses: not retrieved.

---

## Q5. Has any regulator or national society commented on LoRa/Meshtastic in amateur bands?

### Takeaway
I found **no** FCC, ISED, Ofcom, ACMA or IARU statement specific to LoRa/Meshtastic on 2 m. The only regulatory discussion found concerns the **ISM** band, 902-928 MHz under Part 15. That's a separate issue. The amateur-band work comes from the Meshtastic project itself (2.8 ham regions).

### Cited Findings
- Hackaday and Brainwagon (Sept 2026) argue that default 250 kHz LoRa presets may not meet the §15.247(a)(2) 500 kHz minimum DTS bandwidth in 902-928 MHz. The blog stresses that the FCC "has said nothing about LoRa mesh" and has taken no enforcement action, so this is a reading of the plain text, not a ruling — [Hackaday 2026-09-17](https://hackaday.com/2026/09/17/fcc-ism-rules-may-shatter-lora-mesh-communities/); [Brainwagon 2026-09-18](https://brainwagon.org/blog/2026-09-18-500-khz-or-bust-why-your-meshtastic-node-may-be-illegal.html)
- ARRL's 2025 filings asked to delete or amend the VHF/UHF symbol-rate and bandwidth limits generally (not LoRa-specific) — [ARRL Files Comments](https://www.arrl.org/news/arrl-files-comments-responding-to-fcc-request-for-input)
- Amateur LoRa/Meshtastic emcomm material exists (e.g., Placer County ARES, M1EMF), but it's operator-level, not regulator-level — [Placer County ARES](https://sites.google.com/view/placercountyares/meshtastic-for-emcomm); [M1EMF](https://www.qsl.net/m1emf/meshtastic.htm)

### Inferences
- The Part 15 ISM questions (Hackaday/Brainwagon) may push US users toward Part 97 bands. That would increase 2 m/70 cm LoRa pressure on band-plan coordinators, which makes coordinating a fixed channel with ARRL and local frequency coordinators more important.

### Gaps
- No RSGB, DARC, WIA, JARL or IARU R1 VHF Committee papers on LoRa were found. A targeted search of IARU R1 interim-meeting documents (C5 committee) might find some.

---

## Q6. Licence-free VHF alternatives: MURS (US) and 169 MHz SRD (EU)

### Takeaway
MURS (US, Part 95J) allows data on 5 channels at 2 W, but caps occupied bandwidth at 11.25 kHz (151.820/151.880/151.940) or 20 kHz (154.570/154.600). The emission list is FM/AM types, so whether LoRa CSS qualifies is doubtful and unconfirmed. EU 169.400-169.475 MHz allows 500 mW e.r.p. at ≤1% duty cycle (ECC/DEC/(05)02, EN 300 220-4, metering). A 12.5 kHz-class LoRa might fit, but no source confirms LoRa there.

### Cited Findings
- MURS §95.2773: occupied bandwidth ≤ 11.25 kHz on 151.820, 151.880 and 151.940 MHz, and ≤ 20.0 kHz on 154.570 and 154.600 MHz. A3E ≤ 8 kHz — [ecfr.io §95.2773](https://ecfr.io/Title-47/Section-95.2773); [customsmobile §95.2773](https://www.customsmobile.com/regulations/title47_chapterI-i4_part95_subpartJ_section95.2773)
- MURS §95.2767: transmitter power output must not exceed 2 W — [ecfr.io Part 95 Subpart J](https://ecfr.io/Title-47/Part-95/Subpart-J)
- MURS §95.2771: permitted emission types are "A1D, A2B, A2D, A3E, F2B, F1D, F2D, F3E, and G3E" — [ecfr.io Part 95 Subpart J](https://ecfr.io/Title-47/Part-95/Subpart-J) (via search summary)
- 169.4-169.475 MHz: 500 mW e.r.p., duty cycle ≤ 1%, per ECC/DEC/(05)02 and EN 300 220 — [CEPT EFIS annex](https://efis.cept.org/adhoc_grabber.jsp?annex=4); [CEPT RIG II 169.4-169.8125 template](https://docdb.cept.org/download/1667)
- ETSI EN 300 220-4 is the harmonised standard for metering devices in 169.400-169.475 MHz under RED article 3.2 — [ETSI EN 300 220-4 V1.1.1](https://www.etsi.org/deliver/etsi_en\300200_300299\30022004\01.01.01_60\en_30022004v010101p.pdf)
- The 169.4-169.8125 range is also listed for non-specific SRDs under EN 300 220, with duty cycles of 0.1-10% depending on the sub-band — [CEPT EFIS](https://efis.cept.org/adhoc_grabber.jsp?annex=5); [VSB LoRa page](https://lora.vsb.cz/index.php/wp-json/wp/v2/pages/9126)

### Inferences
- LoRa's emission designator is usually described as a chirp/FM-type ("F1D"-like). Whether MURS accepts it is an interpretive question; I found no FCC guidance. MURS devices must be certified under Part 95, so a hobbyist SX127x/SX126x board isn't MURS-certified. **MURS LoRa is likely not legal on uncertified hardware**, regardless of emission type (BELIEVED).
- MURS-compatible LoRa BW: ≤10.4 kHz on the 151 MHz channels, ≤15.6 kHz on the 154 MHz channels. Same arithmetic as US 2 m.
- 169 MHz EU: 75 kHz wide, a 1% duty cycle, and metering-oriented harmonised standards. The general SRD sub-bands have their own channel-spacing rules. It suits low-rate telemetry, not a chat mesh. SX126x/SX127x support 169 MHz only with specific matching networks.
- Versus 2 m amateur: 2 m gives far more power (tens to hundreds of W legally) and no duty cycle, but needs a licence, plaintext and ID. MURS and 169 MHz are license-free but are hardware-certification gated and severely power/duty-limited.

### Gaps
- No source on LoRa specifically at 169 MHz or MURS from a regulator.
- MURS emission-mask (§95.2779) text not retrieved.

---

## Summary table (draft region definitions; every row needs primary-source verification)

| Region / country | Legal 2 m band | Data bandwidth cap (regulatory) | Band-plan home for narrow LoRa | Compliant LoRa BW | Confidence |
|---|---|---|---|---|---|
| US (FCC Part 97) | 144.0-148.0 (data 144.1-148.0) | 20 kHz unspecified digital codes; 19.6 kbaud specified codes — §97.307(f)(5); FNPRM (FCC 23-93) pending | ~145.01-145.09 (legacy packet); 145.5-145.8 "experimental" (secondary sources); avoid 144.39, 145.8-146.0 | ≤15.6 kHz (20.8 marginal/over) | BELIEVED (rule text via snippets) |
| Canada (ISED RBR-4 Iss.3, 2022) | 144.0-148.0 | 30 kHz max | 145.59-145.79 "Wide Band Digital"; guard 145.79-145.80 | ≤20.8 kHz (31.25 marginal) | BELIEVED |
| UK (Ofcom; RSGB 2024 plan) | 144.0-146.0 | Licence text not retrieved | 144.794-144.990 digital, 12 kHz necessary BW; APRS 144.800 | ≤10.4 kHz to respect plan; 15.6 kHz exceeds plan | BELIEVED (plan), UNKNOWN (licence) |
| Germany / CEPT (R1) | 144.0-146.0 | Not retrieved | IARU R1 plan (Handbook 10.02) | likely ≤12 kHz per R1 plan | UNKNOWN |
| Australia (ACMA LCD; WIA 2026) | 144.0-148.0 (primary) | 100 kHz in a 2018 WIA LCD table, not confirmed current | WIA 2026 plan, 2 m section not read | ≤62.5 kHz *if* 100 kHz confirmed; plan placement unknown | UNKNOWN |
| Japan (MIC; JARL 2023/2025) | believed 144-146 | Not retrieved | 144.60-144.70 "wideband data"; 145.30 digital calling | plan room ≤62.5 kHz; regulatory cap unknown | BELIEVED (plan), UNKNOWN (cap) |
| New Zealand (RSM) | 144-148 assumed | Not retrieved | — | — | UNKNOWN |
| Meshtastic 2.8 firmware | R1 144-146; R2/R3 144-148 | uses 15.6 kHz Tiny presets in 20 kHz channels; default 145.010 (R2) | — | TinyFast/TinySlow 15.6 kHz | BELIEVED (docs/blog snippets) |

**Blind spot:** every regulatory cell above came from search summaries, not opened documents. The fetch tool was blocked for every host tried this session. Before writing firmware constants, check each against the primary source: eCFR §§97.305, 97.307, 97.309, 97.113, 97.119, 97.221; ISED RBR-4; Ofcom Amateur Radio Licence terms; ACMA Amateur LCD; MIC 使用区別; RSM GURL.
