"""Tests for diagnostic_history.db hardening — WAL pragmas + auto-prune.

Phase 1 closure: diagnostic_history.db had no retention policy at all
and used bare sqlite3.connect. Highest-risk-without-retention category
in the audit."""

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.diagnostic_engine import (
    Category,
    Diagnosis,
    DiagnosticEngine,
    Severity,
    Symptom,
)


@pytest.fixture
def engine(tmp_path: Path, monkeypatch) -> DiagnosticEngine:
    """DiagnosticEngine pointed at a tmp DB."""
    eng = DiagnosticEngine(persist_history=True)
    # Redirect _get_db_path to a tmp file. _init_db has already run with the
    # real path, but we close the persistent connection and reset so the
    # next _get_connection() call hits the tmp DB.
    monkeypatch.setattr(eng, "_get_db_path", lambda: tmp_path / "diag.db")
    if eng._db_conn is not None:
        eng._db_conn.close()
        eng._db_conn = None
    eng._init_db()
    eng._last_diagnostic_prune_ts = 0.0
    return eng


def _symptom(msg="boom"):
    return Symptom(
        message=msg,
        category=Category.CONNECTIVITY,
        severity=Severity.WARNING,
        source="test",
    )


def _diagnosis(symptom=None, cause="nope"):
    return Diagnosis(
        symptom=symptom or _symptom(),
        likely_cause=cause,
        confidence=0.9,
        evidence=[],
        suggestions=[],
        auto_recoverable=False,
    )


class TestDiagnosticDBPragmas:
    def test_connection_journal_mode_wal(self, engine: DiagnosticEngine):
        with engine._db_lock:
            conn = engine._get_connection()
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_connection_synchronous_normal(self, engine: DiagnosticEngine):
        with engine._db_lock:
            conn = engine._get_connection()
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        assert sync == 1

    def test_connection_journal_size_capped(self, engine: DiagnosticEngine):
        with engine._db_lock:
            conn = engine._get_connection()
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
        assert limit == 67_108_864


class TestDiagnosticAutoPrune:
    def test_prune_removes_rows_older_than_retention(self, engine: DiagnosticEngine):
        # Insert an aged row directly so we don't depend on timestamp injection.
        with engine._db_lock:
            conn = engine._get_connection()
            conn.execute(
                "INSERT INTO diagnoses (symptom_message, symptom_category, symptom_severity, "
                "symptom_source, likely_cause, confidence, evidence, suggestions, "
                "auto_recoverable, rule_name, timestamp) "
                "VALUES ('aged', 'network', 'warning', 'test', 'old', 0.5, '[]', '[]', 0, '', "
                "datetime('now', '-100 days'))"
            )
            conn.execute(
                "INSERT INTO diagnoses (symptom_message, symptom_category, symptom_severity, "
                "symptom_source, likely_cause, confidence, evidence, suggestions, "
                "auto_recoverable, rule_name) "
                "VALUES ('fresh', 'network', 'warning', 'test', 'new', 0.5, '[]', '[]', 0, '')"
            )
            conn.commit()
        engine._last_diagnostic_prune_ts = 0.0  # bypass cadence
        engine._maybe_prune_diagnoses()
        with engine._db_lock:
            conn = engine._get_connection()
            aged = conn.execute(
                "SELECT COUNT(*) FROM diagnoses WHERE symptom_message='aged'"
            ).fetchone()[0]
            fresh = conn.execute(
                "SELECT COUNT(*) FROM diagnoses WHERE symptom_message='fresh'"
            ).fetchone()[0]
        assert aged == 0, "aged diagnosis should be pruned"
        assert fresh == 1, "fresh diagnosis should survive"

    def test_prune_skipped_within_cadence(self, engine: DiagnosticEngine):
        with engine._db_lock:
            conn = engine._get_connection()
            conn.execute(
                "INSERT INTO diagnoses (symptom_message, symptom_category, symptom_severity, "
                "symptom_source, likely_cause, confidence, evidence, suggestions, "
                "auto_recoverable, rule_name, timestamp) "
                "VALUES ('aged', 'network', 'warning', 'test', 'old', 0.5, '[]', '[]', 0, '', "
                "datetime('now', '-100 days'))"
            )
            conn.commit()
        engine._last_diagnostic_prune_ts = time.time() - 60  # well inside 1h cadence
        engine._maybe_prune_diagnoses()
        with engine._db_lock:
            conn = engine._get_connection()
            count = conn.execute(
                "SELECT COUNT(*) FROM diagnoses WHERE symptom_message='aged'"
            ).fetchone()[0]
        assert count == 1, "prune fired despite cadence window"

    def test_save_diagnosis_calls_maybe_prune(self, engine: DiagnosticEngine, monkeypatch):
        called = []
        monkeypatch.setattr(engine, "_maybe_prune_diagnoses", lambda: called.append(True))
        engine._save_diagnosis(_symptom(), _diagnosis(), rule_name="t")
        assert called, "_save_diagnosis should drive _maybe_prune_diagnoses"
