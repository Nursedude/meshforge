"""Tests for utils.db_helpers.connect_tuned — the single source of truth
for SQLite pragma settings across MeshForge.

Lock in the contract: WAL + synchronous=NORMAL + journal_size_limit=64MB
+ busy_timeout=30s. If anyone weakens these defaults, this fails."""

from pathlib import Path

import pytest

from utils.db_helpers import (
    DEFAULT_BUSY_TIMEOUT_SECONDS,
    DEFAULT_JOURNAL_SIZE_LIMIT,
    _is_readonly_uri,
    connect_tuned,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_tuned.db"


class TestConnectTuned:
    def test_journal_mode_is_wal(self, db_path: Path):
        conn = connect_tuned(db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            conn.close()

    def test_synchronous_is_normal(self, db_path: Path):
        conn = connect_tuned(db_path)
        try:
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
            # 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
            assert sync == 1
        finally:
            conn.close()

    def test_journal_size_limit_default_64mb(self, db_path: Path):
        conn = connect_tuned(db_path)
        try:
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert limit == DEFAULT_JOURNAL_SIZE_LIMIT == 67_108_864
        finally:
            conn.close()

    def test_busy_timeout_default_30s(self, db_path: Path):
        conn = connect_tuned(db_path)
        try:
            # PRAGMA busy_timeout returns milliseconds
            ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert ms == int(DEFAULT_BUSY_TIMEOUT_SECONDS * 1000) == 30_000
        finally:
            conn.close()

    def test_accepts_str_path(self, db_path: Path):
        # Many callsites pass str(self.db_path) — make sure that works.
        conn = connect_tuned(str(db_path))
        try:
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.commit()
        finally:
            conn.close()

    def test_accepts_path_object(self, db_path: Path):
        conn = connect_tuned(db_path)
        try:
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.commit()
        finally:
            conn.close()

    def test_custom_busy_timeout(self, db_path: Path):
        conn = connect_tuned(db_path, busy_timeout_seconds=5.0)
        try:
            ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert ms == 5_000
        finally:
            conn.close()

    def test_custom_journal_size_limit(self, db_path: Path):
        conn = connect_tuned(db_path, journal_size_limit=1_048_576)
        try:
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert limit == 1_048_576
        finally:
            conn.close()

    def test_check_same_thread_false_passes_through(self, db_path: Path):
        conn = connect_tuned(db_path, check_same_thread=False)
        try:
            # If check_same_thread were ignored, sqlite3 would raise the
            # ProgrammingError on cross-thread use; this just smoke-tests
            # the kwarg reaches the underlying connect.
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.commit()
        finally:
            conn.close()

    def test_wal_persists_across_reopens(self, db_path: Path):
        # The WAL switch lives in the DB header — a second open should
        # see WAL even before connect_tuned re-runs the PRAGMA.
        c1 = connect_tuned(db_path)
        c1.close()
        # Open with bare sqlite3 to prove WAL is on the DB, not just per-conn.
        import sqlite3
        c2 = sqlite3.connect(str(db_path))
        try:
            mode = c2.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            c2.close()


class TestReadOnlyOpensWork:
    """connect_tuned's docstring has advertised `file:...?mode=ro` readers
    since it was written, but it unconditionally ran `PRAGMA journal_mode=WAL`
    and `journal_size_limit` — both WRITES — so every read-only open died with
    "attempt to write a readonly database" (found 2026-09-05).

    That mattered because MF013 requires every SQLite consumer to come through
    this helper: a read-only reader had to choose between the lint rule and
    read-only, and the rule usually won. The tool was forcing the weaker
    guarantee.
    """

    def _db(self, tmp_path):
        p = tmp_path / "ro.db"
        conn = connect_tuned(p)
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()
        return p

    def test_readonly_uri_opens_and_reads(self, tmp_path):
        p = self._db(tmp_path)
        conn = connect_tuned(f"file:{p}?mode=ro", uri=True)
        try:
            assert conn.execute("SELECT a FROM t").fetchone()[0] == 1
        finally:
            conn.close()

    def test_readonly_connection_still_refuses_writes(self, tmp_path):
        """Skipping the pragmas must not have quietly made it writable."""
        import sqlite3
        p = self._db(tmp_path)
        conn = connect_tuned(f"file:{p}?mode=ro", uri=True)
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO t VALUES (2)")
                conn.commit()
        finally:
            conn.close()

    def test_normal_open_still_gets_wal(self, tmp_path):
        """The read-only branch must not weaken read-write callers — WAL is
        the reason this helper exists (the 1.95 GB wedge)."""
        conn = connect_tuned(tmp_path / "rw.db")
        try:
            assert conn.execute(
                "PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        finally:
            conn.close()

    def test_rw_uri_open_still_gets_wal(self, tmp_path):
        conn = connect_tuned(f"file:{tmp_path / 'rw2.db'}?mode=rwc", uri=True)
        try:
            assert conn.execute(
                "PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        finally:
            conn.close()


class TestReadOnlyUriDetection:
    def test_detects_mode_ro(self):
        assert _is_readonly_uri(True, "file:/x/y.db?mode=ro")

    def test_rw_and_rwc_are_not_readonly(self):
        assert not _is_readonly_uri(True, "file:/x/y.db?mode=rw")
        assert not _is_readonly_uri(True, "file:/x/y.db?mode=rwc")

    def test_no_mode_param_is_not_readonly(self):
        assert not _is_readonly_uri(True, "file:/x/y.db")

    def test_uri_false_is_never_readonly(self):
        assert not _is_readonly_uri(False, "/x/y.db?mode=ro")

    def test_a_path_containing_mode_ro_is_not_mistaken_for_one(self):
        """Parsed, not substring-matched: a directory literally named
        'mode=ro' would otherwise silently disable WAL for a writer."""
        assert not _is_readonly_uri(True, "file:/data/mode=ro/y.db")
