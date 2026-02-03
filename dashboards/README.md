# MeshForge Grafana Dashboards

Pre-built Grafana dashboards for monitoring MeshForge mesh networks.

## Available Dashboards

### Prometheus Dashboards

| Dashboard | Description | UID |
|-----------|-------------|-----|
| **MeshForge Overview** | System health, service status, message queues | `meshforge-overview` |
| **MeshForge Node Metrics** | Per-node SNR, RSSI, battery, status table | `meshforge-nodes` |
| **MeshForge Gateway** | Gateway connections, message flow, errors | `meshforge-gateway` |

### InfluxDB Dashboard

| Dashboard | Description | UID |
|-----------|-------------|-----|
| **MeshForge InfluxDB** | Node trends, signal quality, message activity | `meshforge-influxdb` |

### Grafana Infinity Plugin Dashboard

| Dashboard | Description | UID |
|-----------|-------------|-----|
| **MeshForge Infinity** | JSON API integration (no Prometheus required) | `meshforge-infinity` |

## Quick Start

### 1. Enable MeshForge Metrics Server

```python
from utils.metrics_export import start_metrics_server

# Start on default port 9090
server = start_metrics_server()

# Or specify a custom port
server = start_metrics_server(port=9091)
```

Or use the textfile exporter for node_exporter:

```python
from utils.metrics_export import setup_textfile_exporter

# Writes to /var/lib/node_exporter/textfile_collector/meshforge.prom
setup_textfile_exporter()
```

### 2a. Configure Prometheus (for Prometheus dashboards)

Add to your `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'meshforge'
    static_configs:
      - targets: ['localhost:9090']
    scrape_interval: 15s
```

### 2b. Configure InfluxDB (for InfluxDB dashboard)

```python
from utils.metrics_export import start_influxdb_exporter

# InfluxDB 2.x
exporter = start_influxdb_exporter(
    url="http://localhost:8086",
    token="your-token",
    org="meshforge",
    bucket="metrics",
    interval=15
)

# InfluxDB 1.x
exporter = start_influxdb_exporter(
    url="http://localhost:8086",
    database="meshforge",
    interval=15
)
```

### 2c. Configure Infinity Plugin (for JSON API dashboard)

1. Install the Infinity data source plugin in Grafana
2. Add a new Infinity data source with URL: `http://localhost:9090`
3. No authentication required

The JSON API endpoints are:
- `/api/json/metrics` - System metrics
- `/api/json/nodes` - Node list
- `/api/json/status` - Service status

### 3. Import Dashboards to Grafana

1. Open Grafana (default: http://localhost:3000)
2. Go to **Dashboards** > **Import**
3. Upload the JSON file or paste its contents
4. Select your Prometheus data source
5. Click **Import**

## Dashboard Details

### MeshForge Overview

Main dashboard showing:
- Overall health score (0-100%)
- System uptime
- Tracked node count
- Message queue depth
- Dead letter count
- Service health table
- Health score trends over time

### MeshForge Node Metrics

Node-specific metrics:
- Average SNR, RSSI, battery levels
- Per-node signal quality over time
- Node status table with color-coded values
- Battery monitoring trends
- Variable selector for filtering nodes

### MeshForge Gateway

Gateway bridge monitoring:
- Meshtastic/RNS connection status
- Reconnect and error counts
- Message throughput (incoming/outgoing)
- Queue depth visualization
- Retry and dead letter tracking
- Error breakdown by type

## Metrics Reference

| Metric | Type | Description |
|--------|------|-------------|
| `meshforge_health_score` | gauge | Health scores by category (0-100) |
| `meshforge_service_healthy` | gauge | Service health (1=up, 0=down) |
| `meshforge_service_uptime_percent` | gauge | Service uptime percentage |
| `meshforge_nodes_total` | gauge | Total tracked nodes |
| `meshforge_node_snr` | gauge | Node SNR in dB |
| `meshforge_node_rssi` | gauge | Node RSSI in dBm |
| `meshforge_node_battery_percent` | gauge | Node battery level |
| `meshforge_messages_total` | counter | Message counts by direction/status |
| `meshforge_message_queue_depth` | gauge | Current queue sizes |
| `meshforge_gateway_connections` | gauge | Active gateway connections |
| `meshforge_gateway_errors_total` | counter | Gateway errors by type |

## Alerting Examples

Add these to your Prometheus alerting rules:

```yaml
groups:
  - name: meshforge
    rules:
      - alert: MeshForgeHealthLow
        expr: meshforge_health_score{category="overall"} < 50
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "MeshForge health score is low"

      - alert: MeshForgeServiceDown
        expr: meshforge_service_healthy == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "MeshForge service {{ $labels.service }} is down"

      - alert: MeshForgeDeadLetters
        expr: meshforge_dead_letter_count > 10
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Dead letter queue growing"
```

## Requirements

- Grafana 9.0+
- Prometheus 2.0+
- MeshForge with metrics server running

## Customization

These dashboards use template variables and can be customized:

1. Change time ranges in Grafana
2. Add additional panels
3. Modify thresholds (red/yellow/green zones)
4. Add annotations for deployments/events
