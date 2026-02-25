# MeshForge Enhancement TODO List

**Generated**: 2026-02-02
**Source**: MeshCore-Meshtastic-Proxy Analysis
**Status**: Reference (actionable P0/P1 items tracked in `TODO_PRIORITIES.md`)

> **Note (2026-02-21)**: This file contains detailed enhancement proposals with code examples from the MeshCore proxy analysis. Active work items have been promoted to `TODO_PRIORITIES.md`. Use this file as a reference for implementation details and patterns.

---

## Priority Legend
- 🔴 **P0 - Critical**: Reliability/stability issues
- 🟠 **P1 - High**: Significant functionality improvements
- 🟡 **P2 - Medium**: Nice-to-have features
- 🟢 **P3 - Low**: Future enhancements

---

## 🔴 P0 - Critical (Reliability)

### [ ] 1. Circuit Breaker Pattern for Message Queue
**File**: `src/gateway/message_queue.py`
**Effort**: Medium
**Description**: Prevent repeated failures from overwhelming the queue and network.

**Implementation**:
```python
class CircuitBreaker:
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Blocking calls
    HALF_OPEN = "half_open"  # Testing recovery

    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.failures = defaultdict(int)
        self.state = defaultdict(lambda: self.CLOSED)
        self.last_failure = {}

    def record_failure(self, destination: str):
        self.failures[destination] += 1
        if self.failures[destination] >= self.failure_threshold:
            self.state[destination] = self.OPEN
            self.last_failure[destination] = time.time()

    def record_success(self, destination: str):
        self.failures[destination] = 0
        self.state[destination] = self.CLOSED

    def can_send(self, destination: str) -> bool:
        if self.state[destination] == self.OPEN:
            if time.time() - self.last_failure.get(destination, 0) > self.recovery_timeout:
                self.state[destination] = self.HALF_OPEN
                return True
            return False
        return True
```

**Acceptance Criteria**:
- [ ] Circuit breaker blocks sends after 5 consecutive failures
- [ ] Automatic recovery after 60-second timeout
- [ ] Half-open state allows single test message
- [ ] Statistics track circuit state per destination

---

### [ ] 2. Cross-Network Health Dependency
**File**: `src/gateway/rns_bridge.py`
**Effort**: Small
**Description**: Bridge should detect when one leg fails and enter degraded mode.

**Implementation**:
```python
def _check_bridge_health(self) -> BridgeHealthStatus:
    mesh_status = self.meshtastic_manager.get_status()
    rns_status = self.rns_connection.get_status()

    if mesh_status.connected and rns_status.connected:
        return BridgeHealthStatus.HEALTHY
    elif mesh_status.connected or rns_status.connected:
        return BridgeHealthStatus.DEGRADED
    else:
        return BridgeHealthStatus.OFFLINE

def _enter_degraded_mode(self, reason: str):
    """Pause bridging but continue monitoring"""
    self.health_monitor.record_event('degraded_mode_entered', reason=reason)
    # Queue messages instead of attempting delivery
    self._bridging_enabled = False
```

**Acceptance Criteria**:
- [ ] Bridge detects single-leg failures within 30 seconds
- [ ] Messages queued (not dropped) during degraded mode
- [ ] Automatic recovery when both networks reconnect
- [ ] Clear status indication in TUI

---

### [ ] 3. Message Origin Tracking
**File**: `src/gateway/rns_bridge.py`, `src/monitoring/mqtt_subscriber.py`
**Effort**: Medium
**Description**: Track whether messages originated from radio or internet (MQTT).

**Implementation**:
```python
class MessageOrigin(Enum):
    RADIO = "radio"
    MQTT = "mqtt"
    API = "api"
    UNKNOWN = "unknown"

@dataclass
class TrackedMessage:
    content: bytes
    origin: MessageOrigin
    source_network: str  # "meshtastic" or "rns"
    via_internet: bool
    received_at: datetime
    metadata: dict = field(default_factory=dict)
```

