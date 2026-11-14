# MeshForge Codebase Readiness: Meshtastic on 2 m (144-148 MHz) and Firmware 2.8

Method: I read the code at `/home/user/meshforge` with grep and file reads, on 2026-11-14. I did not run the test suite or any code. I tried to list the installed `meshtastic` protobuf enums, but the permission layer denied `python3 -c`. A filesystem search also found no `meshtastic` package installed in this container. So everything I say about protobuf enum contents (for example, whether a 2 m RegionCode exists) is a **Gap**, not a measurement. Sources are repo-relative `file:line` links. "ASSERT" marks claims that come from general RF or regulatory knowledge rather than this repo. Those need confirmation from the web-research notes (`regulatory_bandplans.md`, `community_and_projects.md` in this folder).

---

## Q1. Where does the code assume a band, region, preset, chip, link-budget constant or firmware version? (inventory)

### Takeaway
MeshForge has one firmware-accurate region and preset table, `utils/meshtastic_modem.py`, pinned to firmware v2.7.26. It is sub-GHz only and has no 2 m region. Around it sit at least six independent hand-written region lists: `standalone.py`, `config/lora.py`, `lora_presets.py`, two TUI pickers, and `compliance.py`. None of them knows 144-148 MHz. Many `freq_mhz` defaults are hard-wired to 906.875 or 915 MHz.

### Cited Findings

**The source of truth for regions and presets**
- `FIRMWARE_TAG = "v2.7.26.54e0d8d"`. The preset table is transcribed from firmware `MeshRadio.h` `modemPresetToParams()`, and the docstring says to update the tag and table together. — [src/utils/meshtastic_modem.py:1-25](src/utils/meshtastic_modem.py)
- `REGIONS` is transcribed from `RadioInterface.cpp` `RDEF(...)`. It holds 25 sub-GHz regions (US 902-928, EU_433, EU_868, CN 470-510, ... BR_902). The comment says "Sub-GHz only: LORA_24 uses wide-LoRa bandwidths this module does not model." It has **no 2 m / 144 MHz entry**. — [src/utils/meshtastic_modem.py:61-77](src/utils/meshtastic_modem.py)
- `firmware_params()` raises KeyError for an unknown preset ("an unknown preset is not silently LONG_FAST"). `num_channels()` and `slot_centre_mhz()` index `REGIONS[region.upper()]`, so a 2 m region code raises KeyError. — [src/utils/meshtastic_modem.py:44-126](src/utils/meshtastic_modem.py)
- A test pins the tag (`assert mm.FIRMWARE_TAG == "v2.7.26.54e0d8d"`) and specific region edges. — [tests/test_meshtastic_modem.py:31,142-144](tests/test_meshtastic_modem.py)

**Duplicate region tables that have drifted (none include 2 m)**
- The standalone frequency calculator has its own `REGIONS` dict, commented "all 22 Meshtastic regions". It still carries `UK_868` and `PH`, which the firmware table and test say do not exist. Its `VERY_LONG_SLOW` is 62.5 kHz, but the firmware runs that preset as LONG_FAST at 250 kHz. Its slots are 0-based with `int(range/bw)`, which is the off-by-one the TUI rebuild fixed. — [src/standalone.py:274-313](src/standalone.py), [src/standalone.py:336-345](src/standalone.py); compare [src/launcher_tui/handlers/rf_tools.py:81-92](src/launcher_tui/handlers/rf_tools.py) and [src/utils/meshtastic_modem.py:40-41](src/utils/meshtastic_modem.py)
- `LoRaConfigurator.REGIONS` has 10 regions with made-up channel counts. The advanced wizard hard-codes `config['region'] = 'US'  # Force US region as per requirements` and prints "US (915MHz ISM band)". — [src/config/lora.py:22-70](src/config/lora.py), [src/config/lora.py:245-246](src/config/lora.py), [src/config/lora.py:420](src/config/lora.py)
- `REGION_FREQUENCIES` only covers US, EU, AU, NZ, TW and JP (Hz). — [src/utils/lora_presets.py:153-190](src/utils/lora_presets.py)
- The TUI `_radio_set_region` list is hard-coded (US ... LORA_24, UNSET) and is not derived from `config_pb2.RegionCode`. — [src/launcher_tui/handlers/radio_menu.py:352-384](src/launcher_tui/handlers/radio_menu.py)
- The first-run `MESHTASTIC_REGIONS` list is also hard-coded. — [src/launcher_tui/handlers/first_run.py:119-140](src/launcher_tui/handlers/first_run.py)
- The device config menu offers US, EU_433, EU_868 ... ANZ. — [src/config/device.py:183-198](src/config/device.py)
- The SPI HAT band map only knows `SX1262_900` (850-930 MHz → US/ANZ/JP/KR/TW) and `SX1268_400` (410-510 MHz → EU_433/CN). — [src/config/spi_hats.py:30-46](src/config/spi_hats.py), [src/config/spi_hats.py:127](src/config/spi_hats.py)
- Gateway profile validation warns whenever region == 'US'. — [src/gateway/profiles/__init__.py:215-218](src/gateway/profiles/__init__.py)
- The site planner picks only from 915, 868 and 433 MHz. — [src/diagnostics/site_planner.py:236-242](src/diagnostics/site_planner.py)

