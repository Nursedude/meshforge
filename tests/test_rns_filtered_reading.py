"""WITNESS-TUI slice 2 — per-interface rx accepted vs rx FILTERED, on every box.

rns >= 1.6 drops unstamped announces under enforce_stamps SILENTLY (1.6.1 has
the log lines commented out); the per-interface Filtered ↓ from `rnstatus -F`
is the only witness. The fleet's 1.3.9 has no filter and REJECTS -F — that is
an explained absence ("this rnstatus has no -F"), not UNKNOWN and
never "0 filtered". Anything that observed nothing is UNKNOWN (hfm #2).

Run: python3 -m pytest tests/test_rns_filtered_reading.py -v
"""
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

from utils.rns_status_parser import (  # noqa: E402
    FilterState,
    read_interface_traffic,
    filtered_rx_verdict,
)

FIXTURES = Path(__file__).parent / "fixtures" / "rnstatus"
F_161 = (FIXTURES / "stock_161_F_enforce_idle.txt").read_text()

# Real fleet rnstatus 1.3.9+mf.0 answer to `rnstatus -F` (lab Pi, 2026-11-21).
REJECTS_F_139 = """\
usage: rnstatus [-h] [--config CONFIG] [--version] [-a] [-A] [-P] [-l] [-B]
                [-t] [-s SORT] [-r] [-j] [-R hash] [-i path] [-w seconds] [-d]
                [-D] [-m] [-I seconds] [-v]
                [filter]
rnstatus: error: unrecognized arguments: -F
"""

PLAIN_139 = """\

 Shared Instance[rns/default]
    Status    : Up
    Serving   : 2 programs
    Rate      : 1.00 Gbps
    Traffic   : ↑1.20 KB    0 bps
                ↓900 B      0 bps

 TCPInterface[Regional entry point/example.invalid:4242]
    Status    : Up
    Mode      : Full
    Rate      : 10.00 Mbps
    Traffic   : ↑478 B      0 bps
                ↓254 B      0 bps

"""

BIN = 'utils.rns_status_parser._find_rnstatus_binary'


def _proc(stdout="", stderr="", rc=0):
    return MagicMock(stdout=stdout, stderr=stderr, returncode=rc)


def _leg(traffic):
    legs = [i for i in traffic.status.interfaces if i.type_name == "TCPInterface"]
    assert len(legs) == 1
    return legs[0]


# ------------------------------------------------------------- the five states

@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run')
def test_stock_16x_is_counted_in_one_call(mock_run, _b):
    mock_run.return_value = _proc(F_161)
    t = read_interface_traffic()
    assert t.filter_state is FilterState.COUNTED
    assert mock_run.call_count == 1
    assert "-F" in mock_run.call_args[0][0]
    leg = _leg(t)
    assert leg.rx.bytes_total == 254
    assert leg.rx_filtered.bytes_total == 491


@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run')
def test_fleet_139_rejecting_F_is_no_filter_and_rereads_plain(mock_run, _b):
    mock_run.side_effect = [_proc(stderr=REJECTS_F_139, rc=2), _proc(PLAIN_139)]
    t = read_interface_traffic()
    assert t.filter_state is FilterState.NO_FILTER
    assert mock_run.call_count == 2
    assert "-F" not in mock_run.call_args_list[1][0][0]
    # accepted traffic still observed from the plain read
    assert t.status.parse_error is None
    assert _leg(t).rx.bytes_total == 254


@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='rnstatus', timeout=15))
def test_timeout_is_unknown_and_not_retried(mock_run, _b):
    t = read_interface_traffic()
    assert t.filter_state is FilterState.UNKNOWN
    assert mock_run.call_count == 1          # a wedged rnsd is not hit twice
    assert t.status.timed_out
    assert "timed out" in t.filter_reason


@patch(BIN, return_value=None)
def test_missing_binary_is_unknown(_b):
    t = read_interface_traffic()
    assert t.filter_state is FilterState.UNKNOWN
    assert "not found" in t.filter_reason


@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run')
def test_no_shared_instance_is_unknown_not_no_filter(mock_run, _b):
    mock_run.return_value = _proc(stderr="Could not get shared instance\n", rc=1)
    t = read_interface_traffic()
    assert t.filter_state is FilterState.UNKNOWN
    # one -F try + one plain re-read for the accepted view (r1 #5)
    assert mock_run.call_count == 2


