# Meshtastic firmware 2.8.x telemetry & sensor support vs 2.7.26 (researched 2026-11-14)

Method: read-only git clones (treated as untrusted data, nothing built or run) of
`meshtastic/protobufs` (master @ `a09c0ef`, 2026-11-14) and `meshtastic/firmware`
(develop @ `c7f895df1`, 2026-11-14) under
`/tmp/claude-0/-home-user-meshforge/05c0902a-7fe6-51f6-a7ef-c160469e24c5/scratchpad/clones2/`.
The GitHub HTML pull list returned 403; one unauthenticated REST search call worked (open-PR list below).
Evidence tags: **VERIFIED** = read directly in the source at the named tag/commit this session;
**BELIEVED** = inferred from reading code, not run; **UNKNOWN** = not established.

Tag dates (VERIFIED, `git log -1` commit dates):
- protobufs: v2.7.26 `da60cee` 2026-06-17 · v2.8.0 `7b2464c` 2026-08-28 · v2.8.1 `8017acb` 2026-10-01 · v2.8.2 `ffd5ad4` 2026-10-28
- firmware: v2.7.26.54e0d8d 2026-06-23 · v2.8.0.47db0e3 2026-08-31 · v2.8.1.8e6a88d 2026-09-28 · v2.8.2.d5f1619 2026-11-03 (55 develop commits since). The 2.7/develop merge-base is `82aefd1af` (2026-05-19).

## Q1 — telemetry.proto / module_config.proto: v2.7.26 → v2.8.0/2.8.1/2.8.2/master

### Takeaway
2.8 adds one new `Telemetry` oneof variant (`soil_water_metrics = 11`, from protobufs v2.8.1), 18 new EnvironmentMetrics fields (ADC ch0–7, one-wire temperature ch0–7, lightning), `AirQualityMetrics.pm_status_flags = 26`, four new sensor-type enums (54–57), and deprecates PowerMetrics ch4–ch8 and `EnvironmentMetrics.one_wire_temperature = 23`. TelemetryConfig gets no new wire fields, only metadata annotations. MQTT `json_enabled` is now deprecated and ignored. (VERIFIED)

