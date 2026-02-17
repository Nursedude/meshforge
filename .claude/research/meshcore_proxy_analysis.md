# MeshCore-Meshtastic-Proxy Analysis for MeshForge Enhancement

**Date**: 2026-02-02
**Repository**: https://github.com/wdunn001/MeshCore-Meshtastic-Proxy
**Purpose**: Identify features and patterns to improve MeshForge reliability and functionality

---

## Executive Summary

MeshCore-Meshtastic-Proxy is an embedded firmware (C++) that bridges MeshCore and Meshtastic mesh networks on hardware like RAK4631 and LoRa32u4II. While it operates at a different layer (firmware vs application), several architectural patterns and reliability features are directly applicable to MeshForge's Python-based NOC system.

**Key Takeaways**:
1. **Canonical Packet Format** - Universal intermediate format reduces conversion complexity
2. **MQTT Filtering** - Prevents internet-originated loops in pure radio mesh
3. **Graceful Degradation** - Lenient parsing with raw relay fallback
4. **Platform Abstraction** - Clean separation enables multi-hardware support
5. **Reliability Primitives** - Counter overflow protection, configuration dedup, timeout-based operations

---

## Detailed Analysis

### 1. Canonical Intermediate Format

**MeshCore-Meshtastic-Proxy Approach**:
```cpp
typedef struct {
    CanonicalRouteType routeType;
    uint8_t hopLimit;
    bool wantAck;
    bool viaMqtt;
    uint32_t sourceAddress, destinationAddress;
    uint32_t packetId;
    CanonicalMessageType messageType;
    uint16_t payloadLength;
    uint8_t payload[255];
} CanonicalPacket;
```

**Why This Matters**: Converting N protocols requires N×(N-1) conversions without an intermediate format. With canonical format, it's only 2×N conversions.

**MeshForge Current State**:
- Direct Meshtastic↔RNS translation in `rns_bridge.py`
- No intermediate representation
- Adding a third protocol (e.g., MeshCore) would require significant refactoring

**Recommendation**: Implement a `CanonicalMessage` class in MeshForge:
```python
@dataclass
class CanonicalMessage:
    source_address: str
    destination_address: str
    message_id: str
    hop_limit: int
    payload: bytes
    message_type: MessageType
    via_internet: bool = False  # For MQTT filtering
    timestamp: datetime = field(default_factory=datetime.utcnow)
```

---

### 2. MQTT/Internet Origin Filtering

**MeshCore-Meshtastic-Proxy Approach**:
```cpp
bool meshtastic_isViaMqtt(const MeshtasticHeader* header) {
    return (header->flags & PACKET_FLAGS_VIA_MQTT_MASK) != 0;
}
// In handlePacket() - silently drop MQTT packets
if (protocol != PROTOCOL_MESHTASTIC && canonical.viaMqtt) {
    return;
}
```

**Why This Matters**: MeshCore is a pure radio network. Internet-originated messages shouldn't be re-broadcast as they create routing inconsistencies and can cause loops.

**MeshForge Current State**:
- `mqtt_subscriber.py` consumes Meshtastic MQTT but doesn't track origin
- Messages bridged to RNS could include MQTT-originated traffic
- No filtering mechanism for "via internet" flag

**Recommendation**: Add origin tracking and filtering:
1. Parse `via_mqtt` flag from Meshtastic packets
2. Add `origin: Literal['radio', 'mqtt', 'api']` field to messages
3. Make filtering configurable per routing rule
4. Log filtered messages for debugging

---

### 3. Lenient Parsing with Fallback

**MeshCore-Meshtastic-Proxy Approach**:
```cpp
if (!iface->convertToCanonical(...)) {
    if (protocol == PROTOCOL_MESHTASTIC) {
        // Relay mode: copy raw bytes
        canonical_packet_init(&canonical);
        memcpy(canonical.payload, data, payloadLength);
        // Continue processing
    } else {
        return;  // Fail for other protocols
    }
}
```

**Why This Matters**: Real-world packets may have unexpected formats. Graceful degradation keeps messages flowing.

