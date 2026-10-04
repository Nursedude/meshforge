# Measuring the Air: LoRa, Power, and Truth in a Two-Site Mesh

**Subtitle:** We turned every radio down and expected quiet. Our instruments said nothing changed. Direct physics said why: the amplifier was flat, the "interference" was inside the box, and several of our own numbers were measuring something else.

**By:** Dude AI (Claude Opus 5.5) — with Shawn, WH6GXZ (Nursedude)

**Date:** 2026-10-03

**Read time:** 14 minutes

---

We turned every Meshtastic radio on a crowded 915 MHz site but two controls down from 30 to 17 dBm and expected the air to go quiet. The one radio we then measured directly barely got quieter: its output was flat, so 17 and 30 dBm radiate within half a dB of each other (published bench data says that amplifier should not be; we suspect its USB power supply). Only at 7 dBm did its level fall, by about 11 dB, and the worst path on site still went out direct, with no relay.

On the way, two independent radio receivers showed that a Reticulum node's constant "-71 dBm interference" was not in the air at all, and half a dozen of our own instruments turned out to be measuring something other than what they claimed. This is the story of those measurements, of the physics underneath them, and of how three mesh transports share one crowded band.

## The lab

Two buildings about 76 m apart through ohia forest, each holding a steel three-shelf rack of Raspberry Pi nodes, every one a few feet from its neighbours on 915 MHz. That density is the whole story: on this site the problem is not reach, it is radios shouting at each other.

- **Yurt:** moc1, moc2, moc3, lehua. moc3 carries an RNode (Heltec V3, stock antenna) for Reticulum; lehua sits in a utility box behind the rack, against the far wall, on the worst path to the tent.
- **Tent:** VolcanoAI, kiai, moc5, moc. moc bridges the two Meshtastic presets with a second radio; moc5 hosts an Airspy Mini SDR.
- **Elsewhere:** Farley-server, a Station G2 with an 8 dBi antenna 22 ft up, carries the long legs to the island's public nodes.

Three transports share the band. Meshtastic runs two presets: LongFast on frequency slot 20 (FS20, the busy one) and ShortTurbo on another. Reticulum runs over RNodes on its own frequency (SF7, 250 kHz). Public MeshCore nodes use a fourth (our own MeshCore relay is still a plan). RNS is the hub that carries traffic between meshes that cannot hear each other.

Two software-defined radios kept us honest: the Airspy Mini (12-bit) and a NESDR SMArTee XTR (8-bit). Neither trusts the other's levels; both agree on what is and is not in the air.

## LoRa in one page

LoRa trades speed for sensitivity: each step up in spreading factor doubles the time a symbol takes and lets the receiver decode about 2.5 dB further below the noise. That is how a 22 dBm radio reaches a mountaintop.

| Spreading factor | 7 | 8 | 9 | 10 | 11 | 12 |
| --- | --- | --- | --- | --- | --- | --- |
| Lowest decodable SNR (dB) | -7.5 | -10 | -12.5 | -15 | -17.5 | -20 |
| SX1262 sensitivity at 250 kHz (dBm) | -121 | | | | | -134 |

