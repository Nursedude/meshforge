# Session Notes: Node Visibility Deep Dive

**Date**: 2026-02-04
**Branch**: `claude/improve-node-visibility-jeu2h`
**Focus**: Meshtastic 2.7, Metrics, Telemetry, Reliability

---

## Research Sources

- [Meshtastic 2.7 Preview: BaseUI](https://meshtastic.org/blog/meshtastic-2-7-preview/)
- [Demystifying ROUTER_LATE](https://meshtastic.org/blog/demystifying-router-late/)
- [Meshtastic Firmware Releases](https://github.com/meshtastic/firmware/releases)
- [Meshtastic JS Library](https://github.com/meshtastic/js)
- [Telemetry Module Docs](https://meshtastic.org/docs/configuration/module/telemetry/)

---

## Meshtastic 2.7 Key Changes (Relevant to Node Visibility)

### 1. Telemetry Disabled by Default (2.7.13+)
**CRITICAL CHANGE**: Device telemetry broadcasts are now **OFF by default**.

- **Impact**: Passive monitoring will see fewer metrics
- **Mitigation**: Need active telemetry request via CLI: `--request-telemetry --dest '!nodeID'`
- **MeshForge Action**: Consider implementing telemetry request functionality

### 2. Legacy DMs Deprecated - PKI Only
- Direct messages now require PKI (Public Key Infrastructure)
- Non-private DMs no longer allowed
- **MeshForge Action**: Track node encryption/PKI status

### 3. Graduated NodeInfo Timeout Scaling
- NodeInfo send timeout now scales based on active mesh size
- Larger meshes = longer intervals between nodeinfo broadcasts
- **MeshForge Action**: Adjust stale node thresholds based on mesh size

### 4. Key Verification Feature
- New 6-digit verification code for node-to-node trust
- Proves devices are using their respective keys
- **MeshForge Action**: Future - display verification status

### 5. BaseUI Favorites
- Favorited nodes appear in menu bar
- **MeshForge Action**: Could sync favorites with MeshForge tracking

### 6. NodeInfo Ping Removed
- Double-tap to send nodeinfo no longer works in BaseUI/MUI
- Reduces ability to manually trigger discovery

---

## ROUTER_LATE Deep Dive

### Behavior
- "Polite" rebroadcaster - defers if another node rebroadcasts first
- Uses delayed contention window (Window 3)
- Essential for mountain/high-elevation nodes with wide coverage

### Packet Dropping
- Drops **low-priority packets when TX queue is full**
- Shared frequency congestion leads to packet loss
- Queue can fill in seconds during burst activity

### Monitoring Thresholds
| Metric | Warning Threshold | Description |
|--------|-------------------|-------------|
| ChUtil | >25% | Shared channel airtime utilization |
| AirUtilTX | >7-8% | This node's TX airtime |

### Contention Windows
- **Window 1**: ROUTER (immediate rebroadcast)
- **Window 2**: CLIENT, CLIENT_MUTE, REPEATER, SENSOR
- **Window 3**: ROUTER_LATE (delayed)

**SNR-based priority**: Lower SNR = smaller contention window = further nodes rebroadcast first

---

## Current MeshForge Implementation Analysis

### Node Discovery Sources
| Source | File | Method |
|--------|------|--------|
| meshtasticd TCP | `node_monitor.py` | `localhost:4403` via Meshtastic Python API |
| MQTT Public | `mqtt_subscriber.py` | `mqtt.meshtastic.org:8883` (TLS) |
| MQTT Local | `mqtt_subscriber.py` | `localhost:1883` (for meshtasticd→mosquitto) |
| RNS | `node_tracker.py` | Path table, known destinations, announce handlers |

### Telemetry Currently Tracked

**MQTT Subscriber (mqtt_subscriber.py:739-765)**:
```python
# Device metrics only!
- battery_level
- voltage
- channel_utilization
- air_util_tx
```

**Node Monitor (node_monitor.py)**:
```python
# More complete
- battery_level, voltage, channel_utilization, air_util_tx
- temperature, humidity, pressure (environment)
```

**Unified Node Tracker (node_tracker.py)**:
```python
# Full telemetry support in Telemetry dataclass
- Device: battery, voltage, channel_util, air_util_tx, uptime
- Environment: temp, humidity, pressure, gas_resistance
- Air Quality: PM2.5, PM10, CO2, IAQ
- Health: heart_rate, spo2, body_temp
- Detection: motion, reed sensors
```

### Relay Node Discovery (2.6+)
- **Implemented in**: `mqtt_subscriber.py:539-616`
- Creates partial nodes (`!????xx`) from `relay_node` field
- Merges when full ID discovered via telemetry

---

## GAP ANALYSIS

### Priority 1: Critical Gaps

#### GAP-1: MQTT Missing Environment Metrics
**Location**: `mqtt_subscriber.py:_handle_telemetry()`
**Issue**: Only extracts `device_metrics`, ignores `environment_metrics`
**Impact**: Temperature, humidity, pressure not captured from MQTT
**Fix Complexity**: Low - add extraction for environment_metrics payload

#### GAP-2: No Active Telemetry Request
**Issue**: MeshForge only passively receives telemetry
**Impact**: With 2.7.13+ default telemetry OFF, many nodes show no metrics
**Fix**: Implement meshtastic CLI wrapper for `--request-telemetry`
**Complexity**: Medium

#### GAP-3: ChUtil/AirUtilTX Threshold Alerts
**Issue**: No alerts when approaching congestion thresholds
**Impact**: Router operators unaware of mesh congestion
**Fix**: Add threshold monitoring with alerts (ChUtil >25%, AirUtilTX >7-8%)
**Complexity**: Low

### Priority 2: Important Gaps

#### GAP-4: No Mesh Size Tracking
**Issue**: Don't track active mesh size for context
**Impact**: Can't adjust NodeInfo timeout expectations
**Fix**: Track unique nodes seen in rolling window
**Complexity**: Low

#### GAP-5: Missing Air Quality Extraction (MQTT)
**Issue**: MQTT subscriber doesn't parse air_quality_metrics
**Impact**: AQI data not captured from MQTT sources
**Fix**: Add `air_quality_metrics` parsing to `_handle_telemetry()`
**Complexity**: Low

#### GAP-6: No Queue Fullness Visibility
**Issue**: Can't see when ROUTER_LATE is dropping packets
**Impact**: Invisible packet loss
**Fix**: Would require firmware telemetry - out of scope
**Complexity**: N/A (firmware limitation)

### Priority 3: Enhancement Gaps

#### GAP-7: No PKI/Key Verification Status
**Issue**: Don't track encryption status of nodes
**Impact**: Can't show secure vs insecure communications
**Complexity**: Medium (need to parse key fields)

#### GAP-8: Signal Quality vs Contention Window
**Issue**: Don't correlate SNR with expected rebroadcast timing
**Impact**: Can't predict which nodes will relay first
**Complexity**: High (algorithm understanding)

#### GAP-9: Favorited Nodes Sync
**Issue**: BaseUI favorites not synced to MeshForge
**Impact**: Different favorite lists in firmware vs NOC
**Complexity**: Medium

---

## Recommended Improvements (Prioritized)

### Phase 1: Quick Wins (This Session)

1. **Add environment_metrics extraction to MQTT subscriber**
   - File: `mqtt_subscriber.py`
   - Extract: temperature, humidity, pressure from `environment_metrics` payload
   - ~20 lines of code

2. **Add air_quality_metrics extraction to MQTT subscriber**
   - Extract: PM2.5, PM10, CO2 from `air_quality_metrics` payload
   - ~30 lines of code

3. **Add ChUtil/AirUtilTX threshold warnings**
   - Add warning when thresholds exceeded
   - Surface in stats/monitoring output

4. **Add mesh size tracking**
   - Track unique nodes seen in 24hr window
   - Useful for NodeInfo timeout expectations

### Phase 2: Medium Effort (Future Session)

5. **Active telemetry request command**
   - TUI option to request telemetry from specific node
   - Wraps `meshtastic --request-telemetry --dest '!nodeID'`

6. **Health metrics extraction**
   - Heart rate, SpO2 from MQTT telemetry
   - Wire up to Telemetry dataclass

### Phase 3: Larger Effort (Future)

7. **PKI status tracking**
8. **Favorites sync**
9. **Signal quality trending enhancements**

---

## Code Locations for Implementation

| File | Lines | Purpose |
|------|-------|---------|
| `src/monitoring/mqtt_subscriber.py` | 739-765 | Add env/AQ metrics extraction |
| `src/monitoring/mqtt_subscriber.py` | 63-88 | Add MQTTNode fields |
| `src/monitoring/mqtt_subscriber.py` | 874-884 | Add threshold stats |
| `src/gateway/node_tracker.py` | 172-261 | Telemetry dataclass (reference) |

---

## Session Status

- [x] Research Meshtastic 2.7 changes
- [x] Research ROUTER_LATE behavior
- [x] Explore current MeshForge implementation
- [x] Identify gaps and prioritize
- [x] Implement Phase 1 improvements
- [x] Test changes
- [x] Commit and push

---

## Implementation Summary (Completed)

### Changes to `src/monitoring/mqtt_subscriber.py`

#### 1. New Constants Added (lines 62-68)
```python
# Mesh congestion thresholds (from ROUTER_LATE documentation)
CHUTIL_WARNING_THRESHOLD = 25.0    # Channel utilization warning
CHUTIL_CRITICAL_THRESHOLD = 40.0   # Channel utilization critical
AIRUTILTX_WARNING_THRESHOLD = 7.0  # TX airtime warning
AIRUTILTX_CRITICAL_THRESHOLD = 10.0 # TX airtime critical
MESH_SIZE_WINDOW_HOURS = 24        # Track nodes in 24hr window
```

#### 2. New MQTTNode Fields (lines 88-98)
```python
# Environment metrics (BME280, BME680, BMP280)
temperature: Optional[float]     # Celsius
humidity: Optional[float]        # 0-100%
pressure: Optional[float]        # hPa
gas_resistance: Optional[float]  # Ohms (BME680 VOC)

# Air quality metrics (PMSA003I, SCD4X)
pm25_standard: Optional[int]     # PM2.5 standard µg/m³
pm25_environmental: Optional[int]
pm10_standard: Optional[int]
pm10_environmental: Optional[int]
co2: Optional[int]               # CO2 ppm
iaq: Optional[int]               # Indoor Air Quality index
```

#### 3. Extended `_handle_telemetry()` Method
- Now extracts `environment_metrics` payload (temp, humidity, pressure, gas_resistance)
- Now extracts `air_quality_metrics` payload (PM2.5, PM10, CO2, IAQ)

#### 4. New API Methods
| Method | Purpose |
|--------|---------|
| `get_congested_nodes(warning_only=False)` | Get nodes with ChUtil/AirUtilTX above thresholds |
| `get_nodes_with_environment_metrics()` | Get nodes with temp/humidity/pressure data |
| `get_nodes_with_air_quality()` | Get nodes with PM2.5/CO2 data |
| `get_mesh_health()` | Get mesh health summary with status and recommendations |
| `get_mesh_size()` | Get mesh size stats (total, 24hr, online) |

#### 5. Enhanced `get_stats()` Output
Now includes:
- `mesh_health_status`: "healthy", "warning", or "critical"
- `mesh_chutil_avg`: Average channel utilization
- `mesh_airutiltx_avg`: Average TX airtime
- `congested_nodes`: Count of congested nodes
- `nodes_with_env_metrics`: Count with environment sensors
- `nodes_with_aq_metrics`: Count with air quality sensors
- `mesh_size_24h`: Nodes seen in last 24 hours

#### 6. Enhanced GeoJSON Output
Properties now include:
- `channel_utilization`, `air_util_tx`
- `is_congested` boolean flag
- `temperature`, `humidity`, `pressure`
- `pm25`, `co2`, `iaq`

---

## Future Work (Phase 2+)

1. **Active telemetry request** - TUI command to request telemetry from silent nodes
2. **Health metrics extraction** - Heart rate, SpO2 from MQTT
3. **PKI status tracking** - Encryption/key verification status
4. **Favorites sync** - Sync BaseUI favorites with MeshForge
5. **Contention window prediction** - SNR-based relay timing estimates

---

## Related Issues/PRs

- This work follows: #678 (Relay node discovery), #677 (RNS packet sniffer), #676 (TCP monitoring)
- Addresses Meshtastic 2.7.x telemetry changes where telemetry is disabled by default
