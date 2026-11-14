# Meshtastic Firmware 2.8 (2.8.x) — releases, features, breaking changes, client API, 2m/VHF, develop state

Research date: 2026-11-14. Method note: GitHub web pages (releases, PRs, tags, raw protobufs) and PyPI were fetched directly. meshtastic.org and nodakmesh.org failed DNS in this session (outside the allowed network domains), so the official blog post "Let's talk about 2.8" was seen ONLY as a search-result snippet, not read in full. The GitHub API (api.github.com) returned 403, so release lists come from the rendered HTML pages, which show day+month but not the year. Years are inferred as 2026 from the protobufs tag dates (which do show years), the Mesh America article dated 2026-11-03, and the "Latest" 2.7.26 assets being dated 2026.

## What are the 2.8.x tags/releases, with dates? Alpha vs beta vs stable?

### Takeaway
As of 2026-11-14 there is NO stable (beta-channel) 2.8 release. Five 2.8 tags exist, all alpha/pre-release, and three of them are revoked. The current non-revoked alpha is 2.8.2.d5f1619 (04 Nov 2026). The "Latest" (beta/stable channel) firmware is still 2.7.26.54e0d8d (24 Jun 2026).

### Cited Findings
- Firmware releases page, newest first: v2.8.2.d5f1619 Alpha (pre-release), 04 Nov; v2.8.2.4c89b69 Alpha **Revoked**, 02 Nov; v2.8.1.8e6a88d Alpha, 01 Oct; v2.8.0.47db0e3 Alpha **Revoked**, 01 Sep; v2.8.0.7239fe8 **Revoked**, 30 Aug; v2.7.26.54e0d8d **Beta, marked "Latest"**, 24 Jun; v2.7.25 Alpha 10 Jun; v2.7.24 Alpha 23 May; v2.7.23 Alpha 08 May; v2.7.22 Alpha 14 Apr — [GitHub firmware releases](https://github.com/meshtastic/firmware/releases)
- 2.8.0.7239fe8 was revoked after LR1110 initialization problems on T1000-E devices after a fresh flash; notes point users to 2.8.0.47db0e3 — [GitHub firmware releases](https://github.com/meshtastic/firmware/releases)
- 2.8.0.47db0e3 is also labelled revoked; its page gives no reason. The 2.8.1 notes explain: "A problem was found in the 2.8.0 packet signing code"; 2.8.0 is revoked and 2.8.1 signatures are NOT compatible with 2.8.0 — [v2.8.0.47db0e3](https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3); [v2.8.1.8e6a88d](https://github.com/meshtastic/firmware/releases/tag/v2.8.1.8e6a88d)
- 2.8.2.4c89b69 revoked (no reason on the listing); 2.8.2.d5f1619 is a re-release "to fix a stack exhaustion bug in environmental telemetry" — [v2.8.2.d5f1619](https://github.com/meshtastic/firmware/releases/tag/v2.8.2.d5f1619)
- 2.8.0 full changelog range is v2.7.23.b246bcd...v2.8.0.47db0e3 (i.e. 2.8 branched from the 2.7.23 era of develop) — [v2.8.0.47db0e3](https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3)
- Protobufs repo tags (with years): v2.8.2 Oct 28, 2026; v2.8.1 Oct 1, 2026; v2.8.0 Aug 28, 2026; v2.7.26 Jun 17, 2026; v2.7.25 Jun 2, 2026; v2.7.24 May 29, 2026; v2.7.23 Apr 22, 2026; v2.7.22 Apr 14, 2026; v2.7.21 Mar 22, 2026; v2.7.20 Mar 8, 2026 — [protobufs tags](https://github.com/meshtastic/protobufs/tags)
- Search-result snippet of the official blog post "Let's talk about 2.8" says 2.8 brings signed packets, private-by-default location and support for much larger meshes, and that 2.8.1 is an alpha "not ready for wide deployment" — [meshtastic.org blog (snippet only, not fetched)](https://meshtastic.org/blog/lets-talk-about-2-8/)
- Third-party coverage of 2.8.2 alpha (signing tightened, direct BLE for Linux nodes), dated 2026-11-03 — [Mesh America (snippet only)](https://meshamerica.com/2026/11/03/meshtastic-2-8-2-alpha/)

### Inferences
- Cadence: one 2.8.x alpha per month (Sep, Oct, Nov). Each alpha has needed a revoke/re-release, so a 2.8 beta/stable promotion is not imminent on evidence available; I would not expect stable before a further alpha (BELIEVED, inference).
- Meshtastic's channel model: "Alpha" = pre-release, "Beta" = the channel the apps/flasher treat as stable. 2.7.26 is that channel today.

### Gaps
- Exact revocation reason for 2.8.2.4c89b69 is not on its page (only implied by the d5f1619 re-release note).
- Full text of the meshtastic.org 2.8 blog post (date, author, stable ETA) — host blocked in this session.

## Headline features and changelog for 2.8 (with 2.7 context)

### Takeaway
2.8 is a large, breaking release: XEdDSA packet signing, node numbers derived from the public key (not MAC), opt-in position/telemetry, no precise position on public channels, a Traffic Management module and routing/dedup rework, a NodeDB storage redesign (storage-format break), new region/preset allocation (EU merge, new amateur 2m/1.25m/70cm regions, TinyFast/TinySlow presets), US default preset → LongTurbo, and many meshtasticd (Linux native) additions in 2.8.2.

### Cited Findings
**Breaking/high-impact (2.8.0 warnings block)** — [v2.8.0.47db0e3](https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3)
- New US nodes default to **LongTurbo** instead of LongFast on first-time region selection on the device screen (#11637); LongTurbo and LongFast cannot hear each other.
- Node numbers derived from public-key identity instead of hardware MAC.
- XEdDSA packet signing (#10478); long names reduced to 25 bytes.
- Precise position disallowed on known-key (public) channels; telemetry and position OFF by default (#10929).
- Redesigned NodeDB storage and traffic management; "completely new allocation of LoRa regions and presets, including ham-specific carve-outs".
- Full erase + reflash recommended if bootloop/crashes after upgrade; export config first. Flash hardening / filesystem unification / write-behind LFS cache for STM32WL and nRF52 is a storage-format break (#10171).

**Routing / traffic management** — [v2.8.0.47db0e3](https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3)
- Traffic Management Module: dedup, rate limiting, role-aware policing (#9358, expanded #10706).
- Next-hop cache overflow store (#10745); hash-table O(1) packet history lookups (#9499).
- Automatic variable hop limits from live activity and message-size estimates (#10176); congestion-aware position interval and hop-exhaustion tuning (#9921).
- Packets with missing/invalid `hop_start` (pre-hop firmware) are deprecated and **blocked** (#9476).
- Noise-floor tracking with sliding window (#9347); mesh beacon feature (#10618, admin/config #10839).
- 2.8.2: smarter Listen-Before-Talk with CAD-to-RX handoff (#11137), SX126x re-stages TX after modem param changes (#12101), RX frames read from dedicated FreeRTOS task (#11983) — [v2.8.2.d5f1619](https://github.com/meshtastic/firmware/releases/tag/v2.8.2.d5f1619)

**Encryption / PKI / signing**
- 2.8.0: XEdDSA signing (#10478), unsigned-packet policy hardening (#10858), runtime-toggleable `MESHTASTIC_LOCKDOWN` on nRF52 (#10349/#10712), spoof detection for UDP multicast (#9905) — [v2.8.0.47db0e3](https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3)
- 2.8.1: sign the whole Data envelope (#11422); ack over PKC when no channel can carry it (#11891); acks signed via `ack_proof` — [v2.8.1.8e6a88d](https://github.com/meshtastic/firmware/releases/tag/v2.8.1.8e6a88d)
- 2.8.2: in **Compatible** mode nodes no longer sign; AES-CCM takes precedence; Strict mode gains exemptions (#12191); key verification refused in licensed (ham) mode (#12201) — [v2.8.2.d5f1619](https://github.com/meshtastic/firmware/releases/tag/v2.8.2.d5f1619)
- `SecurityConfig.packet_signature_policy` (since 2.8.0): COMPATIBLE=0 "No Signing", BALANCED=1 "Sign, Accept Unsigned", STRICT=2 "Require Signatures" — [config.proto](https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/config.proto)

**MQTT**
- 2.8.2: MQTT gateways now uplink PKI DMs they relay but cannot decrypt (remain end-to-end encrypted) (#12110) — [v2.8.2.d5f1619](https://github.com/meshtastic/firmware/releases/tag/v2.8.2.d5f1619). No other MQTT changes listed in 2.8.0/2.8.1 notes.

**TAK**
- ATAK Plugin V2; legacy unishox2 compression dropped (#10105); TAKTALK voice/text structures — [v2.8.0.47db0e3](https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3)
- Protobufs Oct 28, 2026: "Add TAKPacketV2 start_epoch_lo14 to restore the CoT start time (#1151)" — [protobufs commits](https://github.com/meshtastic/protobufs/commits/master)

**meshtasticd / portduino / config.yaml**
- 2.8.0: JSON removed from firmware builds but **full JSON support retained in meshtasticd** (#10152); portduino malloc shim (#10677); native sensor simulation (#10748); `PortduinoSetOptions` (#10157); MCP server for device interaction (#10194, moved to own repo #10861); Ethernet HTTP/HTTPS API ported to RP2350+W5500 (#10573) — [v2.8.0.47db0e3](https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3)
- 2.8.1: macOS native MUI simulator target — [GitHub firmware releases](https://github.com/meshtastic/firmware/releases)
- 2.8.2: **BLE peripheral via BlueZ** so phone apps connect directly to a Pi node (#11396); **DMShell** remote shell over the mesh (#11976, #12111); native mDNS discovery via Avahi/Bonjour (#12032, #12142); **admin keys in config.yaml, no longer limited to three** (#12056); faster CH341 USB radio handling (#12013, #12016) — [v2.8.2.d5f1619](https://github.com/meshtastic/firmware/releases/tag/v2.8.2.d5f1619)
- LoRa config changes now apply live without reboot (#9962) — [v2.8.0.47db0e3](https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3)

**Store-and-forward**: not mentioned in any 2.8 release note fetched.

**2.7 context**
- 2.7.x ran as monthly alphas through 2026 (2.7.20 Mar → 2.7.26 Jun); 2.7.26 promoted to Beta/"Latest" 24 Jun 2026 — [GitHub firmware releases](https://github.com/meshtastic/firmware/releases); [protobufs tags](https://github.com/meshtastic/protobufs/tags)
- Protobuf comments: LONG_SLOW "Deprecated in 2.7: Unpopular slow preset" — [config.proto](https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/config.proto)
- Python 2.7.8 notes mention the traffic management module already appearing in protobufs v2.7.19 — [python releases](https://github.com/meshtastic/python/releases)

### Inferences
- "Support for much larger meshes" (blog snippet) maps concretely to the NodeDB warm store (#10705 etc.), O(1) packet history, next-hop overflow store and traffic management (inference from the itemised notes).
- The node-number change means a node upgrading 2.7→2.8 will appear as a NEW node to peers and to any client DB keyed on node num/`!xxxxxxxx` ID (BELIEVED from the release note wording; not observed).

### Gaps
- Release notes list no PR for the node-number derivation or NodeDB redesign; algorithm for deriving node num from public key not documented on the pages read.
- No specific 2.8 changes found for store-and-forward or the HTTP `/api/v1/*` endpoints.

## Breaking changes to the client API (FromRadio/ToRadio, admin, config fields) for Python / TCP 4403 / HTTP fromradio clients

### Takeaway
The protobuf changes are additive at the wire level (new fields/oneof variants/enum values; nothing removed was seen), so an older protobuf decoder will mostly silently ignore new data — but several BEHAVIOURAL breaks will hit clients: node IDs change on upgrade, position/telemetry stop arriving by default, pre-hop_start packets are dropped, unknown new RegionCode/ModemPreset enum values (27–37, 14–16), and 2.8.0↔2.8.1 signing incompatibility.

### Cited Findings
- New `FromRadio` oneof variants: `lockdown_status` (18), `region_presets` (19) "Map of which modem presets are legal in each LoRa region", `display_frame` (21), `display_palette` (22); field 20 absent. `ToRadio.heartbeat` (7) exists ("keep the device connection awake on serial") — [mesh.proto](https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/mesh.proto)
- `MeshPacket.xeddsa_signed` (22, bool), `MeshPacket.ack_proof_status` (23, AckProofStatus); `Data.xeddsa_signature` (10, bytes); `Routing.ack_proof` (4, bytes); `NodeInfo.has_xeddsa_signed` (14), `NodeInfo.heard_on_current_lora` (15) — [mesh.proto](https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/mesh.proto)
- `User.long_name` comment: "Limited to 24 bytes of UTF-8: longer names are accepted from senders" / "Clients should enforce 24 bytes in their UI." (release notes say 25 bytes) — [mesh.proto](https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/mesh.proto); [v2.8.0 notes](https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3)
- `MeshPacket.hop_start` (15) has no deprecation marker in proto, but firmware 2.8 blocks packets with missing/invalid hop_start (#9476) — [mesh.proto](https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/mesh.proto); [v2.8.0 notes](https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3)
- Recent protobuf additions (Oct–Nov 2026): revised packet signature policy + AES-CCM override documented (#1141, Nov 1); `NotificationType UNSIGNED_IDENTITY_UPDATE_IGNORED` (#1161, Nov 4); `DeviceMetadata.ble_address` (#1159, Nov 1); `DeviceMetadata.bootloader_version` (#1166, Nov 9); `transparent_mode` in config.proto (#975, Nov 1) — [protobufs commits](https://github.com/meshtastic/protobufs/commits/master)
- Firmware develop: "Router: drop received packets that carry our own node number" (#12270, merged Nov 1, 2026); open "Answer refused requests and rate-limited phone sends with a NAK" (#12225); open "Send admin-made owner changes and admin NodeInfo requests past the routine throttle" (#12262) — [firmware PRs](https://github.com/meshtastic/firmware/pulls?q=is%3Apr+144)
- 2.8.2: admin keys configurable in meshtasticd config.yaml beyond three (#12056) — [v2.8.2.d5f1619](https://github.com/meshtastic/firmware/releases/tag/v2.8.2.d5f1619)
- JSON dropped from MCU firmware builds but kept in meshtasticd (#10152) — [v2.8.0 notes](https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3)

### Inferences
- A client on python 2.7.11 (protobufs ≈v2.7.26) talking to a 2.8 node: protobuf3 unknown fields are skipped, so FromRadio variants 18/19/21/22 decode as an empty/unset oneof — code that does `fr.WhichOneof("payload_variant")` and assumes a known value may hit `None` or an unhandled branch (BELIEVED; verify by decoding a 2.8.2 want_config stream with the 2.7 python lib).
- Unknown enum values (e.g. region 28 = ITU2_2M, preset 14 = TINY_FAST) decode as raw ints in python protobuf; `Config.LoRaConfig.RegionCode.Name(28)` raises ValueError on old generated code (BELIEVED).
- Rate limiting / traffic management and the "phone send NAK" work (#12225, open) mean bursty TX from a gateway client may be throttled or NAK'd on 2.8+ (inference).
- Any client DB keyed by node number must expect identity churn on 2.7→2.8 upgrade (inference from release note).
- Since JSON stays in meshtasticd, HTTP JSON endpoints on Linux native are presumably unaffected; MCU HTTP JSON endpoints (ESP32) may be gone (BELIEVED; not confirmed which endpoints).

### Gaps
- No explicit "client API compatibility" section exists in any 2.8 release note (confirmed absent on 2.8.0 and 2.8.1 pages).
- Did not inspect admin.proto for 2.8 AdminMessage changes (beacon admin #10839 is the only admin-related item noted).
- Did not verify whether `/api/v1/fromradio` / `/api/v1/toradio` semantics changed.

## Meshtastic Python library version aligned with 2.8, and protobufs version

### Takeaway
There is no 2.8 Python library. PyPI latest is meshtastic **2.7.11** (GitHub release 17 Jul, inferred 2026); the last release explicitly naming its protobufs is 2.7.10 → protobufs v2.7.26. Protobufs repo has tags v2.8.0/2.8.1/2.8.2 that the Python lib does not yet ship.

### Cited Findings
- PyPI `info.version` = 2.7.11; `requires_dist` includes `protobuf>=4.21.12` — [PyPI JSON](https://pypi.org/pypi/meshtastic/json)
- Python releases: 2.7.11 (17 Jul, "Latest": .cfg export/import, simulated multi-node mesh testing, TCP half-close fix so last write isn't lost); 2.7.10 (29 Jun: protobufs v2.7.26, traceroute error handling, bitfield `--set`); 2.7.9 (09 Jun); 2.7.8 (02 Mar: protobufs v2.7.19, traffic management module, hop limits on send functions); no 2.8 mention — [python releases](https://github.com/meshtastic/python/releases)
- Protobufs v2.8.0 tagged Aug 28 2026, v2.8.1 Oct 1, v2.8.2 Oct 28 — [protobufs tags](https://github.com/meshtastic/protobufs/tags)

### Inferences
- Python lib has had no release for ~4 months while firmware moved to 2.8; third-party clients wanting signing fields, new regions or `region_presets` must regenerate from protobufs v2.8.2 themselves or use the python master branch (BELIEVED; python master not checked).

### Gaps
- Whether python `master` already vendors 2.8 protobufs — not checked.

## Region/radio changes — VHF/2m, LR11x0, LR2021

### Takeaway
2.8 is the first Meshtastic release with official amateur VHF/UHF regions: ITU1_2M (144–146 MHz), ITU2_2M and ITU3_2M (144–148 MHz), plus 70cm and 1.25m regions, using new 20 kHz-slot TinyFast/TinySlow presets, a "Ham/licensed mode" that blocks relaying to unlicensed users, and first 2m hardware (LilyGo T-Beam BPF, ~5 W). LR1121/LR2021 support was added to the T-Beam 1W in 2.8.2.

### Cited Findings
- RegionCode enum: ITU1_2M=27 "ITU Region 1 Amateur Radio 2m band (144-146 MHz)"; ITU2_2M=28 "(144-148 MHz)"; ITU3_2M=33 "(144-148 MHz)"; ITU1_70CM=34 (430–440); ITU2_70CM=35 (420–450, "Check local law!"); ITU3_70CM=36 (430–450); ITU2_125CM=37 (220–225, "Some countries do not allocate 220-222 MHz (Ex: USA/Canada)"); also EU_866=29, EU_874=30, EU_917=31, EU_N_868=32 — [config.proto](https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/config.proto)
- ModemPreset: TINY_FAST=14 and TINY_SLOW=15 "optimized for compliance with Amateur Radio restrictions with 20kHz bandwidth" (TinySlow "Comparable link budget and data rate to LONG_MODERATE"); MEDIUM_TURBO=16; LITE_FAST/LITE_SLOW (10/11, 125 kHz EU 866), NARROW_FAST/NARROW_SLOW (12/13, 62.5 kHz EU 868) — [config.proto](https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/config.proto)
- No dedicated LoRaConfig "ham mode" field; `override_frequency` comment references licensed HAM operators; `SecurityConfig` comments reference "Licensed (ham) mode" — [config.proto](https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/config.proto)
- PR #10623 "Add 2 Meter (~144mhz) Amateur Radio Regions" by vidplace7, merged to develop **June 3, 2026**: 15.6 kHz bandwidth padded to 20 kHz slot width; default slots ITU1_2M slot 26 (144.510 MHz), ITU2_2M slot 51 (145.010 MHz), ITU3_2M slot 33 (144.650 MHz); TINY_FAST default; tested on `tlora-v2-1-1_6` 144 MHz build and `t-beam-bpf`; split from #10558 — [PR #10623](https://github.com/meshtastic/firmware/pull/10623)
- PR #10558 "Add T-Beam BPF - 144-148 Mhz LoRa @ ~37 dBm (5 Watts)" by vidplace7, merged to develop **July 27, 2026**; LilyGo T-Beam BPF, ESP32-S3; chip named SX1278 only in an automated Copilot summary; depends on #10623, protobufs #934 (TINY_FAST/TINY_SLOW) and #10560 (per-region slot override); 2m-only build defaults owner to licensed mode and restricts region picker to 2m; default preset changed narrow_fast → TinyFast; adds TX_GAIN_LORA ("This is a 5-6W radio!!"); RF95 pin change may regress T-Beam Supreme — [PR #10558](https://github.com/meshtastic/firmware/pull/10558)
- 2.8.0/2.8.1 release notes: 70cm (#10627), 1.25m (#10638), 2m (#10623) regions; "Ham Mode" prompts for new amateur regions (#10663); licensed operators blocked from rebroadcasting to/from unlicensed users (#9958); EU regions merge (#10675) + Narrow/Lite (#10120); region→preset map (#10736); TinyFast/TinySlow in config+menu (#10597); low-bandwidth conversion support in MeshRadio (#10595) — [v2.8.1.8e6a88d](https://github.com/meshtastic/firmware/releases/tag/v2.8.1.8e6a88d)
- 2.8.2: key verification refused in licensed (ham) mode (#12201, merged Oct 19, 2026); T-Beam 1W supports LR1121 and LR2021 (#12204) — [v2.8.2.d5f1619](https://github.com/meshtastic/firmware/releases/tag/v2.8.2.d5f1619); [firmware PRs](https://github.com/meshtastic/firmware/pulls?q=is%3Apr+144)
- MUI: "JP frequency update" (device-ui #376) — [v2.8.0 notes](https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3)

### Inferences
- 2m Meshtastic requires a sub-GHz-low-capable LoRa chip: SX1276/77/78-class (RF95) and LR11x0/LR2021 cover 144 MHz by datasheet; SX1262 is specified 150–960 MHz, so 144–148 is outside its spec (PHYSICS/datasheet recollection, ASSERTED — not verified from a source this session; the 2m test targets named were the RF95-based tlora-v2-1-1_6 and T-Beam BPF, consistent with this).
- Ham-mode semantics in Meshtastic historically = encryption off + callsign as name; 2.8 adds licensed/unlicensed relay separation and refuses PKI key verification in that mode (consistent with no-encryption rules) (inference).
- 20 kHz slot at 15.6 kHz BW gives very low data rate; TinySlow ≈ LONG_MODERATE link budget per proto comment.

### Gaps
- Power limits / duty cycle for 2m regions: not stated in #10623.
- No source read confirming which chips firmware allows on ITU*_2M regions (e.g. whether SX1262 boards are blocked).
- 70cm/1.25m PR details (#10627, #10638) not fetched.

## What's happening on develop / next (2.9 or 3.0?) as of Nov 2026, and VHF/2m relevance

### Takeaway
No 2.9 or 3.0 release planning is visible on GitHub; the only release-targeting label seen is "2.8.next". Develop activity in Nov 2026 is stabilisation and radio work (CAD/LBT on LR11x0/LR2021, LR1110 firmware update, GPS PPS timing, NodeDB fixes, a draft crypto backend rewrite). No open 2m/VHF-specific PRs surfaced in the searches.

### Cited Findings
- PR #10949 carries label "2.8.next" (only release-target label found); "2.9" search hits were all 2.9-inch e-paper displays — [firmware PRs "2.9"](https://github.com/meshtastic/firmware/pulls?q=is%3Apr+sort%3Aupdated-desc+2.9)
- Open #12335 "LR11x0, LR2021: transmit straight from a clear CAD (LBT exit), with a slim scan setup" (opened Nov 10, 2026); open #12235 MakerDiary Connect Kit nRF54L15 variant (LR1121) — [firmware PRs "2.9"](https://github.com/meshtastic/firmware/pulls?q=is%3Apr+sort%3Aupdated-desc+2.9)
- Recent develop: #12313 "DO NOT MERGE: crypto backend rewrite, draft for CI flash numbers" (Nov 6); #12324 GPS sub-second NMEA timing and PPS discipline (merged); #12331 ThinkNode M9 LR1110 firmware 4.2 (merged Nov 11); #12284 tune modelled LR2021 in test suite (merged Nov 3); #12344 NodeDB clamp LoRa config after channels load (opened Nov 12); #12341 meshtasticd preset for CardputerZero Cap LoRa-1262 (merged Nov 11); #12281 DeviceMetadata.ble_address (merged Nov 3) — [firmware PRs](https://github.com/meshtastic/firmware/pulls?q=is%3Apr+144)
- Search for "144" PRs: none of the 25 visible titles mention 144 MHz/2m/VHF/BPF/TinyFast beyond #12201 (ham-mode key verification) — [firmware PRs](https://github.com/meshtastic/firmware/pulls?q=is%3Apr+144)

### Inferences
- Next tag is most likely another 2.8.x alpha (2.8.3) followed by a 2.8 beta; LR11x0/LR2021 CAD/LBT work (#12335) indirectly matters for VHF since LR11x0/LR2021 are 2m-capable chips (inference).

### Gaps
- No roadmap document or milestone for 2.9/3.0 found; meshtastic.org blog (which may discuss roadmap) unreachable in this session.
- Search on "144" returned 137 results; only the first page (25) was read, so older 2m PRs beyond #10558/#10623 may exist.
