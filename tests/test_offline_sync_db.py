"""Tests for offline_sync.db hardening — full pragma set via connect_tuned.

Phase 1 closure: offline_sync had WAL + synchronous=NORMAL but was
missing journal_size_limit. The audit graded it MEDIUM risk; this
brings it to parity with node_history.db / messages.db / analytics.db /
traffic_capture.db."""

from pathlib import Path

import pytest

from utils.offline_sync import OfflineSyncQueue


@pytest.fixture
def queue(tmp_path: Path) -> OfflineSyncQueue:
    return OfflineSyncQueue(db_path=tmp_path / "offline_sync.db")


class TestOfflineSyncPragmas:
    def test_journal_mode_is_wal(self, queue):
        with queue._lock:
            conn = queue._get_connection()
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_synchronous_is_normal(self, queue):
        with queue._lock:
            conn = queue._get_connection()
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert sync == 1

    def test_journal_size_limit_capped(self, queue):
        with queue._lock:
            conn = queue._get_connection()
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
        assert limit == 67_108_864, "size_limit should match the standard 64MB cap"
