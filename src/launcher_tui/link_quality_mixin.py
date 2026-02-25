"""
Link Quality Mixin for MeshForge Launcher TUI.

Provides link quality analysis tools:
- Score individual links
- View quality for all topology edges
- Track quality trends
- Get alerts for degraded links
"""

import logging
from typing import Optional

from utils.link_quality import LinkQualityScorer
from utils.link_quality import score_topology_edges
from gateway.network_topology import get_network_topology

logger = logging.getLogger(__name__)


class LinkQualityMixin:
    """Mixin providing link quality analysis tools for the TUI launcher."""

    def _link_quality_menu(self):
        """Link quality analysis menu."""
        choices = [
            ("overview", "Quality Overview"),
            ("best", "Best Links"),
            ("worst", "Worst Links"),
            ("alerts", "Quality Alerts"),
            ("score", "Score Single Link"),
            ("trends", "Quality Trends"),
            ("back", "Back"),
        ]

        while True:
            choice = self.dialog.menu(
                "Link Quality Analysis",
                "Analyze mesh network link quality:",
                choices
            )

            if choice is None or choice == "back":
                break

            dispatch = {
                "overview": ("Quality Overview", self._show_quality_overview),
                "best": ("Best Links", self._show_best_links),
                "worst": ("Worst Links", self._show_worst_links),
                "alerts": ("Quality Alerts", self._show_quality_alerts),
                "score": ("Score Link", self._score_link_interactive),
                "trends": ("Quality Trends", self._show_quality_trends),
            }
            entry = dispatch.get(choice)
            if entry:
                self._safe_call(*entry)

    def _get_link_scorer(self):
        """Get the link quality scorer instance."""
        return LinkQualityScorer()

    def _get_topology_scores(self):
        """Score all edges in the current topology."""
        try:
            topology = get_network_topology()
            if topology is None:
                return None, None

            return score_topology_edges(topology), topology

        except Exception as e:
            logger.error(f"Error scoring topology: {e}")
            return None, None

    def _show_quality_overview(self):
        """Show overall link quality statistics."""
        scores, topology = self._get_topology_scores()

        if scores is None:
            self.dialog.msgbox(
                "Unavailable",
                "Link quality module or topology not available.\n\n"
                "Ensure the gateway is running."
            )
            return

        if not scores:
            self.dialog.msgbox("No Links", "No links found in the topology.")
            return

        # Calculate statistics
        all_scores = [s.score for s in scores.values()]
        avg_score = sum(all_scores) / len(all_scores)
        min_score = min(all_scores)
        max_score = max(all_scores)

        # Count by quality
        quality_counts = {}
        for score in scores.values():
            quality = score.quality.value
            quality_counts[quality] = quality_counts.get(quality, 0) + 1

        lines = [
            "LINK QUALITY OVERVIEW",
            "=" * 50,
            "",
            f"Total Links:    {len(scores)}",
            f"Average Score:  {avg_score:.1f}/100",
            f"Best Score:     {max_score:.1f}/100",
            f"Worst Score:    {min_score:.1f}/100",
            "",
            "Quality Distribution:",
            "-" * 30,
        ]

        for quality in ["excellent", "good", "fair", "poor", "bad"]:
            count = quality_counts.get(quality, 0)
            pct = (count / len(scores) * 100) if scores else 0
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            lines.append(f"  {quality.capitalize():<10} {bar} {count} ({pct:.0f}%)")

        self.dialog.msgbox("Quality Overview", "\n".join(lines))

    def _show_best_links(self):
        """Show the best quality links."""
        scores, topology = self._get_topology_scores()

        if not scores:
            self.dialog.msgbox("No Data", "No link quality data available.")
            return

        # Sort by score descending
        sorted_links = sorted(
            scores.items(),
            key=lambda x: x[1].score,
            reverse=True
        )[:15]

        lines = [
            "BEST QUALITY LINKS",
            "=" * 60,
            "",
        ]

        for link_id, score in sorted_links:
            parts = link_id.split("_", 1)
            src = parts[0][:12] if len(parts) > 0 else "?"
            dst = parts[1][:12] if len(parts) > 1 else "?"

            snr = score.inputs.get("snr")
            snr_str = f"{snr:.1f}dB" if snr else "N/A"

            lines.append(f"[{score.score:5.1f}] {score.quality.value.upper():<10}")
            lines.append(f"   {src} → {dst}")
            lines.append(f"   SNR: {snr_str} | Hops: {score.inputs.get('hops', '?')}")
            lines.append("")

        self.dialog.msgbox("Best Links", "\n".join(lines))

    def _show_worst_links(self):
        """Show the worst quality links."""
        scores, topology = self._get_topology_scores()

        if not scores:
            self.dialog.msgbox("No Data", "No link quality data available.")
            return

        # Sort by score ascending (worst first)
        sorted_links = sorted(
            scores.items(),
            key=lambda x: x[1].score
        )[:15]

        lines = [
            "WORST QUALITY LINKS",
            "=" * 60,
            "",
        ]

        for link_id, score in sorted_links:
            parts = link_id.split("_", 1)
            src = parts[0][:12] if len(parts) > 0 else "?"
            dst = parts[1][:12] if len(parts) > 1 else "?"

            snr = score.inputs.get("snr")
            snr_str = f"{snr:.1f}dB" if snr else "N/A"

            lines.append(f"[{score.score:5.1f}] {score.quality.value.upper():<10}")
            lines.append(f"   {src} → {dst}")
            lines.append(f"   SNR: {snr_str} | Hops: {score.inputs.get('hops', '?')}")

            # Show first recommendation if available
            if score.recommendations:
                rec = score.recommendations[0][:50]
                lines.append(f"   ! {rec}...")
            lines.append("")

        self.dialog.msgbox("Worst Links", "\n".join(lines))

    def _show_quality_alerts(self):
        """Show alerts for links below quality threshold."""
        scores, topology = self._get_topology_scores()

        if not scores:
            self.dialog.msgbox("No Data", "No link quality data available.")
            return

        # Find poor and bad links
        alerts = []
        for link_id, score in scores.items():
            if score.quality.value in ("poor", "bad"):
                alerts.append((link_id, score))

        alerts.sort(key=lambda x: x[1].score)

        if not alerts:
            self.dialog.msgbox(
                "No Alerts",
                "All links are in fair or better condition!\n\n"
                f"Total links checked: {len(scores)}"
            )
            return

        lines = [
            f"LINK QUALITY ALERTS ({len(alerts)} issues)",
            "=" * 60,
            "",
        ]

        for link_id, score in alerts:
            parts = link_id.split("_", 1)
            src = parts[0][:12] if len(parts) > 0 else "?"
            dst = parts[1][:12] if len(parts) > 1 else "?"

            severity = "CRITICAL" if score.quality.value == "bad" else "WARNING"

            lines.append(f"[{severity}] {src} → {dst}")
            lines.append(f"   Score: {score.score:.1f}/100 ({score.quality.value})")

            # Component breakdown
            lines.append(f"   Components: SNR={score.snr_score:.0f} RSSI={score.rssi_score:.0f} "
                         f"Hops={score.hops_score:.0f}")

            # Recommendations
            for rec in score.recommendations[:2]:
                lines.append(f"   → {rec[:55]}")

            lines.append("")

        self.dialog.msgbox("Quality Alerts", "\n".join(lines))

    def _score_link_interactive(self):
        """Score a single link with user-provided values."""
        scorer = self._get_link_scorer()

        if scorer is None:
            self.dialog.msgbox("Error", "Link quality module not available.")
            return

        # Get SNR input
        snr_input = self.dialog.inputbox(
            "Link Quality Scorer",
            "Enter SNR (dB) or leave empty:",
            ""
        )
        snr = None
        if snr_input:
            try:
                snr = float(snr_input)
            except ValueError:
                pass

        # Get RSSI input
        rssi_input = self.dialog.inputbox(
            "Link Quality Scorer",
            "Enter RSSI (dBm) or leave empty:",
            ""
        )
        rssi = None
        if rssi_input:
            try:
                rssi = int(float(rssi_input))
            except ValueError:
                pass

        # Get hop count
        hops_input = self.dialog.inputbox(
            "Link Quality Scorer",
            "Enter hop count (default: 1):",
            "1"
        )
        try:
            hops = int(hops_input) if hops_input else 1
        except ValueError:
            hops = 1

        # Calculate score
        score = scorer.score(snr=snr, rssi=rssi, hops=hops)

        # Display result
        lines = [
            "LINK QUALITY SCORE",
            "=" * 50,
            "",
            f"Overall Score: {score.score:.1f}/100",
            f"Quality: {score.quality.value.upper()}",
            "",
            "Component Scores:",
            "-" * 30,
            f"  SNR:        {score.snr_score:.1f}/100",
            f"  RSSI:       {score.rssi_score:.1f}/100",
            f"  Hops:       {score.hops_score:.1f}/100",
            f"  Age:        {score.age_score:.1f}/100",
            f"  Stability:  {score.stability_score:.1f}/100",
            "",
        ]

        if score.recommendations:
            lines.append("Recommendations:")
            lines.append("-" * 30)
            for rec in score.recommendations:
                lines.append(f"  • {rec}")

        self.dialog.msgbox("Link Score", "\n".join(lines))

    def _show_quality_trends(self):
        """Show quality trend information."""
        scores, topology = self._get_topology_scores()

        if not scores:
            self.dialog.msgbox("No Data", "No link quality data available.")
            return

        lines = [
            "LINK QUALITY ANALYSIS",
            "=" * 60,
            "",
            "Note: Trend tracking requires continuous monitoring.",
            "Current snapshot analysis:",
            "",
        ]

        # Analyze current state
        excellent = sum(1 for s in scores.values() if s.quality.value == "excellent")
        good = sum(1 for s in scores.values() if s.quality.value == "good")
        fair = sum(1 for s in scores.values() if s.quality.value == "fair")
        poor = sum(1 for s in scores.values() if s.quality.value == "poor")
        bad = sum(1 for s in scores.values() if s.quality.value == "bad")

        total = len(scores)
        health_score = (
            excellent * 100 + good * 80 + fair * 60 + poor * 30 + bad * 10
        ) / total if total > 0 else 0

        lines.append(f"Network Health Score: {health_score:.1f}/100")
        lines.append("")

        # Health indicator
        if health_score >= 80:
            lines.append("Status: HEALTHY")
            lines.append("Network is performing well.")
        elif health_score >= 60:
            lines.append("Status: FAIR")
            lines.append("Some links need attention.")
        elif health_score >= 40:
            lines.append("Status: DEGRADED")
            lines.append("Multiple links showing issues.")
        else:
            lines.append("Status: CRITICAL")
            lines.append("Network requires immediate attention!")

        lines.append("")
        lines.append("Actions to improve:")
        lines.append("-" * 30)

        if bad > 0:
            lines.append(f"  • Address {bad} bad links immediately")
        if poor > 0:
            lines.append(f"  • Investigate {poor} poor links")
        if excellent + good < total * 0.5:
            lines.append("  • Consider adding relay nodes")
        if fair > total * 0.3:
            lines.append("  • Check antenna alignments")

        self.dialog.msgbox("Quality Trends", "\n".join(lines))
