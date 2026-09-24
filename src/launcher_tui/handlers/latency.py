"""
Latency Handler — Service latency monitoring, health status, degradation alerts.

Converted from latency_mixin.py as part of the mixin-to-registry migration.
"""

import logging
import time

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.latency_monitor import get_latency_monitor

logger = logging.getLogger(__name__)


class LatencyHandler(BaseHandler):
    """TUI handler for latency monitoring display methods."""

    handler_id = "latency"
    menu_section = "dashboard"

    def menu_items(self):
        return [
            ("latency", "Latency Monitor     Service response times", None),
        ]

    def execute(self, action):
        if action == "latency":
            self._latency_menu()

    def _latency_menu(self):
        """Latency Monitor — service response times and health."""
        while True:
            choices = [
                ("status", "Service Latency     Current RTT for all services"),
                ("probe", "Probe Now           Run one probe cycle"),
                ("degraded", "Degraded Services   Show unhealthy services"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Latency Monitor",
                "Service response time monitoring:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "status": ("Service Latency", self._show_latency_status),
                "probe": ("Probe Now", self._latency_probe_now),
                "degraded": ("Degraded Services", self._show_degraded_services),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
            else:
                self.ctx.notify_unwired(choice, "LatencyHandler._latency_menu")

    def _show_latency_status(self):
        """Show current latency for all monitored services."""
        clear_screen()
        print("=== Service Latency Status ===\n")

        monitor = get_latency_monitor(auto_start=False)
        summary = monitor.get_summary()

        if not summary:
            print("  No services configured for monitoring.")
            self.ctx.wait_for_enter()
            return

        # UNKNOWN because the probe cannot be MADE is not "no data yet"
        # (review B 2026-09-23: an EMFILE box read "No probe data yet").
        unobservable = [s for s in summary if s.get('unobservable_since') is not None]
        has_data = any(s.get('status') != 'UNKNOWN' for s in summary)
        if not has_data and not unobservable:
            print("  No probe data yet. Use 'Probe Now' to run a check.\n")
        for s in unobservable:
            print(f"  {s.get('name', '?')}: UNKNOWN — cannot probe {s.get('host')}:{s.get('port')} "
                  f"(the monitor could not open a socket); its numbers are history, not the present.")
        if unobservable:
            print()

        status_colors = {
            'HEALTHY': "\033[0;32m",
            'DEGRADED': "\033[0;33m",
            'DOWN': "\033[0;31m",
            'UNKNOWN': "\033[2m",
        }

        print(f"  {'Service':<22} {'Status':<10} {'RTT':>8} {'Jitter':>8} {'Loss':>6}")
        print(f"  {'-'*56}")

        for svc in summary:
            name = svc.get('name', '?')
            status = svc.get('status', 'UNKNOWN')
            color = status_colors.get(status, "")
            reset = "\033[0m" if color else ""

            rtt = f"{svc.get('avg_rtt_ms', 0):.1f}ms" if status != 'UNKNOWN' else "-"
            jitter = f"{svc.get('jitter_ms', 0):.1f}ms" if status != 'UNKNOWN' else "-"
            loss = f"{svc.get('packet_loss_pct', 0):.0f}%" if status != 'UNKNOWN' else "-"

            print(f"  {name:<22} {color}{status:<10}{reset} {rtt:>8} {jitter:>8} {loss:>6}")

        print()
        self.ctx.wait_for_enter()

    def _latency_probe_now(self):
        """Run a single probe cycle and show results."""
        clear_screen()
        print("=== Probing Services ===\n")

        monitor = get_latency_monitor(auto_start=False)
        print("  Running probe cycle...\n")
        health = monitor.probe_once()

        if not health:
            print("  No services configured.")
            self.ctx.wait_for_enter()
            return

        status_indicators = {
            'HEALTHY': ("\033[0;32m", "●"),
            'DEGRADED': ("\033[0;33m", "●"),
            'DOWN': ("\033[0;31m", "●"),
            'UNKNOWN': ("\033[2m", "○"),
        }

        for name, svc in health.items():
            status = svc.status
            color, icon = status_indicators.get(status, ("\033[2m", "○"))
            reset = "\033[0m"

            # HEALTHY here means "the TCP connect succeeded" — a listener, not
            # a working service; say what was measured (2026-09-23).
            shown = "OPEN (listening)" if status == 'HEALTHY' else status
            if status == 'UNKNOWN' and svc.unobservable_since is not None:
                shown = "UNKNOWN — cannot probe (samples below are history)"
            print(f"  {color}{icon}{reset} {name:<22} {color}{shown}{reset}")
            if svc.is_reachable and status != 'UNKNOWN':
                print(f"    RTT: {svc.avg_rtt_ms:.1f}ms  Jitter: {svc.jitter_ms:.1f}ms  Loss: {svc.packet_loss_pct:.0f}%")
            elif status == 'DOWN':
                print(f"    Service not responding on {svc.host}:{svc.port}")
            print()

        self.ctx.wait_for_enter()

    def _show_degraded_services(self):
        """Show only degraded or down services."""
        clear_screen()
        print("=== Degraded / Down Services ===\n")

        monitor = get_latency_monitor(auto_start=False)
        summary = monitor.get_summary()

        # An unobservable service is not "no data yet" and not "healthy":
        # the probe could not be MADE (EMFILE etc.), so its status is UNKNOWN
        # while it holds real history (review B 2026-09-23: this pane read
        # "No probe data yet" / "All services healthy" under EMFILE).
        unobservable = [s for s in summary if s.get('unobservable_since') is not None]
        has_data = any(s.get('status') != 'UNKNOWN' for s in summary)
        if not has_data and not unobservable:
            print("  No probe data yet. Use 'Probe Now' first.")
            self.ctx.wait_for_enter()
            return

        degraded = monitor.get_degraded()

        for s in unobservable:
            print(f"  \033[2m○ UNKNOWN\033[0m  {s['name']}")
            print(f"           Cannot probe {s['host']}:{s['port']} — the monitor "
                  f"could not open a socket (since {time.strftime('%H:%M:%S', time.localtime(s['unobservable_since']))}).")
            print(f"           Not proven healthy; not proven down.\n")

        if not degraded:
            if unobservable:
                print(f"  {len(unobservable)} service(s) UNOBSERVABLE; none of the observed ones is degraded.")
            else:
                print("  \033[0;32mAll services healthy.\033[0m No degradation detected.")
            self.ctx.wait_for_enter()
            return

        print(f"  {len(degraded)} service(s) need attention:\n")

        health = monitor.get_health()
        for name in degraded:
            svc = health.get(name)
            if not svc:
                continue

            if svc.status == 'DOWN':
                print(f"  \033[0;31m● DOWN\033[0m  {name}")
                print(f"           Host: {svc.host}:{svc.port}")
                print(f"           Service is not responding to TCP probes.")
            else:
                print(f"  \033[0;33m● DEGRADED\033[0m  {name}")
                print(f"           RTT: {svc.avg_rtt_ms:.1f}ms  Jitter: {svc.jitter_ms:.1f}ms  Loss: {svc.packet_loss_pct:.0f}%")

                reasons = []
                if svc.jitter_ms > 50:
                    reasons.append(f"jitter {svc.jitter_ms:.0f}ms > 50ms threshold")
                if svc.avg_rtt_ms > 200:
                    reasons.append(f"RTT {svc.avg_rtt_ms:.0f}ms > 200ms threshold")
                if svc.packet_loss_pct > 10:
                    reasons.append(f"loss {svc.packet_loss_pct:.0f}% > 10% threshold")
                if reasons:
                    print(f"           Cause: {'; '.join(reasons)}")
            print()

        self.ctx.wait_for_enter()
