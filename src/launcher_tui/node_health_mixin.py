"""
Node Health Mixin — Battery forecasting, signal trending, latency monitoring.

Wires the following utility modules into the TUI:
- utils.predictive_maintenance (MaintenancePredictor)
- utils.signal_trending (SignalTrend)
- utils.latency_monitor (LatencyMonitor, probe_tcp)

Provides menu methods callable from Dashboard and Maps submenus.
"""

import logging
import subprocess
import time
from backend import clear_screen
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# --- Optional dependencies (module-level safe_import) ---
probe_tcp, DEFAULT_SERVICES, _HAS_LATENCY = safe_import(
    'utils.latency_monitor', 'probe_tcp', 'DEFAULT_SERVICES'
)
MaintenancePredictor, _HAS_PREDICTOR = safe_import(
    'utils.predictive_maintenance', 'MaintenancePredictor'
)
SignalTrend, SignalTrendingManager, _HAS_SIGNAL = safe_import(
    'utils.signal_trending', 'SignalTrend', 'SignalTrendingManager'
)
get_http_client, _HAS_HTTP = safe_import(
    'utils.meshtastic_http', 'get_http_client'
)

# TCP fallback removed (Issue #17/#29 — single-client contention).
# HTTP API provides the same data without competing for meshtasticd's TCP slot.


