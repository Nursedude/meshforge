"""WITNESS-TUI leg [6/6] — the FULL state table (Fable r2 seam, 2026-11-21).

Two review rounds found blindness escaping one case at a time (a listed
interface vanishing, a partial -F listing judged complete, an UNKNOWN that
could print "filtered 0 B"). Instead of a third round of hand-picked cases,
this enumerates the input space and checks every row against an ORACLE that
derives the expected outcome from the INPUTS alone — never from the code:

  -F outcome  x  plain re-read outcome  x  shape of interface 1  x
  shape of interface 2  x  duplicate names

Each row drives the REAL read_interface_traffic() -> interface_traffic_leg()
-> summary_lines() through a fake subprocess.run.

Run: python3 -m pytest tests/test_rns_traffic_state_table.py -q
"""
import itertools
import os
import re
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

from utils.rns_status_parser import FilterState, read_interface_traffic  # noqa: E402
from handlers._rns_diagnostics_engine import (  # noqa: E402
    interface_traffic_leg, summary_lines,
)

# ------------------------------------------------------------------ shapes
# Each interface block shape: does it carry a readable Traffic (↑ and ↓), is
# it RX-only, does the -F listing carry a Filtered block, and its value.
SHAPES = {
    #       traffic   rx_only  filtered (None = no block)
    "TF0": ("full",   False,   "0"),
    "TF4": ("full",   False,   "491"),
    "T":   ("full",   False,   None),
    "F4":  (None,     False,   "491"),      # Filtered block, no Traffic
    "N":   (None,     False,   None),       # header + Status/Mode only
    "R0":  ("full",   True,    "0"),        # RX-only link
    "Tt":  ("tx",     False,   "0"),        # Traffic ↑ only, ↓ missing
    "Rr":  ("rx",     False,   "0"),        # Traffic ↓ read, ↑ unreadable
}

_TRAFFIC = {
    ("full", False): "    Traffic   : ↑478 B      0 bps\n                ↓254 B      0 bps\n",
    ("full", True):  "    Traffic   : ↑0 B        0 bps\n                ↓254 B      0 bps\n",
    ("tx", False):   "    Traffic   : ↑478 B      0 bps\n",
    ("rx", False):   "    Traffic   : ↑n/a\n                ↓254 B      0 bps\n",
}


def _block(name, shape, with_filtered):
    traffic, rx_only, filtered = SHAPES[shape]
    out = f" TCPInterface[{name}]\n    Status    : Up\n    Mode      : Full\n"
    out += "    Rate      : 10.00 Mbps, MTU 16384\n"
    if traffic:
        out += _TRAFFIC[(traffic, rx_only)]
    if with_filtered and filtered is not None:
        out += f"    Filtered  : ↑0 B  0 bps\n                ↓{filtered} B  0 bps\n"
    return out + "\n"


SHARED_F = ("\n Shared Instance[rns/default]\n    Status    : Up\n    Serving   : 0 programs\n"
            "    Traffic   : ↑0 B        0 bps\n                ↓900 B      0 bps\n"
            "    Filtered  : ↑0 B  0 bps\n                ↓0 B  0 bps\n\n")
SHARED_PLAIN = ("\n Shared Instance[rns/default]\n    Status    : Up\n    Serving   : 0 programs\n"
                "    Traffic   : ↑0 B        0 bps\n                ↓900 B      0 bps\n\n")
FOOTER_T_F = (" Totals       : ↑9 KB  0 bps\n                ↓9 KB  0 bps\n"
              " Filtered     : ↑0 B  0 bps\n                ↓8.00 KB  0 bps\n")
REJECT = ("usage: rnstatus [-h] [--config CONFIG] [--version] [-a] [-A] [-P] [-l] [-B]\n"
          "                [-t] [-s SORT] [-r] [-j] [-R hash] [-i path] [-w seconds] [-d]\n"
          "                [filter]\nrnstatus: error: unrecognized arguments: -F\n")
CRASH = ("Traceback (most recent call last):\n  File \"rnstatus.py\", line 783, in program_setup\n"
         "KeyError: 'frxb'\n")

F_OUTCOMES = ["ok", "footer", "rc1", "crash", "reject", "timeout", "noshared", "empty", "missing"]
PLAIN_OUTCOMES = ["ok", "noshared", "timeout"]


def _names(dup):
    return ("peer/a:1", "peer/a:1") if dup else ("peer/a:1", "peer/b:2")


def _listing(shapes, dup, with_filtered):
    n1, n2 = _names(dup)
    head = SHARED_F if with_filtered else SHARED_PLAIN
    return head + _block(n1, shapes[0], with_filtered) + _block(n2, shapes[1], with_filtered)


def _proc(stdout="", stderr="", rc=0):
    return MagicMock(stdout=stdout, stderr=stderr, returncode=rc)


def _fake_run(f_out, p_out, shapes, dup, calls):
    def run(cmd, **kw):
        calls.append(list(cmd))
        is_f = "-F" in cmd
        outcome = f_out if is_f else p_out
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout", 15))
        if outcome == "noshared":
            return _proc(stderr="Could not get shared instance\n", rc=1)
        if outcome == "empty":
            return _proc("", rc=0)
        if outcome == "reject":
            return _proc(stderr=REJECT, rc=2)
        listing = _listing(shapes, dup, with_filtered=is_f)
        if outcome == "footer":
            return _proc(listing + FOOTER_T_F)
        if outcome == "rc1":
            return _proc(listing, rc=1)
        if outcome == "crash":
            # stock 1.6.1 vs a pre-1.6 rnsd: first block printed, then KeyError
            return _proc(SHARED_F.split("    Filtered")[0], stderr=CRASH, rc=1)
        return _proc(listing)
    return run


