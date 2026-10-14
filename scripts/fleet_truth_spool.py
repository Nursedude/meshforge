#!/usr/bin/env python3
"""fleet_truth_spool — ssh-fetch fan-out-unreachable boxes into the truth spool.

Some fleet boxes are unreachable to the /api/fleet/truth HTTP fan-out BY
NETWORK DESIGN: kiai sits behind the site NAT (ssh rides a reverse tunnel,
direct :5000 is filtered), and moc3 runs no map service at all. This cron —
run in the OPERATOR context, where the fleet ssh keys live (the /fleet/dups
precedent: the sandboxed map service never sshes) — reads each configured
box's box_truth/v1 document over ssh and writes one freshness-stamped spool
file per box. The
collector (utils.fleet_truth_collector) reads the spool ONLY when the direct
fetch failed and only while fresh (SPOOL_STALE_S) — a dead cron makes the
boxes read dark again, never last-known-healthy, and the cron_verdict wiring
(#78) pages the dead cron itself.

The document (2026-10-14, NOC read grant): the box runs ITS OWN export
(``scripts/box_truth_export.py``, ``utils.box_truth_collect``) — the inline
shell string this cron used to send is gone, because a restricted grant can
only run one fixed program. Every read goes through ``utils.box_truth_reader``:
identity checked against the registry's ``expect_hostkey`` on every read (S2),
bounded (S8), and ending in ONE named outcome (S1) written to the spool as
``transport`` so the NOC can say "grant refused" instead of "no response".

Targets file: ~/.config/meshforge/truth_spool_targets — ``<alias> [noc]`` per
line, '#' comments (``utils.box_truth_center``, shared with the grant CLI that
writes it). ``noc`` = read with the pinned grant key only (S3). A line WITHOUT it is
not read at all: its spool row says ``refused_grant`` and names the push
that grants it (the interactive-key read was deleted 2026-10-14 once every
target was pinned — S12 complete). Absent/empty file = nothing to do
(exit 0): boxes are OPTED IN here, this never guesses.

Exit codes: 0 = ran and wrote a spool file for every target (an unreachable
target still gets an empty-handed spool file — the truth layer surfaces the
darkness; box-down paging belongs to fleet_offline_check, not this organ);
1 = infrastructural failure (no ssh, spool dir unwritable, targets
unreadable).

Crontab (manager box):
    */2 * * * * /usr/bin/python3 /opt/meshforge/scripts/fleet_truth_spool.py \
        >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh truth_spool $?
"""

import json
import os
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from typing import Optional  # noqa: E402

from utils import box_truth as bt  # noqa: E402
from utils import box_truth_reader as br  # noqa: E402
from utils.box_truth_center import read_targets, targets_path  # noqa: E402
from utils.fleet_truth_collector import SPOOL_SCHEMA, truth_spool_dir  # noqa: E402
from utils.paths import atomic_write_text, get_real_user_home  # noqa: E402

#: The ten box_truth/v1 sections, carried into the spool doc under the same
#: keys the collector has always read (one allowlist: utils.box_truth).
_SECTION_NAMES = bt.SECTIONS


#: Service whose absence removes a box's whole HTTP truth surface
#: (/api/status + /fleet/slo are both served by it).
_HTTP_SURFACE_SERVICE = "meshforge-map"
#: Service whose absence removes the box's LOCAL probe coverage. Judged
#: separately from the HTTP surface: the spool reads watchdog.json as a raw
#: FILE, so a map-less box that still runs a watchdog keeps owing us that
#: signal (moc3). Only a role that declares the unit absent/disabled is
#: exempt — and then the cell is `absent` (the organ is not here), never
#: `accepted_blind` (which claims it may be running unseen).
_WATCHDOG_SERVICE = "meshforge-watchdog"
#: Role-catalog service states that mean "this box is not supposed to run it".
_NOT_EXPECTED = ("disabled", "absent")