**Files to modify**:
1. `mqtt_subscriber.py`: Tag messages with `origin=MQTT`
2. `rns_bridge.py`: Parse Meshtastic `via_mqtt` flag
3. `message_queue.py`: Store origin in queue schema
4. Add filtering rules based on origin

**Acceptance Criteria**:
- [ ] MQTT-originated messages tagged correctly
- [ ] Meshtastic `via_mqtt` flag parsed
- [ ] Configurable filter: "drop internet-originated messages"
- [ ] Origin visible in message lifecycle history

---

### [ ] 4. Timeout Audit for Async Operations
**File**: Multiple
**Effort**: Small
**Description**: Ensure all async operations have explicit timeouts.

**Files to audit**:
- [ ] `src/gateway/node_tracker.py` - RNS announce callbacks
- [ ] `src/gateway/rns_bridge.py` - LXMF operations
- [ ] `src/monitoring/mqtt_subscriber.py` - Connection operations
- [ ] `src/utils/service_check.py` - systemctl calls

**Pattern**:
```python
# Before
result = rns.Transport.request_path(destination)

# After
try:
    result = rns.Transport.request_path(destination)
    # Set operation timeout
    deadline = time.time() + 30
    while not result.ready() and time.time() < deadline:
        time.sleep(0.1)
    if not result.ready():
        raise TimeoutError("Path request timed out")
except Exception as e:
    logger.error(f"Path request failed: {e}")
```

**Acceptance Criteria**:
- [ ] All RNS operations have 30-second timeout
- [ ] All subprocess calls have explicit timeout
- [ ] Timeout errors logged with context
- [ ] No hanging operations possible

---

## 🟠 P1 - High (Functionality)

### [ ] 5. Canonical Message Format
**File**: `src/gateway/canonical.py` (new)
**Effort**: Large
**Description**: Protocol-agnostic message representation for easier multi-protocol support.

**Implementation**:
```python
@dataclass
class CanonicalMessage:
    """Protocol-agnostic message representation"""
    id: str
    source_address: str
    destination_address: str
    payload: bytes
    timestamp: datetime
    hop_limit: int = 3
    via_internet: bool = False
    message_type: str = "text"
    encoding: str = "utf-8"
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_meshtastic(cls, packet: dict) -> 'CanonicalMessage':
        """Convert Meshtastic packet to canonical form"""
        return cls(
            id=str(packet.get('id', uuid.uuid4().hex)),
            source_address=packet.get('fromId', ''),
            destination_address=packet.get('toId', '^all'),
            payload=packet.get('decoded', {}).get('payload', b''),
            timestamp=datetime.fromtimestamp(packet.get('rxTime', time.time())),
            hop_limit=packet.get('hopLimit', 3),
            via_internet=packet.get('viaMqtt', False),
            metadata={'snr': packet.get('rxSnr'), 'rssi': packet.get('rxRssi')}
        )

    @classmethod
    def from_lxmf(cls, message) -> 'CanonicalMessage':
        """Convert LXMF message to canonical form"""
        ...

    def to_meshtastic(self) -> dict:
        """Convert to Meshtastic-compatible dict"""
        ...

    def to_lxmf(self):
        """Convert to LXMF message"""
        ...
```

**Acceptance Criteria**:
- [ ] Bidirectional conversion: Meshtastic ↔ Canonical ↔ LXMF
- [ ] No data loss in round-trip conversion
- [ ] Metadata preserved (SNR, RSSI, timestamps)
- [ ] Unit tests for all conversions

---

### [ ] 6. Lenient Parsing Mode
**File**: `src/gateway/parsers.py` (new)
**Effort**: Medium
**Description**: Handle malformed packets gracefully instead of dropping.

