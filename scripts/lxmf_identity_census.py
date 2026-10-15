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
Also walks the system rnsd configdir (``SYSTEM_ROOTS``: /etc/reticulum,
where the fleet's rnsd keeps ``storage/transport_identity``). That key is
root-owned, so without sudo it reads BLIND — run with sudo to name it.
A symlink is never followed (loops, foreign trees) and never skipped
silently: it is listed as ``BLIND (symlink, not followed)`` with its target.
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
MAX_DEPTH = 10        # DR snapshot keys sit at depth 7 under fleet-configs/
SKIP_DIRS = {".cache", ".git", "node_modules", "site-packages", "dist-packages",
             ".npm", ".cargo", ".rustup", "__pycache__", ".venv", "venv",
             ".local/lib", ".mozilla", ".config/chromium", "go",
             ".platformio", ".arduino15", ".espressif", ".pio",  # build trees
             ".claude"}  # agent state: never RNS identities

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


SYSTEM_ROOTS = (Path("/etc/reticulum"),)
SYSTEM_HINTS = [("storage/transport_identity", "rnsd transport identity (system)")]


def _walk(home: Path):
    """(candidate files, unreadable dirs, depth-capped dirs, symlinks) under
    ``home``. A symlink is RETURNED, never silently dropped (non-author pass
    2026-10-15: ``~/.reticulum -> /etc/reticulum`` read as zero identities).


    A dir NOT descended because of MAX_DEPTH is RETURNED, never silently
    dropped (2026-10-07: depth 6 hid the DR snapshot gateway keys)."""
    blind: List[str] = []
    capped: List[str] = []
    linked: List[str] = []
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
                    # Only a link that could HIDE an identity is a blind spot:
                    # a directory link, or a file link named like one. An
                    # interpreter link in a venv is not (legibility).
                    if e.is_dir() or "identity" in e.name.lower():
                        linked.append(e.path)
                    continue
                if e.is_dir():
                    rel = os.path.relpath(e.path, home)
                    if e.name in SKIP_DIRS or rel in SKIP_DIRS:
                        continue
                    if depth >= MAX_DEPTH:
                        capped.append(e.path)
                        continue
                    stack.append((Path(e.path), depth + 1))
                elif (e.is_file() and "identity" in e.name.lower()
                      and e.stat().st_size == IDENTITY_SIZE):
                    found.append(Path(e.path))
            except OSError:
                continue
    return found, blind, capped, linked


def scan_linked(home: Path) -> List[str]:
    return _walk(Path(home))[3]


def scan_system(roots=None) -> List[dict]:
    """Identity rows under the system roots (each labelled by SYSTEM_HINTS)."""
    import RNS
    rows = []
    for root in (SYSTEM_ROOTS if roots is None else roots):
        root = Path(root)
        if not root.is_dir():
            continue
        for p in sorted(_walk(root)[0]):
            try:
                ident = RNS.Identity.from_file(str(p))
                if ident is None:
                    continue
                h = RNS.Destination.hash_from_name_and_identity("lxmf.delivery", ident)
            except Exception:  # noqa: BLE001 — not an identity after all
                continue
            rel = os.path.relpath(p, root)
            app = next((a for frag, a in SYSTEM_HINTS if rel.endswith(frag)),
                       f"system ({root})")
            rows.append({"path": str(p), "app": app, "lxmf_delivery": h.hex(),
                         "copy": False})
    return rows


def scan_blind(home: Path) -> List[str]:
    return _walk(Path(home))[1]


def scan_capped(home: Path) -> List[str]:
    return _walk(Path(home))[2]


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
    ap.add_argument("--no-system", action="store_true",
                    help="walk the home only, not " + ", ".join(map(str, SYSTEM_ROOTS)))
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
    _, blind, capped, linked = _walk(Path(a.home))
    if not a.no_system:
        rows += scan_system()
        for root in SYSTEM_ROOTS:
            if Path(root).exists():
                _, b2, c2, l2 = _walk(Path(root))
                blind += b2
                capped += c2
                linked += l2
    if a.json:
        print(json.dumps({"box": box, "identities": rows, "blind": blind,
                          "symlinks": linked}, indent=2))
        return 0
    live = [r for r in rows if not r["copy"]]
    print(f"== {box}: {len(live)} live identity file(s) under {a.home}"
          f" (+{len(rows) - len(live)} backup/snapshot copies)")
    for r in sorted(rows, key=lambda r: (r["copy"], r["app"], r["path"])):
        tag = "  [copy]" if r["copy"] else ""
        print(f"  {r['lxmf_delivery']}  {r['app']:<36} {r['path']}{tag}")
    for b in blind:
        print(f"  BLIND (unreadable, not empty): {b}")
    for ln in linked:
        try:
            target = os.readlink(ln)
        except OSError:
            target = "?"
        print(f"  BLIND (symlink, not followed): {ln} -> {target}")
    for c in capped[:20]:
        print(f"  BLIND (depth limit {MAX_DEPTH}, not searched): {c}")
    if len(capped) > 20:
        print(f"  BLIND (depth limit {MAX_DEPTH}): +{len(capped) - 20} more dirs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
