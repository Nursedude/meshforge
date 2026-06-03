#!/usr/bin/env python3
"""Operator CLI for the gateway's durable session layer (Theme-A step 3).

Inspects/manages gateway_sessions.db — the bridge's record of active
(mesh node ↔ RNS peer) conversations, maintained when
rns.sessions_enabled. Read/expire work regardless of the flag.
Precedent: scripts/gateway_contacts.py.

Usage:
    python3 scripts/gateway_sessions.py list
    python3 scripts/gateway_sessions.py expire             # all sessions
    python3 scripts/gateway_sessions.py expire !aabb0042   # one mesh node
    (--db PATH overrides the default DB; used by tests)
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.session_store import SessionStore  # noqa: E402


def _age(ts: float) -> str:
    sec = max(0, int(time.time() - ts))
    if sec < 90:
        return f"{sec}s"
    if sec < 5400:
        return f"{sec // 60}m"
    return f"{sec // 3600}h"


def _peer_label(peer_hash: str, use_default_db: bool) -> str:
    """Best-effort display name for an RNS peer via the identity SSOT.

    Only consulted when running against the default DBs (tests pass --db
    and must not touch real contact_mapping.db). Never fails list.
    """
    label = peer_hash[:8]
    if not use_default_db:
        return label
    try:
        from gateway.identity_binding import IdentityBinder
        contact = IdentityBinder().show('rns', peer_hash)
        if contact is not None and contact.display_name:
            label = f"{label} ({contact.display_name})"
    except Exception:
        pass
    return label


def cmd_list(store: SessionStore, args) -> int:
    rows = store.list_active()
    if not rows:
        print("no active sessions")
        return 0
    for r in rows:
        peer = _peer_label(r["rns_peer_hash"], args.db is None)
        print(f"{r['mesh_id']:<11} <-> {peer:<34} "
              f"last {_age(r['last_activity']):>4} ago  "
              f"msgs {r['message_count']}")
    print(f"({len(rows)} active session(s))")
    return 0


def cmd_expire(store: SessionStore, args) -> int:
    n = store.expire_all(args.mesh_id)
    scope = f" for {args.mesh_id}" if args.mesh_id else ""
    print(f"expired {n} session(s){scope}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Gateway session layer (active mesh<->RNS conversations)")
    parser.add_argument("--db", default=None,
                        help="gateway_sessions.db path override")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list active sessions")
    p = sub.add_parser("expire", help="force-expire sessions")
    p.add_argument("mesh_id", nargs="?", default=None,
                   help="limit to one mesh node id (!hex8); omit for all")

    args = parser.parse_args(argv)
    store = SessionStore(db_path=args.db)
    handler = {"list": cmd_list, "expire": cmd_expire}[args.command]
    return handler(store, args)


if __name__ == "__main__":
    sys.exit(main())