**Implementation**:
```python
class ParsingMode(Enum):
    STRICT = "strict"
    LENIENT = "lenient"
    RAW = "raw"

class PacketParser:
    def __init__(self, mode: ParsingMode = ParsingMode.LENIENT):
        self.mode = mode
        self.parse_errors = Counter()

    def parse(self, data: bytes, protocol: str) -> Union[ParsedPacket, RawPacket]:
        try:
            return self._strict_parse(data, protocol)
        except ParseError as e:
            self.parse_errors['total'] += 1
            if self.mode == ParsingMode.STRICT:
                raise
            elif self.mode == ParsingMode.LENIENT:
                return self._partial_parse(data, protocol, error=e)
            else:  # RAW
                return RawPacket(
                    data=data,
                    protocol=protocol,
                    error=str(e),
                    timestamp=datetime.utcnow()
                )
```

**Acceptance Criteria**:
- [ ] Strict mode fails fast on parse errors
- [ ] Lenient mode extracts what it can, logs errors
- [ ] Raw mode passes through with metadata
- [ ] Parse error statistics available
- [ ] Mode configurable per protocol

---

### [ ] 7. Event-Driven Path Table Updates
**File**: `src/gateway/node_tracker.py`
**Effort**: Medium
**Description**: Replace 30-second polling with event-driven updates.

**Current behavior** (polling):
```python
# Every 30 seconds
path_table = RNS.Transport.path_table
```

**New behavior** (event-driven):
```python
class PathTableWatcher:
    def __init__(self, tracker: NodeTracker):
        self.tracker = tracker
        # Register for RNS events
        RNS.Transport.register_announce_handler(self._on_announce)

    def _on_announce(self, destination_hash, announced_identity, app_data):
        """Called immediately when new announce received"""
        self.tracker.update_node_from_announce(
            destination_hash, announced_identity, app_data
        )

    def _on_path_discovered(self, destination_hash):
        """Called when new path discovered"""
        hops = RNS.Transport.hops_to(destination_hash)
        self.tracker.update_node_hops(destination_hash, hops)
```

**Acceptance Criteria**:
- [ ] New announces processed within 1 second
- [ ] Path discoveries trigger immediate updates
- [ ] Fallback to polling if events unavailable
- [ ] Reduced CPU usage from polling

---

### [ ] 8. Routing Decision Analytics
**File**: `src/gateway/routing_analytics.py` (new)
**Effort**: Medium
**Description**: Track routing decisions for debugging and optimization.

**Implementation**:
```python
@dataclass
class RoutingDecision:
    message_id: str
    timestamp: datetime
    source_network: str
    matched_rule: Optional[str]
    confidence_score: float
    decision: str  # "bridge", "drop", "bounce"
    reason: str

class RoutingAnalytics:
    def __init__(self, max_history=10000):
        self.decisions = deque(maxlen=max_history)

    def record(self, decision: RoutingDecision):
        self.decisions.append(decision)

    def get_statistics(self) -> dict:
        return {
            'total_decisions': len(self.decisions),
            'by_decision': Counter(d.decision for d in self.decisions),
            'by_rule': Counter(d.matched_rule for d in self.decisions),
            'avg_confidence': statistics.mean(d.confidence_score for d in self.decisions),
            'low_confidence_rate': sum(1 for d in self.decisions if d.confidence_score < 0.5) / len(self.decisions)
        }

    def get_bounced_messages(self, limit=100):
        return [d for d in self.decisions if d.decision == 'bounce'][:limit]
```

**Acceptance Criteria**:
- [ ] All routing decisions logged
- [ ] Bounced messages queryable
- [ ] Statistics available via API
- [ ] Export to JSON for analysis

---

## 🟡 P2 - Medium (Observability)

### [ ] 9. Prometheus Metrics Export
**File**: `src/gateway/metrics.py` (new)
**Effort**: Medium
**Description**: Export metrics in Prometheus format for monitoring.

**Implementation**:
```python
from prometheus_client import Counter, Gauge, Histogram, start_http_server

# Message metrics
messages_received = Counter(
    'meshforge_messages_received_total',
    'Total messages received',
    ['network', 'origin']
)
messages_bridged = Counter(
    'meshforge_messages_bridged_total',
    'Total messages bridged',
    ['direction']
)
messages_dropped = Counter(
    'meshforge_messages_dropped_total',
    'Total messages dropped',
    ['reason']
)

# Queue metrics
queue_depth = Gauge('meshforge_queue_depth', 'Message queue depth')
queue_age_seconds = Histogram(
    'meshforge_queue_age_seconds',
    'Age of messages in queue',
    buckets=[1, 5, 10, 30, 60, 300, 600]
)

# Health metrics
connection_status = Gauge(
    'meshforge_connection_status',
    'Connection status (1=connected, 0=disconnected)',
    ['network']
)
```