**Hard-coded frequency defaults (all 902-928 MHz)**
- `preset_impact.DEFAULT_FREQ_MHZ = 906.875` ("Meshtastic US frequency"). It is reused by `multihop.py:38,221`. — [src/utils/preset_impact.py:68-69](src/utils/preset_impact.py)
- In `rf.py`: `required_antenna_height(freq_mhz=915.0)`, `knife_edge_diffraction(freq_mhz=906.875)`, `multi_obstacle_loss(906.875)`, `detailed_link_budget(freq_mhz=906.875)`, `batch_link_quality(freq_mhz=915.0)`. — [src/utils/rf.py:398,529,601,696,988](src/utils/rf.py)
- `terrain.py` LOS analysis defaults to `freq_mhz=915.0`. — [src/utils/terrain.py:19,816,938](src/utils/terrain.py)
- The standalone detailed link budget prompts `Frequency (MHz) [906.875]`. — [src/standalone.py:226](src/standalone.py)
- The SDR handler's default centre frequency is "915.0". — [src/launcher_tui/handlers/sdr.py:201](src/launcher_tui/handlers/sdr.py)

**Radio chips and hardware templates**
- `templates/available.d/` holds about 29 overlays. They are almost all `Module: sx1262` (900 MHz E22/MeshAdv/RAK/Waveshare etc.), plus `ebyte-e22-400m30s.yaml` (sx1268), `adafruit-rfm9x.yaml`/`elecrow-rfm95.yaml`/`lora-Adafruit-RFM9x.yaml` (RF95/SX1276 family) and `lora-piggystick-lr1121.yaml` (LR1121). **None is labelled or tuned for 2 m.** — [templates/available.d/](templates/available.d/), [templates/available.d/lora-Adafruit-RFM9x.yaml:2](templates/available.d/lora-Adafruit-RFM9x.yaml)
- The `templates/config.yaml` commented module blocks are sx1262, sx1280 and sx1268 ("tested with Ebyte E22 400M33S"). — [templates/config.yaml:18-80](templates/config.yaml)
- `hardware.py` and `meshtasticd_templates.py` describe the 1 W modules only as 915 MHz SX1262 or 433 MHz SX1268. — [src/config/hardware.py:352,373,700](src/config/hardware.py), [src/core/meshtasticd_templates.py:299-322](src/core/meshtasticd_templates.py)
- Chip frequency ranges are already in the repo's research: SX1262 is "150 -- 960 MHz" and SX1276 is "137 -- 1020 MHz". The comparison row reads "Frequency Range | 137--1020 MHz | 150--960 MHz | SX1276 (wider)", and the selection advice says "Need frequency range outside 150--960 MHz (SX1276 goes to 137/1020 MHz)". — [.claude/research/semtech_official_reference.md:217,315,404,451](.claude/research/semtech_official_reference.md)

**The firmware 2.7.26 source pin runs through the code**
- 14 `src` files cite v2.7.26 source behaviour: `config/hardware.py`, `core/meshtasticd_templates.py`, `gateway/ack_tracker.py`, `gateway/canonical_message.py`, `handlers/first_run.py`, `handlers/rf_tools.py`, `launcher_tui/live_truth.py`, `utils/automation_engine.py`, `utils/defaults.py`, `utils/dm_delivery.py`, `utils/map_data_collector.py`, `utils/meshtastic_modem.py`, `utils/pin_recheck.py`, `utils/radio_channel_load.py`. (Measured with `grep -rln -E "v?2\.7\.26" src`.)
- `MESHTASTIC_MAX_PAYLOAD = 233` and the on-air budgets are derived from "meshtasticd v2.7.26 protobufs/meshtastic/mesh.proto" and `Router.cpp`. — [src/gateway/canonical_message.py:50-60](src/gateway/canonical_message.py), [src/utils/defaults.py:61](src/utils/defaults.py)
- `MESHTASTICD_TOP_LEVEL_KEYS` (12 keys) is measured from `PortduinoGlue.cpp` on v2.7.26. Keys outside the set are flagged as silently ignored. — [src/core/meshtasticd_templates.py:31-40](src/core/meshtasticd_templates.py)
- Port number names are "synced with meshtastic 2.7.x". — [src/monitoring/traffic_models.py:68-99](src/monitoring/traffic_models.py)
- Python lib floor is `meshtastic>=2.7.11` with no upper bound. CI installs exactly `meshtastic==2.7.11`. — [requirements/core.txt:11-15](requirements/core.txt), [.github/workflows/ci.yml:235,353](.github/workflows/ci.yml)
- MQTT topic and region handling is version-conditional ("meshtasticd 2.7.x publishes region-less topics"). `mqtt_region` defaults to "US". — [src/gateway/config.py:257,405-410](src/gateway/config.py), [src/gateway/_mesh_bridge_mqtt.py:122-132](src/gateway/_mesh_bridge_mqtt.py)
- The meshtasticd package is held at 2.7.26.54e0d8d by an `apt_hold` pin with a re-check predicate. — [docs/fleet_platform.yaml:98-112](docs/fleet_platform.yaml), checked by [src/utils/pin_recheck.py:1-35](src/utils/pin_recheck.py)

