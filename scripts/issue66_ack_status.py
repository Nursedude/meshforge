#!/usr/bin/env python3
"""
Issue #66 — operator observability of the application-layer ack queue.

Reads the gateway's live message_queue.db and reports current ack
tracking state. Read-only — safe to run alongside a running gateway
(SQLite WAL mode handles the concurrent reader).

Output: one row per ack-tracked message in the format
  <msg_id>  <ack_status>  origin=<network>:<address>  timeout=<iso>
plus a summary line.

Usage (on a gateway box, e.g. moc3 or meshanchor-server):
  python3 scripts/issue66_ack_status.py
  python3 scripts/issue66_ack_status.py --watch
  python3 scripts/issue66_ack_status.py --db /path/to/message_queue.db
  python3 scripts/issue66_ack_status.py --only pending

Use --watch (poll every 2s) when running an end-to-end smoke: send an
ack_required=True message via the bridge, watch this script's output
flip from 'pending' to 'acked' or 'timeout' to confirm the synthesis
path fired against the live DB.

This is the radio-leg handoff observation tool — software-level proof
of the synthesis path is in tests/test_ack_synthesis_e2e_issue66.py.
"""

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path


def _default_db_path() -> str:
    """
    Default location matches gateway.message_queue.PersistentMessageQueue
    fallback: ~/.config/meshforge/message_queue.db. Uses
    get_real_user_home() so the script sees the same DB the gateway
    service writes to even when run under sudo.
    """
    # Make src/ importable so we can reuse the project's sudo-aware path.
    sys.path.insert(
        0, str(Path(__file__).resolve().parent.parent / "src")
    )
    from utils.paths import get_real_user_home  # noqa: E402

    return str(
        get_real_user_home() / ".config" / "meshforge" / "message_queue.db"
    )


def _query(db_path: str, only: str = None):
    """Return list of dicts for ack-tracked messages, optionally filtered."""
    if not Path(db_path).exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        sys.exit(2)

    # Reuse the project's tuned-WAL connection so the script respects
    # the same pragmas the gateway sets (MF013 / 2026-04-26 fleet-wedge
    # invariant). Even though this script only SELECTs, going through
    # connect_tuned keeps the read consistent with concurrent gateway
    # writes and avoids busy_timeout drift.
    sys.path.insert(
        0, str(Path(__file__).resolve().parent.parent / "src")
    )
    from utils.db_helpers import connect_tuned  # noqa: E402

    conn = connect_tuned(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute("PRAGMA table_info(messages)")
        cols = {row[1] for row in cursor.fetchall()}
        if "ack_required" not in cols:
            print(
                "this DB predates Issue #66 (no ack_required column).\n"
                "the gateway needs to restart to run the migration.",
                file=sys.stderr,
            )
            sys.exit(3)

        sql = """
            SELECT id, ack_status, ack_origin_network, ack_origin_address,
                   ack_timeout_at, ack_at, created_at
              FROM messages
             WHERE ack_required = 1
        """
        params = ()
        if only:
            sql += " AND ack_status = ?"
            params = (only,)
        sql += " ORDER BY created_at DESC LIMIT 50"
        cursor = conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def _print(rows):
    """One row per line + summary."""
    counts = {"pending": 0, "acked": 0, "timeout": 0}
    for r in rows:
        status = r.get("ack_status") or "?"
        counts[status] = counts.get(status, 0) + 1
        msg_id = r["id"]
        origin_net = r.get("ack_origin_network") or "-"
        origin_addr = r.get("ack_origin_address") or "-"
        deadline = r.get("ack_timeout_at") or "-"
        acked_at = r.get("ack_at") or "-"
        print(
            f"  {msg_id[:24]:24}  {status:8}  "
            f"origin={origin_net}:{origin_addr}  deadline={deadline}  "
            f"acked={acked_at}"
        )
    print(
        f"-- pending={counts.get('pending', 0)}  "
        f"acked={counts.get('acked', 0)}  "
        f"timeout={counts.get('timeout', 0)}  "
        f"total={len(rows)}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None,
                        help="path to message_queue.db (default: ~/.config/meshforge)")
    parser.add_argument("--watch", action="store_true",
                        help="poll every 2s; Ctrl-C to stop")
    parser.add_argument("--only", choices=("pending", "acked", "timeout"),
                        default=None, help="filter by ack_status")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="--watch poll interval seconds (default 2.0)")
    args = parser.parse_args()

    db_path = args.db or _default_db_path()

    if not args.watch:
        rows = _query(db_path, only=args.only)
        _print(rows)
        return

    print(f"watching {db_path} (Ctrl-C to stop)")
    try:
        while True:
            rows = _query(db_path, only=args.only)
            print(f"\n[{time.strftime('%H:%M:%S')}]")
            _print(rows)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
