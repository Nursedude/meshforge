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


class TestOutgoingPacketId:
    """INBOX-2 step 1 (2026-11-07): a send keeps the radio's packet id.

    Measured: 53 of 61 inbox channel posts were decoded by another fleet box,
    and every one read "unconfirmed" — the join key (packet id) was thrown
    away at ``inbox_api.send``. A row the column did not exist for reads NULL
    (unknown), never 0 (hfm #1)."""

    LEGACY_SCHEMA = '''
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            network TEXT NOT NULL,
            from_id TEXT NOT NULL,
            to_id TEXT,
            content TEXT NOT NULL,
            channel INTEGER DEFAULT 0,
            is_dm BOOLEAN DEFAULT 1,
            snr REAL,
            rssi INTEGER,
            delivered BOOLEAN DEFAULT 0
        )'''  # the schema as shipped before 2026-11-07

    @staticmethod
    def _columns(db_path):
        import sqlite3
        c = sqlite3.connect(str(db_path))
        try:
            return [r[1] for r in c.execute("PRAGMA table_info(messages)")]
        finally:
            c.close()

    @staticmethod
    def _rows(db_path):
        import sqlite3
        c = sqlite3.connect(str(db_path))
        try:
            return c.execute("SELECT content, packet_id FROM messages "
                             "ORDER BY id").fetchall()
        finally:
            c.close()

    def test_store_outgoing_keeps_packet_id(self, tmp_db):
        r = messaging_mod.store_outgoing(content="Wx", network="meshtastic",
                                         to_id=None, channel=3, delivered=True,
                                         packet_id=0x79C4A807)
        assert r.success, r.message
        assert self._rows(tmp_db) == [("Wx", 0x79C4A807)]

    def test_packet_id_defaults_to_unknown(self, tmp_db):
        messaging_mod.store_outgoing(content="x", network="meshtastic",
                                     to_id=None)
        assert self._rows(tmp_db) == [("x", None)]

    @pytest.mark.parametrize("bad", [0, -1, 2 ** 32, True, "4242", 1.5])
    def test_impossible_packet_id_is_refused_loudly_row_kept(self, tmp_db,
                                                             bad, caplog):
        r = messaging_mod.store_outgoing(content="x", network="meshtastic",
                                         to_id=None, packet_id=bad)
        assert r.success, "the message record must not be lost"
        assert self._rows(tmp_db) == [("x", None)]
        assert any("packet_id" in m for m in caplog.messages), caplog.messages

    def test_legacy_db_gains_column_old_rows_unknown(self, tmp_db):
        import sqlite3
        c = sqlite3.connect(str(tmp_db))
        c.execute(self.LEGACY_SCHEMA)
        c.execute("INSERT INTO messages (network, from_id, content) "
                  "VALUES ('meshtastic', 'local', 'old')")
        c.commit()
        c.close()
        messaging_mod._init_db().close()
        assert "packet_id" in self._columns(tmp_db)
        assert self._rows(tmp_db) == [("old", None)]
        messaging_mod._init_db().close()  # idempotent
        assert self._columns(tmp_db).count("packet_id") == 1

    def test_read_back_carries_packet_id(self, tmp_db):
        # A column nobody reads is not a join key (code-review 2026-11-07).
        messaging_mod.store_outgoing(content="Wx", network="meshtastic",
                                     to_id=None, channel=3, delivered=True,
                                     packet_id=0x79C4A807)
        r = messaging_mod.get_messages(limit=5)
        assert r.success, r.message
        msgs = r.data["messages"]
        assert [m["packet_id"] for m in msgs] == [0x79C4A807]

    def test_failed_migration_closes_the_connection_and_raises(self, tmp_db):
        import sqlite3
        opened = []
        real = messaging_mod.connect_tuned

        def tracking(*a, **k):
            c = real(*a, **k)
            opened.append(c)
            return c

        def boom(*a, **k):
            raise sqlite3.OperationalError("database is locked")

        with patch.object(messaging_mod, "connect_tuned", tracking), \
                patch.object(messaging_mod, "ensure_column", boom):
            with pytest.raises(sqlite3.OperationalError):
                messaging_mod._init_db()
        assert opened, "precondition: a connection was opened"
        with pytest.raises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")  # closed — no fd leak (#73)


class _StaleSchemaConn:
    """A connection whose FIRST schema read happens just before another
    process (``winner``) adds the column — PRAGMA-then-ALTER is not atomic."""

    def __init__(self, real, winner_sql_path):
        self.real, self.path, self.n = real, winner_sql_path, 0

    def execute(self, sql, *a):
        if sql.startswith("PRAGMA table_info"):
            self.n += 1
            if self.n == 1:
                from utils.db_helpers import connect_tuned
                w = connect_tuned(self.path)
                w.execute("ALTER TABLE t ADD COLUMN c INTEGER")
                w.commit()
                w.close()
                return [(0, "id", "INTEGER", 0, None, 1)]
        return self.real.execute(sql, *a)


class TestEnsureColumn:
    """``utils.db_helpers.ensure_column`` — the one additive-column helper."""

    @staticmethod
    def _db(tmp_path):
        from utils.db_helpers import connect_tuned
        path = tmp_path / "x.db"
        c = connect_tuned(path)
        c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        c.commit()
        return path, c

    @staticmethod
    def _cols(conn):
        return [r[1] for r in conn.execute("PRAGMA table_info(t)")]

    def test_adds_once_then_noop(self, tmp_path):
        from utils.db_helpers import ensure_column
        _, c = self._db(tmp_path)
        assert ensure_column(c, "t", "c", "INTEGER") is True
        assert ensure_column(c, "t", "c", "INTEGER") is False
        assert self._cols(c).count("c") == 1
        c.close()

    def test_losing_the_race_is_tolerated(self, tmp_path):
        from utils.db_helpers import ensure_column
        path, c = self._db(tmp_path)
        stale = _StaleSchemaConn(c, path)
        assert ensure_column(stale, "t", "c", "INTEGER") is False
        assert self._cols(c).count("c") == 1
        c.close()

    def test_any_other_failure_propagates(self, tmp_path):
        # A swallow-every-OperationalError change (hfm #1) must fail here: the
        # column is NOT there afterwards, so the error is real.
        import sqlite3
        from utils.db_helpers import ensure_column
        path, c = self._db(tmp_path)
        c.close()
        ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        with pytest.raises(sqlite3.OperationalError):
            ensure_column(ro, "t", "c", "INTEGER")
        ro.close()
