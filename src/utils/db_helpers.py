"""SQLite connection helpers — single source of truth for tuned pragmas.

Why this exists: a 1.95 GB rollback-journal-mode node_history.db wedged the
:5000 map service on a fleet box (2026-04-26) for 16+ minutes during a prune.
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
import urllib.parse
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


# Sentinel for "use sqlite3's default isolation_level (deferred autocommit)".
_DEFAULT_ISOLATION = object()


def _is_readonly_uri(uri: bool, path: Union[str, Path]) -> bool:
    """True when this open is read-only, so file-writing pragmas are skipped.

    Only a URI open can be read-only, and only via a `mode=` parameter in its
    query string. Parsed rather than substring-matched so a database whose
    PATH happens to contain "mode=ro" is not mistaken for a read-only open.
    `mode=ro` is the read-only mode; `memory` is writable, `rw`/`rwc` are not
    read-only.
    """
    if not uri:
        return False
    try:
        query = urllib.parse.urlparse(str(path)).query
        return "ro" in urllib.parse.parse_qs(query).get("mode", [])
    except (ValueError, AttributeError):
        return False


def connect_tuned(
    path: Union[str, Path],
    *,
    busy_timeout_seconds: float = DEFAULT_BUSY_TIMEOUT_SECONDS,
    journal_size_limit: int = DEFAULT_JOURNAL_SIZE_LIMIT,
    check_same_thread: bool = True,
    isolation_level=_DEFAULT_ISOLATION,
    uri: bool = False,
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
        isolation_level: Pass-through. Default keeps sqlite3's default
            (auto-begin DEFERRED transactions). Pass None for autocommit
            or "DEFERRED"/"IMMEDIATE"/"EXCLUSIVE" for explicit modes.
        uri: Pass-through. Set True when path is a URI like
            "file:/.../db?mode=ro" (read-only readers).

    Returns:
        A tuned sqlite3.Connection. Caller owns lifecycle (close it).
    """
    kwargs = dict(
        timeout=busy_timeout_seconds,
        check_same_thread=check_same_thread,
        uri=uri,
    )
    if isolation_level is not _DEFAULT_ISOLATION:
        kwargs["isolation_level"] = isolation_level
    conn = sqlite3.connect(str(path), **kwargs)
    # journal_mode / journal_size_limit WRITE to the database file, so on a
    # read-only connection they raise "attempt to write a readonly database"
    # and the connect fails outright. The docstring above has advertised
    # `mode=ro` readers since this helper was written, and MF013 requires every
    # consumer to come through here — so a read-only reader had to choose
    # between the lint rule and read-only, and the rule usually won. That is a
    # tool forcing the weaker guarantee (2026-09-05).
    #
    # WAL is a property of the DATABASE, not of a connection: whichever writer
    # opened it already set it, and a reader neither needs nor can change it.
    # Skipping these on a read-only open therefore loses nothing.
    if not _is_readonly_uri(uri, path):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA journal_size_limit={int(journal_size_limit)}")
    # synchronous is per-connection and legal read-only; harmless either way.
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn
