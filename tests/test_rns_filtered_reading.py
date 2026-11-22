"""WITNESS-TUI slice 2 — per-interface rx accepted vs rx FILTERED, on every box.

rns >= 1.6 drops unstamped announces under enforce_stamps SILENTLY (1.6.1 has
the log lines commented out); the per-interface Filtered ↓ from `rnstatus -F`
is the only witness. The fleet's 1.3.9 has no filter and REJECTS -F — that is
an explained absence ("this RNS has no announce filter"), not UNKNOWN and
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
    assert mock_run.call_count == 1


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
    assert "no announce filter" in text
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
