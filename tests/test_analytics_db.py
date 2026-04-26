"""Tests for analytics.db hardening — WAL pragmas via connect_tuned.

Phase 1 closure: analytics.db was opening bare sqlite3.connect at 8
callsites with no PRAGMAs. Same DB-bloat class as the fleet-host
2026-04-26 wedge."""

from pathlib import Path

import pytest

from utils.analytics import AnalyticsStore


@pytest.fixture
def store(tmp_path: Path) -> AnalyticsStore:
    return AnalyticsStore(db_path=tmp_path / "analytics.db")


class TestAnalyticsDBPragmas:
    def test_connect_returns_wal(self, store: AnalyticsStore):
        conn = store._connect()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            conn.close()

    def test_connect_synchronous_normal(self, store: AnalyticsStore):
        conn = store._connect()
        try:
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
            assert sync == 1
        finally:
            conn.close()

    def test_connect_journal_size_capped(self, store: AnalyticsStore):
        conn = store._connect()
        try:
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert limit == 67_108_864
        finally:
            conn.close()
