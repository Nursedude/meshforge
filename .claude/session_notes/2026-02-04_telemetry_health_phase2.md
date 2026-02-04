# Session Notes: Telemetry & Health Metrics Phase 2+ Planning
**Date:** 2026-02-04
**Branch:** `claude/telemetry-health-metrics-planning-bnCeO`
**Session ID:** session_01EYXrTEkQGGKEovcd6VMfD5

## Session Objective

Planning Phase 2+ features for Meshtastic 2.7+ integration:
- Active telemetry request command for silent 2.7+ nodes
- Health metrics extraction (heart rate, SpO2)
- PKI/key verification status tracking
- Favorites sync with BaseUI

---

## Research Summary

### 1. Active Telemetry Request for Silent 2.7+ Nodes

**Background:**
- Meshtastic 2.7.13 stopped sending telemetry by default (good for mesh health)
- Silent nodes require explicit telemetry requests
- CLI command: `meshtastic --request-telemetry --dest '!nodeID'`

**Existing Implementation:**
- `src/commands/meshtastic.py:545` - `request_telemetry(dest)` function exists
- `src/cli/meshtastic_cli.py:389` - CLI menu option `t` for manual request

**Phase 2+ Enhancement:**
```python
# New: Automatic telemetry polling for silent nodes
class TelemetryPoller:
    """Proactively request telemetry from nodes that haven't reported."""

    def __init__(self, node_tracker, poll_interval_minutes=30):
        self.node_tracker = node_tracker
        self.poll_interval = poll_interval_minutes * 60
        self.last_poll: Dict[str, datetime] = {}

    def identify_silent_nodes(self) -> List[str]:
        """Find nodes online but with stale telemetry (>poll_interval)."""
        silent = []
        for node_id, node in self.node_tracker.nodes.items():
            if node.is_online:
                telemetry_age = datetime.now() - (node.telemetry.timestamp or datetime.min)
                if telemetry_age.total_seconds() > self.poll_interval:
                    silent.append(node_id)
        return silent

    async def poll_silent_nodes(self):
        """Request telemetry from silent nodes (rate-limited)."""
        silent = self.identify_silent_nodes()
        for node_id in silent:
            if self._should_poll(node_id):
                await self._request_telemetry(node_id)
                await asyncio.sleep(10)  # Rate limit: 1 request per 10s
```

