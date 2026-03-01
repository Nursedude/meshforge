"""
Node Health Handler — Battery forecasting, signal trending, latency monitoring.

Converted from node_health_mixin.py as part of the mixin-to-registry migration.
"""

import logging
import time

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

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


class NodeHealthHandler(BaseHandler):
    """TUI handler for node health analysis features."""

    handler_id = "node_health"
    menu_section = "dashboard"

    def menu_items(self):
        return [
            ("health", "Node Health         Battery, signal, latency", None),
        ]

    def execute(self, action):
        if action == "health":
            self._node_health_menu()

    def _node_health_menu(self):
        """Node health analysis submenu."""
        while True:
            choices = [
                ("latency", "Service Latency     TCP probe all services"),
                ("battery", "Battery Forecast    Node battery projections"),
                ("signal", "Signal Trends       SNR/RSSI analysis"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
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
                self.ctx.safe_call(*entry)

    def _service_latency_probe(self):
        """Probe all NOC services and display latency/health."""
        clear_screen()
        print("=== Service Latency Probe ===\n")
        print("Probing services (2s timeout each)...\n")

        if not _HAS_LATENCY:
            print("  Latency monitor module not available.")
            print("  File: src/utils/latency_monitor.py")
            self.ctx.wait_for_enter()
            return

        results = []
        for name, host, port in DEFAULT_SERVICES:
            success, rtt_ms = probe_tcp(host, port, timeout=2.0)
            results.append((name, host, port, success, rtt_ms))

            if success:
                if rtt_ms < 10:
                    color = "\033[0;32m"
                    label = "HEALTHY"
                elif rtt_ms < 100:
                    color = "\033[0;33m"
                    label = "OK"
                else:
                    color = "\033[0;31m"
                    label = "SLOW"
                print(f"  {color}{label:8s}\033[0m {name:<22} {rtt_ms:>7.1f}ms  ({host}:{port})")
            else:
                print(f"  \033[0;31m{'DOWN':8s}\033[0m {name:<22} {'---':>7}    ({host}:{port})")

        up_count = sum(1 for r in results if r[3])
        down_count = len(results) - up_count
        up_results = [r for r in results if r[3]]
        avg_rtt = sum(r[4] for r in up_results) / len(up_results) if up_results else 0.0

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
        self.ctx.wait_for_enter()

    def _battery_forecast_display(self):
        """Show battery forecasts with drain rates and maintenance recommendations."""
        clear_screen()
        print("=== Battery Forecast & Maintenance ===\n")

        if not _HAS_PREDICTOR:
            print("  Predictive maintenance module not available.")
            print("  File: src/utils/predictive_maintenance.py")
            self.ctx.wait_for_enter()
            return

        print("Querying node telemetry...\n")
        nodes = self._get_meshtastic_node_telemetry()

        if not nodes:
            print("  No node battery data available.\n")
            print("  Battery forecasting requires telemetry data from nodes.")
            print("  Ensure meshtasticd is running and nodes report telemetry.")
            self.ctx.wait_for_enter()
            return

        predictor = MaintenancePredictor()

        for node_id, data in nodes.items():
            battery_pct = data.get('battery_level')
            voltage = data.get('voltage')
            if battery_pct is not None:
                predictor.record_battery(node_id, battery_pct, voltage=voltage)

        print(f"  {'Node':<16} {'Battery':>8} {'Voltage':>8} {'Drain Rate':>11} {'Critical In':>12} {'Status':<12}")
        print(f"  {'-'*71}")

        for node_id, data in nodes.items():
            battery_pct = data.get('battery_level')
            voltage = data.get('voltage')
            name = data.get('short_name', node_id[:12])

            if battery_pct is None:
                print(f"  {name:<16} {'---':>8}")
                continue

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

            if battery_pct > 50:
                color = "\033[0;32m"
                status = "Good"
            elif battery_pct > 30:
                color = "\033[0;33m"
                status = "Warning"
            elif battery_pct > 15:
                color = "\033[0;31m"
                status = "Critical"
            else:
                color = "\033[0;31m"
                status = "SHUTDOWN RISK"

            v_str = f"{voltage:.2f}V" if voltage else "---"
            print(f"  {name:<16} {color}{battery_pct:>6.1f}%\033[0m {v_str:>8} {drain_str:>11} {crit_str:>12} {status:<12}")

        recs = predictor.get_maintenance_recommendations()
        if recs:
            priority_colors = {
                'urgent': '\033[1;31m',
                'soon': '\033[0;33m',
                'scheduled': '\033[0;34m',
                'monitor': '\033[2m',
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
        self.ctx.wait_for_enter()

    def _get_meshtastic_node_telemetry(self):
        """Get node telemetry via meshtasticd HTTP API."""
        nodes = {}

        if _HAS_HTTP:
            try:
                client = get_http_client()
                if client.is_available:
                    report = client.get_report()
                    http_nodes = client.get_nodes()
                    if report and report.has_battery:
                        nodes['local'] = {
                            'battery_level': report.battery_percent,
                            'voltage': report.battery_voltage_mv / 1000.0 if report.battery_voltage_mv else None,
                            'short_name': 'Local',
                            'long_name': 'Local Device',
                        }
                    for node in http_nodes:
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

        return nodes

    def _signal_trending_display(self):
        """Show signal trend analysis for nodes with stability scoring."""
        clear_screen()
        print("=== Signal Trending Analysis ===\n")

        if not _HAS_SIGNAL:
            print("  Signal trending module not available.")
            print("  File: src/utils/signal_trending.py")
            self.ctx.wait_for_enter()
            return

        print("Querying node signal data...\n")
        nodes = self._get_meshtastic_node_signals()

        if not nodes:
            print("  No signal data available.\n")
            print("  Signal trending requires SNR/RSSI data from nodes.")
            print("  Ensure meshtasticd is running and nodes are in range.")
            self.ctx.wait_for_enter()
            return

        manager = SignalTrendingManager()
        now = time.time()
        for node_id, data in nodes.items():
            snr = data.get('snr')
            rssi = data.get('rssi')
            if snr is not None or rssi is not None:
                manager.add_sample(node_id, now, snr=snr, rssi=rssi)

        print(f"  {'Node':<16} {'SNR':>8} {'RSSI':>8} {'Hops':>5} {'Stability':>10} {'Trend':>12}")
        print(f"  {'-'*63}")

        for node_id, data in nodes.items():
            name = data.get('short_name', node_id[:12])
            snr = data.get('snr')
            rssi = data.get('rssi')
            hops = data.get('hops_away', '?')

            snr_str = f"{snr:.1f}dB" if snr is not None else "---"
            rssi_str = f"{rssi}dBm" if rssi is not None else "---"

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

            if snr is not None:
                if snr > 5:
                    color = "\033[0;32m"
                elif snr > 0:
                    color = "\033[0;33m"
                else:
                    color = "\033[0;31m"
            else:
                color = "\033[2m"

            hops_str = str(hops) if hops is not None else "?"
            print(f"  {name:<16} {color}{snr_str:>8}\033[0m {rssi_str:>8} {hops_str:>5} {stab_str:>10} {trend_str:>12}")

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
        self.ctx.wait_for_enter()

    def _get_meshtastic_node_signals(self):
        """Get SNR/RSSI data via meshtasticd HTTP API."""
        nodes = {}

        if _HAS_HTTP:
            try:
                client = get_http_client()
                if client.is_available:
                    for node in client.get_nodes():
                        if node.snr:
                            nodes[node.node_id] = {
                                'snr': node.snr,
                                'rssi': None,
                                'short_name': node.short_name or node.node_id[:8],
                                'hops_away': None,
                            }
                    if nodes:
                        return nodes
            except Exception as e:
                logger.debug(f"HTTP API signal query failed: {e}")
        else:
            logger.debug("meshtastic_http module not available")

        return nodes
