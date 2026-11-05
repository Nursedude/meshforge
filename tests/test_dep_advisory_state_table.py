"""The whole input space of ``dep_advisory_check.apt_lines``, judged against
ONE independently written spec.

Why this file exists (2026-11-05): six review rounds of the apt leg found
9-10 defects EACH, because every fix was reasoned against one finding, never
against every input — r5 fixed one case by reversing r4's (r6 #3), and 17
fixtures in the main test file carried the r6 #6 shape (no stamp field) while
asserting "clean". This file enumerates every combination of

    unattended-upgrades × pending × update-stamp × security views (0–2)

and compares the rendered verdict with ``spec()`` below. ``spec()`` is the
decision record: read it top to bottom to know what any box will say. A
mismatch prints EVERY disagreeing row at once, so a class is seen as a class.

DECIDED rows (operator calls, marked ``DECIDED`` in spec()) say who decided
and when — changing one is a new decision, not a refactor.
"""

import importlib.util
import itertools
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
_s = importlib.util.spec_from_file_location(
    "dep_advisory_check_table", os.path.join(_ROOT, "scripts", "dep_advisory_check.py"))
dac = importlib.util.module_from_spec(_s)
_s.loader.exec_module(dac)

ARCHIVE_TS = 1793828633.0
WINDOW_H = dac.APT_SECURITY_VIEW_FRESH_H

# ── the input dimensions ────────────────────────────────────────────────────
UU = ("installed", "absent")
PENDING = (0, 3, None)                      # None = apt-get -s failed
STAMP = {                                   # what the reporter sends
    "fresh":      {"update_stamp_present": True, "update_stamp_age_h": 3},
    "stale":      {"update_stamp_present": True,
                   "update_stamp_age_h": dac.APT_LISTS_STALE_H + 24},
    "absent":     {"update_stamp_present": False, "update_stamp_age_h": None},
    "unreadable": {"update_stamp_present": True, "update_stamp_age_h": None},
    "missing":    {},                        # an older / malformed report
}
#: One view's state. "mgr" = the manager cannot read that view's archive.
VIEW = ("fresh", "past", "date", "noref", "mgr")
VIEWSETS = ([None, ()]                       # None = reporter error; () = no suite
            + [(a,) for a in VIEW]
            + list(itertools.product(VIEW, repeat=2)))


def _file(i, state):
    host = "deb.example.org" if state == "noref" else "deb.debian.org"
    return "%s_debian-security_dists_v%d%s-security_InRelease" % (host, i, state)


def _view(i, state):
    lag_h = {"fresh": 2, "past": WINDOW_H + 16}.get(state, 2)
    return {"file": _file(i, state), "suite": "stable-security",
            "date_ts": None if state == "date" else ARCHIVE_TS - lag_h * 3600}


def _reference(url):
    if "mgr-security" in url:
        return None, "archive unreachable: timeout"
    return ARCHIVE_TS, None


def _apt(uu, pending, stamp, views):
    apt = {"unattended_upgrades": uu, "pending_total": pending,
           "pending_security": pending, "lists_age_h": 2}
    apt.update(STAMP[stamp])
    if views is None:
        apt["security_views"] = None
        apt["security_views_error"] = "reporter: PermissionError"
    else:
        apt["security_views"] = [_view(i, s) for i, s in enumerate(views)]
    return apt


# ── the spec: what a box MUST say, written without reading apt_lines ───────
def spec(uu, pending, stamp, views):
    """→ (findings, unknown, line). findings = frozenset of kinds;
    unknown = the UNKNOWN-list tag suffix or None; line = None | "clean" |
    ("lower-bound", cause, lag_shown, reasons_needed)."""
    f = set()
    if uu == "absent":
        f.add("uu_absent")
    if pending is None:                     # nothing below is observable
        return frozenset(f), "apt", None
    unknown = None
    if stamp == "stale":
        f.add("stale")
    elif stamp == "absent":
        # DECIDED r6 #2a (operator 2026-11-05): an absent stamp stays a
        # finding even beside fresh lists — apt.systemd.daily, which runs
        # unattended-upgrades, never recorded a success.
        f.add("never")
    elif stamp in ("unreadable", "missing"):
        unknown = "apt-update-stamp"        # r6 #1 / #6
    if views == ():
        f.add("no_suite")
    if pending > 0:
        f.add("pending")
        return frozenset(f), unknown, None
    # DECIDED r6 #2b (operator 2026-11-05): the lag line renders beside ANY
    # other finding — it is an independent measurement, and a winning reason
    # never erases a second defect (the r6 #3 class). Only a missing suite
    # has no view to judge.
    if views == ():
        return frozenset(f), unknown, None
    if views is None:
        return frozenset(f), unknown, ("lower-bound", "box", False, ("reporter",))
    past = [s for s in views if s == "past"]
    box = [(i, s) for i, s in enumerate(views) if s in ("date", "noref")]
    mgr = [s for s in views if s == "mgr"]
    reasons = tuple(_file(i, s) for i, s in box) + (("archive unreachable",) if mgr else ())
    if past:
        return frozenset(f), unknown, ("lower-bound", "box", True, reasons)
    if box:
        return frozenset(f), unknown, ("lower-bound", "box", False, reasons)
    if mgr:
        return frozenset(f), unknown, ("lower-bound", "manager", False, reasons)
    return frozenset(f), unknown, "clean"


