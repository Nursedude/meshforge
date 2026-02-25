"""
Coverage Analytics and Link Budget History

Provides network analysis over time, tracking:
- Coverage area calculations
- Link budget history and trends
- Network health metrics
- Node connectivity patterns
"""

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


@dataclass
class LinkBudgetSample:
    """Single link budget measurement."""
    timestamp: str
    source_node: str
    dest_node: str
    rssi_dbm: float
    snr_db: float
    distance_km: Optional[float]
    packet_loss_pct: float
    link_quality: str  # excellent, good, fair, bad


@dataclass
class CoverageStats:
    """Coverage area statistics."""
    total_nodes: int
    nodes_with_position: int
    bounding_box: Dict[str, float]  # min_lat, max_lat, min_lon, max_lon
    center_point: Tuple[float, float]
    estimated_area_km2: float
    average_node_spacing_km: float
    coverage_radius_km: float  # Estimated effective radius


@dataclass
class NetworkHealthMetrics:
    """Aggregate network health metrics."""
    timestamp: str
    online_nodes: int
    offline_nodes: int
    avg_rssi_dbm: float
    avg_snr_db: float
    avg_link_quality_pct: float
    packet_success_rate: float
    uptime_hours: float


class AnalyticsStore:
    """SQLite-based storage for analytics data."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = get_real_user_home() / ".config" / "meshforge" / "analytics.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        self._last_cleanup = 0.0  # epoch time of last auto-cleanup
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()

                # Link budget history
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS link_budget_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        source_node TEXT NOT NULL,
                        dest_node TEXT NOT NULL,
                        rssi_dbm REAL,
                        snr_db REAL,
                        distance_km REAL,
                        packet_loss_pct REAL,
                        link_quality TEXT
                    )
                """)

                # Network health snapshots
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS network_health (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        online_nodes INTEGER,
                        offline_nodes INTEGER,
                        avg_rssi_dbm REAL,
                        avg_snr_db REAL,
                        avg_link_quality_pct REAL,
                        packet_success_rate REAL
                    )
                """)

                # Coverage snapshots
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS coverage_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        total_nodes INTEGER,
                        nodes_with_position INTEGER,
                        min_lat REAL,
                        max_lat REAL,
                        min_lon REAL,
                        max_lon REAL,
                        center_lat REAL,
                        center_lon REAL,
                        area_km2 REAL,
                        avg_spacing_km REAL,
                        coverage_radius_km REAL
                    )
                """)

                # Create indexes for efficient queries
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_link_budget_timestamp
                    ON link_budget_history(timestamp)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_link_budget_nodes
                    ON link_budget_history(source_node, dest_node)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_network_health_timestamp
                    ON network_health(timestamp)
                """)

                conn.commit()
            finally:
                conn.close()

    # Auto-cleanup interval: once per hour
    AUTO_CLEANUP_INTERVAL = 3600

    def _maybe_cleanup(self):
        """Periodically clean up old analytics data to prevent unbounded disk growth."""
        now = time.time()
        if now - self._last_cleanup < self.AUTO_CLEANUP_INTERVAL:
            return
        self._last_cleanup = now
        try:
            self.cleanup_old_data(days=30)
        except Exception as e:
            logger.debug(f"Analytics auto-cleanup error: {e}")

    def record_link_budget(self, sample: LinkBudgetSample):
        """Record a link budget measurement."""
        self._maybe_cleanup()
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO link_budget_history
                    (timestamp, source_node, dest_node, rssi_dbm, snr_db,
                     distance_km, packet_loss_pct, link_quality)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sample.timestamp, sample.source_node, sample.dest_node,
                    sample.rssi_dbm, sample.snr_db, sample.distance_km,
                    sample.packet_loss_pct, sample.link_quality
                ))
                conn.commit()
            finally:
                conn.close()

    def record_network_health(self, metrics: NetworkHealthMetrics):
        """Record network health snapshot."""
        self._maybe_cleanup()
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO network_health
                    (timestamp, online_nodes, offline_nodes, avg_rssi_dbm,
                     avg_snr_db, avg_link_quality_pct, packet_success_rate)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    metrics.timestamp, metrics.online_nodes, metrics.offline_nodes,
                    metrics.avg_rssi_dbm, metrics.avg_snr_db,
                    metrics.avg_link_quality_pct, metrics.packet_success_rate
                ))
                conn.commit()
            finally:
                conn.close()

    def record_coverage(self, stats: CoverageStats):
        """Record coverage snapshot."""
        self._maybe_cleanup()
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO coverage_snapshots
                    (timestamp, total_nodes, nodes_with_position, min_lat, max_lat,
                     min_lon, max_lon, center_lat, center_lon, area_km2,
                     avg_spacing_km, coverage_radius_km)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    datetime.now().isoformat(),
                    stats.total_nodes, stats.nodes_with_position,
                    stats.bounding_box.get('min_lat'),
                    stats.bounding_box.get('max_lat'),
                    stats.bounding_box.get('min_lon'),
                    stats.bounding_box.get('max_lon'),
                    stats.center_point[0], stats.center_point[1],
                    stats.estimated_area_km2, stats.average_node_spacing_km,
                    stats.coverage_radius_km
                ))
                conn.commit()
            finally:
                conn.close()

    def get_link_budget_history(
        self,
        source_node: Optional[str] = None,
        dest_node: Optional[str] = None,
        hours: int = 24
    ) -> List[LinkBudgetSample]:
        """Get link budget history for time period."""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                query = "SELECT * FROM link_budget_history WHERE timestamp > ?"
                params = [cutoff]

                if source_node:
                    query += " AND source_node = ?"
                    params.append(source_node)
                if dest_node:
                    query += " AND dest_node = ?"
                    params.append(dest_node)

                query += " ORDER BY timestamp DESC"
                cursor.execute(query, params)
                rows = cursor.fetchall()

                samples = []
                for row in rows:
                    samples.append(LinkBudgetSample(
                        timestamp=row[1],
                        source_node=row[2],
                        dest_node=row[3],
                        rssi_dbm=row[4],
                        snr_db=row[5],
                        distance_km=row[6],
                        packet_loss_pct=row[7],
                        link_quality=row[8]
                    ))
                return samples
            finally:
                conn.close()

    def get_network_health_history(self, hours: int = 24) -> List[NetworkHealthMetrics]:
        """
        Get network health history for time period.

        Args:
            hours: Number of hours to look back (default 24)

        Returns:
            List of NetworkHealthMetrics objects

        API Contract:
            - ALWAYS returns a list (never None)
            - Empty list if no data in time period
            - Results ordered by timestamp descending (newest first)
            - Thread-safe (uses internal lock)
            - Tests: tests/test_analytics.py::TestAnalyticsStore
        """
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM network_health
                    WHERE timestamp > ? ORDER BY timestamp DESC
                """, (cutoff,))
                rows = cursor.fetchall()

                metrics = []
                for row in rows:
                    metrics.append(NetworkHealthMetrics(
                        timestamp=row[1],
                        online_nodes=row[2],
                        offline_nodes=row[3],
                        avg_rssi_dbm=row[4],
                        avg_snr_db=row[5],
                        avg_link_quality_pct=row[6],
                        packet_success_rate=row[7],
                        uptime_hours=0  # Calculated separately
                    ))
                return metrics
            finally:
                conn.close()

    def get_link_budget_trends(
        self,
        source_node: str,
        dest_node: str,
        hours: int = 168  # 1 week default
    ) -> Dict[str, Any]:
        """Analyze link budget trends over time."""
        history = self.get_link_budget_history(source_node, dest_node, hours)

        if not history:
            return {
                'has_data': False,
                'sample_count': 0,
            }

        rssi_values = [s.rssi_dbm for s in history if s.rssi_dbm]
        snr_values = [s.snr_db for s in history if s.snr_db]

        def safe_avg(values):
            return sum(values) / len(values) if values else 0

        def safe_min(values):
            return min(values) if values else 0

        def safe_max(values):
            return max(values) if values else 0

        return {
            'has_data': True,
            'sample_count': len(history),
            'period_hours': hours,
            'rssi': {
                'avg': safe_avg(rssi_values),
                'min': safe_min(rssi_values),
                'max': safe_max(rssi_values),
                'trend': self._calculate_trend(rssi_values),
            },
            'snr': {
                'avg': safe_avg(snr_values),
                'min': safe_min(snr_values),
                'max': safe_max(snr_values),
                'trend': self._calculate_trend(snr_values),
            },
            'quality_distribution': self._quality_distribution(history),
        }

    def _calculate_trend(self, values: List[float]) -> str:
        """Calculate trend direction (improving, stable, degrading)."""
        if len(values) < 2:
            return 'insufficient_data'

        # Compare first and last thirds
        third = len(values) // 3
        if third < 1:
            return 'stable'

        recent_avg = sum(values[:third]) / third
        old_avg = sum(values[-third:]) / third

        diff = recent_avg - old_avg
        threshold = 2.0  # dB

        if diff > threshold:
            return 'improving'
        elif diff < -threshold:
            return 'degrading'
        return 'stable'

    def _quality_distribution(self, samples: List[LinkBudgetSample]) -> Dict[str, int]:
        """Calculate quality distribution."""
        dist = {'excellent': 0, 'good': 0, 'fair': 0, 'bad': 0}
        for s in samples:
            if s.link_quality in dist:
                dist[s.link_quality] += 1
        return dist

    def cleanup_old_data(self, days: int = 30):
        """Remove data older than specified days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM link_budget_history WHERE timestamp < ?",
                    (cutoff,)
                )
                cursor.execute(
                    "DELETE FROM network_health WHERE timestamp < ?",
                    (cutoff,)
                )
                cursor.execute(
                    "DELETE FROM coverage_snapshots WHERE timestamp < ?",
                    (cutoff,)
                )
                conn.commit()
                logger.info(f"Cleaned up analytics data older than {days} days")
            finally:
                conn.close()


class CoverageAnalyzer:
    """Analyze network coverage from node positions."""

    def __init__(self, store: Optional[AnalyticsStore] = None):
        self.store = store or AnalyticsStore()

    def analyze_coverage(self, nodes: List[Dict]) -> CoverageStats:
        """
        Analyze coverage from list of nodes with position data.

        Args:
            nodes: List of node dicts with 'lat', 'lon' keys

        Returns:
            CoverageStats with calculated metrics
        """
        # Filter nodes with valid positions
        positioned = [
            n for n in nodes
            if n.get('lat') and n.get('lon') and
            n['lat'] != 0 and n['lon'] != 0
        ]

        if not positioned:
            return CoverageStats(
                total_nodes=len(nodes),
                nodes_with_position=0,
                bounding_box={'min_lat': 0, 'max_lat': 0, 'min_lon': 0, 'max_lon': 0},
                center_point=(0, 0),
                estimated_area_km2=0,
                average_node_spacing_km=0,
                coverage_radius_km=0,
            )

        # Calculate bounding box
        lats = [n['lat'] for n in positioned]
        lons = [n['lon'] for n in positioned]

        min_lat, max_lat = min(lats), max(lats)
        min_lon, max_lon = min(lons), max(lons)

        # Center point
        center_lat = (min_lat + max_lat) / 2
        center_lon = (min_lon + max_lon) / 2

        # Estimate area using bounding box
        # 1 degree lat ~= 111 km, 1 degree lon ~= 111 * cos(lat) km
        import math
        lat_span_km = (max_lat - min_lat) * 111
        lon_span_km = (max_lon - min_lon) * 111 * math.cos(math.radians(center_lat))
        area_km2 = lat_span_km * lon_span_km

        # Average node spacing (simple approximation)
        if len(positioned) > 1:
            avg_spacing = math.sqrt(area_km2 / len(positioned))
        else:
            avg_spacing = 0

        # Coverage radius (diagonal / 2)
        coverage_radius = math.sqrt(lat_span_km**2 + lon_span_km**2) / 2

        stats = CoverageStats(
            total_nodes=len(nodes),
            nodes_with_position=len(positioned),
            bounding_box={
                'min_lat': min_lat, 'max_lat': max_lat,
                'min_lon': min_lon, 'max_lon': max_lon
            },
            center_point=(center_lat, center_lon),
            estimated_area_km2=round(area_km2, 2),
            average_node_spacing_km=round(avg_spacing, 2),
            coverage_radius_km=round(coverage_radius, 2),
        )

        # Save snapshot
        self.store.record_coverage(stats)

        return stats

    def get_coverage_history(self, days: int = 7) -> List[Dict]:
        """Get coverage snapshots over time."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        with self.store._lock:
            conn = sqlite3.connect(self.store.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT timestamp, total_nodes, nodes_with_position,
                           area_km2, coverage_radius_km
                    FROM coverage_snapshots
                    WHERE timestamp > ? ORDER BY timestamp DESC
                """, (cutoff,))
                rows = cursor.fetchall()

                return [
                    {
                        'timestamp': row[0],
                        'total_nodes': row[1],
                        'nodes_with_position': row[2],
                        'area_km2': row[3],
                        'coverage_radius_km': row[4],
                    }
                    for row in rows
                ]
            finally:
                conn.close()


def get_analytics_store() -> AnalyticsStore:
    """Get singleton analytics store instance."""
    global _analytics_store
    try:
        return _analytics_store
    except NameError:
        _analytics_store = AnalyticsStore()
        return _analytics_store


def get_coverage_analyzer() -> CoverageAnalyzer:
    """Get singleton coverage analyzer instance."""
    global _coverage_analyzer
    try:
        return _coverage_analyzer
    except NameError:
        _coverage_analyzer = CoverageAnalyzer(get_analytics_store())
        return _coverage_analyzer


# =============================================================================
# PREDICTIVE ANALYTICS (Sprint B: Predictive Network Health)
# =============================================================================

@dataclass
class PredictiveAlert:
    """A predictive alert for upcoming network issues."""
    alert_type: str  # 'snr_degradation', 'node_offline', 'link_failure'
    severity: str  # 'info', 'warning', 'critical'
    message: str
    predicted_time_hours: Optional[float]  # Hours until predicted event
    confidence: float  # 0.0 to 1.0
    evidence: List[str]
    suggestions: List[str]
    affected_nodes: List[str]
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


class PredictiveAnalyzer:
    """
    Predictive analytics for proactive network health monitoring.

    Analyzes historical trends to predict:
    - Node failures (based on SNR/RSSI degradation)
    - Link degradation (based on packet loss patterns)
    - Network health decline (based on aggregate metrics)

    Usage:
        analyzer = PredictiveAnalyzer()
        alerts = analyzer.analyze_all()
        for alert in alerts:
            if alert.severity == 'critical':
                print(f"CRITICAL: {alert.message}")

    API Contract:
        - analyze_all() returns List[PredictiveAlert] (may be empty)
        - Thread-safe (uses underlying AnalyticsStore lock)
        - Does not modify analytics data
        - Tests: tests/test_predictive_analytics.py
    """

    # Thresholds for predictions
    SNR_DEGRADATION_THRESHOLD = -3.0  # dB drop to trigger warning
    SNR_CRITICAL_THRESHOLD = -10.0  # dB absolute value = critical
    RSSI_DEGRADATION_THRESHOLD = -6.0  # dB drop to trigger warning
    PACKET_LOSS_WARNING = 10.0  # % packet loss
    PACKET_LOSS_CRITICAL = 25.0  # % packet loss
    MIN_SAMPLES_FOR_PREDICTION = 5  # Need at least this many data points

    def __init__(self, store: Optional[AnalyticsStore] = None):
        self.store = store or get_analytics_store()

    def analyze_all(self) -> List[PredictiveAlert]:
        """
        Run all predictive analyses and return alerts.

        Returns:
            List of PredictiveAlert objects, sorted by severity
        """
        alerts: List[PredictiveAlert] = []

        # Analyze network health trends
        alerts.extend(self._analyze_network_health_trends())

        # Analyze link-specific degradation
        alerts.extend(self._analyze_link_degradation())

        # Sort by severity (critical first)
        severity_order = {'critical': 0, 'warning': 1, 'info': 2}
        alerts.sort(key=lambda a: severity_order.get(a.severity, 3))

        return alerts

    def _analyze_network_health_trends(self) -> List[PredictiveAlert]:
        """Analyze aggregate network health for degradation patterns."""
        alerts: List[PredictiveAlert] = []

        # Get 48 hours of health data for trend analysis
        history = self.store.get_network_health_history(hours=48)

        if len(history) < self.MIN_SAMPLES_FOR_PREDICTION:
            return alerts

        # Analyze SNR trend
        snr_values = [h.avg_snr_db for h in history if h.avg_snr_db is not None]
        if len(snr_values) >= self.MIN_SAMPLES_FOR_PREDICTION:
            snr_alert = self._check_metric_degradation(
                values=snr_values,
                metric_name="Average SNR",
                unit="dB",
                warning_drop=abs(self.SNR_DEGRADATION_THRESHOLD),
                critical_absolute=self.SNR_CRITICAL_THRESHOLD,
                higher_is_better=True
            )
            if snr_alert:
                alerts.append(snr_alert)

        # Analyze RSSI trend
        rssi_values = [h.avg_rssi_dbm for h in history if h.avg_rssi_dbm is not None]
        if len(rssi_values) >= self.MIN_SAMPLES_FOR_PREDICTION:
            rssi_alert = self._check_metric_degradation(
                values=rssi_values,
                metric_name="Average RSSI",
                unit="dBm",
                warning_drop=abs(self.RSSI_DEGRADATION_THRESHOLD),
                critical_absolute=-100.0,  # Very weak signal
                higher_is_better=True
            )
            if rssi_alert:
                alerts.append(rssi_alert)

        # Analyze node count trend (nodes going offline)
        online_counts = [h.online_nodes for h in history if h.online_nodes is not None]
        if len(online_counts) >= self.MIN_SAMPLES_FOR_PREDICTION:
            node_alert = self._check_node_count_decline(online_counts)
            if node_alert:
                alerts.append(node_alert)

        return alerts

    def _analyze_link_degradation(self) -> List[PredictiveAlert]:
        """Analyze individual link health for degradation."""
        alerts: List[PredictiveAlert] = []

        # Get recent link budget data
        history = self.store.get_link_budget_history(hours=168)  # 1 week

        if len(history) < self.MIN_SAMPLES_FOR_PREDICTION:
            return alerts

        # Group by link (source -> dest)
        links: Dict[Tuple[str, str], List[LinkBudgetSample]] = {}
        for sample in history:
            key = (sample.source_node, sample.dest_node)
            if key not in links:
                links[key] = []
            links[key].append(sample)

        # Analyze each link
        for (source, dest), samples in links.items():
            if len(samples) < self.MIN_SAMPLES_FOR_PREDICTION:
                continue

            # Check SNR degradation for this link
            snr_values = [s.snr_db for s in samples if s.snr_db is not None]
            if snr_values:
                trend = self._calculate_trend_slope(snr_values)
                recent_avg = sum(snr_values[:min(5, len(snr_values))]) / min(5, len(snr_values))

                if trend < -0.5:  # Degrading more than 0.5 dB per sample period
                    # Estimate time to critical
                    if recent_avg > self.SNR_CRITICAL_THRESHOLD:
                        samples_to_critical = (recent_avg - self.SNR_CRITICAL_THRESHOLD) / abs(trend)
                        hours_to_critical = samples_to_critical * 4  # Assume ~4 hour sample rate

                        severity = 'warning' if hours_to_critical > 24 else 'critical'

                        alerts.append(PredictiveAlert(
                            alert_type='link_snr_degradation',
                            severity=severity,
                            message=f"Link {source} → {dest} SNR degrading at {trend:.1f} dB/day",
                            predicted_time_hours=hours_to_critical,
                            confidence=min(0.9, 0.5 + len(samples) * 0.05),
                            evidence=[
                                f"Current SNR: {recent_avg:.1f} dB",
                                f"Trend: {trend:.2f} dB/sample",
                                f"Samples analyzed: {len(samples)}",
                            ],
                            suggestions=[
                                "Check antenna alignment between nodes",
                                "Investigate new RF interference sources",
                                "Consider adding relay node",
                            ],
                            affected_nodes=[source, dest],
                        ))

            # Check packet loss increase
            loss_values = [s.packet_loss_pct for s in samples if s.packet_loss_pct is not None]
            if loss_values and len(loss_values) >= 3:
                recent_loss = sum(loss_values[:min(3, len(loss_values))]) / min(3, len(loss_values))

                if recent_loss > self.PACKET_LOSS_CRITICAL:
                    alerts.append(PredictiveAlert(
                        alert_type='link_packet_loss',
                        severity='critical',
                        message=f"Link {source} → {dest} has {recent_loss:.1f}% packet loss",
                        predicted_time_hours=None,  # Already happening
                        confidence=0.9,
                        evidence=[
                            f"Recent packet loss: {recent_loss:.1f}%",
                            f"Threshold: {self.PACKET_LOSS_CRITICAL}%",
                        ],
                        suggestions=[
                            "Check for interference or obstructions",
                            "Verify node is still powered on",
                            "Consider switching channels",
                        ],
                        affected_nodes=[source, dest],
                    ))
                elif recent_loss > self.PACKET_LOSS_WARNING:
                    alerts.append(PredictiveAlert(
                        alert_type='link_packet_loss',
                        severity='warning',
                        message=f"Link {source} → {dest} packet loss increasing ({recent_loss:.1f}%)",
                        predicted_time_hours=None,
                        confidence=0.75,
                        evidence=[
                            f"Recent packet loss: {recent_loss:.1f}%",
                            f"Warning threshold: {self.PACKET_LOSS_WARNING}%",
                        ],
                        suggestions=[
                            "Monitor link closely",
                            "Check for new sources of interference",
                        ],
                        affected_nodes=[source, dest],
                    ))

        return alerts

    def _check_metric_degradation(
        self,
        values: List[float],
        metric_name: str,
        unit: str,
        warning_drop: float,
        critical_absolute: float,
        higher_is_better: bool = True
    ) -> Optional[PredictiveAlert]:
        """Check if a metric is degrading over time."""
        if len(values) < self.MIN_SAMPLES_FOR_PREDICTION:
            return None

        # Calculate trend (compare recent vs older)
        third = len(values) // 3
        if third < 1:
            return None

        recent_avg = sum(values[:third]) / third
        old_avg = sum(values[-third:]) / third

        if higher_is_better:
            drop = old_avg - recent_avg  # Positive = degradation
        else:
            drop = recent_avg - old_avg  # Positive = degradation

        # Check for critical absolute value
        is_critical_absolute = (
            (higher_is_better and recent_avg < critical_absolute) or
            (not higher_is_better and recent_avg > critical_absolute)
        )

        if is_critical_absolute:
            return PredictiveAlert(
                alert_type='metric_critical',
                severity='critical',
                message=f"{metric_name} is at critical level: {recent_avg:.1f} {unit}",
                predicted_time_hours=None,
                confidence=0.9,
                evidence=[
                    f"Current {metric_name}: {recent_avg:.1f} {unit}",
                    f"Critical threshold: {critical_absolute} {unit}",
                ],
                suggestions=[
                    "Immediate investigation recommended",
                    "Check hardware and environmental factors",
                ],
                affected_nodes=[],
            )

        # Check for degradation trend
        if drop > warning_drop:
            # Estimate time to critical
            samples_to_critical = None
            if drop > 0:
                remaining = recent_avg - critical_absolute if higher_is_better else critical_absolute - recent_avg
                if remaining > 0:
                    rate_per_sample = drop / (len(values) - third)
                    if rate_per_sample > 0:
                        samples_to_critical = remaining / rate_per_sample

            hours_estimate = samples_to_critical * 4 if samples_to_critical else None

            return PredictiveAlert(
                alert_type='metric_degradation',
                severity='warning',
                message=f"{metric_name} degrading: dropped {drop:.1f} {unit} over analysis period",
                predicted_time_hours=hours_estimate,
                confidence=min(0.85, 0.5 + len(values) * 0.03),
                evidence=[
                    f"Recent avg: {recent_avg:.1f} {unit}",
                    f"Historical avg: {old_avg:.1f} {unit}",
                    f"Change: -{drop:.1f} {unit}",
                ],
                suggestions=[
                    "Monitor trend closely",
                    "Investigate potential causes of degradation",
                ],
                affected_nodes=[],
            )

        return None

    def _check_node_count_decline(self, counts: List[int]) -> Optional[PredictiveAlert]:
        """Check if online node count is declining."""
        if len(counts) < self.MIN_SAMPLES_FOR_PREDICTION:
            return None

        recent = counts[:min(5, len(counts))]
        older = counts[-min(5, len(counts)):]

        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)

        if older_avg > 0:
            decline_pct = ((older_avg - recent_avg) / older_avg) * 100

            if decline_pct > 20:  # More than 20% node decline
                return PredictiveAlert(
                    alert_type='node_count_decline',
                    severity='warning' if decline_pct < 40 else 'critical',
                    message=f"Network node count declining: {decline_pct:.0f}% fewer nodes online",
                    predicted_time_hours=None,
                    confidence=0.8,
                    evidence=[
                        f"Current online nodes: {recent_avg:.0f}",
                        f"Previous period: {older_avg:.0f}",
                        f"Decline: {decline_pct:.1f}%",
                    ],
                    suggestions=[
                        "Check power status of offline nodes",
                        "Verify network connectivity",
                        "Check for environmental changes (weather, obstructions)",
                    ],
                    affected_nodes=[],
                )

        return None

    def _calculate_trend_slope(self, values: List[float]) -> float:
        """
        Calculate simple linear trend slope.

        Returns:
            Slope value (positive = increasing, negative = decreasing)
        """
        if len(values) < 2:
            return 0.0

        n = len(values)
        # Simple linear regression
        x_mean = (n - 1) / 2
        y_mean = sum(values) / n

        numerator = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return 0.0

        return numerator / denominator

    def get_network_forecast(self, hours_ahead: int = 24) -> Dict[str, Any]:
        """
        Generate a network health forecast.

        Args:
            hours_ahead: How many hours to forecast

        Returns:
            Dict with forecast metrics and confidence
        """
        history = self.store.get_network_health_history(hours=72)

        if len(history) < self.MIN_SAMPLES_FOR_PREDICTION:
            return {
                'has_forecast': False,
                'reason': 'Insufficient historical data',
            }

        # Calculate trends
        snr_values = [h.avg_snr_db for h in history if h.avg_snr_db]
        rssi_values = [h.avg_rssi_dbm for h in history if h.avg_rssi_dbm]
        node_counts = [h.online_nodes for h in history if h.online_nodes]

        snr_slope = self._calculate_trend_slope(snr_values) if snr_values else 0
        rssi_slope = self._calculate_trend_slope(rssi_values) if rssi_values else 0
        node_slope = self._calculate_trend_slope([float(n) for n in node_counts]) if node_counts else 0

        # Estimate samples per hour (rough approximation)
        samples_per_hour = len(history) / 72 if history else 0.25

        # Project forward
        projected_samples = int(hours_ahead * samples_per_hour)

        current_snr = snr_values[0] if snr_values else 0
        current_rssi = rssi_values[0] if rssi_values else 0
        current_nodes = node_counts[0] if node_counts else 0

        forecast_snr = current_snr + (snr_slope * projected_samples)
        forecast_rssi = current_rssi + (rssi_slope * projected_samples)
        forecast_nodes = max(0, current_nodes + int(node_slope * projected_samples))

        # Determine health outlook
        if forecast_snr < self.SNR_CRITICAL_THRESHOLD or forecast_nodes < current_nodes * 0.5:
            outlook = 'degrading'
        elif snr_slope > 0.1 or node_slope > 0:
            outlook = 'improving'
        else:
            outlook = 'stable'

        return {
            'has_forecast': True,
            'hours_ahead': hours_ahead,
            'current': {
                'avg_snr_db': round(current_snr, 1),
                'avg_rssi_dbm': round(current_rssi, 1),
                'online_nodes': current_nodes,
            },
            'forecast': {
                'avg_snr_db': round(forecast_snr, 1),
                'avg_rssi_dbm': round(forecast_rssi, 1),
                'online_nodes': forecast_nodes,
            },
            'trends': {
                'snr_per_hour': round(snr_slope * samples_per_hour, 2),
                'rssi_per_hour': round(rssi_slope * samples_per_hour, 2),
                'nodes_per_hour': round(node_slope * samples_per_hour, 2),
            },
            'outlook': outlook,
            'confidence': min(0.9, 0.4 + len(history) * 0.01),
        }


def get_predictive_analyzer() -> PredictiveAnalyzer:
    """Get singleton predictive analyzer instance."""
    global _predictive_analyzer
    try:
        return _predictive_analyzer
    except NameError:
        _predictive_analyzer = PredictiveAnalyzer(get_analytics_store())
        return _predictive_analyzer
