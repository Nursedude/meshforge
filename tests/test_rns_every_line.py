"""WITNESS-TUI r6 — every rnstatus line accounted for, red-first.

Five Fable rounds, five holes, one cause: ``parse_rnstatus`` ignored by
default. A line it did not recognise was skipped and leg [6/6] reported on
the rest, so "All checks passed" meant "everything I recognised looked fine"
(r5: ``RNode-Multi[High Datarate]`` — a stock RNodeSubInterface name the
header regex could not read — vanished with its counters).

The cure is at the parser boundary (hfm #3): every line is classified, and
anything unrecognised lands in ``RNSStatus.unread_lines``; the reader's ONE
completeness judgement (``accepted_gap``) turns any unread line into a
blind spot.

The inputs here are NOT text I wrote: the corpora in ``tests/fixtures/
rnstatus_stock_*.json`` were printed by the real stock ``program_setup`` of
rns 1.3.9 (this fleet) and 1.6.1 (meshforge-lab stock-witness venv) from
random interface stats — see ``tests/rnstatus_stock_driver.py``.

Run: python3 -m pytest tests/test_rns_every_line.py -v
"""
import json
import os
import random
import sys
from unittest.mock import MagicMock, patch

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
sys.path.insert(0, os.path.join(HERE, '..', 'src', 'launcher_tui'))
sys.path.insert(0, HERE)

from utils.rns_status_parser import (  # noqa: E402
    FilterState, InterfaceStatus, InterfaceTraffic, accepted_gap, parse_rnstatus,
    read_interface_traffic,
)

CORPORA = ["rnstatus_stock_139.json", "rnstatus_stock_161.json"]


