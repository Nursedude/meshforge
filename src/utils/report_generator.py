"""
Network Status Report Generator.

Generates comprehensive markdown reports capturing current mesh network state.
Pulls data from all available MeshForge subsystems:
- Health scoring
- Signal trending
- Diagnostic history
- Predictive maintenance
- RF analysis

Usage:
    from utils.report_generator import generate_report, ReportConfig

    report = generate_report()
    print(report)  # Markdown string

    # Or with config:
    config = ReportConfig(include_rf_analysis=True, include_recommendations=True)
    report = generate_report(config=config)

    # Save to file:
    save_report(report, "/path/to/report.md")
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.safe_import import safe_import

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency imports (consolidated via safe_import)
# ---------------------------------------------------------------------------
HealthScorer, format_health_display, get_health_scorer, _HAS_HEALTH_SCORE = safe_import(
    'utils.health_score', 'HealthScorer', 'format_health_display', 'get_health_scorer'
)
SignalTrendingManager, _HAS_SIGNAL_TRENDING = safe_import(
    'utils.signal_trending', 'SignalTrendingManager'
)
MaintenancePredictor, _HAS_PREDICTIVE_MAINTENANCE = safe_import(
    'utils.predictive_maintenance', 'MaintenancePredictor'
)
get_diagnostic_engine, Category, _HAS_DIAGNOSTIC_ENGINE = safe_import(
    'utils.diagnostic_engine', 'get_diagnostic_engine', 'Category'
)
PresetAnalyzer, _HAS_PRESET_IMPACT = safe_import(
    'utils.preset_impact', 'PresetAnalyzer'
)
__version__, _HAS_VERSION = safe_import('__version__', '__version__')


@dataclass
class ReportConfig:
    """Configuration for report generation."""
    title: str = "MeshForge Network Status Report"
    include_health: bool = True
    include_signals: bool = True
    include_diagnostics: bool = True
    include_maintenance: bool = True
    include_rf_analysis: bool = True
    include_recommendations: bool = True
    include_metadata: bool = True
    max_diagnostic_entries: int = 20
    max_signal_nodes: int = 50


@dataclass
class ReportSection:
    """A section of the report."""
    heading: str
    level: int  # 1=H1, 2=H2, 3=H3
    content: str
    order: int = 0


class ReportGenerator:
    """
    Generates comprehensive markdown network status reports.

    Collects data from all available MeshForge subsystems and
    formats into a single coherent report.
    """

    def __init__(self, config: Optional[ReportConfig] = None):
        self.config = config or ReportConfig()
        self._sections: List[ReportSection] = []

    def generate(self) -> str:
        """
        Generate the full report.

        Returns:
            Markdown-formatted report string
        """
        self._sections = []

        # Header
        self._add_header()

        # Sections based on config
        if self.config.include_health:
            self._add_health_section()

        if self.config.include_signals:
            self._add_signal_section()

        if self.config.include_maintenance:
            self._add_maintenance_section()

        if self.config.include_diagnostics:
            self._add_diagnostics_section()

        if self.config.include_rf_analysis:
            self._add_rf_section()

        if self.config.include_recommendations:
            self._add_recommendations_section()

        if self.config.include_metadata:
            self._add_metadata_section()

        # Assemble
        return self._assemble_report()

    def _add_header(self) -> None:
        """Add report header."""
        now = datetime.now()
        content = f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        self._sections.append(ReportSection(
            heading=self.config.title,
            level=1,
            content=content,
            order=0,
        ))

    def _add_health_section(self) -> None:
        """Add network health scoring section."""
        lines = []

        if _HAS_HEALTH_SCORE:
            try:
                scorer = _get_health_scorer()
                if scorer is None:
                    lines.append("*Health scorer not initialized — no node data available.*")
                else:
                    snapshot = scorer.get_snapshot()
                if scorer is not None and not snapshot.node_count and not snapshot.service_count:
                    # With nothing reporting, the scorer's category DEFAULTS
                    # rendered as "65/100 (fair)" (truth sweep level two,
                    # 2026-09-22) — a score of nothing is not a score.
                    lines.append("**Overall Score: UNKNOWN** — no nodes or services "
                                 "reporting to the health scorer; nothing was measured.")
                elif scorer is not None:
                    lines.append(f"**Overall Score: {snapshot.overall_score:.0f}/100** "
                                 f"({snapshot.status})")
                    lines.append("")
                    lines.append("| Category | Score | Status |")
                    lines.append("|----------|-------|--------|")
                    for cat, score in snapshot.category_scores.items():
                        status = _score_status(score)
                        lines.append(f"| {cat.title()} | {score:.0f} | {status} |")
                    lines.append("")
                    lines.append(f"- Nodes reporting: {snapshot.node_count}")
                    lines.append(f"- Services tracked: {snapshot.service_count}")

                    trend = scorer.get_trend()
                    if trend != 'stable':
                        lines.append(f"- Trend: **{trend}**")
            except Exception as e:
                lines.append(f"*Error collecting health data: {e}*")
        else:
            lines.append("*Health score module not available.*")

        self._sections.append(ReportSection(
            heading="Network Health",
            level=2,
            content="\n".join(lines),
            order=10,
        ))

    def _add_signal_section(self) -> None:
        """Add signal trending section."""
        lines = []

        if _HAS_SIGNAL_TRENDING:
            try:
                manager = _get_signal_manager()
                if manager is None:
                    lines.append("*Signal trending not initialized — no signal data available.*")
                else:
                    nodes = manager.get_tracked_nodes()
                    if not nodes:
                        lines.append("*No nodes currently tracked.*")
                    else:
                        lines.append(f"Tracking {len(nodes)} node(s).\n")
                        lines.append("| Node | Current SNR | Current RSSI | Trend | Samples |")
                        lines.append("|------|-------------|--------------|-------|---------|")

                        for node_id in sorted(nodes)[:self.config.max_signal_nodes]:
                            report = manager.get_report(node_id)
                            if report:
                                snr_str = f"{report.current_snr:.1f} dB" if report.current_snr else "N/A"
                                rssi_str = f"{report.current_rssi:.0f} dBm" if report.current_rssi else "N/A"
                                trend = report.trend if hasattr(report, 'trend') else "—"
                                samples = report.sample_count if hasattr(report, 'sample_count') else "—"
                                lines.append(f"| {node_id} | {snr_str} | {rssi_str} | {trend} | {samples} |")

                        # Check for degrading nodes
                        degrading = manager.get_degrading_nodes()
                        if degrading:
                            lines.append(f"\n**Warning:** {len(degrading)} node(s) showing signal degradation.")
            except Exception as e:
                lines.append(f"*Error collecting signal data: {e}*")
        else:
            lines.append("*Signal trending module not available.*")

        self._sections.append(ReportSection(
            heading="Signal Quality",
            level=2,
            content="\n".join(lines),
            order=20,
        ))

    def _add_maintenance_section(self) -> None:
        """Add predictive maintenance section."""
        lines = []

        if _HAS_PREDICTIVE_MAINTENANCE:
            try:
                predictor = _get_maintenance_predictor()
                if predictor is None:
                    lines.append("*Maintenance predictor not initialized — no telemetry data.*")
                else:
                    node_ids = predictor.get_node_ids()
                    if not node_ids:
                        lines.append("*No nodes tracked for maintenance.*")
                    else:
                        # Battery forecasts
                        forecasts = predictor.get_all_forecasts()
                        battery_nodes = [f for f in forecasts.values()
                                         if f.trend != 'insufficient_data']
                        if battery_nodes:
                            lines.append("### Battery Status\n")
                            lines.append("| Node | Level | Drain Rate | Hours to Critical | Trend |")
                            lines.append("|------|-------|------------|-------------------|-------|")
                            for f in sorted(battery_nodes, key=lambda x: x.current_pct):
                                rate = f"{f.drain_rate_pct_per_hour:.2f}%/h" if f.trend == 'draining' else "—"
                                critical = f"{f.hours_to_critical:.0f}h" if f.hours_to_critical else "—"
                                lines.append(f"| {f.node_id} | {f.current_pct:.0f}% | {rate} | "
                                             f"{critical} | {f.trend} |")

                        # Dropout patterns
                        patterns = predictor.get_all_patterns()
                        problem_nodes = [p for p in patterns.values()
                                         if p.prediction in ('intermittent', 'failing')]
                        if problem_nodes:
                            lines.append("\n### Node Reliability Issues\n")
                            lines.append("| Node | Uptime | Dropouts/Day | Pattern | Reliability |")
                            lines.append("|------|--------|--------------|---------|-------------|")
                            for p in sorted(problem_nodes, key=lambda x: x.reliability_score):
                                lines.append(f"| {p.node_id} | {p.uptime_pct:.0f}% | "
                                             f"{p.dropouts_per_day:.1f} | {p.prediction} | "
                                             f"{p.reliability_score:.0f}/100 |")

                        # Recommendations
                        recs = predictor.get_maintenance_recommendations()
                        if recs:
                            lines.append("\n### Maintenance Actions\n")
                            for rec in recs[:10]:
                                icon = {'urgent': '!!!', 'soon': '!!',
                                        'scheduled': '!', 'monitor': '?'}.get(rec.priority, '')
                                lines.append(f"- **[{rec.priority.upper()}]** {rec.node_id}: "
                                             f"{rec.action}")
                                lines.append(f"  - Reason: {rec.reason}")

                        if not battery_nodes and not problem_nodes and not recs:
                            lines.append("All tracked nodes are healthy. No maintenance needed.")
            except Exception as e:
                lines.append(f"*Error collecting maintenance data: {e}*")
        else:
            lines.append("*Predictive maintenance module not available.*")

        self._sections.append(ReportSection(
            heading="Predictive Maintenance",
            level=2,
            content="\n".join(lines),
            order=30,
        ))

    def _add_diagnostics_section(self) -> None:
        """Add diagnostic history section."""
        lines = []

        if _HAS_DIAGNOSTIC_ENGINE:
            try:
                engine = get_diagnostic_engine()

                # Health summary
                summary = engine.get_health_summary()
                lines.append(f"**System Health:** {summary.get('overall_health', 'unknown')}")
                lines.append(f"- Symptoms last hour: {summary.get('symptoms_last_hour', 0)}")
                lines.append(f"- Total diagnosed: {summary['stats'].get('diagnoses_made', 0)}")
                lines.append(f"- Auto-recoveries: {summary['stats'].get('auto_recoveries', 0)}")

                # Recent diagnoses
                recent = engine.get_recent_diagnoses(limit=self.config.max_diagnostic_entries)
                if recent:
                    lines.append(f"\n### Recent Diagnoses ({len(recent)})\n")
                    lines.append("| Time | Category | Cause | Confidence |")
                    lines.append("|------|----------|-------|------------|")
                    for d in recent[-10:]:
                        ts = d.symptom.timestamp.strftime('%H:%M:%S') if hasattr(d.symptom.timestamp, 'strftime') else '—'
                        cat = d.symptom.category.value
                        cause = d.likely_cause[:50]
                        lines.append(f"| {ts} | {cat} | {cause} | {d.confidence:.0%} |")

                # Recurring issues
                recurring = engine.get_recurring_issues(threshold=2, hours=24)
                if recurring:
                    lines.append("\n### Recurring Issues\n")
                    for issue in recurring[:5]:
                        lines.append(f"- **{issue['likely_cause']}** "
                                     f"({issue['count']}x in {issue['category']})")
            except Exception as e:
                lines.append(f"*Error collecting diagnostic data: {e}*")
        else:
            lines.append("*Diagnostic engine not available.*")

        self._sections.append(ReportSection(
            heading="Diagnostics",
            level=2,
            content="\n".join(lines),
            order=40,
        ))

    def _add_rf_section(self) -> None:
        """Add RF analysis section."""
        lines = []

        if _HAS_PRESET_IMPACT:
            try:
                analyzer = PresetAnalyzer()

                # Current preset analysis
                lines.append("### LoRa Preset Summary\n")
                lines.append("| Preset | Max Range (LOS) | Sensitivity | Throughput |")
                lines.append("|--------|-----------------|-------------|------------|")

                key_presets = ['SHORT_FAST', 'MEDIUM_FAST', 'LONG_FAST', 'LONG_SLOW']
                for preset in key_presets:
                    try:
                        impact = analyzer.analyze_preset(preset)
                        lines.append(f"| {preset} | {impact.max_range_los_km:.1f} km | "
                                     f"{impact.sensitivity_dbm:.1f} dBm | "
                                     f"{impact.throughput_bps:.0f} bps |")
                    except Exception as e:
                        logger.debug(f"Preset {preset} analysis failed: {e}")
            except Exception as e:
                lines.append(f"*Error in RF analysis: {e}*")
        else:
            lines.append("*RF analysis module not available.*")

        self._sections.append(ReportSection(
            heading="RF Analysis",
            level=2,
            content="\n".join(lines),
            order=50,
        ))

    def _add_recommendations_section(self) -> None:
        """Add actionable recommendations section."""
        lines = []
        recommendations = []

        # Gather recommendations from all subsystems
        try:
            scorer = _get_health_scorer()
            if scorer:
                snapshot = scorer.get_snapshot()
                if snapshot.overall_score < 50:
                    recommendations.append(
                        ("urgent", "Network health is critical — investigate immediately"))
                elif snapshot.overall_score < 70:
                    recommendations.append(
                        ("soon", "Network health is degraded — review node connectivity"))

                for cat, score in snapshot.category_scores.items():
                    if score < 40:
                        recommendations.append(
                            ("soon", f"{cat.title()} score is low ({score:.0f}/100) — needs attention"))
        except Exception as e:
            logger.debug(f"Error collecting health recommendations: {e}")

        try:
            predictor = _get_maintenance_predictor()
            if predictor:
                for rec in predictor.get_maintenance_recommendations()[:5]:
                    recommendations.append((rec.priority, f"{rec.node_id}: {rec.action}"))
        except Exception as e:
            logger.debug(f"Error collecting maintenance recommendations: {e}")

        if recommendations:
            # Sort by priority
            priority_order = {'urgent': 0, 'soon': 1, 'scheduled': 2, 'monitor': 3}
            recommendations.sort(key=lambda r: priority_order.get(r[0], 4))

            for priority, action in recommendations:
                icon = {'urgent': '[!!!]', 'soon': '[!!]',
                        'scheduled': '[!]', 'monitor': '[?]'}.get(priority, '-')
                lines.append(f"- {icon} **[{priority.upper()}]** {action}")
        else:
            lines.append("No actionable recommendations at this time. Network is healthy.")

        self._sections.append(ReportSection(
            heading="Recommendations",
            level=2,
            content="\n".join(lines),
            order=60,
        ))

    def _add_metadata_section(self) -> None:
        """Add report metadata."""
        lines = []

        if _HAS_VERSION:
            lines.append(f"- MeshForge Version: {__version__}")
        else:
            lines.append("- MeshForge Version: unknown")

        lines.append(f"- Report Generated: {datetime.now().isoformat()}")
        lines.append(f"- Host: {_get_hostname()}")
        lines.append(f"- Python: {_get_python_version()}")

        self._sections.append(ReportSection(
            heading="Report Metadata",
            level=2,
            content="\n".join(lines),
            order=99,
        ))

    def _assemble_report(self) -> str:
        """Assemble all sections into final markdown."""
        parts = []
        sorted_sections = sorted(self._sections, key=lambda s: s.order)

        for section in sorted_sections:
            prefix = "#" * section.level
            parts.append(f"{prefix} {section.heading}\n")
            parts.append(section.content)
            parts.append("")  # Blank line between sections

        return "\n".join(parts)


# =============================================================================
# Module-level convenience functions
# =============================================================================

def generate_report(config: Optional[ReportConfig] = None) -> str:
    """
    Generate a network status report.

    Args:
        config: Optional ReportConfig to customize output

    Returns:
        Markdown-formatted report string
    """
    generator = ReportGenerator(config)
    return generator.generate()


def save_report(report: str, path: str) -> str:
    """
    Save a report to a file.

    Args:
        report: Markdown report content
        path: File path to save to

    Returns:
        Absolute path of saved file
    """
    from pathlib import Path
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(report)
    logger.info(f"Report saved to {file_path}")
    return str(file_path.resolve())


def generate_and_save(path: Optional[str] = None,
                      config: Optional[ReportConfig] = None) -> str:
    """
    Generate and save a report.

    Args:
        path: Optional file path (defaults to timestamped file in config dir)
        config: Optional ReportConfig

    Returns:
        Path to saved report file
    """
    if path is None:
        from utils.paths import get_real_user_home
        reports_dir = get_real_user_home() / ".config" / "meshforge" / "reports"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(reports_dir / f"status_report_{timestamp}.md")

    report = generate_report(config)
    return save_report(report, path)


# =============================================================================
# Private helpers — lazy singleton access
# =============================================================================

_signal_manager = None
_maintenance_predictor = None


def _get_health_scorer():
    """Get shared health scorer instance if available."""
    if not _HAS_HEALTH_SCORE:
        return None
    return get_health_scorer()


def _get_signal_manager():
    """Get signal trending manager if available."""
    global _signal_manager
    if not _HAS_SIGNAL_TRENDING:
        return None
    if _signal_manager is None:
        _signal_manager = SignalTrendingManager()
    return _signal_manager


def _get_maintenance_predictor():
    """Get maintenance predictor if available."""
    global _maintenance_predictor
    if not _HAS_PREDICTIVE_MAINTENANCE:
        return None
    if _maintenance_predictor is None:
        _maintenance_predictor = MaintenancePredictor()
    return _maintenance_predictor


def _score_status(score: float) -> str:
    """Convert numeric score to status text."""
    if score >= 80:
        return "Good"
    elif score >= 60:
        return "Fair"
    elif score >= 40:
        return "Degraded"
    else:
        return "Critical"


def _get_hostname() -> str:
    """Get current hostname."""
    import socket
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def _get_python_version() -> str:
    """Get Python version string."""
    import sys
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
