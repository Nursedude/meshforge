#!/usr/bin/env python3
"""dep_advisory_check.py — do the fleet's INSTALLED packages carry advisories?

Born 2026-09-04, from a day where every surface we owned said "fine":

  * ``requirements/rns.txt`` pinned ``cryptography>=45.0.7,<47`` while the fix
    for the reported CVE was 49.0.0. The pin made the fleet UNPATCHABLE, and
    every box was fully COMPLIANT with it.
  * ``probe_dep_version_drift`` read ``clean`` throughout — correctly, by its
    own definition. It asks only "installed BELOW the requirements floor?",
    so a ceiling that forbids the patched version is invisible to it. The
    calibrated_claims coverage question in live form: what would still pass
    this check if the thing were dead?
  * GitHub's own Dependabot flipped the alert to ``state=fixed`` with NO
    manifest change, reported ONE medium — and the installed version actually
    carried FOUR advisories, THREE of them high. Its ``first_patched_version``
    (49.0.0) would still have left a high open (GHSA-g6cj-pr64-35w5, ``<50``).

So this asks the question none of those asked, against the artifact that
matters: **for the version actually INSTALLED on each box, are there any
published advisories?** The authority is GitHub's advisory database queried
DIRECTLY -- deliberately not this repo's own Dependabot, which is the surface
that lied. Evidence you did not write outranks evidence you did.

Manager-side. Mirrors ``ecosystem_ci_status.sh`` in shape and exit codes so it
reads the same way at a terminal and wires into ``cron_verdict.sh`` identically.

Outputs:
  - Always: ``~/.meshforge-dep-advisories`` — one block per box.
  - On findings: ``~/.meshforge-dep-ADVISORY`` — present = act, absent = clean.
    (Absent is only written when the run could actually SEE the fleet.)

Exit codes:
  0 — every box observed, no advisories
  1 — at least one advisory on an installed version
  2 — UNKNOWN: gh missing/unauthenticated, or NO box could be observed.
      Never conflated with 0. Unobservable is not healthy.

⚠️ The distinction this file exists to protect: a FAILED advisory query and a
query that legitimately returned zero advisories are DIFFERENT. An empty list
from a broken call must never render as "clean" — that is the exact error path
that produced every incident cited above.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from utils.paths import get_real_user_home  # noqa: E402

CLEAN = "clean"
ADVISORY = "advisory"
UNKNOWN = "unknown"

#: Security-relevant packages we actually run. Deliberately a SHORT explicit
#: list, not "everything installed": each entry is here because a flaw in it
#: reaches something real on this fleet, and an unbounded list would turn a
#: cheap check into a rate-limited crawl.
#:   cryptography/pyopenssl — RNS+LXMF primitives and every TLS path
#:   requests/urllib3       — outbound HTTP (collector, federation, ntfy)
#:   flask/werkzeug/jinja2  — the map server's web surface
#:   pyyaml                 — config parsing (fleet_roles, deployment)
#:   paramiko/twisted       — ssh + event loop where present
DEFAULT_PACKAGES = (
    "cryptography", "pyopenssl", "requests", "urllib3",
    "flask", "werkzeug", "jinja2", "pyyaml", "paramiko", "twisted",
)

#: Read versions from the interpreter that HOSTS the services (rnsd's own
#: python), not from whatever ``python3`` resolves to for the login shell.
#: calibrated_claims #7: verify the consumer of record. A box can carry more
#: python envs than a probe can see (user site, root dist-packages, pipx).
REMOTE_PYTHON = "/usr/bin/python3"

# ⚠️ KNOWN LIMITATION (found 2026-09-05, not yet cured): ``md.version(name)``
# returns the FIRST distribution of that name on sys.path, which is not
# necessarily the one ``import name`` resolves to. the manager box carries THREE
# cryptography dist-infos — 43.0.3 and 46.0.3 in the SAME
# /usr/local/lib/python3.13/dist-packages, plus 43.0.0 from apt — so this
# reporter says 43.0.3 while the interpreter actually imports 46.0.3. Here it
# OVER-reports, which is the safe direction; a stale NEWER dist-info shadowing
# a live older package would UNDER-report and read clean while vulnerable code
# runs. The honest fix is to report ALL claimants rather than silently pick
# one; until then, treat a version from this sweep as "some dist-info on that
# box says", and confirm against ``import`` before acting on a single box.
_REMOTE_SRC = """
import importlib.metadata as md, json
out = {}
for name in %(pkgs)r:
    try:
        out[name] = md.version(name)
    except Exception:
        out[name] = None          # not installed here -- absent, not vulnerable
