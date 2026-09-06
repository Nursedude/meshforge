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

Distro-managed packages (added 2026-09-06)
------------------------------------------
Debian and Ubuntu patch CVEs WITHOUT changing the upstream version:
``python3-urllib3 1.26.12-1+deb12u4`` on bookworm carries eight CVE fixes and
still reports ``1.26.12`` to ``importlib.metadata``. Matching that version
against upstream advisory ranges therefore OVER-reports — on 2026-09-05 it
called moc4's urllib3 "8 advisories, 5 high" when seven were already patched
by the distro and the eighth was one Debian had triaged as ignored. Worse, the
over-report pointed at the WRONG cure: pip-installing over an apt package puts
a second copy in ``/usr/local`` that shadows ``/usr/lib``, which is exactly how
the manager box ended up with three cryptography dist-infos.

So for a package whose IMPORT resolves under ``/usr/lib/python3/dist-packages``
the reporter also records the owning ``dpkg`` package, its distro version, and
the CVE ids named in that package's shipped ``changelog.Debian.gz`` — the
distro's own record of what it fixed, an authority we did not write. An
advisory whose CVE is named there is reported ``distro-patched`` and is NOT a
finding. One that is not named stays a finding, tagged ``apt-managed`` so the
reader reaches for apt (or the accept list), never pip.

An advisory the distro has DECLINED to fix (Debian's ``no-dsa`` / ``ignored``
triage, or a Windows-only issue) has no apt cure and would otherwise be a
finding forever. ``~/.config/meshforge/dep_advisory_accepted`` records that
decision explicitly, one per line::

    GHSA-2xpw-w6gg-jr37  until=2026-12-31  Debian ignored (no-dsa) on bookworm+trixie

Acceptance MUST carry ``until=`` — a decision without an expiry is how a bug
becomes policy — and it only ever silences a DISTRO-MANAGED install. A
pip-managed copy of the same version has a fix one ``pip`` away and stays a
finding regardless of the list. Accepted and expired entries are still shown
in the status file, so nothing accepted ever disappears from view.

The same per-box read also reports APT HYGIENE, the root cause of the drift
this widening was born from: ``unattended-upgrades`` absent, security updates
pending, or package lists so stale that "pending" is only a lower bound. Each
is a finding; a box with no dpkg at all is inert (nothing to say).

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
that produced every incident cited above. The same rule covers the distro
changelog: an UNREADABLE changelog means "cannot tell what was patched" and
leaves every advisory open — never "nothing was patched", never "all were".
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
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

#: Distribution name -> import name, where they differ. ``find_spec`` needs the
#: import name to say WHERE a package resolves from; everything not listed
#: imports under its own distribution name.
IMPORT_NAMES = {"pyopenssl": "OpenSSL", "pyyaml": "yaml"}

#: Where Debian/Ubuntu apt puts python packages. An import resolving under
#: here is distro-managed: its upstream version string says nothing about
#: which CVEs are fixed in it.
DISTRO_PREFIX = "/usr/lib/python3/dist-packages"

#: Package lists older than this make "pending updates" a lower bound only —
#: the periodic ``apt-get update`` is not running, which is itself the finding.
APT_LISTS_STALE_H = 7 * 24

#: Read versions from the interpreter that HOSTS the services (rnsd's own
#: python), not from whatever ``python3`` resolves to for the login shell.
#: calibrated_claims #7: verify the consumer of record. A box can carry more
#: python envs than a probe can see (user site, root dist-packages, pipx).
REMOTE_PYTHON = "/usr/bin/python3"

