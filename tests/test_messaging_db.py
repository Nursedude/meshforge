"""Tests for messages.db hardening — WAL pragmas + hourly auto-prune.

Phase 1 of the post-fleet-host-2026-04-26 DB-bloat closure: messages.db
was at HIGH bloat risk (per-message writes, manual-only retention,
rollback-journal mode). This test pins down the new contract."""

import time
from pathlib import Path
from unittest.mock import patch

import pytest

import commands.messaging as messaging_mod


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch):
    """Redirect _get_db_path to a tmp file and reset prune timer."""
    db_path = tmp_path / "messages.db"
    monkeypatch.setattr(messaging_mod, "_get_db_path", lambda: db_path)
    # Reset the module-level prune timestamp so tests are independent.
    monkeypatch.setattr(messaging_mod, "_last_message_prune_ts", 0.0)
    return db_path


class TestMessagesDBPragmas:
    """Lock in the WAL contract for messages.db."""

    def test_init_db_returns_wal_connection(self, tmp_db):
        conn = messaging_mod._init_db()
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            conn.close()

    def test_init_db_synchronous_normal(self, tmp_db):
        conn = messaging_mod._init_db()
        try:
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
            assert sync == 1, "expected synchronous=NORMAL"
        finally:
            conn.close()

    def test_init_db_journal_size_capped(self, tmp_db):
        conn = messaging_mod._init_db()
        try:
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert limit == 67_108_864
        finally:
            conn.close()


class TestAutoPrune:
    """Hourly auto-prune fires from store_incoming. Closes the gap that
    let messages.db grow unbounded with manual-only clear_messages()."""

    def test_prune_skipped_within_cadence(self, tmp_db):
        # Seed with a row dated 100 days ago so retention should reap it.
        conn = messaging_mod._init_db()
        try:
            conn.execute(
                "INSERT INTO messages (network, from_id, to_id, content, channel, is_dm, snr, rssi, delivered, timestamp) "
                "VALUES ('meshtastic', '!aged', NULL, 'old', 0, 0, NULL, NULL, 1, datetime('now', '-100 days'))"
            )
            conn.commit()
        finally:
            conn.close()
        # Pretend a prune just ran 60s ago — within the 1h cadence window.
        messaging_mod._last_message_prune_ts = time.time() - 60
        messaging_mod._maybe_prune_messages()
        # Aged row should still be present.
        conn = messaging_mod._init_db()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE from_id = '!aged'"
            ).fetchone()[0]
            assert count == 1, "prune fired despite cadence block"
        finally:
            conn.close()

    def test_prune_removes_rows_older_than_retention(self, tmp_db):
        conn = messaging_mod._init_db()
        try:
            # Aged row well outside retention.
            conn.execute(
                "INSERT INTO messages (network, from_id, to_id, content, channel, is_dm, snr, rssi, delivered, timestamp) "
                "VALUES ('meshtastic', '!aged', NULL, 'old', 0, 0, NULL, NULL, 1, datetime('now', '-100 days'))"
            )
            # Fresh row inside retention.
            conn.execute(
                "INSERT INTO messages (network, from_id, to_id, content, channel, is_dm, snr, rssi, delivered) "
                "VALUES ('meshtastic', '!fresh', NULL, 'new', 0, 0, NULL, NULL, 1)"
            )
            conn.commit()
        finally:
            conn.close()
        # Force cadence to allow prune to run.
        messaging_mod._last_message_prune_ts = 0.0
        messaging_mod._maybe_prune_messages()
        conn = messaging_mod._init_db()
        try:
            aged = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE from_id = '!aged'"
            ).fetchone()[0]
            fresh = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE from_id = '!fresh'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert aged == 0, "aged row survived auto-prune"
        assert fresh == 1, "fresh row should survive"

    def test_store_incoming_triggers_prune(self, tmp_db, monkeypatch):
        # store_incoming should call _maybe_prune_messages() after commit.
        called = []
        monkeypatch.setattr(
            messaging_mod, "_maybe_prune_messages",
            lambda *a, **kw: called.append(True),
        )
        result = messaging_mod.store_incoming(
            from_id="!abc", content="hi", network="meshtastic"
        )
        assert result.success
        assert called, "store_incoming should have called _maybe_prune_messages"

    def test_prune_disabled_when_interval_zero(self, tmp_db, monkeypatch):
        monkeypatch.setattr(messaging_mod, "_MESSAGE_PRUNE_INTERVAL_SECONDS", 0)
        conn = messaging_mod._init_db()
        try:
            conn.execute(
                "INSERT INTO messages (network, from_id, to_id, content, channel, is_dm, snr, rssi, delivered, timestamp) "
                "VALUES ('meshtastic', '!aged', NULL, 'old', 0, 0, NULL, NULL, 1, datetime('now', '-100 days'))"
            )
            conn.commit()
        finally:
            conn.close()
        messaging_mod._last_message_prune_ts = 0.0  # would normally trigger
        messaging_mod._maybe_prune_messages()
        conn = messaging_mod._init_db()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE from_id = '!aged'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 1, "prune ran despite interval=0 disable"