def _unit_expected(deployment_raw, unit: str) -> "Optional[bool]":
    """Does this box's DECLARED role expect ``unit`` to be running?

    ``True`` / ``False`` when the box's own ``deployment.json`` names a role
    the catalog knows AND that role says something about ``unit``; ``None``
    when we cannot tell — no declaration, unknown role, unloadable catalog,
    or a role that is simply silent about this unit.

    ``None`` is load-bearing and must never collapse to ``False``: the NOC
    treats "not expected" as a gap that stops tainting the fleet verdict, so
    guessing it would silence a genuinely broken box. An undeclared box keeps
    darkening the verdict, which is the honest default (honest_failure_modes
    #2 — unobservable is not permission to look away).
    """
    if not isinstance(deployment_raw, dict):
        return None
    role = deployment_raw.get("role")
    if not isinstance(role, str) or not role:
        return None
    try:
        import importlib.util
        script = Path(__file__).resolve().parent / "provision_role.py"
        spec = importlib.util.spec_from_file_location("provision_role", script)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        # Register BEFORE exec: py3.12+ @dataclass resolves annotations via
        # sys.modules[cls.__module__], so an unregistered module raises
        # AttributeError mid-import and this whole check silently degrades to
        # "undecidable". Same pattern (and same comment) as
        # watchdog_probes_drift._plan_role_actions — the repo already paid for
        # this once; a test caught it the second time.
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        role_def = mod.resolve_role(mod.load_roles(mod.DEFAULT_ROLES_FILE), role)
    except Exception:
        return None            # catalog/tooling unavailable → indeterminate
    services = role_def.get("services")
    if not isinstance(services, dict):
        return None
    state = services.get(unit)
    if not isinstance(state, str):
        # The role catalog says nothing about this unit for this role. Silence
        # is not a declaration — stay indeterminate rather than inventing one.
        return None
    return state.strip().lower() not in _NOT_EXPECTED


def http_surface_expected(deployment_raw) -> "Optional[bool]":
    """Does the declared role expect /api/status + /fleet/slo to exist?"""
    return _unit_expected(deployment_raw, _HTTP_SURFACE_SERVICE)


def watchdog_expected(deployment_raw) -> "Optional[bool]":
    """Does the declared role expect a LOCAL watchdog on this box?

    2026-08-31. Without this, a box whose role legitimately runs no watchdog
    (the zero-class `field-node` tier — ~46 MB RSS does not fit a 512MB-class
    board) darkened the fleet verdict FOREVER, because `watchdog` is a
    core-observability subsystem and is deliberately NOT part of the
    HTTP-surface exemption. A permanently-dark verdict is not a safe default:
    it trains the reader to ignore `dark`, which costs more than the gap it
    was flagging.

    The distinction this restores is the domain's oldest one — `inert` (the
    organ is not here, by declaration) is not `indeterminate` (we should be
    able to see it and cannot). Only the second is a finding.

    ⚠️ This does NOT make such a box unwatched: reachability always taints,
    and `mini`, `services` and `radio` are still observed through the ssh
    spool and still taint when they fail. What is given up is the box's LOCAL
    probe coverage, which is exactly what the role declaration says it gave up.
    """
    return _unit_expected(deployment_raw, _WATCHDOG_SERVICE)


def sections_from_doc(doc: "Optional[dict]") -> dict:
    """box_truth/v1 sections -> the spool's section keys. Every key present;
    a non-object section is None (unobservable), never a guess."""
    raw = (doc or {}).get("sections")
    raw = raw if isinstance(raw, dict) else {}
    return {k: (raw.get(k) if isinstance(raw.get(k), dict) else None)
            for k in _SECTION_NAMES}


def _expect_of(alias: str) -> "tuple[Optional[str], Optional[str]]":
    """(expect_hostkey, why-not). An unreadable registry is named as such —
    never "stamp it", which is the wrong fix (review B 6)."""
    from utils.fleet_naming import load_registry_quiet, registry_path
    reg = load_registry_quiet()
    if reg is None:
        return None, (f"fleet_naming registry unreadable ({registry_path()}) — "
                      "fix it: scripts/fleet_naming_audit.py")
    host = reg.hosts.get(alias)
    return (host.expect_hostkey if host else None), None


