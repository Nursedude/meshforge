"""Config Doctor — read-only per-box config drift diagnosis.

Surfaces common config breakage without applying fixes. Fix hints route
operators to the handlers that already own remediation (RNS Diagnostics,
NomadNet Service Control, Gateway Preflight).

Check functions live in ``_config_doctor_checks.py`` so this handler
stays under the 1,500-line cap and the checks are unit-testable without
instantiating the TUI.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from handler_protocol import BaseHandler

from ._config_doctor_checks import (
    CheckResult,
    FAIL,
    OK,
    SKIP,
    WARN,
    run_all_checks,
)

logger = logging.getLogger(__name__)

# ANSI color codes — mirror the palette used elsewhere in the TUI.
_ANSI = {
    OK:   "\033[0;32m",   # green
    WARN: "\033[0;33m",   # yellow
    FAIL: "\033[0;31m",   # red
    SKIP: "\033[0;37m",   # light gray
}
_ANSI_RESET = "\033[0m"
_ANSI_BOLD = "\033[1m"

# Status symbol used in the summary table.
_SYMBOL = {OK: "  OK  ", WARN: " WARN ", FAIL: " FAIL ", SKIP: " SKIP "}

# Ranking for summary headline — "worst status wins".
_RANK = {OK: 0, SKIP: 1, WARN: 2, FAIL: 3}


class ConfigDoctorHandler(BaseHandler):
    """Read-only health audit across rnsd, gateway, and NomadNet configs."""

    handler_id = "config_doctor"
    menu_section = "system"

    def menu_items(self):
        return [
            ("run", "Config Doctor        Audit per-box config drift", None),
            ("details", "Config Doctor Details   Drill into last run", None),
        ]

    def execute(self, action):
        dispatch = {
            "run": ("Config Doctor", self._run_and_render),
            "details": ("Config Doctor Details", self._details_menu),
        }
        entry = dispatch.get(action)
        if entry:
            self.ctx.safe_call(*entry)

    # ------------------------------------------------------------------
    # Run + render (summary view)
    # ------------------------------------------------------------------

    def _run_and_render(self) -> None:
        from backend import clear_screen

        results = self._collect_results()
        # Keep the most recent run so "details" can drill in without re-running.
        self._last_results: List[CheckResult] = results

        clear_screen()
        print(f"{_ANSI_BOLD}=== Config Doctor ==={_ANSI_RESET}\n")
        print("Per-box config-drift audit. Read-only — no fixes applied.\n")

        self._render_summary_table(results)

        # Headline: worst status across all checks determines the verdict.
        worst = max(results, key=lambda r: _RANK[r.status])
        print()
        if _RANK[worst.status] >= _RANK[FAIL]:
            color = _ANSI[FAIL]
            print(f"{color}Verdict: at least one critical issue "
                  f"needs attention.{_ANSI_RESET}")
        elif _RANK[worst.status] >= _RANK[WARN]:
            color = _ANSI[WARN]
            print(f"{color}Verdict: warnings present — review recommended."
                  f"{_ANSI_RESET}")
        else:
            color = _ANSI[OK]
            print(f"{color}Verdict: no drift detected.{_ANSI_RESET}")

        # Inline fix hints for non-OK rows so operators can act without
        # needing the details view.
        print()
        failing = [r for r in results if r.status in (FAIL, WARN)]
        if failing:
            print(f"{_ANSI_BOLD}Action items:{_ANSI_RESET}")
            for r in failing:
                self._print_fix_block(r, indent="  ")

        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Details view
    # ------------------------------------------------------------------

    def _details_menu(self) -> None:
        """Show full per-check detail for the most recent run.

        If no run has happened yet in this TUI session, kick one off so
        operators can enter the details view directly.
        """
        from backend import clear_screen

        results = getattr(self, "_last_results", None)
        if not results:
            results = self._collect_results()
            self._last_results = results

        choices = [
            (r.name, f"{_SYMBOL[r.status].strip():5s}  {r.message[:60]}")
            for r in results
        ]
        while True:
            selection = self.ctx.dialog.menu(
                "Config Doctor — Details",
                "Select a check to view full detail:",
                choices,
            )
            if not selection:
                return
            match = next((r for r in results if r.name == selection), None)
            if match is None:
                return
            self._render_single(match)

    def _render_single(self, result: CheckResult) -> None:
        from backend import clear_screen

        clear_screen()
        color = _ANSI[result.status]
        print(f"{_ANSI_BOLD}=== Config Doctor: {result.name} ==={_ANSI_RESET}\n")
        print(f"Status : {color}{_SYMBOL[result.status].strip()}{_ANSI_RESET}")
        print(f"Summary: {result.message}")
        if result.details:
            print()
            print("Details:")
            for line in result.details:
                print(f"  {line}")
        if result.fix_hint:
            print()
            print(f"{_ANSI_BOLD}Fix:{_ANSI_RESET}")
            for line in result.fix_hint.splitlines():
                print(f"  {line}")
        if result.route_hint:
            print()
            print(f"Try: {result.route_hint}")
        print()
        self.ctx.wait_for_enter()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _collect_results(self) -> List[CheckResult]:
        """Invoke the check runner with the NomadNet SSOT state pulled from
        the NomadNet handler if it's loaded (keeps the check pure)."""
        return run_all_checks(service_state=self._nomadnet_service_state())

    def _nomadnet_service_state(self) -> Optional[dict]:
        """Look up NomadNet service state via the registered handler.

        Returns None if the NomadNet handler isn't loaded (e.g. a
        radio_maps-profile box). The underlying check treats None as
        SKIP, so the result stays graceful.
        """
        registry = getattr(self.ctx, "registry", None)
        if registry is None:
            return None
        handler = registry.get_handler("nomadnet")
        if handler is None or not hasattr(handler, "_nomadnet_service_state"):
            return None
        try:
            return handler._nomadnet_service_state()
        except Exception as exc:   # pragma: no cover — SSOT self-contained
            logger.warning("Config Doctor: nomadnet state lookup failed: %s", exc)
            return None

    def _render_summary_table(self, results: List[CheckResult]) -> None:
        max_name = max(len(r.name) for r in results)
        for r in results:
            color = _ANSI[r.status]
            print(f"  [{color}{_SYMBOL[r.status]}{_ANSI_RESET}] "
                  f"{r.name.ljust(max_name)}  {r.message}")

    def _print_fix_block(self, result: CheckResult, indent: str = "") -> None:
        color = _ANSI[result.status]
        print(f"{indent}{color}{_SYMBOL[result.status].strip()}{_ANSI_RESET} "
              f"{result.name}")
        if result.fix_hint:
            for line in result.fix_hint.splitlines():
                print(f"{indent}    {line}")
        if result.route_hint:
            print(f"{indent}    Try: {result.route_hint}")
        print()