### Inferences
- `meshtastic_modem.py` is the right place to add a 2 m region. If a 2 m RegionCode is added there, it reaches the TUI Frequency Slot calculator ([rf_tools.py:89-92](src/launcher_tui/handlers/rf_tools.py) iterates `REGIONS`), the site planner's generated frequency lines ([site_planner.py:221-232](src/launcher_tui/handlers/site_planner.py)) and the RNode overlap check ([commands/rnode.py:643-662](src/commands/rnode.py)).
- The duplicate tables will not pick it up and would each need hand edits. The cleaner fix is to derive them from `meshtastic_modem.REGIONS` (or from `config_pb2.RegionCode` where the lib is present), as `rf_tools.py` already does.
- 2 m hardware work should start from the SX1276-class overlays (RFM9x templates), because SX1262/SX1268/LR1121 parts stop at 150 MHz or higher (per the repo's own Semtech notes). Whether a given RFM9x board's RF matching network actually performs at 144 MHz is a hardware question. ASSERT: module front ends are band-tuned, so the chip range alone does not mean the board works at 144 MHz.

### Gaps
- **Does Meshtastic firmware 2.8 (or 2.7.x) define a 2 m RegionCode, and what are its edges?** I could not check. The protobuf lib is not installed here and `python3 -c` was denied. The repo pins firmware source at v2.7.26 only, and `REGIONS` (transcribed from that tag) has no 2 m entry. If no such region exists, 2 m Meshtastic would presumably use `override_frequency` under a ham-mode radio, which needs web or upstream confirmation.
- Whether `ModemPreset` changed in 2.8 (new presets, or narrow-band presets suitable for 2 m) is not determinable from this repo.

---

## Q2. Can MeshForge display and manage 2 m Meshtastic nodes? (goal a)

### Takeaway
Display would mostly work, because nodes arrive through meshtasticd, MQTT or the protobuf API whatever their band. Management would not: no TUI path sets `override_frequency`, ham/licensed mode or a 2 m region. The `isLicensed` flag is parsed in one place and shown nowhere. Several validators would reject or mis-describe a 2 m radio.

### Cited Findings
- `NodeInfo.is_licensed` is parsed from `user.get('isLicensed', False)` in `node_monitor`, and nothing else in `src` references it. The gateway `node_models`/`node_tracker` and the map collectors do not carry it. — [src/monitoring/node_monitor.py:88,423](src/monitoring/node_monitor.py). Measured: `grep -rn is_licensed src` returns only these two lines.
- Admin `set_owner` writes long and short name but not `is_licensed`. — [src/gateway/_protobuf_admin.py:452-454](src/gateway/_protobuf_admin.py)
- There is no `--set-ham` / `set_ham` anywhere in `src` or `scripts`. Measured with grep, zero hits.
- `override_frequency` is only READ (RNode overlap check: `"override_frequency": float(lora.override_frequency or 0.0)`, then used as the centre). It is never set by any TUI path. — [src/commands/rnode.py:635,653-654](src/commands/rnode.py)
- The LoRa fields the TUI can set are `lora.modem_preset`, `lora.channel_num` and `lora.region`, plus tx_power via the CLI. — [src/core/meshtastic_cli.py:387,391](src/core/meshtastic_cli.py), [src/utils/device_config_store.py:169-174](src/utils/device_config_store.py), [src/launcher_tui/handlers/radio_menu.py:378-384](src/launcher_tui/handlers/radio_menu.py)
- The RNode check returns UNKNOWN for a radio not on a preset ("radio uses custom LoRa settings") and for a region or preset missing from the firmware table. So a 2 m radio would degrade to UNKNOWN rather than a false "clear". — [src/commands/rnode.py:646-662](src/commands/rnode.py)
- The coverage map uses `PRESET_RANGES` radii (LONG_FAST 10 km ... SHORT_TURBO 1 km). These ignore frequency and are implicitly 915 MHz figures. — [src/utils/coverage_map.py:511-521,583](src/utils/coverage_map.py)
- Packet dissector bug, independent of band: the code labels `portnum == 4` as POSITION and `portnum == 5` as NODEINFO. Its own port table maps 3 = POSITION, 4 = NODEINFO and 5 = ROUTING. — [src/monitoring/packet_dissectors.py:234,246](src/monitoring/packet_dissectors.py) vs [src/monitoring/traffic_models.py:73-75](src/monitoring/traffic_models.py)
- `hwModel` and `role` are captured per node, so a 2 m hardware model would display if the lib knows its enum. — [src/monitoring/node_monitor.py:418-419](src/monitoring/node_monitor.py)

### Inferences
- Minimum changes for (a):
  1. Carry `is_licensed` through `node_models` → map/GeoJSON → TUI node views, and show a "HAM/licensed" badge.
  2. Add a "Licensed (ham) mode" action that sets the owner callsign and `is_licensed` through the guarded admin path (`_protobuf_admin`), not a raw CLI.
  3. Add an `override_frequency` / custom-modem setter that validates against the Part 97 band table (`amateur/compliance.py`).
  4. Make the region pickers derive from `meshtastic_modem.REGIONS` or `RegionCode`.
  5. Show band or centre frequency per node or radio where it is known.
- The coverage map's preset radii should be computed from `rf.realistic_max_range(link_budget, freq_mhz, env)`, not a static table, or they will understate 2 m range. ASSERT: there is roughly 16 dB less FSPL at 146 MHz than at 915 MHz (20·log10(915/146) ≈ 15.9 dB, from the formula in rf.py:473).

### Gaps
- Whether 2 m Meshtastic nodes report a distinct hwModel, region or modem_preset in NodeInfo/MapReport (and so whether MeshForge could tell their band apart) depends on upstream protobufs. Not checked.

---

## Q3. Is the RF math correct at 144-148 MHz? (goal b)

### Takeaway
The core physics in `rf.py` is frequency-generic: FSPL, Fresnel, knife-edge diffraction, log-distance path loss, realistic range, radio horizon and earth bulge. So 2 m math works if callers pass `freq_mhz≈146`. The empirical tables are 868/915 MHz data: environment exponents, building penetration, cable and connector loss, and noise floor. Some antenna guidance text is also 915-specific. No test exercises VHF.

### Cited Findings
**Strengths (generic in frequency)**
- `fresnel_radius(distance_km, freq_ghz)` = 17.3·sqrt(d/(4f)). — [src/utils/rf.py:446-458](src/utils/rf.py)
- `free_space_path_loss(distance_m, freq_mhz)` = 20log d + 20log f − 27.55. — [src/utils/rf.py:461-473](src/utils/rf.py)
- `knife_edge_diffraction` uses `wavelength_m = 300.0 / freq_mhz`. — [src/utils/rf.py:528-564](src/utils/rf.py)
- `log_distance_path_loss`, `realistic_max_range` and `detailed_link_budget` all take `freq_mhz`. — [src/utils/rf.py:687-770,813-884](src/utils/rf.py)
- `radio_horizon_km(h1, h2, k=4/3)` does not depend on frequency. — [src/utils/rf.py:886](src/utils/rf.py)
- `rx_sensitivity(SF, BW)` is computed from SF/BW and a noise figure, not from frequency. — [src/utils/rf.py:786-810](src/utils/rf.py)
- The standalone FSPL and Fresnel tools prompt for frequency with no band restriction. — [src/standalone.py:145-200](src/standalone.py)

**915-specific constants**
- `ENVIRONMENT_PARAMS` (path-loss exponent, shadowing, fade margin) are "From field measurements at 868/915 MHz". — [src/utils/rf.py:52-64](src/utils/rf.py)
- `BUILDING_PENETRATION_DB` is "Building entry loss at 915 MHz (dB) — ITU-R P.2109 + field data" (wood 8 dB ... metal 30 dB). — [src/utils/rf.py:77-85](src/utils/rf.py)
- `CONNECTOR_LOSS_DB` and `CABLE_LOSS_DB_PER_M` are "at 915 MHz" (RG58 0.5 dB/m, LMR-400 0.15 dB/m). `calculate_cable_loss` takes no frequency, so `detailed_link_budget(freq_mhz=146)` still applies 915 MHz cable loss. — [src/utils/rf.py:331-374](src/utils/rf.py), [src/utils/rf.py:733-734](src/utils/rf.py)
- `DEFAULT_NOISE_FLOOR_DBM = -120.0`, and `snr_estimate()` defaults to −120 dBm. — [src/utils/rf.py:226,511](src/utils/rf.py)
- `REPORTED_SNR_CEILING_DB = 6.0` was measured on one SF11/250 kHz receiver on the fleet. The comment itself says other configs are unmeasured. — [src/utils/rf.py:94-104](src/utils/rf.py)
- `effective_radiated_power` docstring: "US Part 15 allows 1W (30 dBm) ERP for 915 MHz ISM". The `fcc_part15_247_check` docstring: "Part 97 (licensed amateur) operation has different rules and is not judged here." — [src/utils/rf.py:393,1026-1041](src/utils/rf.py)
- Antenna guidance in the site planner is written for 915 MHz only ("Antenna Guidelines for 915 MHz", "1/4 wave (915MHz): ~8cm"). — [src/launcher_tui/handlers/site_planner.py:234-255](src/launcher_tui/handlers/site_planner.py), [src/diagnostics/site_planner.py:522-529](src/diagnostics/site_planner.py)
- Knowledge-base text uses 915 MHz Fresnel and cable examples. — [src/utils/knowledge_content.py:150,244](src/utils/knowledge_content.py), [src/utils/knowledge_content_extended.py:158,287-301](src/utils/knowledge_content_extended.py)
- `tests/test_rf.py` passes frequency 915 in every case (18× `, 915)`, 13× `, 915,`, 1× `freq_mhz=915.0`). It has no 144-148 MHz case. Measured with grep.

### Inferences
- Changes for (b):
  1. Make cable and connector loss frequency-aware, either per-band tables or a sqrt(f) scaling flagged as an approximation, and pass `freq_mhz` into `calculate_cable_loss`.
  2. Add VHF rows (or a frequency argument) for building penetration, which is lower at VHF (ASSERT), and for environment exponents. Label them UNMEASURED until fleet data exists, following the repo's own `REPORTED_SNR_CEILING_DB` honesty pattern.
  3. Add a VHF noise-floor option. ASSERT: man-made noise at 144 MHz is typically above the thermal floor, so −120 dBm may be optimistic.
  4. Replace the 906.875/915 defaults with "the radio's own centre frequency" (from `meshtastic_modem.channel_centre_mhz` or `override_frequency`).
  5. Add 146 MHz cases to `tests/test_rf.py`.
- ASSERT (physics): the first Fresnel radius at 146 MHz is about 2.5× the 915 MHz value (sqrt(915/146)). Clearance heights and terrain LOS verdicts at 915 MHz would be wrong at 2 m if callers keep the 915 default. `terrain.py`'s default makes this a silent risk.

### Gaps
- Whether SX1276 LoRa sensitivity at 137-175 MHz differs from the 868/915 datasheet figures the repo carries. `semtech_official_reference.md:288,367` says its values are "typical at 868/915 MHz". No VHF band row was found.

---

## Q4. Does MeshForge enforce or inform ham-band rules (licensed mode, no encryption, callsign ID)? (goal c)

### Takeaway
There is an `amateur/` package with a 2 m band entry, callsign tools and a 10-minute ID timer. It is mostly reference material and is not wired to the radio or the bridge. The TUI band plan deliberately filters 2 m out. The compliance module's rule summary leaves out the §97.113 encryption ban. Nothing checks that a ham-band radio has encryption off.

### Cited Findings
- `Part97Reference` has `BandPrivilege(band="2m", 144.0-148.0, modes [CW, Phone, Digital, FM, SSB, Packet], 1500 W, Tech+)` and `BandSegment("2m", "All Modes", 144.000, 148.000, ...)`. — [src/amateur/compliance.py:304-313](src/amateur/compliance.py), [src/amateur/compliance.py:444-448](src/amateur/compliance.py)
- The rule summary for '97.113' reads "(a) Music, (b) Business/commercial, (c) Broadcasting, (d) Obscene language, (e) False signals, (f) Third-party without agreement". It does **not** mention "messages encoded for the purpose of obscuring their meaning". The '97.311' SS entry is a one-line summary. — [src/amateur/compliance.py:169-216](src/amateur/compliance.py)
- `ComplianceChecker.check_content()` flags commercial words and long messages only. There is no encryption or PSK check. — [src/amateur/compliance.py:845-884](src/amateur/compliance.py)
- `ComplianceChecker.MESHTASTIC_PRESETS` is stale and covers ISM only (`'EU_868': {'freq': 869.525, 'bw': 125, 'sf': 12...}`, JP/KR/TW 125 kHz). It disagrees with the firmware table in `meshtastic_modem`. — [src/amateur/compliance.py:523-530](src/amateur/compliance.py)
- `ISM_BANDS` maps only 900 MHz/2.4 GHz (US), 868 (EU) and 915 (AU_NZ) to Meshtastic regions. — [src/amateur/compliance.py:475-520](src/amateur/compliance.py)
- The TUI band plan filters to `lora_ranges = ((420.0, 450.0), (902.0, 928.0))`, so **2 m is never shown**. Its fallback text says "No encryption allowed on ham bands" and "Meshtastic ham mode: higher power, ID broadcast". — [src/launcher_tui/handlers/amateur_radio.py:124-170](src/launcher_tui/handlers/amateur_radio.py)
- The TUI compliance check shells out to `meshtastic --host localhost --get lora`, scrapes any line containing "frequency" and checks only frequency, not power. Errors are swallowed (`except Exception: pass`). The radio's encryption or licensed state is never read. — [src/launcher_tui/handlers/amateur_radio.py:172-230](src/launcher_tui/handlers/amateur_radio.py)
- `CallsignManager.should_identify()` / `get_id_string()` and `StationIDTimer` (10-minute default, `_stop_event.wait(1)` loop) exist. Their only users are inside `src/amateur/` (re-exported by `__init__`). No gateway, bridge or TUI send path calls them. `should_identify` uses `datetime.now()` wall-clock deltas. — [src/amateur/callsign.py:572-610](src/amateur/callsign.py), [src/amateur/callsign.py:715-830](src/amateur/callsign.py), [src/amateur/__init__.py:12-19](src/amateur/__init__.py)
- The project's own failure-mode rule says wall-clock durations are forgeable on RTC-less Pis and require monotonic anchors. — [.claude/rules/honest_failure_modes.md](.claude/rules/honest_failure_modes.md) item 6
- Operator decision recorded in the bridge contract: "fleet radios are NOT in licensed (HAM) mode and modes stay — licensed mode disables encryption and loses LongFast public". The 220 B DM limit "assumes PKC (wrong only for a licensed radio...)". — [.claude/foundations/bridge_contract_matrix.md:241-242](.claude/foundations/bridge_contract_matrix.md)
- `utils.defaults.MESHTASTIC_ONAIR_MAX_DM = 220` and `MAX_MESHTASTIC_DM_MSG_LENGTH = 215` assume PKI overhead (same matrix rows F17/F18).

### Inferences
- Changes for (c):
  1. Add 144-148 MHz (and 222 MHz) to the band-plan filter, or show every Part 97 allocation a LoRa chip can reach (137-1020 MHz).
  2. Add the §97.113(a)(4) encryption ban to the rule table.
  3. Add a "ham posture" probe that reads the radio's owner `is_licensed`, channel PSKs and override frequency, and returns OK / VIOLATION / UNKNOWN. An unread radio must be UNKNOWN, per honest_failure_modes #2.
  4. Wire `StationIDTimer` (on a monotonic clock) into any TX path a ham-mode leg uses.
  5. Refresh `ComplianceChecker.MESHTASTIC_PRESETS` from `meshtastic_modem` or delete it.
- On a licensed radio the PKI-DM budget assumption (220 B) is conservative, not wrong: oversize refusals would fire early. BELIEVED from the matrix text, not measured.

### Gaps
- §97.305/§97.307/§97.311 limits on 2 m data or spread-spectrum emissions (bandwidth, symbol rate, whether SS/CSS is authorized below 222 MHz) are not in the repo beyond one-line summaries. ASSERT: this may decide whether 125/250 kHz LoRa is legal on 2 m at all in the US, so it needs the regulatory notes. The repo's verified Part 97 research ([.claude/research/dudeclaw_role_usecases_2026_06_21.md:153-178](.claude/research/dudeclaw_role_usecases_2026_06_21.md)) covers 33 cm and 70 cm, not 2 m.

---

## Q5. Can 2 m traffic be bridged to 915/433/868 meshes and RNS without breaking amateur rules? (goal d)

### Takeaway
The bridge (`mesh_bridge.py` preset bridge, `rns_bridge.py`, MQTT downlink inject) has no idea that a leg might be amateur. It has no per-leg "licensed" attribute, no rule to keep traffic in clear text, no callsign stamping, and no third-party-traffic gate. Its encryption features (downlink PSK, AES-CTR envelopes) assume ISM channels. RNode profile validation accepts 137 MHz and up, so an RNS-on-2 m interface (RNS is always encrypted) would pass validation with no warning.

### Cited Findings
- `MeshBridgeConfig` "Bridges two separate Meshtastic networks with different LoRa presets". Each leg has `preset`, `name`, `downlink_psk` and `injection_mode`. Forwarded text is prefixed `"[Mesh:{source_preset}] "`. There is no licensed or callsign field. — [src/gateway/config.py:268-381](src/gateway/config.py), [src/gateway/config.py:295-308](src/gateway/config.py)
- `downlink` injection AES-CTR-encrypts ServiceEnvelopes with a 32-byte channel PSK and requires it (`expected 32-byte AES-256 PSK`). — [src/gateway/mqtt_downlink_inject.py:207-321,344-386](src/gateway/mqtt_downlink_inject.py), [src/gateway/_rns_bridge_xform.py:789-843](src/gateway/_rns_bridge_xform.py)
- Grep for `part.?97|amateur|ham|licens` across `rnode_profile.py`, `commands/rnode.py`, `handlers/rnode.py`, `mesh_bridge.py`, `rns_bridge.py` and `message_routing.py` returns **zero hits**.
- RNode profile validation: `if not 137_000_000 <= p["frequency"] <= 3_000_000_000: raise ProfileError("...outside RNode hardware range")`. That is a hardware check only, with no band-regime check. Region defaults are US 903.625, EU 867.5 and AU 917 MHz. — [src/utils/rnode_profile.py:35-74](src/utils/rnode_profile.py)
- `MessageRouter` decides "by rules and direction, not by path quality". — [.claude/ROADMAP.md:72-84](.claude/ROADMAP.md)
- Prior in-repo posture for an amateur bridge (AREDN): "Part 97 posture by design: bridged traffic crosses AREDN unencrypted, tagged". Any TCPInterface site-to-site link "would put encrypted tunnels over Part 97 links". — [.claude/research/aredn_meshtastic_openwrt_2026_06_11.md:63,111-116](.claude/research/aredn_meshtastic_openwrt_2026_06_11.md)
- CLAW-1 decision: "Receive-first. ... TX growth is a separate, ratified step after §97.115 is read verbatim." — [.claude/plans/claw1_grilling_2026_11_11.md:15](.claude/plans/claw1_grilling_2026_11_11.md)
- The repo's verified Part 97 table: ISM gets "AES OK / no ID". 33 cm/70 cm amateur get "clear-text only (§97.113) / ID ≤10 min (§97.119) / automatic control permitted (§97.221(b))". — [.claude/research/dudeclaw_role_usecases_2026_06_21.md:155-178](.claude/research/dudeclaw_role_usecases_2026_06_21.md)

### Inferences
- Needed for (d):
  1. A per-leg `regime: ism | amateur` attribute in `MeshBridgeConfig` and in the RNS interface writers. Following the repo's honest-failure rule #3, validation should refuse `amateur` + `downlink_psk`, `amateur` + `injection_mode=downlink`, and an amateur leg with no configured control-operator callsign.
  2. An egress policy for amateur legs: stamp the callsign or ID (StationIDTimer), tag the origin network (the existing `[Mesh:...]` prefix is a start), and an optional allow-list or "licensed originators only" gate for third-party traffic (§97.115; repo summary at [compliance.py:174-178](src/amateur/compliance.py)).
  3. A warning when an RNode profile frequency lands inside a Part 97 allocation (use `Part97Reference.get_band_by_frequency`), because RNS links and LXMF are encrypted end to end. ASSERT: this is the reason "RNS on 2 m" is a regulatory problem, not just a configuration one. The 2026-06 outline says "RNS doesn't dictate band; you do. Run it on Part 97 frequencies, follow ID rules" ([docs/birc_2026_05_17_outline.md:196](docs/birc_2026_05_17_outline.md)), which contradicts §97.113 for encrypted RNS traffic and should be revisited.
- Decrypt-at-gateway and re-transmit in clear text (the AREDN pattern) is the existing compliant model. In the 915 (encrypted) → 2 m (clear) direction, the bridge would publish content that 915 users sent under an encrypted expectation. That is a privacy and consent concern for the bridge contract matrix ("what it keeps / loses / translates"), not only a regulatory one.

### Gaps
- Whether third-party traffic from unlicensed 915 MHz Meshtastic users relayed onto 2 m by an automatically controlled station is permissible (§97.115 and §97.221 applied to 2 m) needs the regulatory research. I found nothing in the repo that settles it for 2 m.

---

## Q6. How ready is the code for a firmware 2.8 protobuf/API bump? (goal e)

### Takeaway
The repo is unusually well instrumented for a firmware bump: source-pinned constants, a test that pins `FIRMWARE_TAG`, a pin-recheck catalog and a dependency floor probe. But the pins are everywhere (14 files), the lib floor is 2.7.11 with CI pinned to exactly 2.7.11, and fleet notes record that 2.8 "re-keys NodeNum" and was alpha-only. Several region and enum tables are hand-written rather than read from protobuf enums.

### Cited Findings
- Fleet note: "Do not roll meshtasticd: `apt-mark hold` on all 9 boxes ... 2.8.x is alpha, re-keys NodeNum, had builds revoked. Before any move, `objdump -T` the box's OWN package for `pthread_detach`". The fix for firmware#10468 "ships in 2.8.2 ... NOT in 2.7.26 stable". — [.claude/foundations/persistent_issues.md:174-181](.claude/foundations/persistent_issues.md)
- 2026-11-09 landscape check: `meshtastic/firmware v2.8.2.d5f1619 src/mesh/NodeDB.cpp:1192 still sets ModemPreset_LONG_FAST; every 2.8 tag is a pre-release (stable = 2.7.26)`. — [.claude/research/landscape_2026_11_09.md:50](.claude/research/landscape_2026_11_09.md)
- `meshtastic_modem.py`'s update procedure: "re-read modemPresetToParams at the new firmware tag, edit FIRMWARE_TAG and the table together; tests/test_meshtastic_modem.py pins both." — [src/utils/meshtastic_modem.py:15-18](src/utils/meshtastic_modem.py)
- The dependency floor is read by `probe_dep_version_drift` as the SSOT. — [requirements/core.txt:11-15](requirements/core.txt), [src/utils/requirements_floor.py:31-82](src/utils/requirements_floor.py)
- Version parsing handles `2.7.15.567b8ea`-style strings. — [src/installer/version.py:23-40](src/installer/version.py), [src/updates/version_checker.py:102,359-399](src/updates/version_checker.py)
- Code reads enums dynamically in a few places (`enum.RegionCode.Name(lora.region)`, `ModemPreset.Name(...)`). An unknown int from newer firmware read through an older lib would raise there; `rnode.py` catches it as UNKNOWN. — [src/commands/rnode.py:629-638](src/commands/rnode.py), [src/launcher_tui/handlers/gateway_wizard.py:236](src/launcher_tui/handlers/gateway_wizard.py)
- Hand-mirrored enum: the `MESHTASTIC_PORTS` dict ("synced with meshtastic 2.7.x"). Unknown ports fall back to `PORT_{n}`, so this degrades gracefully. — [src/monitoring/traffic_models.py:68-99](src/monitoring/traffic_models.py), [src/monitoring/packet_dissectors.py:103](src/monitoring/packet_dissectors.py)
- `MESHTASTICD_TOP_LEVEL_KEYS` (12 keys measured on v2.7.26) would flag any new 2.8 config.yaml key as "silently ignored". — [src/core/meshtasticd_templates.py:31-40](src/core/meshtasticd_templates.py)
- Firmware-behaviour pins that would need re-verifying on 2.8: payload and on-air budgets ([canonical_message.py:50-60](src/gateway/canonical_message.py), [defaults.py:61](src/utils/defaults.py)), implicit-ACK semantics ([ack_tracker.py:39](src/gateway/ack_tracker.py)), the NodeDB rule ([map_data_collector.py:40](src/utils/map_data_collector.py)), channel-load legs ([radio_channel_load.py:13](src/utils/radio_channel_load.py)), DM delivery ([dm_delivery.py:4](src/utils/dm_delivery.py)), API rate limiting ([rns_bridge.py:89,567](src/gateway/rns_bridge.py), [message_queue.py:146](src/gateway/message_queue.py)), and region-less MQTT topics ([config.py:405-410](src/gateway/config.py)).

### Inferences
- A 2.8 bump checklist from the code:
  1. Re-transcribe `REGIONS` and `FIRMWARE_MODEM_PARAMS` at the 2.8 tag, adding any 2 m or ham region if one exists, and update the test pin.
  2. Raise the `meshtastic` floor and the CI pin together.
  3. Re-measure the 14 v2.7.26-pinned constants.
  4. Re-derive `MESHTASTICD_TOP_LEVEL_KEYS`.
  5. Plan node identity migration for the NodeNum re-key, since node trackers, favorites, watch lists and dedup keys depend on it. The CLAW-1 plan already notes "watch lists that rot when a node id re-keys" ([claw1_grilling_2026_11_11.md:16](.claude/plans/claw1_grilling_2026_11_11.md)).
  6. Replace the hand-written region pickers with `RegionCode` enumeration so new region codes appear automatically.
- A 2.8 firmware roll is blocked by policy (apt hold, CLAUDE.md "fleet NEVERs"), not by code. Any 2 m work that needs 2.8-only features inherits that gate.

### Gaps
- The actual 2.8 protobuf diff (new LoRaConfig fields, RegionCodes, ModemPresets, User fields) cannot be determined here because no lib or proto is in the container. It needs the upstream `meshtastic/protobufs` diff between the v2.7.26 and v2.8.x tags.

---

## Q7. Is there existing research in .claude/research/ on VHF, ham mode or Meshtastic versions?

### Takeaway
There is no VHF or 2 m Meshtastic research doc. There is relevant adjacent material: a verified Part 97 encryption, ID and automatic-control analysis (scoped to 33 cm and 70 cm), Semtech chip frequency ranges (SX1276 reaches 137 MHz, SX1262 does not), the AREDN clear-text bridge posture, and firmware 2.8 status notes.

### Cited Findings
- `dudeclaw_role_usecases_2026_06_21.md` Angle 5 is the Part 97 regulatory gate, with CFR text quoted: §97.113(a)(4) (no encryption), §97.119(a) (ID ≤10 min; digital ID allowed by (b)(3)) and §97.221(b) (automatic control on "6 m or shorter" bands). Its regime table covers 915 ISM vs 33 cm/70 cm only. It mentions 144.39 MHz only as North American APRS. — [.claude/research/dudeclaw_role_usecases_2026_06_21.md:42-46,90,153-178](.claude/research/dudeclaw_role_usecases_2026_06_21.md)
- `semtech_official_reference.md` has the SX1262 (150-960 MHz) and SX1276 (137-1020 MHz) ranges and FSPL formulas. Its sensitivity tables are "typical at 868/915 MHz". — [.claude/research/semtech_official_reference.md:217,288,315,367,404,451](.claude/research/semtech_official_reference.md)
- `aredn_meshtastic_openwrt_2026_06_11.md` documents the Part 97 clear-text-and-callsign bridge posture. — [.claude/research/aredn_meshtastic_openwrt_2026_06_11.md:63,111-116](.claude/research/aredn_meshtastic_openwrt_2026_06_11.md)
- `landscape_2026_11_09.md` documents 2.8 status (pre-release; LONG_FAST still the default). — [.claude/research/landscape_2026_11_09.md:50](.claude/research/landscape_2026_11_09.md)
- `lora_physical_layer.md` covers the LoRa PHY, with sensitivity tables per SF. — [.claude/research/lora_physical_layer.md:252-273](.claude/research/lora_physical_layer.md)
- `firmware_viability.md` exists. I did not read it in depth; grep found no VHF or 2.8 terms in it.
- `domain_architecture.md` mentions VHF only for HF/VHF propagation (aurora). Propagation code is HF-only: `get_band_conditions` is "HF band propagation conditions", and there is no tropospheric-ducting model. — [.claude/foundations/domain_architecture.md:141,149](.claude/foundations/domain_architecture.md), [src/commands/propagation.py:334-341](src/commands/propagation.py)
- The PSKReporter band list includes "2m", "70cm". — [src/monitoring/pskreporter_subscriber.py:74-77](src/monitoring/pskreporter_subscriber.py)

### Inferences
- A new research doc would close the gap: 2 m Meshtastic plus the §97.305/307/311 emission rules for 2 m. It would extend the existing Angle 5 table with a 2 m row, which is currently missing.
- ASSERT: VHF tropospheric ducting matters more at 2 m than at 915 MHz. MeshForge's NOAA/HF propagation layer has no hook for it, which is an opportunity rather than a requirement.

### Gaps
- I did not read every research doc in full. The grep for VHF/144/2 m/licensed/2.8 is the basis for "no VHF doc exists".

---

## Q8. What does the roadmap say that this relates to?

### Takeaway
The roadmap puts "RNS is the network stack and the hub", "one box with N radios is its own hub", and best-available-route by measured leg health at the centre. A 2 m leg fits as another measured leg, but it is a different regulatory regime that the bridge contract does not yet model. The roadmap also says to STOP adding new edges "before the 2-leg bridge is honest".

### Cited Findings
- "RNS is the network stack and the hub. Cross-mesh traffic goes RNS-first." / "One box with N radios is its own hub (D1, 2026-10-18) ... STANDALONE Meshtastic router ... Build it on src/gateway/mesh_bridge.py". — [.claude/ROADMAP.md:26-36](.claude/ROADMAP.md)
- "The bridge contract is .claude/foundations/bridge_contract_matrix.md ... per edge, what is kept / lost / translated ... Upstream versions are variables; that matrix is the constant." — [.claude/ROADMAP.md:37-41](.claude/ROADMAP.md)
- "STOP: dashboard breadth, new detectors without a product defect, new edges before the 2-leg bridge is honest". — [.claude/ROADMAP.md:62-66](.claude/ROADMAP.md)
- The central gap, best available route: "MessageRouter decides by rules and direction, not by path quality"; "R2c ... the moc3↔VolcanoAI RNode leg dies both ways during 915 MHz interference bursts". — [.claude/ROADMAP.md:70-84](.claude/ROADMAP.md)
- BIC-6: "best-available-route: the router uses measured leg health so a flaky RF leg cannot own paths (1.1, not a 1.0 gate)". — [.claude/ROADMAP.md:193](.claude/ROADMAP.md)
- PARITY-1 lists "coverage planning (Site Planner)" among features to match. — [.claude/ROADMAP.md:262](.claude/ROADMAP.md)
- CLAW-1: RF witnesses, receive-first, with TX expansion gated on reading §97.115. — [.claude/ROADMAP.md:275](.claude/ROADMAP.md), [.claude/plans/claw1_grilling_2026_11_11.md:15](.claude/plans/claw1_grilling_2026_11_11.md)
- "Built by a ham, for hams". — [.claude/ROADMAP.md:46](.claude/ROADMAP.md)

### Inferences
- 2 m adds a band-diverse leg, so 915 MHz interference bursts would not take it down. That is exactly the kind of leg best-available-route (BIC-6) wants. The roadmap's ordering, though (2-leg bridge honest first; new edges stopped), puts a 2 m leg after BR-T and the 2-leg honesty work. It would most naturally land as a 1.1 item tied to BIC-6, plus a new bridge-matrix edge row declaring that the 2 m leg drops encryption and adds callsign ID.
- The bridge contract matrix is the natural place to declare the regulatory "translation": encrypted on 915, clear text on 2 m, with ID added. This fits its kept / lost / translated vocabulary.

### Gaps
- There is no roadmap item that mentions VHF, 2 m, ham-mode legs or firmware 2.8 adoption explicitly. Grep of ROADMAP.md for vhf/licens/2.8/part 97 found none.

---

## Summary table: changes by goal (from the findings above)

| Goal | Exists (strength) | Missing / wrong | Key files |
|---|---|---|---|
| (a) display/manage | Nodes are ingested whatever the band; `isLicensed` parsed; RNode check degrades to UNKNOWN | `is_licensed` shown nowhere; no ham-mode, override_frequency or 2 m region setter; 6+ hand-written region tables; dissector portnum off-by-one | node_monitor.py:88,423; radio_menu.py:352; first_run.py:119; packet_dissectors.py:234,246 |
| (b) RF math | FSPL, Fresnel, diffraction, range and horizon are frequency-generic | 915 defaults (906.875/915); cable, building and environment tables at 915; noise floor; no VHF tests; static coverage radii | rf.py:52-85,226,331-374,398,529,696; terrain.py:816; preset_impact.py:69; coverage_map.py:512 |
| (c) ham rules | 2 m in Part97Reference; callsign tools; StationIDTimer | Band plan hides 2 m; §97.113 encryption ban missing from rule table; no PSK/licensed check; ID timer unwired and wall-clock based | compliance.py:169-216,304,444,523,845; amateur_radio.py:152,172-230; callsign.py:572,715 |
| (d) bridging | AREDN clear-text precedent; prefix tagging | No per-leg regime; PSK/downlink allowed on any leg; no callsign stamping or third-party gate; RNode accepts ham freqs with no Part 97 warning | config.py:268-381; mqtt_downlink_inject.py; rnode_profile.py:61 |
| (e) firmware 2.8 | FIRMWARE_TAG + test pin; pin_recheck; dependency floor probe | 14 files pinned to v2.7.26; lib 2.7.11 floor / CI 2.7.11 exact; NodeNum re-key; hand-written enums; YAML key allowlist | meshtastic_modem.py:25; requirements/core.txt:15; ci.yml:235,353; persistent_issues.md:181 |
