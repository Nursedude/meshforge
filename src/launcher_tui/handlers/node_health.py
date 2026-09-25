"""
Node Health Handler — Battery forecasting, signal trending, latency monitoring.

Converted from node_health_mixin.py as part of the mixin-to-registry migration.
"""

import logging

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

probe_tcp, DEFAULT_SERVICES, ProbeUnobservable, _HAS_LATENCY = safe_import(
    'utils.latency_monitor', 'probe_tcp', 'DEFAULT_SERVICES', 'ProbeUnobservable'
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
            else:
                self.ctx.notify_unwired(choice, "NodeHealthHandler._node_health_menu")

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
            try:
                success, rtt_ms = probe_tcp(host, port, timeout=2.0)
            except ProbeUnobservable as e:
                print(f"  \033[2m{'UNKNOWN':8s}\033[0m {name:<22} {'---':>7}    "
                      f"({host}:{port}) — {e}")
                continue
            results.append((name, host, port, success, rtt_ms))

            # A TCP connect proves a LISTENER, not a working service
            # (meshtasticd binds :9443 with or without a working web client,
            # 2026-08-11) — so this says OPEN, never "HEALTHY" (2026-09-23).
            if success:
                if rtt_ms < 10:
                    color = "\033[0;32m"
                    label = "OPEN"
                elif rtt_ms < 100:
                    color = "\033[0;33m"
                    label = "OPEN"
                else:
                    color = "\033[0;31m"
                    label = "SLOW"
                print(f"  {color}{label:8s}\033[0m {name:<22} {rtt_ms:>7.1f}ms  ({host}:{port})")
            else:
                print(f"  \033[0;31m{'CLOSED':8s}\033[0m {name:<22} {'---':>7}    ({host}:{port})")

        up_count = sum(1 for r in results if r[3])
        down_count = len(results) - up_count
        unknown_count = len(DEFAULT_SERVICES) - len(results)
        up_results = [r for r in results if r[3]]
        avg_rtt = sum(r[4] for r in up_results) / len(up_results) if up_results else 0.0

        try:
            from utils.latency_monitor import NOT_TCP_PROBED
        except ImportError:
            NOT_TCP_PROBED = ()
        for name, why in NOT_TCP_PROBED:
            print(f"  {'--':8s} {name:<22} {'':>7}    not TCP-probed: {why}")

        print(f"\n{'='*50}")
        print(f"  Ports: {up_count} accepting, {down_count} not accepting"
              + (f", {unknown_count} UNKNOWN (probe could not be made)" if unknown_count else ""))
        print("  A port that accepts proves something is LISTENING there, not that")
        print("  the service works. A closed port may be off by design on this box.")
        if up_results:
            print(f"  Avg RTT:  {avg_rtt:.1f}ms")

        if down_count > 0:
            print("\n  Some probed ports are not accepting connections.")
            # In-Domain Principle: offer to fix the down LOCAL services this box
            # runs, right here — no shell-escape. collect_degraded_services
            # re-checks via systemd (the SSOT, authoritative over a raw TCP
            # probe), and the chooser is profile-gated so intentionally-off
            # services aren't nagged. (The mqtt probe maps to the mosquitto unit.)
            from service_remediation import (
                collect_degraded_services, offer_service_fix_chooser,
            )
            degraded = collect_degraded_services(
                ["meshtasticd", "rnsd", "mosquitto"])
            if offer_service_fix_chooser(self.ctx, degraded):
                return  # operator engaged the fix chooser; no extra wait

        print()
        self.ctx.wait_for_enter()

    # Battery Forecast and Signal Trends used to ask meshtasticd's HTTP API
    # for /json/report and /json/nodes — ESP32-only, NEVER served by
    # meshtasticd (#76) — so on every fleet box both screens said "No node
    # data available ... Ensure meshtasticd is running" about a daemon that
    # was fine; and with data they took ONE sample "now", which cannot be a
    # trend. Analytics already measures both from node_history.db (the map
    # collector's 48 h of snapshots). Same menu items, real data (2026-09-23).
    def _analytics(self):
        from handlers.analytics import AnalyticsHandler
        a = AnalyticsHandler()
        a.ctx = self.ctx
        return a

    def _battery_forecast_display(self):
        """Battery slope + ETA to 20 % per node — Analytics' Predictive Alerts."""
        self._analytics()._show_predictive_alerts()

    def _signal_trending_display(self):
        """SNR trend per node over 48 h — Analytics' Link Trends."""
        self._analytics()._show_link_trends()
