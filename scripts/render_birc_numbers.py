#!/usr/bin/env python3
"""Render docs/birc_2026_05_17_live_numbers.md from live repo state.

Reads version + test counts from each ecosystem repo under /opt/ and
optionally a map API endpoint. Writes a markdown file Claude.ai design
can re-pull before talk day to refresh the deck's stateful slides.

Usage:
    python3 scripts/render_birc_numbers.py
    python3 scripts/render_birc_numbers.py --map-host moc1
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPOS = [
    ("meshforge",                Path("/opt/meshforge"),                "src/__version__.py"),
    ("meshforge-maps",           Path("/opt/meshforge-maps"),           "src/__init__.py"),
    ("meshing_around_meshforge", Path("/opt/meshing_around_meshforge"), "meshing_around_clients/__init__.py"),
    ("meshanchor",               Path("/opt/meshanchor"),               "src/__version__.py"),
    ("RNS-Management-Tool",      Path("/opt/RNS-Management-Tool"),      None),
]

VERSION_RE = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')


def read_version(repo_root: Path, version_file: str | None) -> str:
    if version_file is None:
        return "—"
    path = repo_root / version_file
    if not path.exists():
        return "—"
    m = VERSION_RE.search(path.read_text())
    return m.group(1) if m else "—"


def count_tests(repo_root: Path) -> tuple[int, int]:
    """Return (test_file_count, test_function_count)."""
    tests_dir = repo_root / "tests"
    if not tests_dir.exists():
        return (0, 0)
    files = list(tests_dir.rglob("test_*.py"))
    fn_count = 0
    for f in files:
        try:
            fn_count += len(re.findall(r"^\s*def\s+test_", f.read_text(), re.MULTILINE))
        except (OSError, UnicodeDecodeError):
            continue
    return (len(files), fn_count)


def fetch_map_status(host: str, port: int = 5000, timeout: int = 5) -> dict | None:
    url = f"http://{host}:{port}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def render(args) -> str:
    rows = []
    totals_files = 0
    totals_fns = 0
    for name, root, vfile in REPOS:
        if not root.exists():
            rows.append((name, "—", 0, 0, "repo absent"))
            continue
        ver = read_version(root, vfile)
        nf, nfn = count_tests(root)
        totals_files += nf
        totals_fns += nfn
        rows.append((name, ver, nf, nfn, ""))

    map_status = fetch_map_status(args.map_host) if args.map_host else None

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out: list[str] = []
    out.append("# BIRC 2026-05-17 — live numbers")
    out.append("")
    out.append("> **For Claude.ai design**: this file is regenerated from live "
               "repo state by `scripts/render_birc_numbers.py`. Pull this raw URL "
               "before lock to refresh the deck's stateful slides.")
    out.append(">")
    out.append("> **Raw URL**: "
               "`https://raw.githubusercontent.com/Nursedude/meshforge/main/"
               "docs/birc_2026_05_17_live_numbers.md`")
    out.append(">")
    out.append(f"> **Generated**: {now}")
    out.append("")
    out.append("## Ecosystem versions + test counts")
    out.append("")
    out.append("| Repo | Version | Test files | Test functions | Notes |")
    out.append("|------|---------|-----------:|---------------:|-------|")
    for name, ver, nf, nfn, note in rows:
        out.append(f"| `{name}` | `{ver}` | {nf} | {nfn:,} | {note} |")
    out.append(f"| **Total** | — | **{totals_files}** | **{totals_fns:,}** | across ecosystem |")
    out.append("")

    if map_status:
        hist = map_status.get("history", {})
        directory = map_status.get("directory", {})
        out.append(f"## Live map state — `{args.map_host}:5000`")
        out.append("")
        out.append(f"- Directory nodes total: **{directory.get('total', 0):,}**")
        out.append(f"  - with position: {directory.get('with_position', 0):,}")
        out.append(f"  - without position: {directory.get('without_position', 0):,}")
        by_net = directory.get("by_network", {})
        if by_net:
            parts = [f"{k}: {v:,}" for k, v in by_net.items()]
            out.append(f"- By network: {' · '.join(parts)}")
        out.append(f"- Retention: {hist.get('retention_days', '?')} d (local) · "
                   f"{directory.get('retention_external_days', '?')} d (external)")
        out.append("")
        out.append("> **Note on `/api/status.history`**: `total_observations` is "
                   "`MAX(rowid)` on `node_observations` — a lifetime insert "
                   "high-water mark, NOT a current-window count. Pruning shrinks "
                   "`COUNT(*)` but not rowid. `unique_nodes` there is the "
                   "directory size, not distinct observers. For an "
                   "audience-facing \"live mesh\" number, cite the directory "
                   "total above; for a real activity number, query "
                   "`node_observations` directly (~hundreds of active "
                   "observers, ~30k rows in a 2-day window on the publishing box).")
        out.append("")
    elif args.map_host:
        out.append(f"## Live map state — `{args.map_host}:5000`")
        out.append("")
        out.append(f"- Could not fetch `/api/status` from `{args.map_host}` — host "
                   "down, port blocked, or daemon stopped.")
        out.append("")

    out.append("## Slide-by-slide refresh hints")
    out.append("")
    out.append("These are the slides whose numbers should be replaced from the "
               "values above before the deck is locked on 2026-05-16.")
    out.append("")
    out.append("- **Slide 4 / footer** — `MeshForge — Nursedude / WH6GXZ` "
               "title — version stamp.")
    out.append("- **Slide 10** — STATUS · vX.Y.Z-BETA → use `meshforge` version. "
               "Test count → use **MeshForge test function total**, not "
               "ecosystem-wide total.")
    out.append("- **Slide 13** — Field Validation — `3,160 tests · 90 files` → "
               "replace with MeshForge file + function counts. `vX.Y.Z-β` → "
               "MeshForge version.")
    out.append("- **Slide 14** — `meshforge-maps vX.Y.Z-b · NN,NNN nodes` → "
               "meshforge-maps version + map directory total.")
    out.append("- **Slide 16** — `~3,000 TESTS` → MeshForge test function total "
               "(round to nearest 100).")
    out.append("- **Slide 18** — repo grid version stamps → use the table above.")
    out.append("- **Slide 22** — `5,619 TESTS Across all five repos` → "
               "ecosystem total from the table above.")
    out.append("")
    out.append("## Lock & version-stamp pattern")
    out.append("")
    out.append("Add to footer of every slide (or just title + closing slides):")
    out.append("")
    out.append("```")
    fm_ver = next((v for n, v, *_ in rows if n == "meshforge"), "—")
    out.append(f"MeshForge {fm_ver} · data window 2026-04-28 → 2026-05-04 · "
               f"rendered {now.split()[0]}")
    out.append("```")
    out.append("")
    out.append("Any drift between deck and reality after lock becomes "
               "intentional — the deck is a snapshot, the live state is "
               "always at `github.com/Nursedude/meshforge`.")
    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map-host", default="localhost",
                        help="Host to query for /api/status (default: localhost; "
                             "skip live map block if empty string).")
    parser.add_argument("--output", default="docs/birc_2026_05_17_live_numbers.md",
                        help="Output markdown path (relative to repo root or absolute).")
    args = parser.parse_args()
    if args.map_host == "":
        args.map_host = None

    body = render(args)
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = Path(__file__).resolve().parent.parent / out_path
    out_path.write_text(body)
    print(f"Wrote {out_path} ({len(body)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
