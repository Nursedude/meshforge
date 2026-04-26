"""SQLite connection helpers — single source of truth for tuned pragmas.

Why this exists: a 1.95 GB rollback-journal-mode node_history.db wedged the
:5000 map service on fleet-host (2026-04-26) for 16+ minutes during a prune.
The fix was WAL + synchronous=NORMAL + journal_size_limit; this module
guarantees every SQLite consumer in MeshForge gets the same treatment.

Usage:
    from utils.db_helpers import connect_tuned

    conn = connect_tuned(self.db_path)
    try:
        conn.execute("INSERT INTO ...")
        conn.commit()
    finally:
        conn.close()

Phase 2 will add a lint rule (MF011) flagging bare sqlite3.connect() outside
this module + tests, mirroring MF007/MF008/MF009.
"""

import sqlite3
from pathlib import Path
from typing import Union

# 64 MB cap on WAL/journal growth. Matches /opt/meshforge-maps'
# maps_node_history.db (commit 222265e, 2026-04-20) and node_history.db
# (commit fe11e83, 2026-04-26). Lower than that risks frequent
# checkpoints; higher risks the multi-GB SD-card wedge we just fixed.
DEFAULT_JOURNAL_SIZE_LIMIT = 67_108_864

# busy_timeout — how long a writer waits for a lock before SQLITE_BUSY.
# 30 s is generous for Pi-class storage where checkpoints can briefly
# block writers; matches NodeHistoryDB's prior `timeout=30`.
DEFAULT_BUSY_TIMEOUT_SECONDS = 30.0


def connect_tuned(
    path: Union[str, Path],
    *,
    busy_timeout_seconds: float = DEFAULT_BUSY_TIMEOUT_SECONDS,
    journal_size_limit: int = DEFAULT_JOURNAL_SIZE_LIMIT,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    """Open a SQLite connection with the MeshForge-standard pragmas.

    - journal_mode=WAL: per-commit fsyncs no longer rewrite the entire
      DB file. The change is persistent on the DB header — first open
      after a fresh file (or rollback-journal DB) performs the conversion.
    - synchronous=NORMAL: with WAL this is durable across power loss
      across most-recent commits; sufficient for telemetry. Per-connection.
    - journal_size_limit: caps WAL file growth so a long-running writer
      can't balloon it to multi-GB.
    - busy_timeout: configured via sqlite3.connect's `timeout` parameter,
      which sets PRAGMA busy_timeout for us.

    Args:
        path: Database file path (str or Path).
        busy_timeout_seconds: How long a writer waits for a lock.
        journal_size_limit: Cap on WAL file size in bytes.
        check_same_thread: Pass-through to sqlite3.connect. Set False
            when sharing the connection across threads with external locking.

    Returns:
        A tuned sqlite3.Connection. Caller owns lifecycle (close it).
    """
    conn = sqlite3.connect(
        str(path),
        timeout=busy_timeout_seconds,
        check_same_thread=check_same_thread,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA journal_size_limit={int(journal_size_limit)}")
    return conn