@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run')
def test_ok_with_zero_interfaces_is_unknown(mock_run, _b):
    mock_run.return_value = _proc(" Transport Instance <ab> running\n")
    t = read_interface_traffic()
    assert t.filter_state is FilterState.UNKNOWN


# ----------------------------------------------------- per-interface verdicts

@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run')
def test_verdicts_counted(mock_run, _b):
    mock_run.return_value = _proc(F_161)
    t = read_interface_traffic()
    leg = _leg(t)
    state, text = filtered_rx_verdict(leg, t.filter_state)
    assert state is FilterState.COUNTED
    assert "491 B" in text and "254 B" in text
    assert "not logged" in text              # the >0 case says WHY it matters
    shared = [i for i in t.status.interfaces if i.type_name == "Shared Instance"][0]
    state, text = filtered_rx_verdict(shared, t.filter_state)
    assert state is FilterState.COUNTED
    assert "not logged" not in text          # 0 filtered: no scare text


@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run')
def test_verdict_leg_missing_its_block_under_F_is_unknown(mock_run, _b):
    # -F accepted, but this leg printed no Filtered block: blind, never 0.
    no_leg_block = F_161.replace(
        "    Filtered  : ↑0 B  0 bps\n                ↓491 B  0 bps\n", "")
    assert no_leg_block != F_161
    mock_run.return_value = _proc(no_leg_block)
    t = read_interface_traffic()
    state, text = filtered_rx_verdict(_leg(t), t.filter_state)
    assert state is FilterState.UNKNOWN
    assert "filtered UNKNOWN" in text
    assert "filtered 0" not in text


@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run')
def test_verdict_no_filter_says_so_and_never_claims_zero(mock_run, _b):
    mock_run.side_effect = [_proc(stderr=REJECTS_F_139, rc=2), _proc(PLAIN_139)]
    t = read_interface_traffic()
    state, text = filtered_rx_verdict(_leg(t), t.filter_state)
    assert state is FilterState.NO_FILTER
    assert "this rnstatus has no -F" in text
    assert "filtered 0" not in text and "filtered: 0" not in text


# ------------------------------- the TUI's old second reader now reads ONE way

@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run')
def test_check_rns_interface_health_does_not_count_filtered_as_rx(mock_run, _b):
    from handlers._rns_diagnostics_engine import check_rns_interface_health
    mock_run.return_value = _proc(F_161)
    traffic = read_interface_traffic()
    rows = check_rns_interface_health(traffic)
    legs = [r for r in rows if r[0].startswith("TCPInterface[")]
    assert len(legs) == 1, rows                # no phantom row from the Filtered ↓
    name, tx, rx, healthy = legs[0]
    assert rx == "254 B" and tx == "478 B"
    assert healthy


@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run')
def test_check_rns_interface_health_blind_is_empty(mock_run, _b):
    from handlers._rns_diagnostics_engine import check_rns_interface_health
    mock_run.return_value = _proc(stderr="Could not get shared instance\n", rc=1)
    assert check_rns_interface_health() == []


# =========================================================================
# Fable r1 (2026-11-21) — the input table. One defect kind ran through the
# r1 findings: the parser ASSERTED a value it never read (a 0 B default, a
# default "traffic" section). Every row below is unread → UNKNOWN / no row,
# and printed values travel as the TEXT rnstatus printed (= the awk reader).
# =========================================================================

import pytest  # noqa: E402

from utils.rns_status_parser import parse_rnstatus, format_bytes  # noqa: E402

_LEG = "Regional entry point"


def _leg_of(raw):
    legs = [i for i in parse_rnstatus(raw).interfaces
            if i.display_name.startswith(_LEG)]
    assert len(legs) == 1, raw
    return legs[0]


HELD_BLOCK = "    Held      : ↑7 B  0 bps\n                ↓9 B  0 bps\n"
LEG_FILTERED = "    Filtered  : ↑0 B  0 bps\n                ↓491 B  0 bps\n"


