# MeshForge Prometheus Metrics

MeshForge exports metrics in Prometheus format for any Prometheus scraper. Grafana dashboards and the exporter's Grafana-only
endpoints were removed 2026-11-14 (unused since 2026-04; operator).

## Quick Start

### Option 1: HTTP Server (Recommended)

Start the built-in metrics server:

```python
from utils.metrics_export import start_metrics_server

# Start on port 9090
server = start_metrics_server(port=9090)
print(f"Metrics available at http://localhost:9090/metrics")

# Server runs in background thread
# To stop: server.stop()
```

**Command-line quick test:**

```bash
cd /path/to/meshforge
python3 -c "
from src.utils.metrics_export import start_metrics_server
import time
server = start_metrics_server(9090)
print('Metrics server running on http://localhost:9090/metrics')
print('Press Ctrl+C to stop')
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    server.stop()
"
```

### Option 2: Textfile Exporter (for node_exporter)

If you use Prometheus node_exporter with the textfile collector:

```python
from utils.metrics_export import setup_textfile_exporter

# Writes metrics every 15 seconds to:
# /var/lib/node_exporter/textfile_collector/meshforge.prom
setup_textfile_exporter()
```

Configure node_exporter to read from this directory:

```bash
node_exporter --collector.textfile.directory=/var/lib/node_exporter/textfile_collector
```

### Option 3: One-time Export

Generate metrics as a string:

```python
from utils.metrics_export import PrometheusExporter

exporter = PrometheusExporter()
metrics_text = exporter.export()
print(metrics_text)

# Or write to file
exporter.write_to_file("/tmp/meshforge.prom")
```

## Prometheus Configuration

Add MeshForge as a scrape target in `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'meshforge'
    static_configs:
      - targets: ['localhost:9090']
    scrape_interval: 15s
    scrape_timeout: 10s
```

For multiple MeshForge instances:

```yaml
scrape_configs:
  - job_name: 'meshforge'
    static_configs:
      - targets:
          - 'node1.local:9090'
          - 'node2.local:9090'
          - 'gateway.local:9090'
        labels:
          network: 'mesh-primary'
```

## Available Metrics

### System Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `meshforge_info` | gauge | version | MeshForge version (always 1) |
| `meshforge_uptime_seconds` | gauge | - | Process uptime in seconds |
| `meshforge_last_scrape_timestamp` | gauge | - | Unix timestamp of last collection |

### Health Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `meshforge_health_score` | gauge | category | Health score 0-100 (overall, connectivity, performance, reliability, freshness) |
| `meshforge_service_healthy` | gauge | service | Service up (1) or down (0) |
| `meshforge_service_uptime_percent` | gauge | service | Service uptime 0-100% |
| `meshforge_service_latency_ms` | gauge | service | Health check latency in ms |
| `meshforge_service_consecutive_fails` | gauge | service | Consecutive check failures |

### Node Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `meshforge_nodes_total` | gauge | state | Node count by state |
| `meshforge_node_snr` | gauge | node_id | Signal-to-noise ratio (dB) |
| `meshforge_node_rssi` | gauge | node_id | Received signal strength (dBm) |
| `meshforge_node_battery_percent` | gauge | node_id | Battery level 0-100% |
| `meshforge_node_last_seen_seconds` | gauge | node_id | Seconds since last heard |

### Message Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `meshforge_messages_total` | counter | direction, status | Total messages by direction (incoming/outgoing) and status (enqueued/delivered/failed) |
| `meshforge_message_queue_depth` | gauge | status | Queue depth by status (pending/in_progress) |
| `meshforge_message_retries_total` | counter | - | Total retry attempts |
| `meshforge_dead_letter_count` | gauge | - | Messages in dead letter queue |

### Gateway Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `meshforge_gateway_connections` | gauge | network | Active connections (meshtastic/rns) |
| `meshforge_gateway_reconnects_total` | counter | network | Reconnection attempts |
| `meshforge_gateway_errors_total` | counter | network, error_type | Errors by network and type |

## Custom Metrics

Add your own metrics:

```python
from utils.metrics_export import PrometheusExporter

exporter = PrometheusExporter()

# Set a custom metric value
exporter.set_custom_metric(
    name="my_custom_metric",
    value=42.0,
    labels={"location": "hawaii", "band": "900MHz"}
)

# Register a custom collector function
def collect_my_metrics():
    return [
        "# HELP my_app_requests Total requests",
        "# TYPE my_app_requests counter",
        "my_app_requests 1234",
    ]

exporter.register_collector(collect_my_metrics)
```

## Endpoints

The metrics server provides:

| Endpoint | Description |
|----------|-------------|
| `/metrics` | Prometheus metrics in exposition format |
| `/health` | Health check (returns "OK") |
| `/healthz` | Kubernetes-style health check |

## Integration with TUI

The TUI launcher can start the metrics server automatically. Add to your config:

```yaml
# ~/.config/meshforge/config.yaml
metrics:
  enabled: true
  port: 9090
```

Or start manually from the TUI: **Tools** > **Start Metrics Server**

## Troubleshooting

### Port Already in Use

```
OSError: [Errno 98] Address already in use
```

Choose a different port or stop the existing server:

```python
server = start_metrics_server(port=9091)  # Use different port
```

### Metrics Not Updating

Verify the metrics server is running:

```bash
curl http://localhost:9090/metrics
```

Check that data sources (SharedHealthState, MetricsHistory) are populated.

### Permission Errors

Textfile exporter needs write access:

```bash
sudo mkdir -p /var/lib/node_exporter/textfile_collector
sudo chown $(whoami) /var/lib/node_exporter/textfile_collector
```
