"""Tests for health_state.db hardening — Phase 1 follow-up.

This DB was missed in the initial Phase 1 audit (commit 2743ded) — it
had WAL+sync set but was missing journal_size_limit, and purge_old_data
was defined but never called on a schedule. Caught by the meshforge-maps
audit (read-only reader at shared_health_state.py:67-70 depends on this
writer being healthy)."""

import time
from pathlib import Path

import pytest

from utils.shared_health_state import SharedHealthState


@pytest.fixture
def state(tmp_path: Path) -> SharedHealthState:
    s = SharedHealthState(db_path=tmp_path / "health_state.db")
    yield s
    s.close()


class TestHealthStateDBPragmas:
    def test_journal_mode_wal(self, state: SharedHealthState):
        with state._get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_synchronous_normal(self, state: SharedHealthState):
        with state._get_connection() as conn:
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert sync == 1

    def test_journal_size_limit_capped(self, state: SharedHealthState):
        with state._get_connection() as conn:
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
        assert limit == 67_108_864, "the missing pragma — added in Phase 1 follow-up"


class TestHealthStateAutoPurge:
    def _seed_old_event(self, state: SharedHealthState, days_old: int):
        """Insert an event row directly with a stale timestamp."""
        cutoff = time.time() - (days_old * 86400)
        with state._get_connection() as conn:
            conn.execute(
                "INSERT INTO health_events (service, old_state, new_state, reason, timestamp, process_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("test_svc", "healthy", "unhealthy", "test", cutoff, "pid-test"),
            )

    def test_purge_removes_events_older_than_retention(self, state: SharedHealthState):
        self._seed_old_event(state, days_old=30)  # well outside 7d
        self._seed_old_event(state, days_old=1)   # inside retention
        state._last_purge_ts = 0.0  # bypass cadence
        state._maybe_purge()
        with state._get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM health_events"
            ).fetchone()[0]
        assert count == 1, "30d-old event should be purged, 1d-old should survive"

    def test_purge_skipped_within_cadence(self, state: SharedHealthState):
        self._seed_old_event(state, days_old=30)
        state._last_purge_ts = time.time() - 60  # well inside 1h cadence
        state._maybe_purge()
        with state._get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM health_events"
            ).fetchone()[0]
        assert count == 1, "purge fired despite cadence window"

    def test_update_service_triggers_purge(self, state: SharedHealthState, monkeypatch):
        called = []
        monkeypatch.setattr(
            state, "_maybe_purge", lambda *a, **kw: called.append(True)
        )
        state.update_service("test_svc", "healthy", reason="ok")
        assert called, "update_service should drive _maybe_purge"

    def test_purge_disabled_when_interval_zero(self, state: SharedHealthState, monkeypatch):
        monkeypatch.setattr(state, "PURGE_INTERVAL_SECONDS", 0)
        self._seed_old_event(state, days_old=30)
        state._last_purge_ts = 0.0
        state._maybe_purge()
        with state._get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM health_events"
            ).fetchone()[0]
        assert count == 1, "interval=0 should disable auto-purge"
