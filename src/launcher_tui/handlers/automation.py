"""
Automation Handler - TUI menu for auto-ping, auto-traceroute, auto-welcome.

Provides configuration and control for the AutomationEngine's periodic
network monitoring and node greeting tasks. Includes traceroute history
viewing and log access.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from backend import clear_screen
from handler_protocol import BaseHandler
from utils.safe_import import safe_import

_AutomationEngine, _HAS_ENGINE = safe_import(
    'utils.automation_engine', 'AutomationEngine'
)
_get_automation_engine, _HAS_GET_ENGINE = safe_import(
    'utils.automation_engine', 'get_automation_engine'
)
_get_traceroute_log_path, _HAS_LOG_PATH = safe_import(
    'utils.automation_engine', 'get_traceroute_log_path'
)

logger = logging.getLogger(__name__)


class AutomationHandler(BaseHandler):
    """TUI handler for mesh network automation features."""

    handler_id = "automation"
    menu_section = "mesh_networks"

    def menu_items(self) -> List[Tuple[str, str, Optional[str]]]:
        return [
            ("automation", "Automation       Auto-ping, traceroute, welcome", None),
        ]

    def execute(self, action: str) -> None:
        if action == "automation":
            self._menu_automation()

    def _get_engine(self):
        """Get the automation engine instance."""
        if not _HAS_GET_ENGINE or _get_automation_engine is None:
            self.ctx.dialog.msgbox(
                "Automation Not Available",
                "The automation engine module is not available.",
                height=6, width=50
            )
            return None
        try:
            return _get_automation_engine()
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Automation Error",
                f"Failed to initialize automation engine:\n{e}",
                height=8, width=55
            )
            return None

    def _menu_automation(self) -> None:
        """Automation menu — configure and control automated tasks."""
        engine = self._get_engine()
        if not engine:
            return

        while True:
            status = engine.get_status()
            running = status.get("running", False)
            active = status.get("active_threads", [])
            state_str = (
                f"RUNNING ({', '.join(active)})"
                if running and active else "STOPPED"
            )

            choice = self.ctx.dialog.menu(
                "Automation",
                f"MeshMonitor-inspired automation tasks\n"
                f"Status: {state_str}",
                choices=[
                    ("1", "Status & Statistics    - Current automation state"),
                    ("2", "Configure Auto-Ping   - Periodic node pinging"),
                    ("3", "Configure Traceroute  - Periodic route tracing"),
                    ("4", "Configure Welcome     - Greet new nodes"),
                    ("5", "Start Automation      - Launch enabled tasks"),
                    ("6", "Stop Automation       - Stop all tasks"),
                    ("7", "Traceroute History    - View past results"),
                    ("8", "Traceroute Logs       - View log file"),
                ],
                height=18, width=62
            )

            if not choice:
                return

            dispatch = {
                "1": ("Status", self._show_status),
                "2": ("Auto-Ping Config", self._configure_ping),
                "3": ("Auto-Traceroute Config", self._configure_traceroute),
                "4": ("Auto-Welcome Config", self._configure_welcome),
                "5": ("Start Automation", self._start_automation),
                "6": ("Stop Automation", self._stop_automation),
                "7": ("Traceroute History", self._view_traceroute_history),
                "8": ("Traceroute Logs", self._view_traceroute_logs),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _show_status(self) -> None:
        """Show automation status and statistics."""
        engine = self._get_engine()
        if not engine:
            return

        status = engine.get_status()
        stats = status.get("stats", {})
        config = status.get("config", {})

        ping_cfg = config.get("auto_ping", {})
        trace_cfg = config.get("auto_traceroute", {})
        welcome_cfg = config.get("auto_welcome", {})

        auto_disc = trace_cfg.get("auto_discover", True)
        targets_desc = (
            "auto-discover active nodes"
            if auto_disc and not trace_cfg.get("targets")
            else f"{len(trace_cfg.get('targets', []))} static"
            + (" + auto-discover" if auto_disc else "")
        )

        lines = [
            f"Engine: {'RUNNING' if status.get('running') else 'STOPPED'}",
            f"Active: {', '.join(status.get('active_threads', [])) or 'none'}",
            "",
            "=== Auto-Ping ===",
            f"  Enabled:  {ping_cfg.get('enabled', False)}",
            f"  Targets:  {len(ping_cfg.get('targets', []))}",
            f"  Interval: {ping_cfg.get('interval_minutes', 15)} min",
            f"  Sent:     {stats.get('pings_sent', 0)} "
            f"(OK: {stats.get('pings_success', 0)}, "
            f"Fail: {stats.get('pings_failed', 0)})",
            f"  Last:     {stats.get('last_ping_cycle', 'never')}",
            "",
            "=== Auto-Traceroute ===",
            f"  Enabled:  {trace_cfg.get('enabled', False)}",
            f"  Targets:  {targets_desc}",
            f"  Interval: {trace_cfg.get('interval_minutes', 60)} min",
            f"  Sent:     {stats.get('traceroutes_sent', 0)} "
            f"(OK: {stats.get('traceroutes_success', 0)}, "
            f"Fail: {stats.get('traceroutes_failed', 0)})",
            f"  Last:     {stats.get('last_traceroute_cycle', 'never')}",
            "",
            "=== Auto-Welcome ===",
            f"  Enabled:  {welcome_cfg.get('enabled', False)}",
            f"  Message:  {welcome_cfg.get('message', 'N/A')[:40]}",
            f"  Cooldown: {welcome_cfg.get('cooldown_hours', 24)}h",
            f"  Sent:     {stats.get('welcomes_sent', 0)}",
            f"  Last:     {stats.get('last_welcome_check', 'never')}",
        ]

        self.ctx.dialog.msgbox(
            "Automation Status",
            "\n".join(lines),
            height=30, width=62
        )

    def _configure_ping(self) -> None:
        """Configure auto-ping settings."""
        engine = self._get_engine()
        if not engine:
            return

        settings = engine.get_settings()
        ping_cfg = settings.get("auto_ping", {})

        choice = self.ctx.dialog.menu(
            "Auto-Ping Configuration",
            f"Currently: {'ENABLED' if ping_cfg.get('enabled') else 'DISABLED'}",
            choices=[
                ("1", f"Toggle    - {'Disable' if ping_cfg.get('enabled') else 'Enable'} auto-ping"),
                ("2", f"Interval  - Currently {ping_cfg.get('interval_minutes', 15)} min"),
                ("3", f"Targets   - {len(ping_cfg.get('targets', []))} nodes configured"),
            ],
            height=12, width=55
        )

        if choice == "1":
            ping_cfg["enabled"] = not ping_cfg.get("enabled", False)
            settings.set("auto_ping", ping_cfg)
            state = "enabled" if ping_cfg["enabled"] else "disabled"
            self.ctx.report_action(
                settings.save(),
                "Auto-Ping", f"Auto-ping {state}.",
                "Save Failed", f"Auto-ping {state} this session but could NOT be saved — won't persist.",
            )

        elif choice == "2":
            val = self.ctx.dialog.inputbox(
                "Ping Interval",
                "Enter interval in minutes (1-1440):",
                init=str(ping_cfg.get("interval_minutes", 15)),
                height=8, width=45
            )
            if val:
                try:
                    minutes = max(1, min(1440, int(val)))
                    ping_cfg["interval_minutes"] = minutes
                    settings.set("auto_ping", ping_cfg)
                    self._save_or_warn(settings, "Ping interval")
                except ValueError:
                    self.ctx.dialog.msgbox(
                        "Error", "Invalid number.", height=6, width=30
                    )

        elif choice == "3":
            self._edit_targets("auto_ping", "Ping")

    def _configure_traceroute(self) -> None:
        """Configure auto-traceroute settings."""
        engine = self._get_engine()
        if not engine:
            return

        settings = engine.get_settings()
        trace_cfg = settings.get("auto_traceroute", {})
        auto_disc = trace_cfg.get("auto_discover", True)

        choice = self.ctx.dialog.menu(
            "Auto-Traceroute Configuration",
            f"Currently: {'ENABLED' if trace_cfg.get('enabled') else 'DISABLED'}\n"
            f"Auto-discover: {'ON' if auto_disc else 'OFF'}",
            choices=[
                ("1", f"Toggle    - {'Disable' if trace_cfg.get('enabled') else 'Enable'} auto-traceroute"),
                ("2", f"Interval  - Currently {trace_cfg.get('interval_minutes', 60)} min"),
                ("3", f"Targets   - {len(trace_cfg.get('targets', []))} nodes configured"),
                ("4", f"Auto-Discover - {'Disable' if auto_disc else 'Enable'} active node discovery"),
            ],
            height=14, width=64
        )

        if choice == "1":
            trace_cfg["enabled"] = not trace_cfg.get("enabled", False)
            settings.set("auto_traceroute", trace_cfg)
            state = "enabled" if trace_cfg["enabled"] else "disabled"
            self.ctx.report_action(
                settings.save(),
                "Auto-Traceroute", f"Auto-traceroute {state}.",
                "Save Failed", f"Auto-traceroute {state} this session but could NOT be saved — won't persist.",
            )

        elif choice == "2":
            val = self.ctx.dialog.inputbox(
                "Traceroute Interval",
                "Enter interval in minutes (5-1440):",
                init=str(trace_cfg.get("interval_minutes", 60)),
                height=8, width=45
            )
            if val:
                try:
                    minutes = max(5, min(1440, int(val)))
                    trace_cfg["interval_minutes"] = minutes
                    settings.set("auto_traceroute", trace_cfg)
                    self._save_or_warn(settings, "Traceroute interval")
                except ValueError:
                    self.ctx.dialog.msgbox(
                        "Error", "Invalid number.", height=6, width=30
                    )

        elif choice == "3":
            self._edit_targets("auto_traceroute", "Traceroute")

        elif choice == "4":
            trace_cfg["auto_discover"] = not auto_disc
            settings.set("auto_traceroute", trace_cfg)
            new_state = "enabled" if trace_cfg["auto_discover"] else "disabled"
            desc = (
                "All online nodes from the node inventory will be\n"
                "automatically traced each cycle (in addition to\n"
                "any static targets)."
                if trace_cfg["auto_discover"]
                else "Only statically configured targets will be traced."
            )
            self.ctx.report_action(
                settings.save(),
                "Auto-Discover", f"Auto-discovery {new_state}.\n\n{desc}",
                "Save Failed", f"Auto-discovery {new_state} this session but could NOT be saved — won't persist.",
            )

    def _configure_welcome(self) -> None:
        """Configure auto-welcome settings."""
        engine = self._get_engine()
        if not engine:
            return

        settings = engine.get_settings()
        welcome_cfg = settings.get("auto_welcome", {})

        choice = self.ctx.dialog.menu(
            "Auto-Welcome Configuration",
            f"Currently: {'ENABLED' if welcome_cfg.get('enabled') else 'DISABLED'}",
            choices=[
                ("1", f"Toggle     - {'Disable' if welcome_cfg.get('enabled') else 'Enable'} auto-welcome"),
                ("2", f"Message    - Edit welcome message"),
                ("3", f"Cooldown   - Currently {welcome_cfg.get('cooldown_hours', 24)}h"),
            ],
            height=12, width=55
        )

        if choice == "1":
            welcome_cfg["enabled"] = not welcome_cfg.get("enabled", False)
            settings.set("auto_welcome", welcome_cfg)
            state = "enabled" if welcome_cfg["enabled"] else "disabled"
            self.ctx.report_action(
                settings.save(),
                "Auto-Welcome", f"Auto-welcome {state}.",
                "Save Failed", f"Auto-welcome {state} this session but could NOT be saved — won't persist.",
            )

        elif choice == "2":
            val = self.ctx.dialog.inputbox(
                "Welcome Message",
                "Enter the message to send to new nodes:",
                init=welcome_cfg.get("message", "Welcome to the mesh!"),
                height=8, width=55
            )
            if val:
                welcome_cfg["message"] = val
                settings.set("auto_welcome", welcome_cfg)
                self._save_or_warn(settings, "Welcome message")

        elif choice == "3":
            val = self.ctx.dialog.inputbox(
                "Welcome Cooldown",
                "Hours before re-welcoming a node (1-168):",
                init=str(welcome_cfg.get("cooldown_hours", 24)),
                height=8, width=45
            )
            if val:
                try:
                    hours = max(1, min(168, int(val)))
                    welcome_cfg["cooldown_hours"] = hours
                    settings.set("auto_welcome", welcome_cfg)
                    self._save_or_warn(settings, "Welcome cooldown")
                except ValueError:
                    self.ctx.dialog.msgbox(
                        "Error", "Invalid number.", height=6, width=30
                    )

    def _edit_targets(self, config_key: str, label: str) -> None:
        """Edit target node list for ping or traceroute."""
        engine = self._get_engine()
        if not engine:
            return

        settings = engine.get_settings()
        cfg = settings.get(config_key, {})
        targets = cfg.get("targets", [])

        current = "\n".join(targets) if targets else "(none)"
        val = self.ctx.dialog.inputbox(
            f"{label} Targets",
            f"Enter node IDs (comma-separated, e.g. !abc123,!def456):\n\n"
            f"Current: {current}",
            init=",".join(targets),
            height=12, width=60
        )

        if val is not None:
            new_targets = [
                t.strip() for t in val.split(",")
                if t.strip()
            ]
            cfg["targets"] = new_targets
            settings.set(config_key, cfg)
            self.ctx.report_action(
                settings.save(),
                f"{label} Targets Updated", f"Set {len(new_targets)} target(s).",
                "Save Failed",
                f"{len(new_targets)} target(s) applied this session but could NOT "
                "be saved — won't persist across a restart.",
            )

    def _save_or_warn(self, settings, what: str) -> bool:
        """Persist settings; surface a failed write instead of swallowing it.

        SettingsManager.save() returns False on a failed disk write (never
        raises) and these sites have no success dialog, so without this a failed
        persist is silent and the change quietly won't survive a restart
        (honest-signal .save() sweep, #74-#77).
        """
        ok = settings.save()
        if not ok:
            self.ctx.dialog.msgbox(
                "Save Failed",
                f"{what} was applied this session but could NOT be saved — it "
                "won't persist across a restart. Check disk space / permissions.",
                height=8, width=52,
            )
        return ok

    def _start_automation(self) -> None:
        """Start all enabled automation tasks."""
        engine = self._get_engine()
        if not engine:
            return

        if engine.is_alive():
            self.ctx.dialog.msgbox(
                "Already Running",
                "Automation engine is already running.",
                height=6, width=42
            )
            return

        started = engine.start()
        if started:
            status = engine.get_status()
            active = status.get("active_threads", [])
            self.ctx.dialog.msgbox(
                "Automation Started",
                f"Running tasks: {', '.join(active)}",
                height=7, width=50
            )
        else:
            self.ctx.dialog.msgbox(
                "No Tasks Enabled",
                "Enable at least one automation task first\n"
                "(auto-ping, auto-traceroute, or auto-welcome).",
                height=8, width=50
            )

    def _stop_automation(self) -> None:
        """Stop all automation tasks."""
        engine = self._get_engine()
        if not engine:
            return

        if not engine.is_alive():
            self.ctx.dialog.msgbox(
                "Not Running",
                "Automation engine is not currently running.",
                height=6, width=44
            )
            return

        engine.stop()
        self.ctx.dialog.msgbox(
            "Automation Stopped",
            "All automation tasks have been stopped.",
            height=6, width=44
        )

    def _view_traceroute_history(self) -> None:
        """View persistent traceroute history from SQLite."""
        engine = self._get_engine()
        if not engine:
            return

        store = engine.get_traceroute_store()

        choice = self.ctx.dialog.menu(
            "Traceroute History",
            "View traceroute results from persistent storage",
            choices=[
                ("recent", "Recent Results     - Last 40 traceroutes"),
                ("summary", "Node Summary       - Per-node success rates"),
            ],
            height=10, width=58,
        )

        if choice == "recent":
            self._show_recent_traceroutes(store)
        elif choice == "summary":
            self._show_traceroute_summary(store)

    def _show_recent_traceroutes(self, store) -> None:
        """Display recent traceroute results."""
        results = store.get_recent(limit=40)
        if not results:
            self.ctx.dialog.msgbox(
                "No History",
                "No traceroute results recorded yet.\n"
                "Enable auto-traceroute or run an on-demand\n"
                "traceroute from Network Tools.",
                height=8, width=50,
            )
            return

        clear_screen()
        print("=== Traceroute History (Most Recent) ===\n")
        print(f"{'Time':<20} {'Node':<14} {'Status':<8} {'Hops':<6} {'Route'}")
        print("-" * 78)

        for r in results:
            ts = r.get("timestamp_dt", "")[:19]
            node = r.get("node_id", "?")[:13]
            name = r.get("node_name", "")
            if name:
                node = f"{name[:10]}"
            ok = r.get("success", False)
            status = "\033[0;32mOK\033[0m    " if ok else "\033[0;31mFAIL\033[0m  "
            hops = str(r.get("hops", 0)) if ok else "-"

            route_hops = r.get("route_json", [])
            if route_hops:
                route_str = " -> ".join(f"!{h:08x}" if isinstance(h, int) else str(h) for h in route_hops[:5])
                if len(route_hops) > 5:
                    route_str += " ..."
            elif ok and r.get("raw_output"):
                route_str = r["raw_output"][:30]
            elif not ok:
                route_str = r.get("error", "")[:30]
            else:
                route_str = ""

            print(f"{ts:<20} {node:<14} {status} {hops:<6} {route_str}")

        print(f"\nTotal: {len(results)} results shown")
        self.ctx.wait_for_enter()

    def _show_traceroute_summary(self, store) -> None:
        """Display per-node traceroute summary."""
        summary = store.get_summary()
        if not summary:
            self.ctx.dialog.msgbox(
                "No Data",
                "No traceroute data available yet.",
                height=6, width=40,
            )
            return

        clear_screen()
        print("=== Traceroute Summary (Per Node) ===\n")
        print(
            f"{'Node':<14} {'Name':<12} {'Total':<7} {'OK%':<7} "
            f"{'Avg Hops':<10} {'Last Seen'}"
        )
        print("-" * 72)

        for s in summary:
            node = s["node_id"][:13]
            name = s.get("node_name", "")[:11]
            total = s["total"]
            rate = f"{s['success_rate']:.0f}%"
            avg_hops = str(s["avg_hops"]) if s["avg_hops"] else "-"
            last = s.get("last_seen", "never")

            # Color-code success rate
            pct = s["success_rate"]
            if pct >= 80:
                rate_str = f"\033[0;32m{rate:<7}\033[0m"
            elif pct >= 50:
                rate_str = f"\033[0;33m{rate:<7}\033[0m"
            else:
                rate_str = f"\033[0;31m{rate:<7}\033[0m"

            print(
                f"{node:<14} {name:<12} {total:<7} {rate_str} "
                f"{avg_hops:<10} {last}"
            )

        print(f"\nNodes tracked: {len(summary)}")
        self.ctx.wait_for_enter()

    def _view_traceroute_logs(self) -> None:
        """View the traceroute log file."""
        if not _HAS_LOG_PATH or _get_traceroute_log_path is None:
            self.ctx.dialog.msgbox(
                "Not Available",
                "Traceroute log path not available.",
                height=6, width=40,
            )
            return

        try:
            log_path = _get_traceroute_log_path()
        except Exception as e:
            self.ctx.dialog.msgbox(
                "Error", f"Could not determine log path:\n{e}",
                height=7, width=50,
            )
            return

        if not log_path.exists():
            self.ctx.dialog.msgbox(
                "No Log File",
                f"Traceroute log not yet created.\n\n"
                f"Expected at:\n{log_path}\n\n"
                f"Run a traceroute to generate log entries.",
                height=10, width=55,
            )
            return

        try:
            with open(log_path, "r") as f:
                lines = f.readlines()
        except OSError as e:
            self.ctx.dialog.msgbox(
                "Read Error", f"Could not read log file:\n{e}",
                height=7, width=50,
            )
            return

        # Show last 50 lines
        tail_lines = lines[-50:]

        clear_screen()
        print(f"=== Traceroute Log ({log_path.name}) ===")
        print(f"Showing last {len(tail_lines)} of {len(lines)} lines\n")

        for line in tail_lines:
            line = line.rstrip()
            # Color-code OK/FAIL
            if " OK" in line:
                print(f"\033[0;32m{line}\033[0m")
            elif "FAIL" in line:
                print(f"\033[0;31m{line}\033[0m")
            else:
                print(line)

        print(f"\nLog file: {log_path}")
        self.ctx.wait_for_enter()
