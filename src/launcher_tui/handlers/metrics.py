"""
Metrics Handler — Historical network metrics, trends, Prometheus, Grafana.

Converted from metrics_mixin.py as part of the mixin-to-registry migration.
"""

import logging
import subprocess
from datetime import datetime
from pathlib import Path

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

get_metrics_history, MetricType, _HAS_METRICS_HISTORY = safe_import(
    'utils.metrics_history', 'get_metrics_history', 'MetricType'
)
from utils.paths import get_real_user_home
start_metrics_server, _HAS_METRICS_EXPORT = safe_import(
    'utils.metrics_export', 'start_metrics_server'
)


class MetricsHandler(BaseHandler):
    """TUI handler for historical metrics and trends."""

    handler_id = "metrics"
    menu_section = "dashboard"

    # Class-level prometheus server state
    _prometheus_server = None
    _prometheus_port = 9090

    def menu_items(self):
        return [
            ("metrics", "Historical Trends   Metrics over time", None),
        ]

    def execute(self, action):
        if action == "metrics":
            self._metrics_menu()

    def _get_metrics_history(self):
        if not _HAS_METRICS_HISTORY:
            return None
        return get_metrics_history()

    def _metrics_menu(self):
        choices = [
            ("stats", "Storage Statistics"),
            ("trends", "View Trends"),
            ("node", "Node Metrics Summary"),
            ("edge", "Edge/Link Metrics"),
            ("recent", "Recent Metrics"),
            ("export", "Export Metrics (CSV)"),
            ("prometheus", "Prometheus Server"),
            ("grafana", "Grafana Dashboards"),
            ("cleanup", "Database Maintenance"),
            ("back", "Back"),
        ]
        while True:
            choice = self.ctx.dialog.menu("Historical Metrics", "Network metrics and trend analysis:", choices)
            if choice is None or choice == "back":
                break
            dispatch = {
                "stats": ("Metrics Stats", self._metrics_stats),
                "trends": ("Metric Trends", self._metrics_trends),
                "node": ("Node Summary", self._metrics_node_summary),
                "edge": ("Edge Summary", self._metrics_edge_summary),
                "recent": ("Recent Changes", self._metrics_recent),
                "export": ("Export Metrics", self._metrics_export),
                "prometheus": ("Prometheus Server", self._metrics_prometheus),
                "grafana": ("Grafana Dashboards", self._grafana_menu),
                "cleanup": ("Database Maintenance", self._metrics_cleanup),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _metrics_stats(self):
        history = self._get_metrics_history()
        if history is None:
            self.ctx.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return
        stats = history.get_statistics()
        lines = [
            "METRICS STORAGE STATISTICS", "=" * 50, "",
            f"Raw Data Points: {stats['raw_points']:,}",
            f"Hourly Aggregates: {stats['hourly_aggregates']:,}", "",
            f"Unique Nodes: {stats['unique_nodes']}",
            f"Unique Edges: {stats['unique_edges']}", "",
            f"Retention: {stats['retention_days']} days", "",
        ]
        if stats['oldest_timestamp']:
            lines.append(f"Oldest Data: {stats['oldest_timestamp'][:19]}")
        if stats['newest_timestamp']:
            lines.append(f"Newest Data: {stats['newest_timestamp'][:19]}")
        if stats['metric_types']:
            lines.extend(["", "METRICS BY TYPE:"])
            for metric_type, count in sorted(stats['metric_types'].items()):
                lines.append(f"  {metric_type}: {count:,}")
        self.ctx.dialog.msgbox("Metrics Statistics", "\n".join(lines))

    def _metrics_trends(self):
        history = self._get_metrics_history()
        if history is None:
            self.ctx.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return
        if not _HAS_METRICS_HISTORY:
            self.ctx.dialog.msgbox("Error", "MetricType not available")
            return
        type_choices = [
            ("snr", "SNR (Signal-to-Noise Ratio)"), ("rssi", "RSSI (Signal Strength)"),
            ("hops", "Hop Count"), ("link_quality", "Link Quality"),
            ("latency", "Latency"), ("announce_rate", "Announce Rate"), ("back", "Back"),
        ]
        type_choice = self.ctx.dialog.menu("Metric Type", "Select metric to analyze:", type_choices)
        if not type_choice or type_choice == "back":
            return
        type_map = {
            "snr": MetricType.SNR, "rssi": MetricType.RSSI, "hops": MetricType.HOPS,
            "link_quality": MetricType.LINK_QUALITY, "latency": MetricType.LATENCY,
            "announce_rate": MetricType.ANNOUNCE_RATE,
        }
        metric_type = type_map.get(type_choice)
        if not metric_type:
            return
        period_choices = [("1", "Last 1 hour"), ("6", "Last 6 hours"), ("24", "Last 24 hours"), ("168", "Last 7 days"), ("back", "Back")]
        period_choice = self.ctx.dialog.menu("Time Period", "Select analysis period:", period_choices)
        if not period_choice or period_choice == "back":
            return
        try:
            hours = float(period_choice)
        except ValueError:
            return
        trend = history.get_trend(metric_type, hours=hours)
        if not trend:
            self.ctx.dialog.msgbox("No Data", f"No data found for {metric_type.value} in the last {hours:.0f} hours.")
            return
        lines = [
            f"TREND ANALYSIS: {metric_type.value.upper()}", "=" * 50, "",
            f"Period: {trend.period_hours:.0f} hours", f"Data Points: {trend.count:,}", "",
            "VALUES:", f"  Min: {trend.min_value:.2f}", f"  Max: {trend.max_value:.2f}",
            f"  Average: {trend.avg_value:.2f}", f"  Std Dev: {trend.std_dev:.2f}", "",
            "TREND:", f"  First Value: {trend.first_value:.2f}", f"  Last Value: {trend.last_value:.2f}",
            f"  Change: {trend.change:+.2f} ({trend.change_percent:+.1f}%)", "",
        ]
        if trend.trend == "improving":
            trend_display = "↑ IMPROVING"
        elif trend.trend == "degrading":
            trend_display = "↓ DEGRADING"
        else:
            trend_display = "→ STABLE"
        lines.append(f"Status: {trend_display}")
        lines.extend(["", f"From: {trend.start_time.strftime('%Y-%m-%d %H:%M')}", f"To: {trend.end_time.strftime('%Y-%m-%d %H:%M')}"])
        self.ctx.dialog.msgbox("Trend Analysis", "\n".join(lines))

    def _metrics_node_summary(self):
        history = self._get_metrics_history()
        if history is None:
            self.ctx.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return
        node_id = self.ctx.dialog.inputbox("Node ID", "Enter node ID to view metrics for:", "")
        if not node_id:
            return
        summary = history.get_node_metrics_summary(node_id)
        lines = [f"NODE METRICS: {node_id}", "=" * 50, ""]
        if summary['last_seen']:
            lines.extend([f"Last Seen: {summary['last_seen'][:19]}", ""])
        if not summary['metrics']:
            lines.append("No metrics recorded for this node.")
        else:
            lines.append("LATEST VALUES:")
            for metric_name, data in sorted(summary['metrics'].items()):
                value = data['latest_value']
                time_str = data['latest_time'][:16]
                trend_info = data.get('trend')
                trend_indicator = ""
                if trend_info:
                    if trend_info['trend'] == "improving":
                        trend_indicator = " ↑"
                    elif trend_info['trend'] == "degrading":
                        trend_indicator = " ↓"
                    else:
                        trend_indicator = " →"
                lines.append(f"  {metric_name}: {value:.2f}{trend_indicator}")
                lines.append(f"    @ {time_str}")
        self.ctx.dialog.msgbox("Node Metrics", "\n".join(lines))

    def _metrics_edge_summary(self):
        history = self._get_metrics_history()
        if history is None:
            self.ctx.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return
        if not _HAS_METRICS_HISTORY:
            return
        source_id = self.ctx.dialog.inputbox("Source Node", "Enter source node ID:", "local")
        if not source_id:
            return
        dest_id = self.ctx.dialog.inputbox("Destination Node", "Enter destination node ID:", "")
        if not dest_id:
            return
        edge_id = f"{source_id}->{dest_id}"
        lines = [f"EDGE METRICS: {edge_id}", "=" * 50, ""]
        metrics_to_show = [
            (MetricType.SNR, "SNR (dB)"), (MetricType.RSSI, "RSSI (dBm)"),
            (MetricType.HOPS, "Hops"), (MetricType.ANNOUNCE_RATE, "Announces"),
        ]
        has_data = False
        for metric_type, label in metrics_to_show:
            latest = history.get_latest(metric_type, edge_id=edge_id)
            trend = history.get_trend(metric_type, edge_id=edge_id, hours=24)
            if latest:
                has_data = True
                trend_indicator = ""
                change_str = ""
                if trend:
                    if trend.trend == "improving":
                        trend_indicator = " ↑"
                    elif trend.trend == "degrading":
                        trend_indicator = " ↓"
                    else:
                        trend_indicator = " →"
                    change_str = f" ({trend.change:+.1f})"
                lines.append(f"{label}: {latest.value:.2f}{trend_indicator}{change_str}")
                lines.append(f"  Last: {latest.timestamp.strftime('%H:%M:%S')}")
                if trend:
                    lines.append(f"  24h Avg: {trend.avg_value:.2f}")
                lines.append("")
        if not has_data:
            lines.append("No metrics recorded for this edge.")
        self.ctx.dialog.msgbox("Edge Metrics", "\n".join(lines))

    def _metrics_recent(self):
        history = self._get_metrics_history()
        if history is None:
            self.ctx.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return
        points = history.get_recent(hours=1, limit=50)
        if not points:
            self.ctx.dialog.msgbox("No Data", "No recent metrics found.")
            return
        lines = ["RECENT METRICS (Last Hour)", "=" * 60, ""]
        for point in reversed(points[-20:]):
            time_str = point.timestamp.strftime('%H:%M:%S')
            metric = point.metric_type.value
            value = point.value
            target = ""
            if point.node_id:
                target = f" [{point.node_id[:15]}]"
            elif point.edge_id:
                target = f" [{point.edge_id[:20]}]"
            lines.append(f"{time_str} | {metric:<15} | {value:>10.2f}{target}")
        lines.extend(["", f"Total points in last hour: {len(points)}"])
        self.ctx.dialog.msgbox("Recent Metrics", "\n".join(lines))

    def _metrics_export(self):
        history = self._get_metrics_history()
        if history is None:
            self.ctx.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return
        period_choices = [("24", "Last 24 hours"), ("168", "Last 7 days"), ("720", "Last 30 days"), ("back", "Back")]
        period_choice = self.ctx.dialog.menu("Export Period", "Select time period to export:", period_choices)
        if not period_choice or period_choice == "back":
            return
        try:
            hours = float(period_choice)
        except ValueError:
            return
        export_dir = get_real_user_home() / ".cache" / "meshforge"
        export_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        export_path = export_dir / f"metrics_export_{timestamp}.csv"
        self.ctx.dialog.infobox("Exporting...", "Exporting metrics to CSV...")
        try:
            count = history.export_csv(str(export_path), hours=hours)
            self.ctx.dialog.msgbox("Export Complete", f"Exported {count:,} metric points.\n\nFile: {export_path}")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Export failed:\n{e}")

    def _metrics_prometheus(self):
        while True:
            server_running = self._prometheus_server is not None
            port = self._prometheus_port
            status = f"[RUNNING on port {port}]" if server_running else "[STOPPED]"
            choices = []
            if server_running:
                choices.extend([("stop", "Stop Server"), ("test", "Test Endpoint")])
            else:
                choices.extend([("start", "Start Server"), ("port", f"Set Port (current: {port})")])
            choices.extend([("curl", "Show curl Command"), ("back", "Back")])
            choice = self.ctx.dialog.menu("Prometheus Server", f"Prometheus metrics exporter:\n{status}", choices)
            if choice is None or choice == "back":
                break
            if choice == "start":
                self._prometheus_start()
            elif choice == "stop":
                self._prometheus_stop()
            elif choice == "test":
                self._prometheus_test()
            elif choice == "port":
                self._prometheus_set_port()
            elif choice == "curl":
                self._prometheus_show_curl()

    def _prometheus_start(self):
        if not _HAS_METRICS_EXPORT:
            self.ctx.dialog.msgbox("Error", "Prometheus exporter module not available.")
            return
        if MetricsHandler._prometheus_server is not None:
            self.ctx.dialog.msgbox("Already Running", "Server is already running.")
            return
        port = self._prometheus_port
        try:
            MetricsHandler._prometheus_server = start_metrics_server(port=port)
            self.ctx.dialog.msgbox("Server Started", f"Prometheus metrics server started.\n\nPort: {port}\nEndpoint: http://localhost:{port}/metrics\n\nServer runs in background while TUI is active.")
        except Exception as e:
            MetricsHandler._prometheus_server = None
            self.ctx.dialog.msgbox("Error", f"Failed to start server:\n{e}")

    def _prometheus_stop(self):
        if MetricsHandler._prometheus_server is None:
            self.ctx.dialog.msgbox("Not Running", "Server is not running.")
            return
        try:
            MetricsHandler._prometheus_server.shutdown()
            MetricsHandler._prometheus_server = None
            self.ctx.dialog.msgbox("Server Stopped", "Prometheus metrics server stopped.")
        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Failed to stop server:\n{e}")

    def _prometheus_test(self):
        port = self._prometheus_port
        url = f"http://localhost:{port}/metrics"
        clear_screen()
        print(f"=== Testing Prometheus Endpoint ===")
        print(f"URL: {url}\n")
        try:
            result = subprocess.run(['curl', '-s', url], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                lines = result.stdout.split('\n')[:50]
                print('\n'.join(lines))
                if len(result.stdout.split('\n')) > 50:
                    print(f"\n... ({len(result.stdout.split(chr(10)))} total lines)")
            else:
                print(f"Error: {result.stderr}")
        except FileNotFoundError:
            print("curl not found. Install with: apt install curl")
        except subprocess.TimeoutExpired:
            print("Request timed out.")
        except Exception as e:
            print(f"Error: {e}")
        print()
        self.ctx.wait_for_enter()

    def _prometheus_set_port(self):
        port_str = self.ctx.dialog.inputbox("Prometheus Port", "Enter port for Prometheus metrics server:", str(self._prometheus_port))
        if not port_str:
            return
        try:
            port = int(port_str)
            if not (1024 <= port <= 65535):
                raise ValueError("Port must be between 1024 and 65535")
            MetricsHandler._prometheus_port = port
        except ValueError as e:
            self.ctx.dialog.msgbox("Invalid Port", str(e))

    def _prometheus_show_curl(self):
        port = self._prometheus_port
        self.ctx.dialog.msgbox("Prometheus Scrape", f"To test the metrics endpoint:\n\n  curl http://localhost:{port}/metrics\n\nPrometheus scrape config:\n\n  - job_name: 'meshforge'\n    static_configs:\n      - targets: ['localhost:{port}']")

    def _metrics_cleanup(self):
        history = self._get_metrics_history()
        if history is None:
            self.ctx.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return
        stats = history.get_statistics()
        lines = [
            "DATABASE MAINTENANCE", "=" * 50, "",
            f"Current raw points: {stats['raw_points']:,}",
            f"Hourly aggregates: {stats['hourly_aggregates']:,}",
            f"Retention period: {stats['retention_days']} days", "",
            "Note: Automatic cleanup runs hourly.",
            "Data older than 24 hours is aggregated.",
            f"Data older than {stats['retention_days']} days is deleted.",
        ]
        self.ctx.dialog.msgbox("Database Maintenance", "\n".join(lines))
        if self.ctx.dialog.yesno("Run Cleanup", "Run cleanup and aggregation now?", default_no=True):
            self.ctx.dialog.infobox("Processing...", "Running cleanup...")
            try:
                history._perform_cleanup()
                history._aggregate_old_data()
                self.ctx.dialog.msgbox("Cleanup Complete", "Database maintenance completed successfully.")
            except Exception as e:
                self.ctx.dialog.msgbox("Error", f"Cleanup failed:\n{e}")

    def _grafana_menu(self):
        import shutil
        grafana_running = False
        grafana_url = "http://localhost:3000"
        try:
            from utils.service_check import check_service
            grafana_status = check_service('grafana-server')
            grafana_running = grafana_status.available
        except Exception as e:
            logger.debug("Grafana status check failed: %s", e)
        src_dir = Path(__file__).parent.parent.parent
        dashboards_dir = src_dir / "dashboards"
        dashboard_files = list(dashboards_dir.glob("meshforge-*.json")) if dashboards_dir.exists() else []
        status_lines = []
        if grafana_running:
            status_lines.append(f"Grafana: RUNNING at {grafana_url}")
        else:
            status_lines.append("Grafana: NOT RUNNING")
        status_lines.append(f"Dashboards available: {len(dashboard_files)}")
        while True:
            choices = [
                ("status", "Grafana Status"), ("open", "Open Grafana (browser)"),
                ("dashboards", "View Dashboard Files"), ("install", "Install Grafana"),
                ("import", "Import Dashboard Instructions"), ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu("Grafana Dashboards", "\n".join(status_lines), choices)
            if choice is None or choice == "back":
                break
            if choice == "status":
                self._grafana_status()
            elif choice == "open":
                self._grafana_open(grafana_url)
            elif choice == "dashboards":
                self._grafana_list_dashboards(dashboard_files)
            elif choice == "install":
                self._grafana_install()
            elif choice == "import":
                self._grafana_import_instructions(dashboard_files)

    def _grafana_status(self):
        import shutil
        lines = ["GRAFANA STATUS", "=" * 50, ""]
        try:
            result = subprocess.run(['systemctl', 'status', 'grafana-server'], capture_output=True, text=True, timeout=10)
            for line in result.stdout.split('\n')[:10]:
                lines.append(line)
        except FileNotFoundError:
            lines.append("systemctl not available")
        except Exception as e:
            lines.append(f"Error checking status: {e}")
        lines.append("")
        import shutil
        if shutil.which('grafana-server'):
            lines.append("grafana-server: INSTALLED")
        else:
            lines.extend(["grafana-server: NOT FOUND", "", "Install with: sudo apt install grafana"])
        self.ctx.dialog.msgbox("Grafana Status", "\n".join(lines))

    def _grafana_open(self, url):
        import threading
        def open_browser():
            try:
                subprocess.run(['xdg-open', url], timeout=10)
            except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
                logger.debug("Failed to open Grafana URL: %s", e)
        threading.Thread(target=open_browser, daemon=True).start()
        self.ctx.dialog.msgbox("Opening Grafana", f"Opening {url} in browser...\n\nDefault login:\n  Username: admin\n  Password: admin")

    def _grafana_list_dashboards(self, dashboard_files):
        if not dashboard_files:
            self.ctx.dialog.msgbox("No Dashboards", "No MeshForge dashboard files found.")
            return
        lines = ["AVAILABLE DASHBOARDS", "=" * 50, ""]
        for f in dashboard_files:
            lines.append(f"  {f.name}")
        lines.extend(["", f"Location: {dashboard_files[0].parent}", "", "Import these via Grafana UI:", "  Dashboards > Import > Upload JSON"])
        self.ctx.dialog.msgbox("Dashboard Files", "\n".join(lines))

    def _grafana_install(self):
        instructions = """GRAFANA INSTALLATION (Raspberry Pi / Debian)
==================================================

Step 1: Add Grafana GPG key
  curl -fsSL https://apt.grafana.com/gpg.key | \\
    sudo gpg --dearmor -o /usr/share/keyrings/grafana.gpg

Step 2: Add Grafana repository
  echo "deb [signed-by=/usr/share/keyrings/grafana.gpg] \\
    https://apt.grafana.com stable main" | \\
    sudo tee /etc/apt/sources.list.d/grafana.list

Step 3: Install Grafana
  sudo apt update
  sudo apt install grafana

Step 4: Enable and start service
  sudo systemctl enable grafana-server
  sudo systemctl start grafana-server

After install:
  - Access at http://localhost:3000
  - Default login: admin / admin
  - Add Prometheus data source (http://localhost:9090)
  - Import MeshForge dashboards from dashboards/ folder
"""
        self.ctx.dialog.msgbox("Install Grafana", instructions)

    def _grafana_import_instructions(self, dashboard_files):
        dash_path = str(dashboard_files[0].parent) if dashboard_files else "dashboards/"
        instructions = f"""IMPORT MESHFORGE DASHBOARDS
==================================================

1. Open Grafana: http://localhost:3000

2. Add Prometheus data source:
   Configuration > Data Sources > Add > Prometheus
   URL: http://localhost:9090

3. Import dashboards:
   Dashboards > Import > Upload JSON file

   Dashboard files location:
   {dash_path}

   Available dashboards:
"""
        for f in dashboard_files:
            instructions += f"   - {f.name}\n"
        instructions += """
4. Select your Prometheus data source

5. Click Import

The dashboards will show:
- Node counts and status
- Gateway connections
- Message queue depth
- Service health
"""
        self.ctx.dialog.msgbox("Import Instructions", instructions)