**Acceptance Criteria**:
- [ ] Metrics endpoint at `/metrics` (port 9090)
- [ ] All key metrics exposed
- [ ] Labels for filtering/grouping
- [ ] Compatible with Grafana dashboards

---

### [ ] 10. Dead Letter Queue API
**File**: `src/gateway/message_queue.py`
**Effort**: Small
**Description**: API to query and manage dead letter messages.

**Implementation**:
```python
class MessageQueue:
    def get_dead_letters(self, limit: int = 100, offset: int = 0) -> List[dict]:
        """Query dead letter queue"""
        cursor = self.conn.execute('''
            SELECT id, content, destination, error_reason, failed_at
            FROM dead_letters
            ORDER BY failed_at DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset))
        return [dict(row) for row in cursor.fetchall()]

    def retry_dead_letter(self, message_id: str) -> bool:
        """Move message from dead letter back to pending"""
        ...

    def purge_dead_letters(self, older_than_hours: int = 24) -> int:
        """Remove old dead letters"""
        ...

    def get_dead_letter_stats(self) -> dict:
        """Statistics about dead letter queue"""
        return {
            'total': self._count_dead_letters(),
            'by_reason': self._group_by_error_reason(),
            'oldest': self._get_oldest_dead_letter()
        }
```

**Acceptance Criteria**:
- [ ] Query dead letters with pagination
- [ ] Retry individual messages
- [ ] Purge old messages
- [ ] Statistics by failure reason

---

### [ ] 11. Health Event Persistence
**File**: `src/gateway/bridge_health.py`
**Effort**: Small
**Description**: Persist health events across restarts.

**Implementation**:
```python
class PersistentHealthMonitor(BridgeHealthMonitor):
    def __init__(self, db_path: Path):
        super().__init__()
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS health_events (
                id INTEGER PRIMARY KEY,
                event_type TEXT,
                timestamp TEXT,
                details TEXT,
                network TEXT
            )
        ''')
        conn.commit()

    def record_event(self, event_type: str, **kwargs):
        super().record_event(event_type, **kwargs)
        # Also persist
        conn = sqlite3.connect(self.db_path)
        conn.execute('''
            INSERT INTO health_events (event_type, timestamp, details, network)
            VALUES (?, ?, ?, ?)
        ''', (event_type, datetime.utcnow().isoformat(), json.dumps(kwargs), kwargs.get('network')))
        conn.commit()
```

**Acceptance Criteria**:
- [ ] Events survive restart
- [ ] Query historical events
- [ ] Automatic cleanup of old events (7 days)
- [ ] Export to JSON

---

### [ ] 12. Distributed Tracing
**File**: `src/gateway/tracing.py` (new)
**Effort**: Medium
**Description**: Trace message path through entire system.

**Implementation**:
```python
class MessageTracer:
    def __init__(self):
        self.traces = {}

    def start_trace(self, message_id: str) -> str:
        trace_id = uuid.uuid4().hex[:16]
        self.traces[trace_id] = {
            'message_id': message_id,
            'start_time': time.time(),
            'events': []
        }
        return trace_id

    def add_event(self, trace_id: str, event: str, **metadata):
        if trace_id in self.traces:
            self.traces[trace_id]['events'].append({
                'event': event,
                'timestamp': time.time(),
                **metadata
            })

    def end_trace(self, trace_id: str) -> dict:
        if trace_id in self.traces:
            trace = self.traces.pop(trace_id)
            trace['end_time'] = time.time()
            trace['duration'] = trace['end_time'] - trace['start_time']
            return trace
```

