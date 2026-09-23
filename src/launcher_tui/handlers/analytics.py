"""
Analytics Handler — network health, link trends, predictions, coverage.

Re-pointed 2026-09-23 at the LIVE node history store. The previous screens
read three `analytics.db` tables that nothing had ever written (no caller of
`record_link_budget` / `record_network_health` / `record_coverage` since the
module landed; empty or absent on all 10 boxes) while saying "Data is
collected when nodes exchange packets". Operator: the user needs these eyes —
so they now read `node_history.db`, which the map collector writes every
cycle, through `utils.node_history_analytics` (read-only).

Every screen states its source, window and what it judged, and says so
plainly when this box has no history, cannot read it, or has too little.
"""

import logging
import time

from backend import clear_screen
from handler_protocol import BaseHandler
from utils import node_history_analytics as nha

logger = logging.getLogger(__name__)

_SOURCE = "node_history.db on THIS box (the map collector's node snapshots)"


def _not_ok(res) -> bool:
    """Print the honest line for a non-ok result; True if the caller stops."""
    state = res.get("state")
    if state == "absent":
        print("  No node history on this box — nothing records it here")
        print(f"  (not found: {res.get('path')}).")
        print("  It is written by the map collector (meshforge-map); a box")
        print("  without the map service has no history to analyse.")
        return True
    if state == "unreadable":
        print("  UNKNOWN — the node history could not be read:")
        print(f"    {res.get('error')}")
        return True
    if state == "empty":
        print(f"  No node observations in the last {res.get('window_h'):.0f} h —")
        print("  the collector has not recorded any in this window.")
        return True
    return False


def _hhmm(epoch: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(epoch))