@pytest.mark.parametrize("raw, accepted, filtered", [
    (F_161, "254 B", "491 B"),                                          # row 1
    (F_161.replace("Filtered  :", "filtered  :"), "254 B", "491 B"),    # row 2
    (F_161.replace(LEG_FILTERED, HELD_BLOCK + LEG_FILTERED), "254 B", "491 B"),  # row 3
    (F_161.replace(LEG_FILTERED, LEG_FILTERED + HELD_BLOCK), "254 B", "491 B"),  # row 3
    (F_161 + " Totals    : ↑9 KB  0 bps\n              ↓9 KB  0 bps\n",
     "254 B", "491 B"),                                                 # row 4
    (F_161.replace("↓254 B      0 bps", "↓254 B"), "254 B", "491 B"),   # row 6
    (F_161.replace("↓254 B      0 bps", "↓1.20 KB    0 bps"), "1.20 KB", "491 B"),  # row 7
    (F_161.replace("↓254 B      0 bps", "↓1,234 B    0 bps")
          .replace("↓491 B  0 bps", "↓100.00 MB  0 bps"), "1,234 B", "100.00 MB"),  # row 7
])
def test_input_table_accepted_and_filtered(raw, accepted, filtered):
    leg = _leg_of(raw)
    assert leg.rx.observed and format_bytes(leg.rx) == accepted
    assert leg.rx_filtered is not None and format_bytes(leg.rx_filtered) == filtered


def test_row5_truncated_leg_is_unread_not_zero():
    leg = _leg_of(" TCPInterface[Regional entry point/x:1]\n    Status    : Up\n")
    assert not leg.rx.observed and not leg.tx.observed
    assert leg.rx_filtered is None
    state, text = filtered_rx_verdict(leg, FilterState.COUNTED)
    assert state is FilterState.UNKNOWN
    assert "rx accepted UNKNOWN" in text and "0 B" not in text


def test_stray_arrow_before_any_header_is_not_traffic():
    raw = " TCPInterface[Regional entry point/x:1]\n    ↓77 B  0 bps\n    Status    : Up\n"
    assert not _leg_of(raw).rx.observed


def test_format_bytes_is_the_printed_text_not_a_float():
    leg = _leg_of(F_161.replace("↓254 B      0 bps", "↓1,234,567 B  0 bps"))
    assert format_bytes(leg.rx) == "1,234,567 B"
    assert leg.rx.bytes_total == 1234567


# ---------------------------------------------- health rows: the old contract

def _health(traffic):
    from handlers._rns_diagnostics_engine import check_rns_interface_health
    return check_rns_interface_health(traffic)


def _traffic(raw, state=None, reason="", timed_out=False, parse_error=None):
    from utils.rns_status_parser import InterfaceTraffic
    st = parse_rnstatus(raw)
    st.timed_out = timed_out
    if parse_error:
        st.parse_error = parse_error
    return InterfaceTraffic(st, state or FilterState.COUNTED, reason)


def test_row5_truncated_leg_emits_no_health_row():
    rows = _health(_traffic(" TCPInterface[Regional entry point/x:1]\n    Status    : Up\n"))
    assert rows == []


def test_row8_shared_instance_is_not_a_health_row():
    # The old reader never listed it; a Shared Instance with rx>0, tx 0 must
    # not raise "RX-only ... port 37428 not bound" (r1 finding 5).
    raw = (" Shared Instance[rns/default]\n    Status    : Up\n"
           "    Traffic   : ↑0 B        0 bps\n                ↓900 B      0 bps\n")
    assert _health(_traffic(raw)) == []


def test_row9_nonzero_exit_with_a_listing_keeps_rows():
    rows = _health(_traffic(PLAIN_139, parse_error="rnstatus exited 1: x"))
    assert [r[0] for r in rows] == ["TCPInterface[Regional entry point/example.invalid:4242]"]


def test_health_guards_timeout_and_no_shared_instance():
    assert _health(_traffic(PLAIN_139, timed_out=True)) == []
    assert _health(_traffic("Could not get shared instance\n" + PLAIN_139)) == []


# ------------------------------------------------- reader: rows 10, 11, 12

@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run')
def test_row10_F_failure_on_16_rereads_plain_for_accepted(mock_run, _b):
    mock_run.side_effect = [_proc(stderr="some 1.6 error\n", rc=1), _proc(PLAIN_139)]
    t = read_interface_traffic()
    assert t.filter_state is FilterState.UNKNOWN
    assert "some 1.6 error" in t.filter_reason
    assert mock_run.call_count == 2
    assert _leg(t).rx.bytes_total == 254          # accepted view not lost