def _load(name):
    with open(os.path.join(HERE, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


def _cases():
    for name in CORPORA:
        for c in _load(name)["cases"]:
            yield pytest.param(c, id=f"{name[15:18]}-{c['case']}-{'F' if c['fstats'] else 'plain'}")


def _matches_truth(st, truth):
    """True when the parse is EXACTLY the truth stock printed from."""
    got = [(i.full_name, i.tx.text if i.tx.observed else None,
            i.rx.text if i.rx.observed else None,
            i.rx_filtered.text if i.rx_filtered is not None else None,
            i.status is InterfaceStatus.UP) for i in st.interfaces]
    want = [(t["name"], t["tx"], t["rx"], t["rx_filtered"], t["up"]) for t in truth]
    return got == want


def _leg(t):
    from handlers._rns_diagnostics_engine import interface_traffic_leg
    return interface_traffic_leg(t, running=True, instance_ok=True, rnstatus_present=True)


def _traffic(st, fstats):
    return InterfaceTraffic(st, FilterState.COUNTED if fstats else FilterState.NO_FILTER)


# ------------------------------------------------- the corpora (stock text)

def test_corpora_present_and_cover_both_versions():
    srcs = [_load(n)["source"] for n in CORPORA]
    assert "1.3.9" in srcs[0] and "1.6.1" in srcs[1]
    c161 = _load(CORPORA[1])["cases"]
    assert any(c["fstats"] for c in c161) and any("Filtered" in c["output"] for c in c161)
    # the r5 shapes are in the stock text, not only in a hand fixture
    allout = "".join(c["output"] for n in CORPORA for c in _load(n)["cases"])
    assert "[High Datarate]\n" in allout and "\n WDCL over /dev/ttyUSB" in allout


@pytest.mark.parametrize("case", list(_cases()))
def test_stock_output_every_line_read_and_exact(case):
    st = parse_rnstatus(case["output"])
    assert st.unread_lines == [], st.unread_lines[:3]
    assert _matches_truth(st, case["truth"])
    assert accepted_gap(st) is None


@pytest.mark.parametrize("case", list(_cases()))
def test_stock_output_leg_names_every_shown_interface(case):
    leg = _leg(_traffic(parse_rnstatus(case["output"]), case["fstats"]))
    text = "\n".join(leg.lines)
    for t in case["truth"]:
        assert f"  {t['name']}: " in text
    assert not any("interface traffic" in u for u in leg.unobserved)


def test_live_stock_driver_fresh_cases():
    """Fresh random cases through the INSTALLED stock rnstatus (skips where
    RNS is not importable, e.g. CI's minimal profile — the corpora cover it)."""
    pytest.importorskip("RNS.Utilities.rnstatus")
    import rnstatus_stock_driver as d
    rng = random.Random(os.environ.get("RNSTATUS_FUZZ_SEED", "r6"))
    fs = [False, True] if d.has_fstats() else [False]
    for _ in range(200):
        stats = d.random_stats(rng)
        for flag in fs:
            out = d.render(stats, fstats=flag)
            st = parse_rnstatus(out)
            assert st.unread_lines == [], (out, st.unread_lines)
            assert _matches_truth(st, d.truth(stats, fstats=flag)), out


# --------------------------------------- the property: never complete-but-wrong

GARBAGE = [
    "    Weird     : 5 widgets",
    " Something rns 2.0 prints",
    "                ↓5 B",
    "                ↑7 KB  0 bps",
    "Traceback (most recent call last):",
    "    Traffic   : ↑?? B",
    "    Traffic   : nonsense",
    "    Status    : Sideways",
    "  orphan indented text",
    " RNode-Multi[Low Datarate]",
    "\t    Traffic   : ↑1 B",
    "    Filtered  : ↑1 B  0 bps",
    "garbage at column zero",
    "    : no name",
    " Totals       : ↑1 B  0 bps",
]


def test_injected_garbage_is_blind_or_harmless():
    """Insert one garbage line anywhere in stock text. Judged at the END
    (the leg's verdict, not the parser's internals): the leg is BLIND
    (unobserved non-empty — never "All checks passed"), or the parse is
    still EXACTLY the truth. A wrong reading the leg calls complete fails.
    An unread line must always reach the leg as blindness."""
    rng = random.Random(20261122)
    checked = blind = 0
    for name in CORPORA:
        for c in _load(name)["cases"]:
            lines = c["output"].split("\n")
            for g in GARBAGE:
                pos = rng.randrange(len(lines) + 1)
                out = "\n".join(lines[:pos] + [g] + lines[pos:])
                st = parse_rnstatus(out)
                checked += 1
                leg = _leg(_traffic(st, c["fstats"]))
                if st.unread_lines:
                    assert accepted_gap(st) is not None
                    assert any("could not read" in u for u in leg.unobserved), out
                if leg.unobserved:
                    blind += 1
                else:
                    assert _matches_truth(st, c["truth"]), (g, pos, out)
    assert checked > 1000 and blind > checked // 2


def test_every_unread_line_blocks_all_checks_passed():
    from handlers._rns_diagnostics_engine import summary_lines
    c = _load(CORPORA[0])["cases"][0]
    st = parse_rnstatus(c["output"] + "\n wholly new rns footer\n")
    assert st.unread_lines == [" wholly new rns footer"]
    leg = _leg(_traffic(st, False))
    s = "\n".join(summary_lines(leg.issues, leg.warnings, leg.unobserved))
    assert "All checks passed" not in s
    assert ("interface traffic (1 line(s) this parser could not read: "
            "' wholly new rns footer')") in leg.unobserved


# ------------------------------------------------ r5 IMPORTANT + named shapes

RNODE_MULTI = (
    "\n Shared Instance[rns/default]\n    Status    : Up\n    Serving   : 2 programs\n"
    "    Traffic   : ↑1.00 KB  0 bps\n                ↓2.00 KB  0 bps\n"
    "    Filtered  : ↑0 B  0 bps\n                ↓0 B  0 bps\n"
    "\n RNode-Multi[High Datarate]\n    Status    : Up\n    Mode      : Full\n"
    "    Traffic   : ↑0 B       0 bps\n                ↓44.00 KB  0 bps\n"
    "    Filtered  : ↑0 B  0 bps\n                ↓9.00 KB  0 bps\n"
    "\n Transport Instance <aa> running\n Uptime is 1h\n\n")


def _p(stdout="", stderr="", rc=0):
    return MagicMock(stdout=stdout, stderr=stderr, returncode=rc)


def test_r5_rnode_multi_subinterface_is_read_not_vanished():
    with patch('utils.rns_status_parser._find_rnstatus_binary', return_value='/x/rnstatus'), \
         patch('subprocess.run', side_effect=[_p(RNODE_MULTI)]):
        t = read_interface_traffic()
    assert t.filter_state is FilterState.COUNTED
    names = [i.full_name for i in t.status.interfaces]
    assert names == ["Shared Instance[rns/default]", "RNode-Multi[High Datarate]"]
    rm = t.status.interfaces[1]
    assert rm.type_name == "RNode-Multi" and rm.rx.text == "44.00 KB"
    assert rm.rx_filtered.text == "9.00 KB"
    leg = _leg(t)
    assert any("RNode-Multi[High Datarate]: RX-ONLY" in ln for ln in leg.lines)
    assert any("filtered 9.00 KB" in ln for ln in leg.lines)
    assert leg.warnings and leg.issues


def test_wdcl_unbracketed_name_is_an_interface():
    out = ("\n WDCL over /dev/ttyUSB0\n    Status    : Up\n    Mode      : Full\n"
           "    Traffic   : ↑5 B  0 bps\n                ↓6 B  0 bps\n\n")
    st = parse_rnstatus(out)
    assert st.unread_lines == []
    assert [i.full_name for i in st.interfaces] == ["WDCL over /dev/ttyUSB0"]
    assert st.interfaces[0].tx.text == "5 B"


def test_top_level_line_without_a_status_block_is_unread():
    out = "\n Mystery Thing\n    Traffic   : ↑5 B  0 bps\n                ↓6 B  0 bps\n\n"
    st = parse_rnstatus(out)
    assert st.interfaces == []
    assert st.unread_lines[0] == " Mystery Thing"


def test_status_with_gravity_is_up_161():
    st = parse_rnstatus(" X[y]\n    Status    : Up, gravity 3\n"
                        "    Traffic   : ↑1 B  0 bps\n                ↓1 B  0 bps\n")
    assert st.interfaces[0].status is InterfaceStatus.UP and st.unread_lines == []


def test_unknown_field_name_inside_a_block_is_unread():
    st = parse_rnstatus(" X[y]\n    Status    : Up\n    Gizmo     : 4\n"
                        "    Traffic   : ↑1 B  0 bps\n                ↓1 B  0 bps\n")
    assert st.unread_lines == ["    Gizmo     : 4"]


def test_traffic_field_that_does_not_parse_is_unread():
    st = parse_rnstatus(" X[y]\n    Status    : Up\n    Traffic   : ↑?? B\n"
                        "                ↓1 B  0 bps\n")
    assert st.unread_lines == ["    Traffic   : ↑?? B"]
    assert not st.interfaces[0].tx.observed


def test_block_missing_its_rx_line_is_blind_by_name():
    """A line can go MISSING too (a footer cut the block before its ↓):
    the leg names the half-read interface — never a 0 B, never passed."""
    st = parse_rnstatus(" X[y]\n    Status    : Up\n    Traffic   : ↑1 B  0 bps\n"
                        " Totals       : ↑1 B  0 bps\n                ↓1 B  0 bps\n")
    assert not st.interfaces[0].rx.observed
    leg = _leg(_traffic(st, False))
    assert leg.unobserved == [
        "interface traffic (1 interface(s) listed without readable counters: X[y])"]


def test_footer_takes_only_its_own_continuation():
    st = parse_rnstatus(" Totals       : ↑1 B  0 bps\n                ↓1 B  0 bps\n"
                        "                ↓9 B  0 bps\n")
    assert st.unread_lines == ["                ↓9 B  0 bps"]


def test_rns_log_lines_are_accounted_not_unread():
    st = parse_rnstatus("[2026-11-22 00:10:00] [Warning] some RNS log text\n"
                        " X[y]\n    Status    : Up\n"
                        "    Traffic   : ↑1 B  0 bps\n                ↓1 B  0 bps\n")
    assert st.unread_lines == [] and len(st.interfaces) == 1


# ----------------------------------------------- r5 real-gap survivors (item 4)

def test_dash_Fx_rejection_is_not_no_filter():
    """`-Fx` is another option; only a rejection of -F itself is NO_FILTER."""
    rej = "usage: rnstatus [-h]\nrnstatus: error: unrecognized arguments: -Fx\n"
    with patch('utils.rns_status_parser._find_rnstatus_binary', return_value='/x/rnstatus'), \
         patch('subprocess.run', side_effect=[_p(stderr=rej, rc=2), _p(RNODE_MULTI)]):
        t = read_interface_traffic()
    assert t.filter_state is FilterState.UNKNOWN


def test_half_read_interface_is_not_a_health_row():
    from handlers._rns_diagnostics_engine import check_rns_interface_health
    st = parse_rnstatus(" X[y]\n    Status    : Up\n    Traffic   : ↑1 B  0 bps\n")
    rows = check_rns_interface_health(InterfaceTraffic(st, FilterState.NO_FILTER))
    assert rows == []          # never "↓0 B" from an unread counter


def test_missing_filtered_counts_shown_not_only_fully_read_interfaces():
    """A filtered-blind interface whose Traffic was half-read is still named
    (blind_spots reads shown_interfaces, not leg_rows)."""
    from handlers._rns_diagnostics_engine import blind_spots
    st = parse_rnstatus(" X[y]\n    Status    : Up\n    Traffic   : ↑1 B  0 bps\n"
                        " Z[w]\n    Status    : Up\n    Traffic   : ↑1 B  0 bps\n"
                        "                ↓1 B  0 bps\n    Filtered  : ↑0 B  0 bps\n"
                        "                ↓0 B  0 bps\n")
    spots = blind_spots(InterfaceTraffic(st, FilterState.COUNTED), running=True,
                        instance_ok=True, rnstatus_present=True)
    assert any("printed no Filtered line: X[y]" in s for s in spots)


# --------------------- callers: an empty health list is not DOWN / UP (r5 nit)

MESH_UP = ("\n Meshtastic_Interface[MeshLF]\n    Status    : Up\n    Mode      : Full\n"
           "    Traffic   : ↑5 KB  0 bps\n                ↓6 KB  0 bps\n\n")
OTHER = ("\n TCPInterface[peer/h:1]\n    Status    : Up\n    Mode      : Full\n"
         "    Traffic   : ↑5 KB  0 bps\n                ↓6 KB  0 bps\n\n")


def _subtitle(tmp_path, *responses):
    from test_nomadnet_handler import _make_nomadnet
    h = _make_nomadnet()
    cfg = tmp_path / "config"
    cfg.write_text("[[MeshLF]]\n  type = Meshtastic_Interface\n  enabled = yes\n")
    run = MagicMock(side_effect=list(responses))
    with patch('utils.paths.ReticulumPaths.get_config_file', return_value=cfg), \
         patch('handlers._rns_interface_mgr.find_blocking_interfaces', return_value=[]), \
         patch('utils.rns_status_parser._find_rnstatus_binary', return_value='/x/rnstatus'), \
         patch('subprocess.run', run):
        return h._mesh_iface_subtitle_state(), run.call_count


def test_subtitle_blind_rnstatus_is_unknown_not_down(tmp_path):
    import subprocess
    text, calls = _subtitle(tmp_path, subprocess.TimeoutExpired("rnstatus", 5))
    assert text == "Meshtastic iface: UNKNOWN (rnstatus timed out)"
    assert calls == 1          # one plain run: the subtitle never shows filtered


def test_subtitle_unread_line_is_unknown_not_down(tmp_path):
    text, _ = _subtitle(tmp_path, _p(OTHER + " some new rns line\n"))
    assert text.startswith("Meshtastic iface: UNKNOWN (1 line(s) this parser could not read")


def test_subtitle_complete_reading_without_mesh_is_down(tmp_path):
    text, calls = _subtitle(tmp_path, _p(OTHER))
    assert (text, calls) == ("Meshtastic iface: DOWN", 1)


def test_subtitle_mesh_listed_is_up(tmp_path):
    text, _ = _subtitle(tmp_path, _p(OTHER + MESH_UP))
    assert text == "Meshtastic iface: UP"


def test_interface_health_returns_rows_and_why_blind():
    from handlers._rns_diagnostics_engine import interface_health
    rows, why = interface_health(InterfaceTraffic(parse_rnstatus(OTHER), FilterState.NO_FILTER))
    assert rows == [("TCPInterface[peer/h:1]", "5 KB", "6 KB", True)] and why is None
    rows, why = interface_health(InterfaceTraffic(
        parse_rnstatus(OTHER + " some new rns line\n"), FilterState.NO_FILTER))
    assert len(rows) == 1 and why.startswith("1 line(s) this parser could not read")


@pytest.mark.parametrize("blind,running,si,expect", [
    ("rnstatus timed out", True, True, ("UNKNOWN", "(rnstatus timed out)")),
    (None, True, True, ("UP", "(no traffic data)")),
    (None, True, False, ("UNKNOWN", "rnsd running but shared instance not available")),
    ("rnstatus timed out", False, False, ("DOWN", "rnsd not running")),
])
def test_interface_table_unlisted_row_never_up_when_blind(blind, running, si, expect):
    from handlers.rns_interfaces import unlisted_row_status
    assert unlisted_row_status("RNodeInterface", blind, rnsd_running=running,
                               si_available=si) == expect


# ---------------------------------------------- Fable r6 IMPORTANT (seam)

BRACKET_FIRST = (
    "\n Shared Instance[rns/default]\n    Status    : Up\n    Serving   : 2 programs\n"
    "    Traffic   : ↑1.00 KB  0 bps\n                ↓2.00 KB  0 bps\n"
    "\n [nested][High Datarate]\n    Status    : Up\n    Mode      : Full\n"
    "    Traffic   : ↑0 B       0 bps\n                ↓44.00 KB  0 bps\n"
    "\n Transport Instance <aa> running\n Uptime is 1h\n")


def test_r6_bracket_first_name_reaches_the_leg():
    """Stock RNodeSubInterface under a section named "[nested]" has an empty
    type_name; the engine's old `if i.type_name` filter (for the retired
    regex's bogus matches) dropped this fully-read RX-only interface and the
    leg said "All checks passed". The PARSER alone decides what an interface is."""
    st = parse_rnstatus(BRACKET_FIRST)
    assert st.unread_lines == []
    leg = _leg(_traffic(st, False))
    assert any("[nested][High Datarate]: RX-ONLY" in ln for ln in leg.lines)
    assert leg.issues == ["Interface [nested][High Datarate] is RX-only (no TX)"]


def test_live_stock_driver_fresh_cases_judged_at_the_leg():
    """Fresh stock cases judged at the END: every shown interface is a leg
    row, and the leg is not blind on genuine stock text."""
    pytest.importorskip("RNS.Utilities.rnstatus")
    import rnstatus_stock_driver as d
    rng = random.Random("r6-leg")
    fs = [False, True] if d.has_fstats() else [False]
    for _ in range(300):
        stats = d.random_stats(rng)
        for flag in fs:
            out = d.render(stats, fstats=flag)
            leg = _leg(_traffic(parse_rnstatus(out), flag))
            text = "\n".join(leg.lines)
            truth = d.truth(stats, fstats=flag)
            for t in truth:
                assert f"  {t['name']}: " in text, out
            acc = [u for u in leg.unobserved if u.startswith("interface traffic")]
            # nothing shown is an explained blindness, never "passed"
            assert acc == ([] if truth else
                           ["interface traffic (rnstatus listed no interfaces)"]), out


# ------------------- Fable r6 mutant survivors: right code, now pinned (nits)

_BLK = " X[y]\n    Status    : Up\n"
_TR = "    Traffic   : ↑1 B  0 bps\n                ↓2 B  0 bps\n"


def test_duplicate_traffic_block_is_unread_and_first_kept():      # M1
    st = parse_rnstatus(_BLK + _TR + "    Traffic   : ↑0 B  0 bps\n                ↓9 KB  0 bps\n")
    assert st.unread_lines == ["    Traffic   : ↑0 B  0 bps", "                ↓9 KB  0 bps"]
    assert st.interfaces[0].rx.text == "2 B"


@pytest.mark.parametrize("bad", [                                  # M5 / M6
    "    Traffic   : x↑1 B  0 bps\n                ↓2 B  0 bps\n",
    "    Traffic   : ↑1 B  0 bps\n                x↓2 B  0 bps\n",
])
def test_counter_must_start_with_its_arrow(bad):
    assert parse_rnstatus(_BLK + bad).unread_lines


def test_header_scan_stops_at_a_blank_line():                       # M10
    st = parse_rnstatus(" X[y]\n\n    Status    : Up\n" + _TR)
    assert st.interfaces == [] and st.unread_lines[0] == " X[y]"


def test_status_value_must_be_exactly_up_or_down():                 # M11
    st = parse_rnstatus(" X[y]\n    Status    : Upside-down\n" + _TR)
    assert st.unread_lines == ["    Status    : Upside-down"]
    assert st.interfaces[0].status is InterfaceStatus.UNKNOWN


def test_bracket_led_top_level_text_is_not_a_log_line():            # M13
    assert parse_rnstatus(" [not a log] text\n").unread_lines == [" [not a log] text"]
