#!/usr/bin/env python3
"""DB audit — health/security/bloat scan across all SQLite consumers.

Reads from src/utils/db_inventory.py and reports per-DB:
  - file size + WAL size
  - owner / mode (security)
  - live PRAGMA state vs. expected
  - schema present
  - retention contract from inventory

Exit code 1 if any DB has a FAIL verdict (size > cap, pragmas wrong,
world-writable, etc.). Suitable for cron / CI / pre-merge.

Why this exists: 2026-04-26 the fleet-host :5000 service wedged 16+
minutes in jbd2_log_wait_commit because node_history.db had grown to
1.95 GB in rollback-journal mode. Manual audit on each new DB has
already missed once (health_state.db in Phase 1). This script is the
automated check.

Usage:
    python3 scripts/db_audit.py             # default thresholds
    python3 scripts/db_audit.py --max-db-mb 200 --max-wal-mb 50
    python3 scripts/db_audit.py --json      # machine-readable output
    python3 scripts/db_audit.py --verbose   # include per-spec notes
"""

import argparse
import json
import os
import pwd
import sqlite3
import stat
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add src/ to path so we can import the inventory + helper.
_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from utils.db_helpers import connect_tuned  # noqa: E402
from utils.db_inventory import INVENTORY, DBSpec  # noqa: E402


@dataclass
class AuditResult:
    name: str
    path: str
    exists: bool
    size_bytes: int = 0
    wal_bytes: int = 0
    owner: str = ""
    mode: int = 0
    journal_mode: str = ""
    synchronous: int = -1
    journal_size_limit: int = -1
    schema_tables: int = 0
    has_auto_prune: bool = False
    retention_days: Optional[int] = None
    verdict: str = "OK"   # OK | WARN | FAIL | NOT_CREATED
    issues: List[str] = field(default_factory=list)
    notes: str = ""


def _format_size(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n/1024:.0f}{unit[0]}"
        n //= 1
        if n >= 1024:
            n //= 1024
    return str(n)


def _human_size(n: int) -> str:
    """Compact human-readable size."""
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n // 1024}K"
    if n < 1024 * 1024 * 1024:
        return f"{n // (1024*1024)}M"
    return f"{n / (1024*1024*1024):.1f}G"


def _owner_string(stat_result: os.stat_result) -> str:
    try:
        user = pwd.getpwuid(stat_result.st_uid).pw_name
    except KeyError:
        user = str(stat_result.st_uid)
    try:
        import grp
        group = grp.getgrgid(stat_result.st_gid).gr_name
    except (KeyError, ImportError):
        group = str(stat_result.st_gid)
    return f"{user}:{group}"


