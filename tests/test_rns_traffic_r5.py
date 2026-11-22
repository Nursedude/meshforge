"""WITNESS-TUI r5 — Fable r4 findings, red-first.

IMPORTANT: on the fleet path (-F rejected -> NO_FILTER) a plain re-read that
LISTED then exited non-zero said "All checks passed": the reader wrote
"accepted traffic UNKNOWN" into filter_reason and the leg never read it.
Seam (4th round of the same kind): the reader alone judges the accepted
reading's completeness (`accepted_gap`), for whichever view it returns; the
leg only renders it.

Run: python3 -m pytest tests/test_rns_traffic_r5.py -v
"""
import logging
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'launcher_tui'))

from utils.rns_status_parser import (  # noqa: E402
    FilterState, InterfaceTraffic, parse_rnstatus, read_interface_traffic,
)

BIN = 'utils.rns_status_parser._find_rnstatus_binary'
REJECT = "usage: rnstatus [-h]\nrnstatus: error: unrecognized arguments: -F\n"
SI = " Shared Instance[37428]\n    Status    : Up\n    Traffic   : ↑1 KB  0 bps\n                ↓2 KB  0 bps\n\n"
TCP = (" TCPInterface[Regional/host:4242]\n    Status    : Up\n"
       "    Traffic   : ↑478 B  0 bps\n                ↓254 B  0 bps\n\n")
CRASH_TXB = "Traceback (most recent call last):\n  File \"rnstatus.py\", line 1\nKeyError: 'txb'\n"


def _p(stdout="", stderr="", rc=0):
    return MagicMock(stdout=stdout, stderr=stderr, returncode=rc)


def _read(*responses):
    with patch(BIN, return_value='/usr/bin/rnstatus'), \
         patch('subprocess.run', side_effect=list(responses)):
        return read_interface_traffic()


def _leg(t, running=True, instance_ok=True, present=True):
    from handlers._rns_diagnostics_engine import interface_traffic_leg
    return interface_traffic_leg(t, running=running, instance_ok=instance_ok,
                                 rnstatus_present=present)


def _summary(leg):
    from handlers._rns_diagnostics_engine import summary_lines
    return "\n".join(summary_lines(leg.issues, leg.warnings, leg.unobserved))


# ------------------------------------------------------------ IMPORTANT

def test_no_filter_plain_partial_crash_is_blind_not_all_passed():
    t = _read(_p(stderr=REJECT, rc=2), _p(SI + TCP, CRASH_TXB, 1))
    assert t.filter_state is FilterState.NO_FILTER
    leg = _leg(t)
    assert leg.unobserved == [
        "interface traffic (partial listing — rnstatus exited 1: KeyError: 'txb')"]
    assert "All checks passed" not in _summary(leg)
    # what WAS read is still shown
    assert "  TCPInterface[Regional/host:4242]: ↑478 B ↓254 B" in leg.lines


def test_no_filter_plain_full_listing_rc1_is_blind():
    t = _read(_p(stderr=REJECT, rc=2), _p(SI + TCP, "", 1))
    leg = _leg(t)
    assert leg.unobserved == [
        "interface traffic (partial listing — rnstatus exited 1: Shared Instance[37428])"]


def test_unknown_path_plain_partial_reports_accepted_blindness_too():
    t = _read(_p(stderr="No shared RNS instance available to get status from\n", rc=1),
              _p(TCP, CRASH_TXB, 1))
    assert t.filter_state is FilterState.UNKNOWN
    leg = _leg(t)
    assert leg.unobserved[0] == ("interface traffic (partial listing — "
                                 "rnstatus exited 1: KeyError: 'txb')")
    assert leg.unobserved[1].startswith("filtered rx (rnstatus -F: ")


def test_reader_owns_accepted_completeness():
    t = _read(_p(stderr=REJECT, rc=2), _p(SI + TCP, CRASH_TXB, 1))
    assert t.accepted_gap == "partial listing — rnstatus exited 1: KeyError: 'txb'"
    ok = _read(_p(stderr=REJECT, rc=2), _p(SI + TCP))
    assert ok.accepted_gap is None


# ------------------------------------------------ surviving mutants (r4)