print(json.dumps(out))
"""


def _run(cmd: List[str], timeout: int, stdin_text: Optional[str] = None):
    """(rc, stdout, stderr). Never raises on a non-zero exit — callers must be
    able to tell 'the command failed' from 'the answer was empty'."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, input=stdin_text)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ss" % timeout
    except (OSError, ValueError) as exc:
        return 125, "", str(exc)


def read_fleet_hosts(explicit: Optional[str] = None) -> Tuple[List[str], Optional[str]]:
    """Same precedence as scripts/fleet_pull.sh — ONE notion of who the fleet
    is. Returns (hosts, error); an unreadable list is an error, never []."""
    home = str(get_real_user_home())
    candidates = [explicit] if explicit else [
        os.environ.get("MESHFORGE_FLEET_HOSTS"),
        os.path.join(home, ".config", "meshforge", "fleet_hosts.meshforge"),
        "/etc/meshforge/fleet_hosts.meshforge",
        os.path.join(home, ".config", "meshforge", "fleet_hosts"),
        "/etc/meshforge/fleet_hosts",
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                hosts = [ln.strip() for ln in fh
                         if ln.strip() and not ln.strip().startswith("#")]
        except OSError as exc:
            return [], "fleet host list %s unreadable: %s" % (path, exc)
        if not hosts:
            return [], "fleet host list %s is empty" % path
        return hosts, None
    return [], "no fleet host list found (looked in ~/.config/meshforge and /etc/meshforge)"


def local_host_aliases() -> set:
    """Names that mean THIS box. The manager has no inbound ssh to itself, so
    a host resolving to one of these is collected by running the same reporter
    locally rather than over ssh."""
    aliases = {"localhost"}
    try:
        name = socket.gethostname()
    except OSError:
        name = ""
    if name:
        aliases.add(name)
        aliases.add(name.split(".")[0])
    return {a.lower() for a in aliases if a}


def collect_installed(host: str, packages, timeout: int = 40):
    """{pkg: version|None} for one box, or (None, reason). A box we cannot
    reach is UNKNOWN — it is never folded in as though it were clean."""
    src = _REMOTE_SRC % {"pkgs": list(packages)}
    if host.lower() in local_host_aliases():
        rc, out, err = _run([REMOTE_PYTHON, "-"], timeout=timeout,
                            stdin_text=src)
        how = "local python failed"
    else:
        rc, out, err = _run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host,
             REMOTE_PYTHON, "-"], timeout=timeout, stdin_text=src)
        how = "unreachable or remote python failed"
    if rc != 0:
        return None, "%s (rc=%s): %s" % (
            how, rc, (err or out).strip()[:120])
    try:
        return json.loads(out.strip().splitlines()[-1]), None
    except (ValueError, IndexError) as exc:
        return None, "unparseable version report: %s" % exc


def query_advisories(package: str, version: str, timeout: int = 60):
    """(list_of_advisories, None) or (None, reason).

    ⚠️ The whole point of this function's signature: a FAILED query returns
    ``None``, never ``[]``. An empty list is a POSITIVE finding of no known
    advisories and may only come from a call that actually succeeded."""
    rc, out, err = _run(
        ["gh", "api", "/advisories?ecosystem=pip&affects=%s@%s&per_page=100"
         % (package, version)], timeout=timeout)
    if rc != 0:
        return None, "advisory query failed (rc=%s): %s" % (
            rc, (err or out).strip()[:120])
    try:
        data = json.loads(out)
    except ValueError as exc:
        return None, "advisory response unparseable: %s" % exc
    if not isinstance(data, list):
        return None, "advisory response was not a list"
    return data, None


