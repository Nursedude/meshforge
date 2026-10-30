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

Targets file: ~/.config/<app>/truth_spool_targets — ``<alias> [noc]`` per
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
    */2 * * * * /usr/bin/python3 <INSTALL_DIR>/scripts/fleet_truth_spool.py \
        >/dev/null 2>&1; <INSTALL_DIR>/scripts/cron_verdict.sh truth_spool $?  (installed by the grant CLI: box_truth_center.SPOOL_CRON_LINE)
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
from utils import box_truth_app as app  # noqa: E402
from utils.box_truth_center import (  # noqa: E402
    SPOOL_SCHEMA, read_targets, targets_path, truth_spool_dir)
from utils.paths import atomic_write_text, get_real_user_home  # noqa: E402

#: The ten box_truth/v1 sections, carried into the spool doc under the same
#: keys the collector has always read (one allowlist: utils.box_truth).
_SECTION_NAMES = bt.SECTIONS


def expected_from_role(role_section) -> "tuple[Optional[bool], Optional[bool]]":
    """(http_surface_expected, watchdog_expected) as the BOX judged them.

    Session 3 step 2 (2026-10-14): the export's ``role`` section carries the
    target's own verdict, resolved on the target through ITS provisioner and
    ITS catalog. This center copies it through and judges nothing — so a
    center of either app reads a box of either app with no catalog crossing
    the wire. None when the export predates the section, the box could not
    decide (its ``why`` says what), or a value is not a bool: never coerced,
    never False by default (False is what stops a dark box tainting the
    fleet verdict)."""
    exp = role_section.get("expected") if isinstance(role_section, dict) else None
    if not isinstance(exp, dict):
        return None, None

    def tri(v):
        return v if isinstance(v, bool) else None
    return tri(exp.get("http_surface")), tri(exp.get("watchdog"))


def sections_from_doc(doc: "Optional[dict]") -> dict:
    """box_truth/v1 sections -> the spool's section keys. Every key present;
    a non-object section is None (unobservable), never a guess."""
    raw = (doc or {}).get("sections")
    raw = raw if isinstance(raw, dict) else {}
    return {k: (raw.get(k) if isinstance(raw.get(k), dict) else None)
            for k in _SECTION_NAMES}


def _expect_of(alias: str) -> "tuple[Optional[str], Optional[str]]":
    """(expect_hostkey, why-not) from THIS app's identity source (the seam:
    MeshForge's fleet_naming registry, MeshAnchor's fleet.json ``ssh``)."""
    return app.expect_hostkey_of(alias)


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
            stderr_head=(f"{target.alias} has no grant flag (noc) in {targets_path(home)} — "
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
        # Decided ON THE BOX (its export's `role` section) and copied through
        # as the two fields the collector has always read. None (undecidable
        # or an older export) is written as null, never as False.
        doc["http_surface_expected"], doc["watchdog_expected"] = expected_from_role(
            sections.get("role"))
        got = [k for k, v in sections.items() if v is not None]
        try:
            atomic_write_text(spool_dir / f"{target.alias}.json",
                              json.dumps(doc, separators=(",", ":")) + "\n")
        except OSError as exc:
            print(f"truth_spool: FATAL write failed for {target.alias}: {exc}",
                  file=sys.stderr)
            failures += 1
            continue
        # GRANT flag (`noc`), not the host-key pin — "pinned" means that elsewhere.
        how = "granted" if target.pinned else "no grant"
        outcome = transport["outcome"]
        print(f"truth_spool: {target.alias} [{how}] {outcome}: "
              f"{'+'.join(got) if got else 'empty-handed'}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