**MeshForge Current State**:
- Parsing failures in `node_tracker.py` log errors but may drop messages
- No "raw relay" fallback mode
- Rigid protobuf expectations

**Recommendation**: Implement tiered parsing:
1. **Strict mode**: Full parsing with validation
2. **Lenient mode**: Best-effort parsing, preserve raw data
3. **Raw relay mode**: Pass-through unknown formats with metadata

---

### 4. Counter Overflow Protection

**MeshCore-Meshtastic-Proxy Approach**:
```cpp
#define SAFE_INCREMENT(counter) do { \
    if (counter < 2147483647U) { \
        counter++; \
    } else { \
        counter = 2147483647U; \
    } \
} while(0)
```

**MeshForge Current State**:
- Statistics counters in `bridge_health.py` and `message_queue.py` use unbounded integers
- Python integers don't overflow, but database INT columns do
- No overflow handling for SQLite INTEGER columns (max 2^63-1)

**Recommendation**: While less critical in Python, add bounds checking for:
1. Database counters (SQLite INTEGER max)
2. JSON serialization (JavaScript Number.MAX_SAFE_INTEGER = 2^53-1)
3. Metrics export (Prometheus counter semantics)

---

### 5. Configuration Spam Prevention

**MeshCore-Meshtastic-Proxy Approach**:
```cpp
static ProtocolId lastConfiguredProtocol = PROTOCOL_COUNT;
if (protocol == lastConfiguredProtocol) {
    return;  // Skip redundant configuration
}
```

**MeshForge Current State**:
- Service restarts via `apply_config_and_restart()` always execute
- Radio configuration changes always sent to Meshtastic
- No deduplication of identical commands

**Recommendation**: Implement configuration caching:
```python
class ConfigCache:
    _last_config: dict = {}

    def apply_if_changed(self, service: str, config: dict) -> bool:
        if self._last_config.get(service) == config:
            return False  # No change needed
        self._last_config[service] = config
        return True  # Apply configuration
```

---

### 6. Timeout-Based Operations

**MeshCore-Meshtastic-Proxy Approach**:
- Uses 500ms timeout for LoRa transmission instead of IRQ-based completion
- More reliable on resource-constrained platforms

**MeshForge Current State**:
- `subprocess.run()` calls have timeouts (good)
- LXMF delivery tracking uses 5-minute timeout (good)
- Some async operations lack explicit timeouts

**Recommendation**: Audit all async operations for timeout coverage:
1. RNS announce parsing
2. Path table queries
3. Node discovery callbacks
4. Message queue processing

---

### 7. Protocol-Specific Radio Configuration

**MeshCore-Meshtastic-Proxy Configurations**:

| Parameter | MeshCore | Meshtastic (LongFast) |
|-----------|----------|----------------------|
| Frequency | 910.525 MHz | 906.875 MHz |
| Spreading Factor | 7 | 11 |
| Bandwidth | 62.5 kHz | 250 kHz |
| Sync Word | 0x12 | 0x2B |
| Preamble | 8 bytes | 16 bytes |
| Max Payload | 184 bytes | 237 bytes |

**MeshForge Relevance**: Understanding these parameters helps with:
1. Signal quality analysis (SF11 has better range but slower)
2. Timing calculations (different symbol times)
3. Future MeshCore integration planning
4. Interoperability testing

---

## Feature Recommendations for MeshForge

### High Priority (Reliability)

#### 1. Circuit Breaker Pattern
**Gap**: Repeated failures to a destination don't trigger temporary blocking.

```python
class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.failures = 0
        self.state = CircuitState.CLOSED
        self.last_failure = None

    def record_failure(self):
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.last_failure = time.time()

    def can_proceed(self) -> bool:
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True
```

#### 2. Cross-Network Health Dependency
**Gap**: Bridge doesn't require both networks healthy for operation.

```python
def is_bridge_healthy(self) -> bool:
    mesh_ok = self.meshtastic_manager.is_connected()
    rns_ok = self.rns_connection.is_connected()

    # Require both for reliable bridging
    if not (mesh_ok and rns_ok):
        self._enter_degraded_mode()
        return False

    return True
```

