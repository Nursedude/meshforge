"""
Tests for AnalyticsCollector and time-series aggregation (C2).

Covers:
- AnalyticsCollector start/stop lifecycle
- Network health collection from SharedHealthState
- Link budget collection from node getter
- Time-series aggregation (hourly/daily summaries)
- Graceful behavior when data sources unavailable
"""

import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.analytics import (
    AnalyticsCollector,
    AnalyticsStore,
    LinkBudgetSample,
    NetworkHealthMetrics,
)


@pytest.fixture
def tmp_store(tmp_path):
    """Create an AnalyticsStore backed by a temp SQLite DB."""
    db = tmp_path / "test_analytics.db"
    return AnalyticsStore(db_path=db)


@pytest.fixture
def mock_health_state():
    """Mock SharedHealthState returning sample metrics."""
    state = MagicMock()
    state.get_metrics.return_value = {
        "total_services": 5,
        "healthy_count": 3,
        "avg_uptime_pct": 85.0,
    }
    return state


@pytest.fixture
def sample_nodes():
    """Sample node dicts for node_getter."""
    return [
        {"id": "!abc123", "snr": 8.5, "rssi": -75, "is_online": True},
        {"id": "!def456", "snr": 2.0, "rssi": -95, "is_online": True},
        {"id": "!ghi789", "snr": -3.0, "rssi": -110, "is_online": False},
    ]


@pytest.fixture
def node_getter(sample_nodes):
    """Callable that returns sample nodes."""
    return MagicMock(return_value=sample_nodes)


# ─────────────────────────────────────────────────────────────────
# AnalyticsCollector — Lifecycle
# ─────────────────────────────────────────────────────────────────

class TestCollectorLifecycle:

    def test_start_creates_daemon_thread(self, tmp_store):
        collector = AnalyticsCollector(store=tmp_store, interval_sec=60)
        collector.start()
        assert collector.is_running
        assert collector._thread.daemon is True
        assert collector._thread.name == "analytics-collector"
        collector.stop()
        assert not collector.is_running

    def test_stop_joins_thread(self, tmp_store):
        collector = AnalyticsCollector(store=tmp_store, interval_sec=60)
        collector.start()
        collector.stop()
        assert collector._thread is None
        assert collector._stop_event.is_set()

    def test_start_idempotent(self, tmp_store):
        collector = AnalyticsCollector(store=tmp_store, interval_sec=60)
        collector.start()
        thread1 = collector._thread
        collector.start()  # Second call should be no-op
        assert collector._thread is thread1
        collector.stop()

    def test_stop_event_interrupts_wait(self, tmp_store):
        """Collector should stop quickly even with long interval."""
        collector = AnalyticsCollector(store=tmp_store, interval_sec=3600)
        collector.start()
        t0 = time.monotonic()
        collector.stop()
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, f"Stop took {elapsed:.1f}s, expected < 5s"

    def test_collection_count_increments(self, tmp_store, mock_health_state, node_getter):
        collector = AnalyticsCollector(
            store=tmp_store,
            health_state=mock_health_state,
            node_getter=node_getter,
            interval_sec=0.1,
        )
        collector.start()
        time.sleep(0.5)
        collector.stop()
        assert collector._collection_count >= 2


# ─────────────────────────────────────────────────────────────────
# AnalyticsCollector — Network Health Collection
# ─────────────────────────────────────────────────────────────────

class TestCollectNetworkHealth:

    def test_records_from_health_state(self, tmp_store, mock_health_state):
        collector = AnalyticsCollector(
            store=tmp_store,
            health_state=mock_health_state,
        )
        collector.collect_once()
        history = tmp_store.get_network_health_history(hours=1)
        assert len(history) == 1
        assert history[0].online_nodes == 3
        assert history[0].offline_nodes == 2

    def test_enriches_with_node_data(self, tmp_store, mock_health_state, node_getter):
        collector = AnalyticsCollector(
            store=tmp_store,
            health_state=mock_health_state,
            node_getter=node_getter,
        )
        collector.collect_once()
        history = tmp_store.get_network_health_history(hours=1)
        assert len(history) == 1
        # Node data should override health_state counts (2 online, 1 offline)
        assert history[0].online_nodes == 2
        assert history[0].offline_nodes == 1
        # Check averaged signal values
        expected_snr = (8.5 + 2.0 + -3.0) / 3
        assert abs(history[0].avg_snr_db - expected_snr) < 0.01
        expected_rssi = (-75 + -95 + -110) / 3
        assert abs(history[0].avg_rssi_dbm - expected_rssi) < 0.01

    def test_skips_when_no_health_state(self, tmp_store):
        collector = AnalyticsCollector(store=tmp_store, health_state=None)
        collector.collect_once()
        history = tmp_store.get_network_health_history(hours=1)
        assert len(history) == 0

    def test_handles_health_state_error(self, tmp_store):
        bad_state = MagicMock()
        bad_state.get_metrics.side_effect = RuntimeError("service down")
        collector = AnalyticsCollector(store=tmp_store, health_state=bad_state)
        # Should not raise
        collector.collect_once()
        history = tmp_store.get_network_health_history(hours=1)
        assert len(history) == 0

    def test_handles_node_getter_error(self, tmp_store, mock_health_state):
        bad_getter = MagicMock(side_effect=RuntimeError("nodes unavailable"))
        collector = AnalyticsCollector(
            store=tmp_store,
            health_state=mock_health_state,
            node_getter=bad_getter,
        )
        collector.collect_once()
        # Health record should still be written (without enrichment)
        history = tmp_store.get_network_health_history(hours=1)
        assert len(history) == 1


