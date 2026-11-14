# MeshForge sensor-telemetry handling and Meshtastic 2.8 readiness (code audit)

Audit date 2026-11-14, HEAD `7abbb8f`. Method: I read the code statically and grepped it. I did not run the test suite and observed no live box, so every claim about runtime behaviour is BELIEVED (reasoned from code). Two upstream facts were fetched this session: the firmware MQTT JSON serializer and the telemetry.proto files, at master and at tag v2.7.26. Sources are file:line paths under `/home/user/meshforge`.

## Ingestion: where TELEMETRY_APP (portnum 67) is decoded, which variants and fields, and what is dropped

### Takeaway
Four ingest legs decode telemetry, and each one keeps only part of it. Device, environment, air-quality and health data are partly handled. Power, local_stats, host_metrics, traffic_management_stats and soil_water_metrics are dropped on every leg. The most serious finding is in the MQTT JSON leg. It reads nested keys (`payload.device_metrics.*`), but firmware publishes telemetry JSON as FLAT keys (`payload.battery_level`, `payload.temperature`). So on real firmware JSON that leg probably records no telemetry at all, and the MQTT-fed consumers downstream (Kilo, Prometheus, InfluxDB, the TUI MQTT node view, low-battery alerts) would be starved of it. This is BELIEVED, not observed live.

### Cited Findings
- **The MQTT JSON leg dispatches `type=="telemetry"` to `_handle_telemetry`.** — [src/monitoring/_mqtt_message_decoder.py:124-125](src/monitoring/_mqtt_message_decoder.py)
- **`_handle_telemetry` reads nested dicts:**
  - `payload.get("device_metrics")`: battery_level, voltage, channel_utilization, air_util_tx (uptime is ignored).
  - `environment_metrics`: temperature, relative_humidity, barometric_pressure, gas_resistance.
  - `air_quality_metrics`: pm25_standard, pm25_environmental, pm10_standard, pm10_environmental, co2, iaq.
  - `health_metrics`: heart_bpm, spO2, temperature.
  - Values are range-clamped through `_safe_int` / `_safe_float`.
  - [src/monitoring/_mqtt_message_decoder.py:493-592](src/monitoring/_mqtt_message_decoder.py)
