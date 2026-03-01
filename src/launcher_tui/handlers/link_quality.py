"""
Link Quality Handler — Link quality analysis, scoring, alerts, trends.

Converted from link_quality_mixin.py as part of the mixin-to-registry migration.
"""

import logging

from handler_protocol import BaseHandler
from utils.link_quality import LinkQualityScorer, score_topology_edges
from gateway.network_topology import get_network_topology

logger = logging.getLogger(__name__)


class LinkQualityHandler(BaseHandler):
    """TUI handler for link quality analysis tools."""

    handler_id = "link_quality"
    menu_section = "maps_viz"

    def menu_items(self):
        return [
            ("quality", "Link Quality        Quality analysis", None),
        ]

    def execute(self, action):
        if action == "quality":
            self._link_quality_menu()

    def _link_quality_menu(self):
        choices = [
            ("overview", "Quality Overview"), ("best", "Best Links"),
            ("worst", "Worst Links"), ("alerts", "Quality Alerts"),
            ("score", "Score Single Link"), ("trends", "Quality Trends"),
            ("back", "Back"),
        ]
        while True:
            choice = self.ctx.dialog.menu("Link Quality Analysis", "Analyze mesh network link quality:", choices)
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
                self.ctx.safe_call(*entry)

    def _get_topology_scores(self):
        try:
            topology = get_network_topology()
            if topology is None:
                return None, None
            return score_topology_edges(topology), topology
        except Exception as e:
            logger.error(f"Error scoring topology: {e}")
            return None, None

    def _show_quality_overview(self):
        scores, topology = self._get_topology_scores()
        if scores is None:
            self.ctx.dialog.msgbox("Unavailable", "Link quality module or topology not available.\n\nEnsure the gateway is running.")
            return
        if not scores:
            self.ctx.dialog.msgbox("No Links", "No links found in the topology.")
            return
        all_scores = [s.score for s in scores.values()]
        avg_score = sum(all_scores) / len(all_scores)
        quality_counts = {}
        for score in scores.values():
            quality = score.quality.value
            quality_counts[quality] = quality_counts.get(quality, 0) + 1
        lines = ["LINK QUALITY OVERVIEW", "=" * 50, "", f"Total Links:    {len(scores)}", f"Average Score:  {avg_score:.1f}/100", f"Best Score:     {max(all_scores):.1f}/100", f"Worst Score:    {min(all_scores):.1f}/100", "", "Quality Distribution:", "-" * 30]
        for quality in ["excellent", "good", "fair", "poor", "bad"]:
            count = quality_counts.get(quality, 0)
            pct = (count / len(scores) * 100) if scores else 0
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            lines.append(f"  {quality.capitalize():<10} {bar} {count} ({pct:.0f}%)")
        self.ctx.dialog.msgbox("Quality Overview", "\n".join(lines))

    def _show_best_links(self):
        scores, topology = self._get_topology_scores()
        if not scores:
            self.ctx.dialog.msgbox("No Data", "No link quality data available.")
            return
        sorted_links = sorted(scores.items(), key=lambda x: x[1].score, reverse=True)[:15]
        lines = ["BEST QUALITY LINKS", "=" * 60, ""]
        for link_id, score in sorted_links:
            parts = link_id.split("_", 1)
            src = parts[0][:12] if len(parts) > 0 else "?"
            dst = parts[1][:12] if len(parts) > 1 else "?"
            snr = score.inputs.get("snr")
            snr_str = f"{snr:.1f}dB" if snr else "N/A"
            lines.extend([f"[{score.score:5.1f}] {score.quality.value.upper():<10}", f"   {src} → {dst}", f"   SNR: {snr_str} | Hops: {score.inputs.get('hops', '?')}", ""])
        self.ctx.dialog.msgbox("Best Links", "\n".join(lines))

    def _show_worst_links(self):
        scores, topology = self._get_topology_scores()
        if not scores:
            self.ctx.dialog.msgbox("No Data", "No link quality data available.")
            return
        sorted_links = sorted(scores.items(), key=lambda x: x[1].score)[:15]
        lines = ["WORST QUALITY LINKS", "=" * 60, ""]
        for link_id, score in sorted_links:
            parts = link_id.split("_", 1)
            src = parts[0][:12] if len(parts) > 0 else "?"
            dst = parts[1][:12] if len(parts) > 1 else "?"
            snr = score.inputs.get("snr")
            snr_str = f"{snr:.1f}dB" if snr else "N/A"
            lines.append(f"[{score.score:5.1f}] {score.quality.value.upper():<10}")
            lines.append(f"   {src} → {dst}")
            lines.append(f"   SNR: {snr_str} | Hops: {score.inputs.get('hops', '?')}")
            if score.recommendations:
                rec = score.recommendations[0][:50]
                lines.append(f"   ! {rec}...")
            lines.append("")
        self.ctx.dialog.msgbox("Worst Links", "\n".join(lines))

    def _show_quality_alerts(self):
        scores, topology = self._get_topology_scores()
        if not scores:
            self.ctx.dialog.msgbox("No Data", "No link quality data available.")
            return
        alerts = [(link_id, score) for link_id, score in scores.items() if score.quality.value in ("poor", "bad")]
        alerts.sort(key=lambda x: x[1].score)
        if not alerts:
            self.ctx.dialog.msgbox("No Alerts", f"All links are in fair or better condition!\n\nTotal links checked: {len(scores)}")
            return
        lines = [f"LINK QUALITY ALERTS ({len(alerts)} issues)", "=" * 60, ""]
        for link_id, score in alerts:
            parts = link_id.split("_", 1)
            src = parts[0][:12] if len(parts) > 0 else "?"
            dst = parts[1][:12] if len(parts) > 1 else "?"
            severity = "CRITICAL" if score.quality.value == "bad" else "WARNING"
            lines.append(f"[{severity}] {src} → {dst}")
            lines.append(f"   Score: {score.score:.1f}/100 ({score.quality.value})")
            lines.append(f"   Components: SNR={score.snr_score:.0f} RSSI={score.rssi_score:.0f} Hops={score.hops_score:.0f}")
            for rec in score.recommendations[:2]:
                lines.append(f"   → {rec[:55]}")
            lines.append("")
        self.ctx.dialog.msgbox("Quality Alerts", "\n".join(lines))

    def _score_link_interactive(self):
        scorer = LinkQualityScorer()
        snr_input = self.ctx.dialog.inputbox("Link Quality Scorer", "Enter SNR (dB) or leave empty:", "")
        snr = None
        if snr_input:
            try:
                snr = float(snr_input)
            except ValueError:
                pass
        rssi_input = self.ctx.dialog.inputbox("Link Quality Scorer", "Enter RSSI (dBm) or leave empty:", "")
        rssi = None
        if rssi_input:
            try:
                rssi = int(float(rssi_input))
            except ValueError:
                pass
        hops_input = self.ctx.dialog.inputbox("Link Quality Scorer", "Enter hop count (default: 1):", "1")
        try:
            hops = int(hops_input) if hops_input else 1
        except ValueError:
            hops = 1
        score = scorer.score(snr=snr, rssi=rssi, hops=hops)
        lines = [
            "LINK QUALITY SCORE", "=" * 50, "",
            f"Overall Score: {score.score:.1f}/100", f"Quality: {score.quality.value.upper()}", "",
            "Component Scores:", "-" * 30,
            f"  SNR:        {score.snr_score:.1f}/100", f"  RSSI:       {score.rssi_score:.1f}/100",
            f"  Hops:       {score.hops_score:.1f}/100", f"  Age:        {score.age_score:.1f}/100",
            f"  Stability:  {score.stability_score:.1f}/100", "",
        ]
        if score.recommendations:
            lines.extend(["Recommendations:", "-" * 30])
            for rec in score.recommendations:
                lines.append(f"  • {rec}")
        self.ctx.dialog.msgbox("Link Score", "\n".join(lines))

    def _show_quality_trends(self):
        scores, topology = self._get_topology_scores()
        if not scores:
            self.ctx.dialog.msgbox("No Data", "No link quality data available.")
            return
        lines = ["LINK QUALITY ANALYSIS", "=" * 60, "", "Note: Trend tracking requires continuous monitoring.", "Current snapshot analysis:", ""]
        excellent = sum(1 for s in scores.values() if s.quality.value == "excellent")
        good = sum(1 for s in scores.values() if s.quality.value == "good")
        fair = sum(1 for s in scores.values() if s.quality.value == "fair")
        poor = sum(1 for s in scores.values() if s.quality.value == "poor")
        bad = sum(1 for s in scores.values() if s.quality.value == "bad")
        total = len(scores)
        health_score = (excellent * 100 + good * 80 + fair * 60 + poor * 30 + bad * 10) / total if total > 0 else 0
        lines.append(f"Network Health Score: {health_score:.1f}/100")
        lines.append("")
        if health_score >= 80:
            lines.extend(["Status: HEALTHY", "Network is performing well."])
        elif health_score >= 60:
            lines.extend(["Status: FAIR", "Some links need attention."])
        elif health_score >= 40:
            lines.extend(["Status: DEGRADED", "Multiple links showing issues."])
        else:
            lines.extend(["Status: CRITICAL", "Network requires immediate attention!"])
        lines.extend(["", "Actions to improve:", "-" * 30])
        if bad > 0:
            lines.append(f"  • Address {bad} bad links immediately")
        if poor > 0:
            lines.append(f"  • Investigate {poor} poor links")
        if excellent + good < total * 0.5:
            lines.append("  • Consider adding relay nodes")
        if fair > total * 0.3:
            lines.append("  • Check antenna alignments")
        self.ctx.dialog.msgbox("Quality Trends", "\n".join(lines))