Source: [SX1262 datasheet](https://cdn.sparkfun.com/assets/6/b/5/1/4/SX1262_datasheet.pdf), Tables 6-1 and 3-8. Sensitivity follows from thermal noise plus the radio's noise figure plus that SNR limit:

    S = -174 + 10·log10(BW) + NF + SNR_lim

With a 6 dB noise figure at 125 kHz and SF12 this gives -137 dBm, the datasheet figure exactly. Doubling the bandwidth halves the airtime and costs 3 dB.

**Decoding below the noise.** De-chirping folds a symbol's energy into one frequency bin while the noise stays spread across all of them ([AN1200.22](https://www.stderr.nl/static/files/Hardware/Electronics/LoRa/AN1200.22.pdf)). A 2026 bench study on Meshtastic presets saw SF12 decode down to about -18 dB SNR, failing abruptly within 2-4 dB of the limit ([Hernandez Ortiz et al.](https://arxiv.org/pdf/2605.17063)).

**Why reported SNR stops rising.** Semtech's own FAQ says anything above +5 dB "is meaningless" ([Semtech LoRa FAQ](https://www.semtech.com/design-support/faq/faq-lora)); the same bench study measured ceilings of 8-11 dB at 250 kHz and 5-7 dB at 125 kHz. The register is not the limit. No public document explains the estimator's ceiling, so treat a strong-link SNR as "plenty" and nothing more precise.

**Collisions and neighbours.** Two packets on the same spreading factor usually survive if one is about 6 dB stronger, a conservative convention ([Croce et al.](https://iris.unipa.it/retrieve/handle/10447/301652/1028841/08267219.pdf) found capture near 0 dB in simulation). Different spreading factors are only quasi-orthogonal. The rack is the harder problem. A rough estimate, not a measurement: a neighbour three feet away radiating 17 dBm arrives near -8 dBm, and since a "17" setting on our measured amplified radio radiated close to full output, the real figure may be near 0 dBm, close to the E22 module's +10 dBm input limit. The SX1262's 88 dB blocking at 1 MHz is a best case, measured with the wanted signal just above sensitivity; a receiver driven that hard risks desensing or compressing whatever the channel spacing.

**The path.** At 915 MHz the first Fresnel zone over a 76 m path is about 2.5 m in radius at midpath; split it with a relay and each hop needs about 1.8 m. Foliage in leaf costs roughly 13-17 dB over 50-76 m of trees by [Weissberger's model](https://en.wikipedia.org/wiki/Weissberger%27s_model); [ITU-R P.833-10](https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.833-10-202109-I!!PDF-E.pdf) caps excess woodland loss near 26.5 dB at 949 MHz. Even the worst case leaves tens of dB of margin on a 76 m link, so closing at 7 dBm is expected; it says nothing precise about the canopy.

## Three transports on one band

Meshtastic floods everything and relies on overhearing; MeshCore floods once, then follows a learned path; Reticulum floods only announcements and path requests and forwards data hop by hop on next-hop tables. That single difference decides how much airtime each spends per message.

| | Meshtastic | MeshCore | Reticulum (RNS) |
| --- | --- | --- | --- |
| Address | 32-bit node number; channel by name and key | Ed25519 public key, 1-3 byte hashes on air | 128-bit hash of a destination, tied to an identity |
| Routing | Managed flooding; direct messages learn a next hop (2.6+) and fall back to flooding | Flood to discover, then the path rides in the packet; channels always flood | Announces build next-hop tables at transport nodes |
| Who relays | Every node except client-mute | Repeaters only | Only nodes with transport enabled |
| Delivery proof | Overheard relay counts as an ACK for broadcasts; explicit ACK for direct messages | ACK, or the returned path | Signed proof; link receipts |
| Built-in brakes | 25% / 40% channel gates on telemetry, position and node info (never on text or relays) | Configurable airtime factor on repeaters | Announces capped at 2% of interface bandwidth |
| Hears the others | No | No | Any medium; other meshes need a gateway |

**Meshtastic** waits before relaying, for a window weighted by signal-to-noise so the weakest-hearing (usually farthest) node relays first, and cancels if it hears someone else do it ([FloodingRouter](https://github.com/meshtastic/firmware/blob/727d8c31dcd2a8e577b672bc5da394a88751bee5/src/mesh/FloodingRouter.cpp), [contention window](https://github.com/meshtastic/firmware/blob/727d8c31dcd2a8e577b672bc5da394a88751bee5/src/mesh/RadioInterface.cpp)). The default hop limit is 3. The frequency comes from a hash of the channel name: two meshes on the same preset with different names usually sit on different frequencies and never hear each other.

**MeshCore** only lets repeaters forward floods. A first message floods, the reply carries back the list of repeaters it crossed, and later messages travel only along that path ([FAQ](https://github.com/meshcore-dev/MeshCore/blob/a366955cb2f67b8e6842d4f00d2b6a554dddd88a/docs/faq.md), [packet format](https://github.com/meshcore-dev/MeshCore/blob/a366955cb2f67b8e6842d4f00d2b6a554dddd88a/docs/packet_format.md)).

**Reticulum** addresses destinations, not radios, so one address is reachable over LoRa, TCP or anything else without translation. Encryption is end to end; a bridge never holds user keys ([Understanding Reticulum](https://github.com/markqvist/Reticulum/blob/e40191b3d193b46b7f2d8a44424a594cd758839b/docs/source/understanding.rst)). Its packets carry more header and cryptographic overhead than a Meshtastic text, but it never floods data, which is why it works as the hub between meshes that cannot hear each other.

One difference matters for a crowded rack. Meshtastic's channel check detects only LoRa chirps; an RNode also holds off when it sees any non-LoRa energy 11 dB over its noise floor, if interference avoidance is enabled ([RNode firmware](https://github.com/markqvist/RNode_Firmware/blob/56775c51b60a6190242e324c210997dcc7637e53/RNode_Firmware.ino)). That is how a constant false "interference" can silence a radio.

## The 25% knee

Meshtastic's firmware draws a line at 25% of airtime: above it, a node stops sending its polite periodic broadcasts. A simple collision model shows why a quarter is a sensible place for that line.

![In a simple collision model, 25% busy leaves about 56% of packets surviving. Heuristic survival = (1 − busy)², a model not a measurement (textbook e^(−2G) gives 61% at 25%). Dots: yurt FS20 11.4% → 78% and tent FS20 12.6% → 76% (SDR-measured), lehua peak sample 44% → 31% (as its radio reported)](img/2026-10-03-aloha-knee.png)

The curve is a simple heuristic in the spirit of pure ALOHA, where nobody coordinates: a packet survives if the channel is free when it starts and stays free for one packet time, so survival is (1 − busy)². The textbook Poisson form, e^(−2G), gives 61% rather than 56% at 25%; either way survival falls steadily with load, and pure ALOHA's throughput peaks at 18.4% of capacity ([ALOHA](https://platform.commit.tu-berlin.de/en/book/telecom/chapter/ch19/section/s04)). LoRa is kinder than pure ALOHA because radios listen first and the capture effect saves the stronger packet. It is harsher when nodes cannot hear each other, as between our yurt and tent. Studies of LoRa at scale reach the same conclusion: success falls exponentially as nodes are added on one spreading factor ([Georgiou and Raza](https://ar5iv.arxiv.org/html/1610.04793), [Adelantado et al.](https://arxiv.org/pdf/1607.08011)).

Two cautions about the number a radio reports. Utilization counts only airtime the radio could lock onto, so interference it cannot decode is invisible to it. And it is a 60-second window, sampled every 15 minutes. Every LongFast box crossed 25% for at least 16 to at least 45 of the 180 minutes we examined (lower bounds, per box), and the 15-minute samples rarely caught it; on moc, which no app drains, they never could. Only the firmware's own "Ch. util >25%, skip send" lines did.

The practical lever follows from flooding: every node that hears a packet may relay it. Fewer listeners per packet (lower power, higher placement, sensible hop limits) means fewer copies in the air. More nodes at lower power, with height doing the reaching, adds paths without adding load everywhere.

## The experiment: turning the power down

We cut every Meshtastic radio on site from 30 to 17 dBm except two controls (lehua was missed until 13:11), expecting quieter racks. The one radio we then measured directly barely got quieter: its output stayed flat, so 17 and 30 radiate almost the same.

The first readings said nothing changed. Signal-to-noise between our own radios moved a quarter of a dB (one reporting step); channel load rose on the control box we never touched (time of day). Rather than call that "no effect", we went to direct physics: an SDR in the same tent, listening while one radio stepped through its settings and sent marked test packets we could pick out by their exact airtime.

![Received level barely moves from setting 12 to 30; setting 7 cuts it about 11 dB. VolcanoAI HAT at one nearby SDR, relative to setting 17, not calibrated radiated power: set 30 (chip 22) +0.4 dB (run 2, within 0.3 dB), set 17 0 dB, set 12 −1.5 dB and set 7 −11.4 dB (run 3, about ±2 dB), against a dashed line showing a linear amplifier](img/2026-10-03-pa-transfer.png)

The chip itself caps at 22 dBm, so a setting of 30 drives it at 22. Above a setting of about 12 the received level stops following the setting; only at 7 does it fall, by about 11 dB. These are relative levels at one SDR position, not calibrated radiated power. The 30 point comes from a run that repeated within 0.3 dB; the 12 and 7 points come from a later run whose reference drifted 1.8 dB, so read them as about ±2 dB. One honest failure on the way: our first run set the SDR's gain too high and measured the receiver's own ceiling, not the transmitter. A gain sweep found the headroom and the rerun is what you see here.

This contradicts the published data, and we have not resolved it. Bench measurements of the EBYTE E22-900M30S module show a nearly linear amplifier, about 7 to 11 dB of gain, all the way to 20-22 dBm of drive ([S5NC bench test](https://github.com/S5NC/EBYTE_ESP32-S3/blob/e53961d2b8bd133f0ded5b5f001bb938f5c36e85/E22-900M30S%20power%20output%20testing.txt), [EBYTE data via ndoo.sg](https://ndoo.sg/projects:amateur_radio:meshtastic:components?rev=1729513582)). The module draws about 650 mA when transmitting and wants more than 5 V for best performance ([EBYTE manual](https://voltiq.ru/datasheets/ebyte/E22-900M30S-user-manual.pdf)). Our radio hangs off a USB port. A current- or voltage-limited supply would flatten the curve long before the amplifier saturates on its own. That is our hypothesis, not a finding; the test is the same step with the radio on a powered hub. Until then, the curve above describes this installation, not the module.

The question we set out to answer then answered itself: at 7 dBm, the worst path on site (VolcanoAI in the tent to lehua in the far corner of the yurt) still went out direct on both traceroutes. One reply came back direct, the other through one relay.

## The interference that wasn't in the air

The yurt's RNode reported interference at -71 dBm in 65% and 96% of its polls across two 10-minute windows (60-82% on earlier days), through both power cuts. Two independent receivers placed beside it heard no sustained signal in its channel while it said so.

| When the RNode said | Polls | Receiver beside it, RNode channel |
| --- | --- | --- |
| Interference now, -71 dBm | 34 (Airspy) + 50 (NESDR) | +0.7 / +0.9 dB over floor (median), 0% busy (median) |
| Nothing now | 18 + 1 | +1.1 / +0.7 dB over floor (median), 0% busy (median) |

The receivers differed in everything that could fool us: 12-bit against 8-bit, different antennas, different hours. A real -71 dBm signal should have stood well clear of the floor at which these receivers see our own LoRa packets (the same Airspy shows those at +26 to +43 dB); that is an estimate, since neither SDR is calibrated. One thing they did catch: the NESDR logged brief 5 ms impulses in that channel (+15 dB, 0.24% of the time), not LoRa and not yet explained.

So what is the number? Reading the RNode firmware: it flags interference when the in-band RSSI exceeds the radio's own noise-floor estimate by 11 dB at a moment with no LoRa carrier, and reports that instant's RSSI. Conducted noise on the USB cable, a sagging supply (this Pi had logged under-voltage) or the board itself can assert it, with nothing in the air. Reticulum passes the value through unfiltered. The RNode's own reported noise floor, -96 dBm, sits about 18 dB above what thermal noise and a typical noise figure give at 250 kHz, which also points inside the box.

It is not harmless. If interference avoidance is enabled in the radio's EEPROM, the RNode defers its own transmissions while the flag is up. By our reading of the firmware, an autoinstalled RNode leaves it disabled; ours is unverified without taking the serial port. The cure is not more power or a better antenna. It is a ferrite, a clean supply, or a dummy-load test to prove the source is inside the box.

## Instruments that lie politely

None of these crashed or threw an error. Each returned a reasonable-looking number that meant something other than what it seemed to.

| Reading | What it seemed to say | What it was | How we caught it |
| --- | --- | --- | --- |
| SNR near +6 dB on every local link | "nothing changed" after the power cut | The radio's SNR estimate tops out on strong links | Cut one radio ~11 dB (SDR-measured); its SNR at lehua, 76 m away, moved 0.25 dB: one reporting step, from three readings |
| No channel-utilization samples on moc | "over the knee all day" | Samples are logged only while an app drains the radio's queue | Read the firmware branch that logs them |
| Noise floor -120 dBm | "a beautifully quiet floor" | The firmware's default before its first measurement | Seen live 255 s after a reboot |
| No route back on a direct traceroute | "the reply never came" | Phone apps draw the return leg only from relays; the return SNR exists | The originating radio's own log held both legs |
| A tx_power that reads back as changed | "done" | A web-client edit held in an open transaction: in RAM, not on air, not saved | The journal said "Delay save" and nothing else |
| SDR peaks identical at every power | "the amplifier ignores the setting" | The receiver's own ceiling at high gain | A gain sweep; the rerun at gain 0 had 22 dB headroom |

The common thread: each number was true about something. The mistake was ours whenever we read it as being about something else.

## Operating rules we now live by

Measure first; assert only what you cannot measure, and say so; fall back on physics when neither is available.

1. **Ask what the number is OF.** A measurement of the wrong quantity carries more authority than a bad guess, and does more damage.
2. **Measure the measurer.** Check an instrument's headroom and its blind spots before trusting its verdict, especially when you built it.
3. **Every installation has its own power curve.** "Set 17 dBm" means nothing until you have measured where your output stops following the setting. Ours stayed within about 2 dB from 12 to 30.
4. **Height and hops beat watts.** One antenna at 22 ft carries the long legs; the racks stay quiet and the mesh supplies the reach.
5. **Apply settings so they commit.** Use a path that saves and confirms (the CLI), then verify from the radio's own log. Restart any app that talks to the radio afterwards; a reboot can strand it.
6. **Keep a control.** One radio left unchanged told us the afternoon was busier than the morning, which would otherwise have looked like an effect.
7. **Unobservable is not healthy.** A missing sample, a default value or a hidden field should read as "cannot tell", never as quiet.

## How this compares to other work

The literature backs every mechanism we propose; it disagrees with two of our numbers, and it holds no field study of Meshtastic congestion to compare our third against.

| Our finding | What others report | Verdict |
| --- | --- | --- |
| Output flat from setting 12 to 30 on a USB-powered E22 radio | Vendor: lower voltage means lower power, 650 mA at TX. Meshtastic's own MeshToad config: reduce to 10 dBm "to avoid over-drawing the USB port". Bench curves for similar modules show compression, not flatness | Agree on the cause, disagree on the shape |
| Reported SNR stops near +6 dB on strong LongFast links | Semtech: above +5 dB "is meaningless". Bench study: 8-11 dB ceiling at 250 kHz | Agree a ceiling exists; ours is 2-5 dB lower |
| Every LongFast box crossed the 25% gate, for at least 16 to at least 45 of 180 minutes, mostly unseen in 15-minute samples | Firmware: utilization is a 60 s window, "a snapshot rather than an average". Meshtastic warns router roles off above 25% | Agree on the mechanism; no published field data found |
| An RNode's -71 dBm "interference" was not in the air | RNode firmware: the flag is the chip's own RSSI against a learned floor; it re-learns only below -83 dBm, so a steady -71 never triggers a re-learn | Agree: the firmware explains why the flag recurs, not why it sometimes drops for minutes |
| Rack neighbours near the input limit (estimated); the one measured radio changed about 0.4 dB from 30 to 17 | E22 module: +10 dBm maximum input before damage risk. SX1262: 88 dB blocking at 1 MHz. Different spreading factors are not fully orthogonal | Consistent with the specs; no co-location study found |

**The amplifier.** The cause has strong support. EBYTE's manual says "the lower the voltage is, the lower the transmitting power is" ([manual](https://voltiq.ru/datasheets/ebyte/E22-900M30S-user-manual.pdf)), and the config file our own radio loads tells users to cap it "to avoid over-drawing the USB port" ([lora-usb-meshtoad-e22.yaml](https://github.com/meshtastic/firmware/blob/727d8c31dcd2a8e577b672bc5da394a88751bee5/bin/config.d/lora-usb-meshtoad-e22.yaml)). The shape does not match: a comparable USB module's maker publishes roughly +4 dB for the same 17-to-22 dBm drive step ([uMesh](https://github.com/linser233/uMesh/blob/74a56080d226e4a8525b6072a04fbb63c2bb47cf/RF_Power.md)), and a Heltec V4 measured with a spectrum analyser flattened near +27-28 dBm ([MeshCore #1708](https://github.com/meshcore-dev/MeshCore/issues/1708)). Our +0.4 dB is flatter than either. A failing part stays on the list beside the supply.

**The SNR ceiling.** Semtech and a gateway vendor agree that packet SNR is a demodulator figure, not "signal above the noise", and that it stops rising ([Semtech FAQ](https://semtech.com/design-support/faq/faq-lora), [MultiTech](https://www.multitech.net/developer/?p=18209)). The 2026 bench study puts LongFast's ceiling at 8-11 dB ([Hernandez Ortiz et al.](https://arxiv.org/html/2605.17063)). Ours sits lower; a noisier rack is a plausible reason we have not tested.

**The knee.** The firmware's own comment calls the 60-second figure a snapshot ([airtime.h](https://github.com/meshtastic/firmware/blob/727d8c31dcd2a8e577b672bc5da394a88751bee5/src/airtime.h)), and Meshtastic tells router operators to stop above 25% ([ROUTER_LATE](https://meshtastic.org/blog/demystifying-router-late/)). Its case that managed flooding scales past 100 nodes rests on simulation ([managed flood](https://meshtastic.org/blog/why-meshtastic-uses-managed-flood-routing/)). We found no published field measurement of time spent over the gate, though that is a statement about our search, not about the field.

**The false interference.** The RNode firmware explains our result rather than merely permitting it: its recalibration only fires for assertions below -83 dBm, so a steady -71 dBm keeps re-asserting; that explains why the flag recurs, though not why it sometimes drops for minutes ([RNode_Firmware.ino](https://github.com/markqvist/RNode_Firmware/blob/56775c51b60a6190242e324c210997dcc7637e53/RNode_Firmware.ino)). We found no report of the same on a Heltec V3, and no primary source on USB-borne self-noise.

**Scale.** Classic LoRa capacity work finds success falling exponentially as nodes share a spreading factor ([Georgiou and Raza](https://ar5iv.labs.arxiv.org/html/1610.04793)), and inter-SF interference costs a further 10% or so at scale ([Mahmood et al.](https://arxiv.org/abs/1808.01761)). Those studies assume nodes spread over kilometres, not three feet apart on a shelf.

## Sources

**Radio physics**

- [Semtech SX1261/2 datasheet, Rev 1.2](https://cdn.sparkfun.com/assets/6/b/5/1/4/SX1262_datasheet.pdf): SNR limits, sensitivity, selectivity, time on air
- [Semtech AN1200.22, LoRa Modulation Basics](https://www.stderr.nl/static/files/Hardware/Electronics/LoRa/AN1200.22.pdf)
- [Semtech LoRa FAQ](https://www.semtech.com/design-support/faq/faq-lora): SNR above +5 dB, RSSI saturation
- [Hernandez Ortiz et al., 2026](https://arxiv.org/pdf/2605.17063): SX1262 Meshtastic presets on a guided link, SNR ceilings
- [Croce et al., IEEE Commun. Lett. 2018](https://iris.unipa.it/retrieve/handle/10447/301652/1028841/08267219.pdf): capture and inter-SF interference
- [RadioLib SX126x time-on-air implementation, commit 02f0afaa](https://github.com/jgromes/RadioLib/blob/02f0afaaa32de07874e2954dd929e8c08356e24f/src/modules/SX126x/SX126x.cpp)

**Capacity**

- [Pure and slotted ALOHA](https://platform.commit.tu-berlin.de/en/book/telecom/chapter/ch19/section/s04)
- [Georgiou and Raza, Can LoRa Scale?](https://ar5iv.arxiv.org/html/1610.04793)
- [Adelantado et al., Understanding the Limits of LoRaWAN](https://arxiv.org/pdf/1607.08011)
- [Mahmood et al., Scalability Analysis of a LoRa Network under Imperfect Orthogonality](https://arxiv.org/abs/1808.01761)

**Propagation**

- [ITU-R P.833-10, Attenuation in vegetation](https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.833-10-202109-I!!PDF-E.pdf)
- [Weissberger's foliage model](https://en.wikipedia.org/wiki/Weissberger%27s_model)

**Hardware**

- [EBYTE E22-900M30S manual](https://voltiq.ru/datasheets/ebyte/E22-900M30S-user-manual.pdf)
- [S5NC E22-900M30S output test, commit e53961d2](https://github.com/S5NC/EBYTE_ESP32-S3/blob/e53961d2b8bd133f0ded5b5f001bb938f5c36e85/E22-900M30S%20power%20output%20testing.txt)
- [RNode firmware, commit 56775c51](https://github.com/markqvist/RNode_Firmware/blob/56775c51b60a6190242e324c210997dcc7637e53/RNode_Firmware.ino)

**Transports**

- [Meshtastic firmware, commit 727d8c31](https://github.com/meshtastic/firmware/tree/727d8c31dcd2a8e577b672bc5da394a88751bee5) and [mesh algorithm docs](https://meshtastic.org/docs/overview/mesh-algo/)
- [MeshCore, commit a366955c](https://github.com/meshcore-dev/MeshCore/tree/a366955cb2f67b8e6842d4f00d2b6a554dddd88a)
- [Reticulum, commit e40191b3](https://github.com/markqvist/Reticulum/tree/e40191b3d193b46b7f2d8a44424a594cd758839b)

**Cited in the comparison**

- [Meshtastic lora-usb-meshtoad-e22.yaml, commit 727d8c31](https://github.com/meshtastic/firmware/blob/727d8c31dcd2a8e577b672bc5da394a88751bee5/bin/config.d/lora-usb-meshtoad-e22.yaml)
- [Meshtastic airtime.h, commit 727d8c31](https://github.com/meshtastic/firmware/blob/727d8c31dcd2a8e577b672bc5da394a88751bee5/src/airtime.h)
- [Meshtastic: Demystifying ROUTER_LATE](https://meshtastic.org/blog/demystifying-router-late/)
- [Meshtastic: Why managed flood routing](https://meshtastic.org/blog/why-meshtastic-uses-managed-flood-routing/)
- [uMesh RF power measurements, commit 74a56080](https://github.com/linser233/uMesh/blob/74a56080d226e4a8525b6072a04fbb63c2bb47cf/RF_Power.md)
- [MeshCore issue #1708, Heltec V4 output](https://github.com/meshcore-dev/MeshCore/issues/1708)
- [MultiTech, LoRa SNR and RSSI](https://www.multitech.net/developer/?p=18209)

Our own measurements: two SDR dwells per site, three transmitter A/B runs and the fleet's own logs, all on 3 October 2026.

_The live, citable version of this paper (with interactive charts) is the [Claude Doc](https://claude.ai/code/artifact/57c1f68a-62fa-40e8-9c39-981fc6134279)._
