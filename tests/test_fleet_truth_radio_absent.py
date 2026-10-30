"""A box whose role declares NO radio reads its radio cell absent (2026-10-30).

meshforge-lab is a bench box: its role declares ``meshtasticd: absent`` (the
run script opens the experiment's USB radios directly). The day it got a NOC
read grant, /fleet stopped reading it DARK and rolled it up FAILED on "radio
not connected", which flipped the whole fleet headline to failed for a radio
that is not there by declaration.

The box now judges its radio from its own role (``role.expected.radio``) and
the builder treats ``radio_expected is False`` the way it treats a declared
absent watchdog: the cell is ``absent`` and stops tainting. Never inferred
from silence: None keeps the old FAILED. Shared with MeshAnchor
byte-for-byte, like ``fleet_truth.py``.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils import fleet_truth as ft  # noqa: E402

NOW = 1_000_000.0


def _lab_snap(*, radio_expected=False, radio=None, method="ssh_spool"):
    """The lab as the spool reports it: watchdog + mini healthy, no HTTP
    surface by role, and the radio probe's answer (not listening)."""
    if radio is None:
        radio = {"connected": False, "mode": None}
    slo = {"radio": radio} if radio is not False else None
    return {"alias": "meshforge-lab", "resolution_method": method,
            "answered_at": NOW, "error": None,
            "http_surface_expected": False, "watchdog_expected": True,
            "radio_expected": radio_expected,
            "status": {"app": {"name": "meshforge", "role": "lab"},
                       "watchdog": {"installed": True, "ok": True, "signals": []},
                       "mini_dudeai": {"installed": True, "ok": True}},
            "slo": slo}


def _box(snap):
    t = ft.build_fleet_truth([snap], now=NOW, signal_classes=[], noc_host="moc")
    return t, t["boxes"][0]


def test_a_declared_radio_less_box_reads_absent_not_failed():
    t, b = _box(_lab_snap())
    r = b["subsystems"]["radio"]
    assert r["state"] == ft.DARK and r["absent"] is True
    assert "declared role runs no radio" in r["reason"]
    assert b["box_state"] != ft.FAILED, "an absent-by-design radio failed the box"


def test_an_unobservable_radio_on_a_radio_less_box_is_absent_too():
    _t, b = _box(_lab_snap(radio=False))
    r = b["subsystems"]["radio"]
    assert r["state"] == ft.DARK and r["absent"] is True


def test_undecided_still_fails():
    """THE anti-silence guard: None (an export older than the verdict, or a
    role silent about the radio) must keep "not connected" a failure."""
    _t, b = _box(_lab_snap(radio_expected=None))
    r = b["subsystems"]["radio"]
    assert r["state"] == ft.FAILED
    assert not r.get("absent")
    assert b["box_state"] == ft.FAILED


def test_a_radio_that_is_actually_connected_is_left_healthy():
    """A radio the role says is not there, but which IS connected, is an
    observation: report it, never paint it absent."""
    _t, b = _box(_lab_snap(radio={"connected": True, "mode": "usb"}))
    r = b["subsystems"]["radio"]
    assert r["state"] == ft.HEALTHY and not r.get("absent")


def test_a_radio_box_is_untouched():
    """A box whose role expects its radio keeps "not connected" a failure."""
    _t, b = _box(_lab_snap(radio_expected=True))
    assert b["subsystems"]["radio"]["state"] == ft.FAILED


# ── reachability names its real transport (provenance 2026-10-14) ────────

def test_a_spool_read_box_names_the_spool_as_its_source():
    _t, b = _box(_lab_snap(method="ssh_spool"))
    assert b["reachable"]["source"] == "ssh_spool"


def test_a_direct_fanout_box_still_says_fanout():
    _t, b = _box(_lab_snap(method="dns"))
    assert b["reachable"]["source"] == "fanout"


def test_a_dark_box_says_fanout():
    snap = {"alias": "x", "resolution_method": "dns", "answered_at": None,
            "error": "no response from x (dns)", "status": None, "slo": None}
    _t, b = _box(snap)
    assert b["reachable"]["state"] == ft.DARK
    assert b["reachable"]["source"] == "fanout"
