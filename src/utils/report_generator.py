"""
Network Status Report Generator — the verified screens, in one document.

Rebuilt 2026-09-25 by the TUI live-truth pass. The previous report read four
in-process singletons (health scorer, signal trending manager, maintenance
predictor, diagnostic engine) that nothing in the TUI process ever feeds. On a
box with 334 radio nodes and 410 in the history it printed "No nodes currently
tracked", an UNKNOWN score, and then turned that UNKNOWN into the
recommendation "Network health is degraded" — a finding from no measurement.

Every number now comes from a source a Dashboard screen already reads and the
live pass checked against the real system:
  * Nodes        — utils.node_counts (radio's own telemetry, map view, rnsd path table)
  * Node history — utils.node_history_analytics (snapshots, link trends, falling battery/SNR)
  * Watchdog / delivery — monitoring.traffic_pulse (the /api/status watchdog
                   block and the delivery QA verdict)
  * RF reference — utils.preset_impact, labelled as physics, not measurement

Each source is tri-state: a value, or UNKNOWN with why. "Findings" lists only
what was measured; sources that could not be read are listed as not observed,
never folded into "healthy" (honest_failure_modes #2).

Usage:
    from utils.report_generator import generate_report, generate_and_save
    print(generate_report())
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from __version__ import __version__
from utils import node_counts
from utils import node_history_analytics as nha
from utils.preset_impact import PresetAnalyzer

logger = logging.getLogger(__name__)

REFERENCE_PRESETS = ("SHORT_TURBO", "SHORT_FAST", "MEDIUM_FAST", "LONG_FAST", "LONG_SLOW")


@dataclass
class ReportConfig:
    """Configuration for report generation."""
    title: str = "MeshForge Network Status Report"
    include_nodes: bool = True
    include_history: bool = True
    include_watchdog: bool = True
    include_rf_reference: bool = True
    include_findings: bool = True
    include_metadata: bool = True
    max_listed: int = 10


@dataclass
class ReportSection:
    """A section of the report."""
    heading: str
    level: int  # 1=H1, 2=H2, 3=H3
    content: str
    order: int = 0


def _pulse() -> Dict[str, Any]:
    """The Traffic Heartbeat snapshot (watchdog + delivery QA). Lazy import:
    monitoring is a heavier package and must not break report import."""
    from monitoring.traffic_pulse import pulse_snapshot
    return pulse_snapshot()


class ReportGenerator:
    """Collects each source once, then renders sections and findings from it."""

    def __init__(self, config: Optional[ReportConfig] = None):
        self.config = config or ReportConfig()
        self._sections: List[ReportSection] = []
        self._findings: List[Tuple[str, str]] = []   # (priority, text)
        self._unobserved: List[str] = []

    def generate(self) -> str:
        """Generate the full report as markdown."""
        self._sections, self._findings, self._unobserved = [], [], []
        self._add_header()
        if self.config.include_nodes:
            self._add_nodes_section()
        if self.config.include_history:
            self._add_history_section()
        if self.config.include_watchdog:
            self._add_watchdog_section()
        if self.config.include_rf_reference:
            self._add_rf_section()
        if self.config.include_findings:
            self._add_findings_section()
        if self.config.include_metadata:
            self._add_metadata_section()
        return self._assemble_report()

    # ── sections ─────────────────────────────────────────────────────
    def _add(self, heading: str, lines: List[str], order: int) -> None:
        self._sections.append(ReportSection(heading, 2, "\n".join(lines), order))

    def _add_header(self) -> None:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self._sections.append(ReportSection(
            self.config.title, 1,
            f"Generated: {now} · scope: this box only\n", 0))

    def _add_nodes_section(self) -> None:
        lines = []
        radio = _safe(node_counts.radio_self_report, {"online": None})
        if radio.get("online") is not None:
            age = radio.get("age_s")
            when = f", reported {age / 60:.0f} min ago" if age is not None else ""
            lines.append(f"- Meshtastic radio: **{radio['online']} online / "
                         f"{radio['total']} known** ({radio['source']}{when})")
        else:
            lines.append(f"- Meshtastic radio: UNKNOWN — {radio.get('why', 'no answer')}")
            self._unobserved.append("radio node count")
        view = _safe(node_counts.meshtastic_radio_nodes, {"count": None})
        if view.get("count") is not None:
            lines.append(f"- Map collector: {view['count']} nodes ({view['source']})")
        else:
            lines.append(f"- Map collector: UNKNOWN — {view.get('why', 'no answer')}")
        rns = _safe(node_counts.rns_path_table_counts, {"network": None})
        if rns.get("network") is not None:
            lines.append(f"- RNS: **{rns['network']} network destinations** "
                         f"(+{rns['ipc']} local IPC peers; {rns['source']})")
        else:
            lines.append(f"- RNS: UNKNOWN — {rns.get('why', 'no answer')}")
            self._unobserved.append("RNS path table")
        self._add("Nodes", lines, 10)

    def _add_history_section(self) -> None:
        lines = []
        tl = _safe(nha.health_timeline, {"state": "unreadable", "error": "raised"})
        if tl.get("state") != "ok":
            lines.append(f"UNKNOWN — node history {_why(tl)}")
            self._unobserved.append("node history")
            self._add("Node History", lines, 20)
            return
        full = [h for h in tl["hours"] if h.get("snapshots")]
        if full:
            h = full[-1]
            snr = h["avg_snr_online"]
            lines.append(f"Latest snapshot hour ({datetime.fromtimestamp(h['hour_epoch']):%m-%d %H:00}): "
                         f"**{h['known']} known, {h['online']} online**, mean SNR of online "
                         f"nodes {'—' if snr is None else f'{snr:.1f} dB'} "
                         f"({h['snr_samples']} readings)")
        else:
            lines.append("No full snapshot in the window yet.")
        tr = _safe(nha.link_trends, {"state": "unreadable", "error": "raised"})
        if tr.get("state") == "ok":
            dec = tr["declining"]
            lines.append(f"\nLink trends: {tr['nodes_judged']} of {tr['nodes_with_snr']} nodes judged "
                         f"(first {tr['edge_h']:.0f} h vs last {tr['edge_h']:.0f} h); "
                         f"{len(dec)} declining.")
            for r in dec[:self.config.max_listed]:
                lines.append(f"- {r['name']}: {r['snr_first']:.1f} → {r['snr_last']:.1f} dB "
                             f"({r['delta_db']:+.1f})")
            for r in dec:
                if r["delta_db"] <= -6.0:
                    self._findings.append(("soon", f"{r['name']}: SNR fell {-r['delta_db']:.1f} dB"))
        else:
            lines.append(f"\nLink trends: UNKNOWN — {_why(tr)}")
        pr = _safe(nha.predictive, {"state": "unreadable", "error": "raised"})
        if pr.get("state") == "ok":
            alerts = pr["alerts"]
            lines.append(f"\nFalling battery / SNR: {len(alerts)} alert(s) from "
                         f"{pr['battery_nodes_judged']} battery and {pr['snr_nodes_judged']} SNR "
                         f"series with ≥{pr['min_samples']} readings.")
            for a in alerts[:self.config.max_listed]:
                if a["kind"] == "battery":
                    txt = (f"{a['name']}: battery {a['last']:.0f}% falling {-a['slope']:.2f} %/h, "
                           f"~{a['eta_h_to_floor']:.0f} h to floor")
                    self._findings.append(("urgent" if a["eta_h_to_floor"] < 24 else "soon", txt))
                else:
                    txt = f"{a['name']}: SNR falling {-a['slope']:.2f} dB/h (last {a['last']:.1f})"
                    self._findings.append(("monitor", txt))
                lines.append(f"- {txt}")
        else:
            lines.append(f"\nFalling battery / SNR: UNKNOWN — {_why(pr)}")
        lines.append("\n*Source: node_history.db on this box (the map collector's snapshots; "
                     "position-less nodes are not recorded).*")
        self._add("Node History", lines, 20)

    def _add_watchdog_section(self) -> None:
        lines = []
        try:
            snap = _pulse()
        except Exception as e:  # the heartbeat's contract says it won't; be safe
            logger.debug("report: pulse snapshot failed: %s", e)
            self._unobserved += ["watchdog signals", "delivery QA"]
            self._add("Watchdog & Delivery", [f"UNKNOWN — heartbeat snapshot failed ({e})"], 30)
            return
        diag, qa = snap.get("diag") or {}, snap.get("qa") or {}
        lines.append(f"- Watchdog: {diag.get('status', 'unobservable')} — {diag.get('detail', '')}")
        if diag.get("status") == "unobservable":
            self._unobserved.append("watchdog signals")
        for s in (diag.get("signals") or [])[:self.config.max_listed]:
            lines.append(f"  - {s.get('cls')} · {s.get('subject')}")
            self._findings.append(("soon", f"watchdog signal {s.get('cls')} on {s.get('subject')}"))
        lines.append(f"- Delivery QA: {qa.get('status', 'unobservable')} — "
                     f"{qa.get('verdict') or qa.get('detail', '')}")
        if qa.get("status") == "unobservable":
            self._unobserved.append("delivery QA")
        elif qa.get("status") == "alert":
            self._findings.append(("urgent", f"delivery: {qa.get('verdict')}"))
        self._add("Watchdog & Delivery", lines, 30)

    def _add_rf_section(self) -> None:
        lines = ["*Reference figures computed from LoRa physics (receiver sensitivity at "
                 "the preset's SF/BW, raw bit rate) — not measurements of this network.*\n",
                 "| Preset | Sensitivity | Raw bit rate |",
                 "|--------|-------------|--------------|"]
        analyzer = PresetAnalyzer()
        for preset in REFERENCE_PRESETS:
            try:
                p = analyzer.analyze_preset(preset)
                lines.append(f"| {preset} | {p.sensitivity_dbm:.1f} dBm | {p.throughput_bps:.0f} bps |")
            except Exception as e:  # an unknown preset name is skipped, not fatal
                logger.debug("report: preset %s: %s", preset, e)
        self._add("RF Reference", lines, 50)

    def _add_findings_section(self) -> None:
        order = {"urgent": 0, "soon": 1, "scheduled": 2, "monitor": 3}
        lines = [f"- **[{p.upper()}]** {t}"
                 for p, t in sorted(self._findings, key=lambda f: order.get(f[0], 4))]
        if not lines:
            lines.append("Nothing measured calls for action.")
        if self._unobserved:
            lines.append(f"\n**Not observed** (UNKNOWN, not healthy): {', '.join(self._unobserved)}.")
        self._add("Findings", lines, 60)

    def _add_metadata_section(self) -> None:
        import socket
        import sys
        v = sys.version_info
        self._add("Report Metadata", [
            f"- MeshForge Version: {__version__}",
            f"- Report Generated: {datetime.now().isoformat()}",
            f"- Host: {socket.gethostname()}",
            f"- Python: {v.major}.{v.minor}.{v.micro}",
        ], 99)

    def _assemble_report(self) -> str:
        parts = []
        for section in sorted(self._sections, key=lambda s: s.order):
            parts += [f"{'#' * section.level} {section.heading}\n", section.content, ""]
        return "\n".join(parts)


def _safe(fn, fallback: Dict[str, Any]) -> Dict[str, Any]:
    """Call a source; an exception becomes UNKNOWN with the reason, never a value."""
    try:
        return fn()
    except Exception as e:
        logger.debug("report: %s failed: %s", getattr(fn, "__name__", fn), e)
        return {**fallback, "why": f"{e.__class__.__name__}: {e}"}


def _why(res: Dict[str, Any]) -> str:
    state = res.get("state", "unknown")
    detail = res.get("error") or res.get("why") or res.get("path") or ""
    return f"{state}{f' ({detail})' if detail else ''}"


# =============================================================================
# Module-level convenience functions
# =============================================================================

def generate_report(config: Optional[ReportConfig] = None) -> str:
    """Generate a network status report (markdown)."""
    return ReportGenerator(config).generate()


def save_report(report: str, path: str) -> str:
    """Save a report to a file; returns the absolute path."""
    from pathlib import Path
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(report)
    logger.info(f"Report saved to {file_path}")
    return str(file_path.resolve())


def generate_and_save(path: Optional[str] = None,
                      config: Optional[ReportConfig] = None) -> str:
    """Generate and save a report (default: timestamped file in the config dir)."""
    if path is None:
        from utils.paths import get_real_user_home
        reports_dir = get_real_user_home() / ".config" / "meshforge" / "reports"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(reports_dir / f"status_report_{timestamp}.md")
    return save_report(generate_report(config), path)
