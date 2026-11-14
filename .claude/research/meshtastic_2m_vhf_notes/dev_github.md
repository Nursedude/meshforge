# Meshtastic developer work toward 2 m / VHF (144 MHz) operation — primary-source (GitHub) notes

Research date: 2026-11-14. Method: `git clone --filter=blob:none` of meshtastic/firmware, protobufs, Meshtastic-Android, Meshtastic-Apple, web, and a `--depth 300` clone of meshtastic/meshtastic (docs). Every commit hash below was read with `git show`/`git log` on those clones this session (VERIFIED as present in the repos). Region-table and code quotes are from `raw.githubusercontent.com` / `git show origin/develop:<path>` fetched 2026-11-14.

Access limits this session (affects what is missing): the GitHub REST API returned 403 (rate limit / session not enabled), so **issue and discussion threads, PR review comments, and PR open/close dates could not be read**. PR numbers come from squash-commit titles `(#NNNN)`. Dates are commit/author dates on the merged commit, not PR-open dates. meshtastic.org did not resolve from this sandbox; the docs were read from the meshtastic/meshtastic GitHub repo instead (same source the site builds from).

Commit URL pattern: `https://github.com/meshtastic/firmware/commit/<hash>`; PR pattern `https://github.com/meshtastic/firmware/pull/<n>`; protobufs `https://github.com/meshtastic/protobufs/...`.

---

## Q1. Which GitHub issues/PRs/commits deal with 2 m / 144 MHz, VHF, ham bands, new RegionCodes, override_frequency?

### Takeaway
2 m support is **merged and shipped**, not proposed. In Apr–Jul 2026 the project added ITU-split amateur regions (2 m ×3, 70 cm ×3, 1.25 m ×1), two 15.6 kHz "TINY" presets for 2 m, licensed-only gating, two 144 MHz LilyGO targets, and on-device Ham Mode UI. These landed in firmware 2.8.0 (tagged 2026-08-29/31; 2.8.0 was later revoked over a signing bug and replaced by 2.8.1, tagged 2026-09-28). The developer driving it is "Austin" (GitHub `vidplace7`), with Ben Meadors, Thomas Göttgens, Jason P, Benjamin Faershtein, Jonathan Bennett and James Rich (Android) on related work.