def summarize(advisories) -> str:
    bits = []
    for a in advisories:
        if not isinstance(a, dict):
            continue
        bits.append("%s(%s)" % (a.get("ghsa_id") or "?",
                                a.get("severity") or "?"))
    return ", ".join(bits)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hosts-file")
    ap.add_argument("--packages", nargs="*", default=list(DEFAULT_PACKAGES))
    ap.add_argument("--host", action="append", dest="only_hosts",
                    help="check just these host(s) instead of the fleet list")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    home = str(get_real_user_home())
    status_file = os.path.join(home, ".meshforge-dep-advisories")
    finding_file = os.path.join(home, ".meshforge-dep-ADVISORY")

    rc_gh, _, _ = _run(["gh", "auth", "status"], timeout=30)
    if rc_gh != 0:
        msg = "UNKNOWN: gh CLI missing or unauthenticated — the fleet was NOT checked"
        _write(status_file, [msg])
        if not args.quiet:
            print(msg)
        return 2

    if args.only_hosts:
        hosts, err = args.only_hosts, None
    else:
        hosts, err = read_fleet_hosts(args.hosts_file)
    if err:
        msg = "UNKNOWN: %s — the fleet was NOT checked" % err
        _write(status_file, [msg])
        if not args.quiet:
            print(msg)
        return 2

    if not args.only_hosts:
        # The manager box is absent from the fleet host list BY DESIGN — that
        # list is shared with fleet_pull.sh, which ssh-es outward and would be
        # ssh-ing to itself. This sweep has no such constraint: it collects
        # locally when the host is this box. Inheriting the exclusion made the
        # checker blind to the box it RUNS ON, and on 2026-09-05 that box was
        # carrying five unnoticed cryptography advisories (three high) while
        # this very report covered the other nine and read as the fleet's
        # whole story. Blind by construction, not by scope.
        aliases = local_host_aliases()
        if not any(h.lower() in aliases for h in hosts):
            try:
                hosts = [socket.gethostname().split(".")[0]] + list(hosts)
            except OSError:
                pass

    # One query per DISTINCT (pkg, version) across the whole fleet, not one per
    # box: nine boxes usually share a handful of versions, and the advisory API
    # is a shared external resource we should lean on lightly.
    cache: Dict[Tuple[str, str], Tuple[Optional[list], Optional[str]]] = {}
    lines: List[str] = []
    findings: List[str] = []
    observed = 0
    unknown_hosts: List[str] = []

    for host in hosts:
        installed, herr = collect_installed(host, args.packages)
        if installed is None:
            unknown_hosts.append(host)
            lines.append("%-20s UNKNOWN — %s" % (host, herr))
            continue
        observed += 1
        for pkg in args.packages:
            ver = installed.get(pkg)
            if not ver:
                continue                      # absent by design → inert, not a finding
            key = (pkg, ver)
            if key not in cache:
                cache[key] = query_advisories(pkg, ver)
            advs, aerr = cache[key]
            if advs is None:
                lines.append("%-20s %-14s %-10s UNKNOWN — %s"
                             % (host, pkg, ver, aerr))
                unknown_hosts.append("%s/%s" % (host, pkg))
                continue
            if advs:
                lines.append("%-20s %-14s %-10s ADVISORY x%d — %s"
                             % (host, pkg, ver, len(advs), summarize(advs)))
                findings.append("%s %s %s: %s" % (host, pkg, ver, summarize(advs)))
            else:
                lines.append("%-20s %-14s %-10s clean" % (host, pkg, ver))

    header = ["# fleet dependency advisories — installed versions vs the "
              "GitHub advisory DB", "# boxes observed: %d/%d%s"
              % (observed, len(hosts),
                 ("  UNKNOWN: " + ", ".join(unknown_hosts)) if unknown_hosts else "")]
    _write(status_file, header + lines)
    if not args.quiet:
        print("\n".join(header + lines))

    # Order matters: a run that saw NOTHING is UNKNOWN even though it also
    # found no advisories — "no findings" from an observation that never
    # happened is the lie this file exists to refuse.
    if observed == 0:
        _write(finding_file, ["UNKNOWN: no box could be observed"])
        return 2
    if findings:
        _write(finding_file,
               ["# installed versions carrying published advisories"] + findings)
        return 1
    if unknown_hosts:
        # Partial blindness: real findings elsewhere are absent-of-evidence.
        # Do not delete the finding file on a partial view.
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