**Events to trace**:
- `received` - Message received from network
- `parsed` - Message parsed successfully
- `queued` - Message added to queue
- `routing_decision` - Routing rule matched
- `sent` - Message sent to target network
- `delivered` - Delivery confirmed

**Acceptance Criteria**:
- [ ] Full message lifecycle visible
- [ ] Duration between events calculated
- [ ] Export traces to JSON
- [ ] Integration with logging

---

## 🟢 P3 - Low (Future)

### [ ] 13. MeshCore Protocol Support
**File**: `src/protocols/meshcore.py` (new)
**Effort**: Large
**Description**: Add MeshCore as third bridgeable protocol.

**Prerequisites**:
- Canonical message format (P1-5)
- Protocol plugin architecture

**MeshCore specifics**:
- Frequency: 910.525 MHz
- Spreading Factor: 7
- Bandwidth: 62.5 kHz
- Max payload: 184 bytes
- Route types: FLOOD, SINGLE_HOP

---

### [ ] 14. Configuration Deduplication
**File**: `src/utils/config_cache.py` (new)
**Effort**: Small
**Description**: Avoid sending identical configurations.

```python
class ConfigCache:
    def __init__(self):
        self._cache = {}

    def should_apply(self, key: str, config: dict) -> bool:
        config_hash = hash(json.dumps(config, sort_keys=True))
        if self._cache.get(key) == config_hash:
            return False
        self._cache[key] = config_hash
        return True
```

---

### [ ] 15. Integration Test Suite
**File**: `tests/integration/` (new)
**Effort**: Large
**Description**: End-to-end tests for message bridging.

**Test scenarios**:
- [ ] Meshtastic → RNS message delivery
- [ ] RNS → Meshtastic message delivery
- [ ] Queue persistence across restart
- [ ] Circuit breaker activation
- [ ] Degraded mode behavior
- [ ] Reconnection recovery

---

## Implementation Notes

### Dependencies to Add
```
# requirements.txt additions
prometheus-client>=0.17.0  # For metrics export
```

### Database Schema Changes
```sql
-- Add to message_queue.py schema
ALTER TABLE messages ADD COLUMN origin TEXT DEFAULT 'unknown';
ALTER TABLE messages ADD COLUMN via_internet INTEGER DEFAULT 0;

-- New table for health events
CREATE TABLE health_events (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    details TEXT,
    network TEXT
);
```

### Configuration Changes
```yaml
# config.yaml additions
bridge:
  circuit_breaker:
    enabled: true
    failure_threshold: 5
    recovery_timeout: 60

  origin_filtering:
    drop_mqtt_originated: false  # Set true for pure radio mesh

  parsing_mode: lenient  # strict, lenient, raw

metrics:
  enabled: true
  port: 9090
  path: /metrics
```

---

## Progress Tracking

| ID | Priority | Task | Status | Assignee |
|----|----------|------|--------|----------|
| 1 | P0 | Circuit Breaker | ⬜ Not Started | |
| 2 | P0 | Cross-Network Health | ⬜ Not Started | |
| 3 | P0 | Message Origin Tracking | ⬜ Not Started | |
| 4 | P0 | Timeout Audit | ⬜ Not Started | |
| 5 | P1 | Canonical Message Format | ⬜ Not Started | |
| 6 | P1 | Lenient Parsing Mode | ⬜ Not Started | |
| 7 | P1 | Event-Driven Path Updates | ⬜ Not Started | |
| 8 | P1 | Routing Analytics | ⬜ Not Started | |
| 9 | P2 | Prometheus Metrics | ⬜ Not Started | |
| 10 | P2 | Dead Letter API | ⬜ Not Started | |
| 11 | P2 | Health Persistence | ⬜ Not Started | |
| 12 | P2 | Distributed Tracing | ⬜ Not Started | |
| 13 | P3 | MeshCore Support | ⬜ Not Started | |
| 14 | P3 | Config Deduplication | ⬜ Not Started | |
| 15 | P3 | Integration Tests | ⬜ Not Started | |

---

*Generated from MeshCore-Meshtastic-Proxy analysis - 2026-02-02*