def fetch_box(target, *, home: Path, expect_of=None,
              read=None) -> "tuple[dict, dict, dict]":
    """One bounded, identity-checked read -> (sections, transport, meta).
    A pinned target (``noc`` in the targets file) reads with the grant key
    only (S3); any failure -> all-None sections + the outcome that says
    which failure it was (S1). An UNPINNED target is never read: its row
    is ``refused_grant`` with the push that would grant it, so the NOC
    shows DARK-with-the-fix instead of a silently missing box."""
    expect_of = expect_of or _expect_of
    read = read or br.read_box
    if not target.pinned:
        res = br.ReadResult(
            alias=target.alias, outcome="refused_grant", pinned=False,
            stderr_head=(f"{target.alias} is not pinned in {targets_path(home)} — "
                         f"grant it from this center: box_truth_grant.py push "
                         f"{target.alias} --confirm {target.alias}"))
        transport = res.transport()
        transport["reason"] = res.stderr_head
        return sections_from_doc(None), transport, {}
    key_path = br.noc_key_path(home)
    expect, why = expect_of(target.alias)
    if key_path is not None and not key_path.exists():
        res = br.ReadResult(alias=target.alias, outcome="refused_grant", pinned=True,
                            stderr_head=f"center key {key_path} missing — "
                                        "box_truth_grant.py center-key --create")
    elif key_path is not None and not os.access(key_path, os.R_OK):
        res = br.ReadResult(alias=target.alias, outcome="refused_grant", pinned=True,
                            stderr_head=f"center key {key_path} unreadable by this user "
                                        "(created under sudo?) — chown it to the operator")
    elif expect is None and why:
        res = br.ReadResult(alias=target.alias, outcome="identity_unpinned",
                            pinned=key_path is not None, stderr_head=why)
    else:
        res = read(target.alias, expect, key_path=key_path)
    transport = res.transport()
    transport["reason"] = None if res.outcome == "ok" else br.describe(res)
    meta = {}
    if res.doc:
        meta = {k: res.doc.get(k) for k in ("schema", "app", "app_version", "host",
                                             "section_errors", "tripwire", "witness")}
    return sections_from_doc(res.doc), transport, meta


def main() -> int:
    home = get_real_user_home()
    targets = read_targets(targets_path(home))
    if not targets:
        print("truth_spool: no targets configured — nothing to do")
        return 0

    try:
        spool_dir = truth_spool_dir()
        spool_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"truth_spool: FATAL spool dir unwritable: {exc}", file=sys.stderr)
        return 1

    failures = 0
    for target in targets:
        sections, transport, meta = fetch_box(target, home=home)
        extra = {"transport": transport, "box_truth": meta}
        doc = {
            "schema": SPOOL_SCHEMA,
            "alias": target.alias,
            "fetched_at": time.time(),
            **sections,
            **extra,
        }
        # Resolved HERE, not at the NOC's render time: the role catalog and
        # the box's declaration are both in hand at this moment, and the
        # consumer should read a decided boolean rather than re-derive it.
        # None (undecidable) is written through as null, never as False.
        doc["http_surface_expected"] = http_surface_expected(
            sections.get("deployment"))
        doc["watchdog_expected"] = watchdog_expected(sections.get("deployment"))
        got = [k for k, v in sections.items() if v is not None]
        try:
            atomic_write_text(spool_dir / f"{target.alias}.json",
                              json.dumps(doc, separators=(",", ":")) + "\n")
        except OSError as exc:
            print(f"truth_spool: FATAL write failed for {target.alias}: {exc}",
                  file=sys.stderr)
            failures += 1
            continue
        how = "pinned" if target.pinned else "unpinned"
        outcome = transport["outcome"]
        print(f"truth_spool: {target.alias} [{how}] {outcome}: "
              f"{'+'.join(got) if got else 'empty-handed'}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