# ── reading what apt_lines actually said ───────────────────────────────────
#: Matched on the text right after "boxA apt: " — the absent-stamp message
#: also contains "no successful periodic", so a substring match is ambiguous.
_FINDING_KIND = (("unattended-upgrades ABSENT", "uu_absent"),
                 ("no successful periodic", "stale"),
                 ("no update-stamp", "never"), ("no *-security suite", "no_suite"),
                 ("security update(s) pending", "pending"))


def observe(uu, pending, stamp, views):
    a = dac.apt_lines("boxA", _apt(uu, pending, stamp, views), _reference)
    kinds = set()
    for fnd in a.findings:
        body = fnd.split(" apt: ", 1)[1]
        hit = [k for key, k in _FINDING_KIND if body.startswith(key)
               or (key == "security update(s) pending" and key in body)]
        assert len(hit) == 1, "unclassifiable finding: %r" % fnd
        kinds.add(hit[0])
    unknown = a.unknown.split("/", 1)[1] if a.unknown else None
    sec = [s for s in a.status if " clean — " in s or " lower-bound — " in s]
    assert len(sec) <= 1, sec
    if not sec:
        line = None
    elif " clean — " in sec[0]:
        line = "clean"
    else:
        line = ("lower-bound", a.lower_bound_cause, sec[0])
    return frozenset(kinds), unknown, line, a


def _matches(exp_line, got_line, a):
    if exp_line is None or exp_line == "clean":
        return exp_line == got_line and (a.lower_bound_cause is None)
    if not isinstance(got_line, tuple):
        return False
    _, cause, lag_shown, reasons = exp_line
    text = got_line[2]
    if got_line[1] != cause:
        return False
    if lag_shown != ("%dh behind the archive" % (WINDOW_H + 16) in text):
        return False
    return all(r in text for r in reasons)


def all_rows():
    return list(itertools.product(UU, PENDING, STAMP, VIEWSETS))


def mismatches():
    bad = []
    for row in all_rows():
        exp_f, exp_u, exp_line = spec(*row)
        got_f, got_u, got_line, a = observe(*row)
        if exp_f != got_f or exp_u != got_u or not _matches(exp_line, got_line, a):
            bad.append((row, (sorted(exp_f), exp_u, exp_line),
                        (sorted(got_f), got_u, got_line if not isinstance(got_line, tuple)
                         else got_line[:2] + (got_line[2][40:],))))
    return bad


def test_the_table_covers_the_whole_space():
    assert len(all_rows()) == len(UU) * len(PENDING) * len(STAMP) * len(VIEWSETS) == 960


def test_every_row_matches_the_spec():
    bad = mismatches()
    report = "\n".join("%s\n   spec: %s\n   got:  %s" % b for b in bad[:40])
    assert not bad, "%d of %d rows disagree with spec():\n%s" % (
        len(bad), len(all_rows()), report)


if __name__ == "__main__":                  # the table, readable: one line per class
    from collections import Counter
    c = Counter()
    for row in all_rows():
        f, u, line = spec(*row)
        key = (",".join(sorted(f)) or "-", u or "-",
               line if not isinstance(line, tuple) else "lower-bound/" + line[1])
        c[key] += 1
    print("%-36s %-18s %-22s %s" % ("findings", "unknown", "security line", "rows"))
    for (f, u, line), n in sorted(c.items()):
        print("%-36s %-18s %-22s %d" % (f, u, str(line), n))
