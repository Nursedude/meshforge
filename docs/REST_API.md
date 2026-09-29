# MeshForge REST API Documentation

> **Version:** 0.4.6-beta
> **Base URL:** `http://localhost:5000/api`

---

## Overview

MeshForge provides a REST API for:
- Node management and monitoring
- Network configuration
- Gateway bridge control
- Analytics and metrics
- Webhook event subscriptions

All responses are JSON. Errors include `error` field with description.

---

## Authentication

Currently, the API is intended for local use and does not require authentication.
For production deployments, consider placing behind a reverse proxy with auth.

### The Host rule (DNS-rebinding defence)

The map answers a request only when the `Host` it was addressed to is an IP
literal, a bare name (`moc`), or a local-only name (`.local`, `.home.arpa`,
`.internal`, `.local.mesh`). Anything else — a public name — gets `403`, on
every route except `/healthz` and `/metrics`. A public name is exactly what a
DNS-rebinding page sends: a hostile site re-points its own name at this box and
then reads the API from your browser's trusted address.

**Behind a reverse proxy**, forward the upstream's own host, not the browser's:

- **nginx** — the default (`proxy_set_header Host $proxy_host`) already does.
- **Caddy** — forwards the browser's Host by default; add
  `header_up Host {upstream_hostport}` to the `reverse_proxy` block.

---

## Endpoints

### Nodes

#### GET /api/nodes
List all known mesh nodes.

**Response:**
```json
{
  "nodes": [
    {
      "id": "!abc12345",
      "name": "MyNode",
      "short_name": "MYND",
      "hardware": "TBEAM",
      "firmware": "2.3.0",
      "last_seen": "2026-01-15T10:30:00Z",
      "position": {
        "latitude": 21.3069,
        "longitude": -157.8583,
        "altitude": 50
      },
      "metrics": {
        "rssi": -85,
        "snr": 8.5,
        "battery_level": 85
      }
    }
  ]
}
```

#### GET /api/nodes/{node_id}
Get details for a specific node.

#### GET /api/nodes/{node_id}/telemetry
Get telemetry history for a node.

**Query params:**
- `hours` - Hours of history (default: 24)

---

### System

#### GET /api/system/status
Get system status overview.

**Response:**
```json
{
  "meshtasticd": {
    "running": true,
    "version": "2.3.0",
    "uptime_seconds": 86400
  },
  "rnsd": {
    "running": true,
    "version": "0.7.0"
  },
  "gateway": {
    "running": true,
    "messages_bridged": 1234
  },
  "meshforge_version": "0.4.6-beta"
}
```

#### GET /api/system/diagnostics
Run system diagnostics.

---

### Gateway

#### GET /api/gateway/status
Get gateway bridge status.

**Response:**
```json
{
  "running": true,
  "uptime_seconds": 3600,
  "meshtastic_connected": true,
  "rns_connected": true,
  "messages_bridged": 156,
  "last_message": "2026-01-15T10:30:00Z"
}
```

#### POST /api/gateway/start
Start the gateway bridge.

#### POST /api/gateway/stop
Stop the gateway bridge.

#### GET /api/gateway/queue
Get pending message queue.

---

### Configuration

#### GET /api/config/meshtasticd
Get meshtasticd configuration.

#### PUT /api/config/meshtasticd
Update meshtasticd configuration.

#### GET /api/config/templates
List available config templates.

#### POST /api/config/templates/apply
Apply a configuration template.

---

### Network Tools

#### GET /api/tools/ping/{node_id}
Ping a mesh node.

**Response:**
```json
{
  "success": true,
  "rtt_ms": 1250,
  "hops": 2
}
```

#### POST /api/tools/traceroute
Trace route to a node.

**Body:**
```json
{
  "destination": "!abc12345"
}
```

#### GET /api/tools/link-budget
Calculate link budget.

**Query params:**
- `lat1`, `lon1` - Point 1 coordinates
- `lat2`, `lon2` - Point 2 coordinates
- `freq_mhz` - Frequency in MHz (default: 906)
- `tx_power_dbm` - TX power (default: 20)
- `antenna_gain_dbi` - Antenna gain (default: 2)

---

### Analytics

#### GET /api/analytics/coverage
Get current coverage statistics.

**Query params:**
- `nodes` - JSON array of node objects with lat/lon (optional)

**Response:**
```json
{
  "total_nodes": 15,
  "nodes_with_position": 12,
  "bounding_box": {
    "min_lat": 21.25,
    "max_lat": 21.45,
    "min_lon": -157.95,
    "max_lon": -157.75
  },
  "center_point": {
    "latitude": 21.35,
    "longitude": -157.85
  },
  "estimated_area_km2": 150.5,
  "average_node_spacing_km": 3.5,
  "coverage_radius_km": 12.3
}
```

#### GET /api/analytics/coverage/history
Get coverage history over time.

**Query params:**
- `days` - Number of days (default: 7)

#### GET /api/analytics/link-budget/history
Get link budget measurement history.

**Query params:**
- `source` - Source node ID (optional)
- `dest` - Destination node ID (optional)
- `hours` - Hours of history (default: 24)

**Response:**
```json
{
  "period_hours": 24,
  "sample_count": 48,
  "samples": [
    {
      "timestamp": "2026-01-15T10:30:00Z",
      "source_node": "!abc123",
      "dest_node": "!def456",
      "rssi_dbm": -95,
      "snr_db": 5.5,
      "distance_km": 8.2,
      "packet_loss_pct": 2.5,
      "link_quality": "good"
    }
  ]
}
```

