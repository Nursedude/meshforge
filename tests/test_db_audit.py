"""Tests for scripts/db_audit.py — the audit logic itself.

Exercises the audit against synthetic DBs in tmp_path so we don't
depend on real fleet state. Verifies each verdict path: OK, WARN,
FAIL, NOT_CREATED."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest


# Import the audit script as a module — it lives in scripts/ which
# isn't on the normal package path.
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "db_audit.py"
)
_spec = importlib.util.spec_from_file_location("db_audit", _SCRIPT_PATH)
_db_audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_db_audit)
audit_db = _db_audit.audit_db
AuditResult = _db_audit.AuditResult


from utils.db_inventory import DBSpec
from utils.db_helpers import connect_tuned


def _make_spec(name: str, path: Path) -> DBSpec:
    return DBSpec(
        name=name,
        path_factory=lambda: path,
        creator_module="test",
        has_auto_prune=True,
        retention_days=7,
    )


class TestAuditNotCreated:
    def test_missing_db_reports_not_created(self, tmp_path):
        spec = _make_spec("missing", tmp_path / "missing.db")
        r = audit_db(spec, max_db_mb=500, max_wal_mb=100, mode_mask=0o002)
        assert r.exists is False
        assert r.verdict == "NOT_CREATED"


class TestAuditOK:
    def test_freshly_tuned_db_is_ok(self, tmp_path):
        path = tmp_path / "fresh.db"
        # Create via the helper so PRAGMAs are correct.
        conn = connect_tuned(path)
        conn.execute("CREATE TABLE foo (id INTEGER)")
        conn.commit()
        conn.close()
        spec = _make_spec("fresh", path)
        r = audit_db(spec, max_db_mb=500, max_wal_mb=100, mode_mask=0o002)
        assert r.exists is True
        assert r.verdict == "OK", f"unexpected issues: {r.issues}"
        assert r.journal_mode == "wal"
        assert r.schema_tables == 1
        # synchronous + journal_size_limit are per-connection — the
        # read-only audit reads them but doesn't enforce against the
        # spec (lint MF013 enforces writer correctness instead).


class TestAuditPragmaDrift:
    def test_rollback_journal_db_is_FAIL(self, tmp_path):
        path = tmp_path / "rollback.db"
        # Open via raw sqlite3 — defaults to rollback-journal mode.
        conn = sqlite3.connect(str(path))
        # Force away from any default WAL by setting DELETE mode explicitly.
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("CREATE TABLE bar (id INTEGER)")
        conn.commit()
        conn.close()
        spec = _make_spec("rollback", path)
        r = audit_db(spec, max_db_mb=500, max_wal_mb=100, mode_mask=0o002)
        assert r.verdict == "FAIL"
        assert any("journal_mode" in i for i in r.issues)


class TestAuditPermissionGap:
    def test_world_writable_is_FAIL(self, tmp_path):
        path = tmp_path / "perm.db"
        conn = connect_tuned(path)
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        conn.close()
        path.chmod(0o666)  # world-writable
        spec = _make_spec("perm", path)
        r = audit_db(spec, max_db_mb=500, max_wal_mb=100, mode_mask=0o002)
        assert r.verdict == "FAIL"
        assert any("mode" in i for i in r.issues)


class TestAuditSizeBloat:
    def test_oversized_db_is_WARN(self, tmp_path):
        path = tmp_path / "big.db"
        conn = connect_tuned(path)
        conn.execute("CREATE TABLE big (data BLOB)")
        # Insert a few KB to force the file above 0
        conn.execute("INSERT INTO big VALUES (?)", (b"x" * 8192,))
        conn.commit()
        conn.close()
        spec = _make_spec("big", path)
        # Cap at 0 MB so even a tiny file trips WARN
        r = audit_db(spec, max_db_mb=0, max_wal_mb=100, mode_mask=0o002)
        assert r.verdict in ("WARN", "FAIL")
        assert any("size" in i for i in r.issues)


class TestAuditEmptySchema:
    def test_empty_db_FAIL(self, tmp_path):
        path = tmp_path / "empty.db"
        # Create the file but no schema — open + close without DDL.
        conn = sqlite3.connect(str(path))
        conn.close()
        spec = _make_spec("empty", path)
        r = audit_db(spec, max_db_mb=500, max_wal_mb=100, mode_mask=0o002)
        assert r.verdict == "FAIL"
        assert any("zero tables" in i for i in r.issues)