# ─────────────────────────────────────────────────────────────────
# AnalyticsCollector — Link Budget Collection
# ─────────────────────────────────────────────────────────────────

class TestCollectLinkBudgets:

    def test_records_link_budgets_per_node(self, tmp_store, node_getter):
        collector = AnalyticsCollector(
            store=tmp_store,
            node_getter=node_getter,
        )
        collector.collect_once()
        history = tmp_store.get_link_budget_history(hours=1)
        assert len(history) == 3  # All 3 nodes have SNR or RSSI

    def test_classifies_link_quality(self, tmp_store, node_getter):
        collector = AnalyticsCollector(
            store=tmp_store,
            node_getter=node_getter,
        )
        collector.collect_once()
        history = tmp_store.get_link_budget_history(hours=1)
        qualities = {s.dest_node: s.link_quality for s in history}
        assert qualities.get("!abc123") == "excellent"  # SNR 8.5
        assert qualities.get("!def456") == "good"       # SNR 2.0
        assert qualities.get("!ghi789") == "fair"       # SNR -3.0

    def test_skips_nodes_without_signal(self, tmp_store):
        getter = MagicMock(return_value=[
            {"id": "!no_signal", "snr": None, "rssi": None},
            {"id": "!has_snr", "snr": 5.0, "rssi": None},
        ])
        collector = AnalyticsCollector(store=tmp_store, node_getter=getter)
        collector.collect_once()
        history = tmp_store.get_link_budget_history(hours=1)
        assert len(history) == 1
        assert history[0].dest_node == "!has_snr"

    def test_skips_nodes_without_id(self, tmp_store):
        getter = MagicMock(return_value=[
            {"id": "", "snr": 5.0, "rssi": -80},
        ])
        collector = AnalyticsCollector(store=tmp_store, node_getter=getter)
        collector.collect_once()
        history = tmp_store.get_link_budget_history(hours=1)
        assert len(history) == 0

    def test_skips_when_no_node_getter(self, tmp_store):
        collector = AnalyticsCollector(store=tmp_store, node_getter=None)
        collector.collect_once()
        history = tmp_store.get_link_budget_history(hours=1)
        assert len(history) == 0

    def test_handles_object_nodes(self, tmp_store):
        """Supports node objects (not just dicts) via getattr."""
        node = MagicMock()
        node.id = "!obj_node"
        node.snr = 6.0
        node.rssi = -82
        node.is_online = True
        getter = MagicMock(return_value=[node])
        collector = AnalyticsCollector(store=tmp_store, node_getter=getter)
        collector.collect_once()
        history = tmp_store.get_link_budget_history(hours=1)
        assert len(history) == 1
        assert history[0].dest_node == "!obj_node"
        assert history[0].snr_db == 6.0

    def test_offline_nodes_get_100pct_loss(self, tmp_store, node_getter):
        collector = AnalyticsCollector(store=tmp_store, node_getter=node_getter)
        collector.collect_once()
        history = tmp_store.get_link_budget_history(hours=1)
        losses = {s.dest_node: s.packet_loss_pct for s in history}
        assert losses.get("!abc123") == 0.0   # online
        assert losses.get("!ghi789") == 100.0  # offline


# ─────────────────────────────────────────────────────────────────
# Time-Series Aggregation — Hourly Summary
# ─────────────────────────────────────────────────────────────────