def test_filtered_up_line_alone_is_not_filtered_rx():
    # Fable P6: Filtered ↑ stored into rx_filtered survived every file.
    raw = TCP.replace("\n\n", "\n    Filtered  : ↑5 B  0 bps\n\n")
    leg = _leg(InterfaceTraffic(parse_rnstatus(raw), FilterState.COUNTED))
    assert "      rx accepted 254 B · filtered UNKNOWN (no Filtered line for this interface)" \
        in leg.lines
    assert leg.warnings == []


def test_idle_interface_is_not_rx_only():
    # Fable P21: is_healthy = tx == 0 alone survived (no idle fixture).
    raw = TCP.replace("↑478 B", "↑0 B").replace("↓254 B", "↓0 B")
    leg = _leg(InterfaceTraffic(parse_rnstatus(raw), FilterState.NO_FILTER))
    assert leg.lines[0] == "  TCPInterface[Regional/host:4242]: ↑0 B ↓0 B"
    assert leg.issues == []


def test_long_error_line_is_cut_at_120():
    # Fable P9: a 40-char cut survived (no line longer than 40).
    long = "KeyError: '" + "x" * 200 + "'"
    t = _read(_p(stderr=REJECT, rc=2), _p(TCP, long + "\n", 1))
    assert t.accepted_gap == f"partial listing — rnstatus exited 1: {long[:120]}"


# ---------------------------------------------------------------- nits

def test_observed_filtered_is_shown_when_accepted_is_unread():
    # nit 1: a Filtered ↓ read on an interface whose Traffic was not.
    raw = (" RNodeInterface[RNode LoRa]\n    Status    : Up\n"
           "    Filtered  : ↑0 B  0 bps\n                ↓5.10 KB  0 bps\n\n"
           + TCP.replace("\n\n", "\n    Filtered  : ↑0 B  0 bps\n                ↓0 B  0 bps\n\n"))
    leg = _leg(InterfaceTraffic(parse_rnstatus(raw), FilterState.COUNTED))
    i = leg.lines.index("  RNodeInterface[RNode LoRa]: ↑UNKNOWN ↓UNKNOWN")
    assert leg.lines[i + 1].startswith("      rx accepted UNKNOWN · filtered 5.10 KB ⚠")
    assert len(leg.warnings) == 1 and leg.warnings[0].startswith("1 interface(s) filtered")
    assert leg.unobserved == ["interface traffic (1 interface(s) listed without "
                              "readable counters: RNodeInterface[RNode LoRa])"]
    assert leg.issues == []


def test_any_named_header_closes_the_arrow_section():
    # nit 2: a digit in a header name ("I2P B32") left the section open.
    raw = (" TCPInterface[one/a:1]\n    Traffic   : ↑1 B  0 bps\n                ↓2 B  0 bps\n"
           "    Filtered  : ↑0 B  0 bps\n                ↓0 B  0 bps\n"
           "    I2P B32   : ↑1 B\n                ↓66 B\n")
    one, = parse_rnstatus(raw).interfaces
    assert one.rx_filtered.text == "0 B" and one.rx.text == "2 B"


def test_missing_binary_gives_one_remedy_never_pip():
    with patch(BIN, return_value=None):
        t = read_interface_traffic()
    leg = _leg(t, present=False)
    text = "\n".join(leg.lines + leg.unobserved + [t.filter_reason])
    assert "pip" not in text
    assert "Settings > Updates" in text


def test_leg_does_not_reparse_or_relog_the_dump(caplog):
    # nit 5: one leg logged the whole rnstatus dump at WARNING 4 times.
    noisy = _p(TCP, "Could not connect to peer9/h9:4299\n", 0)
    t = _read(noisy, _p(TCP, "Could not connect to peer9/h9:4299\n", 0))
    assert t.status.parse_error                     # the view carries the error text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="utils.rns_status_parser"):
        _leg(t)
    assert caplog.records == []


def test_ipv6_interface_name_is_kept_whole():
    # nit 6: _IFACE_RE stopped at the first ']'.
    raw = (" TCPInterface[v6/[fe80::1]:4242]\n    Status    : Up\n"
           "    Traffic   : ↑1 B  0 bps\n                ↓2 B  0 bps\n")
    one, = parse_rnstatus(raw).interfaces
    assert one.full_name == "TCPInterface[v6/[fe80::1]:4242]"