### Cited Findings
- **Telemetry oneof on master (VERIFIED):** `device_metrics=2, environment_metrics=3, air_quality_metrics=4, power_metrics=5, local_stats=6, health_metrics=7, host_metrics=8, traffic_management_stats=9, soil_water_metrics=11`. Tag 10 is not used in the oneof. `TrafficManagementStats` (9) was already in v2.7.26. Its fields are unchanged: `packets_inspected=1, position_dedup_drops=2, nodeinfo_cache_hits=3, rate_limit_drops=4, unknown_packet_drops=5, hop_exhausted_packets=6, router_hops_preserved=7`. — [telemetry.proto @ master](https://github.com/meshtastic/protobufs/blob/master/meshtastic/telemetry.proto)
- **New in v2.8.0 (VERIFIED, by grepping each tag):**
  - EnvironmentMetrics `adc_voltage_ch0..ch7 = 24..31` (float V) and `one_wire_temperature_ch0..ch7 = 32..39` (float °C)
  - `lightning_strike_count_1h = 40` (uint32) and `lightning_distance_km = 41` (float)
  - `repeated float one_wire_temperature = 23` is now `[deprecated = true]`, with the comment "Never implemented, but Voltage may be mis-interpreted by old clients as temperature"
  - `AirQualityMetrics.pm_status_flags = 26` (uint32, the raw SEN6X status register: bit 4 fan, 6 RH&T, 7 gas/VOC-NOx, 9 CO2 (SEN66), 10 HCHO, 11 PM, 12 CO2 (SEN63C/69C), 21 fan-speed warning)
  - Enums `SPA06 = 54`, `HM330X = 55`, `SEN6X = 56`, `AS3935 = 57`
  - New flash-state messages `SEN6XState` and `AS3935State{tuning_cap_pf=1}`
  - `SHTC3=7, SHT31=12, SHT4X=17, SHT21=47` are now `[deprecated=true]`, because the drivers were consolidated into SHTXX
  - Source commits: "Move ADC values to env telemetry (#877)" 2026-08-11, "Add SEN6X fields (#1031)" 2026-08-11, "Add AS3935 lightning sensor telemetry fields (#981)" 2026-08-17, "Add HM330X sensor" 2026-07-16
  - [v2.8.0 telemetry.proto](https://github.com/meshtastic/protobufs/blob/v2.8.0/meshtastic/telemetry.proto); [#981](https://github.com/meshtastic/protobufs/pull/981); [#1031](https://github.com/meshtastic/protobufs/pull/1031); [#877](https://github.com/meshtastic/protobufs/pull/877)
- **PowerMetrics (VERIFIED):** `ch4_voltage=7 … ch8_current=16` are marked `[deprecated = true]` with the note "TODO Remove". ch1–ch3 (fields 1–6) remain. — [v2.8.0 telemetry.proto](https://github.com/meshtastic/protobufs/blob/v2.8.0/meshtastic/telemetry.proto)
- **New in v2.8.1 (VERIFIED): `SoilWaterMetrics`**, carried as `Telemetry.soil_water_metrics = 11`. Fields:
  - `soil_ph=1, ph=2, electrical_conductivity=3` (mS/cm), `salinity=4` (mg/l)
  - `nitrogen=5, phosphorus=6, potassium=7` (mg/kg)
  - `dissolved_oxygen=8` (mg/l), `orp=9` (mV), `chemical_oxygen_demand=10` (mg/l), `turbidity=11` (NTU)
  - `nitrate=12, ammonium=13` (ppm), `biochemical_oxygen_demand=14` (mg/l), `solar_irradiance=15` (W/m²)
  - History: these were first added to EnvironmentMetrics ("Add telemetry protos for soil and water metrics (#1055)", 2026-09-03). They were then split out to keep that message inside the payload budget ("Split soil and water chemistry out of EnvironmentMetrics into SoilWaterMetrics", 2026-09-09). EnvironmentMetrics now carries `reserved 42 to 56` plus the 15 reserved names.
  - [v2.8.1 telemetry.proto](https://github.com/meshtastic/protobufs/blob/v2.8.1/meshtastic/telemetry.proto); [#1055](https://github.com/meshtastic/protobufs/pull/1055)
- v2.8.1 → v2.8.2 → master: no further wire changes to telemetry.proto (the grep markers are identical). "Cap HostMetrics.user_string at 161 bytes so Telemetry fits the 233 byte payload (#1073)" (2026-09-10) changes the nanopb options only. — [#1073](https://github.com/meshtastic/protobufs/pull/1073)
- **Fields already present in v2.7.26, so not new in 2.8 (VERIFIED by diff context):** soil_moisture/soil_temperature (through 22), `LocalStats.noise_floor = 15`, radiation, rainfall, wind, UV/IR/white lux, IAQ, and CO2/PM/particle counts. — [v2.7.26 telemetry.proto](https://github.com/meshtastic/protobufs/blob/v2.7.26/meshtastic/telemetry.proto)
- **module_config.proto TelemetryConfig (VERIFIED):** the fields are the same set in v2.7.26 and master:
  - `device_update_interval=1, environment_update_interval=2, environment_measurement_enabled=3, environment_screen_enabled=4, environment_display_fahrenheit=5`
  - `air_quality_enabled=6, air_quality_interval=7`
  - `power_measurement_enabled=8, power_update_interval=9, power_screen_enabled=10`
  - `health_measurement_enabled=11, health_update_interval=12, health_screen_enabled=13`
  - `device_telemetry_enabled=14` (since_firmware "2.7.13"), `air_quality_screen_enabled=15` (since "2.7.18")
  - Only `field_metadata` labels and descriptions were added.
  - [module_config.proto @ master](https://github.com/meshtastic/protobufs/blob/master/meshtastic/module_config.proto)
- **Other module_config changes relevant to telemetry (VERIFIED):**
  - `MQTTConfig.json_enabled = 6` is `deprecated=true`, `deprecated_since: "2.8.0"`, with the comment "JSON packet support on MQTT was removed, and this field is ignored".
  - The SerialConfig mode gains `MODBUS = 11` ("Modbus-RTU master, polls an RS485 sensor and sends telemetry"), from "Add MODBUS serial module mode (#1127)", 2026-10-03, after v2.8.1.
  - TrafficManagementConfig removes the booleans `enabled, position_dedup_enabled, rate_limit_enabled, exhaust_hop_telemetry, router_preserve_hops…` (now reserved). Non-zero `rate_limit_window_secs=8` / `rate_limit_max_packets=9` now implicitly enable rate limiting, and 0 disables it.
  - [#1127](https://github.com/meshtastic/protobufs/pull/1127); [#1066 zero semantics](https://github.com/meshtastic/protobufs/pull/1066)

### Inferences
- BELIEVED: a consumer that decodes with v2.7.26 generated code will silently drop the fields above 23, `pm_status_flags`, and the whole `soil_water_metrics` variant. In python, an unknown oneof member decodes to `WhichOneof('variant') == None`. That yields an empty Telemetry, not an error, which is a classic "degraded → valid-looking" trap (honest_failure_modes item 1).
- BELIEVED: old clients may misread the deprecated `one_wire_temperature=23` (the proto comment says so). Consumers should ignore 23 and read `one_wire_temperature_ch*`.

### Gaps
- `HealthMetrics` / `HostMetrics` / `DeviceMetrics` / `LocalStats` field-level diff: these messages had no hunk in the `v2.7.26..master` diff, so I believe they are unchanged apart from the options file. The full message text was not re-read.

## Q2 — Firmware sensor drivers added in 2.8 (src/modules/Telemetry/Sensor/)

### Takeaway
v2.8.0 added these drivers: ADS1X15 ADC, AS3935 lightning, an in-tree BME680 IAQ estimator (the BSEC blob was removed), DS248X one-wire I²C bridge, HM330X PM, SEN6X, SPA06, and SHTxx consolidation. v2.8.2 added RAKSensorHub (RAK2560 WisMesh Hub). develop, after Nov 4, adds Linux `Sensors:` shell-command telemetry (CommandSensor). No firmware path emits `SoilWaterMetrics` yet. (VERIFIED by `git ls-tree` per tag + grep)

### Cited Findings
- **Driver headers at each tag (VERIFIED, `git ls-tree`):**
  - Removed after v2.7.26: `SHT31Sensor`, `SHT4XSensor`, `SHTC3Sensor` (merged into `SHTXXSensor`) and `IndicatorSensor`.
  - New at v2.8.0: `ADS1X15Sensor, AS3935Sensor, BME680IaqEstimator, CO2Sensor, DS248XSensor, HM330XSensor, RollingCounter, SEN6XSensor, SENXXSensor, SHTXXSensor, SPA06Sensor`.
  - v2.8.1 is the same as v2.8.0.
  - New at v2.8.2: `RAKSensorHub` (+`RAKSensorHubUplink.cpp`).
  - New on develop: `CommandSensor`, `CommandSensorFields`.
  - [firmware tree @ develop](https://github.com/meshtastic/firmware/tree/develop/src/modules/Telemetry/Sensor)
- **Dated commits (VERIFIED, `git log`):**
  - "Consolidate SHTs into one class (#9859)" 2026-04-04
  - "Add one wire i2c bridge support (#10078)" 2026-08-03
  - "Add HM330x PM Sensor (#11069)" 2026-08-05
  - "Multi one wire measurements (#10192)" 2026-08-11
  - "Add ADS1X15 ADC (#9846)" 2026-08-13
  - "Add SEN6x sensors (#11390)" 2026-08-13
  - "Remove proprietary Bosch BSEC blob; open in-tree IAQ estimator for BME680 (#11381)" 2026-08-13
  - "Add AS3935 lightning sensor support (#10931)" 2026-08-19
  - "feat(raksensorhub): SensorHub uplink for WisMesh Hub (RAK2560) (#11817)" 2026-10-15
  - Links: [#10931](https://github.com/meshtastic/firmware/pull/10931), [#11390](https://github.com/meshtastic/firmware/pull/11390), [#11381](https://github.com/meshtastic/firmware/pull/11381), [#9846](https://github.com/meshtastic/firmware/pull/9846), [#11069](https://github.com/meshtastic/firmware/pull/11069), [#10078](https://github.com/meshtastic/firmware/pull/10078), [#10192](https://github.com/meshtastic/firmware/pull/10192), [#11817](https://github.com/meshtastic/firmware/pull/11817)
- **Detection and I²C fixes (VERIFIED):**
  - "Rescan I2C on AQ module init (#10593)" 2026-07-03
  - "Don't misdetect the ES7210 audio codec as an INA219 (#11120)"
  - "fix(detect): check LPS22HB before SFA30 at 0x5D and CRC-validate SFA30 probe (#11881)" 2026-09-18
  - "Fix INA226 detection for non-TI compatible chip (Silergy) (#10247)"
  - "Reclock I2C for sensors is aware of screen I2C frequency (#9898)"
  - "I2C reclock guard … SENXX (#11412)"
  - [#11881](https://github.com/meshtastic/firmware/pull/11881)
- **Who populates the new fields (VERIFIED, grep develop):**
  - `lightning_*`, `adc_voltage_ch*` and `one_wire_temperature_ch*` are written in AS3935Sensor.cpp, ADS1X15Sensor.cpp, DS248XSensor.cpp, CommandSensorFields.cpp and EnvironmentTelemetry.cpp.
  - `soil_water_metrics` appears only in a comment in RAKSensorHubUplink.cpp: "RAK_SENSORHUB_EXTENDED_ENV_METRICS is on (retarget to soil_water_metrics later)".
  - The #11817 commit body: "soil/water chemistry stays gated until SoilWaterMetrics lands in firmware."
  - [commit 4964a0c](https://github.com/meshtastic/firmware/commit/4964a0c6a)
- **Linux/portduino:**
  - The `I2C: I2CDevice:` yaml key exists in PortduinoGlue.cpp (VERIFIED).
  - New on develop 2026-11-13 (after v2.8.2): "portduino: read environment telemetry from shell commands (Sensors:) (#12276)". A `config.yaml` `Sensors:` list holds entries of `Type: <EnvironmentMetrics field name>`, `Command:` and `Scale:`.
    - Readings are cached for 30 s, and command sensors get a 2 s exec timeout.
    - An unknown Type stops meshtasticd, and `--check` validates the section.
    - The section does NOT enable telemetry itself "because enabling it also broadcasts to the mesh".
  - "portduino Sensors: add air quality types (#12305)" 2026-11-13 extends this to AirQualityMetrics fields, except pm_status_flags.
  - [#12276](https://github.com/meshtastic/firmware/pull/12276); [#12305](https://github.com/meshtastic/firmware/pull/12305)
- **Noise floor:** "Noise floor (#9347)" 2026-05-28 added noise sampling (in 2.8). Then "fix(telemetry): restore the noise floor feeder and stop shipping its default (#11749)" 2026-09-06, after v2.8.0, stopped broadcasting the −120 dBm placeholder. `LocalStats.noise_floor` has no presence bit, so the field is now left at 0 when there are no samples. — [#11749](https://github.com/meshtastic/firmware/pull/11749)
- "Fail the build when Telemetry no longer fits the packet payload (#11810)" 2026-09-10 — [#11810](https://github.com/meshtastic/firmware/pull/11810)

### Inferences
- BELIEVED: in 2.8.0, an upgraded `noise_floor` value can be the −120 sentinel. From 2.8.1 on it is 0 meaning "no samples". A MOC should treat `noise_floor == 0` and `== -120` (on 2.8.0) as unobservable, not as measured.
- BELIEVED: `Sensors:` (CommandSensor) is develop-only as of 2026-11-14, so it would first ship in 2.8.3 or later.

### Gaps
- I did not read each driver's I²C address table; per-sensor addresses are not listed here.

## Q3 — Behavioural changes affecting telemetry consumers

### Takeaway
- **Opt-in migration:** an upgrade to any 2.8.x runs a one-time migration that turns OFF every mesh-broadcast telemetry flag. Telemetry still flows to the locally connected client (TCP 4403/serial/BLE).
- **Region throttle:** telemetry/position intervals are multiplied by a per-region throttle: 2× on the ham 2 m profile, 10× on EU "Lite".
- **MQTT JSON:** removed on all builds. meshtasticd keeps JSON only for trace/JSON-file logging.
- **Signing:** XEdDSA signing became opt-in (Balanced/Strict) after v2.8.2.

### Cited Findings
- **Opt-in migration (VERIFIED, NodeDB.cpp on develop; the call is present in v2.8.0, v2.8.1 and v2.8.2):**
  - `optInDisableTelemetryBroadcast()` sets `device_telemetry_enabled`, `environment_measurement_enabled`, `air_quality_enabled`, `power_measurement_enabled` and `health_measurement_enabled` to false. It also sets `mqtt.map_report_settings.should_report_location = false` and leaves map_reporting_enabled alone.
  - It runs once, gated by `moduleConfig.version < POSITION_TELEMETRY_OPTIN_VER`. The comment says it "does NOT re-clobber a user who later re-enables sharing".
  - Position precision is set to 0 only on public/default-PSK channels.
  - [NodeDB.cpp @ develop](https://github.com/meshtastic/firmware/blob/develop/src/mesh/NodeDB.cpp)
- **Mesh vs phone (VERIFIED, DeviceTelemetry.cpp develop):** a mesh send requires `device_telemetry_enabled`. Otherwise it does `sendTelemetry(NODENUM_BROADCAST, true)` to the phone only, plus LocalStats to the phone. The v2.7.26 gate is the same. — [DeviceTelemetry.cpp](https://github.com/meshtastic/firmware/blob/develop/src/modules/Telemetry/DeviceTelemetry.cpp)
- **Intervals (VERIFIED, Default.h develop):**
  - `default_telemetry_broadcast_interval_secs = IF_ROUTER(12h, 1h)`
  - `min_default_telemetry_interval_secs = IF_ROUTER(12h, 30 min)`
  - `MAX_INTERVAL` clamps device/environment intervals
  - Intervals are scaled with node count above 40 online nodes (pre-existing behaviour)
  - [Default.h](https://github.com/meshtastic/firmware/blob/develop/src/mesh/Default.h)
- **Region throttle (VERIFIED, new in 2.8; present at v2.8.0 and v2.8.1):** `Default::getConfiguredOrDefaultMsScaled(…, TrafficType)` multiplies by `myRegion->profile->telemetryThrottle`, where ≤1 is neutral. Profiles in RadioInterface.cpp, in the order "presets, spacing, padding, audio, licensed, text throttle, position throttle, telemetry throttle":
  - `PROFILE_HAM_20KHZ … true, 0, 2, 2` is used by ITU1_2M/ITU2_2M/ITU3_2M, so position and telemetry are ×2.
  - `PROFILE_HAM_100KHZ … 1, 1` is used by ITU2_125CM and ITU1/2/3_70CM, so there is no throttle.
  - `PROFILE_LITE … 10, 10` gives ×10.
  - STD/EU868/NARROW give ×1.
  - [RadioInterface.cpp](https://github.com/meshtastic/firmware/blob/develop/src/mesh/RadioInterface.cpp); [Default.cpp](https://github.com/meshtastic/firmware/blob/develop/src/mesh/Default.cpp)
- **Throttle coverage (VERIFIED, grep develop):** Device, Environment, Health and Power telemetry pass `TrafficType::TELEMETRY`. AirQualityTelemetry.cpp calls the 3-argument overload without TrafficType, so AQ intervals are NOT region-throttled. — [AirQualityTelemetry.cpp](https://github.com/meshtastic/firmware/blob/develop/src/modules/Telemetry/AirQualityTelemetry.cpp)
- **Traffic Management (VERIFIED, TrafficManagementModule.cpp develop):** per-node rate limiting applies to every portnum except ROUTING_APP and ADMIN_APP, so TELEMETRY_APP from a chatty node is dropped and not relayed when over `rate_limit_max_packets` per `rate_limit_window_secs`. The drop is counted in `TrafficManagementStats.rate_limit_drops`. The `exhaust_hop_telemetry` toggle was removed from the proto (now reserved). — [TrafficManagementModule.cpp](https://github.com/meshtastic/firmware/blob/develop/src/modules/TrafficManagementModule.cpp)
- **MQTT JSON (VERIFIED):**
  - "Remove fragile JSON libraries from the firmware while retaining Meshtasticd JSON support (#10152)" 2026-06-03 deletes JSON from MQTT.cpp (−166 lines) and removes `MeshPacketSerializer_nRF52.cpp`.
  - The count of `json` in `src/mqtt/MQTT.cpp` is 80 at v2.7.26 and 0 at v2.8.0, v2.8.2 and develop.
  - On develop, `MeshPacketSerializer` is `#if ARCH_PORTDUINO` and is called only from Router.cpp for LOG_TRACE and the `JSONFilename` file output.
  - The proto marks `json_enabled` deprecated since 2.8.0 and "ignored".
  - [#10152](https://github.com/meshtastic/firmware/pull/10152)
- **The JSON serializer does not cover the new fields (VERIFIED, MeshPacketSerializer.cpp develop):**
  - The telemetry branch handles device/environment/air_quality/power only.
  - Environment keys stop at the pre-2.8 set (…rainfall_1h/24h, soil_moisture/temperature) plus `voltage/current_ch1..3`.
  - It does not handle lightning, adc_voltage_ch*, one_wire_temperature_ch*, pm_status_flags or soil_water_metrics.
  - [MeshPacketSerializer.cpp](https://github.com/meshtastic/firmware/blob/develop/src/serialization/MeshPacketSerializer.cpp)
- **Signed telemetry (VERIFIED, commit body):** "Rework XEdDSA signing policy… (#12191)" 2026-10-22 is after v2.8.1 and before v2.8.2:
  - Compatible, the default, no longer signs. Balanced and Strict sign broadcasts. Licensed (ham) nodes also sign unicasts, but only under Balanced/Strict.
  - Signing costs 66 bytes per packet.
  - Packets on an AES-CCM channel are never signed.
  - Only Strict drops unsigned packets.
  - [#12191](https://github.com/meshtastic/firmware/pull/12191)
  - The protobuf note: "Document the revised packet signature policy and AES-CCM override (#1141)" 2026-11-01. — [protobufs #1141](https://github.com/meshtastic/protobufs/pull/1141)
- **Other:** "Correct for awake time in AQ telemetry (#11404)"; "fix(telemetry): stop emitting -0.001V sentinel when battery unavailable (#10217)" 2026-05-22; "Fix SENSOR power saving deep sleep truncating TX… (#10939)". — [#10217](https://github.com/meshtastic/firmware/pull/10217)

### Inferences
- **Migration timing (BELIEVED):** I believe the #12191 commit is in v2.8.2 from its date, but I did not run `git merge-base --is-ancestor`. The v2.8.2 tag (2026-11-01/03) postdates the commit (2026-10-22), but 2.8.2 may have been cut from an `origin/2.8.2` branch.
- **Upgraded fleets (BELIEVED):** after an upgrade from 2.7.x, a fleet of 2.8 nodes will go quiet on mesh telemetry (device, environment, AQ, power, health) unless each node is re-enabled. A MOC fed over MQTT/mesh will see telemetry stop. That is a config change, not node failure, so the MOC should not report "dark". TCP 4403/serial to the local node still delivers phone-path telemetry.
- **MQTT consumers (BELIEVED):** anything that relied on `msh/.../json/...` topics gets nothing from any 2.8 node. Consumers must decode protobuf ServiceEnvelopes.
- **2 m stations (BELIEVED):** expect the effective default interval to double to 2 h, and the 30 min floor to become 60 min. Remote-node telemetry age thresholds should scale by region.

### Gaps
- The PR number for the 2.8 opt-in flip: the task's hint "#10929" is unconfirmed. The `--grep=opt-in` search of the commit log did not surface it, and `-S` pickaxe over the blobless clone timed out. UNKNOWN.
- The new-install defaults are all-false through memset in both versions, BELIEVED from reading `installDefaultModuleConfig`. Whether clients/userPrefs turned them on in 2.7 is UNKNOWN.
- Whether TCP 4403 (portduino) delivers telemetry identically to BLE/serial was not traced (BELIEVED same PhoneAPI path).

## Q4 — Ham-specific telemetry

### Takeaway
I found no APRS-style weather or telemetry, SWR, or station-power feature in 2.8 or develop. The ham-relevant changes are the 2 m region ×2 telemetry/position throttle, licensed-mode signing rules, and key-verification refusal in ham mode. AS3935 lightning and the ADS1X15 8-channel ADC are the closest to "station telemetry" (an ADC channel could carry forward/reflected voltage, but nothing implements SWR). (VERIFIED absence by grep; see gaps)

### Cited Findings
- `git log --grep='aprs\|swr'` over the merge-base..develop range returns nothing, and `grep -rli aprs src` returns nothing (VERIFIED, develop 2026-11-14). — [firmware develop](https://github.com/meshtastic/firmware/tree/develop)
- The ham 2 m throttle ×2 and the 70 cm/1.25 m ×1 values are listed in Q3. — [RadioInterface.cpp](https://github.com/meshtastic/firmware/blob/develop/src/mesh/RadioInterface.cpp)
- Other ham-mode changes:
  - "Key verification: refuse in licensed (ham) mode (#12201)" 2026-10-19
  - "feat(admin): append the optional ham long_name to the call sign (#11612)" 2026-08-26
  - "Licensed operation removed persisted channel encryption/admin access" (`channels.ensureLicensedOperation()` in loadFromDisk)
  - [#12201](https://github.com/meshtastic/firmware/pull/12201); [#11612](https://github.com/meshtastic/firmware/pull/11612)
- RemoteHardwareModule had only maintenance commits since the merge-base ("Null-check reply allocations… RemoteHardware (#11260)", log tidy). No GPIO-status feature changes. — [#11260](https://github.com/meshtastic/firmware/pull/11260)
- The SerialModule `MODBUS = 11` mode (RS-485 sensor → telemetry) is in the proto. The firmware implementation is the open PR "Modbus-RTU serial module mode and seeed-m2100 variant" [#12064](https://github.com/meshtastic/firmware/pull/12064) (opened 2026-10-03, still open 2026-11-14).

### Inferences
- BELIEVED: since ham mode strips encryption, telemetry on licensed nodes is cleartext on air by design. With Compatible signing it is also unsigned. Integrity then depends on choosing Balanced/Strict.

### Gaps
- The meshtastic.org docs pages for licensed mode were not fetched, so there may be documented-but-unimplemented plans. UNKNOWN.

## Q5 — Upcoming (develop after 2026-11-04 and open PRs)

### Takeaway
On develop since Nov 4: Linux `Sensors:` command telemetry (#12276, #12305) and NodeDB environment-satellite storage changes. Open PRs include a shared telemetry scheduler, SoilWaterMetrics for RAKSensorHub, Modbus-RTU, INA228, a radar level sensor, and several "drop failed read instead of reporting it" fixes. There were 53 open PRs matching "sensor OR telemetry" (REST search, 2026-11-14).

### Cited Findings
- Merged on develop after 2026-11-04 (VERIFIED, git log):
  - #12276 and #12305 (2026-11-13)
  - "NodeDB: hold environment satellites protobuf-encoded (~10 KB on nRF52) (#12295)" 2026-11-04
  - "fix(nrf54l15dk): link the INA3221 and SHTxx drivers (#12050)" 2026-11-13
  - [#12295](https://github.com/meshtastic/firmware/pull/12295)
- Open PRs (VERIFIED from the GitHub search API, created dates; total_count 53):
  - [#12365](https://github.com/meshtastic/firmware/pull/12365) "Telemetry: create EnvironmentTelemetryModule when ENVIRONMENTAL_TELEMETRY_MODULE_ENABLE is set" (2026-11-14)
  - [#12364](https://github.com/meshtastic/firmware/pull/12364) "Telemetry: add HLK-LD8001H UART radar level sensor" (2026-11-14)
  - [#12370](https://github.com/meshtastic/firmware/pull/12370) "fix(telemetry): omit RadSens radiation on failed I2C read" (2026-11-14)
  - [#12342](https://github.com/meshtastic/firmware/pull/12342) "Fix INA219 detection and channel-1 power telemetry" (2026-11-12)
  - [#12327](https://github.com/meshtastic/firmware/pull/12327) "Telemetry: shared scheduler with per-module read intervals" (2026-11-08)
  - [#12328](https://github.com/meshtastic/firmware/pull/12328) "fix(telemetry): wake SHTC3 before SHTXX init" (milestone 2.8.3)
  - [#12279](https://github.com/meshtastic/firmware/pull/12279) "RAKSensorHub: hardware UART link, multi-probe provisioning and SoilWaterMetrics" (2026-11-02)
  - [#12297](https://github.com/meshtastic/firmware/pull/12297) "NodeDB: bound the satellite maps by an aggregate byte budget" (2026-11-03)
  - [#12244](https://github.com/meshtastic/firmware/pull/12244) "fix(telemetry): drop failed BME280 reads instead of reporting them" (2026-10-27)
  - [#12236](https://github.com/meshtastic/firmware/pull/12236) "Send device telemetry early on power-saving SENSOR nodes" (milestone 2.8.3)
  - [#12231](https://github.com/meshtastic/firmware/pull/12231) MESHTASTIC_EXCLUDE_I2C build fix (milestone 2.8.3)
  - [#12123](https://github.com/meshtastic/firmware/pull/12123) "feat(telemetry): support the TI INA228 power monitor" (2026-10-10)
  - [#12064](https://github.com/meshtastic/firmware/pull/12064) Modbus-RTU serial mode (2026-10-03)
  - [#12017](https://github.com/meshtastic/firmware/pull/12017) detection-sensor dwell filter / clear report
  - [#11764](https://github.com/meshtastic/firmware/pull/11764) IAQ alert logic
  - [#11564](https://github.com/meshtastic/firmware/pull/11564) "AirQualityTelemetry: decouple sensor reads from publishing…"
  - [#10556](https://github.com/meshtastic/firmware/pull/10556) MPU-9250/RAK1905
  - [#9903](https://github.com/meshtastic/firmware/pull/9903) INA219/INA226 as primary battery sensor
  - [#11952](https://github.com/meshtastic/firmware/pull/11952) T-Deck Pro touch misdetected as DS2482
- protobufs after v2.8.2: "Add 'sensitive' Metadata field and tag passwords/keys (#1125)" 2026-11-09 (metadata only, no telemetry wire change). — [protobufs #1125](https://github.com/meshtastic/protobufs/pull/1125)

### Inferences
- BELIEVED: several open PRs (#12244, #12370, #12342) fix failed reads that were reported as values. Until they land, 2.8.x BME280/RadSens/INA219 readings may include bogus values on read failure, so a MOC should range-check them.
- BELIEVED: the first SoilWaterMetrics producer is #12279. Until it merges, `soil_water_metrics` will not appear from stock firmware.

### Gaps
- I only saw PR titles and created dates. I did not read PR bodies, review state, or merge likelihood, and I did not page past the first 40 search results of 53.