def audit_db(
    spec: DBSpec,
    max_db_mb: int,
    max_wal_mb: int,
    mode_mask: int,
) -> AuditResult:
    """Audit a single DB. Never raises — failures captured in `issues`."""
    result = AuditResult(
        name=spec.name,
        path=str(spec.path_factory()),
        exists=False,
        has_auto_prune=spec.has_auto_prune,
        retention_days=spec.retention_days,
        notes=spec.notes,
    )
    db_path = spec.path_factory()
    if not db_path.exists():
        result.verdict = "NOT_CREATED"
        return result
    result.exists = True

    # File metadata
    try:
        st = db_path.stat()
        result.size_bytes = st.st_size
        result.owner = _owner_string(st)
        result.mode = stat.S_IMODE(st.st_mode)
    except OSError as e:
        result.issues.append(f"stat failed: {e}")
        result.verdict = "FAIL"
        return result

    # WAL sibling
    wal_path = db_path.with_suffix(db_path.suffix + "-wal")
    if wal_path.exists():
        try:
            result.wal_bytes = wal_path.stat().st_size
        except OSError:
            pass

    # Permission checks
    if result.mode & mode_mask:
        result.issues.append(f"mode {oct(result.mode)} violates mask {oct(mode_mask)}")
        result.verdict = "FAIL"

    # Size thresholds
    size_mb = result.size_bytes / (1024 * 1024)
    if size_mb > max_db_mb:
        result.issues.append(f"size {size_mb:.0f} MB > cap {max_db_mb} MB")
        if result.verdict != "FAIL":
            result.verdict = "WARN"
    wal_mb = result.wal_bytes / (1024 * 1024)
    if wal_mb > max_wal_mb:
        result.issues.append(f"WAL {wal_mb:.0f} MB > cap {max_wal_mb} MB")
        result.verdict = "FAIL"

    # PRAGMA state via a read-only URI connection — strictly does not
    # mutate (no journal_mode conversion, no per-connection PRAGMA writes).
    # journal_mode is persisted on the DB header so a read-only open
    # surfaces it correctly. synchronous + journal_size_limit are
    # per-connection PRAGMAs and will report DEFAULTS here, not what the
    # writer applies — we trust lint MF013 + TestSqliteConnectContract
    # to enforce that all writers go through connect_tuned.
    try:
        ro_uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(ro_uri, uri=True, timeout=2.0)
        try:
            result.journal_mode = (
                conn.execute("PRAGMA journal_mode").fetchone()[0] or ""
            ).lower()
            result.synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
            result.journal_size_limit = conn.execute(
                "PRAGMA journal_size_limit"
            ).fetchone()[0]
            tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            result.schema_tables = tables
        finally:
            conn.close()
    except sqlite3.Error as e:
        result.issues.append(f"PRAGMA query failed: {e}")
        result.verdict = "FAIL"
        return result

    # PRAGMA expectations — only journal_mode is checked here.
    # journal_mode is persistent on the DB header so the read-only open
    # sees the writer's last-set value correctly. If it's not 'wal', a
    # writer somewhere opened this DB without going through connect_tuned
    # (or the DB predates the migration). FAIL is the right verdict.
    if spec.expected_journal_mode and result.journal_mode != spec.expected_journal_mode:
        result.issues.append(
            f"journal_mode={result.journal_mode!r} (expected {spec.expected_journal_mode!r}). "
            f"Open this DB through utils.db_helpers.connect_tuned to migrate."
        )
        result.verdict = "FAIL"
    # synchronous + journal_size_limit are per-connection, so a
    # read-only audit can't observe what the writer applies. The lint
    # rule MF013 + TestSqliteConnectContract enforce writer correctness
    # at code-time; we don't double-check at audit time to avoid
    # false-positive WARN/FAIL on legitimate defaults.

    # Schema sanity — if the DB exists but has zero tables, something
    # truncated/corrupted it.
    if result.schema_tables == 0:
        result.issues.append("zero tables in schema")
        result.verdict = "FAIL"

    return result


def render_table(results: List[AuditResult], verbose: bool = False) -> str:
    """Render a fixed-width table of audit results."""
    lines = []
    header = (
        f"{'DB':<22} {'SIZE':>6} {'WAL':>6} {'OWNER':<16} "
        f"{'MODE':>5} {'PRAGMAS':<8} {'RETENTION':<10} {'VERDICT':<11}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in results:
        if not r.exists:
            lines.append(
                f"{r.name:<22} {'-':>6} {'-':>6} {'-':<16} "
                f"{'-':>5} {'-':<8} {'-':<10} {r.verdict:<11}"
            )
            continue
        pragmas = "ok" if not any("journal_mode" in i or "synchronous" in i or "journal_size_limit" in i for i in r.issues) else "FAIL"
        retention = (
            f"{r.retention_days}d auto" if r.has_auto_prune and r.retention_days
            else "auto" if r.has_auto_prune
            else "manual" if r.retention_days is None
            else f"{r.retention_days}d"
        )
        lines.append(
            f"{r.name:<22} {_human_size(r.size_bytes):>6} "
            f"{_human_size(r.wal_bytes):>6} {r.owner:<16} "
            f"{oct(r.mode)[-4:]:>5} {pragmas:<8} {retention:<10} {r.verdict:<11}"
        )
        if r.issues:
            for issue in r.issues:
                lines.append(f"    └─ {issue}")
        if verbose and r.notes:
            lines.append(f"    {r.notes}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--max-db-mb", type=int, default=500,
                        help="WARN above this size in MB (default 500)")
    parser.add_argument("--max-wal-mb", type=int, default=100,
                        help="FAIL above this WAL size in MB (default 100)")
    parser.add_argument("--mode-mask", type=lambda s: int(s, 8), default=0o002,
                        help="FAIL if any of these mode bits are set "
                             "(default 002 — world-writable)")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON instead of a table")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Include per-spec notes in table output")
    args = parser.parse_args()

    results = [
        audit_db(spec, args.max_db_mb, args.max_wal_mb, args.mode_mask)
        for spec in INVENTORY
    ]

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print(render_table(results, verbose=args.verbose))

    # Exit code: 1 if any FAIL.
    return 1 if any(r.verdict == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