# ------------------------------------------------------------------ oracle

def _oracle(f_out, p_out, shapes):
    """Expected outcome, from the INPUTS only."""
    if f_out == "missing":
        state, calls, view = FilterState.UNKNOWN, 0, None
    elif f_out == "timeout":
        state, calls, view = FilterState.UNKNOWN, 1, None
    elif f_out in ("ok", "footer"):
        state, calls, view = FilterState.COUNTED, 1, "F"
    elif f_out == "reject":
        state, calls, view = FilterState.NO_FILTER, 2, ("P" if p_out == "ok" else None)
    else:  # rc1, crash, noshared, empty: UNKNOWN, accepted re-read plain
        state, calls, view = FilterState.UNKNOWN, 2, ("P" if p_out == "ok" else None)

    rows = []          # (shape) of every interface that must get a row, in order
    unread = 0
    for sh in shapes:
        traffic, rx_only, filtered = SHAPES[sh]
        if view is None:
            continue
        if traffic == "full":
            rows.append(sh)
        else:
            unread += 1
    filtered_blind = state is FilterState.UNKNOWN or (
        state is FilterState.COUNTED and any(SHAPES[sh][2] is None for sh in rows))
    accepted_blind = view is None or unread > 0
    issues = sum(1 for sh in rows if SHAPES[sh][1])
    warn = state is FilterState.COUNTED and any(SHAPES[sh][2] == "491" for sh in rows)
    return dict(state=state, calls=calls, rows=rows, filtered_blind=filtered_blind,
                accepted_blind=accepted_blind, issues=issues, warn=warn)


CASES = list(itertools.product(F_OUTCOMES, PLAIN_OUTCOMES,
                               itertools.product(SHAPES, SHAPES), (False, True)))


@pytest.mark.parametrize("f_out, p_out, shapes, dup", CASES,
                         ids=[f"{a}-{b}-{s[0]}+{s[1]}-{'dup' if d else 'uniq'}"
                              for a, b, s, d in CASES])
def test_state_table(f_out, p_out, shapes, dup):
    exp = _oracle(f_out, p_out, shapes)
    calls = []
    binary = None if f_out == "missing" else "/usr/bin/rnstatus"
    with patch('utils.rns_status_parser._find_rnstatus_binary', return_value=binary), \
         patch('subprocess.run', side_effect=_fake_run(f_out, p_out, shapes, dup, calls)):
        t = read_interface_traffic()
    leg = interface_traffic_leg(t, running=True, instance_ok=f_out != "noshared",
                                rnstatus_present=binary is not None)
    text = "\n".join(leg.lines)

    assert t.filter_state is exp["state"]
    assert len(calls) == exp["calls"], calls

    # Rows: exactly the interfaces whose traffic was read, in order, never collapsed.
    row_lines = [l for l in leg.lines if l.startswith("  TCPInterface[")]
    assert len(row_lines) == len(exp["rows"]), (text, exp["rows"])
    verdict_lines = [l.strip() for l in leg.lines if l.startswith("      rx accepted")]
    assert len(verdict_lines) == len(exp["rows"])
    for sh, vl in zip(exp["rows"], verdict_lines):
        filtered = SHAPES[sh][2]
        if exp["state"] is FilterState.COUNTED and filtered is not None:
            assert f"filtered {filtered} B" in vl, vl
        else:
            # never a filtered number it did not read (r2 M35)
            assert not re.search(r"filtered \d", vl), vl
        if exp["state"] is FilterState.NO_FILTER:
            assert "this rnstatus has no -F" in vl

    # Footer totals never land on an interface (r2 #4); Shared Instance never a row.
    assert "8.00 KB" not in text
    assert "Shared Instance[" not in text

    # Blindness: present iff the oracle says something was not seen.
    filt = [u for u in leg.unobserved if u.startswith("filtered rx")]
    acc = [u for u in leg.unobserved if u.startswith("interface traffic")]
    assert bool(filt) == exp["filtered_blind"], (leg.unobserved, exp)
    assert bool(acc) == exp["accepted_blind"], (leg.unobserved, exp)
    assert set(leg.unobserved) == set(filt + acc)

    assert len(leg.issues) == exp["issues"]
    assert bool(leg.warnings) == exp["warn"]

    summary = "\n".join(summary_lines(leg.issues, leg.warnings, leg.unobserved))
    clean = not (leg.issues or leg.warnings or leg.unobserved)
    assert ("All checks passed" in summary) == clean


def test_table_size_is_what_it_claims():
    # the table above must stay the FULL product — a shrunk table pins less
    assert len(CASES) == len(F_OUTCOMES) * len(PLAIN_OUTCOMES) * len(SHAPES) ** 2 * 2


# ------------------------------------- the wiring into run_rns_diagnostics

def test_apply_traffic_leg_prints_every_line_and_folds_every_entry():
    from handlers._rns_diagnostics_engine import TrafficLeg, apply_traffic_leg
    leg = TrafficLeg(lines=["a", "b"], issues=["i"], warnings=["w"], unobserved=["u"])
    issues, warnings, unobserved, printed = ["i0"], ["w0"], ["u0"], []
    apply_traffic_leg(leg, issues, warnings, unobserved, emit=printed.append)
    assert printed == ["a", "b"]
    assert issues == ["i0", "i"] and warnings == ["w0", "w"] and unobserved == ["u0", "u"]


def test_summary_with_only_warnings_is_not_all_passed():
    s = "\n".join(summary_lines([], ["w"], []))
    assert "All checks passed" not in s
    assert "with warnings" in s
