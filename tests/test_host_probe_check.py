"""host_probe_check._verdict — kstack-corroborated freeze verdict.

The dude-claw witness (Leg C) probes a target's SSH port for a banner. banner==0
ALONE is ambiguous — a swap-wedged userspace (real freeze) AND a merely loaded
box both miss the banner window. The .32 Pi Zero W (running two mesh bots)
false-fired HOST_FROZEN for days this way (2026-06-23), surfacing as an
mf5_soak_watch FAIL on a healthy box. The fix corroborates banner==0 with the
kernel hung-task signal (kstack==1) before claiming a freeze.

This is invariant 2 of the recurring map-wedge class — a detector must never map
one ambiguous observation onto a definitive verdict (honest_failure_modes #1).
"""

import os
import sys

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), '..', 'scripts')


@pytest.fixture
def verdict():
    sys.path.insert(0, SCRIPTS)
    try:
        import host_probe_check
        yield host_probe_check._verdict
    finally:
        if SCRIPTS in sys.path:
            sys.path.remove(SCRIPTS)


def _fields(**kw):
    """A probe-fields dict; healthy defaults, override per case."""
    base = {"ip_alive": 1, "app_state": "open", "banner": 43,
            "kstack": 0, "rtt_ms": 5}
    base.update(kw)
    return base


class TestVerdictKstackCorroboration:
    def test_banner0_kstack1_is_frozen(self, verdict):
        # no banner AND the kernel flagged a hung task = the genuine freeze
        assert verdict(_fields(banner=0, kstack=1), True) == "HOST_FROZEN"

    def test_banner0_kstack0_is_ok_not_frozen(self, verdict):
        # THE bug: loaded box, kernel healthy, slow banner — must NOT page
        assert verdict(_fields(banner=0, kstack=0), True) == "OK"

    def test_banner0_kstack_missing_preserves_frozen(self, verdict):
        # can't corroborate (older claw tool) → conservative: don't miss a freeze
        assert verdict(_fields(banner=0, kstack=None), True) == "HOST_FROZEN"

    def test_banner_present_is_ok_regardless_of_kstack(self, verdict):
        assert verdict(_fields(banner=43, kstack=0), True) == "OK"
        assert verdict(_fields(banner=43, kstack=1), True) == "OK"

    def test_ip_dead_is_unreachable(self, verdict):
        assert verdict(_fields(ip_alive=0), True) == "UNREACHABLE"

    def test_blind_collector_is_unknown(self, verdict):
        # lost visibility is never read as OK
        assert verdict(_fields(), False) == "UNKNOWN"

    def test_ip_alive_none_is_unknown(self, verdict):
        assert verdict(_fields(ip_alive=None), True) == "UNKNOWN"

    def test_closed_app_port_with_banner0_is_ok(self, verdict):
        # banner==0 only matters when the port is actually open
        assert verdict(_fields(app_state="closed", banner=0, kstack=0), True) == "OK"
