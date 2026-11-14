# Hardware & RF Engineering for a 2 m (144–148 MHz) Meshtastic/LoRa Node

> Researcher notes, 2026-11-14. **Method caveat (read first):** in this session WebFetch could only reach `github.com`; every other host failed DNS (`nicerf.com`, `cdn.sparkfun.com`, `cdn-shop.adafruit.com`, `wiki.lilygo.cc`, `nodakmesh.org`, `jgromes.github.io`). So:
> - Facts tagged **[fetched]** were read from the primary page this session (Meshtastic firmware PRs / variant.h, a GitHub project README).
> - Facts tagged **[search-snippet]** come from search-engine summaries of the cited page. The page itself was not opened, so treat exact numbers as BELIEVED and check them against the PDF.
> - Facts tagged **[training]** are from my prior knowledge (Semtech datasheets, FCC §97.307, ITU-R P.372/P.526). I could NOT re-fetch them here. They are labelled, never presented as verified.
> - All link-budget, filter, ppm and antenna numbers are **my own calculations**; the formulas are shown.

---

## 1. Which LoRa transceivers cover 144 MHz: officially vs. unofficially

### Takeaway
Only the **SX1276/77/78/79 family** (137–1020 MHz; SX1278 137–525 MHz) officially covers 2 m. Every newer Semtech part (SX1261/62/68, LLCC68, LR1110/LR1120/LR1121, LR2021) starts at **150 MHz**, so 144–148 MHz is 2–6 MHz below spec. Hobbyists report that the SX126x PLL locks far below 150 MHz, but PA match, harmonics and sensitivity are unguaranteed there. The shipping 2 m Meshtastic hardware (LilyGO T-Beam BPF) uses an **SX1278**.

