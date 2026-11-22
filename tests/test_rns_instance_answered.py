"""ONE "an RNS instance answered" predicate, from the parser (WITNESS-TUI r11 seam 2).

r10 computed it on the Interface Status screen as ``blind is None or
bool(health)``. ``health_rows()`` drops the Shared Instance row and any row
whose counters were not both read, so a BLIND reading whose only read block
was ``Shared Instance … Status: Up`` (or a half-read interface) said NOT
RUNNING + DOWN "no RNS instance running" + "Start rnsd" beside the instance
that had just answered — or DEGRADED + the repair wizard when rnsd ran
(Fable r10 #1, r10-scratch/tbl2.py). The parser defines an interface (a
header whose block holds a Status line); it now also says whether a reading
reached an instance, and the screen asks it.
"""

import itertools
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils.rns_status_parser import RNSStatus, instance_answered, parse_rnstatus  # noqa: E402


def _blk(name, status="Up", traffic=True):
    s = f"\n {name}\n    Status    : {status}\n    Mode      : Full\n"
    if traffic:
        s += "    Traffic   : ↑5 KB  0 bps\n                ↓6 KB  0 bps\n"
    return s


FOOT = "\n Transport Instance <aabb> running\n Uptime is 5s\n\n"
SI = ("\n Shared Instance[rns/default]\n    Status    : Up\n    Serving   : 1 program\n"
      "    Rate      : 1.00 Gbps\n    Traffic   : ↑1 KB  0 bps\n                ↓2 KB  0 bps\n")
A = _blk("RNodeInterface[Alpha]")
KEYERR = "Traceback (most recent call last):\nKeyError: 'frxb'\n"
NOINST = "No shared RNS instance available to get status from\n"


def _p(text, rc=0, err=""):
    return SimpleNamespace(stdout=text, stderr=err, returncode=rc)


# reading -> (subprocess result for the plain rnstatus, answered?)
READ = {
    "complete": (_p(SI + A + FOOT), True),
    "si_only": (_p(SI + FOOT), True),
    "si_only_blind": (_p(SI + " weird\n" + FOOT), True),          # Fable r10 #1
    "si_only_rc1": (_p(SI, 1, KEYERR), True),                    # partial listing
    "si+halfread": (_p(SI + _blk("RNodeInterface[Alpha]", traffic=False) + FOOT), True),
    "halfread_only": (_p(_blk("RNodeInterface[Alpha]", traffic=False) + FOOT), True),
    "alpha_rc1": (_p(A, 1, KEYERR), True),
    "alpha_down": (_p(SI + _blk("RNodeInterface[Alpha]", status="Down") + FOOT), True),
    "foot_only": (_p(FOOT), False),
    "noinst_rc0": (_p(NOINST, 0), False),
    "alpha+noinst_text": (_p(A + NOINST, 1), False),             # error line: untrusted
    "empty_rc1": (_p("", 1), False),
    "timeout": ("TIMEOUT", False),
    "oserror": ("OSERR", False),
    "nobinary": ("NOBIN", False),
}


def _status(reading):
    """The RNSStatus run_rnstatus would return for ``reading``."""
    from utils import rns_status_parser as P
    r, _ = READ[reading]

    def run(argv, *a, **k):
        if r == "TIMEOUT":
            raise subprocess.TimeoutExpired(argv, 15)
        if r == "OSERR":
            raise OSError("exec failed")
        return r
    with patch.object(P, "_find_rnstatus_binary",
                      return_value=None if r == "NOBIN" else "/x/rnstatus"), \
         patch("subprocess.run", MagicMock(side_effect=run)):
        return P.run_rnstatus()


@pytest.mark.parametrize("reading", sorted(READ))
def test_instance_answered_per_reading(reading):
    assert instance_answered(_status(reading)) is READ[reading][1]


def test_instance_answered_literals():
    assert instance_answered(parse_rnstatus(SI + " weird\n" + FOOT)) is True
    assert instance_answered(RNSStatus()) is False
    assert instance_answered(RNSStatus(timed_out=True)) is False
    # A listing captured before a timeout is not trusted (the RPC hung).
    hung = parse_rnstatus(SI + A)
    hung.timed_out = True
    assert hung.interfaces and instance_answered(hung) is False


