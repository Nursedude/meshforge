"""rns_interface_down_peer_reachable must CONFIRM before it claims a wedge.

2026-10-27/28: four "[RED] … restart rnsd" pages on moc3-gateway's
TCPInterface, each edge_up→edge_down in exactly one ~30 s tick (one was our
own drill restart). The 2026-05-30 incident the probe was built for held for
HOURS. A single Down tick is a reconnect, not a wedge — and the false page's
cure text is "restart rnsd", the #69 race trigger. So: N consecutive Down +
reachable observations of the SAME interface before the wedge signal, and the
blind moment in between reads `indeterminate`, never `clean`.
"""

from unittest.mock import patch

import pytest

from utils import watchdog_probes_rns_iface as wpr
from utils.watchdog_probes import probe_rns_interface_down_peer_reachable

_DOWN = (
    " TCPInterface[Regional RNS/192.0.2.38:4242]\n"
    "    Status    : Down\n"
    "    Mode      : Full\n"
)
_UP = (
    " TCPInterface[Regional RNS/192.0.2.38:4242]\n"
    "    Status    : Up\n"
    "    Mode      : Full\n"
)
_OTHER_DOWN = (
    " TCPInterface[Other RNS/192.0.2.99:4242]\n"
    "    Status    : Down\n"
    "    Mode      : Full\n"
)
_ENV = "MESHFORGE_RNS_IFACE_DOWN_CONFIRM_TICKS"


_RNSD = {"pid": 100}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Pins the knob AND rnsd's MainPID — the probe reads both from the box."""
    monkeypatch.delenv(_ENV, raising=False)
    _RNSD["pid"] = 100
    monkeypatch.setattr(wpr, "_resolve_main_pid_status",
                        lambda unit: ("ok", _RNSD["pid"]))
    wpr.reset_rns_iface_down_streaks()
    yield
    wpr.reset_rns_iface_down_streaks()


def _tick(text, reachable=True):
    with patch("utils.watchdog_probes_rns_iface._tcp_reachable", return_value=reachable):
        return probe_rns_interface_down_peer_reachable(rnstatus_text=text)


def _disp():
    from utils.watchdog_probe_core import collect_dispositions
    return collect_dispositions()["rns_interface_down_peer_reachable"]


def _reset_disp():
    from utils.watchdog_probe_core import reset_dispositions
    reset_dispositions()


def test_single_down_tick_does_not_page():
    """The measured false-page shape: one tick Down, next tick Up."""
    _reset_disp()
    assert _tick(_DOWN) is None
    got = _disp()
    assert got["disp"] == "indeterminate"
    assert "1 of 3" in got["reason"]
    assert "UNCONFIRMED" in got["reason"]
    _reset_disp()
    assert _tick(_UP) is None
    assert _disp()["disp"] == "clean"


def test_sustained_down_pages_on_the_nth_tick():
    assert _tick(_DOWN) is None
    assert _tick(_DOWN) is None
    sig = _tick(_DOWN)
    assert sig is not None
    assert sig.severity == "wedge"
    assert sig.extra["consecutive_down"] == 3
    assert sig.extra["confirm_ticks"] == 3
    # Still pages on every later tick while it holds (tracker de-dups).
    assert _tick(_DOWN) is not None


def test_recovery_resets_the_streak():
    _tick(_DOWN)
    _tick(_DOWN)
    _tick(_UP)
    assert _tick(_DOWN) is None
    assert _tick(_DOWN) is None
    assert _tick(_DOWN) is not None


def test_failed_peer_connect_holds_the_streak():
    """A failed 3 s connect is an unobserved tick, not a recovery: a lossy
    path must not reset a real wedge to zero forever (review 2026-10-28)."""
    _tick(_DOWN)
    _tick(_DOWN)
    assert _tick(_DOWN, reachable=False) is None
    assert _tick(_DOWN) is not None


def test_rnsd_restart_starts_the_streak_over():
    """The 18:29 drill shape: Down for 2 ticks, rnsd restarts, the
    reconnect reads Down for one tick — that must not page."""
    _tick(_DOWN)
    _tick(_DOWN)
    _RNSD["pid"] = 200
    assert _tick(_DOWN) is None
    assert _tick(_DOWN) is None
    assert _tick(_DOWN) is not None


def test_unresolvable_rnsd_pid_is_not_a_restart(monkeypatch):
    monkeypatch.setattr(wpr, "_resolve_main_pid_status",
                        lambda unit: ("unknown", None))
    _tick(_DOWN)
    _tick(_DOWN)
    assert _tick(_DOWN) is not None


def test_reason_names_the_longest_pending_streak():
    both = _DOWN + _OTHER_DOWN
    _reset_disp()
    _tick(_OTHER_DOWN)
    _reset_disp()
    _tick(both)
    got = _disp()
    assert "Other RNS" in got["reason"] and "2 of 3" in got["reason"]
    assert "+1 more" in got["reason"]


def test_streak_is_per_interface():
    """Two interfaces alternating Down must not add up to one wedge."""
    assert _tick(_DOWN) is None
    assert _tick(_OTHER_DOWN) is None
    assert _tick(_DOWN) is None
    assert _tick(_OTHER_DOWN) is None


def test_unobservable_tick_holds_the_streak():
    """rnstatus errored for one tick: we did not SEE the interface Up, so
    the streak is neither advanced nor cleared (absence of evidence)."""
    _tick(_DOWN)
    _tick(_DOWN)
    assert _tick("") is None
    assert _tick(_DOWN) is not None


def test_env_knob_retunes(monkeypatch):
    monkeypatch.setenv(_ENV, "1")
    assert _tick(_DOWN) is not None


def test_bad_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(_ENV, "0")
    assert _tick(_DOWN) is None
    assert _tick(_DOWN) is None
    assert _tick(_DOWN) is not None


def test_knob_uses_the_shared_parser():
    """hfm #5: one parser for every N-consecutive-ticks knob."""
    assert "_env_confirm_ticks" in wpr._iface_down_confirm_ticks.__code__.co_names
