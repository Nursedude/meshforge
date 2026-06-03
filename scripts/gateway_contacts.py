#!/usr/bin/env python3
"""Operator CLI for the gateway's cross-protocol identity SSOT.

Theme-A step 2 companion tool (precedent: scripts/issue66_ack_status.py).
Manages the contact_mapping.db that IdentityBinder populates from bridge
traffic. Works regardless of rns.cross_protocol_identity_enabled —
explicit operator links are always allowed and recorded verified=1.

Address tokens are protocol-qualified (the canonical reply-token shape):
    meshtastic:!aabb0042    rns:<32-hex>    meshcore:<12-hex>

Usage:
    python3 scripts/gateway_contacts.py list
    python3 scripts/gateway_contacts.py show meshtastic:!aabb0042
    python3 scripts/gateway_contacts.py link "Alice" rns:deadbeef...
    python3 scripts/gateway_contacts.py verify rns:deadbeef...
    python3 scripts/gateway_contacts.py unlink meshcore:aabbccddeeff
    (--db PATH overrides the default DB; used by tests)
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from gateway.canonical_message import parse_reply_token  # noqa: E402
from gateway.identity_binding import IdentityBinder  # noqa: E402


def _parse_token(binder: IdentityBinder, token: str):
    """Token → (protocol, normalized address) or (None, None) + message."""
    proto, addr = parse_reply_token(token)
    if proto is None:
        print(f"error: malformed token {token!r} "
              f"(expected proto:addr, e.g. meshtastic:!aabb0042)")
        return None, None
    norm = binder._norm_address(proto, addr)
    if norm is None:
        print(f"error: {addr!r} is not a valid {proto} address")
        return None, None
    return proto, norm


def cmd_list(binder: IdentityBinder, _args) -> int:
    contacts = binder.list_all()
    if not contacts:
        print("no contacts")
        return 0
    for c in contacts:
        rows = binder.address_rows(c.id)
        flags = "".join(
            "V" if r["verified"] else "u" for r in rows
        )
        addrs = "  ".join(f"{r['protocol']}:{r['address']}" for r in rows)
        print(f"{c.display_name:<24} [{flags}] {addrs}")
    print(f"({len(contacts)} contact(s); V=verified, u=unverified)")
    return 0


def cmd_show(binder: IdentityBinder, args) -> int:
    proto, norm = _parse_token(binder, args.token)
    if proto is None:
        return 1
    contact = binder.show(proto, norm)
    if contact is None:
        print(f"no contact holds {proto}:{norm}")
        return 1
    print(f"name:    {contact.display_name}")
    print(f"id:      {contact.id}")
    print(f"created: {contact.created_at}")
    for r in binder.address_rows(contact.id):
        v = "verified" if r["verified"] else "unverified"
        print(f"  {r['protocol']:<11} {r['address']}  ({v}, "
              f"last_seen {r['last_seen']})")
    return 0


def cmd_link(binder: IdentityBinder, args) -> int:
    proto, norm = _parse_token(binder, args.token)
    if proto is None:
        return 1
    contact_id = binder.link(args.name, proto, norm)
    if contact_id is None:
        print("link failed (see log above — address taken, name conflict, "
              "or invalid input)")
        return 1
    print(f"linked {proto}:{norm} -> '{args.name}' "
          f"({contact_id[:8]}, verified)")
    return 0


def cmd_unlink(binder: IdentityBinder, args) -> int:
    proto, norm = _parse_token(binder, args.token)
    if proto is None:
        return 1
    if binder.unlink(proto, norm):
        print(f"unlinked {proto}:{norm}")
        return 0
    print(f"{proto}:{norm} was not mapped")
    return 1


def cmd_verify(binder: IdentityBinder, args) -> int:
    proto, norm = _parse_token(binder, args.token)
    if proto is None:
        return 1
    if binder.verify(proto, norm):
        print(f"verified {proto}:{norm}")
        return 0
    print(f"{proto}:{norm} was not mapped")
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Gateway cross-protocol contact mapping (identity SSOT)")
    parser.add_argument("--db", default=None,
                        help="contact_mapping.db path override")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list all contacts")
    p = sub.add_parser("show", help="show the contact holding an address")
    p.add_argument("token", help="proto:addr token")
    p = sub.add_parser("link", help="link an address to a name (verified)")
    p.add_argument("name", help="contact display name")
    p.add_argument("token", help="proto:addr token")
    p = sub.add_parser("unlink", help="remove one address mapping")
    p.add_argument("token", help="proto:addr token")
    p = sub.add_parser("verify", help="mark an address operator-verified")
    p.add_argument("token", help="proto:addr token")

    args = parser.parse_args(argv)
    binder = IdentityBinder(db_path=args.db)
    handler = {
        "list": cmd_list,
        "show": cmd_show,
        "link": cmd_link,
        "unlink": cmd_unlink,
        "verify": cmd_verify,
    }[args.command]
    return handler(binder, args)


if __name__ == "__main__":
    sys.exit(main())