#### GET /api/analytics/link-budget/trends
Get link budget trend analysis.

**Query params:**
- `source` - Source node ID (required)
- `dest` - Destination node ID (required)
- `hours` - Hours of data (default: 168)

**Response:**
```json
{
  "has_data": true,
  "sample_count": 156,
  "period_hours": 168,
  "rssi": {
    "avg": -92.5,
    "min": -105,
    "max": -82,
    "trend": "stable"
  },
  "snr": {
    "avg": 6.2,
    "min": -2,
    "max": 12,
    "trend": "improving"
  },
  "quality_distribution": {
    "excellent": 45,
    "good": 89,
    "fair": 18,
    "bad": 4
  }
}
```

#### POST /api/analytics/link-budget
Record a link budget measurement.

**Body:**
```json
{
  "source_node": "!abc123",
  "dest_node": "!def456",
  "rssi_dbm": -95,
  "snr_db": 5.5,
  "distance_km": 8.2,
  "packet_loss_pct": 2.5,
  "link_quality": "good"
}
```

#### GET /api/analytics/health/history
Get network health history.

**Query params:**
- `hours` - Hours of history (default: 24)

---

### Webhooks

MeshForge can send events to external HTTP endpoints.

#### Event Types

| Event | Description |
|-------|-------------|
| `node_online` | Node came online |
| `node_offline` | Node went offline |
| `message_received` | Message received |
| `position_update` | Node position updated |
| `telemetry_update` | Telemetry data received |
| `alert_battery_low` | Low battery alert |
| `alert_signal_poor` | Poor signal alert |
| `alert_node_unreachable` | Node unreachable |
| `gateway_status` | Gateway status change |
| `service_status` | Service status change |

#### Webhook Payload Format

```json
{
  "event_type": "node_online",
  "timestamp": "2026-01-15T10:30:00Z",
  "source": "meshforge",
  "version": "1.0",
  "data": {
    "node_id": "!abc12345",
    "node_name": "MyNode"
  }
}
```

#### Headers

| Header | Description |
|--------|-------------|
| `Content-Type` | `application/json` |
| `User-Agent` | `MeshForge-Webhook/1.0` |
| `X-MeshForge-Event` | Event type |
| `X-MeshForge-Timestamp` | Event timestamp |
| `X-MeshForge-Signature` | HMAC-SHA256 signature (if secret configured) |

#### GET /api/analytics/webhooks
List configured webhook endpoints.

**Response:**
```json
{
  "endpoints": [
    {
      "url": "https://example.com/webhook",
      "name": "My Webhook",
      "enabled": true,
      "events": ["node_online", "node_offline"],
      "timeout_seconds": 10,
      "retry_count": 3
    }
  ],
  "event_types": ["node_online", "node_offline", "..."]
}
```

#### POST /api/analytics/webhooks
Add a new webhook endpoint.

**Body:**
```json
{
  "url": "https://example.com/webhook",
  "name": "My Webhook",
  "events": ["node_online", "node_offline"],
  "secret": "your-hmac-secret",
  "timeout_seconds": 10,
  "retry_count": 3,
  "headers": {
    "Authorization": "Bearer token123"
  }
}
```

#### DELETE /api/analytics/webhooks
Remove a webhook endpoint.

**Query params:**
- `url` - Webhook URL to remove

#### POST /api/analytics/webhooks/test
Send a test event to a webhook.

**Body:**
```json
{
  "url": "https://example.com/webhook"
}
```

---

### Service Management

#### GET /api/service/{name}/status
Get service status (meshtasticd, rnsd, etc.)

#### POST /api/service/{name}/start
Start a service.

#### POST /api/service/{name}/stop
Stop a service.

#### POST /api/service/{name}/restart
Restart a service.

---

## Error Responses

All errors return appropriate HTTP status codes with JSON body:

```json
{
  "error": "Description of what went wrong"
}
```

| Code | Meaning |
|------|---------|
| 400 | Bad request - invalid parameters |
| 404 | Resource not found |
| 409 | Conflict - resource already exists |
| 500 | Internal server error |
| 503 | Service unavailable |

---

## Rate Limiting

No rate limiting currently implemented. For production use, consider:
- Nginx rate limiting
- API gateway
- Token bucket implementation

---

## Examples

### cURL: Get node list
```bash
curl http://localhost:5000/api/nodes
```

### cURL: Add webhook
```bash
curl -X POST http://localhost:5000/api/analytics/webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/mesh-events",
    "name": "My Integration",
    "events": ["node_online", "node_offline"]
  }'
```

### Python: Monitor nodes
```python
import requests

response = requests.get('http://localhost:5000/api/nodes')
nodes = response.json()['nodes']

for node in nodes:
    print(f"{node['name']}: RSSI={node['metrics']['rssi']} dBm")
```

### JavaScript: Get coverage
```javascript
fetch('/api/analytics/coverage')
  .then(r => r.json())
  .then(data => {
    console.log(`Coverage area: ${data.estimated_area_km2} km²`);
  });
```

---

## Changelog

### v0.4.6-beta
- Added analytics endpoints (coverage, link budget history)
- Added webhook management API
- Added link budget trend analysis

### v0.4.5
- Initial REST API implementation
- Node, system, gateway endpoints

---

*Made with aloha for the mesh community*