- **Firmware serializes telemetry JSON flat.** Every key goes directly into `msgPayload`, and `jsonObj["payload"] = msgPayload`. There is no `device_metrics` wrapper.
  - Device keys: battery_level, voltage, channel_utilization, air_util_tx, uptime_seconds.
  - Environment keys: temperature … soil_temperature, 22 keys.
  - Air-quality keys: `pm10`, `pm25`, `pm100`, co2, co2_temperature, co2_humidity, form_*. These are not `pm25_standard`.
  - Power keys: voltage_ch1..ch3, current_ch1..ch3.
  - local_stats, health_metrics and host_metrics write NOTHING, so the payload is `{}`.
  - [firmware MeshPacketSerializer.cpp (develop)](https://raw.githubusercontent.com/meshtastic/firmware/develop/src/serialization/MeshPacketSerializer.cpp)
- **The test fixtures enshrine the nested shape** (`"payload": {"device_metrics": {"battery_level": 85}}`), so the suite cannot catch the mismatch. — [tests/test_mqtt_robustness.py:276-282, 399-447](tests/test_mqtt_robustness.py)
- **The same module's position handler reads flat keys** (`payload.latitude_i`), which shows the authors know the JSON payload is flat for position. — [src/gateway/mqtt_bridge_handler.py:1022-1024](src/gateway/mqtt_bridge_handler.py)
- **The gateway bridge's MQTT telemetry handler also reads nested `device_metrics` and `environment_metrics`.** It only logs at debug level and stores nothing. — [src/gateway/mqtt_bridge_handler.py:1003-1015](src/gateway/mqtt_bridge_handler.py)
- **The encrypted protobuf (`/e/`) MQTT leg decrypts packets but processes only text.** It checks `if result and result.get("text")`, so decrypted TELEMETRY_APP packets are dropped. — [src/monitoring/_mqtt_message_decoder.py:129-148](src/monitoring/_mqtt_message_decoder.py)
- **The TCP / meshtastic-python live leg is `MessageListener._on_receive`.** It handles TEXT, TELEMETRY and POSITION and ignores everything else. — [src/utils/message_listener.py:438-455](src/utils/message_listener.py)
- **What `_handle_telemetry` keeps on the TCP leg:**
  - environmentMetrics: temperature, relativeHumidity, barometricPressure.
  - airQualityMetrics: pm25Standard, pm10Standard, co2, iaq.
  - deviceMetrics: batteryLevel, voltage, channelUtilization, airUtilTx.
  - Dropped: powerMetrics, localStats, healthMetrics, hostMetrics, trafficManagementStats, and every other environment field (lux, wind_*, rain, soil, IAQ-in-env, gas_resistance, radiation, distance, weight).
  - Exceptions are swallowed at debug level.
  - [src/utils/message_listener.py:539-628](src/utils/message_listener.py)
- **TCP-leg bugs:**
  - **Readings of 0.0 are dropped as "no reading".** The guard is `if temp or humidity or pressure:`, so a 0.0 °C reading is falsy. — [message_listener.py:551](src/utils/message_listener.py)
  - **Pressure is written to the wrong attribute.** The code writes `node.telemetry.barometric_pressure`, but the dataclass field is `pressure` ([node_models.py:352](src/gateway/node_models.py)). The value lands on an ad-hoc attribute that `to_dict()` never serializes, so pressure is lost from cache and GeoJSON. — [message_listener.py:566](src/utils/message_listener.py)
  - **Air quality is dropped unless a telemetry object already exists** (`if node and node.telemetry:`), and the guard `if pm25 or pm10 or co2` is falsy at zero. — [message_listener.py:577, 587](src/utils/message_listener.py)
  - **Device metrics are skipped when battery is absent** (`if battery is not None`), even if voltage and chUtil are present. — [message_listener.py:604](src/utils/message_listener.py)
  - **Telemetry from unknown nodes is dropped** (`if node:`), because `tracker.get_node(from_id)` must already exist. — [message_listener.py:560, 612](src/utils/message_listener.py)
  - **No leg sets `telemetry.timestamp`.** A grep for `telemetry.timestamp =` in src returns nothing. — (grep, this session)
- **NodeDB snapshot legs** read the meshtastic-python `interface.nodes` dict:
  - `UnifiedNode.from_meshtastic` reads deviceMetrics batteryLevel, voltage and uptimeSeconds only, stamped `timestamp=datetime.now()`. — [src/gateway/node_models.py:875-882](src/gateway/node_models.py)
  - `node_monitor._parse_node_data` reads deviceMetrics and environmentMetrics (temperature, relativeHumidity, barometricPressure). — [src/monitoring/node_monitor.py:379, 460-465](src/monitoring/node_monitor.py)
  - The map collector reads deviceMetrics batteryLevel, voltage, chUtil and airUtilTx, plus env temperature, humidity and pressure. — [src/utils/_map_collector_meshtastic.py:549-579](src/utils/_map_collector_meshtastic.py)
- **The packet dissector (traffic inspector) handles portnum 67 `deviceMetrics` only** (batteryLevel, voltage, channelUtilization). Every other variant shows no fields. — [src/monitoring/packet_dissectors.py:258-269](src/monitoring/packet_dissectors.py)
- **The canonical message maps TELEMETRY_APP/67 to `MessageType.TELEMETRY`**, without decoding the payload. — [src/gateway/canonical_message.py:647, 658](src/gateway/canonical_message.py)
- **The health-sensor counter increments once per PACKET, not per unique node**, despite its name. The comment admits "simple count for now". — [src/monitoring/_mqtt_message_decoder.py:586-592](src/monitoring/_mqtt_message_decoder.py)
- **The Telemetry data model** covers device metrics, environment (temperature, humidity, pressure, gas_resistance), AirQualityMetrics, HealthMetrics and DetectionSensor. It has no PowerMetrics, LocalStats, HostMetrics or extended-environment fields. — [src/gateway/node_models.py:210-280, 329-435](src/gateway/node_models.py)

### Inferences
- **The upstream telemetry ladder is not represented at all past the four partially-handled variants.** No source file mentions power, local_stats or host_metrics ingestion. The only hits are `HostMetrics` as a meshtasticd YAML config key and in the diag24h parser's name map ([src/moc_analysis_tool/collectors/diag24h_parser.py:111-114](src/moc_analysis_tool/collectors/diag24h_parser.py)).
- **If the flat-JSON finding holds, every MQTT telemetry field on MQTTNode stays `None` for real firmware traffic.** Only snr, rssi, hops and position (via the separate flat handlers) would populate. That fits the roadmap's open "telemetry from real devices" owed check (see Gaps under the last question).
- **The TCP leg is the only path that would populate the tracker's Telemetry**, and it does so without a timestamp.

### Gaps
- **The flat-JSON breakage is not observed live.** It would be confirmed by `mosquitto_sub -t 'msh/+/2/json/#' -C 5` on a box with JSON uplink, or by `SELECT metric,count(*) FROM readings WHERE transport='mqtt' GROUP BY metric` on `kilo_telemetry.db`. Zero env rows would confirm it. UNKNOWN until one of those runs.
- **The exact meshtastic-python node-dict keys populated for powerMetrics and localStats were not checked.** The installed library could not be inspected because a Bash `python3 -c` call was permission-denied.

## Storage: SQLite telemetry history, retention and schemas

### Takeaway
There is no general per-node sensor history table. Persisted telemetry comes down to three stores:
- `node_history.node_observations` keeps battery and voltage only, and only for positioned nodes, for 48 hours.
- Kilo's `readings` table is a generic `(ts, transport, node_key, metric, value)` time-series kept for 30 days. It covers 12 metrics, and only for registered lab nodes, collected in bounded windows.
- `metrics.db` is a generic metric/hourly rollup store.

Environment and air-quality history therefore exists only through Kilo, or through an external InfluxDB.

### Cited Findings
- **`node_observations` columns:** node_id, timestamp, lat, lon (NOT NULL), alt, snr, rssi, battery, voltage, is_online, network, hardware, role, via_mqtt, name. There are no environment columns.
  - The default retention of 48 hours is documented in the docstring.
  - The voltage column was added on 2026-09-05 after it had been dropped at the persistence layer.
  - [src/utils/node_history.py:115, 203-231, 596-708](src/utils/node_history.py)
- **`record_observations()` requires lat/lon**, so position-less sensor nodes never contribute a row. That matters more on 2.8, where position is off by default. — [src/utils/_map_collector_meshtastic.py:567-572](src/utils/_map_collector_meshtastic.py) (comment); [src/utils/node_history.py:596](src/utils/node_history.py)
- **The `kilo_telemetry` DBSpec** has retention_days=30, auto-prune, a `readings` table and an `edges` table kept for 7 days. — [src/utils/db_inventory.py:78-92](src/utils/db_inventory.py); [src/kilo/store.py:61-95](src/kilo/store.py)
- **Kilo's `NODE_METRICS` vocabulary:** temperature, humidity, pressure, gas_resistance, co2, iaq, pm25_standard, pm10_standard, voltage, battery_level, channel_utilization, air_util_tx.
  - It is sourced from MQTTNode attributes, which are the broken nested-JSON leg described above.
  - Its stated contract is that a None metric is never recorded.
  - [src/kilo/ingest.py:22-46, 85-127](src/kilo/ingest.py)
- **Kilo admits it has no per-metric timestamps** ("MQTTNode carries no per-metric timestamps"). It dedups on value change in-window. — [src/kilo/ingest.py:94-110](src/kilo/ingest.py)
- **The `metrics` DBSpec** (`metrics_history`) is generic, with tables `metrics(timestamp, metric_type, value, node_id, edge_id, tags)` and `metrics_hourly`. — [src/utils/db_inventory.py:158-160](src/utils/db_inventory.py); [src/utils/metrics_history.py:215-249](src/utils/metrics_history.py)
- **The tracker cache persists `telemetry.to_dict()` and restores it on load.** — [src/gateway/node_tracker.py:771-777](src/gateway/node_tracker.py)

### Inferences
- Kilo is designed honestly (tri-state status, no fabricated 0). But because it rides MQTTNode, its MQTT leg would record only nothing for env/AQ/battery under flat firmware JSON. In that case only its claw and scout legs carry data. BELIEVED.

### Gaps
- I did not check whether `metrics_history` receives any per-node sensor metric today. I found no caller grep for env metrics into it.

## Display: TUI, maps, GeoJSON, and Prometheus/InfluxDB/Grafana

### Takeaway
Sensors are displayed in five places: the TUI MQTT node detail, MQTT GeoJSON and the NOC web map popup, the folium coverage-map popup (battery only), Prometheus gauges and InfluxDB points. No surface shows the age of a reading. Several TUI checks use truthiness, so real zeros are hidden. The web map handles null correctly.

### Cited Findings
- **TUI MQTT node details show** battery, voltage, environment readings, PM2.5/CO2/IAQ and heart/SpO2/body temperature.
  - The gates are truthy: `has_device = node.battery_level or node.voltage`, `has_env = node.temperature or …`, `if node.voltage:`.
  - As a result, a 0 °C reading, or voltage 0.0 from a USB-powered node (a real, common value per [node_history.py:66-70](src/utils/node_history.py)), is hidden.
  - [src/launcher_tui/handlers/mqtt.py:622-703](src/launcher_tui/handlers/mqtt.py)
- **MQTT GeoJSON properties** include battery, channel_utilization, air_util_tx, is_congested, temperature, humidity, pressure, pm25, co2 and iaq. They do not include voltage, gas_resistance, health metrics, or any per-reading timestamp. — [src/monitoring/mqtt_subscriber.py:846-903](src/monitoring/mqtt_subscriber.py)
- **The tracker GeoJSON carries battery only.** — [src/gateway/node_tracker.py:950-962](src/gateway/node_tracker.py)
- **The NOC web map popup renders temperature and humidity** with explicit null and undefined checks, so zero shows correctly. — [web/node_map.html:2957-2966](web/node_map.html)
- **The folium coverage-map popup shows Battery only.** — [src/utils/coverage_map.py:979-1022](src/utils/coverage_map.py)
- **The Prometheus exporter emits temperature, humidity, pressure, gas, air-quality and health gauges** from the MQTT subscriber (`is not None` checks, good).
  - `_cap_recent` caps label cardinality and logs the cap (citing rule #9).
  - [src/utils/prometheus_exporter.py:109-123, 644-700](src/utils/prometheus_exporter.py)
- **The InfluxDB exporter writes** `meshforge_environment` (temperature, humidity, pressure, gas) and air-quality points per node. — [src/utils/influxdb_exporter.py:419-445](src/utils/influxdb_exporter.py)
- **The changelog claims a "Full telemetry pipeline — sensor data through Prometheus, InfluxDB, Grafana".** — [src/__version__.py:212](src/__version__.py)

### Inferences
- **Exporters violate honest_failure_modes #2 and #1.** MQTTNode keeps the last value forever with no reading timestamp, so a sensor that died hours ago still exports a fresh-looking gauge every scrape. Filtering is by node `last_seen`, which any packet type updates, not by reading age.
- **The "Full telemetry pipeline" changelog claim is unverified** against real firmware JSON, given the nested-key finding.

### Gaps
- I did not check whether Grafana dashboards ship in-repo (not grepped).
- I did not exhaustively check `topology.py`, `traffic_pulse.py` and the other handler hits for sensor display.

## Management: telemetry module configuration

### Takeaway
You cannot currently set telemetry module config (intervals, environment_measurement_enabled, sensor types) from the TUI. What exists:
- an admin `set_module_config('telemetry', …)` primitive with no UI caller;
- a legacy Rich CLI form that only builds a dict;
- a meshtasticd `HostMetrics` YAML editor;
- an on-air telemetry-request poller, off by default.

### Cited Findings
- **`ProtobufAdmin` can read and set module config.** Module type 5 is telemetry, and get/set go through a begin/commit transaction. — [src/gateway/_protobuf_admin.py:214-258, 305-330](src/gateway/_protobuf_admin.py)
- **No TUI, utils or commands caller of `set_module_config` was found.** — (grep of src/launcher_tui, src/utils, src/commands this session)
- **The `configure_telemetry()` form** prompts for device_update_interval, environment_measurement_enabled, environment_update_interval, fahrenheit and air_quality_enabled, and stores the answers into `self.module_config`.
  - No apply or device-write step exists.
  - No external caller of the module was found.
  - [src/config/modules.py:277-314](src/config/modules.py)
- **The meshtasticd templates and editor cover the `HostMetrics` section** (ReportInterval, Channel, UserStringCommand) and the `I2C: I2CDevice: /dev/i2c-1` setting. — [src/core/meshtasticd_config.py:171-188](src/core/meshtasticd_config.py); [src/config/yaml_editor.py:757-796](src/config/yaml_editor.py); [src/core/meshtasticd_templates.py:37, 178-179](src/core/meshtasticd_templates.py)
- **`TelemetryPoller` sends `meshtastic --host H --request-telemetry --dest !id`** as a subprocess, rate-limited to 4 per minute and 5 per cycle, and gated by `tx_guard.assert_cli_args_allowed`.
  - The daemon registers it only if `telemetry_enabled` is set, which defaults to False.
  - The TUI MQTT handler can also auto-start it and offers single and batch requests.
  - [src/utils/telemetry_poller.py:1-20, 41-44, 333-428](src/utils/telemetry_poller.py); [src/daemon_config.py:51-52](src/daemon_config.py); [src/daemon.py:435-470, 1001-1005](src/daemon.py); [src/launcher_tui/handlers/mqtt.py:217-225, 907-1150](src/launcher_tui/handlers/mqtt.py)
- **The poller's target selection treats a node with `telemetry_timestamp is None` as "No telemetry ever — definitely should poll".** — [src/utils/telemetry_poller.py:204-206](src/utils/telemetry_poller.py)
- **There is also a CLI wrapper, `request_telemetry(dest)`.** — [src/commands/meshtastic.py:866-868](src/commands/meshtastic.py)

### Inferences
- **The poller would re-poll the same nodes every cycle.** The TCP leg never sets `telemetry.timestamp`, and `from_meshtastic` stamps `now()` only when a deviceMetrics block exists. So nodes without a NodeDB deviceMetrics block look "never" and get polled every cycle, regardless of whether replies arrive. That costs airtime and grows on 2.8, where telemetry is off by default. BELIEVED.
- **The poller's subprocess opens a separate TCP client to meshtasticd.** CLAUDE.md's Issue #29 rule says never to create a TCPInterface directly. The CLI subprocess sidesteps `MESHTASTIC_CONNECTION_LOCK`. This is a possible regression class, not verified.

### Gaps
- I did not check whether an in-app web config API (`config_api`, port 8081) exposes module config.

## Alerting and diagnostics: thresholds, stale-sensor detection, honest-failure handling

### Takeaway
The alert engine has a low-battery threshold (20%) and an SNR threshold, and a "disconnect" alert after 30 minutes with no packet. Nothing detects a stale sensor, and nothing alerts on environment readings (temperature, voltage, AQ). Several paths map degraded or absent states onto healthy-looking or wrong values.

### Cited Findings
- **Default alert settings:** battery_threshold 20, snr_threshold -10, disconnect_timeout_minutes 30. Battery alerts are severity 3 below 10%. — [src/utils/mesh_alert_engine.py:39-46, 132-152](src/utils/mesh_alert_engine.py)
- **The alert engine is fed only by MQTT subscriber node updates.**
  - It records `_known_nodes[node_id] = time.time()` on any packet.
  - A disconnect alert fires when a node is not heard for 30 minutes.
  - Nodes are never removed after alerting, so the alert repeats after each cooldown.
  - [src/utils/mesh_alert_engine.py:262-293](src/utils/mesh_alert_engine.py)
- **Low-battery alerts read MQTTNode.battery_level**, which the nested-JSON finding implies never populates from real JSON. — [src/utils/mesh_alert_engine.py:136](src/utils/mesh_alert_engine.py)
- **The tracker merge keeps the old telemetry unless the new object has a timestamp**, and the TCP leg never sets one. — [src/gateway/node_tracker.py:481-483](src/gateway/node_tracker.py)
- **`has_environment_sensors()` and `AirQualityMetrics.has_data()` use `any([...])` truthiness.** A 0 °C / 0 ppm node reads as having no sensor. — [src/gateway/node_models.py:252-254, 415-417](src/gateway/node_models.py)
- **Positive pattern:** `_clean_voltage` treats voltage=0.0 as real and USB-powered nodes as `battery_level=101`, documented as honest handling. — [src/utils/node_history.py:57-75](src/utils/node_history.py)
- **Positive pattern:** the decoder uses range-clamped `_safe_*` checks and rejects out-of-range values as None rather than 0. — [src/monitoring/_mqtt_message_decoder.py:357-380](src/monitoring/_mqtt_message_decoder.py)

### Inferences — violations of honest_failure_modes (`.claude/rules/honest_failure_modes.md`)
- **#1 (degraded value overlaps the healthy domain):**
  - Truthy guards turn a 0.0 reading into "no sensor / not shown" (message_listener.py:551, 577; mqtt.py:661, 680, 692; node_models.py:252, 415).
  - `from_meshtastic` stamps NodeDB metrics with `now()` instead of the reading's time (node_models.py:881).
- **#2 (absence ≠ cleared):**
  - Last-known sensor values persist and export indefinitely with no age, so a dead sensor reads as live (Prometheus/InfluxDB, GeoJSON).
  - Conversely, "disconnect" is asserted from traffic silence. On 2.8 silence is the default (see the last question).
- **#5 (two consumers, one constant):** the MQTT decoder and the test fixtures agree with each other on a nested JSON schema that disagrees with the producer, the firmware serializer. No shared schema pin with upstream exists.
- **#9 (every swallow gets a witness):**
  - `except Exception: pass` drops decrypted telemetry and other non-text packets (_mqtt_message_decoder.py:147-148).
  - The TCP telemetry handler swallows exceptions at debug level (message_listener.py:627-628).
  - Unknown variants are dropped without any counter.
- **#7 (closed enums need closed consumers):** the variant if-chains have no "unknown variant" counter, so new 2.8 variants vanish silently.

### Gaps
- There is no runtime evidence for any of the above. Each could be confirmed by a regression test, for example feeding a flat firmware JSON fixture into `_handle_json_message` and asserting `battery_level`.

## Other protocols: MeshCore, RNS/LXMF/Sideband sensor data

### Takeaway
MeshCore sensor data is not ingested. Only the contact type "sensor" (4) is named, and there is no CayenneLPP or telemetry request. RNS has a partial Sideband telemetry parser that extracts lat/lon/alt/speed/battery from msgpack announce app_data and stashes the rest raw. Kilo additionally ingests the "claw" (ESP32 wireclaw) and "scout" tick files.

### Cited Findings
- **MeshCore** contact types are `{1: companion, 2: repeater, 3: room, 4: sensor}`. A grep found no telemetry, LPP or CayenneLPP handling in `meshcore_handler.py`. — [src/gateway/meshcore_handler.py:1050](src/gateway/meshcore_handler.py)
- **The MeshCore node constructor keeps battery only** (`Telemetry(battery_level=..., timestamp=now())`). — [src/gateway/node_models.py:1077-1082](src/gateway/node_models.py)
- **RNS / LXMF:** `LXMFParser._parse_msgpack_telemetry` extracts latitude/longitude/altitude, speed and battery, and stores the remaining keys in `metadata['raw_telemetry']`. It requires msgpack and logs at debug level when msgpack is missing. — [src/gateway/rns_services.py:133-175, 342-385](src/gateway/rns_services.py)
- **Kilo claw/scout metric legs** read from tick JSON files and record nothing for a null half. — [src/kilo/ingest.py:51-80, 160-330](src/kilo/ingest.py)

### Inferences
- I believe Sideband's real telemetry format (the `Telemeter` packed sensor dict, keyed by sensor IDs, sent in LXMF message fields) differs from the announce app_data shape parsed here. So Sideband environment sensors are probably not captured. BELIEVED, not verified against Sideband source.

### Gaps
- Sideband's telemetry wire format was not fetched this session.
- The MeshCore library's telemetry API was not checked.

## Ham angle: APRS/weather telemetry, station power, amateur hooks

### Takeaway
There is no APRS, APRS-WX or ham weather-telemetry path. `src/amateur/` has callsign, ARES/RACES and Part 97 compliance helpers, and the compliance text mentions telemetry. Ham-relevant station-power data is limited to battery and voltage (no PowerMetrics INA channels). Space weather (NOAA, HamClock) exists, but that is propagation data, not sensor telemetry.

### Cited Findings
- **`src/amateur/` contains** `ares_races.py`, `callsign.py` and `compliance.py`. The compliance summary includes "One-way for beacons, telecommand, telemetry". — [src/amateur/compliance.py:166](src/amateur/compliance.py)
- **APRS appears in src only** as a mention of an "APRS gateway" edition/feature. No APRS code was found. — [src/core/__init__.py:10](src/core/__init__.py)
- **Solar flux and space weather appear in the map visualization.** — [src/utils/_map_visualization.py:280](src/utils/_map_visualization.py)

### Inferences
- The upstream wind, rain, lux and lightning fields would suit an APRS-WX-style station view. MeshForge drops all of them at ingest today.

### Gaps
- None beyond the absence noted.

## 2.8 readiness: default-off telemetry, new fields and variants, signing, TrafficManagement

### Takeaway
MeshForge reads silence as staleness or disconnection, which conflicts with 2.8's opt-in telemetry and position. It is also blind to the new telemetry content. That content splits into two groups:
- **Already in protobufs v2.7.26 and still dropped by MeshForge:** traffic_management_stats, local_stats, host_metrics, power_metrics.
- **Not decodable at all by python 2.7.11:** soil_water_metrics (variant 11), lightning fields, adc_voltage_ch0..7, one_wire_temperature_ch0..7, pm_status_flags, sensor types 54-57.

There is no signature or xeddsa awareness anywhere in src.

### Cited Findings
- **Upstream telemetry.proto master** has these oneof variants: device 2, environment 3, air_quality 4, power 5, local_stats 6, health 7, host 8, traffic_management_stats 9, soil_water_metrics 11.
  - EnvironmentMetrics goes up to field 41 (adc_voltage_ch0-7 at 24-31, one_wire_temperature_ch0-7 at 32-39, lightning_strike_count_1h 40, lightning_distance_km 41).
  - AirQualityMetrics goes up to 26 (pm_status_flags).
  - LocalStats includes noise_floor (15) and num_tx_dropped (14).
  - TrafficManagementStats has packets_inspected, position_dedup_drops, nodeinfo_cache_hits, rate_limit_drops, unknown_packet_drops, hop_exhausted_packets and router_hops_preserved.
  - The highest TelemetrySensorType is 57 (AS3935).
  - [protobufs master telemetry.proto](https://raw.githubusercontent.com/meshtastic/protobufs/master/meshtastic/telemetry.proto)
- **The v2.7.26 telemetry.proto** has variants 2-9, including traffic_management_stats, but no soil_water_metrics.
  - EnvironmentMetrics ends at 23, so there are no adc, lightning or one-wire channel fields.
  - AirQualityMetrics ends at 25.
  - The highest sensor type is 53 (ICM42607P).
  - [protobufs v2.7.26 telemetry.proto](https://raw.githubusercontent.com/meshtastic/protobufs/v2.7.26/meshtastic/telemetry.proto)
- **2.8 makes telemetry and position OFF by default (#10929).** No 2.8 Python library exists: PyPI latest is 2.7.11, and protobufs ≈ v2.7.26. — [sibling notes: firmware_2_8.md:38, 114](.claude/research/meshtastic_2m_vhf_notes/firmware_2_8.md) (citing [v2.8.0 release](https://github.com/meshtastic/firmware/releases/tag/v2.8.0.47db0e3), [PyPI](https://pypi.org/pypi/meshtastic/json))
- **2.8 signing fields** are `MeshPacket.xeddsa_signed` (22) and `Data.xeddsa_signature` (10). — [firmware_2_8.md:91](.claude/research/meshtastic_2m_vhf_notes/firmware_2_8.md)
- **A grep of src for `xeddsa|traffic_management|soil|lightning|wind_speed|lux` found no ingestion code.** — (grep, this session)
- **The poller docstring already anticipates silent nodes** ("2.7.13+ no longer sends telemetry by default"). — [src/utils/telemetry_poller.py:4-6](src/utils/telemetry_poller.py)
- **Online status in MQTTNode uses a 15-minute default threshold** (`is_online(threshold_minutes=15)`). — [src/monitoring/_mqtt_types.py:87](src/monitoring/_mqtt_types.py)

### Inferences
1. **Absence read as dead.** With telemetry and position off, a healthy 2.8 node may emit only NodeInfo (hours apart) plus its own traffic. As a result:
   - the alert engine fires "Node disconnected" after 30 minutes (mesh_alert_engine.py:262-283);
   - MQTT `is_online` flips false after 15 minutes;
   - the telemetry poller burns airtime repeatedly requesting telemetry that the node will not send if its telemetry module is disabled.

   That is honest_failure_modes #2 in the "unobservable ≠ dark" form. A 2.8-aware model needs a tri-state such as "telemetry not offered". BELIEVED.
2. **Position-less sensor nodes never reach `node_observations`.** That table requires lat/lon, and 2.8 position is off by default with no precise position on public channels, so these nodes have no stored battery or voltage history. BELIEVED.
3. **New fields decoded by python 2.7.11 are silently skipped as protobuf unknown fields.** A soil_water_metrics packet decodes as a Telemetry with an unset oneof, which every MeshForge handler drops with no witness (#7, #9). On the MQTT JSON leg the 2.8 serializer may add keys for new fields. Even so, the nested-key reader would still miss them. Not checked against the 2.8 serializer revision specifically.
4. **TrafficManagementStats and LocalStats noise_floor / num_tx_dropped would be the most valuable 2.8 MOC signals.** They provide rate-limit drops and dedup drops, so you could tell congestion from silence, and the firmware has emitted them since v2.7.19-era protobufs. MeshForge drops both on every leg.
5. **Signed packets and node IDs.** I found no telemetry-side handling of signed or unsigned status. The 2.8 node-number change (derived from the public key) would orphan Kilo registry anchors and tracker telemetry history keyed by the old `!id`. BELIEVED, from the sibling notes' "node IDs change on upgrade" (firmware_2_8.md:87).

### Gaps
- **Owed check on the roadmap.** ROADMAP lists "telemetry from real devices" as an owed client data-model check (BOT-DATA). It is the existing roadmap item closest to this audit. — [.claude/ROADMAP.md:307](.claude/ROADMAP.md)
- **No dedicated sensors or telemetry research doc exists in `.claude/research/`.** A directory listing matched no telemetry/sensor/wx/aprs file other than this folder.
- **The 2.8 MQTT JSON serializer revision was not diffed.** I fetched `develop`, not a 2.8 tag.
- **No live 2.8 node was observed.** All 2.8 behaviour here is inference from release notes.
