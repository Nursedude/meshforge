#!/usr/bin/env python3
"""LXMF identity census — which identity on THIS box is which hash.

Born 2026-10-07: 58cecbd0 sat in peer configs as an unowned "lone hash" for
months; it was the MeshAnchor gateway, named only after hashing its key
file by hand. This makes that a command.

Walks the user's home for RNS identity files (regular files, exactly 64
bytes, "identity" in the name), and prints each one's PUBLIC
``lxmf.delivery`` address with the app its path implies. Read-only. Key
material is read locally and NEVER printed. A directory it cannot read is
listed as BLIND — unobservable is not empty.

Fleet fan-out (from the manager):
  for b in $(awk '!/^#/{print $1}' ~/.config/meshforge/fleet_hosts); do
    ssh "$b" python3 /opt/meshforge/scripts/lxmf_identity_census.py --box "$b"
  done
Run with sudo to include root-owned identities (e.g. a root rnsd's).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from typing import List, Optional

IDENTITY_SIZE = 64          # RNS Identity.to_file: X25519 + Ed25519 private keys
MAX_DEPTH = 6
SKIP_DIRS = {".cache", ".git", "node_modules", "site-packages", "dist-packages",
             ".npm", ".cargo", ".rustup", "__pycache__", ".venv", "venv",
             ".local/lib", ".mozilla", ".config/chromium", "go"}

# Path fragment (relative to home) → app. First match wins; most specific first.
APP_HINTS = [
    (".config/meshforge/gateway_identity", "MeshForge gateway"),
    (".config/meshanchor/gateway_identity", "MeshAnchor gateway"),
    (".config/meshforge/", "MeshForge (component)"),
    (".config/meshanchor/", "MeshAnchor (component)"),
    ("transport_identity", "rnsd transport identity (not an LXMF inbox)"),
    (".nomadnetwork", "NomadNet"),
    (".lxmd/", "lxmd (propagation node)"),
    ("meshchat", "MeshChat / MeshChatX"),
    (".config/sideband", "Sideband"),
    (".reticulum/storage/identity", "rnsd transport identity (not an LXMF inbox)"),
]
#: Path fragments that mark a COPY (backup/snapshot), not a live identity.
COPY_HINTS = ("superseded", "snapshot", "backup", "archived", ".bak")

NAME_HINTS = [("broadcast", "broadcast bridge"), ("echo", "lab-echo"),
              ("tracer", "lab-tracer"), ("synth", "lab-synth"),
              ("reemit", "re-emit bridge")]


def _app(rel: str) -> str:
    low = rel.lower()
    base = "unknown app"
    for frag, app in APP_HINTS:
        if frag in low:
            base = app
            break
    if base.endswith("(component)"):
        fname = os.path.basename(low)
        for frag, what in NAME_HINTS:
            if frag in fname:
                return base.replace("(component)", what)
    return base


def _walk(home: Path):
    """Yield (candidate files, blind dirs) under ``home``."""
    blind: List[str] = []
    found: List[Path] = []
    stack = [(home, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = list(os.scandir(d))
        except PermissionError:
            blind.append(str(d))
            continue
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                if e.is_dir():
                    rel = os.path.relpath(e.path, home)
                    if e.name in SKIP_DIRS or rel in SKIP_DIRS or depth >= MAX_DEPTH:
                        continue
                    stack.append((Path(e.path), depth + 1))
                elif (e.is_file() and "identity" in e.name.lower()
                      and e.stat().st_size == IDENTITY_SIZE):
                    found.append(Path(e.path))
            except OSError:
                continue
    return found, blind


def scan_blind(home: Path) -> List[str]:
    return _walk(Path(home))[1]


def scan(home: Path) -> List[dict]:
    import RNS  # external dep, imported only when actually scanning
    home = Path(home)
    rows = []
    for p in sorted(_walk(home)[0]):
        try:
            ident = RNS.Identity.from_file(str(p))
            if ident is None:
                continue
            h = RNS.Destination.hash_from_name_and_identity("lxmf.delivery", ident)
        except Exception:  # noqa: BLE001 — not an identity after all
            continue
        rel = os.path.relpath(p, home)
        rows.append({"path": str(p), "app": _app(rel), "lxmf_delivery": h.hex(),
                     "copy": any(c in rel.lower() for c in COPY_HINTS)})
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--home", type=Path, default=None)
    ap.add_argument("--box", default=None, help="label for the output")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.home is None:
        _src = str(Path(__file__).resolve().parents[1] / "src")
        if _src not in sys.path:
            sys.path.insert(0, _src)
        from utils.paths import get_real_user_home
        a.home = get_real_user_home()
    box = a.box or socket.gethostname()
    try:
        rows = scan(a.home)
    except ImportError as e:
        print(f"== {box}: UNKNOWN — RNS not importable here ({e}); "
              f"identities NOT scanned (unobservable, not absent)")
        return 2
    blind = scan_blind(a.home)
    if a.json:
        print(json.dumps({"box": box, "identities": rows, "blind": blind}, indent=2))
        return 0
    live = [r for r in rows if not r["copy"]]
    print(f"== {box}: {len(live)} live identity file(s) under {a.home}"
          f" (+{len(rows) - len(live)} backup/snapshot copies)")
    for r in sorted(rows, key=lambda r: (r["copy"], r["app"], r["path"])):
        tag = "  [copy]" if r["copy"] else ""
        print(f"  {r['lxmf_delivery']}  {r['app']:<36} {r['path']}{tag}")
    for b in blind:
        print(f"  BLIND (unreadable, not empty): {b}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