# ``md.version(name)`` returns the FIRST distribution of that name on sys.path,
# which is not necessarily the one ``import name`` resolves to (the manager box
# carried three cryptography dist-infos on 2026-09-05 — 43.0.3 and 46.0.3 in the
# SAME dist-packages dir plus 43.0.0 from apt — and this reporter said 43.0.3
# while the interpreter imported 46.0.3). Since 2026-09-06 the reporter ALSO
# lists every claimant of the name and the file the import actually resolves
# to, and the status line shows both whenever they disagree. The advisory query
# still runs against ``md.version``'s answer; a line carrying ``claimants:`` is
# the cue to clean the stale dist-info rather than trust either number alone.
_REMOTE_SRC = r"""
import glob, gzip, importlib.metadata as md, importlib.util, json, os, re, subprocess, time
PKGS = %(pkgs)r
IMPORT_NAMES = %(import_names)r
DISTRO_PREFIX = %(distro_prefix)r

def _run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout
    except Exception:
        return -1, ""

claimants = {}
try:
    for d in md.distributions():
        n = (d.metadata["Name"] or "").lower()
        if n:
            claimants.setdefault(n, set()).add(d.version)
except Exception:
    claimants = {}

packages = {}
for name in PKGS:
    rec = {"version": None, "origin": None, "claimants": [], "dpkg": None}
    try:
        rec["version"] = md.version(name)
    except Exception:
        packages[name] = rec            # not installed here -- absent, not vulnerable
        continue
    rec["claimants"] = sorted(claimants.get(name.lower(), set()))
    try:
        spec = importlib.util.find_spec(IMPORT_NAMES.get(name, name))
        rec["origin"] = spec.origin if spec else None
    except Exception:
        rec["origin"] = None
    if rec["origin"] and rec["origin"].startswith(DISTRO_PREFIX + "/"):
        rc, out = _run(["dpkg", "-S", rec["origin"]])
        deb = out.split(":", 1)[0].strip() if rc == 0 and ":" in out else None
        dpkg = {"package": deb, "version": None, "changelog_cves": None}
        if deb:
            rc2, ver = _run(["dpkg-query", "-W", "-f=${Version}", deb])
            if rc2 == 0 and ver.strip():
                dpkg["version"] = ver.strip()
            for path in glob.glob("/usr/share/doc/%%s/changelog.Debian.gz" %% deb):
                try:
                    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
                        dpkg["changelog_cves"] = sorted(set(
                            re.findall(r"CVE-\d{4}-\d{4,}", fh.read())))
                except Exception:
                    dpkg["changelog_cves"] = None   # unreadable != nothing fixed
        rec["dpkg"] = dpkg
    packages[name] = rec

apt = None
if os.path.exists("/usr/bin/dpkg-query"):
    apt = {"unattended_upgrades": None, "pending_total": None,
           "pending_security": None, "lists_age_h": None}
    rc, out = _run(["dpkg-query", "-W", "-f=${Status}", "unattended-upgrades"])
    apt["unattended_upgrades"] = (
        "installed" if rc == 0 and out.strip() == "install ok installed" else "absent")
    rc, out = _run(["apt-get", "-s", "upgrade"])
    if rc == 0:
        inst = [ln for ln in out.splitlines() if ln.startswith("Inst ")]
        apt["pending_total"] = len(inst)
        apt["pending_security"] = sum(1 for ln in inst if "security" in ln.lower())
    try:
        newest = max((os.path.getmtime(p) for p in glob.glob("/var/lib/apt/lists/*")),
                     default=None)
        if newest:
            apt["lists_age_h"] = int(max(0.0, time.time() - newest) / 3600)
    except Exception:
        pass
print(json.dumps({"packages": packages, "apt": apt}))
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


def read_accepted(path: str, today: Optional[_dt.date] = None):
    """{ghsa: (until_date, reason)} from the operator's accept list, plus a
    list of warnings for lines that could not count as acceptance.

    A line without ``until=`` is NOT an acceptance (warned, ignored): a
    decision with no expiry is the ``known_benign``-becomes-policy shape. A
    malformed date is treated the same way. Ignoring a bad line is the safe
    direction — the advisory stays a finding."""
    accepted: Dict[str, Tuple[_dt.date, str]] = {}
    warnings: List[str] = []
    if not os.path.isfile(path):
        return accepted, warnings
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read().splitlines()
    except OSError as exc:
        warnings.append("accept list %s unreadable: %s" % (path, exc))
        return accepted, warnings
    for n, line in enumerate(raw, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        ghsa = parts[0]
        until = None
        reason_bits = []
        for tok in parts[1:]:
            if tok.startswith("until="):
                try:
                    until = _dt.date.fromisoformat(tok[len("until="):])
                except ValueError:
                    until = None
            else:
                reason_bits.append(tok)
        if not ghsa.startswith("GHSA-") or until is None:
            warnings.append("accept list line %d ignored (needs 'GHSA-... until=YYYY-MM-DD reason'): %s"
                            % (n, s[:80]))
            continue
        accepted[ghsa] = (until, " ".join(reason_bits) or "no reason given")
    return accepted, warnings


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


def parse_report(data) -> Tuple[Dict[str, dict], Optional[dict]]:
    """Normalise a reporter payload to ({pkg: record}, apt|None).

    Accepts the pre-2026-09-06 shape too — ``{pkg: "version"|None}`` — so an
    older reporter (or a test written against it) still reads as a plain
    version with no origin knowledge, never as an error."""
    if not isinstance(data, dict):
        raise ValueError("report is not an object")
    if "packages" in data and isinstance(data["packages"], dict):
        pkgs = {}
        for name, rec in data["packages"].items():
            if isinstance(rec, dict):
                pkgs[name] = {"version": rec.get("version"),
                              "origin": rec.get("origin"),
                              "claimants": list(rec.get("claimants") or []),
                              "dpkg": rec.get("dpkg")}
            else:
                pkgs[name] = {"version": rec, "origin": None,
                              "claimants": [], "dpkg": None}
        apt = data.get("apt")
        return pkgs, apt if isinstance(apt, dict) else None
    return ({name: {"version": v, "origin": None, "claimants": [], "dpkg": None}
             for name, v in data.items()}, None)


def collect_installed(host: str, packages, timeout: int = 90):
    """({pkg: record}, apt|None, None) for one box, or (None, None, reason).
    A box we cannot reach is UNKNOWN — it is never folded in as though it
    were clean."""
    src = _REMOTE_SRC % {"pkgs": list(packages), "import_names": IMPORT_NAMES,
                         "distro_prefix": DISTRO_PREFIX}
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
        return None, None, "%s (rc=%s): %s" % (
            how, rc, (err or out).strip()[:120])
    try:
        pkgs, apt = parse_report(json.loads(out.strip().splitlines()[-1]))
        return pkgs, apt, None
    except (ValueError, IndexError) as exc:
        return None, None, "unparseable version report: %s" % exc


def canonical_name(name: str) -> str:
    """PEP 503: ``prometheus_client`` -> ``prometheus-client``. The advisory DB
    does NOT normalise (drilled 2026-09-06: ``affects=python_jose`` -> 0,
    ``affects=python-jose`` -> 4), so an underscore spelling would be told
    "no advisories" forever. Same helper as dep_range_check.py."""
    return re.sub(r"[-_.]+", "-", name).lower()


def query_advisories(package: str, version: str, timeout: int = 60):
    """(list_of_advisories, None) or (None, reason).

    ⚠️ The whole point of this function's signature: a FAILED query returns
    ``None``, never ``[]``. An empty list is a POSITIVE finding of no known
    advisories and may only come from a call that actually succeeded."""
    rc, out, err = _run(
        ["gh", "api", "/advisories?ecosystem=pip&affects=%s@%s&per_page=100"
         % (canonical_name(package), version)], timeout=timeout)
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


def partition_distro(advisories, dpkg: Optional[dict], accepted: Dict[str, Tuple[_dt.date, str]],
                     today: _dt.date):
    """Split one distro-managed package's advisories into
    (patched, accepted, expired, open).

    ``patched``  — CVE named in the distro changelog (the distro says fixed).
    ``accepted`` — on the operator's accept list, unexpired.
    ``expired``  — on the list but past ``until=``: back to open, and said so.
    ``open``     — everything else, INCLUDING every advisory when the
                   changelog is unreadable (None) and every advisory with no
                   CVE id at all (cannot be matched, so cannot be called fixed).
    """
    cves = None
    if isinstance(dpkg, dict) and isinstance(dpkg.get("changelog_cves"), list):
        cves = set(dpkg["changelog_cves"])
    patched, acc, expired, open_ = [], [], [], []
    for a in advisories:
        if not isinstance(a, dict):
            continue
        cve = a.get("cve_id")
        if cves is not None and cve and cve in cves:
            patched.append(a)
            continue
        ghsa = a.get("ghsa_id")
        if ghsa in accepted:
            until, _reason = accepted[ghsa]
            if until >= today:
                acc.append(a)
            else:
                expired.append(a)
            continue
        open_.append(a)
    return patched, acc, expired, open_


def apt_lines(host: str, apt: Optional[dict]):
    """(status_lines, finding_lines, unknown_key|None) for one box's apt
    hygiene. ``apt`` None = no dpkg on that box = inert, nothing to say."""
    if not isinstance(apt, dict):
        return [], [], None
    status, findings = [], []
    uu = apt.get("unattended_upgrades")
    age = apt.get("lists_age_h")
    age_s = ("lists %sh old" % age) if age is not None else "lists age unknown"
    if uu == "absent":
        msg = "unattended-upgrades ABSENT — security updates are not applied automatically"
        status.append("%-20s %-14s %-10s FINDING — %s" % (host, "apt", "-", msg))
        findings.append("%s apt: %s" % (host, msg))
    pend = apt.get("pending_security")
    if pend is None:
        status.append("%-20s %-14s %-10s UNKNOWN — apt-get -s upgrade failed; pending updates unobservable"
                      % (host, "apt", "-"))
        return status, findings, "%s/apt" % host
    stale = age is not None and age > APT_LISTS_STALE_H
    if stale:
        msg = ("package lists %sh stale — periodic apt-get update is not running; "
               "pending counts are a lower bound" % age)
        status.append("%-20s %-14s %-10s FINDING — %s" % (host, "apt", "-", msg))
        findings.append("%s apt: %s" % (host, msg))
    if pend > 0:
        msg = "%d security update(s) pending (%s)" % (pend, age_s)
        status.append("%-20s %-14s %-10s FINDING — %s" % (host, "apt", "-", msg))
        findings.append("%s apt: %s" % (host, msg))
    elif not stale and uu != "absent":
        status.append("%-20s %-14s %-10s clean — %d pending, 0 security (%s)"
                      % (host, "apt", "-", apt.get("pending_total") or 0, age_s))
    return status, findings, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hosts-file")
    ap.add_argument("--packages", nargs="*", default=list(DEFAULT_PACKAGES))
    ap.add_argument("--host", action="append", dest="only_hosts",
                    help="check just these host(s) instead of the fleet list")
    ap.add_argument("--accept-file",
                    help="operator accept list (default ~/.config/meshforge/dep_advisory_accepted)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    home = str(get_real_user_home())
    status_file = os.path.join(home, ".meshforge-dep-advisories")
    finding_file = os.path.join(home, ".meshforge-dep-ADVISORY")
    accept_file = args.accept_file or os.path.join(
        home, ".config", "meshforge", "dep_advisory_accepted")
    today = _dt.date.today()

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

    accepted, accept_warnings = read_accepted(accept_file, today)

    # One query per DISTINCT (pkg, version) across the whole fleet, not one per
    # box: nine boxes usually share a handful of versions, and the advisory API
    # is a shared external resource we should lean on lightly.
    cache: Dict[Tuple[str, str], Tuple[Optional[list], Optional[str]]] = {}
    lines: List[str] = []
    findings: List[str] = []
    observed = 0
    unknown_hosts: List[str] = []
    n_patched = n_accepted = 0

    for host in hosts:
        installed, apt, herr = collect_installed(host, args.packages)
        if installed is None:
            unknown_hosts.append(host)
            lines.append("%-20s UNKNOWN — %s" % (host, herr))
            continue
        observed += 1
        for pkg in args.packages:
            rec = installed.get(pkg) or {}
            ver = rec.get("version")
            if not ver:
                continue                      # absent by design → inert, not a finding
            key = (pkg, ver)
            if key not in cache:
                cache[key] = query_advisories(pkg, ver)
            advs, aerr = cache[key]
            dpkg = rec.get("dpkg")
            shown = ver
            if dpkg:
                shown = "%s [apt %s]" % (ver, (dpkg.get("version") or "?"))
            claim = [c for c in rec.get("claimants") or [] if c != ver]
            if claim:
                shown += " (claimants: %s)" % ", ".join(sorted(set(claim) | {ver}))
            if advs is None:
                lines.append("%-20s %-14s %-10s UNKNOWN — %s"
                             % (host, pkg, shown, aerr))
                unknown_hosts.append("%s/%s" % (host, pkg))
                continue
            if not advs:
                lines.append("%-20s %-14s %-10s clean" % (host, pkg, shown))
                continue
            if dpkg:
                patched, acc, expired, open_ = partition_distro(advs, dpkg, accepted, today)
                n_patched += len(patched)
                n_accepted += len(acc)
                bits = []
                if patched:
                    bits.append("distro-patched x%d" % len(patched))
                if acc:
                    bits.append("accepted x%d (%s)" % (len(acc), ", ".join(
                        "%s until %s: %s" % (a.get("ghsa_id"), accepted[a.get("ghsa_id")][0],
                                             accepted[a.get("ghsa_id")][1])
                        for a in acc)))
                if expired:
                    bits.append("acceptance EXPIRED x%d (%s)" % (len(expired), summarize(expired)))
                if dpkg.get("changelog_cves") is None:
                    bits.append("distro changelog UNREADABLE — nothing could be called patched")
                still = open_ + expired
                if still:
                    lines.append("%-20s %-14s %-10s ADVISORY x%d — %s; %s"
                                 % (host, pkg, shown, len(still), summarize(still),
                                    "; ".join(bits) or "apt-managed"))
                    findings.append(
                        "%s %s %s: %s — apt-managed (%s); fix via apt or the accept list, never pip"
                        % (host, pkg, shown, summarize(still), "; ".join(bits) or "no distro fix named"))
                else:
                    lines.append("%-20s %-14s %-10s %s" % (host, pkg, shown, "; ".join(bits)))
                continue
            lines.append("%-20s %-14s %-10s ADVISORY x%d — %s"
                         % (host, pkg, shown, len(advs), summarize(advs)))
            findings.append("%s %s %s: %s" % (host, pkg, shown, summarize(advs)))
        a_status, a_findings, a_unknown = apt_lines(host, apt)
        lines.extend(a_status)
        findings.extend(a_findings)
        if a_unknown:
            unknown_hosts.append(a_unknown)

    header = ["# fleet dependency advisories — installed versions vs the "
              "GitHub advisory DB", "# boxes observed: %d/%d%s"
              % (observed, len(hosts),
                 ("  UNKNOWN: " + ", ".join(unknown_hosts)) if unknown_hosts else ""),
              "# distro-patched (CVE named in the package's Debian changelog): %d; "
              "accepted via %s: %d" % (n_patched, accept_file, n_accepted)]
    header += ["# WARN %s" % w for w in accept_warnings]
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