# ── the screen asks the parser ─────────────────────────────────────────


def _table(reading, running, si):
    import contextlib
    import io
    from handlers.rns_interfaces import RNSInterfacesHandler
    from handler_test_utils import make_handler_context
    h = RNSInterfacesHandler()
    h.set_context(make_handler_context())
    h._find_nomadnet_info = lambda: (None, None)
    h._rns_cmd_list_interfaces = lambda: SimpleNamespace(data={"interfaces": [
        {"name": n, "settings": {"type": "RNodeInterface", "enabled": "yes"}}
        for n in ("Alpha", "Bravo")]})
    r, _ = READ[reading]
    no_f = _p("", 2, "usage: rnstatus [-h]\nrnstatus: error: unrecognized arguments: -F\n")

    def run(argv, *a, **k):
        if r == "TIMEOUT":
            raise subprocess.TimeoutExpired(argv, 15)
        if r == "OSERR":
            raise OSError("exec failed")
        return no_f if "-F" in argv else r
    buf = io.StringIO()
    with patch("handlers._rns_interface_mgr.find_blocking_interfaces", return_value=[]), \
         patch("utils.service_check.check_process_running", return_value=running), \
         patch("utils.service_check.get_rns_shared_instance_info",
               return_value={"available": si, "method": "x"}), \
         patch("utils.rns_status_parser._find_rnstatus_binary",
               return_value=None if r == "NOBIN" else "/x/rnstatus"), \
         patch("handlers._service_ops_common.journal_tail_text", return_value=""), \
         patch("handlers.rns_interfaces.clear_screen"), \
         patch("builtins.input", return_value=""), \
         patch("subprocess.run", MagicMock(side_effect=run)), \
         contextlib.redirect_stdout(buf):
        h._rns_interface_status()
    return buf.getvalue()


def _bravo(out):
    for ln in out.splitlines():
        body = ln[2:]
        head = f"{'Bravo':<26} {'RNodeInterface':<24} "
        if body.startswith(head):
            rest = body[len(head):]
            return rest[:10].strip(), rest[11:].strip()
    raise AssertionError(out)


STATES = list(itertools.product(sorted(READ), (True, False), (True, False)))


@pytest.mark.parametrize("reading,running,si", STATES)
def test_header_rows_footer_agree_with_the_reading(reading, running, si):
    out = _table(reading, running, si)
    answered = READ[reading][1]
    seen = si or answered
    not_running = "NOT RUNNING" in out
    start_rnsd = "Start rnsd from Service Control." in out
    degraded = "DEGRADED" in out or "NOT responding" in out
    b_status, b_detail = _bravo(out)
    # Header, footer and rows all derive from ONE fact.
    assert not_running == start_rnsd == (not running and not seen)
    assert degraded == (running and not seen)
    assert (b_status == "DOWN") == (not running and not seen)
    if answered:
        # An instance answered: nothing on screen may say it is absent.
        assert not not_running and not degraded and b_status != "DOWN"
        assert "no RNS instance running" not in out
    assert "rnsd is not running" not in out or (not running and seen)


@pytest.mark.parametrize("reading", ["si_only_blind", "si+halfread", "halfread_only", "si_only_rc1"])
def test_fable_r10_1_shared_instance_row_proves_an_instance(reading):
    out = _table(reading, running=False, si=False)                 # rnsd down
    assert "NOT RUNNING" not in out and "Start rnsd from Service Control." not in out
    assert "close RNS client apps" in out
    out = _table(reading, running=True, si=False)                  # rnsd up, probe missed
    assert "rnsd — shared instance available" in out and "DEGRADED" not in out


@pytest.mark.parametrize("reading", ["timeout", "oserror", "nobinary"])
def test_down_names_its_evidence_when_rnstatus_could_not_look(reading):
    """Fable r10 NIT: DOWN came from the probe while rnstatus could not
    look — the row says which evidence it rests on."""
    status, detail = _bravo(_table(reading, running=False, si=False))
    assert status == "DOWN"
    assert detail.startswith("no RNS instance running (shared-instance probe found none;")