class TestHourlySummary:

    def _insert_health_records(self, store, records):
        """Helper to insert multiple health records with specific timestamps."""
        for ts, online, offline, rssi, snr, pkt_rate in records:
            metrics = NetworkHealthMetrics(
                timestamp=ts,
                online_nodes=online,
                offline_nodes=offline,
                avg_rssi_dbm=rssi,
                avg_snr_db=snr,
                avg_link_quality_pct=pkt_rate * 100,
                packet_success_rate=pkt_rate,
                uptime_hours=0,
            )
            store.record_network_health(metrics)

    def test_groups_by_hour(self, tmp_store):
        now = datetime.now()
        hour0 = now.replace(minute=10, second=0, microsecond=0).isoformat()
        hour0b = now.replace(minute=30, second=0, microsecond=0).isoformat()
        hour1 = (now - timedelta(hours=1)).replace(minute=15, second=0, microsecond=0).isoformat()

        records = [
            (hour0,  5, 1, -80.0, 6.0, 0.95),
            (hour0b, 4, 2, -85.0, 5.0, 0.90),
            (hour1,  3, 3, -90.0, 3.0, 0.80),
        ]
        self._insert_health_records(tmp_store, records)

        summary = tmp_store.get_hourly_summary(hours=24)
        assert len(summary) == 2  # Two distinct hours

        # Most recent hour first (DESC order)
        latest = summary[0]
        assert latest["sample_count"] == 2
        assert abs(latest["avg_online"] - 4.5) < 0.2
        assert abs(latest["avg_snr"] - 5.5) < 0.2
        assert abs(latest["avg_rssi"] - (-82.5)) < 0.2

    def test_returns_empty_for_no_data(self, tmp_store):
        summary = tmp_store.get_hourly_summary(hours=24)
        assert summary == []

    def test_respects_hours_window(self, tmp_store):
        now = datetime.now()
        recent = now.replace(minute=0, second=0, microsecond=0).isoformat()
        old = (now - timedelta(hours=48)).isoformat()

        records = [
            (recent, 5, 1, -80.0, 6.0, 0.95),
            (old,    3, 3, -90.0, 3.0, 0.80),
        ]
        self._insert_health_records(tmp_store, records)

        summary = tmp_store.get_hourly_summary(hours=24)
        assert len(summary) == 1  # Only the recent record


# ─────────────────────────────────────────────────────────────────
# Time-Series Aggregation — Daily Summary
# ─────────────────────────────────────────────────────────────────

class TestDailySummary:

    def _insert_health_records(self, store, records):
        for ts, online, offline, rssi, snr, pkt_rate in records:
            metrics = NetworkHealthMetrics(
                timestamp=ts,
                online_nodes=online,
                offline_nodes=offline,
                avg_rssi_dbm=rssi,
                avg_snr_db=snr,
                avg_link_quality_pct=pkt_rate * 100,
                packet_success_rate=pkt_rate,
                uptime_hours=0,
            )
            store.record_network_health(metrics)

    def test_groups_by_day(self, tmp_store):
        now = datetime.now()
        today = now.replace(hour=10, minute=0, second=0, microsecond=0).isoformat()
        today2 = now.replace(hour=14, minute=0, second=0, microsecond=0).isoformat()
        yesterday = (now - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0).isoformat()

        records = [
            (today,     5, 1, -80.0, 6.0, 0.95),
            (today2,    4, 2, -85.0, 5.0, 0.90),
            (yesterday, 3, 3, -90.0, 3.0, 0.80),
        ]
        self._insert_health_records(tmp_store, records)

        summary = tmp_store.get_daily_summary(days=7)
        assert len(summary) == 2  # Two distinct days

        # Most recent day first
        latest = summary[0]
        assert latest["sample_count"] == 2
        assert abs(latest["avg_online"] - 4.5) < 0.2
        assert abs(latest["avg_snr"] - 5.5) < 0.2

    def test_returns_empty_for_no_data(self, tmp_store):
        summary = tmp_store.get_daily_summary(days=7)
        assert summary == []

    def test_respects_days_window(self, tmp_store):
        now = datetime.now()
        recent = now.replace(hour=12, minute=0, second=0, microsecond=0).isoformat()
        old = (now - timedelta(days=14)).isoformat()

        records = [
            (recent, 5, 1, -80.0, 6.0, 0.95),
            (old,    3, 3, -90.0, 3.0, 0.80),
        ]
        self._insert_health_records(tmp_store, records)

        summary = tmp_store.get_daily_summary(days=7)
        assert len(summary) == 1  # Only recent

    def test_packet_success_format(self, tmp_store):
        now = datetime.now().isoformat()
        records = [
            (now, 5, 1, -80.0, 6.0, 0.927),
        ]
        self._insert_health_records(tmp_store, records)

        summary = tmp_store.get_daily_summary(days=7)
        assert len(summary) == 1
        assert summary[0]["avg_packet_success"] == 0.927


# ─────────────────────────────────────────────────────────────────
# Backward Compatibility
# ─────────────────────────────────────────────────────────────────

class TestBackwardCompat:

    def test_collector_works_with_no_sources(self, tmp_store):
        """Collector should run gracefully with no data sources."""
        collector = AnalyticsCollector(
            store=tmp_store,
            health_state=None,
            node_getter=None,
        )
        collector.collect_once()
        assert tmp_store.get_network_health_history(hours=1) == []
        assert tmp_store.get_link_budget_history(hours=1) == []

    def test_default_store_is_singleton(self):
        """Default store parameter uses the get_analytics_store() singleton."""
        with patch("utils.analytics.get_analytics_store") as mock_getter:
            mock_store = MagicMock()
            mock_getter.return_value = mock_store
            collector = AnalyticsCollector()
            assert collector.store is mock_store

    def test_custom_interval(self, tmp_store):
        collector = AnalyticsCollector(store=tmp_store, interval_sec=42)
        assert collector._interval == 42

    def test_default_interval_is_300(self, tmp_store):
        collector = AnalyticsCollector(store=tmp_store)
        assert collector._interval == 300