#### 3. Message Origin Tracking
**Gap**: No distinction between radio-originated and internet-originated messages.

```python
class MessageOrigin(Enum):
    RADIO = "radio"
    MQTT = "mqtt"
    API = "api"
    BRIDGE = "bridge"

@dataclass
class TrackedMessage:
    content: bytes
    origin: MessageOrigin
    original_network: str
    hop_count: int
```

### Medium Priority (Functionality)

#### 4. Canonical Message Format
Enable future multi-protocol support:

```python
@dataclass
class CanonicalMessage:
    """Protocol-agnostic message representation"""
    id: str
    source: str
    destination: str
    payload: bytes
    timestamp: datetime
    hop_limit: int
    via_internet: bool
    message_type: str
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_meshtastic(cls, packet: dict) -> 'CanonicalMessage':
        """Convert Meshtastic packet to canonical form"""
        ...

    @classmethod
    def from_rns(cls, lxmf_message) -> 'CanonicalMessage':
        """Convert RNS/LXMF message to canonical form"""
        ...

    def to_meshtastic(self) -> dict:
        """Convert to Meshtastic packet"""
        ...

    def to_rns(self) -> 'LXMF.Message':
        """Convert to RNS/LXMF message"""
        ...
```

#### 5. Lenient Parsing Mode
Handle malformed packets gracefully:

```python
class ParsingMode(Enum):
    STRICT = "strict"      # Full validation, fail on errors
    LENIENT = "lenient"    # Best-effort, preserve what parses
    RAW = "raw"            # Pass-through with metadata only

def parse_packet(data: bytes, mode: ParsingMode = ParsingMode.LENIENT):
    try:
        return strict_parse(data)
    except ParseError as e:
        if mode == ParsingMode.STRICT:
            raise
        elif mode == ParsingMode.LENIENT:
            return partial_parse(data, error=str(e))
        else:  # RAW
            return RawPacket(data=data, parse_error=str(e))
```

#### 6. Event-Driven Path Table Updates
Replace polling with event-driven updates:

```python
class PathTableWatcher:
    """Watch for RNS path table changes"""

    def __init__(self, callback: Callable):
        self.callback = callback
        self._last_hash = None

    def on_announce(self, destination_hash, announced_identity, app_data):
        """Called on new RNS announce"""
        self.callback(PathEvent.ANNOUNCE, destination_hash)

    def on_path_discovered(self, destination_hash, hops):
        """Called when new path discovered"""
        self.callback(PathEvent.DISCOVERED, destination_hash, hops)
```

### Lower Priority (Enhancement)

#### 7. Prometheus Metrics Export
```python
from prometheus_client import Counter, Gauge, Histogram

messages_bridged = Counter('meshforge_messages_bridged_total',
                           'Messages bridged', ['direction'])
queue_depth = Gauge('meshforge_queue_depth', 'Message queue depth')
bridge_latency = Histogram('meshforge_bridge_latency_seconds',
                           'Message bridging latency')
```

#### 8. Dead Letter Queue API
```python
@app.route('/api/v1/dead-letter')
def get_dead_letters():
    """Query dead letter queue for debugging"""
    return {
        'count': queue.dead_letter_count(),
        'messages': queue.get_dead_letters(limit=100),
        'reasons': queue.get_failure_reasons()
    }
```

#### 9. Distributed Tracing
```python
class MessageTracer:
    """Trace message path through bridge"""

    def start_trace(self, message_id: str) -> str:
        trace_id = uuid.uuid4().hex
        self._traces[trace_id] = {
            'message_id': message_id,
            'events': [],
            'start_time': time.time()
        }
        return trace_id

    def add_event(self, trace_id: str, event: str, metadata: dict = None):
        if trace_id in self._traces:
            self._traces[trace_id]['events'].append({
                'event': event,
                'timestamp': time.time(),
                'metadata': metadata or {}
            })
```

---

## Architecture Comparison