@pytest.mark.parametrize("rejection", [
    REJECTS_F_139,
    "rnstatus: error: unrecognized arguments: '-F'\n",
    "rnstatus: error: no such option: -F\n",
])
@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run')
def test_row11_rejection_wordings_are_no_filter(mock_run, _b, rejection):
    mock_run.side_effect = [_proc(stderr=rejection, rc=2), _proc(PLAIN_139)]
    assert read_interface_traffic().filter_state is FilterState.NO_FILTER


@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run')
def test_rejection_text_inside_a_real_listing_is_not_no_filter(mock_run, _b):
    named = F_161.replace("Regional entry point", "unrecognized arguments: -F x")
    mock_run.return_value = _proc(named)
    t = read_interface_traffic()
    assert t.filter_state is FilterState.COUNTED
    assert mock_run.call_count == 1


@patch(BIN, return_value='/usr/bin/rnstatus')
@patch('subprocess.run')
def test_row12_no_filter_with_failed_plain_read_says_accepted_is_blind(mock_run, _b):
    mock_run.side_effect = [_proc(stderr=REJECTS_F_139, rc=2),
                            _proc(stderr="Could not get shared instance\n", rc=1)]
    t = read_interface_traffic()
    assert t.filter_state is FilterState.NO_FILTER
    assert "accepted traffic UNKNOWN" in t.filter_reason


# ---------------------------- the diagnostics leg itself (r1 findings 1, 6, 9)

def _leg6(traffic, running=True, instance_ok=True, rnstatus_present=True):
    from handlers._rns_diagnostics_engine import interface_traffic_leg
    return interface_traffic_leg(traffic, running=running, instance_ok=instance_ok,
                                 rnstatus_present=rnstatus_present)


def test_leg6_prints_the_filtered_line_under_each_interface():
    out = _leg6(_traffic(F_161))
    text = "\n".join(out.lines)
    assert "rx accepted 254 B · filtered 491 B" in text
    assert out.unobserved == []


def test_leg6_filtered_above_zero_is_a_warning_not_all_passed():
    out = _leg6(_traffic(F_161))
    assert any("filtered" in w for w in out.warnings), out.warnings
    assert out.issues == []                       # observe before alarm


def test_leg6_counted_but_a_leg_printed_no_filtered_line_is_unobserved():
    out = _leg6(_traffic(F_161.replace(LEG_FILTERED, "")))
    assert any("filtered rx" in u for u in out.unobserved), out.unobserved


@pytest.mark.parametrize("kw, raw", [
    ({"timed_out": True}, PLAIN_139),
    ({"parse_error": "rnstatus produced no output (exit 0)"}, ""),
])
def test_leg6_every_blind_reading_records_filtered_unobserved(kw, raw):
    out = _leg6(_traffic(raw, state=FilterState.UNKNOWN, reason="rnstatus -F timed out", **kw))
    assert any("filtered rx" in u and "timed out" in u for u in out.unobserved), out.unobserved
    assert any("interface traffic" in u for u in out.unobserved), out.unobserved


@pytest.mark.parametrize("present, running, ok, needle", [
    (False, True, True, "rnstatus not installed"),
    (True, True, False, "rnstatus cannot connect"),
    (True, True, True, "rnstatus returned nothing"),
])
def test_leg6_no_rows_branches_record_interface_traffic_unobserved(present, running, ok, needle):
    out = _leg6(_traffic("", state=FilterState.UNKNOWN, reason="r"),
                running=running, instance_ok=ok, rnstatus_present=present)
    assert any(needle in u for u in out.unobserved), out.unobserved


def test_leg6_no_filter_is_not_unobserved():
    t = _traffic(PLAIN_139, state=FilterState.NO_FILTER, reason="no -F")
    out = _leg6(t)
    assert out.unobserved == []
    assert "this rnstatus has no -F" in "\n".join(out.lines)


def test_section_resets_at_each_interface_header():
    # Interface 1 ends INSIDE its Filtered block; interface 2's stray arrow
    # before any header must not be read as its traffic (r2 M4 survivor).
    raw = (" TCPInterface[one/a:1]\n    Traffic   : ↑1 B  0 bps\n                ↓2 B  0 bps\n"
           "    Filtered  : ↑0 B  0 bps\n                ↓3 B  0 bps\n"
           " TCPInterface[two/b:2]\n                ↓77 B  0 bps\n    Status    : Up\n")
    one, two = parse_rnstatus(raw).interfaces
    assert format_bytes(one.rx_filtered) == "3 B"
    assert not two.rx.observed and two.rx_filtered is None
