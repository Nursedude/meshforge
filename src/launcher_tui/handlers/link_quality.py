"""
Link Quality Handler — Link quality analysis, scoring, alerts, trends.

Converted from link_quality_mixin.py as part of the mixin-to-registry migration.
"""

import logging

from handler_protocol import BaseHandler
from utils.link_quality import LinkQuality, LinkQualityScorer, score_topology_edges
from utils.rf import grade_snr
from gateway.network_topology import get_network_topology

from .topology import GRAPH_NOT_HERE, graph_observable

logger = logging.getLogger(__name__)


def _split_known(scores):
    """(links WITH signal evidence, count without). A link with no SNR and
    no RSSI is UNKNOWN — never ranked best/worst, never called fine."""
    known = {k: v for k, v in scores.items() if v.quality is not LinkQuality.UNKNOWN}
    return known, len(scores) - len(known)


def _snr_text(score):
    """The shared grade's text; 0.0 dB is a reading, not N/A."""
    return grade_snr(score.inputs.get("snr"), score.inputs.get("sf")).text()


def _num(v, fmt="{:.0f}"):
    return "?" if v is None else fmt.format(v)


def _unknown_line(n):
    return [f"{n} link(s) with no signal evidence — not ranked"] if n else []



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
            else:
                self.ctx.notify_unwired(choice, "LinkQualityHandler._link_quality_menu")

    def _get_topology_scores(self):
        try:
            topology = get_network_topology()
            if topology is None:
                return None, None
            return score_topology_edges(topology), topology
        except Exception as e:
            logger.error(f"Error scoring topology: {e}")
            return None, None

    def _scores_or_explain(self, title):
        """Scores for a link-graph pane, or None after saying why.

        The graph lives in the gateway process; this TUI's copy is empty by
        construction, so every pane here read "No links found" / "No link
        quality data" on every box — including gateways with a live graph
        (2026-09-26; the Topology sibling was cured 2026-09-23 and this one
        was missed). Empty-because-not-looked-at is not empty.
        """
        scores, topology = self._get_topology_scores()
        if scores is None:
            self.ctx.dialog.msgbox("Unavailable", "Link quality module or topology not available.")
            return None
        if not graph_observable(topology):
            self.ctx.dialog.msgbox(title, GRAPH_NOT_HERE)
            return None
        if not scores:
            self.ctx.dialog.msgbox(title, "The gateway's link graph is live and holds no links yet.")
            return None
        return scores

    def _show_quality_overview(self):
        scores = self._scores_or_explain("Quality Overview")
        if scores is None:
            return
        known, n_unknown = _split_known(scores)
        all_scores = [s.score for s in known.values()]
        quality_counts = {}
        for score in scores.values():
            quality = score.quality.value
            quality_counts[quality] = quality_counts.get(quality, 0) + 1
        lines = ["LINK QUALITY OVERVIEW", "=" * 50, "", f"Total Links:    {len(scores)}"]
        if all_scores:
            lines += [f"Average Score:  {sum(all_scores) / len(all_scores):.1f}/100 (links with signal evidence)",
                      f"Best Score:     {max(all_scores):.1f}/100", f"Worst Score:    {min(all_scores):.1f}/100"]
        else:
            lines.append("Average Score:  UNKNOWN — no link has signal evidence")
        lines += ["", "Quality Distribution:", "-" * 30]
        for quality in ["excellent", "good", "fair", "poor", "bad", "unknown"]:
            count = quality_counts.get(quality, 0)
            pct = (count / len(scores) * 100) if scores else 0
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            lines.append(f"  {quality.capitalize():<10} {bar} {count} ({pct:.0f}%)")
        self.ctx.dialog.msgbox("Quality Overview", "\n".join(lines))

    def _show_best_links(self):
        scores = self._scores_or_explain("Best Links")
        if scores is None:
            return
        known, n_unknown = _split_known(scores)
        sorted_links = sorted(known.items(), key=lambda x: x[1].score, reverse=True)[:15]
        lines = ["BEST QUALITY LINKS", "=" * 60, ""]
        for link_id, score in sorted_links:
            parts = link_id.split("_", 1)
            src = parts[0][:12] if len(parts) > 0 else "?"
            dst = parts[1][:12] if len(parts) > 1 else "?"
            lines.extend([f"[{score.score:5.1f}] {score.quality.value.upper():<10}", f"   {src} → {dst}", f"   SNR: {_snr_text(score)} | Hops: {score.inputs.get('hops', '?')}", ""])
        lines += _unknown_line(n_unknown)
        self.ctx.dialog.msgbox("Best Links", "\n".join(lines))

    def _show_worst_links(self):
        scores = self._scores_or_explain("Worst Links")
        if scores is None:
            return
        known, n_unknown = _split_known(scores)
        sorted_links = sorted(known.items(), key=lambda x: x[1].score)[:15]
        lines = ["WORST QUALITY LINKS", "=" * 60, ""]
        for link_id, score in sorted_links:
            parts = link_id.split("_", 1)
            src = parts[0][:12] if len(parts) > 0 else "?"
            dst = parts[1][:12] if len(parts) > 1 else "?"
            lines.append(f"[{score.score:5.1f}] {score.quality.value.upper():<10}")
            lines.append(f"   {src} → {dst}")
            lines.append(f"   SNR: {_snr_text(score)} | Hops: {score.inputs.get('hops', '?')}")
            if score.recommendations:
                rec = score.recommendations[0][:50]
                lines.append(f"   ! {rec}...")
            lines.append("")
        lines += _unknown_line(n_unknown)
        self.ctx.dialog.msgbox("Worst Links", "\n".join(lines))

    def _show_quality_alerts(self):
        scores = self._scores_or_explain("Quality Alerts")
        if scores is None:
            return
        alerts = [(link_id, score) for link_id, score in scores.items() if score.quality.value in ("poor", "bad")]
        alerts.sort(key=lambda x: x[1].score)
        _known, n_unknown = _split_known(scores)
        if not alerts:
            msg = f"No link is poor or bad.\n\nLinks judged: {len(scores) - n_unknown}"
            if n_unknown:
                msg += (f"\n{n_unknown} link(s) have no signal evidence — not judged, "
                        "so not known to be fine.")
            self.ctx.dialog.msgbox("No Alerts", msg)
            return
        lines = [f"LINK QUALITY ALERTS ({len(alerts)} issues)", "=" * 60, ""]
        for link_id, score in alerts:
            parts = link_id.split("_", 1)
            src = parts[0][:12] if len(parts) > 0 else "?"
            dst = parts[1][:12] if len(parts) > 1 else "?"
            severity = "CRITICAL" if score.quality.value == "bad" else "WARNING"
            lines.append(f"[{severity}] {src} → {dst}")
            lines.append(f"   Score: {score.score:.1f}/100 ({score.quality.value})")
            lines.append(f"   Components: SNR={_num(score.snr_score)} RSSI={_num(score.rssi_score)} Hops={score.hops_score:.0f}")
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
        sf_input = self.ctx.dialog.inputbox(
            "Link Quality Scorer",
            "Spreading factor of the radio that measured the SNR (7-12)\n"
            "LongFast=11, ShortTurbo=7. Empty = unknown (SNR not graded):", "")
        sf = None
        if sf_input:
            try:
                sf = int(sf_input)
            except ValueError:
                pass
        hops_input = self.ctx.dialog.inputbox("Link Quality Scorer", "Enter hop count (default: 1):", "1")
        try:
            hops = int(hops_input) if hops_input else 1
        except ValueError:
            hops = 1
        score = scorer.score(snr=snr, rssi=rssi, hops=hops, sf=sf)
        lines = [
            "LINK QUALITY SCORE", "=" * 50, "",
            f"Overall Score: {score.score:.1f}/100", f"Quality: {score.quality.value.upper()}", "",
            "Component Scores:", "-" * 30,
            f"  SNR:        {_num(score.snr_score, '{:.1f}')}/100  {_snr_text(score)}",
            f"  RSSI:       {_num(score.rssi_score, '{:.1f}')}/100",
            f"  Hops:       {score.hops_score:.1f}/100", f"  Age:        {_num(score.age_score, '{:.1f}')}/100",
            f"  Stability:  {_num(score.stability_score, '{:.1f}')}/100", "",
        ]
        if score.recommendations:
            lines.extend(["Recommendations:", "-" * 30])
            for rec in score.recommendations:
                lines.append(f"  • {rec}")
        self.ctx.dialog.msgbox("Link Score", "\n".join(lines))

    def _show_quality_trends(self):
        scores = self._scores_or_explain("Quality Trends")
        if scores is None:
            return
        lines = ["LINK QUALITY ANALYSIS", "=" * 60, "", "Note: Trend tracking requires continuous monitoring.", "Current snapshot analysis:", ""]
        # UNKNOWN links are not judged: counting them as 0 called a network
        # with no signal data CRITICAL (review B, 2026-10-04).
        scores, n_unknown = _split_known(scores)
        if not scores:
            lines += ["Network Health Score: UNKNOWN — no link has signal evidence"]
            lines += _unknown_line(n_unknown)
            self.ctx.dialog.msgbox("Quality Trends", "\n".join(lines))
            return
        excellent = sum(1 for s in scores.values() if s.quality.value == "excellent")
        good = sum(1 for s in scores.values() if s.quality.value == "good")
        fair = sum(1 for s in scores.values() if s.quality.value == "fair")
        poor = sum(1 for s in scores.values() if s.quality.value == "poor")
        bad = sum(1 for s in scores.values() if s.quality.value == "bad")
        total = len(scores)
        health_score = (excellent * 100 + good * 80 + fair * 60 + poor * 30 + bad * 10) / total if total > 0 else 0
        lines.append(f"Network Health Score: {health_score:.1f}/100 (links with signal evidence)")
        lines += _unknown_line(n_unknown)
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