### Cited Findings — protobufs (meshtastic/protobufs, master)
- 2026-04-21, Thomas Göttgens, `d004f50` "Add ITU Region 1 and 2/3 Amateur Radio bands (#897)". This is the first ham-region proto change in master. — [protobufs #897](https://github.com/meshtastic/protobufs/pull/897)
- 2026-05-22, vidplace7, `b61834f` "Add ITU Region 1 and Region 2/3 Amateur Radio 70cm band identifiers to config.proto", merged as PR #920 (`a5e522d`, 2026-05-23, Ben Meadors). — [protobufs #920](https://github.com/meshtastic/protobufs/pull/920)
- 2026-05-24/25, vidplace7, `b9fe83c` "Update ham regions, split by ITU" (PR #922) and `2e284d9` "master: Split ITU regions for 2M ham bands" (PR #923). Both merged 2026-05-26 by Ben Meadors. — [protobufs #922](https://github.com/meshtastic/protobufs/pull/922), [#923](https://github.com/meshtastic/protobufs/pull/923)
- 2026-05-30, vidplace7, `0f9c24a` "Add ITU Region 2 Amateur Radio 1.25m band to Config message" (PR #933, merged 2026-06-05). — [protobufs #933](https://github.com/meshtastic/protobufs/pull/933)
- 2026-06-02, Austin, `8b68f27` "Add TINY_FAST and TINY_SLOW presets for Amateur Radio compliance (#934)". — [protobufs #934](https://github.com/meshtastic/protobufs/pull/934)
- 2026-06-09, vidplace7, `9dacd6b` 'Add "Long_Name" to HamParameters' (PR #941, merged 2026-06-12). `HamParameters` itself dates to 2023-02-04 (`f55635c`, Ben Meadors, "Ham mode admin message"). — [protobufs #941](https://github.com/meshtastic/protobufs/pull/941)
- 2026-07-01, Ian McEwen, `40ee668` "Add is_unmessagable and is_licensed as fields for DeviceProfile exports" (PR #971). — [protobufs #971](https://github.com/meshtastic/protobufs/pull/971)
- Android commit d81a91a9d cites "protobufs #951" as the region→preset compatibility map (`LoRaRegionPresetMap`). I did not open #951 itself. — [Android d81a91a9d / protobufs #951](https://github.com/meshtastic/protobufs/pull/951)

### Cited Findings — firmware (meshtastic/firmware, develop)
- 2026-05-26, Ben Meadors, `c366296ab` "Update LoRaConfig region codes and add new amateur radio bands": bumps the protobufs submodule and regenerates `config.pb.h`. — [commit](https://github.com/meshtastic/firmware/commit/c366296ab)
- 2026-06-03, Austin, `3e873c51b` "Add TinyFast and TinySlow presets to modem configuration and menu actions (#10597)". Touches MeshRadio.h, RadioInterface.cpp, InkHUD menus and test_radio. — [firmware #10597](https://github.com/meshtastic/firmware/pull/10597)
- 2026-06-03, Austin, `ef51c7ec1` "Add 2 Meter (~144mhz) Amateur Radio Regions (#10623)". Commit body: "Default slots: ITU1_2M: Slot 26 (144.510 MHz) / ITU2_2M: Slot 51 (145.010 MHz) / ITU3_2M: Slot 33 (144.650 MHz)". It touches RF95Configuration.h, MeshRadio.h, RadioInterface.cpp (+33), MenuHandler.cpp and InkHUD. — [firmware #10623](https://github.com/meshtastic/firmware/pull/10623)
- 2026-06-04, Austin, `4b424f023` "Add support for T-Beam Supreme 144mhz variant (#10624)". Body: "Adds support for T-Beam-Supreme 144mhz version … Tested / working." It links LilyGO's `t_beam_supreme_144_hw.md` and adds `#define USE_RF95` to `variants/esp32s3/tbeam-s3-core/variant.h` with the comment "// 144mhz variant uses 'RF95' (SX1278)". — [firmware #10624](https://github.com/meshtastic/firmware/pull/10624); [LilyGO doc](https://github.com/Xinyuan-LilyGO/LilyGo-LoRa-Series/blob/master/docs/en/t_beam_supreme/t_beam_supreme_144_hw.md)
- 2026-06-06, Austin, `57f678240` "Add 1.25 Meter '125cm' amateur radio region support (#10638)". — [firmware #10638](https://github.com/meshtastic/firmware/pull/10638)
- 2026-06-09, Jason P, `e02866365` "BaseUI: First attempt at Ham Mode implementation (#10663)". Body: "TX Disabled if N0CALL, enabled if properly set … Allow users back to Normal mode if they don't pick an ITU region". — [firmware #10663](https://github.com/meshtastic/firmware/pull/10663)
- 2026-07-03, Austin, `397ff8523` "Add 70cm amateur radio region support (#10627)". — [firmware #10627](https://github.com/meshtastic/firmware/pull/10627)
- 2026-07-26, Benjamin Faershtein, `1e982fa78` "Sign plaintext packets in licensed mode (#10969)". Co-authored by Ben Meadors and Austin. Body: "Preserve and publish identity keys in licensed mode so existing XEdDSA signatures can authenticate plaintext traffic … keeping PKI encryption disabled". It also contains "fix(baseui): allow confirmed ham region selection". — [firmware #10969](https://github.com/meshtastic/firmware/pull/10969)
- 2026-07-27, Austin, `a640d44b5` "Add T-Beam BPF - 144-148 Mhz LoRa @ ~37 dBm (5 Watts) (#10558)". Co-authored by Thomas Göttgens. Adds `boards/t-beam-bpf.json` and `variants/esp32s3/t-beam-bpf/{variant.h,pins_arduino.h,platformio.ini}`, plus edits to RF95Interface.cpp, NodeDB.cpp, Power.cpp and MenuHandler.cpp. Squash log lines include: "Default PROFILE_HAM to slot 17 — This is an appropriate default in the USA but not the EU. The slot override really should follow the region itself, not the regionprofile." and "This is a 5-6W radio!! Add TX_GAIN_LORA". PR #10558 has a lower number than #10623, so it was opened earlier but merged later; I could not read its open date. — [firmware #10558](https://github.com/meshtastic/firmware/pull/10558)
- 2026-07-30, Thomas Göttgens, `daf121358` "Licensed channel defaults … (#11286)": "Strip the default PSK when licensed defaults are installed". — [firmware #11286](https://github.com/meshtastic/firmware/pull/11286)
- 2026-10-19, Jonathan Bennett, `20dc8e31e` "Key verification: refuse in licensed (ham) mode (#12201)". Body: "licensed operation neither sends nor decrypts PKI … A licensed node now refuses to start a session". — [firmware #12201](https://github.com/meshtastic/firmware/pull/12201)
- Older, 2026-03-22, Tom, `9e61c4462` "fix(routing): prevent licensed users from rebroadcasting packets to or from unlicensed users (#9958)". — [firmware #9958](https://github.com/meshtastic/firmware/pull/9958)
- Release containment, from `git tag --contains`: `ef51c7ec1`, `4b424f023`, `57f678240`, `397ff8523`, `a640d44b5` and `1e982fa78` are all first in `v2.8.0.7239fe8` (tagged 2026-08-29) and `v2.8.0.47db0e3` (2026-08-31). They are **not** in the `2.7` branch (latest 2.7 tag `v2.7.26.54e0d8d`). The latest tags are v2.8.2.d5f1619 (2026-11-03) and v2.8.2.4c89b69 (2026-11-01). — [firmware tags](https://github.com/meshtastic/firmware/tags)

### Cited Findings — clients and docs
- Android, James Rich:
  - `8fb2001b5` / `d81a91a9d` (2026-06-24): "consume region→preset compatibility map + TINY presets" and "gate region→preset map + TINY presets on firmware capability" (#5834, #5941).
  - `c09bf1a40` (2026-08-02): "feat(settings): add 2.8 amateur and EU Lite/Narrow LoRa regions (#6548)".
  - `b79ec33b8` (2026-08-12): "warn before enabling licensed (ham) mode (#6632)".
  - [Android #6548](https://github.com/meshtastic/Meshtastic-Android/pull/6548), [#6632](https://github.com/meshtastic/Meshtastic-Android/pull/6632), [#5941](https://github.com/meshtastic/Meshtastic-Android/pull/5941)
- Web, Dan Ditomaso, `5ffd1b0` (2026-10-15): "feat(web): filter LoRa presets by region and firmware". No ham-specific web commit was found. — [web commit](https://github.com/meshtastic/web/commit/5ffd1b0)
- Apple: no commit in 2026 whose message names ITU, amateur or ham regions turned up. The nearest is `a576f487` (2026-11-01, Garth Vander Houwen), "Add a radio setup flow for region, preset and name (#2873)". Protobufs `f897a29` (2026-09-13, Garth Vander Houwen), "Annotate config fields and enum values from the Apple client", added the human labels that now sit on the ITU enum values. — [Apple #2873](https://github.com/meshtastic/Meshtastic-Apple/pull/2873)
- Blog "Let's talk about 2.8" (date 2026-10-08, author `crichton`):
  - Radio list: "Tiny Fast and Tiny Slow presets, and spreading factors 5 and 6 on supported hardware … Amateur regions for 2m, 1.25m, and 70cm, with Ham Mode setup on the device."
  - On licensed operation: "Licensed amateur operators get signed plaintext. Messages are authenticated without being encrypted."
  - Hardware list includes "T-Beam BPF".
  - Revocation: "2.8.0 is revoked and 2.8.1 replaces it."
  - [docs repo blog/2026/October/lets-talk-about-2-8.mdx](https://github.com/meshtastic/meshtastic/blob/master/blog/2026/October/lets-talk-about-2-8.mdx)
- Docs `docs/blocks/_lora-regions.mdx` lists ITU1_2M 144.0–146.0, ITU2_2M 144.0–148.0, ITU3_2M 144.0–148.0, ITU2_125CM 220.0–225.0, ITU1_70CM 430–440, ITU2_70CM 420–450 and ITU3_70CM 430–450, each with duty cycle 100 and power 30. It adds: "The ITU1_*, ITU2_*, and ITU3_* regions … can only be selected in licensed mode … encryption is not permitted, and you must transmit your call sign." — [_lora-regions.mdx](https://github.com/meshtastic/meshtastic/blob/master/docs/blocks/_lora-regions.mdx)

### Inferences
- The arc ran from proto design (Apr–May 2026) to firmware regions and presets (Jun) to 70 cm and hardware (Jul) to licensed-mode crypto and signing follow-ups (Jul–Oct). That points to a coordinated core-team effort, not a community fork.
- The T-Beam BPF squash log shows the "single PROFILE_HAM with slot 17" design being replaced by per-region `override_slot` values. That is consistent with the ITU split in protobufs #922/#923.

### Gaps
- I could not enumerate or quote GitHub **issues/discussions**: the API was blocked and search engines returned no indexed hits for `ITU2_2M`. Pre-2026 community feature requests for 2 m (if any) and maintainer rejections are therefore **UNKNOWN**. No rejected/closed-unmerged 2 m PR was found, but I only saw merged history, so absence is not established.
- PR open dates, reviewers and review comments are not available.

---

## Q2. Is there a 2 m RegionCode in config.proto, and what are its parameters?

### Takeaway
Yes. There are three: `ITU1_2M = 27` (144–146 MHz), `ITU2_2M = 28` (144–148) and `ITU3_2M = 33` (144–148). Adjacent bands are `ITU2_125CM = 37` (220–225) and `ITU1/2/3_70CM = 34/35/36`. All use 100 % duty cycle and a 30 dBm table limit, with no frequency switching and no wideLora. They use licensed-only profiles: HAM_20KHZ (TINY presets, 15.6 kHz BW in 20 kHz slots) for 2 m, and HAM_100KHZ (NARROW presets, 62.5 kHz BW in 100 kHz slots) for 1.25 m and 70 cm.

### Cited Findings
- config.proto (master) enum values:
  - `ITU1_2M = 27 … label: "ITU Region 1 / Amateur 2m"`, comment "(144-146 MHz)".
  - `ITU2_2M = 28`, "(144-148 MHz)".
  - `ITU3_2M = 33`.
  - `ITU1_70CM = 34` (430–440).
  - `ITU2_70CM = 35` (420–450, "Some countries do not allocate 420-430 MHz or 440-450 MHz. Check local law!").
  - `ITU3_70CM = 36` (430–450).
  - `ITU2_125CM = 37` (220–225, "Some countries do not allocate 220-222 MHz (Ex: USA/Canada)").
  - Interleaved non-ham additions: `EU_866 = 29`, `EU_874 = 30`, `EU_917 = 31`, `EU_N_868 = 32`.
  - [config.proto](https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/config.proto)
- TINY presets (config.proto): `TINY_FAST = 14` / `TINY_SLOW = 15`. Comment: "Preset optimized for compliance with Amateur Radio restrictions with 20kHz bandwidth. Many regions limit data transmission bandwidth in lower amateur bands (2 Meter). Note: TCXO with tight tolerances (±5 ppm or better) is *absolutely required* at these narrow bandwidths. Only compatible with SX127x and SX126x chipsets." — [config.proto](https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/config.proto)
- Modem parameters (firmware `src/mesh/MeshRadio.h`, develop):
  - TINY_FAST: bw 15.6 kHz, SF7, CR 4/5.
  - TINY_SLOW: bw 15.6 kHz, SF8, CR 4/6.
  - NARROW_FAST: 62.5 kHz, SF7, CR 4/6.
  - NARROW_SLOW: 62.5 kHz, SF8, CR 4/6.
  - [MeshRadio.h](https://github.com/meshtastic/firmware/blob/develop/src/mesh/MeshRadio.h)
- Docs radio-settings table: Tiny Fast 0.68 kbps, link budget 155 dB; Tiny Slow 0.33 kbps, 157.5 dB. — [radio-settings.mdx](https://github.com/meshtastic/meshtastic/blob/master/docs/about/overview/radio-settings.mdx)
- Firmware region table, `src/mesh/RadioInterface.cpp` develop, verbatim. The RDEF field order is name, freq_start, freq_end, duty_cycle, power_limit, frequency_switching, wide_lora, profile, default_preset, override_slot:
  ```
  RDEF(ITU1_2M, 144.0f, 146.0f, 100, 30, false, false, PROFILE_HAM_20KHZ, PRESET(TINY_FAST), 26),
  RDEF(ITU2_2M, 144.0f, 148.0f, 100, 30, false, false, PROFILE_HAM_20KHZ, PRESET(TINY_FAST), 51),
  RDEF(ITU3_2M, 144.0f, 148.0f, 100, 30, false, false, PROFILE_HAM_20KHZ, PRESET(TINY_FAST), 33),
  RDEF(ITU2_125CM, 220.0f, 225.0f, 100, 30, false, false, PROFILE_HAM_100KHZ, PRESET(NARROW_SLOW), 37),
  RDEF(ITU1_70CM, 430.0f, 440.0f, 100, 30, false, false, PROFILE_HAM_100KHZ, PRESET(NARROW_SLOW), 37),
  RDEF(ITU2_70CM, 420.0f, 450.0f, 100, 30, false, false, PROFILE_HAM_100KHZ, PRESET(NARROW_SLOW), 137),
  RDEF(ITU3_70CM, 430.0f, 450.0f, 100, 30, false, false, PROFILE_HAM_100KHZ, PRESET(NARROW_SLOW), 37),
  ```
  Comments in the table:
  - ITU1_2M: "Power limit is the regulatory ceiling (1 W / 30 dBm) - individual hardware will cap below this via its own PA curve"; band plan source iaru-r1.org VHF-Bandplan.pdf.
  - ITU2_2M: "Typical admin rules (e.g. US FCC Part 97) allow well above 30 dBm for licensed operators. Default slot: 51 (145.010 MHz)"; source arrl.org/band-plan.
  - ITU3_2M: "Default slot: 33 (144.650 MHz)"; sources IARU R3 band plan and the WIA 2026 Australian band plan.
  - [RadioInterface.cpp](https://raw.githubusercontent.com/meshtastic/firmware/develop/src/mesh/RadioInterface.cpp)
- Profiles (same file), with the comment-defined field order "presets, spacing, padding, audio, licensed, text throttle, position throttle, telemetry throttle":
  ```
  // Ham '20kHz' profile. 15.6kHz bandwidth coerced to 20kHz via padding.
  const RegionProfile PROFILE_HAM_20KHZ = {PRESETS_TINY, 0, 0.0022f, false, true, 0, 2, 2};
  // Ham '100kHz' profile. 62.5kHz bandwidth coerced to 100kHz via padding.
  const RegionProfile PROFILE_HAM_100KHZ = {PRESETS_NARROW, 0, 0.01875f, false, true, 0, 1, 1};
  ```
  So `audioPermitted = false` and `licensedOnly = true` for all ham regions. The 2 m profile also sets position and telemetry throttle to 2 (the STD profile uses 1). — [RadioInterface.cpp](https://raw.githubusercontent.com/meshtastic/firmware/develop/src/mesh/RadioInterface.cpp)
- Slot math, from the firmware formula `freqSlotWidth = spacing + 2*padding + bw/1000`, with `freq = freqStart + bw/2000 + padding + channel_num*freqSlotWidth`:
  - HAM_20KHZ slot width = 0.0044 + 0.0156 = 0.020 MHz. That gives ITU2/3 200 slots and ITU1 100 slots.
  - Slot 51 → 144 + 0.0078 + 0.0022 + 50×0.02 = 145.010 MHz, which matches the code comment. (Arithmetic is mine; the inputs are quoted from code.) — [RadioInterface.cpp](https://raw.githubusercontent.com/meshtastic/firmware/develop/src/mesh/RadioInterface.cpp)
- Proposed next schema (unreleased): protobufs branch `trident` holds `registry/regions.yaml`. It has `ITU1_2M`/`ITU2_2M`/`ITU3_2M` on profile `HAM_20KHZ` with `freq_start_hz: 144000000`, `freq_end_hz: 146000000/148000000`, `duty_cycle_permille: 1000`, `power_limit_dbm: 30`, `override_slot: 26/51/33`, `edge_clearance: false # raster profile`.
  - Commit `bf6f310` (2026-10-04, Thomas Göttgens), "Registry: exact hertz, slot plan, sub-bands, raster profiles": "HAM_20KHZ and HAM_100KHZ are rasters … UA_433 removed and RegionCode renumbered".
  - Commit `b8a2077` (2026-10-05): "User.flags replaces is_licensed and is_unmessagable".
  - Branch head as of 2026-10-09: `fccdbe7`.
  - [protobufs trident branch](https://github.com/meshtastic/protobufs/tree/trident)

### Inferences
- In code, the 30 dBm `power_limit` is not a hard cap for licensed users: `if ((power == 0) || ((power > newRegion->powerLimit) && !devicestate.owner.is_licensed))` clamps only unlicensed nodes. (BELIEVED from reading; not run.)
- Trident is a next-major ("3.x"-style) branch. Its renumbering would change enum values, so the numbers above are valid for the 2.8 wire format only.

### Gaps
- The firmware 2.8 docs do not say whether `trident` will ship, or when.
- No 6 m, 137–143 MHz or 148–174 MHz (commercial/VHF-high) region exists in master config.proto. I found no proposal for one in commit messages.

---

## Q3. Handling outside region tables (override_frequency, ham/is_licensed, encryption) and chip support / limits

### Takeaway
`override_frequency` bypasses slot calculation entirely. Licensed mode (`is_licensed`):
- is required to pick any ITU region;
- strips all channel PSKs and disables the admin channel;
- suppresses PKI keys and DMs, but still signs plaintext with XEdDSA (2.8);
- lifts the region power clamp.

The firmware does **not** enforce per-chip frequency ranges. The only hardware check is sub-GHz vs 2.4 GHz capability. 144 MHz hardware in-tree uses the SX1278 (RF95 driver), not SX126x.

### Cited Findings
- `override_frequency` (config.proto field 14): "Ignore Channel Calculation and use this frequency instead. The frequency_offset still applies." Firmware: `if (loraConfig.override_frequency) { freq = loraConfig.override_frequency; channel_num = -1; }`, and it logs "Frequency override in place, using %.3f". — [config.proto](https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/config.proto); [RadioInterface.cpp](https://raw.githubusercontent.com/meshtastic/firmware/develop/src/mesh/RadioInterface.cpp)
- Docs for override_frequency: "This parameter is for advanced users and licensed HAM radio operators … This will allow you to use out-of-band frequencies … If you are a license HAM operator, make sure you enable HAM mode and turn off encryption." — [lora.mdx](https://github.com/meshtastic/meshtastic/blob/master/docs/configuration/radio/lora.mdx)
- Licensed gate (`RadioInterface::checkConfigRegion`): `// If you are not licensed, you can't use ham regions. if (newRegion->profile->licensedOnly && !devicestate.owner.is_licensed && !prospectiveLicensedOwner)` → "Region %s requires licensed mode", notification `LORA_REGION_REQUIRES_LICENSE`. — [RadioInterface.cpp](https://raw.githubusercontent.com/meshtastic/firmware/develop/src/mesh/RadioInterface.cpp)
- The hardware check in the same function covers capability class only: "wide-LoRa (2.4 GHz) regions need a wide-capable radio, and sub-GHz regions need a radio that can tune below 2.4 GHz (SX128x cannot)" → "Region %s needs %s, which this radio does not support". I found no min/max-MHz check per chip in RadioInterface.cpp, RF95Interface.cpp, SX126xInterface.cpp or LR11x0Interface.cpp (grep for frequency bounds, this session). — [RadioInterface.cpp](https://raw.githubusercontent.com/meshtastic/firmware/develop/src/mesh/RadioInterface.cpp)
- Encryption removal, `Channels::ensureLicensedOperation()`: when `owner.is_licensed`, it sets the admin channel `role = DISABLED` and zeroes its PSK, and sets `psk.size = 0` on every other channel. — [Channels.cpp](https://github.com/meshtastic/firmware/blob/develop/src/mesh/Channels.cpp)
- PKI suppression: `CryptoEngine::ensurePkiKeys` returns false at the top when `user.is_licensed`. Key verification is refused in licensed mode (#12201, 2026-10-19). Plaintext signing in licensed mode came with #10969 (2026-07-26). — [CryptoEngine.cpp](https://github.com/meshtastic/firmware/blob/develop/src/mesh/CryptoEngine.cpp); [#12201](https://github.com/meshtastic/firmware/pull/12201); [#10969](https://github.com/meshtastic/firmware/pull/10969)
- Ham-only hardware defaults to licensed. NodeDB.cpp: `#ifdef HAS_HAM_2M_ONLY // Ham-band-only hardware defaults to licensed operation. The user can still flip this off later (e.g. a commercial operator on an adjacent allocation who wants to keep encryption on) … owner.is_licensed = true;`. The BaseUI menu shows only the three ITU 2 m regions under `HAS_HAM_2M_ONLY`. — [NodeDB.cpp](https://github.com/meshtastic/firmware/blob/develop/src/mesh/NodeDB.cpp); [MenuHandler.cpp](https://github.com/meshtastic/firmware/blob/develop/src/graphics/draw/MenuHandler.cpp)
- BaseUI Ham Mode (#10663): TX stays disabled while the callsign is N0CALL. — [#10663](https://github.com/meshtastic/firmware/pull/10663)
- Chip compatibility for TINY presets: the proto/docs say "Only compatible with SX127x and SX126x". Docs: "they require a TCXO with a tolerance of ±5 ppm or better." I found no code that rejects TINY on LR11x0 or LR20x0. — [lora.mdx](https://github.com/meshtastic/meshtastic/blob/master/docs/configuration/radio/lora.mdx)
- Chip drivers present in firmware develop: RF95/SX127x (`RF95Interface`), SX126x, LR11x0, SX128x and STM32WL. The LR2021 (LR20x0) radio is newly supported in 2.8 (RAK3401; firmware commits 4da58c3c9 and e951b4fe6, Nov 2026). — [firmware src/mesh](https://github.com/meshtastic/firmware/tree/develop/src/mesh)

### Inferences
- The SX126x/LLCC68/LR11x0 datasheet floor is 150 MHz, and the SX1276/77/78 band is 137–525 MHz. This is my own knowledge, **not verified this session**. It would explain why both in-tree 144 MHz targets use the SX1278 via RF95, while the TINY comment still allows SX126x (probably for 150+ MHz override or 220/430 use).
- Because there are no per-chip frequency bounds in code, a 900 MHz board set to ITU2_2M would just program 145 MHz into its radio. The radio would either mis-tune or radiate badly through a mismatched filter and antenna. Region choice is policy-gated (licensed), not hardware-gated. (BELIEVED from reading.)

### Gaps
- Whether RadioLib itself rejects out-of-range frequencies per chip (returning an error the firmware surfaces) was not checked in this session.
- I did not establish whether SF5/SF6 ("on supported hardware") interact with TINY presets.

---

## Q4. Hardware vendors / boards for VHF Meshtastic

### Takeaway
Only **LilyGO** hardware has 144 MHz support in-tree:
- the T-Beam Supreme 144 MHz variant, merged 2026-06-04 into the existing `tbeam-s3-core` target;
- the dedicated **T-Beam BPF** (`variants/esp32s3/t-beam-bpf`, SX1278 plus a PA of about 5 W), merged 2026-07-27.

No RAK, Heltec, Seeed, B&Q or Elecrow VHF board was found in firmware variants or commit messages.

### Cited Findings
- T-Beam BPF `variant.h`:
  - "Hardware is restricted to the amateur 2m band (144-148 MHz). #define HAS_HAM_2M_ONLY 1"
  - "LoRa Radio - SX1278 144-148Mhz #define USE_RF95"
  - "+27dBm PA / LilyGo Docs specify SX1278 power must be capped at 10 / #define RF95_MAX_POWER 10"
  - "Measurements taken with 5V USB power (max 37 dBm on battery power) … Output values: 0, 34, 35, 36, 36, 37, 37, 38, 38, 38"
  - `#define TX_GAIN_LORA 0, 32, 32, 32, 31, 31, 30, 30, 29, 28`
  - an L76K GPS, an SH1106 OLED, an AXP2101 PMU, and `RF95_RXEN 39 // LNA enable`.
  - [variant.h](https://github.com/meshtastic/firmware/blob/develop/variants/esp32s3/t-beam-bpf/variant.h)
- T-Beam Supreme 144: it adds `USE_RF95` alongside SX1262/SX1268/LR1121 in `tbeam-s3-core` ("144mhz variant uses 'RF95' (SX1278)"). This means one firmware binary probes for the radio, not a separate env. — [#10624](https://github.com/meshtastic/firmware/pull/10624)
- The 2.8 blog hardware list names T-Beam BPF and no other VHF board. — [blog](https://github.com/meshtastic/meshtastic/blob/master/blog/2026/October/lets-talk-about-2-8.mdx)
- Other 2026 variants (Wio Tracker L1 Pro 1W, L2, Meshnology W10/W12, Heltec RC, RAK3401+LR2021, T-Connect-Pro, ThinkNode M8/M9) show no 2 m mention in their commit titles. — [firmware commits](https://github.com/meshtastic/firmware/commits/develop)

### Inferences
- The table ceiling is 30 dBm, but the BPF measures up to 37–38 dBm. That gap depends on the licensed-mode power-clamp bypass (see Q2). The "~37 dBm (5 Watts)" PR title confirms the developers intended it.

### Gaps
- I could not check vendor announcements or teasers outside GitHub (RAK, Heltec and others) from here. Vendor-side VHF roadmaps are UNKNOWN.
- I did not grep every `variants/**/variant.h` for `HAS_HAM_2M`. I checked only t-beam-bpf and tbeam-s3-core, so another variant may define it.

---

## Q5. Named developers and timeline

### Takeaway
The lead is **Austin (`vidplace7`, vidplace7@gmail.com)**, who authored the protobuf ITU split, TINY presets, the 2 m/1.25 m/70 cm firmware regions and both LilyGO 144 MHz targets. Others:
- **Ben Meadors**: maintainer merges, submodule bump, co-author of licensed signing.
- **Thomas Göttgens**: original protobufs #897 (2026-04-21), co-author of the T-Beam BPF, licensed PSK defaults, the trident registry.
- **Jason P**: BaseUI Ham Mode.
- **Benjamin Faershtein**: licensed signing.
- **Jonathan Bennett**: key-verification refusal in ham mode.
- **Tom**: licensed rebroadcast rule.
- **Ian McEwen**: is_licensed in DeviceProfile.
- **James Rich**: Android.
- **Dan Ditomaso**: web preset filtering.
- **Garth Vander Houwen**: Apple, enum labels.

No published future timeline was found. The work has already shipped in 2.8.x.

### Cited Findings
- Timeline, all from commits above:
  - 2026-03-22: licensed-routing rule (#9958).
  - 2026-04-21: first ITU proto (#897).
  - 2026-05-22 → 06-05: proto 70 cm, ITU split, 1.25 m (#920/#922/#923/#933).
  - 2026-06-02/03: TINY presets (proto #934, fw #10597) and 2 m regions (fw #10623).
  - 2026-06-04: T-Beam Supreme 144 (#10624).
  - 2026-06-06: 1.25 m (#10638).
  - 2026-06-09: BaseUI Ham Mode (#10663).
  - 2026-07-03: 70 cm (#10627).
  - 2026-07-26: licensed signing (#10969).
  - 2026-07-27: T-Beam BPF (#10558).
  - 2026-07-30: licensed PSK defaults (#11286).
  - 2026-08-02: Android ITU regions (#6548).
  - 2026-08-29/31: v2.8.0 tags.
  - 2026-09-28: v2.8.1 tag (2.8.0 revoked per the 2026-10-08 blog).
  - 2026-10-04/05: trident registry redesign, proposed.
  - 2026-10-19: ham-mode key-verification refusal (#12201).
  - 2026-11-01/03: v2.8.2 tags.
  - Sources: [firmware](https://github.com/meshtastic/firmware/commits/develop), [protobufs](https://github.com/meshtastic/protobufs/commits/master), [blog](https://github.com/meshtastic/meshtastic/blob/master/blog/2026/October/lets-talk-about-2-8.mdx)
- The 2.8 blog says 2.8.1 "isn't ready for wide deployment yet" (alpha) as of 2026-10-08. The 2 m support therefore currently ships in an **alpha** release line. I did not confirm whether 2.8.2 is beta or stable. — [blog](https://github.com/meshtastic/meshtastic/blob/master/blog/2026/October/lets-talk-about-2-8.mdx)

### Inferences
- Austin's full name was not established. Only the "Austin" commit name and the `vidplace7` handle/email are confirmed.

### Gaps
- No roadmap, issue or discussion stating future VHF plans could be read (no API access). A stated timeline is UNKNOWN.
- Hackaday (2026-09-17), "FCC ISM rules may shatter LoRa mesh communities", is a secondary source that may be relevant motivation for ham-band moves. I did not read it, and it is not a primary GitHub source. — [Hackaday](https://hackaday.com/2026/09/17/fcc-ism-rules-may-shatter-lora-mesh-communities/)
