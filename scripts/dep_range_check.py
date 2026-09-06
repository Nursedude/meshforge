#!/usr/bin/env python3
"""dep_range_check.py — does each DECLARED range still permit a safe version?

Born 2026-09-05, from the one question none of our surfaces asked.

``requirements/rns.txt`` pinned ``cryptography>=45.0.7,<47`` for six months.
Every version satisfying that range carried a medium plus three highs, so a box
could be fully requirements-COMPLIANT and vulnerable at the same time. Nothing
we owned could say so:

  * ``probe_dep_version_drift`` asks "installed BELOW the floor?" — a CEILING
    that forbids the patched version is invisible to it, and it read ``clean``
    throughout;
  * ``dep_advisory_check.py`` (its sibling) asks about INSTALLED versions — it
    would have flagged the boxes, but only once they were already running
    vulnerable code, and it says nothing about what a FRESH install resolves;
  * Dependabot asks about the pinned version, and does not reliably report
    "your constraint forbids every safe version". It reported ONE medium where
    four advisories applied, then flipped to ``fixed`` on an unchanged manifest.

So this asks the manifest's own question: **for each declared requirement, is
there at least one RELEASED version that satisfies the specifier AND carries no
published advisory?** If the answer is no, the pin is unpatchable and no amount
of installing will fix it — the manifest must change.

Deliberately a SEPARATE script from ``dep_advisory_check.py`` rather than
another section of it. That one sweeps the fleet over ssh and goes UNKNOWN when
boxes are unreachable; this one needs no fleet at all. Folding them would let a
WAN hiccup blind a check that never depended on the WAN — which is the very
defect class both scripts exist to catch. They share the daily timer, not an
exit code.

Authorities are both EXTERNAL and neither is this repo's Dependabot: PyPI for
what versions exist, GitHub's advisory DB for what is vulnerable.

Outputs:
  - Always: ``~/.meshforge-dep-ranges`` — one line per declared requirement.
  - On findings: ``~/.meshforge-dep-RANGE-FINDING`` — present = act.
    (Written only when the run could actually SEE both authorities.)

Exit codes:
  0 — every declared range still permits at least one advisory-free release
  1 — at least one range permits NOTHING safe (or permits no release at all)
  2 — UNKNOWN: gh missing/unauthenticated, PyPI unreachable, a vulnerable range
      that would not parse, or no manifest found. Never conflated with 0.

⚠️ The invariant this file protects: a range we could not PARSE, or a version
list we could not FETCH, must never render as "clean". We cannot prove a
version is safe from data we failed to read — that is how the six-month pin
stayed invisible, in checker form.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from utils.paths import get_real_user_home  # noqa: E402

from packaging.requirements import InvalidRequirement, Requirement  # noqa: E402
from packaging.specifiers import InvalidSpecifier, SpecifierSet  # noqa: E402
from packaging.version import InvalidVersion, Version  # noqa: E402

CLEAN = "clean"
FINDING = "finding"
UNKNOWN = "unknown"
INERT = "inert"

PYPI_URL = "https://pypi.org/pypi/%s/json"
USER_AGENT = "MeshForge-dep-range-check"

# Requirements we never judge, and why. These are MeshForge-owned hard forks
# pinned by commit SHA under the `# MF-FORK-PIN` SSOT and gated fleet-wide by
# scripts/rns_version_check.py; they are not on PyPI at the pinned commit, and
# moving them is a governed merge/parity/canary/roll procedure, never a version
# bump. Detected structurally (a direct URL) as well, but named here so an
# operator reading the report knows the omission is deliberate.
FORK_PINNED = ("rns", "lxmf")


def _run(cmd: List[str], timeout: int):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ss" % timeout
    except (OSError, ValueError) as exc:
        return 125, "", str(exc)


def find_manifests(root: str) -> List[str]:
    """Every pip manifest in the repo, in a stable order."""
    found = []
    top = os.path.join(root, "requirements.txt")
    if os.path.isfile(top):
        found.append(top)
    reqdir = os.path.join(root, "requirements")
    if os.path.isdir(reqdir):
        for name in sorted(os.listdir(reqdir)):
            if name.endswith(".txt"):
                found.append(os.path.join(reqdir, name))
    return found


def parse_manifest(path: str):
    """[(lineno, Requirement)], [(lineno, raw, reason)] — unparseable lines are
    RETURNED, never skipped. A requirement we could not read is a hole in the
    audit and must be visible as one."""
    reqs, bad = [], []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        return [], [(0, "", "unreadable: %s" % exc)]
    for i, raw in enumerate(lines, 1):
        s = raw.split("#", 1)[0].strip()
        if not s or s.startswith("-"):      # blank, comment, or -r/-e/--flag
            continue
        try:
            reqs.append((i, Requirement(s)))
        except InvalidRequirement as exc:
            bad.append((i, s, str(exc)))
    return reqs, bad


def pypi_versions(package: str, timeout: int = 30):
    """(sorted [Version], None) or (None, reason). A failed fetch is a reason,
    never an empty list — an empty list would read as 'no safe version exists'
    and invert the verdict."""
    try:
        req = urllib.request.Request(PYPI_URL % package,
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, "not on PyPI (404)"
        return None, "PyPI HTTP %s" % exc.code
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return None, "PyPI unreachable: %s" % type(exc).__name__

    out = []
    for vstr, files in (data.get("releases") or {}).items():
        if not files or all(f.get("yanked") for f in files):
            continue                      # never released, or fully yanked
        try:
            out.append(Version(vstr))
        except InvalidVersion:
            continue                      # legacy version string; not a candidate
    if not out:
        return None, "PyPI listed no usable release"
    return sorted(out), None


def _normalize_range(raw: str) -> str:
    """GitHub emits a bare ``= 1.2.3`` for single-version advisories, which
    packaging rejects. Normalize to ``== 1.2.3``; leave everything else alone."""
    parts = []
    for chunk in raw.split(","):
        c = chunk.strip()
        if c.startswith("=") and not c.startswith(("==", "=>", "=<")):
            c = "=" + c
        parts.append(c)
    return ",".join(parts)


def advisory_ranges(package: str, timeout: int = 60):
    """([(ghsa, severity, SpecifierSet)], None) or (None, reason).

    A range that will not parse makes the WHOLE package unknown: we cannot
    prove a version escapes a constraint we could not read."""
    # NEWLINE-DELIMITED, deliberately. `gh api --paginate` concatenates raw JSON
    # arrays (`[...][...]`), and the obvious repair — splicing "][" into "," —
    # is string surgery on data we do not control: one advisory description
    # containing that sequence would either corrupt the parse or, worse, parse
    # into something valid and wrong. `--jq '.[] | @json'` makes gh emit one
    # compact object per line across every page instead (gh 2.46 has no
    # --slurp), so each line parses independently and a malformed line is
    # visible as itself.
    rc, out, err = _run(
        ["gh", "api", "--paginate",
         "/advisories?ecosystem=pip&affects=%s&per_page=100" % package,
         "--jq", ".[] | @json"],
        timeout=timeout)
    if rc != 0:
        return None, "advisory query failed (rc=%s): %s" % (rc, (err or out).strip()[:120])
    advisories = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            advisories.append(json.loads(line))
        except ValueError as exc:
            return None, "unparseable advisory record: %s" % exc

    ranges = []
    for adv in advisories:
        ghsa = adv.get("ghsa_id") or "?"
        sev = adv.get("severity") or "?"
        for vuln in adv.get("vulnerabilities") or []:
            pkg = ((vuln.get("package") or {}).get("name") or "").lower()
            if pkg != package.lower():
                continue
            raw = vuln.get("vulnerable_version_range")
            if not raw:
                return None, "%s gave no vulnerable_version_range" % ghsa
            try:
                ranges.append((ghsa, sev, SpecifierSet(_normalize_range(raw))))
            except InvalidSpecifier:
                return None, "%s range %r would not parse" % (ghsa, raw)
    return ranges, None


def evaluate(spec: SpecifierSet, versions, ranges):
    """(state, detail). Pure — the whole verdict, given data already fetched."""
    permitted = [v for v in versions if spec.contains(v)]
    if not permitted:
        return FINDING, ("specifier permits NO released version — the pin "
                         "cannot be satisfied at all")
    safe = []
    for v in permitted:
        hits = [g for g, _s, r in ranges if r.contains(v, prereleases=True)]
        if not hits:
            safe.append(v)
    if not safe:
        worst = sorted({s for _g, s, r in ranges
                        if any(r.contains(v, prereleases=True) for v in permitted)})
        return FINDING, ("every one of %d permitted release(s) carries an "
                         "advisory (severities: %s) — UNPATCHABLE without "
                         "changing the pin" % (len(permitted), ", ".join(worst) or "?"))
    return CLEAN, "%d of %d permitted release(s) advisory-free; newest safe = %s" % (
        len(safe), len(permitted), max(safe))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", action="append", dest="roots",
                    help="repo root to audit; repeatable (default: this repo). "
                         "A root that does not EXIST is reported inert (not "
                         "deployed on this box) — a root that exists with no "
                         "manifest is UNKNOWN. Those are different claims.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    home = str(get_real_user_home())
    status_file = os.path.join(home, ".meshforge-dep-ranges")
    finding_file = os.path.join(home, ".meshforge-dep-RANGE-FINDING")

    rc_gh, _, _ = _run(["gh", "auth", "status"], timeout=30)
    if rc_gh != 0:
        msg = "UNKNOWN: gh CLI missing or unauthenticated — ranges were NOT checked"
        _write(status_file, [msg])
        if not args.quiet:
            print(msg)
        return 2

    roots = args.roots or [os.path.dirname(_HERE)]
    lines = ["# declared ranges vs released versions + the GitHub advisory DB"]
    findings: List[str] = []
    unknowns: List[str] = []
    work: List[Tuple[str, str, List[str]]] = []   # (label, root, manifests)

    for root in roots:
        label = os.path.basename(os.path.normpath(root)) or root
        if not os.path.isdir(root):
            # Absent BY DESIGN on this box (the sister repo is not checked out
            # everywhere). Absent-by-design must read inert, never as an
            # observation that failed, or the real failures have nowhere to
            # stand out.
            lines.append("%-24s %-18s %-9s %s" % (
                label, "(repo)", INERT, "not checked out here — nothing to audit"))
            continue
        manifests = find_manifests(root)
        if not manifests:
            # PRESENT but unreadable as a python project: that is a failed
            # observation, not an absence, and it must not read as a pass.
            unknowns.append("%s:(no manifest)" % label)
            lines.append("%-24s %-18s %-9s %s" % (
                label, "(repo)", UNKNOWN, "no pip manifest found under %s" % root))
            continue
        work.append((label, root, manifests))

    if not work and not unknowns:
        msg = "UNKNOWN: no auditable repo among %s — nothing was checked" % ", ".join(roots)
        _write(status_file, [msg])
        if not args.quiet:
            print(msg)
        return 2
    # One fetch per package across all manifests: several files declare the
    # same package, and both authorities are shared external resources.
    vcache: Dict[str, Tuple] = {}
    acache: Dict[str, Tuple] = {}

    for label, root, manifests in work:
      for path in manifests:
        rel = "%s/%s" % (label, os.path.relpath(path, root))
        reqs, bad = parse_manifest(path)
        for lineno, raw, reason in bad:
            unknowns.append("%s:%s" % (rel, lineno))
            lines.append("%-24s %-18s %-9s %s" % (rel, raw[:18] or "(file)", UNKNOWN,
                                                  "unparseable: " + reason))
        for lineno, req in reqs:
            name = req.name.lower()
            if req.url or name in FORK_PINNED:
                lines.append("%-24s %-18s %-9s %s" % (
                    rel, req.name, INERT,
                    "fork-pinned by SHA under MF-FORK-PIN — governed separately, "
                    "not a version bump"))
                continue
            if not str(req.specifier):
                lines.append("%-24s %-18s %-9s %s" % (
                    rel, req.name, INERT,
                    "unpinned — always resolvable to latest, so this check has "
                    "no question to ask (a floor would be a separate argument)"))
                continue

            if name not in vcache:
                vcache[name] = pypi_versions(name)
            versions, verr = vcache[name]
            if versions is None:
                unknowns.append("%s:%s" % (rel, req.name))
                lines.append("%-24s %-18s %-9s %s" % (rel, req.name, UNKNOWN, verr))
                continue

            if name not in acache:
                acache[name] = advisory_ranges(name)
            ranges, aerr = acache[name]
            if ranges is None:
                unknowns.append("%s:%s" % (rel, req.name))
                lines.append("%-24s %-18s %-9s %s" % (rel, req.name, UNKNOWN, aerr))
                continue

            state, detail = evaluate(req.specifier, versions, ranges)
            lines.append("%-24s %-18s %-9s %s [%s]" % (
                rel, req.name, state, detail, req.specifier))
            if state == FINDING:
                findings.append("%s:%s %s%s — %s" % (
                    rel, lineno, req.name, req.specifier, detail))

    header = "# %d repo(s), %d manifest(s); %d finding(s); %d unknown(s)" % (
        len(work), sum(len(m) for _l, _r, m in work), len(findings), len(unknowns))
    _write(status_file, [header] + lines)
    if not args.quiet:
        print("\n".join([header] + lines))

    if findings:
        _write(finding_file, ["# declared ranges that permit nothing safe"] + findings)
        return 1
    if unknowns:
        # Partial blindness. Do NOT delete the finding file — absence of
        # evidence from a half-read audit is not evidence of absence.
        return 2
    _remove(finding_file)
    return 0


def _write(path: str, lines: List[str]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError as exc:
        print("warn: could not write %s: %s" % (path, exc), file=sys.stderr)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        print("warn: could not remove %s: %s" % (path, exc), file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
