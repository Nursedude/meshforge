#!/usr/bin/env python3
"""MF version-consistency guard — SSOT vs pyproject vs README badge/heading.

Born 2026-07-07 from a repo audit that found a **4-way version drift** in this
very repo: ``src/__version__.py`` said ``0.6.2-beta`` while ``pyproject.toml``
said ``0.5.5-beta``, the README shields.io badge said ``0.6.1-beta``, and the
CHANGELOG top was ``0.5.4-beta``. Four "authoritative" answers to one question.
That drift also propagated into the sister repos' READMEs. This guard makes the
class un-recurrable: it asserts the machine-canonical version spots all agree
with the SSOT.

**The SSOT is** ``src/__version__.py`` → ``__version__``. Everything else must
match it:
  - ``pyproject.toml`` ``[project].version``
  - the README shields.io ``version-<v>`` badge (``-`` is escaped as ``--``)
  - the README ``## What Works (v<v>)`` heading (if present)

Exit 0 = consistent. Exit 1 = drift (each mismatch printed). Exit 2 = could not
read the SSOT (unobservable is never a pass — honest_failure_modes #2).

Run standalone:  ``python3 scripts/version_consistency_check.py``
Other repo:      ``python3 scripts/version_consistency_check.py --repo /opt/meshforge-maps``
Wired into:      ``scripts/lint.py --all`` (repo-level pass; see ``check_version_consistency``).

Portability note: the SSOT file differs per repo (``src/__version__.py`` here,
``src/__init__.py`` in meshforge-maps, ``meshing_around_clients/__init__.py`` in
the meshing_around client). ``--ssot`` overrides the default so the same guard
can be mirrored to the sisters as a follow-on.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional, Tuple

# Default SSOT + consumers, relative to the repo root. Overridable per repo.
DEFAULT_SSOT = "src/__version__.py"
PYPROJECT = "pyproject.toml"
README = "README.md"

# Known SSOT locations across the fleet's repos, most-specific first. Auto-detect
# walks these so ONE byte-identical guard works in meshforge (src/__version__.py),
# meshforge-maps (src/__init__.py), and the meshing_around client
# (meshing_around_clients/__init__.py) without per-repo edits. Pass --ssot to override.
CANDIDATE_SSOTS = [
    "src/__version__.py",
    "src/__init__.py",
    "meshing_around_clients/__init__.py",
]

_VERSION_RE = re.compile(r"""^\s*__version__\s*=\s*["']([^"']+)["']""", re.M)


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def read_ssot_version(repo_root: str, ssot_rel: str) -> Optional[str]:
    """The single source of truth: ``__version__ = "..."`` in the SSOT file."""
    text = _read(os.path.join(repo_root, ssot_rel))
    if text is None:
        return None
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


def read_pyproject_version(repo_root: str) -> Optional[str]:
    """``[project].version`` from pyproject.toml.

    Returns None if there is no ``[project]`` table version (e.g. a pyproject
    that carries only tooling config) — that is "not declared here", NOT a
    mismatch, so the caller treats None as "nothing to compare".
    """
    text = _read(os.path.join(repo_root, PYPROJECT))
    if text is None:
        return None
    # Scope to the [project] table so we never grab tool.black.target-version
    # or similar. Walk sections; capture the first `version = "..."` inside
    # [project]. tomllib would be cleaner but this repo targets py39.
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = stripped == "[project]"
            continue
        if in_project:
            m = re.match(r"""version\s*=\s*["']([^"']+)["']""", stripped)
            if m:
                return m.group(1)
    return None


def _unescape_badge(raw: str) -> str:
    """shields.io escapes a literal ``-`` as ``--``. Reverse it."""
    return raw.replace("--", "-")


def read_readme_badge_version(repo_root: str) -> Optional[str]:
    """The shields.io ``version-<v>`` badge in the README.

    Handles all the badge dialects the fleet uses:
      ``version-0.6.2--beta-blue.svg`` · ``version-0.7.0--beta-blue`` (no .svg)
      · ``version-0.6.0-blue.svg``. A literal ``-`` in the version is escaped by
    shields as ``--``, so a *single* ``-`` can only be the color delimiter — that
    is what lets us split version from color unambiguously without a ``.svg``
    anchor (the blind spot that let the maps 0.7.0 badge slip past this guard).
    """
    text = _read(os.path.join(repo_root, README))
    if text is None:
        return None
    m = re.search(r"/badge/version-((?:[^-\s)]|--)+?)-[0-9A-Za-z]+(?:\.svg)?", text)
    if not m:
        return None
    return _unescape_badge(m.group(1))


def read_readme_whatworks_version(repo_root: str) -> Optional[str]:
    """The ``## What Works (v<v>)`` heading, if the README uses one."""
    text = _read(os.path.join(repo_root, README))
    if text is None:
        return None
    m = re.search(r"^#{1,4}\s*What Works\s*\(v([0-9][0-9A-Za-z.\-]*)\)", text, re.M)
    return m.group(1) if m else None


