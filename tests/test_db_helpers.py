"""Tests for utils.db_helpers.connect_tuned — the single source of truth
for SQLite pragma settings across MeshForge.

Lock in the contract: WAL + synchronous=NORMAL + journal_size_limit=64MB
+ busy_timeout=30s. If anyone weakens these defaults, this fails."""

from pathlib import Path

import pytest

from utils.db_helpers import (
    DEFAULT_BUSY_TIMEOUT_SECONDS,
    DEFAULT_JOURNAL_SIZE_LIMIT,
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
