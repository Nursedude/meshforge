"""
TX Load Balancer Handler — Dual-radio TX distribution control.

Provides TUI controls for the RadioLoadBalancer: status display,
enable/disable, threshold configuration, event history, and
congested node identification.
"""

import logging

from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)

# Optional import — load balancer requires dual-radio setup
try:
    from gateway.radio_failover import RadioLoadBalancer, LoadBalancerConfig
    _HAS_LOAD_BALANCER = True
except ImportError:
    _HAS_LOAD_BALANCER = False


#: Honest dead-end text (TUI audit finding 6): the RadioLoadBalancer lives
#: in the GATEWAY process; nothing hands it to the TUI.
LB_NOT_HERE = (
    "Load-balancer state is NOT OBSERVABLE from the TUI: it runs inside\n"
    "the gateway process.\n\n"
    "⚠️ Known defect (TUI audit 2026-09-27, finding 6C): the gateway\n"
    "currently never starts it even when enabled — it reads the\n"
    "flag from the wrong config object."
)


class LoadBalancerHandler(BaseHandler):
    """TUI handler for TX load balancing across dual radios."""

    handler_id = "load_balancer"
    menu_section = "mesh_networks"

    def menu_items(self):
        return [
            ("load_balancer", "TX Load Balancer    Dual-radio TX distribution", None),
        ]

    def execute(self, action):
        if action == "load_balancer":
            self._load_balancer_menu()

    def _get_lb(self):
        """Get the active load balancer instance, or None."""
        if not _HAS_LOAD_BALANCER:
            return None
        return getattr(self.ctx, 'load_balancer', None)

    def _load_balancer_menu(self):
        """Main load balancer configuration and monitoring menu."""
        if not _HAS_LOAD_BALANCER:
            self.ctx.dialog.msgbox(
                "Not Available",
                "RadioLoadBalancer module not available.\n\n"
                "Requires: src/gateway/radio_failover.py"
            )
            return

        while True:
            lb = self._get_lb()

            if lb:
                status = lb.get_status()
                state = status.get('state', 'unknown').upper()
                p_w = status.get('primary_weight', 100)
                s_w = status.get('secondary_weight', 0)
                header = (
                    f"State: {state} | "
                    f"Primary: {p_w:.0f}% | Secondary: {s_w:.0f}%"
                )
            else:
                header = "Load balancer: NOT OBSERVABLE from the TUI"

            choices = [
                ("status", "Status              View radio health & weights"),
                ("events", "Event Log           Recent state transitions"),
                ("congested", "Congested Nodes     Top talkers on the mesh"),
                ("thresholds", "Thresholds          TX threshold & max settings"),
                ("counters", "Reset Counters      Clear TX send counts"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "TX Load Balancer",
                header,
                choices,
            )

            if choice is None or choice == "back":
                break
            elif choice == "status":
                self._show_status()
            elif choice == "events":
                self._show_events()
            elif choice == "congested":
                self._show_congested()
            elif choice == "thresholds":
                self._show_thresholds()
            elif choice == "counters":
                self._reset_counters()

    def _show_status(self):
        """Show detailed load balancer status."""
        lb = self._get_lb()
        if not lb:
            self.ctx.dialog.msgbox(
                "TX Load Balancer",
                LB_NOT_HERE
            )
            return

        status = lb.get_status()
        state = status['state'].upper()
        p = status['primary']
        s = status['secondary']
        p_w = status['primary_weight']
        s_w = status['secondary_weight']
        tx = status.get('tx_counts', {})
        thresholds = status.get('thresholds', {})

        p_status = "ONLINE" if p['reachable'] else "OFFLINE"
        s_status = "ONLINE" if s['reachable'] else "OFFLINE"

        lines = [
            f"STATE: {state}",
            "",
            f"Primary Radio   [{p_status}]",
            f"  Port: {p['port']} (HTTP: {p['http_port']})",
            f"  TX Utilization: {p['tx_utilization']:.1f}%",
            f"  Ch Utilization: {p['channel_utilization']:.1f}%",
            f"  TX Weight: {p_w:.0f}%",
            f"  Messages Sent: {tx.get('primary', 0)}",
            "",
            f"Secondary Radio [{s_status}]",
            f"  Port: {s['port']} (HTTP: {s['http_port']})",
            f"  TX Utilization: {s['tx_utilization']:.1f}%",
            f"  Ch Utilization: {s['channel_utilization']:.1f}%",
            f"  TX Weight: {s_w:.0f}%",
            f"  Messages Sent: {tx.get('secondary', 0)}",
            "",
            f"TX Threshold: {thresholds.get('tx_threshold', '?')}%",
            f"TX Max: {thresholds.get('tx_max', '?')}%",
        ]

        if status.get('last_event'):
            lines.append(f"\nLast Event: {status['last_event']}")

        self.ctx.dialog.msgbox("TX Load Balancer Status", "\n".join(lines))

    def _show_events(self):
        """Show recent state transition events."""
        lb = self._get_lb()
        if not lb:
            self.ctx.dialog.msgbox("Event Log", LB_NOT_HERE)
            return

        events = list(lb._events)
        if not events:
            self.ctx.dialog.msgbox("Event Log", "No state transitions recorded.")
            return

        lines = ["Recent State Transitions", "=" * 40, ""]
        for event in reversed(events[-20:]):
            ts = event.timestamp.strftime("%H:%M:%S")
            lines.append(
                f"{ts}  {event.from_state.value} -> {event.to_state.value}"
            )
            lines.append(
                f"         p_tx={event.primary_utilization:.1f}%  "
                f"s_tx={event.secondary_utilization:.1f}%"
            )
            lines.append(f"         {event.reason}")
            lines.append("")

        self.ctx.dialog.msgbox("Event Log", "\n".join(lines))

    def _show_congested(self):
        """Show congested nodes (top talkers)."""
        lb = self._get_lb()
        if not lb:
            self.ctx.dialog.msgbox("Congested Nodes", LB_NOT_HERE)
            return

        status = lb.get_status()
        congested = status.get('congested_nodes', [])

        if not congested:
            self.ctx.dialog.msgbox(
                "Congested Nodes",
                "No congested nodes detected.\n\n"
                "Nodes appear here when channel utilization\n"
                "or TX airtime exceeds warning thresholds."
            )
            return

        lines = ["Top Talkers", "=" * 40, ""]
        for node in congested[:10]:
            name = node.get('name', node.get('id', '?'))
            ch = node.get('channel_util', 0)
            tx = node.get('tx_airtime', 0)
            lines.append(f"{name}")
            lines.append(f"  Channel Util: {ch:.1f}%  TX Airtime: {tx:.1f}%")
            lines.append("")

        self.ctx.dialog.msgbox("Congested Nodes", "\n".join(lines))

    def _show_thresholds(self):
        """Show current threshold configuration."""
        lb = self._get_lb()
        if not lb:
            self.ctx.dialog.msgbox("Thresholds", LB_NOT_HERE)
            return

        config = lb._config
        lines = [
            "TX Load Balancer Thresholds",
            "=" * 40,
            "",
            f"TX Threshold:     {config.tx_threshold:.1f}%",
            "  Start splitting TX when primary exceeds this.",
            "",
            f"TX Max:           {config.tx_max:.1f}%",
            "  Full offload to secondary at this level.",
            "",
            f"Recovery Margin:  {config.recovery_margin:.1f}%",
            "  Hysteresis band for returning to IDLE.",
            f"  (Returns to IDLE at {config.tx_threshold - config.recovery_margin:.1f}%)",
            "",
            f"Weight Change Rate: {config.weight_change_rate:.0f}% per cycle",
            "  Max weight shift per poll interval.",
            "",
            f"Min Primary Weight: {config.min_primary_weight:.0f}%",
            "  Primary radio always keeps at least this share.",
            "",
            f"Health Poll Interval: {config.health_poll_interval:.0f}s",
            "",
            "To change thresholds, edit gateway config:",
            "  ~/.config/meshforge/gateway.json",
        ]

        self.ctx.dialog.msgbox("Threshold Configuration", "\n".join(lines))

    def _reset_counters(self):
        """Reset TX send counters."""
        lb = self._get_lb()
        if not lb:
            self.ctx.dialog.msgbox("Reset Counters", LB_NOT_HERE)
            return

        if self.ctx.dialog.yesno(
            "Reset TX Counters",
            "Reset primary and secondary TX send counters to zero?\n\n"
            "This only resets the display counters, not the\n"
            "load balancing weights or state."
        ):
            lb.reset_counters()
            self.ctx.dialog.msgbox("Reset Counters", "TX counters have been reset.")