def resolve_ssot(repo_root: str, ssot_rel: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Return (ssot_rel_used, version). If ssot_rel is given, use it verbatim.
    Otherwise walk CANDIDATE_SSOTS and use the first that declares __version__ —
    so the same guard works in every repo. Returns (candidates[0], None) if none
    declare a version (the caller treats None as a failure, never "consistent")."""
    if ssot_rel:
        return ssot_rel, read_ssot_version(repo_root, ssot_rel)
    for cand in CANDIDATE_SSOTS:
        v = read_ssot_version(repo_root, cand)
        if v is not None:
            return cand, v
    return CANDIDATE_SSOTS[0], None


def check(
    repo_root: str = ".",
    ssot_rel: Optional[str] = None,
) -> Tuple[Optional[str], List[str]]:
    """Return (ssot_version, [mismatch messages]).

    An empty message list with a non-None ssot means consistent. A None ssot
    means the SSOT could not be read — the caller must treat that as a failure
    (exit 2), never as "consistent". ``ssot_rel=None`` auto-detects the SSOT file.
    """
    ssot_rel, ssot = resolve_ssot(repo_root, ssot_rel)
    problems: List[str] = []
    if ssot is None:
        tried = ssot_rel if ssot_rel else ", ".join(CANDIDATE_SSOTS)
        return None, [f"SSOT version unreadable (tried {tried}) in {repo_root}"]

    # Each consumer is compared only when it declares a version. A consumer that
    # declares nothing (e.g. no [project].version, no badge) is "not asserted
    # here", not a mismatch — but at least ONE consumer must exist so a repo
    # can't pass by declaring versions nowhere.
    checked_any = False
    for label, found in (
        (f"{PYPROJECT} [project].version", read_pyproject_version(repo_root)),
        (f"{README} version badge", read_readme_badge_version(repo_root)),
        (f"{README} 'What Works (v…)' heading", read_readme_whatworks_version(repo_root)),
    ):
        if found is None:
            continue
        checked_any = True
        if found != ssot:
            problems.append(f"{label} = {found!r} != SSOT {ssot!r}")

    if not checked_any:
        problems.append(
            f"no consumer declares a version to compare against SSOT {ssot!r} "
            f"(expected a pyproject [project].version or a README badge)"
        )
    return ssot, problems


def main() -> int:
    parser = argparse.ArgumentParser(description="MeshForge version-consistency guard")
    parser.add_argument("--repo", default=None,
                        help="Repo root (default: the repo this script lives in)")
    parser.add_argument("--ssot", default=None,
                        help="SSOT file relative to repo root (default: auto-detect "
                             f"from {', '.join(CANDIDATE_SSOTS)})")
    args = parser.parse_args()

    repo_root = args.repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ssot, problems = check(repo_root, args.ssot)

    if ssot is None:
        print(f"✗ version-consistency: {problems[0]}")
        return 2
    if problems:
        print(f"✗ version-consistency: SSOT is {ssot!r}, but:")
        for p in problems:
            print(f"    - {p}")
        print("  Fix the drifting consumer(s) to match the SSOT, then re-run.")
        return 1
    print(f"✓ version-consistency: all declared versions agree with SSOT {ssot!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