### Cited Findings
- SX1276 is specified 137 MHz to 1020 MHz, so 144 MHz is in range — [Semtech SX1276 product page](https://www.semtech.com/products/wireless-rf/lora-connect/sx1276) [search-snippet]; [SX1276 datasheet (Mouser copy)](https://www.mouser.com/datasheet/2/761/sx1276-1278113.pdf) [search-snippet].
- In the SX127x datasheet, specs whose symbol ends in "LF" are Band 2 and/or Band 3 performance. RFO_LF is the RF output pin for bands 2 & 3 — [SX1276 datasheet (SparkFun copy)](https://cdn.sparkfun.com/assets/7/7/3/2/2/SX1276_Datasheet.pdf) [search-snippet].
- SX127x sensitivity: the headline "−148 dBm" is the **7.8 kHz** BW SF12 figure for Band 2/3. At **125 kHz SF12 in Band 3** the table gives about **−137 dBm** — [SX1276 datasheet (Mouser copy)](https://www.mouser.com/datasheet/2/761/SEMT_S_A0007665073_1-2575876.pdf) [search-snippet; verify the table].
- [training] SX127x band plan (datasheet Table 1/"Frequency bands"): Band 1 (HF) 862–1020 MHz (SX1276/77/79), Band 2 (LF) 410–525 MHz, Band 3 (LF) 137–175 MHz. SX1278 covers Bands 2–3 only (137–525 MHz). SX1279 extends 137–960. Output: RFO_LF/RFO_HF up to about +14 dBm. PA_BOOST up to +17 dBm continuous and +20 dBm at ≤1% duty (high-power register setting). LoRa needs a crystal/TCXO of about ±10–20 ppm class, with no PLL changes needed for Band 3. *Re-check these against DS rev 7 before quoting as datasheet numbers.*
- SX1261/62 synthesizer range is 150–960 MHz, and RadioLib's driver rejects frequencies outside that window — [SX1262 datasheet rev 1.2 (SparkFun copy)](https://cdn.sparkfun.com/assets/6/b/5/1/4/SX1262_datasheet.pdf), [RadioLib SX1262 class ref](https://jgromes.github.io/RadioLib/class_s_x1262.html) [search-snippet].
- Search found **no documented over-the-air ham test** of an SX1262 at 144 MHz. The SX1262 datasheet rev 2.2 (Dec 2024) still says 150–960 MHz — [DS_SX1261_2 V2.2](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/1198/DS_SX1261_2_V2-2.pdf) [search-snippet].
- **Hobbyist observation (not datasheet):** the SX1262_CHIRP project reports "The PLL is happy from somewhere around 50 MHz up through 1300 MHz in practice". Below 150 MHz "the on-chip PA is out of spec but still produces usable output" for receive-only/low-level work. With the PA network matched for ~900 MHz, at 50 MHz the odd harmonics are "only 10–15 dB down from the fundamental instead of 40+ dB", and "you will be lossy at the antenna port". The project gives no measured power figure — [ibelinp/SX1262_CHIRP](https://github.com/ibelinp/SX1262_CHIRP) [fetched].
- A more cautious bench project (SX1262-SDR) separates "accepted frequency commands, PLL status, RSSI changes, … calibrated reception" into distinct evidence levels. In other words, PLL lock alone does not prove usable reception — [hardcoreerik/SX1262-SDR](https://github.com/hardcoreerik/SX1262-SDR) [search-snippet].
- LR1121: Semtech lists sub-GHz "150 – 960 MHz" plus 2.4 GHz and 2.1 GHz S-band, with "+22dBm" from the HP PA path sub-GHz — [Semtech LR1121 page](https://semtech.com/products/wireless-rf/lora-connect/LR1121) [search-snippet]. Digi-Key lists 150 MHz–2.5 GHz and −144 dBm — [Digi-Key LR1121IMLTRT](https://www.digikey.com/en/products/detail/semtech-corporation/LR1121IMLTRT/17874420). A NiceRF module quotes −142 dBm at SF12/62.5 kHz [search-snippet]. One third-party table claims 430–960 MHz, which contradicts Semtech; disregard it.
- LR2021 ("LoRa Plus", LoRa Gen 4, announced March 2025): sub-GHz **150–960 MHz**, +22 dBm max sub-GHz (to −10 dBm), +12 dBm at 2.4 GHz, best LoRa sensitivity **−141.5 dBm @ SF12/125 kHz** (Semtech). The preliminary DS says −142 dBm. Link budget 163.5 dB — [Semtech LR2021](https://www.semtech.com/products/wireless-rf/lora-plus/lr2021), [CNX Software 2025-03-13](https://www.cnx-software.com/2025/03/13/semtech-lr2021-lora-plus-transceiver-supports-lora-gen-4-amazon-sidewalk-meshtastic-wm-bus-wi-sun-fsk-and-z-wave/), [Elecrow LR2021 module spec](https://www.elecrow.com/download/product/LMM16121D/LR2021_Wireless_Communication_Module_Specifications.pdf) [search-snippet]. Elecrow's module spec gives 105 mA TX at +22 dBm. The current datasheet is "LR2021/22/12 Datasheet v2.2, 2026-08-08" — [Mouser copy](https://lv.mouser.com/pdfDocs/63293790LR20xxDatasheet_V2_2.pdf) [search-snippet].
- A TAPR DCC paper (KG5VBY) states that Semtech's sub-GHz line can cover amateur bands from 2 m to 33 cm, but that 2 m LoRa "would require a specific effort by the amateur radio community to develop custom LoRa board designs" — [TAPR DCC2018-02](https://tapr.org/wp-content/uploads/DCC2018-02.pdf) [search-snippet].
- A builder reported SX1276 LoRa working at 900 MHz, but "when testing on the 144MHz radio, none were able to be decoded" (unresolved in the snippet) — [RadioLib discussion #702](https://github.com/jgromes/RadioLib/discussions/702) [search-snippet]. This shows that board-level matching and filtering at 144 MHz matter, not just the chip's spec.

### Summary table (datasheet vs observed)

| Chip | Official sub-GHz range | 144 MHz status | Max Pout (sub-GHz) | SF12/125k sensitivity |
|---|---|---|---|---|
| SX1276/77/79 | 137–1020 MHz (77: 137–1020, SF6–9) [training] | **In spec (Band 3)** | +20 dBm PA_BOOST (1% duty), +17 cont. [training] | ~−137 dBm Band 3 [search-snippet]; −148 @ 7.8 kHz |
| SX1278 | 137–525 MHz [training] | **In spec** (used by T-Beam BPF) | same as SX1276 | same LF figures |
| SX1261/62/68, LLCC68 | 150–960 MHz | Out of spec. PLL locks per hobbyists; PA/harmonics unguaranteed | +22 dBm (SX1262/68) | −137 dBm (SX1262 SF12/125k) [training] |
| LR1110/1120/1121 | 150–960 MHz | Out of spec | +22 dBm | ~−144 (Digi-Key) [search-snippet] |
| LR2021 | 150–960 MHz | Out of spec | +22 dBm | −141.5 dBm |

### Inferences
- For a **compliant, datasheet-backed** 2 m design today, SX1276/SX1278 is the only Semtech option. It costs the SX126x features (lower RX current, +22 dBm, better blocking/CAD). The Meshtastic project itself chose an SX1278 for the T-Beam BPF.
- Using SX1262/LR11xx/LR2021 at 144 MHz is an engineering experiment. The harmonic-suppression concern is real (SX1262_CHIRP: odd harmonics 10–15 dBc when mismatched), so a strong LPF and a spectrum-analyzer check are required before any on-air use.

### Gaps
- Could not open the Semtech PDFs to quote the exact Band 3 PA_BOOST/RFO_LF power, the Band 3 noise figure, and the full SF6–SF12 sensitivity table at 125/62.5/31.25/20.8/15.6 kHz.
- No public measured data was found for SX1262 TX power or spurious output at 144–148 MHz.
- Did not check non-Semtech LoRa-compatible silicon (e.g., ASR6601 = SX1262 die + MCU, also 150–960 MHz [training]).

---

## 2. Off-the-shelf 140–175 MHz modules: chip, power, interface

### Takeaway
Most "170 MHz" modules target China's 160–173 MHz telemetry band, **not** 144–148 MHz. SPI modules (E22-170M) expose the raw radio that Meshtastic needs. UART "transparent" modules (E32-170T, E22-170T) do not, so they are useless for Meshtastic. The only purpose-built 144–148 MHz LoRa module found is LilyGO's **XP02P181T** (SX1278 + BPF + PA) inside the T-Beam BPF.

### Cited Findings
- **Ebyte E22-170M22S / M30S / M33S**: SX1262, **SPI**, 22 / 30 / 33 dBm, tuning **150.125–170.125 MHz**, default 170.125 MHz, 250 kHz channel spacing — [Ebyte E22-170M33S](https://ebyteiot.com/products/ebyte-e22-170m33s-23dbm-16km-170-125mhz-spi-sx1262-module), [E22-170M22S](https://ebyteiot.com/products/ebyte-e22-170m22s-22dbm-5km-170-125mhz-spi-sx1262-lora-module), [E22-170M30S](https://ebyteiot.com/products/ebyte-e22-170m30s-30dbm-10km-170-125mhz-spi-sx1262-lora-rf-transceiver) [search-snippet]. The E22 user manual found covers only the 400/900 MHz variants, so the 170 MHz range rests on vendor listings.
- **Ebyte E32-170T30D**: SX1278, **UART (TTL 3.3 V)** "wireless serial port", 30 dBm (~1 W), **160–173.5 MHz**, default 170 MHz — [Ebyte store](https://ebyteiot.com/products/lora-sx1278-170mhz-long-range-8km-wireless-transceiver-transmitter-receiver-rf-module-ebyte-e32-170t30d-e32-30dbm-sma-antenna), [E32 manual](https://manualmachine.com/ebyte/e32170t30d/21499103-user-manual) [search-snippet].
- **LilyGO T-Beam BPF**: ESP32-S3, SX1278 with TCXO, integrated band-pass filter **144–148 MHz**, RF module "XP02P181T", AXP2101 PMU, 16 MB flash / 8 MB PSRAM, L76K GPS, 0.96" OLED (SH1106 vs SSD1306 disputed between LilyGO pages). The product page may be delisted — [LilyGO wiki](https://wiki.lilygo.cc/products/t-beam-series/t-beam-bpf/), [electronics-lab](https://www.electronics-lab.com/news/lilygo-t-beam-bpf-esp32-s3-development-board-with-sx1278-lora-oled-gps-and-axp2101-pmu/), [openelab](https://openelab.io/products/lilygo-t-beam-bpf-lora) [search-snippet].
- T-Beam BPF output power: the Meshtastic PR says "This node contains a +27 dBm radio!". The title was later amended to "@ ~37 dBm (5 Watts)", and a commit says "This is a 5-6W radio!!" — [meshtastic/firmware PR #10558](https://github.com/meshtastic/firmware/pull/10558) [fetched]. variant.h records measured output on 5 V USB power of 34→38 dBm across PA steps 2–10 and states a 37 dBm maximum on battery — [variant.h](https://github.com/meshtastic/firmware/blob/develop/variants/esp32s3/t-beam-bpf/variant.h) [fetched].
- A **TLoRa v2.1-1.6 "144 MHz version"** (`tlora-v2-1-1_6`, SX127x) was regression-tested for the 2 m regions. The PR also mentions forthcoming "T-Beam Supreme and TLora32 144mhz versions" — [PR #10623](https://github.com/meshtastic/firmware/pull/10623) [fetched].
- An openelab listing titled "LILYGO T-Beam 1W … SX1278 BPF(144-148 MHZ)" exists — [openelab](https://openelab.io/products/lilygo-t-beam-1w-lora?variant=45228319047878). This may be a listing error: electronics-lab describes the T-Beam-1W separately — [electronics-lab](https://www.electronics-lab.com/lilygo-t-beam-1w-brings-1w-lora-gps-and-esp32-for-long-range-iot/) [search-snippet]. Unconfirmed.
- Waveshare Core1262 ships only HF (850–930) and LF (410–510 MHz) versions, with no VHF option — [Waveshare](https://www.waveshare.com/product/core1262-868m.htm) [search-snippet].

### Inferences
- **Interface matters absolutely.** Meshtastic drives the transceiver registers directly through RadioLib over SPI (variant.h defines LORA_CS/SCK/MISO/MOSI/IRQ/DIO1). A UART-transparent module (E32-170T, E22-170T, E220-170T) runs Ebyte's own MCU firmware and framing, so it cannot run Meshtastic.
- The E22-170M (SPI, SX1262) is the closest SPI module, but it has two problems: (a) its silicon is out of spec below 150 MHz, and (b) its on-module match, LPF, PA and LNA are tuned for 150–170 MHz. At 144–148 MHz you would expect some mismatch loss and an LPF that still passes the fundamental. A 170 MHz LPF passes 145 MHz fine; the issue is the PA match/efficiency. I have **no measurement** of E22-170M performance at 145 MHz.
- An SX1276/78 "LF" module (RFM96/98, Ra-01) is matched for 433 MHz and would need its LF match network re-tuned for 145 MHz. See section 3.

### Gaps
- Could not verify NiceRF **LoRa1276-170**, Ebyte **E220-170**, or **HopeRF RFM9x** 169/170 MHz variants (NiceRF was unreachable, and searches did not surface them).
- XP02P181T part datasheet (PA part number, BPF insertion loss, LNA, TCXO voltage) not found.
- No module prices retrieved.

---

## 3. RF front-end design at 2 m

### Takeaway
At 2 m the hard parts are (1) re-matching the SX127x LF port, (2) a **≥7-pole low-pass filter**, because the 3rd harmonic (432–444 MHz) lands inside the 70 cm amateur band and on 435–438 MHz satellites, and (3) a front-end BPF/SAW against strong 2 m FM, pager (152–159 MHz) and NOAA (162 MHz) signals. TCXO stability is easy at VHF: 1 ppm is only 145 Hz.

### Cited Findings
- LilyGO's design uses SX1278 → PA (to ~5 W) → BPF 144–148 MHz, with an RX-enable (LNA/T-R switch) line on GPIO39 (`RF95_RXEN`), an LDO-switched radio supply on GPIO16 (`RF95_POWER_EN`, must be high before `lora.begin()`), a PA curve `TX_GAIN_LORA = {0,32,32,32,31,31,30,30,29,28}`, and `RF95_MAX_POWER 10` (chip-level dBm into the PA) — [variant.h](https://github.com/meshtastic/firmware/blob/develop/variants/esp32s3/t-beam-bpf/variant.h) [fetched].
- Mismatched SX126x at VHF: odd harmonics only 10–15 dB down — [SX1262_CHIRP](https://github.com/ibelinp/SX1262_CHIRP) [fetched]. (This was measured near 50 MHz, not 145, but it shows that an off-frequency match ruins harmonic suppression.)
- The built-in 144–148 MHz BPF "limits receivable frequencies … reducing out-of-band interference" — [openelab](https://openelab.io/products/lilygo-t-beam-bpf-lora) [search-snippet].
- [training] **FCC §97.307(e)**: for transmitters on 30–225 MHz, spurious emissions must be ≥60 dB below the mean fundamental power. For a transmitter of mean power <25 W, spurious emissions must not exceed **25 µW (−16 dBm)** and must be ≥40 dB below the fundamental, but need not be below 10 µW (−20 dBm). *Re-verify against eCFR before publishing.*

### Own calculations

**Harmonic map (f0 = 144–148 MHz):**
| n | Frequency | Lands in |
|---|---|---|
| 2 | 288–296 MHz | Government/military UHF (225–400) |
| 3 | **432–444 MHz** | **70 cm amateur band; 432 weak-signal segment; 435–438 amateur satellite uplinks**. Also on the 433 LoRa/ISM channel |
| 4 | 576–592 MHz | UHF TV |
| 5 | 720–740 MHz | UHF/LTE |

**Spurious limits applied (from the [training] §97.307(e) text above):**
- 22 dBm (158 mW) bare SX1262/SX1276: 40 dBc gives −18 dBm, which beats −16 dBm. The −20 dBm floor applies, so the spur target is ≤ −18 dBm (≥40 dBc).
- 37 dBm (5 W, T-Beam BPF): 40 dBc gives −3 dBm, but the 25 µW cap binds, so spurs must be ≤ −16 dBm, i.e. **≥53 dBc**.
- 47 dBm (50 W ext. PA): ≥25 W, so the **60 dBc** rule applies (spurs ≤ −13 dBm).

**LPF sizing (50 Ω, fc = 160 MHz, own calc).** Butterworth attenuation is A(f) = 10·log10(1 + (f/fc)^(2N)).
- N=5: 2nd harmonic (288/160 = 1.8) gives 25.5 dB; 3rd (2.7) gives 43.1 dB.
- N=7: 2nd 35.7 dB; 3rd 60.4 dB.
- Add the PA's intrinsic harmonic level (typically −20 to −35 dBc for a class-AB/E stage [training, not sourced]). A 5-pole Butterworth is marginal for the 53–60 dBc case, so use a **7-pole Chebyshev (0.1 dB ripple)** or an elliptic LPF with notches at 288 and 432 MHz.
- 5-pole Butterworth Pi prototype values (g = 0.618, 1.618, 2.000, 1.618, 0.618), with C_unit = 1/(2π·fc·R) = 19.9 pF and L_unit = R/(2π·fc) = 49.7 nH, give C1 = 12.3 pF, L2 = 80.4 nH, C3 = 39.8 pF, L4 = 80.4 nH, C5 = 12.3 pF. This is a starting point only; tune for component Q and parasitics.

**SX127x match at 144 MHz (own reasoning).** The LF RFO/PA_BOOST networks on 433 MHz modules use roughly 3× smaller L/C values than 145 MHz needs. Reactance scales with f, so L_145 ≈ L_433 × (433/145) ≈ 3× and C_145 ≈ 3× as well. Semtech publishes band-specific reference designs (the 169 MHz SX1276 reference is closest [training]). The quickest path is to start from a 169 MHz reference design and scale by 169/145 ≈ 1.17×.

**TCXO stability (own calc).** Δf = f0 × ppm × 1e-6. At 145 MHz: 0.5 ppm gives 73 Hz, 1 ppm gives 145 Hz, 2.5 ppm gives 363 Hz, 10 ppm gives 1.45 kHz, 20 ppm gives 2.9 kHz.
- [training] The LoRa demodulator tolerates a carrier offset of roughly ±BW/4 (Semtech AN guidance for SX127x). For the Meshtastic 2 m BW of 15.6 kHz, that is ±3.9 kHz = **±26.9 ppm total** (TX+RX) at 145 MHz.
- For comparison, 915 MHz/125 kHz allows ±34 ppm and 915 MHz/62.5 kHz allows ±17 ppm. So 15.6 kHz at 2 m is about as forgiving in ppm as ~100 kHz at 915 MHz.
- At BW 7.8/10.4 kHz (±2.0/2.6 kHz = ±13/±18 ppm), a ±2.5 ppm TCXO is comfortable, while a ±10–20 ppm crystal across temperature and aging is not. Long SF11/12 packets at narrow BW also need low drift *during* the packet (LowDataRateOptimize), which favors a TCXO.
- **Conclusion:** use a ±0.5–2.5 ppm TCXO. LilyGO uses a TCXO on the SX1278. The SX127x takes a TCXO on XTA (clipped-sine) with RegTcxo bit 4 set [training], unlike SX126x DIO3-controlled TCXO voltage.

**External PA (5–50 W) and LNA (own reasoning + [training]):**
- 5 W class: an integrated VHF PA (e.g., RA07H-series Mitsubishi modules, or LDMOS such as RD06HVF1/AFT05MS-class parts) [training]. LoRa is constant-envelope (chirp FSK), so a class-C/E saturated PA is fine and needs no linearity. That makes LoRa efficient at high power.
- 50 W: an existing 2 m FM "brick" amplifier works for the same reason (constant envelope). It needs fast T/R keying: a sequencer driven by the LoRa TX-done/RXEN line, because Meshtastic's turnaround is short. COR/RF-sense amps are too slow and would clip preambles. Duty cycle and heat: Meshtastic airtime at 15.6 kHz BW is long (see the airtime note below).
- LNA: a low-NF (≤1 dB) GaAs/PHEMT LNA (e.g., PGA-103/SPF5189-class, [training]) **after** a 2 m BPF or SAW. In pager/repeater-heavy environments the SX127x's limited blocking (~89 dB at 1 MHz offset, from the [training] datasheet value) is the risk. Because external man-made noise dominates at VHF (section 4), extra LNA gain buys little sensitivity; the BPF matters more than the LNA.

**Airtime note (own calc, standard LoRa formula):** symbol time Ts = 2^SF / BW. At 15.6 kHz: SF7 = 8.2 ms, SF9 = 32.8 ms, SF12 = 262 ms per symbol. That is 8× longer than the same SF at 125 kHz. For a 50 W PA this means long key-down periods, so size the heatsink for that.

### Gaps
- No public schematic or BOM found for XP02P181T or any open 2 m LoRa front end.
- Could not fetch Semtech AN1200.x reference matching values for 169 MHz.
- The PA intrinsic harmonic level and LoRa offset tolerance (±BW/4) are training-based and need confirmation from the Semtech AN.

---

## 4. Antennas and propagation physics: 2 m vs 433 / 915 MHz

### Takeaway
2 m gains about **9.5 dB** (vs 433) and **16.0 dB** (vs 915) of free-space path loss with equal-dBi antennas, and diffracts better. But man-made noise at 145 MHz sits **~7–13 dB above thermal** in residential/business areas (ITU-R P.372 model), which cancels much of that gain. Also, a practical 2 m antenna is physically large (λ/4 ≈ 49 cm), and compact helicals give up several dB.

### Cited Findings
- 2 m band 144.000–148.000 MHz in some regions — [Wikipedia (via search)](https://en.wikipedia.org/wiki/23-centimeter_band) [search-snippet; weak source]. Meshtastic's ITU1 region uses 144–146 MHz and ITU2/3 use 144–148 MHz — [PR #10623](https://github.com/meshtastic/firmware/pull/10623) [fetched].
- [training] ITU-R P.372 man-made noise: median Fa(dB) = c − d·log10(f_MHz), with Business c=76.8/d=27.7, Residential 72.5/27.7, Rural 67.2/27.7, Quiet rural 53.6/28.6. The model's stated validity is up to ~250 MHz, so 433/915 values are **extrapolations**. *Not re-fetched; verify against P.372-16.*
- [training] Single knife-edge diffraction (ITU-R P.526): v = h·√(2(d1+d2)/(λ·d1·d2)), J(v) ≈ 6.9 + 20·log10(√((v−0.1)²+1) + v − 0.1) dB for v > −0.78.

### Own calculations
**Antenna lengths:** λ = 300/f_MHz. At 145 MHz, λ = 2.069 m. λ/4 ≈ 0.95 × 51.7 cm ≈ **49 cm**; λ/2 dipole ≈ 98 cm; 5/8 λ ≈ 1.29 m (+~3 dB over λ/4 on a good ground plane, [training]). At 915 MHz, λ/4 ≈ 8 cm. At 433 MHz, λ/4 ≈ 16.4 cm.

**FSPL:** FSPL(dB) = 32.45 + 20·log10(f_MHz) + 20·log10(d_km).
- At 10 km: 145 MHz = 95.7 dB; 433 MHz = 105.2 dB; 915 MHz = 111.7 dB.
- Δ(433→145) = 20·log10(433/145) = **9.5 dB**. Δ(915→145) = **16.0 dB**.
- (This assumes constant antenna *gain*. With constant antenna *size*, the higher band recovers aperture via higher gain.)

**External noise at 145 MHz (P.372 model):**
- Residential Fa = 72.5 − 27.7·2.161 = **12.6 dB**; Business = 16.9 dB; Rural = 7.3 dB; Quiet rural = −8.2 dB.
- At 433 MHz Residential ≈ −0.5 dB; at 915 MHz ≈ −9.5 dB (extrapolated; below a 6 dB receiver NF, so negligible).
- System noise figure referred to the antenna: F_sys = f_a + (f_rx − 1). With a 6 dB receiver NF: residential 145 MHz gives **13.3 dB**, rural gives 9.2 dB, quiet rural ≈ 6.0 dB. 433 MHz residential ≈ 5.9 dB.
- So at a suburban node, 2 m loses ~7 dB of effective sensitivity to noise. A better LNA cannot recover this, because the noise is external.

**Diffraction (own calc, P.526 approximation).** Example: d1 = d2 = 5 km.
- Obstacle h = 10 m above the LOS line: v = 0.197 / 0.34 / 0.494 at 145 / 433 / 915 MHz, giving J = 7.7 / 9.0 / 10.2 dB.
- Deep shadow, h = 100 m: v = 1.97 / 3.4 / 4.94, and J ≈ 13 + 20·log v gives 18.9 / 23.6 / 26.9 dB, so **~8 dB** in favour of 2 m vs 915.

**Foliage (Weissberger, L = 1.33·f_GHz^0.284·d_m^0.588, valid 230 MHz–95 GHz).** For 100 m of trees: 433 MHz = 15.7 dB; 915 MHz = 19.5 dB; 145 MHz ≈ 11.5 dB, but 145 MHz is **out of model range** (extrapolation). Expect a few dB of advantage per 100 m of canopy, growing with depth.

**Handheld/body (reasoning, [training]):** at 2 m the human body is ~λ/4 to λ/2 in size and acts as part of the counterpoise. A rubber-duck/helical at 2 m is typically quoted at −5 to −10 dBi relative to a full λ/4 on a ground plane. A "tiger tail" counterpoise recovers several dB. That handheld penalty is larger than at 915 MHz, where a λ/4 whip is only 8 cm and efficient.

### Gaps
- Could not fetch ITU-R P.372 to confirm coefficients or the frequency validity range.
- No measured 2 m vs 915 MHz Meshtastic side-by-side range test found.
- No sourced dBi figure for compact 2 m helical/stubby antennas.

---

## 5. Link-budget comparison (own calculation)

### Takeaway
With realistic noise, 2 m's net advantage over 915 MHz at the same SF/BW and equal-dBi fixed antennas is roughly **+9 dB (residential) to +13 dB (rural)**, against a raw FSPL advantage of 16 dB. Over 433 MHz it is only **+2 dB (residential) to +9 dB (quiet rural)**. With a handheld 2 m duck the advantage can disappear.

### Cited Findings
- SX127x Band 3 SF12/125k ≈ −137 dBm — [SX1276 DS](https://www.mouser.com/datasheet/2/761/SEMT_S_A0007665073_1-2575876.pdf) [search-snippet]. LR2021 −141.5 dBm SF12/125k — [Semtech](https://www.semtech.com/products/wireless-rf/lora-plus/lr2021).
- The Meshtastic 2 m regions use 15.6 kHz BW ("padded to 20kHz to fit cleanly into ham band plans") with the `TINY_FAST` default preset — [PR #10623](https://github.com/meshtastic/firmware/pull/10623) [fetched].

### Own calculations
**Method:**
- Sensitivity S = −174 + 10·log10(BW) + NF_sys + SNR_req.
- SNR_req [training, Semtech DS]: SF7 −7.5, SF8 −10, SF9 −12.5, SF10 −15, SF11 −17.5, SF12 −20 dB.
- Maximum allowable path loss MAPL = Ptx + Gt + Gr − S. "Range-equivalent margin" = MAPL − 20·log10(f_MHz). Higher is better and directly comparable across bands in free space.

**Case A: SF12/125 kHz, 22 dBm, 2.15 dBi both ends, receiver NF 6 dB. kTB(125 kHz) = −123.0 dBm.**

| Band | Environment | NF_sys | S (dBm) | MAPL (dB) | Margin (MAPL−20logf) |
|---|---|---|---|---|---|
| 145 MHz | Residential | 13.3 | −129.7 | 156.0 | **112.8** |
| 145 MHz | Rural | 9.2 | −133.8 | 160.1 | **116.9** |
| 145 MHz | Quiet rural | 6.0 | −137.0 | 163.3 | **120.1** |
| 433 MHz | Residential | 5.9 | −137.1 | 163.4 | 110.7 |
| 915 MHz | any | 6.0 | −137.0 | 163.3 | 104.1 |

The deltas are: 145 vs 915 = +8.7 dB (residential), +12.8 dB (rural), +16.0 dB (quiet rural). 145 vs 433 = +2.1 / +6.2 / +9.4 dB. Each 6 dB is about 2× free-space distance (about 1.4–2× in real terrain, where the path-loss exponent is 3–4).

**Case B: handheld.** Assume a 2 m duck at −6 dBi, a 433 stubby at −3 dBi and a 915 whip at 0 dBi, all assumptions (not sourced), on one end. The 145 MHz residential margin drops by 8.15 dB relative to case A, 433 by 5.15 dB, and 915 by 2.15 dB. Result: 145 vs 915 ≈ +2.7 dB, 145 vs 433 ≈ −0.9 dB. The 2 m advantage largely disappears handheld in town.

**Case C: Meshtastic 2 m actual BW (15.6 kHz).** kTB = −132.1 dBm, which is 9 dB better than 125 kHz at the same SF, paid for with 8× airtime. At 15.6 kHz SF7 (if TINY_FAST is around SF7–8; the SF is unconfirmed), S = −132.1 + 13.3 − 7.5 = −126.3 dBm residential. With a 37 dBm T-Beam BPF and 2.15 dBi both ends, MAPL = 167.6 dB.

### Inferences
- Most of 2 m's benefit comes from **TX power headroom** (a 5–50 W PA is legal for licensees, constant-envelope and cheap at VHF) plus diffraction and foliage. Raw FSPL contributes less, because the urban noise floor eats ~7 dB.
- Fixed/elevated rural nodes benefit most. Urban handhelds benefit least.

### Gaps
- TINY_FAST SF/CR values not retrieved. Case C uses an assumed SF.

---

## 6. BOM, open-hardware node, and Meshtastic firmware changes

### Takeaway
Meshtastic firmware **already supports 2 m** as of the 2.8 line: regions ITU1_2M / ITU2_2M / ITU3_2M (merged 2026-06-03) and a T-Beam BPF variant (merged 2026-07-27). An open design needs an SPI SX1276/78 (the 2 m-legal silicon), a TCXO, a 145 MHz match, a T/R switch, an optional PA + LNA, a 7-pole LPF / BPF and a ≥49 cm antenna. The variant needs `USE_RF95` pins, RXEN/power-enable, a PA gain curve, and `HAS_HAM_2M_ONLY`.

### Cited Findings
- **PR #10623 "Add 2 Meter (~144mhz) Amateur Radio Regions"**, merged 2026-06-03 into develop, labels 2.8.
  - Regions: ITU1_2M "144-146" (default slot 26 = 144.510 MHz), ITU2_2M "144-148" (slot 51 = 145.010 MHz), ITU3_2M "144-148" (slot 33 = 144.650 MHz).
  - "These regions all use 15.6 kHz bandwidth, padded to 20kHz". Default preset TINY_FAST. TINY BW standardized from 15.625 to 15.6 kHz.
  - Tested on `tlora-v2-1-1_6` (144 MHz version) and `t-beam-bpf`.
  - Source: [PR #10623](https://github.com/meshtastic/firmware/pull/10623) [fetched].
- **PR #10558 "Add LilyGo T-Beam BPF 144-148Mhz LoRa node … ~37 dBm (5 Watts)"**, merged 2026-07-27. It adds `variants/esp32s3/t-beam-bpf/{variant.h,pins_arduino.h,platformio.ini}` and `boards/t-beam-bpf.json`. Region choices are limited to 2 m and licensed mode defaults on. RadioLib "doesn't accept 15.625 kHz" — [PR #10558](https://github.com/meshtastic/firmware/pull/10558) [fetched].
- The 2.8.1 build was still alpha when the NodakMesh write-up was published. That write-up also notes that Semtech rates the SX1262 from 150 MHz — [NodakMesh blog](https://nodakmesh.org/blog/meshtastic-ham-mode-amateur-regions) [search-snippet].
- **T-Beam BPF variant.h (concrete template)** — [variant.h](https://github.com/meshtastic/firmware/blob/develop/variants/esp32s3/t-beam-bpf/variant.h) [fetched]:
  - `USE_RF95` (SX1278).
  - Pins: LORA_CS = 1, LORA_RESET = 18, LORA_IRQ/DIO0 = 14, LORA_DIO1 = 21, SCK = 12, MISO = 13, MOSI = 11.
  - `RF95_RXEN 39`, `RF95_POWER_EN 16`.
  - `RF95_MAX_POWER 10`, `NUM_PA_POINTS 10`, `TX_GAIN_LORA {…}`.
  - `HAS_HAM_2M_ONLY 1`.
  - No TCXO define in the file.
- PR #10623 also improved `RF95Configuration.h` conditional pin macros to avoid redefinition — [PR #10623](https://github.com/meshtastic/firmware/pull/10623) [fetched].
- The LoRa config docs say frequency override "bypasses the channel calculation" and is meant for advanced/licensed users — [Meshtastic LoRa config](https://meshtastic.org/docs/configuration/radio/lora/) [search-snippet].
- SX1276 nodes have had firmware-specific RX regressions, e.g. 2.5.4 RX failure fixed by reverting to 2.5.2 — [firmware #4909](https://github.com/meshtastic/firmware/issues/4909) [search-snippet]. The RF95 path is less exercised than SX126x.

### Firmware checklist for a new open 2 m board (inferred from the above)
1. `variants/<mcu>/<board>/variant.h`: `USE_RF95` (SX1276/78) or `USE_SX1262`, with the caveat that SX1262 is out of spec and RadioLib rejects frequencies below 150 MHz unless patched. Add the SPI/IRQ/DIO1/RESET pins.
2. RF switch: `RF95_RXEN`/`RF95_TXEN`, or for SX126x `SX126X_DIO2_AS_RF_SWITCH` / `SX126X_RXEN`. If the PA needs a supply enable, add a power-enable pin (cf. `RF95_POWER_EN`).
3. TCXO: SX127x needs RegTcxo set (XTA input) [training]. SX126x would use `SX126X_DIO3_TCXO_VOLTAGE` (e.g., 1.8 V) [training].
4. PA curve: `NUM_PA_POINTS`/`TX_GAIN_LORA` so that the reported dBm equals the actual antenna-port dBm. `RF95_MAX_POWER` sets the chip drive limit.
5. `HAS_HAM_2M_ONLY 1`, so the BPF-locked hardware cannot be set to a band it can't serve.
6. Region ITU1/2/3_2M (already in 2.8). Set licensed/ham mode (callsign and no encryption, per the ham rules).
7. `boards/*.json` + `platformio.ini` env.

### BOM estimate (own estimate, prices from training knowledge circa 2025; **not sourced this session**)

| Block | Example part | Est. USD (qty 10–100) |
|---|---|---|
| MCU | ESP32-S3-WROOM-1 module, or nRF52840 module (lower sleep current) | 3–6 / 5–9 |
| Transceiver | SX1276/SX1278 QFN | 2–5 |
| TCXO | 32 MHz ±0.5–2.5 ppm | 0.5–1.5 |
| Match + T/R switch | LC 0402/0603 + SPDT switch (PE4259-class) or PIN diodes | 0.5–1.5 |
| PA (optional) | 1–5 W VHF LDMOS / module | 3–25 |
| LNA (optional) | PHEMT LNA + 2 m BPF/SAW | 1–5 |
| LPF | 7-pole LC, high-Q 0805/air-wound | 0.5–2 |
| PMU/charger, regulators | AXP2101 or TP4056 + LDO/buck | 1–3 |
| PCB 4-layer + SMA/BNC | | 2–5 |
| Antenna | λ/4 or 5/8 2 m whip (BNC/SMA) | 5–25 |
| **Total** | **bare 22 dBm node** | **~$15–30** |
| | **5 W node with PA+LNA+filters** | **~$35–70** |

### Inferences
- **nRF52840 vs ESP32-S3:** both are supported in Meshtastic. nRF52840 is far better for solar/battery repeaters. ESP32-S3 has Wi-Fi/MQTT and was LilyGO's choice. The radio choice does not depend on the MCU.
- **Power budget (rough, own):** a 5 W PA at ~50% efficiency draws ~10 W DC, i.e. ~2.7 A at 3.7 V, during TX. At 15.6 kHz BW the airtime per packet is long (hundreds of ms to seconds), so an 18650 cell's current capability and PMU limits matter. RX current is dominated by the MCU plus SX127x RX (~10–12 mA [training]).

### Gaps
- No sourced pricing for T-Beam BPF, E22-170M or individual ICs.
- TINY_FAST SF/CR and exact airtime not retrieved.
- No published open-hardware 2 m Meshtastic PCB found (besides LilyGO's closed design and the TLoRa 144 MHz variant).
