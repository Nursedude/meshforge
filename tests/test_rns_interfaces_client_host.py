"""Interface Status rows when a CLIENT hosts the shared instance (r9 fix 2).

rnsd always hosts by design; NomadNet, Sideband and MeshChatX are clients.
When rnsd is down or restarting, a client can take the shared instance
(the #69 race) and bring up the SAME config's interfaces. The table used to
check ``not rnsd_running`` FIRST, so every unlisted row said "DOWN rnsd not
running" beside a live instance — and beside a blind reading that could not
have shown it (Fable r8, repro a2.py B1a/B1b). Rows are judged by the
READING; the host is named; DOWN only when nothing hosts an instance at all.
(operator 11-22: "support the clients, play nice with other apps".)
"""

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_rns_every_line import _FOOT, _SI, _blk, _row, _table  # noqa: E402

from handlers.rns_interfaces import unlisted_row_status  # noqa: E402

BLINDS = (None, "rnstatus timed out")
HOSTS = (None, "rnsd", "nomadnet")
STATES = list(itertools.product(BLINDS, (True, False), (True, False), HOSTS))


@pytest.mark.parametrize("blind,running,si,host", STATES)
def test_unlisted_row_state_table_invariants(blind, running, si, host):
    status, detail = unlisted_row_status(
        "RNodeInterface", blind, rnsd_running=running, si_available=si, host=host)
    instance_seen = si or host is not None or blind is None
    # Never UP from absence.
    assert status in ("UNKNOWN", "DOWN")
    # DOWN only when NOTHING hosts an instance and the reading failed.
    assert (status == "DOWN") == (not running and not instance_seen)
    # "rnsd not running" is never a row's verdict beside a live instance.
    if instance_seen:
        assert "rnsd not running" not in detail
    # A client host is named on the row.
    if host not in (None, "rnsd") and status != "DOWN":
        assert f"hosted by {host}" in detail
    # A blind reading's reason is never dropped.
    if blind and status == "UNKNOWN":
        assert blind in detail


def test_unlisted_row_literals():
    f = unlisted_row_status
    assert f("X", None, rnsd_running=False, si_available=True, host="nomadnet") == (
        "UNKNOWN", "(not in rnstatus output; instance hosted by nomadnet)")
    assert f("X", "1 line(s) unread", rnsd_running=False, si_available=True,
             host="nomadnet") == (
        "UNKNOWN", "(1 line(s) unread; instance hosted by nomadnet)")
    assert f("X", "rnstatus timed out", rnsd_running=False, si_available=False,
             host=None) == ("DOWN", "no RNS instance running")


_T = _SI + _blk("RNodeInterface[Alpha]") + _FOOT
_IF = [("Alpha", "RNodeInterface"), ("Bravo", "RNodeInterface")]
_SAFE_ORDER = "close RNS client apps"


# r10 (Fable r9 #1): RNS shares over abstract Unix sockets, never UDP 37428,
# so a UDP owner is NOT the instance host. Whatever it returns, no one is
# named — the host is "not identified" and the safe order is generic.
@pytest.mark.parametrize("owner", [None, ("nomadnet", 4242), ("python3", 88), ("rnsd", 77)])
def test_table_client_hosts_complete_reading(owner):                     # B1a
    out = _table(_T, _IF, running=False, si=True, owner=owner)
    assert _row(out, "Alpha", "RNodeInterface") == ("UP", "↑5 KB  ↓6 KB")
    assert _row(out, "Bravo", "RNodeInterface") == (
        "UNKNOWN", "(not in rnstatus output)")
    text = "\n".join(out)
    assert "Start rnsd from Service Control." not in text
    assert "host not identified" in text and _SAFE_ORDER in text
    for name in ("nomadnet", "python3", "PID"):
        assert f"hosted by {name}" not in text and f"{name} (PID" not in text


def test_table_client_hosts_blind_reading():                             # B1b
    t = _SI + _blk("RNodeInterface[Alpha]") + " weird\n" + _FOOT
    out = _table(t, _IF, running=False, si=True, owner=("nomadnet", 4242))
    st, detail = _row(out, "Bravo", "RNodeInterface")
    assert st == "UNKNOWN" and "could not read" in detail
    assert "hosted by" not in detail


# r10 (Fable r9 #5): a row that was READ proves an instance answered — for
# the row, the header and the footer — even when every proxy probe missed.
@pytest.mark.parametrize("text", [
    _T,                                                       # complete
    _SI + _blk("RNodeInterface[Alpha]") + " weird\n" + _FOOT,  # blind, rows read
])
def test_read_row_proves_an_instance(text):
    out = _table(text, _IF, running=False, si=False, owner=None)
    st, _d = _row(out, "Bravo", "RNodeInterface")
    assert st == "UNKNOWN"
    joined = "\n".join(out)
    assert "NOT RUNNING" not in joined
    assert "Start rnsd from Service Control." not in joined
    assert _SAFE_ORDER in joined


def test_table_nothing_hosts_is_down_and_says_start_rnsd():
    out = _table("", _IF, plain_rc=1, running=False, si=False, owner=None)
    assert _row(out, "Bravo", "RNodeInterface") == ("DOWN", "no RNS instance running")
    joined = "\n".join(out)
    assert "Start rnsd from Service Control." in joined
    assert "NOT RUNNING" in joined


def test_rnsd_running_probe_missed_but_reading_answered_is_not_degraded():
    out = _table(_T, _IF, running=True, si=False, owner=None)
    joined = "\n".join(out)
    assert "rnsd — shared instance available" in joined
    assert "DEGRADED" not in joined and "NOT responding" not in joined
