"""
Historical Metrics Mixin for MeshForge Launcher TUI.

Provides access to historical network metrics:
- View SNR/hop trends over time
- Node metrics summaries
- Trend analysis
- Export functionality
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
from backend import clear_screen
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# Module-level safe imports
get_metrics_history, MetricType, _HAS_METRICS_HISTORY = safe_import(
    'utils.metrics_history', 'get_metrics_history', 'MetricType'
)
from utils.paths import get_real_user_home
start_metrics_server, _HAS_METRICS_EXPORT = safe_import(
    'utils.metrics_export', 'start_metrics_server'
)


class MetricsMixin:
    """Mixin providing historical metrics tools for the TUI launcher."""

    def _metrics_menu(self):
        """Historical metrics and trends menu."""
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
            choice = self.dialog.menu(
                "Historical Metrics",
                "Network metrics and trend analysis:",
                choices
            )

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
                self._safe_call(*entry)

    def _get_metrics_history(self):
        """Get the MetricsHistory instance."""
        if not _HAS_METRICS_HISTORY:
            return None
        return get_metrics_history()

    def _metrics_stats(self):
        """Show metrics storage statistics."""
        history = self._get_metrics_history()

        if history is None:
            self.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return

        stats = history.get_statistics()

        lines = [
            "METRICS STORAGE STATISTICS",
            "=" * 50,
            "",
            f"Raw Data Points: {stats['raw_points']:,}",
            f"Hourly Aggregates: {stats['hourly_aggregates']:,}",
            "",
            f"Unique Nodes: {stats['unique_nodes']}",
            f"Unique Edges: {stats['unique_edges']}",
            "",
            f"Retention: {stats['retention_days']} days",
            "",
        ]

        if stats['oldest_timestamp']:
            lines.append(f"Oldest Data: {stats['oldest_timestamp'][:19]}")
        if stats['newest_timestamp']:
            lines.append(f"Newest Data: {stats['newest_timestamp'][:19]}")

        if stats['metric_types']:
            lines.append("")
            lines.append("METRICS BY TYPE:")
            for metric_type, count in sorted(stats['metric_types'].items()):
                lines.append(f"  {metric_type}: {count:,}")

        self.dialog.msgbox("Metrics Statistics", "\n".join(lines))

    def _metrics_trends(self):
        """View metric trends."""
        history = self._get_metrics_history()

        if history is None:
            self.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return

        if not _HAS_METRICS_HISTORY:
            self.dialog.msgbox("Error", "MetricType not available")
            return

        # Select metric type
        type_choices = [
            ("snr", "SNR (Signal-to-Noise Ratio)"),
            ("rssi", "RSSI (Signal Strength)"),
            ("hops", "Hop Count"),
            ("link_quality", "Link Quality"),
            ("latency", "Latency"),
            ("announce_rate", "Announce Rate"),
            ("back", "Back"),
        ]

        type_choice = self.dialog.menu(
            "Metric Type",
            "Select metric to analyze:",
            type_choices
        )

        if not type_choice or type_choice == "back":
            return

        # Map choice to MetricType
        type_map = {
            "snr": MetricType.SNR,
            "rssi": MetricType.RSSI,
            "hops": MetricType.HOPS,
            "link_quality": MetricType.LINK_QUALITY,
            "latency": MetricType.LATENCY,
            "announce_rate": MetricType.ANNOUNCE_RATE,
        }
        metric_type = type_map.get(type_choice)

        if not metric_type:
            return

        # Select time period
        period_choices = [
            ("1", "Last 1 hour"),
            ("6", "Last 6 hours"),
            ("24", "Last 24 hours"),
            ("168", "Last 7 days"),
            ("back", "Back"),
        ]

        period_choice = self.dialog.menu(
            "Time Period",
            "Select analysis period:",
            period_choices
        )

        if not period_choice or period_choice == "back":
            return

        try:
            hours = float(period_choice)
        except ValueError:
            return

        # Get trend analysis
        trend = history.get_trend(metric_type, hours=hours)

        if not trend:
            self.dialog.msgbox(
                "No Data",
                f"No data found for {metric_type.value} in the last {hours:.0f} hours."
            )
            return

        # Display trend
        lines = [
            f"TREND ANALYSIS: {metric_type.value.upper()}",
            "=" * 50,
            "",
            f"Period: {trend.period_hours:.0f} hours",
            f"Data Points: {trend.count:,}",
            "",
            "VALUES:",
            f"  Min: {trend.min_value:.2f}",
            f"  Max: {trend.max_value:.2f}",
            f"  Average: {trend.avg_value:.2f}",
            f"  Std Dev: {trend.std_dev:.2f}",
            "",
            "TREND:",
            f"  First Value: {trend.first_value:.2f}",
            f"  Last Value: {trend.last_value:.2f}",
            f"  Change: {trend.change:+.2f} ({trend.change_percent:+.1f}%)",
            "",
        ]

        # Trend indicator
        if trend.trend == "improving":
            trend_display = "↑ IMPROVING"
        elif trend.trend == "degrading":
            trend_display = "↓ DEGRADING"
        else:
            trend_display = "→ STABLE"

        lines.append(f"Status: {trend_display}")
        lines.append("")
        lines.append(f"From: {trend.start_time.strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"To: {trend.end_time.strftime('%Y-%m-%d %H:%M')}")

        self.dialog.msgbox("Trend Analysis", "\n".join(lines))

    def _metrics_node_summary(self):
        """Show metrics summary for a node."""
        history = self._get_metrics_history()

        if history is None:
            self.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return

        # Get node ID from user
        node_id = self.dialog.inputbox(
            "Node ID",
            "Enter node ID to view metrics for:",
            ""
        )

        if not node_id:
            return

        summary = history.get_node_metrics_summary(node_id)

        lines = [
            f"NODE METRICS: {node_id}",
            "=" * 50,
            "",
        ]

        if summary['last_seen']:
            lines.append(f"Last Seen: {summary['last_seen'][:19]}")
            lines.append("")

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

        self.dialog.msgbox("Node Metrics", "\n".join(lines))

    def _metrics_edge_summary(self):
        """Show metrics for a specific edge/link."""
        history = self._get_metrics_history()

        if history is None:
            self.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return

        if not _HAS_METRICS_HISTORY:
            return

        # Get edge info from user
        source_id = self.dialog.inputbox(
            "Source Node",
            "Enter source node ID:",
            "local"
        )

        if not source_id:
            return

        dest_id = self.dialog.inputbox(
            "Destination Node",
            "Enter destination node ID:",
            ""
        )

        if not dest_id:
            return

        edge_id = f"{source_id}->{dest_id}"

        lines = [
            f"EDGE METRICS: {edge_id}",
            "=" * 50,
            "",
        ]

        # Get trends for key metrics
        metrics_to_show = [
            (MetricType.SNR, "SNR (dB)"),
            (MetricType.RSSI, "RSSI (dBm)"),
            (MetricType.HOPS, "Hops"),
            (MetricType.ANNOUNCE_RATE, "Announces"),
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

        self.dialog.msgbox("Edge Metrics", "\n".join(lines))

    def _metrics_recent(self):
        """Show recent metric values."""
        history = self._get_metrics_history()

        if history is None:
            self.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return

        # Get recent metrics
        points = history.get_recent(hours=1, limit=50)

        if not points:
            self.dialog.msgbox("No Data", "No recent metrics found.")
            return

        lines = [
            "RECENT METRICS (Last Hour)",
            "=" * 60,
            "",
        ]

        for point in reversed(points[-20:]):  # Show last 20
            time_str = point.timestamp.strftime('%H:%M:%S')
            metric = point.metric_type.value
            value = point.value

            target = ""
            if point.node_id:
                target = f" [{point.node_id[:15]}]"
            elif point.edge_id:
                target = f" [{point.edge_id[:20]}]"

            lines.append(f"{time_str} | {metric:<15} | {value:>10.2f}{target}")

        lines.append("")
        lines.append(f"Total points in last hour: {len(points)}")

        self.dialog.msgbox("Recent Metrics", "\n".join(lines))

    def _metrics_export(self):
        """Export metrics to CSV."""
        history = self._get_metrics_history()

        if history is None:
            self.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return

        # Select export period
        period_choices = [
            ("24", "Last 24 hours"),
            ("168", "Last 7 days"),
            ("720", "Last 30 days"),
            ("back", "Back"),
        ]

        period_choice = self.dialog.menu(
            "Export Period",
            "Select time period to export:",
            period_choices
        )

        if not period_choice or period_choice == "back":
            return

        try:
            hours = float(period_choice)
        except ValueError:
            return

        # Default export path
        export_dir = get_real_user_home() / ".cache" / "meshforge"

        export_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        export_path = export_dir / f"metrics_export_{timestamp}.csv"

        self.dialog.infobox("Exporting...", f"Exporting metrics to CSV...")

        try:
            count = history.export_csv(str(export_path), hours=hours)

            self.dialog.msgbox(
                "Export Complete",
                f"Exported {count:,} metric points.\n\n"
                f"File: {export_path}"
            )

        except Exception as e:
            self.dialog.msgbox("Error", f"Export failed:\n{e}")

    # Class-level storage for prometheus server state
    _prometheus_server = None
    _prometheus_port = 9090

    def _metrics_prometheus(self):
        """Prometheus metrics server menu."""
        while True:
            # Check server status
            server_running = self._prometheus_server is not None
            port = self._prometheus_port

            if server_running:
                status = f"[RUNNING on port {port}]"
            else:
                status = "[STOPPED]"

            choices = []
            if server_running:
                choices.append(("stop", "Stop Server"))
                choices.append(("test", "Test Endpoint"))
            else:
                choices.append(("start", "Start Server"))
                choices.append(("port", f"Set Port (current: {port})"))

            choices.extend([
                ("curl", "Show curl Command"),
                ("back", "Back"),
            ])

            choice = self.dialog.menu(
                "Prometheus Server",
                f"Prometheus metrics exporter:\n{status}",
                choices
            )

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
        """Start Prometheus server in background thread."""
        if not _HAS_METRICS_EXPORT:
            self.dialog.msgbox("Error", "Prometheus exporter module not available.")
            return

        if self._prometheus_server is not None:
            self.dialog.msgbox("Already Running", "Server is already running.")
            return

        port = self._prometheus_port

        try:
            self._prometheus_server = start_metrics_server(port=port)
            self.dialog.msgbox(
                "Server Started",
                f"Prometheus metrics server started.\n\n"
                f"Port: {port}\n"
                f"Endpoint: http://localhost:{port}/metrics\n\n"
                "Server runs in background while TUI is active."
            )
        except Exception as e:
            self._prometheus_server = None
            self.dialog.msgbox("Error", f"Failed to start server:\n{e}")

    def _prometheus_stop(self):
        """Stop the Prometheus server."""
        if self._prometheus_server is None:
            self.dialog.msgbox("Not Running", "Server is not running.")
            return

        try:
            self._prometheus_server.shutdown()
            self._prometheus_server = None
            self.dialog.msgbox("Server Stopped", "Prometheus metrics server stopped.")
        except Exception as e:
            self.dialog.msgbox("Error", f"Failed to stop server:\n{e}")

    def _prometheus_test(self):
        """Test the Prometheus endpoint."""
        import subprocess

        port = self._prometheus_port
        url = f"http://localhost:{port}/metrics"

        clear_screen()
        print(f"=== Testing Prometheus Endpoint ===")
        print(f"URL: {url}\n")

        try:
            result = subprocess.run(
                ['curl', '-s', url],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                # Show first 50 lines
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
        self._wait_for_enter()

    def _prometheus_set_port(self):
        """Configure the Prometheus port."""
        port_str = self.dialog.inputbox(
            "Prometheus Port",
            "Enter port for Prometheus metrics server:",
            str(self._prometheus_port)
        )

        if not port_str:
            return

        try:
            port = int(port_str)
            if not (1024 <= port <= 65535):
                raise ValueError("Port must be between 1024 and 65535")
            self._prometheus_port = port
        except ValueError as e:
            self.dialog.msgbox("Invalid Port", str(e))

    def _prometheus_show_curl(self):
        """Show curl command for scraping."""
        port = self._prometheus_port
        self.dialog.msgbox(
            "Prometheus Scrape",
            f"To test the metrics endpoint:\n\n"
            f"  curl http://localhost:{port}/metrics\n\n"
            f"Prometheus scrape config:\n\n"
            f"  - job_name: 'meshforge'\n"
            f"    static_configs:\n"
            f"      - targets: ['localhost:{port}']"
        )

    def _metrics_cleanup(self):
        """Database maintenance options."""
        history = self._get_metrics_history()

        if history is None:
            self.dialog.msgbox("Unavailable", "Metrics history module not loaded.")
            return

        stats = history.get_statistics()

        lines = [
            "DATABASE MAINTENANCE",
            "=" * 50,
            "",
            f"Current raw points: {stats['raw_points']:,}",
            f"Hourly aggregates: {stats['hourly_aggregates']:,}",
            f"Retention period: {stats['retention_days']} days",
            "",
            "Note: Automatic cleanup runs hourly.",
            "Data older than 24 hours is aggregated.",
            f"Data older than {stats['retention_days']} days is deleted.",
        ]

        self.dialog.msgbox("Database Maintenance", "\n".join(lines))

        if self.dialog.yesno(
            "Run Cleanup",
            "Run cleanup and aggregation now?",
            default_no=True
        ):
            self.dialog.infobox("Processing...", "Running cleanup...")

            try:
                history._perform_cleanup()
                history._aggregate_old_data()

                self.dialog.msgbox(
                    "Cleanup Complete",
                    "Database maintenance completed successfully."
                )

            except Exception as e:
                self.dialog.msgbox("Error", f"Cleanup failed:\n{e}")

    # =========================================================================
    # Grafana Dashboard Management
    # =========================================================================

    def _grafana_menu(self):
        """Grafana dashboards menu."""
        import subprocess
        import shutil
        from pathlib import Path

        # Check Grafana status
        grafana_running = False
        grafana_url = "http://localhost:3000"
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'grafana-server'],
                capture_output=True, text=True, timeout=5
            )
            grafana_running = result.stdout.strip() == 'active'
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Grafana status check failed: %s", e)

        # Find dashboards directory
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
                ("status", "Grafana Status"),
                ("open", "Open Grafana (browser)"),
                ("dashboards", "View Dashboard Files"),
                ("install", "Install Grafana"),
                ("import", "Import Dashboard Instructions"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Grafana Dashboards",
                "\n".join(status_lines),
                choices
            )

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
        """Show detailed Grafana status."""
        import subprocess
        import shutil

        lines = ["GRAFANA STATUS", "=" * 50, ""]

        # Check service
        try:
            result = subprocess.run(
                ['systemctl', 'status', 'grafana-server'],
                capture_output=True, text=True, timeout=10
            )
            # Extract key lines
            for line in result.stdout.split('\n')[:10]:
                lines.append(line)
        except FileNotFoundError:
            lines.append("systemctl not available")
        except Exception as e:
            lines.append(f"Error checking status: {e}")

        # Check if installed
        lines.append("")
        if shutil.which('grafana-server'):
            lines.append("grafana-server: INSTALLED")
        else:
            lines.append("grafana-server: NOT FOUND")
            lines.append("")
            lines.append("Install with: sudo apt install grafana")

        self.dialog.msgbox("Grafana Status", "\n".join(lines))

    def _grafana_open(self, url: str):
        """Open Grafana in browser."""
        import subprocess
        import threading

        def open_browser():
            try:
                subprocess.run(['xdg-open', url], timeout=10)
            except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
                logger.debug("Failed to open Grafana URL: %s", e)

        threading.Thread(target=open_browser, daemon=True).start()
        self.dialog.msgbox(
            "Opening Grafana",
            f"Opening {url} in browser...\n\n"
            f"Default login:\n"
            f"  Username: admin\n"
            f"  Password: admin"
        )

    def _grafana_list_dashboards(self, dashboard_files):
        """Show available dashboard files."""
        from pathlib import Path

        if not dashboard_files:
            self.dialog.msgbox("No Dashboards", "No MeshForge dashboard files found.")
            return

        lines = ["AVAILABLE DASHBOARDS", "=" * 50, ""]
        for f in dashboard_files:
            lines.append(f"  {f.name}")

        lines.append("")
        lines.append(f"Location: {dashboard_files[0].parent}")
        lines.append("")
        lines.append("Import these via Grafana UI:")
        lines.append("  Dashboards > Import > Upload JSON")

        self.dialog.msgbox("Dashboard Files", "\n".join(lines))

    def _grafana_install(self):
        """Show Grafana installation instructions."""
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
        self.dialog.msgbox("Install Grafana", instructions)

    def _grafana_import_instructions(self, dashboard_files):
        """Show how to import dashboards."""
        from pathlib import Path

        if not dashboard_files:
            dash_path = "dashboards/"
        else:
            dash_path = str(dashboard_files[0].parent)

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
        self.dialog.msgbox("Import Instructions", instructions)
