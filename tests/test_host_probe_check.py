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


@pytest.fixture
def hpc():
    sys.path.insert(0, SCRIPTS)
    try:
        import host_probe_check
        yield host_probe_check
    finally:
        if SCRIPTS in sys.path:
            sys.path.remove(SCRIPTS)


def _reply(ip_alive, app="open", banner=43, kstack=0):
    return {"ok": True, "result": (f"host_probe h: ip_alive={ip_alive} "
                                   f"app22={app} banner={banner}B "
                                   f"kstack={kstack} rtt_ms=5")}


def _fake_req(replies):
    """Fake NATS req: device (parsed from the subject) -> canned reply dict, OR
    an Exception instance to RAISE (a dark claw / transport failure)."""
    def req(srv, subj, payload, *, timeout_s):
        dev = subj.split(".tool_exec")[0]
        r = replies.get(dev)
        if isinstance(r, Exception):
            raise r
        return r
    return req


_TARGET = {"name": "t", "host": "10.0.0.9", "app_port": 22, "closed_port": 9}


class TestResolveWitnesses:
    def test_explicit_ordered_list(self, hpc):
        assert hpc._resolve_witnesses({"witness": ["a", "b"]}, "d") == ["a", "b"]

    def test_default_when_absent(self, hpc):
        assert hpc._resolve_witnesses({}, "dudeclaw-01") == ["dudeclaw-01"]

    def test_empty_list_falls_back_to_default(self, hpc):
        assert hpc._resolve_witnesses({"witness": []}, "d") == ["d"]


class TestWitnessFailover:
    """Honest failover across per-target witness claws (2026-07-22). The two
    fleet claws sit on disjoint subnets, so a failover claw that can't route to
    a target must NEVER manufacture a 'host down' — that is the row-7 /
    honest_failure_modes #1 trap this design exists to avoid."""

    def _run(self, hpc, witnesses, replies):
        return hpc._probe_target_with_failover(
            _fake_req(replies), "s", witnesses, _TARGET, 1, attempts=1)

    def test_primary_ok_no_failover(self, hpc):
        r = self._run(hpc, ["c1", "c2"], {"c1": _reply(1)})
        assert r["verdict"] == "OK"
        assert r["witness_device"] == "c1" and r["failover"] is False

    def test_primary_unreachable_is_authoritative(self, hpc):
        # the primary claw HAS a route, so its UNREACHABLE is a real host-down;
        # do NOT fail over to a claw whose 'up' would mask it
        r = self._run(hpc, ["c1", "c2"], {"c1": _reply(0), "c2": _reply(1)})
        assert r["verdict"] == "UNREACHABLE"
        assert r["witness_device"] == "c1" and r["failover"] is False

    def test_primary_dark_failover_confirms_up(self, hpc):
        r = self._run(hpc, ["c1", "c2"],
                      {"c1": ConnectionError("dark"), "c2": _reply(1)})
        assert r["verdict"] == "OK"
        assert r["witness_device"] == "c2" and r["failover"] is True

    def test_primary_dark_failover_unreachable_is_UNKNOWN_not_down(self, hpc):
        # THE honest test: c2 can't route → ip_alive=0 → must read UNKNOWN,
        # never UNREACHABLE (that would be a false host-down page)
        r = self._run(hpc, ["c1", "c2"],
                      {"c1": ConnectionError("dark"), "c2": _reply(0)})
        assert r["verdict"] == "UNKNOWN"
        assert r["failover"] is True and "routing gap" in r.get("note", "")

    def test_primary_dark_failover_frozen_is_definitive(self, hpc):
        # HOST_FROZEN needs ip_alive=1, so the failover claw DID reach it
        r = self._run(hpc, ["c1", "c2"],
                      {"c1": ConnectionError("dark"),
                       "c2": _reply(1, banner=0, kstack=1)})
        assert r["verdict"] == "HOST_FROZEN" and r["failover"] is True

    def test_primary_dark_no_failover_is_unknown(self, hpc):
        r = self._run(hpc, ["c1"], {"c1": ConnectionError("dark")})
        assert r["verdict"] == "UNKNOWN" and r["failover"] is False
        assert r["error"] is not None

    def test_all_claws_dark_is_unknown_records_attempts(self, hpc):
        r = self._run(hpc, ["c1", "c2"],
                      {"c1": ConnectionError("x"), "c2": ConnectionError("y")})
        assert r["verdict"] == "UNKNOWN" and r["failover"] is True
        assert r["error"] is not None and set(r["attempted"]) == {"c1", "c2"}