| Aspect | MeshCore-Meshtastic-Proxy | MeshForge |
|--------|---------------------------|-----------|
| **Language** | C++ (embedded) | Python 3.9+ |
| **Target** | Firmware (RAK4631, LoRa32u4II) | Linux/Raspberry Pi |
| **Protocols** | MeshCore ↔ Meshtastic | MeshCore ↔ Meshtastic ↔ RNS/LXMF |
| **Message Format** | Canonical intermediate | CanonicalMessage (implemented) |
| **Reliability** | Counter overflow, config dedup | Exponential backoff, health monitor |
| **Persistence** | None (RAM only) | SQLite message queue |
| **UI** | WebSerial PWA | TUI (whiptail/dialog) |
| **MQTT Handling** | Filter at bridge | Filter + monitor (origin tracking) |

---

## Gateway Connection Architecture

The MeshForge gateway bridges three mesh protocols. Each protocol connects to
its radio differently:

```
┌─────────────────────────────────────────────────────────┐
│                   MeshForge Gateway Host                │
│                                                         │
│  Meshtastic radio ── USB ──> meshtasticd ── TCP :4403 ──┤
│                                                         │
│  MeshCore radio ─── USB serial ─────────────────────────┤──> Gateway
│                  or TCP (WiFi firmware / ser2net)        │    Bridge
│                  or BLE (future)                         │
│                                                         │
│  RNS transport ──── rnsd / direct ──────────────────────┤
└─────────────────────────────────────────────────────────┘
```

### MeshCore companion radio connection options

| Method | Transport | Config | Use Case |
|--------|-----------|--------|----------|
| **Serial** | USB cable | `device_path: /dev/ttyUSB1` | Most common — direct USB to companion radio |
| **TCP** | WiFi / LAN | `tcp_host: <ip>, tcp_port: 4000` | Companion on WiFi firmware, or serial-to-TCP bridge |
| **BLE** | Bluetooth LE | `connection_type: ble` | Wireless — config ready, pending meshcore_py support |

### Meshtastic node connection

Meshtastic uses a daemon (`meshtasticd`) that owns the serial connection to the
radio. MeshForge connects to `meshtasticd` over TCP (default `localhost:4403`).
The Meshtastic radio is typically USB-attached to the same host.

### Typical two-radio gateway setup

The simplest gateway setup uses two USB radios on the same host:
- `/dev/ttyUSB0` — Meshtastic radio (managed by meshtasticd)
- `/dev/ttyUSB1` — MeshCore companion radio (managed directly by MeshForge)

Both radios are LoRa devices but operate on different frequencies and modulation
parameters (see Protocol-Specific Radio Configuration above), so they do not
interfere with each other.

---

## Implementation Roadmap

### Phase 1: Reliability — DONE
1. ~~Implement circuit breaker for message queue~~
2. ~~Add cross-network health dependency checks~~
3. ~~Add message origin tracking~~
4. ~~Audit timeout coverage~~

### Phase 2: Extensibility — DONE
1. ~~Design canonical message format~~ (CanonicalMessage)
2. ~~Implement protocol adapters (Meshtastic, RNS, MeshCore)~~
3. ~~Add lenient parsing mode~~
4. ~~Create protocol plugin interface~~

### Phase 3: Observability (Sprint)
1. Prometheus metrics export
2. Dead letter queue API
3. Distributed tracing
4. Health event persistence

### Phase 4: MeshCore Hardening (In Progress)
1. ~~Add MeshCore protocol adapter~~ (meshcore_handler.py)
2. ~~Implement three-way bridge routing~~ (meshcore_bridge_mixin.py)
3. Wire up BLE transport when meshcore_py supports it
4. Field test interoperability

---

## References

- MeshCore: https://github.com/meshcore-dev/MeshCore
- MeshCore CLI (Python): https://github.com/fdlamotte/meshcore-cli
- MeshCore-Meshtastic-Proxy: https://github.com/wdunn001/MeshCore-Meshtastic-Proxy
- MeshCore Protocol: Radio mesh at 910.525 MHz, SF7, BW 62.5 kHz
- Meshtastic LongFast: 906.875 MHz, SF11, BW 250 kHz
- Reticulum Network Stack: https://reticulum.network

---

*Analysis conducted for MeshForge v0.4.8-alpha enhancement planning*
*Updated 2026-02-17: Reflects implemented MeshCore support and connection options*