**Sources:**
- [Meshtastic Python CLI Guide](https://meshtastic.org/docs/software/python/cli/)
- [Feature Request: Add request telemetry to Android](https://github.com/meshtastic/Meshtastic-Android/issues/3610)

---

### 2. Health Metrics Extraction (Heart Rate, SpO2)

**Protobuf Definition** (`meshtastic/telemetry.proto`):
```protobuf
message HealthMetrics {
  optional uint32 heart_bpm = 1;   // Heart rate (beats per minute)
  optional uint32 spO2 = 2;        // Blood oxygen saturation %
  optional float temperature = 3;  // Body temperature (Celsius)
}
```

**Current Implementation:**
- `src/gateway/node_tracker.py:121-136` - `HealthMetrics` dataclass exists
- Fields: `heart_rate`, `spo2`, `body_temperature`, `timestamp`
- Already integrated into `Telemetry` class (line 203)

**Missing Pieces:**
1. **MQTT extraction** - Parse health metrics from MQTT payloads
2. **TCP/Protobuf extraction** - Decode from meshtastic-python API
3. **TUI display** - Show health data in node details

**Implementation Plan:**

```python
# mqtt_subscriber.py - Add health metrics extraction
def _extract_health_metrics(self, telemetry_data: dict) -> Optional[Dict]:
    """Extract health metrics from telemetry packet."""
    if "healthMetrics" not in telemetry_data:
        return None

    hm = telemetry_data["healthMetrics"]
    return {
        "heart_bpm": hm.get("heartBpm"),  # Note: protobuf uses camelCase
        "spo2": hm.get("spO2"),
        "temperature": hm.get("temperature")
    }

# Update MQTTNode dataclass
@dataclass
class MQTTNode:
    # ... existing fields ...
    # Health metrics
    heart_bpm: Optional[int] = None
    spo2: Optional[int] = None
    body_temperature: Optional[float] = None
```

**Sources:**
- [telemetry.proto](https://github.com/meshtastic/protobufs/blob/master/meshtastic/telemetry.proto)
- [Telemetry Module Docs](https://meshtastic.org/docs/configuration/module/telemetry/)

---

### 3. PKI/Key Verification Status Tracking

**Background:**
- PKI introduced in Meshtastic 2.5.0
- Mandatory in 2.7 (legacy DMs disabled)
- Uses Curve25519 elliptic-curve keys
- TOFU (Trust On First Use) model
- 32-byte public keys per node

**Protobuf Definition** (`meshtastic/mesh.proto`):
```protobuf
message User {
  // ... other fields ...
  bytes public_key = 6;  // 32-byte Curve25519 public key
}
```

**Key Verification States:**
1. **UNKNOWN** - No key seen yet
2. **TRUSTED** - Key stored on first use (TOFU)
3. **CHANGED** - Different key seen (potential MITM - red key icon)
4. **VERIFIED** - Manually verified out-of-band (future feature)

**Implementation Plan:**

```python
# New: PKIStatus tracking in node_tracker.py
from enum import Enum
from dataclasses import dataclass

class PKIKeyState(Enum):
    """PKI key verification state."""
    UNKNOWN = "unknown"           # No key seen
    TRUSTED = "trusted"           # TOFU - first key accepted
    CHANGED = "changed"           # Key changed (warning!)
    VERIFIED = "verified"         # Manually verified
    LEGACY = "legacy"             # Pre-2.5 node, no PKI

@dataclass
class PKIStatus:
    """PKI encryption status for a node."""
    state: PKIKeyState = PKIKeyState.UNKNOWN
    public_key: Optional[bytes] = None  # 32-byte Curve25519
    public_key_hex: Optional[str] = None  # Display format
    first_seen: Optional[datetime] = None
    last_changed: Optional[datetime] = None
    is_admin_trusted: bool = False  # In admin_key list

    def key_fingerprint(self) -> str:
        """6-character fingerprint for visual verification."""
        if not self.public_key:
            return "------"
        import hashlib
        h = hashlib.sha256(self.public_key).hexdigest()
        return h[:6].upper()

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "public_key_hex": self.public_key_hex,
            "fingerprint": self.key_fingerprint(),
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "is_admin_trusted": self.is_admin_trusted
        }

# Update UnifiedNode in node_tracker.py
@dataclass
class UnifiedNode:
    # ... existing fields ...
    pki_status: PKIStatus = field(default_factory=PKIStatus)
```

**Key Change Detection:**
```python
def update_pki_status(self, node: UnifiedNode, new_public_key: bytes):
    """Update PKI status, detect key changes."""
    if node.pki_status.public_key is None:
        # First key - TOFU
        node.pki_status = PKIStatus(
            state=PKIKeyState.TRUSTED,
            public_key=new_public_key,
            public_key_hex=new_public_key.hex(),
            first_seen=datetime.now()
        )
    elif node.pki_status.public_key != new_public_key:
        # Key changed - WARNING!
        logger.warning(f"PKI key changed for {node.id}! Potential MITM.")
        node.pki_status.state = PKIKeyState.CHANGED
        node.pki_status.last_changed = datetime.now()
        # Keep old key until manually verified
```

**Sources:**
- [Meshtastic Encryption](https://meshtastic.org/docs/overview/encryption/)
- [Security Configuration](https://meshtastic.org/docs/configuration/radio/security/)
- [PR #168: Adding public/private keys](https://github.com/meshtastic/protobufs/pull/168)
- [Firmware PR #1509: PKI implementation](https://github.com/meshtastic/firmware/pull/1509)

---

### 4. Favorites Sync with BaseUI

**Background:**
- BaseUI is stock UI for devices with screens (2.7+)
- Favorites appear as dedicated icon in menu bar
- Device-local feature - stored on device
- No documented sync API found

**Research Findings:**
- Favorites likely stored in device NodeDB
- Client API uses protobufs for NodeDB sync
- `FromRadio.nodeInfo` contains full node data
- Need to explore if favorites are a flag in NodeInfo

**Implementation Approach (Tentative):**

```python
# Approach 1: Use Python API to read device favorites
from meshtastic import MeshtasticInterface

def get_device_favorites(interface: MeshtasticInterface) -> List[str]:
    """Get favorite node IDs from device."""
    favorites = []
    for node_num, node_info in interface.nodes.items():
        if node_info.get("isFavorite", False):
            favorites.append(node_info["user"]["id"])
    return favorites

# Approach 2: Direct protobuf if favorites field exists
# Need to verify: is there a `is_favorite` flag in NodeInfo?
```

**Action Items:**
1. Explore meshtastic-python source for favorites handling
2. Check if `FromRadio` packets include favorites flag
3. Test with physical device running BaseUI 2.7

**Sources:**
- [BaseUI Documentation](https://meshtastic.org/docs/configuration/device-uis/baseui/)
- [Client API](https://meshtastic.org/docs/development/device/client-api/)
- [Meshtastic 2.7 Preview](https://meshtastic.org/blog/meshtastic-2-7-preview/)

---

## Files to Modify

| File | Change | Priority |
|------|--------|----------|
| `src/gateway/node_tracker.py` | Add `PKIStatus` dataclass, add to `UnifiedNode` | High |
| `src/monitoring/mqtt_subscriber.py` | Extract health metrics from telemetry | High |
| `src/commands/meshtastic.py` | Add batch telemetry request, PKI info | Medium |
| `src/utils/telemetry_poller.py` | **NEW** - Automatic polling for silent nodes | Medium |
| `src/launcher_tui/main.py` | Display health metrics, PKI status | Low |
| `src/utils/metrics_export.py` | Export health/PKI metrics to Prometheus | Low |

---

## Implementation Priority

### Phase 2A (Immediate - Health Metrics)
1. Extract health metrics from MQTT telemetry
2. Display in TUI node details
3. Export to Prometheus/Grafana

### Phase 2B (Next - PKI Tracking)
1. Add PKIStatus to node tracker
2. Parse public_key from NodeInfo
3. Detect key changes with warnings
4. TUI: Show key fingerprint and status

### Phase 2C (Later - Active Polling)
1. TelemetryPoller class for silent nodes
2. Rate-limited polling (1 request per 10s)
3. Integration with node state machine

### Phase 2D (Research - Favorites)
1. Investigate meshtastic-python favorites support
2. Test with BaseUI 2.7 device
3. Implement sync if API exists

---

## Meshtastic 2.7.15 Beta Considerations

**From release notes:**
- Bug fixes and stability improvements
- Consider testing with latest beta for integration reliability
- Key fix: Legacy DM prevention at firmware level

**Recommendation:**
- Test Phase 2 features against 2.7.15 beta
- Ensure backwards compatibility with 2.6.x

---

## Implementation Completed This Session

### Phase 2A: Health Metrics Extraction
**File:** `src/monitoring/mqtt_subscriber.py`
- Added `heart_bpm`, `spo2`, `body_temperature` fields to `MQTTNode` dataclass
- Added health metrics extraction in `_handle_telemetry()` method
- Added `nodes_with_health_metrics` stat tracking

### Phase 2B: PKI Status Tracking
**File:** `src/gateway/node_tracker.py`
- Added `PKIKeyState` enum (UNKNOWN, TRUSTED, CHANGED, VERIFIED, LEGACY)
- Added `PKIStatus` dataclass with:
  - `state`, `public_key`, `public_key_hex`
  - `first_seen`, `last_changed`
  - `is_admin_trusted`
  - `key_fingerprint()` method (6-char SHA256)
- Added `pki_status` field to `UnifiedNode`
- Added `update_pki_status()` method with TOFU logic and key change detection
- Added `verify_pki_key()` method for manual verification
- Updated `to_dict()` to include PKI status

### Test Output
```
PKIStatus default state: PKIKeyState.UNKNOWN
Fingerprint (no key): ------
PKIStatus from key: PKIKeyState.TRUSTED, fingerprint: B6ACCA
Node PKI state: PKIKeyState.UNKNOWN
After update: PKIKeyState.TRUSTED, fingerprint: B6ACCA
After key change: PKIKeyState.CHANGED
PKI KEY CHANGED for test_node! Old: B6ACCA, New: CA02A7 - Potential MITM!
MQTTNode health: HR=72, SpO2=98%, Temp=36.5C
Node dict has pki_status: True
```

---

## Session Entropy Check

**Current Status:** LOW - Implementation complete, tests pass
**Indicators:**
- Clear research findings documented
- Implementation plan structured
- Code changes compile and function correctly

**Next Session:**
- Integrate PKI parsing from meshtastic-python API
- Add TUI display for health metrics and PKI status
- Implement TelemetryPoller for silent nodes

---

## Quick Reference

**Request telemetry from node:**
```bash
meshtastic --host localhost --request-telemetry --dest '!ba4bf9d0'
```

**Check node public key (CLI only):**
```bash
meshtastic --host localhost --info  # Look for public_key in output
```

**Enable health telemetry on device:**
```bash
meshtastic --set telemetry.health_enabled true
meshtastic --set telemetry.health_update_interval 300  # 5 minutes
```
