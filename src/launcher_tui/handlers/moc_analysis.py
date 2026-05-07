"""MOC Analysis Tool — TUI handler.

Lets the operator pick a preset, run the full analysis, and surface the
output directory + file counts. Also exposes a "list presets" action
for quick reference.
"""

from __future__ import annotations

import logging

from backend import clear_screen
from handler_protocol import BaseHandler

logger = logging.getLogger(__name__)


class MOCAnalysisHandler(BaseHandler):
    """TUI handler for the MOC Analysis Tool."""

    handler_id = "moc_analysis"
    menu_section = "dashboard"

    def menu_items(self):
        return [
            ("moc_analysis", "MOC Analysis        Generate slide-ready SVG analysis pack", None),
        ]

    def execute(self, action):
        if action == "moc_analysis":
            self._main_menu()

    def _main_menu(self):
        """Top-level MOC Analysis menu."""
        while True:
            choices = [
                ("run", "Run Analysis        Pick a preset and generate SVGs + JSON"),
                ("list", "List Presets        Show available named presets"),
                ("reparse", "Re-parse diag24h    Refresh presentation_capture.db from log"),
                ("back", "Back"),
            ]
            choice = self.ctx.dialog.menu(
                "MOC Analysis",
                "Mesh Operations Center analysis toolset:",
                choices,
            )
            if choice is None or choice == "back":
                break
            dispatch = {
                "run": ("Run Analysis", self._run_analysis),
                "list": ("List Presets", self._list_presets),
                "reparse": ("Re-parse diag24h", self._reparse_diag24h),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _list_presets(self):
        """Print the available preset bundles."""
        from moc_analysis_tool.cli.presets import list_presets
        clear_screen()
        print("=== MOC Analysis Presets ===\n")
        for name, desc in list_presets().items():
            print(f"  {name}")
            print(f"    {desc}\n")
        input("Press Enter to continue...")

    def _run_analysis(self):
        """Pick a preset and run the full analysis."""
        from moc_analysis_tool.cli.presets import PRESETS, get_preset
        from moc_analysis_tool.cli.run_analysis import run_analysis

        preset_choices = [
            (name, f"{factory().region_key or 'all'} · "
                   f"{factory().window.hours():.0f}h window")
            for name, factory in PRESETS.items()
        ]
        preset_choices.append(("back", "Back"))
        chosen = self.ctx.dialog.menu(
            "Run MOC Analysis",
            "Pick a preset to run:",
            preset_choices,
        )
        if chosen is None or chosen == "back":
            return

        try:
            spec = get_preset(chosen)
        except KeyError:
            print(f"Unknown preset: {chosen}")
            input("Press Enter to continue...")
            return

        clear_screen()
        print(f"=== Running MOC Analysis: {spec.name} ===\n")
        print(f"  region: {spec.region_key or 'all'}")
        print(
            f"  window: {spec.window.start.isoformat()} → "
            f"{spec.window.end.isoformat()} ({spec.window.hours():.1f} h)"
        )
        print()

        try:
            summary = run_analysis(spec)
        except Exception as e:
            logger.exception("MOC analysis failed")
            print(f"\nFailed: {e}")
            input("Press Enter to continue...")
            return

        print(f"\nSuccess. Output: {summary['out_dir']}")
        print(f"  {len(summary['json_files'])} JSON files")
        print(f"  {len(summary['svg_files'])} SVG files")
        print(f"  {summary['node_count']} nodes rolled up")
        print(f"  leaderboards: {', '.join(summary['leaderboards'])}")
        input("\nPress Enter to continue...")

    def _reparse_diag24h(self):
        """Re-run the diag24h log parser to refresh presentation_capture.db.

        Useful when the diag_24h cron has captured fresh log entries and
        the operator wants the analysis run to pick them up.
        """
        from moc_analysis_tool.collectors.diag24h_parser import (
            parse_log_into_db, _default_log_path,
        )
        clear_screen()
        log_path = _default_log_path()
        print(f"=== Re-parse diag24h log ===\n")
        print(f"  log: {log_path}")
        if not log_path.exists():
            print(f"  log not found — nothing to do.")
            input("\nPress Enter to continue...")
            return

        size_mb = log_path.stat().st_size / (1024 * 1024)
        confirm = self.ctx.dialog.yesno(
            "Re-parse diag24h log",
            f"Parse {size_mb:.1f} MB log into presentation_capture.db?\n\n"
            "Idempotent: existing rows are not duplicated.",
        )
        if not confirm:
            return

        try:
            summary = parse_log_into_db(log_path=log_path)
        except Exception as e:
            logger.exception("Re-parse failed")
            print(f"\nFailed: {e}")
            input("Press Enter to continue...")
            return

        print(f"\nDone:")
        print(f"  packets_inserted:   {summary['packets_inserted']:,}")
        print(f"  duplicates skipped: {summary['packets_skipped_duplicate']:,}")
        print(f"  telemetry_inserted: {summary['telemetry_inserted']:,}")
        input("\nPress Enter to continue...")