class NodeHealthMixin:
    """TUI mixin for node health analysis features."""

    def _node_health_menu(self):
        """Node health analysis submenu."""
        while True:
            choices = [
                ("latency", "Service Latency     TCP probe all services"),
                ("battery", "Battery Forecast    Node battery projections"),
                ("signal", "Signal Trends       SNR/RSSI analysis"),
                ("back", "Back"),
            ]

            choice = self.dialog.menu(
                "Node Health",
                "Proactive health monitoring and prediction:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "latency": ("Service Latency", self._service_latency_probe),
                "battery": ("Battery Forecast", self._battery_forecast_display),
                "signal": ("Signal Trends", self._signal_trending_display),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _service_latency_probe(self):
        """Probe all NOC services and display latency/health."""
        clear_screen()
        print("=== Service Latency Probe ===\n")
        print("Probing services (2s timeout each)...\n")

        if not _HAS_LATENCY:
            print("  Latency monitor module not available.")
            print("  File: src/utils/latency_monitor.py")
            self._wait_for_enter()
            return

        results = []
        for name, host, port in DEFAULT_SERVICES:
            success, rtt_ms = probe_tcp(host, port, timeout=2.0)
            results.append((name, host, port, success, rtt_ms))

            if success:
                # Color by latency
                if rtt_ms < 10:
                    color = "\033[0;32m"  # green
                    label = "HEALTHY"
                elif rtt_ms < 100:
                    color = "\033[0;33m"  # yellow
                    label = "OK"
                else:
                    color = "\033[0;31m"  # red
                    label = "SLOW"
                print(f"  {color}{label:8s}\033[0m {name:<22} {rtt_ms:>7.1f}ms  ({host}:{port})")
            else:
                print(f"  \033[0;31m{'DOWN':8s}\033[0m {name:<22} {'---':>7}    ({host}:{port})")

        # Summary
        up_count = sum(1 for r in results if r[3])
        down_count = len(results) - up_count
        avg_rtt = 0.0
        up_results = [r for r in results if r[3]]
        if up_results:
            avg_rtt = sum(r[4] for r in up_results) / len(up_results)

        print(f"\n{'='*50}")
        print(f"  Services: {up_count} up, {down_count} down")
        if up_results:
            print(f"  Avg RTT:  {avg_rtt:.1f}ms")

        if down_count > 0:
            print("\n  Down services may need to be started:")
            for name, host, port, success, _ in results:
                if not success:
                    print(f"    sudo systemctl start {name.split('_')[0]}")

        print()
        self._wait_for_enter()

    def _battery_forecast_display(self):
        """Show battery forecasts with drain rates and maintenance recommendations."""
        clear_screen()
        print("=== Battery Forecast & Maintenance ===\n")

        if not _HAS_PREDICTOR:
            print("  Predictive maintenance module not available.")
            print("  File: src/utils/predictive_maintenance.py")
            self._wait_for_enter()
            return

        # Try to get node data from meshtastic
        print("Querying node telemetry...\n")
        nodes = self._get_meshtastic_node_telemetry()

        if not nodes:
            print("  No node battery data available.\n")
            print("  Battery forecasting requires telemetry data from nodes.")
            print("  Ensure meshtasticd is running and nodes report telemetry.")
            self._wait_for_enter()
            return

        predictor = MaintenancePredictor()

        # Feed samples and show forecasts
        for node_id, data in nodes.items():
            battery_pct = data.get('battery_level')
            voltage = data.get('voltage')
            if battery_pct is not None:
                predictor.record_battery(node_id, battery_pct, voltage=voltage)

        # Display battery status table
        print(f"  {'Node':<16} {'Battery':>8} {'Voltage':>8} {'Drain Rate':>11} {'Critical In':>12} {'Status':<12}")
        print(f"  {'-'*71}")

        for node_id, data in nodes.items():
            battery_pct = data.get('battery_level')
            voltage = data.get('voltage')
            name = data.get('short_name', node_id[:12])

            if battery_pct is None:
                print(f"  {name:<16} {'---':>8}")
                continue

            # Get forecast for drain rate and time-to-critical
            forecast = predictor.get_battery_forecast(node_id)
            if forecast.trend == 'draining' and forecast.drain_rate_pct_per_hour > 0:
                drain_str = f"{forecast.drain_rate_pct_per_hour:.2f}%/h"
            elif forecast.trend == 'charging':
                drain_str = "charging"
            elif forecast.trend == 'stable':
                drain_str = "stable"
            else:
                drain_str = "---"

            if forecast.hours_to_critical is not None and forecast.hours_to_critical < 999:
                if forecast.hours_to_critical < 1:
                    crit_str = f"{forecast.hours_to_critical * 60:.0f}min"
                else:
                    crit_str = f"{forecast.hours_to_critical:.0f}h"
            else:
                crit_str = "---"

            # Color code battery level
            if battery_pct > 50:
                color = "\033[0;32m"  # green
                status = "Good"
            elif battery_pct > 30:
                color = "\033[0;33m"  # yellow
                status = "Warning"
            elif battery_pct > 15:
                color = "\033[0;31m"  # red
                status = "Critical"
            else:
                color = "\033[0;31m"
                status = "SHUTDOWN RISK"

            v_str = f"{voltage:.2f}V" if voltage else "---"
            print(f"  {name:<16} {color}{battery_pct:>6.1f}%\033[0m {v_str:>8} {drain_str:>11} {crit_str:>12} {status:<12}")

        # Show maintenance recommendations
        recs = predictor.get_maintenance_recommendations()
        if recs:
            priority_colors = {
                'urgent': '\033[1;31m',   # bold red
                'soon': '\033[0;33m',     # yellow
                'scheduled': '\033[0;34m', # blue
                'monitor': '\033[2m',     # dim
            }
            print(f"\n  Maintenance Recommendations ({len(recs)}):")
            print(f"  {'-'*50}")
            for rec in recs[:10]:
                color = priority_colors.get(rec.priority, '')
                print(f"  {color}[{rec.priority.upper():>9}]\033[0m {rec.node_id}: {rec.action}")
                print(f"             Reason: {rec.reason}")

        print(f"\n  Note: Accurate forecasts require multiple samples over time.")
        print(f"  Battery drain rates calculated after 3+ readings.")
        print()
        self._wait_for_enter()

    def _get_meshtastic_node_telemetry(self):
        """Get node telemetry via meshtasticd HTTP API.

        Returns dict of node_id -> {battery_level, voltage, short_name, ...}
        """
        nodes = {}

        # Primary: meshtasticd HTTP API (no TCP lock, no Python lib needed)
        if _HAS_HTTP:
            try:
                client = get_http_client()
                if client.is_available:
                    report = client.get_report()
                    http_nodes = client.get_nodes()
                    # Device report has battery for the local node
                    if report and report.has_battery:
                        nodes['local'] = {
                            'battery_level': report.battery_percent,
                            'voltage': report.battery_voltage_mv / 1000.0 if report.battery_voltage_mv else None,
                            'short_name': 'Local',
                            'long_name': 'Local Device',
                        }
                    # Node list has per-node data
                    for node in http_nodes:
                        # HTTP API doesn't expose per-node battery yet,
                        # but we get the node list for signal/position data
                        nodes[node.node_id] = {
                            'battery_level': None,
                            'voltage': None,
                            'short_name': node.short_name or node.node_id[:8],
                            'long_name': node.long_name or '',
                            'snr': node.snr,
                        }
                    if nodes:
                        return nodes
            except Exception as e:
                logger.debug(f"HTTP API telemetry query failed: {e}")
        else:
            logger.debug("meshtastic_http module not available")

        # TCP fallback removed: direct TCPInterface creation contends with
        # meshtasticd's single TCP slot, breaking the web client at :9443.
        # HTTP API above provides the same telemetry data without contention.
        # See persistent_issues.md Issue #17 and #29.

        return nodes

    def _signal_trending_display(self):
        """Show signal trend analysis for nodes with stability scoring."""
        clear_screen()
        print("=== Signal Trending Analysis ===\n")

        if not _HAS_SIGNAL:
            print("  Signal trending module not available.")
            print("  File: src/utils/signal_trending.py")
            self._wait_for_enter()
            return

        # Try to get node signal data
        print("Querying node signal data...\n")
        nodes = self._get_meshtastic_node_signals()

        if not nodes:
            print("  No signal data available.\n")
            print("  Signal trending requires SNR/RSSI data from nodes.")
            print("  Ensure meshtasticd is running and nodes are in range.")
            self._wait_for_enter()
            return

        # Feed data into trending manager for analysis
        manager = SignalTrendingManager()
        now = time.time()
        for node_id, data in nodes.items():
            snr = data.get('snr')
            rssi = data.get('rssi')
            if snr is not None or rssi is not None:
                manager.add_sample(node_id, now, snr=snr, rssi=rssi)

        # Display signal table with trend info
        print(f"  {'Node':<16} {'SNR':>8} {'RSSI':>8} {'Hops':>5} {'Stability':>10} {'Trend':>12}")
        print(f"  {'-'*63}")

        for node_id, data in nodes.items():
            name = data.get('short_name', node_id[:12])
            snr = data.get('snr')
            rssi = data.get('rssi')
            hops = data.get('hops_away', '?')

            snr_str = f"{snr:.1f}dB" if snr is not None else "---"
            rssi_str = f"{rssi}dBm" if rssi is not None else "---"

            # Get report for trend/stability info
            report = manager.get_report(node_id)
            if report and report.stability_score >= 0:
                stab_score = report.stability_score
                if stab_score >= 80:
                    stab_color = "\033[0;32m"
                elif stab_score >= 50:
                    stab_color = "\033[0;33m"
                else:
                    stab_color = "\033[0;31m"
                stab_str = f"{stab_color}{stab_score:>3}/100\033[0m"
                trend_str = report.trend_direction
            else:
                stab_str = "   ---"
                trend_str = "---"

            # Color code SNR
            if snr is not None:
                if snr > 5:
                    color = "\033[0;32m"  # green
                elif snr > 0:
                    color = "\033[0;33m"  # yellow
                else:
                    color = "\033[0;31m"  # red
            else:
                color = "\033[2m"  # dim

            hops_str = str(hops) if hops is not None else "?"
            print(f"  {name:<16} {color}{snr_str:>8}\033[0m {rssi_str:>8} {hops_str:>5} {stab_str:>10} {trend_str:>12}")

        # Summary: degrading and unstable nodes
        degrading = manager.get_degrading_nodes()
        if degrading:
            print(f"\n  \033[0;33mWarning:\033[0m {len(degrading)} node(s) with degrading signal:")
            for r in degrading[:5]:
                rate = f"{r.trend_rate_db_per_hour:.2f} dB/hr" if r.trend_rate_db_per_hour else ""
                print(f"    {r.node_id}: {rate}")

        unstable = manager.get_unstable_nodes(threshold=40)
        if unstable:
            print(f"\n  \033[0;31mAlert:\033[0m {len(unstable)} node(s) with low stability (<40/100):")
            for r in unstable[:5]:
                print(f"    {r.node_id}: stability {r.stability_score}/100")

        print(f"\n  Note: Full trend analysis requires multiple observations.")
        print(f"  Re-run periodically to build signal history.")
        print()
        self._wait_for_enter()

    def _get_meshtastic_node_signals(self):
        """Get SNR/RSSI data via meshtasticd HTTP API.

        Returns dict of node_id -> {snr, rssi, short_name, hops_away}
        """
        nodes = {}

        # Primary: meshtasticd HTTP API
        if _HAS_HTTP:
            try:
                client = get_http_client()
                if client.is_available:
                    for node in client.get_nodes():
                        if node.snr:
                            nodes[node.node_id] = {
                                'snr': node.snr,
                                'rssi': None,  # HTTP /json/nodes doesn't expose RSSI
                                'short_name': node.short_name or node.node_id[:8],
                                'hops_away': None,
                            }
                    if nodes:
                        return nodes
            except Exception as e:
                logger.debug(f"HTTP API signal query failed: {e}")
        else:
            logger.debug("meshtastic_http module not available")

        # TCP fallback removed: direct TCPInterface creation contends with
        # meshtasticd's single TCP slot, breaking the web client at :9443.
        # HTTP API above provides the same signal data without contention.
        # See persistent_issues.md Issue #17 and #29.

        return nodes
