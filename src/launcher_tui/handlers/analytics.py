"""
Analytics Handler — Coverage trends, link budget history, predictive alerts.

Converted from analytics_mixin.py as part of the mixin-to-registry migration.
"""

import logging

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

get_analytics_store, get_predictive_analyzer, get_coverage_analyzer, _HAS_ANALYTICS = safe_import(
    'utils.analytics', 'get_analytics_store', 'get_predictive_analyzer', 'get_coverage_analyzer'
)


class AnalyticsHandler(BaseHandler):
    """TUI handler for analytics display methods."""

    handler_id = "analytics"
    menu_section = "dashboard"

    def menu_items(self):
        return [
            ("analytics", "Analytics           Coverage & link trends", None),
        ]

    def execute(self, action):
        if action == "analytics":
            self._analytics_menu()

    def _analytics_menu(self):
        """Analytics — coverage trends, link budget, predictions."""
        while True:
            choices = [
                ("trends", "Link Trends         Link budget over time"),
                ("health", "Health History       Network health timeline"),
                ("forecast", "Forecast            24h network forecast"),
                ("alerts", "Predictive Alerts   Predicted issues"),
                ("coverage", "Coverage Stats      Area & spacing analysis"),
                ("cleanup", "Cleanup Old Data    Purge records > 30d"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Analytics",
                "Historical analysis and predictions:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "trends": ("Link Trends", self._show_link_trends),
                "health": ("Health History", self._show_health_history),
                "forecast": ("Network Forecast", self._show_network_forecast),
                "alerts": ("Predictive Alerts", self._show_predictive_alerts),
                "coverage": ("Coverage Stats", self._show_coverage_stats),
                "cleanup": ("Cleanup Old Data", self._analytics_cleanup),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _show_link_trends(self):
        """Show link budget trends over time."""
        clear_screen()
        print("=== Link Budget Trends ===\n")

        if not _HAS_ANALYTICS:
            print("  Analytics module not available.")
            print("  File: src/utils/analytics.py")
            self.ctx.wait_for_enter()
            return

        store = get_analytics_store()
        history = store.get_link_budget_history(hours=24)

        if not history:
            print("  No link budget data recorded yet.")
            print("  Data is collected when nodes exchange packets.")
            self.ctx.wait_for_enter()
            return

        print(f"  Samples in last 24h: {len(history)}\n")
        print(f"  {'Time':<20} {'Source':<12} {'Dest':<12} {'RSSI':>6} {'SNR':>6} {'Quality':<10}")
        print(f"  {'-'*68}")

        for sample in history[-15:]:
            ts = sample.timestamp[:19] if len(sample.timestamp) > 19 else sample.timestamp
            src = sample.source_node[:10] if sample.source_node else "?"
            dst = sample.dest_node[:10] if sample.dest_node else "?"
            print(f"  {ts:<20} {src:<12} {dst:<12} {sample.rssi_dbm:>5.0f} {sample.snr_db:>5.1f} {sample.link_quality:<10}")

        if len(history) > 15:
            print(f"\n  (showing 15 of {len(history)} — oldest omitted)")

        print()
        self.ctx.wait_for_enter()

    def _show_health_history(self):
        """Show network health metrics over time."""
        clear_screen()
        print("=== Network Health History ===\n")

        if not _HAS_ANALYTICS:
            print("  Analytics module not available.")
            self.ctx.wait_for_enter()
            return

        store = get_analytics_store()
        history = store.get_network_health_history(hours=24)

        if not history:
            print("  No health history recorded yet.")
            print("  Health snapshots are stored when the system runs.")
            self.ctx.wait_for_enter()
            return

        print(f"  Snapshots in last 24h: {len(history)}\n")
        print(f"  {'Time':<20} {'Online':>6} {'Offline':>7} {'Avg RSSI':>9} {'Avg SNR':>8} {'Pkt %':>6}")
        print(f"  {'-'*58}")

        for metric in history[-15:]:
            ts = metric.timestamp[:19] if len(metric.timestamp) > 19 else metric.timestamp
            print(f"  {ts:<20} {metric.online_nodes:>6} {metric.offline_nodes:>7} "
                  f"{metric.avg_rssi_dbm:>8.0f} {metric.avg_snr_db:>7.1f} {metric.packet_success_rate * 100:>5.0f}%")

        if len(history) > 15:
            print(f"\n  (showing 15 of {len(history)} — oldest omitted)")

        print()
        self.ctx.wait_for_enter()

    def _show_network_forecast(self):
        """Show 24-hour network forecast."""
        clear_screen()
        print("=== Network Forecast (24h) ===\n")

        if not _HAS_ANALYTICS:
            print("  Predictive analytics module not available.")
            self.ctx.wait_for_enter()
            return

        analyzer = get_predictive_analyzer()
        forecast = analyzer.get_network_forecast(hours_ahead=24)

        if not forecast:
            print("  Insufficient data for forecast.")
            print("  Need at least 24h of collected metrics.")
            self.ctx.wait_for_enter()
            return

        outlook = forecast.get('outlook', 'unknown')
        confidence = forecast.get('confidence', 0)

        if outlook == 'stable':
            color = "\033[0;32m"
        elif outlook == 'declining':
            color = "\033[0;33m"
        else:
            color = "\033[0;31m"

        print(f"  Outlook:    {color}{outlook}\033[0m")
        print(f"  Confidence: {confidence:.0%}\n")

        pred = forecast.get('predicted_metrics', {})
        if pred:
            print("  Predicted next 24h:")
            for key, val in pred.items():
                label = key.replace('_', ' ').title()
                if isinstance(val, float):
                    print(f"    {label:<25} {val:.1f}")
                else:
                    print(f"    {label:<25} {val}")

        risks = forecast.get('risk_factors', [])
        if risks:
            print(f"\n  Risk Factors ({len(risks)}):")
            for risk in risks:
                print(f"    \033[0;33m!\033[0m {risk}")

        print()
        self.ctx.wait_for_enter()

    def _show_predictive_alerts(self):
        """Show predictive alerts — issues predicted before they happen."""
        clear_screen()
        print("=== Predictive Alerts ===\n")

        if not _HAS_ANALYTICS:
            print("  Predictive analytics module not available.")
            self.ctx.wait_for_enter()
            return

        analyzer = get_predictive_analyzer()
        alerts = analyzer.analyze_all()

        if not alerts:
            print("  No predicted issues.")
            print("  System looks healthy based on available data.")
            self.ctx.wait_for_enter()
            return

        severity_colors = {
            'critical': "\033[1;31m",
            'warning': "\033[0;33m",
            'info': "\033[0;36m",
        }

        print(f"  {len(alerts)} predicted issue(s):\n")
        for alert in alerts:
            color = severity_colors.get(alert.severity, "")
            reset = "\033[0m"
            eta = f"~{alert.predicted_time_hours:.0f}h" if alert.predicted_time_hours else "soon"
            conf = f"{alert.confidence:.0%}" if alert.confidence else "?"

            print(f"  {color}[{alert.severity.upper()}]{reset} {alert.message}")
            print(f"           ETA: {eta}  Confidence: {conf}")
            if alert.suggestions:
                for suggestion in alert.suggestions[:2]:
                    print(f"           -> {suggestion}")
            print()

        self.ctx.wait_for_enter()

    def _show_coverage_stats(self):
        """Show coverage area statistics."""
        clear_screen()
        print("=== Coverage Statistics ===\n")

        if not _HAS_ANALYTICS:
            print("  Coverage analyzer module not available.")
            self.ctx.wait_for_enter()
            return

        analyzer = get_coverage_analyzer()
        history = analyzer.get_coverage_history(days=7)

        if not history:
            print("  No coverage data recorded yet.")
            print("  Coverage is calculated when GPS-enabled nodes report positions.")
            self.ctx.wait_for_enter()
            return

        latest = history[-1]
        print(f"  Total nodes:          {latest.get('total_nodes', 'N/A')}")
        print(f"  Nodes with GPS:       {latest.get('nodes_with_position', 'N/A')}")
        print(f"  Estimated area:       {latest.get('estimated_area_km2', 0):.1f} km2")
        print(f"  Avg node spacing:     {latest.get('average_node_spacing_km', 0):.2f} km")
        print(f"  Coverage radius:      {latest.get('coverage_radius_km', 0):.2f} km")

        if len(history) > 1:
            first = history[0]
            area_change = latest.get('estimated_area_km2', 0) - first.get('estimated_area_km2', 0)
            node_change = latest.get('total_nodes', 0) - first.get('total_nodes', 0)
            print(f"\n  7-day change:")
            sign = "+" if area_change >= 0 else ""
            print(f"    Area:  {sign}{area_change:.1f} km2")
            sign = "+" if node_change >= 0 else ""
            print(f"    Nodes: {sign}{node_change}")

        print()
        self.ctx.wait_for_enter()

    def _analytics_cleanup(self):
        """Purge analytics data older than 30 days."""
        clear_screen()
        print("=== Cleanup Analytics Data ===\n")

        if not _HAS_ANALYTICS:
            print("  Analytics module not available.")
            self.ctx.wait_for_enter()
            return

        confirm = self.ctx.dialog.yesno(
            "Confirm Cleanup",
            "Delete analytics data older than 30 days?\n\n"
            "This removes old link budget samples, health snapshots,\n"
            "and coverage records. Recent data is preserved."
        )

        if not confirm:
            return

        store = get_analytics_store()
        store.cleanup_old_data(days=30)
        print("  Cleanup complete. Data older than 30 days removed.")
        print()
        self.ctx.wait_for_enter()
