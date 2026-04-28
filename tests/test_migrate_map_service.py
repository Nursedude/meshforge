"""Phase A regression guards for migrate_map_service_to_user.sh.

Closes finding F1 from the map-domain audit arc: post-Phase-2 migration,
the new service inherited a multi-hundred-MB WAL from /root and stalled
3+ minutes in ext4_sync_file D-state on first open. The fix runs
PRAGMA wal_checkpoint(TRUNCATE) on the source DB before cp.

These tests cover both (a) the SQLite contract — checkpoint drains the
WAL — and (b) the script contract — the drain runs before copy and is
idempotent on a clean DB.

Run: python3 -m pytest tests/test_migrate_map_service.py -v
"""

import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATE_SCRIPT = REPO_ROOT / "scripts" / "migrate_map_service_to_user.sh"


# ─────────────────────────────────────────────────────────────────
# (a) SQLite-contract tests — pure logic
# ─────────────────────────────────────────────────────────────────


def _build_db_with_wal(db_path: Path, n_rows: int = 5000) -> sqlite3.Connection:
    """Create a WAL-mode DB and write enough rows to keep WAL non-empty.

    Returns the open connection — caller MUST close. Holding it open is
    the only reliable way to keep the WAL on disk; SQLite runs an
    automatic checkpoint when the last connection closes (drains WAL to
    zero), which would defeat the test setup.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # Disable auto-checkpoint so the WAL grows under our control —
    # otherwise SQLite's default 1000-page checkpoint kicks in mid-test.
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, payload TEXT)")
    with conn:
        conn.executemany(
            "INSERT INTO t (payload) VALUES (?)",
            [(f"row-{i}-" + "x" * 200,) for i in range(n_rows)],
        )
    return conn


def _wal_checkpoint_truncate(db_path: Path) -> tuple[int, int, int]:
    """Run the same PRAGMA the migration script runs."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        conn.close()


class TestWalCheckpointDrainsWal:
    """Logical contract — the PRAGMA the migration script runs actually drains WAL."""

    def test_non_empty_wal_drains_to_zero(self, tmp_path):
        db = tmp_path / "node_history.db"
        holder = _build_db_with_wal(db, n_rows=5000)
        try:
            wal_path = tmp_path / "node_history.db-wal"
            wal_before = wal_path.stat().st_size
            assert wal_before > 50_000, (
                f"test setup failed — WAL was {wal_before} bytes, expected >50KB. "
                "wal_autocheckpoint=0 should prevent SQLite from draining it for us."
            )

            busy, log_pages, checkpointed = _wal_checkpoint_truncate(db)
            assert busy == 0, "checkpoint should not be busy with only an idle reader"
            # NOTE: when the writer connection is still open in the same
            # process, PRAGMA may report checkpointed=0 even though the WAL
            # is drained. The on-disk WAL size is the authoritative signal.
            # End-to-end test (subprocess) is the closer match to real-world
            # invocation where the service has been stopped before migration.

            wal_after = wal_path.stat().st_size
            # TRUNCATE mode shrinks WAL to 0 (vs RESTART which leaves it sized).
            assert wal_after == 0, (
                f"wal_checkpoint(TRUNCATE) left {wal_after} bytes in WAL — "
                "should truncate to 0. Wrong PRAGMA mode?"
            )
        finally:
            holder.close()

    def test_clean_db_checkpoint_is_noop(self, tmp_path):
        """A DB that was just closed cleanly should checkpoint fast with no work."""
        db = tmp_path / "clean.db"
        conn = sqlite3.connect(db)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
        finally:
            conn.close()
        # Open + close again to trigger any auto-checkpoint
        sqlite3.connect(db).close()

        busy, log_pages, checkpointed = _wal_checkpoint_truncate(db)
        assert busy == 0
        # On a clean DB, log_pages and checkpointed should both be small (or 0)
        # — point is the call doesn't error and doesn't take long.
        assert log_pages >= 0
        assert checkpointed >= 0


# ─────────────────────────────────────────────────────────────────
# (b) Script-contract tests — drain happens before copy
# ─────────────────────────────────────────────────────────────────


class TestMigrateScriptDrainsBeforeCopy:
    """Lock in the script structure so the WAL drain doesn't accidentally
    move below the `cp` step in a future refactor (where it would be useless)."""

    def test_script_runs_wal_checkpoint_truncate(self):
        text = MIGRATE_SCRIPT.read_text()
        assert "wal_checkpoint(TRUNCATE)" in text, (
            "migration script must call PRAGMA wal_checkpoint(TRUNCATE) — "
            "see project_map_arc_findings.md F1 (closed Phase A)."
        )

    def test_drain_runs_before_db_copy(self):
        """The drain must run on the SOURCE DB before cp, not after."""
        text = MIGRATE_SCRIPT.read_text()
        drain_idx = text.find("wal_checkpoint(TRUNCATE)")
        assert drain_idx > 0, "drain block missing"
        # Find the first cp of node_history.db that follows
        cp_match = re.search(
            r"cp\s+-p\s+['\"]?\$ROOT_DB['\"]?\s+['\"]?\$USER_DB",
            text[drain_idx:],
        )
        assert cp_match is not None, (
            "no `cp -p $ROOT_DB $USER_DB` found AFTER the drain — drain has "
            "moved below the copy or the cp pattern changed. If the latter, "
            "update this test; if the former, fix the script."
        )

    def test_drain_failure_is_non_fatal(self):
        """If checkpoint fails for any reason, the script must still proceed
        with the copy — a worse-than-ideal migration is better than no migration."""
        text = MIGRATE_SCRIPT.read_text()
        # Look for "non-fatal" wording near the WAL_DRAIN heredoc
        wal_block = text[text.find("WAL_DRAIN"):text.find("WAL_DRAIN", text.find("WAL_DRAIN") + 1) + 100]
        assert "non-fatal" in text or "warn" in wal_block.lower(), (
            "WAL drain must be non-fatal (|| warn ...) — checkpoint failure "
            "must not abort the migration."
        )


# ─────────────────────────────────────────────────────────────────
# (c) End-to-end smoke — synthetic DB → invoke script's drain logic
# ─────────────────────────────────────────────────────────────────


class TestEndToEndDrain:
    """Run the same Python heredoc the migration script uses against a
    synthetic DB. Catches regressions in the heredoc itself (typos, wrong
    pragma, silent-fail patterns)."""

    def test_heredoc_drains_synthetic_db(self, tmp_path):
        db = tmp_path / "node_history.db"
        holder = _build_db_with_wal(db, n_rows=3000)
        try:
            wal_path = tmp_path / "node_history.db-wal"
            wal_before = wal_path.stat().st_size
            assert wal_before > 0, "test setup: WAL should be non-empty"

            # Extract and run the same Python the script runs.
            script_text = MIGRATE_SCRIPT.read_text()
            start = script_text.find("python3 - \"$ROOT_DB\" <<'WAL_DRAIN'")
            assert start > 0, "WAL_DRAIN heredoc moved or renamed"
            body_start = script_text.find("\n", start) + 1
            body_end = script_text.find("WAL_DRAIN", body_start)
            body = script_text[body_start:body_end]

            result = subprocess.run(
                ["python3", "-", str(db)],
                input=body,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, (
                f"heredoc exited {result.returncode}: stderr={result.stderr!r}"
            )
            assert "wal_checkpoint(TRUNCATE)" in result.stdout

            wal_after = wal_path.stat().st_size
            assert wal_after == 0, (
                f"end-to-end: WAL still {wal_after} bytes after script drain"
            )
        finally:
            holder.close()