class AnalyticsHandler(BaseHandler):
    """TUI handler for node-history analytics (read-only)."""

    handler_id = "analytics"
    menu_section = "dashboard"

    def menu_items(self):
        return [
            ("analytics", "Analytics           Health, trends, coverage", None),
        ]

    def execute(self, action):
        if action == "analytics":
            self._analytics_menu()

    def _analytics_menu(self):
        """Analytics — from the live node history."""
        while True:
            choices = [
                ("health", "Health History      Online nodes per hour (48 h)"),
                ("trends", "Link Trends         SNR first 6 h vs last 6 h"),
                ("alerts", "Predictive Alerts   Falling battery / SNR"),
                ("coverage", "Coverage Stats      Where the known nodes are"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Analytics",
                "From this box's node history (read-only):",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "health": ("Health History", self._show_health_history),
                "trends": ("Link Trends", self._show_link_trends),
                "alerts": ("Predictive Alerts", self._show_predictive_alerts),
                "coverage": ("Coverage Stats", self._show_coverage_stats),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)
            else:
                self.ctx.notify_unwired(choice, "AnalyticsHandler._analytics_menu")

    def _show_health_history(self):
        clear_screen()
        print("=== Network Health History ===\n")
        res = nha.health_timeline()
        if not _not_ok(res):
            hours = res["hours"]
            print(f"  Source: {_SOURCE}")
            print(f"  Window: {res['window_h']:.0f} h · {res['observations']} snapshots "
                  f"({res['via_mqtt']} via MQTT)")
            print("  Known = positioned nodes in the history (position-less nodes are")
            print("  not recorded here) · Online = the node table marks it recently")
            print("  heard · SNR = mean last-heard SNR of online nodes\n")
            print(f"  {'Hour':<13} {'Known':>6} {'Online':>7} {'Avg SNR':>8} {'SNR n':>6}")
            print(f"  {'-' * 44}")
            for h in hours[-24:]:
                snr = "—" if h["avg_snr_online"] is None else f"{h['avg_snr_online']:.1f}"
                tag = "  (partial)" if h["partial"] else ""
                print(f"  {_hhmm(h['hour_epoch']):<13} {h['known']:>6} {h['online']:>7} "
                      f"{snr:>8} {h['snr_samples']:>6}{tag}")
            if len(hours) > 24:
                print(f"\n  (showing the newest 24 of {len(hours)} hours)")
        print()
        self.ctx.wait_for_enter()

    def _show_link_trends(self):
        clear_screen()
        print("=== Link Trends (SNR) ===\n")
        res = nha.link_trends()
        if not _not_ok(res):
            print(f"  Source: {_SOURCE}")
            print(f"  Mean last-heard SNR of each node, first {res['edge_h']:.0f} h vs last "
                  f"{res['edge_h']:.0f} h of {res['window_h']:.0f} h;")
            print(f"  online readings only, repeats collapsed, ≥{res['min_samples']} "
                  "distinct readings at each end.\n")
            print(f"  Nodes with SNR: {res['nodes_with_snr']} · judged: {res['nodes_judged']}")
            if not res["nodes_with_snr"]:
                print("\n  No online node on this box carries an SNR reading (an MQTT-only")
                print("  view has none) — link trends cannot be measured here.")
            if not res["nodes_judged"]:
                print("\n  Not enough distinct readings at both ends to judge any node —")
                print("  this is not a verdict that links are steady.")
            for label, rows in (("Falling", res["declining"]), ("Rising", res["improving"])):
                if rows:
                    print(f"\n  {label}:")
                    for r in rows:
                        print(f"    {str(r['name'])[:24]:<24} {r['snr_first']:>6.1f} -> "
                              f"{r['snr_last']:>6.1f} dB  ({r['delta_db']:+.1f})")
        print()
        self.ctx.wait_for_enter()

    def _show_predictive_alerts(self):
        clear_screen()
        print("=== Predictive Alerts ===\n")
        res = nha.predictive()
        if not _not_ok(res):
            print(f"  Source: {_SOURCE}")
            print(f"  Least-squares slope over {res['window_h']:.0f} h, online readings, "
                  f"≥{res['min_samples']} distinct each.")
            print(f"  Judged: battery {res['battery_nodes_judged']} node(s) (1-100 %; 0 and "
                  f">100 = no reading/external power) · SNR {res['snr_nodes_judged']} node(s)")
            print(f"  Flags: battery ≤ {res['battery_slope_alert']:.1f} %/h · "
                  f"SNR ≤ {res['snr_slope_alert']:.1f} dB/h\n")
            alerts = res["alerts"]
            if not res["battery_nodes_judged"] and not res["snr_nodes_judged"]:
                print("  Too few distinct readings to judge any node — UNKNOWN,")
                print("  not healthy.")
            elif not alerts:
                print("  None of the judged nodes crosses a flag. Nodes not judged")
                print("  (too few readings) are unknown, not healthy.")
            for a in alerts:
                if a["kind"] == "battery":
                    eta = ("at or below 20 %" if a["eta_h_to_floor"] <= 0
                           else f"~{a['eta_h_to_floor']:.0f} h to 20 %")
                    print(f"  \033[0;33m!\033[0m {str(a['name'])[:24]:<24} battery "
                          f"{a['slope']:+.1f} %/h, now {a['last']:.0f} % ({eta}; n={a['samples']})")
                else:
                    print(f"  \033[0;33m!\033[0m {str(a['name'])[:24]:<24} SNR "
                          f"{a['slope']:+.2f} dB/h, now {a['last']:.1f} dB (n={a['samples']})")
        print()
        self.ctx.wait_for_enter()

    def _show_coverage_stats(self):
        clear_screen()
        print("=== Coverage Statistics ===\n")
        res = nha.coverage()
        if not _not_ok(res):
            print(f"  Source: {_SOURCE} · window {res['window_h']:.0f} h\n")
            print(f"  Nodes in history:   {res['nodes_known']} (the history keeps "
                  "positioned nodes only)")
            print(f"  Usable position:    {res['positioned']} "
                  f"({res['via_mqtt']} via MQTT; 0,0 fixes excluded)")
            nets = ", ".join(f"{k} {v}" for k, v in sorted(res["by_network"].items()))
            print(f"  By network:         {nets}")
            b = res["extent_90"]
            print(f"  Extent (central 90 %): {b['south']:.3f}..{b['north']:.3f} N, "
                  f"{b['west']:.3f}..{b['east']:.3f} E")
            print(f"                      diagonal ~{res['extent_diagonal_km']:.0f} km; "
                  f"{res['outside_extent']} outside it (far or bad fixes)")
        print()
        self.ctx.wait_for_enter()
