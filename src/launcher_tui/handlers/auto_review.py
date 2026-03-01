"""
Auto-Review Handler — Code review and self-audit for the TUI.

Extracted from ai_tools_mixin.py to serve the system menu section (Batch 8).
Provides automated code analysis using the ReviewOrchestrator.
"""

import logging

from handler_protocol import BaseHandler
from utils.safe_import import safe_import

ReviewOrchestrator, ReviewScope, _HAS_AUTO_REVIEW = safe_import(
    'utils.auto_review', 'ReviewOrchestrator', 'ReviewScope'
)

logger = logging.getLogger(__name__)


class AutoReviewHandler(BaseHandler):
    """TUI handler for automated code review."""

    handler_id = "auto_review"
    menu_section = "system"

    def menu_items(self):
        return [
            ("review", "Code Review         Auto-review codebase", None),
        ]

    def execute(self, action):
        if action == "review":
            self._auto_review_menu()

    def _auto_review_menu(self):
        """Code review system — run automated code analysis."""
        while True:
            choices = [
                ("full", "Full Review         Run all review agents"),
                ("security", "Security Review     Command injection, creds"),
                ("redundancy", "Redundancy Review   Duplicate code, imports"),
                ("performance", "Performance Review  Timeouts, loops, memory"),
                ("reliability", "Reliability Review  Error handling, TODOs"),
                ("back", "Back"),
            ]

            choice = self.ctx.dialog.menu(
                "Code Review",
                "Automated code analysis agents:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "full": ("Full Review", lambda: self._run_auto_review("ALL")),
                "security": ("Security Review", lambda: self._run_auto_review("SECURITY")),
                "redundancy": ("Redundancy Review", lambda: self._run_auto_review("REDUNDANCY")),
                "performance": ("Performance Review", lambda: self._run_auto_review("PERFORMANCE")),
                "reliability": ("Reliability Review", lambda: self._run_auto_review("RELIABILITY")),
            }
            entry = dispatch.get(choice)
            if entry:
                self.ctx.safe_call(*entry)

    def _run_auto_review(self, scope_name: str):
        """Execute an auto-review with the specified scope."""
        self.ctx.dialog.infobox("Reviewing", f"Running {scope_name.lower()} review...")

        if not _HAS_AUTO_REVIEW:
            self.ctx.dialog.msgbox(
                "Error",
                "Auto-review module not available.\n\n"
                "Ensure you're running from the src/ directory."
            )
            return

        try:
            scope_map = {
                "ALL": ReviewScope.ALL,
                "SECURITY": ReviewScope.SECURITY,
                "REDUNDANCY": ReviewScope.REDUNDANCY,
                "PERFORMANCE": ReviewScope.PERFORMANCE,
                "RELIABILITY": ReviewScope.RELIABILITY,
            }
            scope = scope_map.get(scope_name, ReviewScope.ALL)

            orchestrator = ReviewOrchestrator()
            report = orchestrator.run_full_review(scope=scope)

            # Build summary
            lines = [
                f"Scope: {scope_name}",
                f"Files Scanned: {report.total_files_scanned}",
                f"Total Issues: {report.total_issues}",
                f"Fixes Applied: {report.total_fixes_applied}",
                "",
            ]

            for category, result in report.agent_results.items():
                lines.append(
                    f"  {category.value.upper()}: "
                    f"{result.total_issues} issues "
                    f"({result.critical_count} critical, "
                    f"{result.high_count} high)"
                )

            # Show top findings
            findings = report.get_all_findings()
            if findings:
                lines.append("")
                lines.append("Top findings:")
                for finding in findings[:10]:
                    lines.append(
                        f"  [{finding.severity.value.upper()}] "
                        f"{finding.file_path}:{finding.line_number or '?'}"
                    )
                    lines.append(f"    {finding.issue}")

                if len(findings) > 10:
                    lines.append(f"  ... and {len(findings) - 10} more")

            self.ctx.dialog.msgbox("Review Results", "\n".join(lines))

        except Exception as e:
            self.ctx.dialog.msgbox("Error", f"Review failed: {e}")
