"""RNS ingress warm-up — make listed/peer identities RECALLABLE in this process.

Measured 2026-10-08 on moc3: rnsd held a path (and so the announce) for the
MeshAnchor broadcast bridge 627f…, but the gateway process restarted at 08:20
never heard a fresh announce, so ``RNS.Identity.recall`` was None in-process
and LXMF delivered 627f's next message as ``source unknown (never
announced)``. Under ``bridge_source_policy: enforce`` that message is
refused — after EVERY gateway restart, until each peer next announces (5 min
for a gateway, 10 for the MA broadcast bridge, longer if one is dropped).

The cure asks the local rnsd for each listed/peer path at connect, and once
(rate-limited) when a member's claim arrives source-unknown. Neither changes
a decision: a path request is witness-keeping, never admission.
"""
from __future__ import annotations

import inspect
import logging
import threading
import types

import pytest

from gateway import bridge_rns_events_mixin as mix
from gateway.bridge_rns_events_mixin import BridgeRnsEventsMixin
from gateway.config import GatewayConfig, RNSConfig

LISTED = "1f68b5463f1b0e8bbac685c109bd1760"
KNOWN = "e599e89222fd28787d12b019345875b0"
PEER = "3dfbdb5d24c6de195ae4f3c0f56b5ea5"
STRANGER = "ab" * 16


@pytest.fixture(autouse=True)
def _no_ambient(monkeypatch, tmp_path):
    monkeypatch.setattr("gateway.lxmf_identity_registry.default_config_dir",
                        lambda: tmp_path / "no-registry")
    # run the nudge inline so a test can see it (production: daemon thread)
    monkeypatch.setattr(mix, "_spawn", lambda fn, *a: fn(*a))


def _host(tmp_path, policy="enforce", identities=(LISTED, KNOWN), known=(KNOWN,)):
    cfg = GatewayConfig()
    cfg.enabled = True
    cfg.rns = RNSConfig(bridge_source_identities=list(identities),
                        bridge_source_policy=policy,
                        peer_gateway_destinations=[PEER])
    b = types.SimpleNamespace(config=cfg, stats={}, _stats_lock=threading.Lock(),
                              _stop_event=threading.Event())
    b._peer_gateway_hash_set = lambda: {PEER}
    b._rns_ingress_ledger_path = tmp_path / "ledger.json"
    b.requested = []
    b.known = set(known)
    b.learns = True        # rnsd answers: a requested identity becomes recallable
    b._rns_recall_identity = lambda h: object() if h.hex() in b.known else None

    def _req(h):
        b.requested.append(h.hex())
        if b.learns:
            b.known.add(h.hex())
        return "sent"
    b._rns_request_path = _req
    for name in ("rns_ingress_warmup", "rns_ingress_posture",
                 "_rns_warmup_state", "_rns_recallable", "_rns_warmup_round",
                 "_rns_warmup_schedule", "_rns_warmup_wait",
                 "_rns_ingress_admits", "_rns_ingress_ledger",
                 "_lxmf_identity_registry", "_rns_ingress_path_nudge"):
        setattr(b, name, getattr(BridgeRnsEventsMixin, name).__get__(b))
    return b


class TestWarmupAtConnect:
    def test_requests_only_the_members_this_process_cannot_recall(self, tmp_path, caplog):
        b = _host(tmp_path)
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            out = b.rns_ingress_warmup(settle_s=0)
        assert sorted(b.requested) == sorted([LISTED, PEER])   # not KNOWN, never a stranger
        assert out == {"members": 3, "known": 1, "requested": 2, "blocked": 0,
                       "failed": 0, "after": 3, "retries": 0}
        line = [r.getMessage() for r in caplog.records if "warm-up" in r.getMessage()]
        assert line and "1/3 recallable in-process before, 3/3 after" in line[0]

    def test_the_line_measures_the_end_not_the_send(self, tmp_path, caplog):
        # rnsd ignores a request whose cached announce is gone: "sent" must
        # not read as cured (review W1)
        b = _host(tmp_path)
        b.learns = False
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=())
        assert out["after"] == 1
        line = [r.getMessage() for r in caplog.records if "warm-up" in r.getMessage()][0]
        assert "1/3 after" in line and "still unknown:" in line and LISTED[:8] in line

    def test_a_tx_guard_block_is_blocked_not_failed(self, tmp_path):
        b = _host(tmp_path)
        b._rns_request_path = lambda h: "blocked"
        out = b.rns_ingress_warmup(settle_s=0)
        assert out["blocked"] == 2 and out["failed"] == 0

    def test_a_call_while_one_runs_kicks_it_and_returns_at_once(self, tmp_path):
        # no thread ever parks behind a request_path bounded_call cannot abort
        b = _host(tmp_path)
        st = b._rns_warmup_state()
        st["active"] = True
        assert b.rns_ingress_warmup(settle_s=0) == {}
        assert st["kick"].is_set() and b.requested == []

    def test_open_policy_does_nothing(self, tmp_path):
        b = _host(tmp_path, policy="open", identities=())
        assert b.rns_ingress_warmup(settle_s=0) == {}
        assert b.requested == []

    def test_observe_warms_too(self, tmp_path):
        # observe is the soak that decides enforce; it must measure the
        # post-restart state enforce will live in.
        b = _host(tmp_path, policy="observe")
        assert b.rns_ingress_warmup(settle_s=0)["requested"] == 2

    def test_an_rnsd_error_is_counted_never_raised(self, tmp_path):
        b = _host(tmp_path)

        def boom(h):
            raise RuntimeError("rpc gone")
        b._rns_request_path = boom
        assert b.rns_ingress_warmup(settle_s=0, retry_delays_s=())["failed"] == 2

    def test_the_connect_path_calls_it(self):
        from gateway.rns_bridge import RNSMeshtasticBridge
        assert "rns_ingress_warmup" in inspect.getsource(RNSMeshtasticBridge._rns_loop)


class TestRetryUnknowns:
    """moc (the HUB), 2026-10-27: after an rnsd restart the one-shot warm-up
    fired 4 s after the TCP peers reconnected and recalled 9/15; a gateway
    restart a minute later read 15/15. rnsd could not answer YET, and nothing
    re-asked, so an enforcing allowlist refused the 6 until they announced."""

    @pytest.fixture(autouse=True)
    def clock(self, monkeypatch):
        """A fake monotonic clock that only Ev.wait advances: no test sleeps."""
        now = [1000.0]
        monkeypatch.setattr(mix, "_monotonic", lambda: now[0])
        return now

    def _ev(self, clock, on_wait=None, stop=False):
        waits = []

        class Ev:
            def wait(self, s):
                waits.append(s)
                clock[0] += s
                if on_wait:
                    on_wait(len(waits))
                return stop

            def is_set(self):
                return stop
        return Ev(), waits

    def _late_rnsd(self, b, answers_on):
        """rnsd learns a hash only on its Nth request (it had no path yet)."""
        seen = {}

        def _req(h):
            k = h.hex()
            b.requested.append(k)
            seen[k] = seen.get(k, 0) + 1
            if seen[k] >= answers_on:
                b.known.add(k)
            return "sent"
        b._rns_request_path = _req

    def _lines(self, caplog):
        return [r.getMessage() for r in caplog.records
                if "warm-up" in r.getMessage() and "already running" not in r.getMessage()]

    def test_an_unknown_is_re_requested_and_the_end_is_re_measured(self, tmp_path, caplog, clock):
        b = _host(tmp_path)
        b._stop_event, waits = self._ev(clock)
        self._late_rnsd(b, answers_on=2)
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=(30.0, 90.0, 300.0))
        assert sorted(b.requested) == sorted([LISTED, PEER] * 2)
        assert out["after"] == 3 and out["retries"] == 1 and out["requested"] == 4
        assert sum(waits) == 30.0                 # stopped once all were recalled
        lines = self._lines(caplog)
        assert len(lines) == 2
        assert "retry 1/3: 1/3 recallable in-process before, 3/3 after" in lines[1]

    def test_retries_are_bounded_and_the_last_line_names_who_is_left(self, tmp_path, caplog, clock):
        b = _host(tmp_path)
        b._stop_event, waits = self._ev(clock)
        b.learns = False
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=(30.0, 90.0))
        assert b.requested.count(LISTED) == 3 and b.requested.count(PEER) == 3
        assert out["retries"] == 2 and out["after"] == 1 and out["requested"] == 6
        assert sum(waits) == 120.0
        last = self._lines(caplog)[-1]
        assert "retry 2/2" in last and "still unknown:" in last and LISTED[:8] in last

    def test_a_member_that_announced_meanwhile_is_not_asked_again(self, tmp_path, clock):
        b = _host(tmp_path)
        b.learns = False
        b._stop_event, _ = self._ev(clock, on_wait=lambda n: b.known.add(PEER))
        out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=(30.0,))
        assert b.requested.count(PEER) == 1 and b.requested.count(LISTED) == 2
        assert out["after"] == 2

    def test_all_announced_meanwhile_reads_recalled_not_still_unknown(self, tmp_path, caplog, clock):
        b = _host(tmp_path)
        b.learns = False
        b._stop_event, _ = self._ev(clock, on_wait=lambda n: b.known.update({LISTED, PEER}))
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=(30.0, 90.0))
        last = self._lines(caplog)[-1]
        assert "3/3 after 0 path request(s)" in last and "still unknown" not in last
        assert out["after"] == 3

    def test_a_member_that_AGED_OUT_is_re_measured_and_re_asked(self, tmp_path, caplog, clock):
        # review 3 #8: every round measures ALL members, not last round's unknown
        b = _host(tmp_path)
        b.learns = False

        def _age(n):
            b.known.discard(KNOWN)
        b._stop_event, _ = self._ev(clock, on_wait=_age)
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=(30.0,))
        assert KNOWN in b.requested and out["after"] == 0
        assert "0/3 recallable in-process before" in self._lines(caplog)[-1]

    def test_stop_ends_the_retries(self, tmp_path, clock):
        b = _host(tmp_path)
        b.learns = False
        b._stop_event, _ = self._ev(clock, stop=True)
        out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=(30.0, 90.0))
        assert b.requested.count(LISTED) == 1 and out["retries"] == 0

    def test_nothing_to_retry_when_the_first_pass_recalled_all(self, tmp_path, clock):
        b = _host(tmp_path)
        b._stop_event, waits = self._ev(clock)
        out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=(30.0, 90.0))
        assert out["retries"] == 0 and len(b.requested) == 2 and waits == []

    def test_a_failed_request_is_retried_a_blocked_one_is_not(self, tmp_path, clock):
        b = _host(tmp_path)
        b._stop_event, _ = self._ev(clock)
        calls = []

        def _req(h):
            calls.append(h.hex())
            if h.hex() == PEER:
                raise RuntimeError("rnsd transport not ready")
            return "blocked"
        b._rns_request_path = _req
        out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=(30.0, 90.0))
        assert calls.count(PEER) == 3 and calls.count(LISTED) == 1
        assert out["failed"] == 3 and out["blocked"] == 1 and out["after"] == 1

    def test_all_blocked_means_no_retry(self, tmp_path, clock):
        b = _host(tmp_path)
        b._stop_event, waits = self._ev(clock)
        b._rns_request_path = lambda h: "blocked"
        out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=(30.0, 90.0))
        assert out["retries"] == 0 and waits == []

    def test_a_retry_line_counts_every_member_not_the_retry_subset(self, tmp_path, caplog, clock):
        b = _host(tmp_path, identities=(LISTED, KNOWN, STRANGER))
        b._stop_event, _ = self._ev(clock)
        seen = {}

        def _req(h):
            k = h.hex()
            if k == STRANGER:
                return "blocked"
            seen[k] = seen.get(k, 0) + 1
            if seen[k] >= 2:
                b.known.add(k)
            return "sent"
        b._rns_request_path = _req
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=(30.0, 90.0))
        lines = self._lines(caplog)
        assert "retry 1/2: 1/4 recallable in-process before, 3/4 after" in lines[1]
        assert "still unknown: " + STRANGER[:8] in lines[1]
        assert out["after"] == 3 and out["retries"] == 1

    def test_shutdown_during_settle_reports_after_as_unmeasured(self, tmp_path, caplog, clock):
        b = _host(tmp_path)
        b.learns = False
        b._stop_event, _ = self._ev(clock, stop=True)
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            out = b.rns_ingress_warmup(settle_s=5, retry_delays_s=(30.0,))
        lines = self._lines(caplog)
        assert len(lines) == 1 and "NOT re-measured" in lines[0]
        assert out["after"] is None and out["retries"] == 0

    def test_no_stop_event_means_no_retry_burst(self, tmp_path):
        b = _host(tmp_path)
        b.learns = False
        del b._stop_event
        out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=(30.0, 90.0))
        assert len(b.requested) == 2 and out["retries"] == 0

    def test_a_reconnect_during_a_wait_kicks_a_fresh_pass_now(self, tmp_path, caplog, clock):
        # reviews 1-3: a reconnect must neither be dropped nor park a thread
        b = _host(tmp_path)
        b.learns = False
        got = []
        b._stop_event, waits = self._ev(
            clock, on_wait=lambda n: got.append(b.rns_ingress_warmup()) if n == 1 else None)
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=(300.0, 300.0))
        assert got == [{}]                           # the reconnect returned at once
        assert waits[0] == 1.0                       # kick seen within one slice, not 300 s
        lines = self._lines(caplog)
        assert "(reconnect)" in lines[1]
        assert out["retries"] == 3                   # kick pass + schedule restarted (2)
        assert not b._rns_warmup_state()["active"]

    def test_a_kick_as_the_schedule_ends_is_not_lost(self, tmp_path, caplog):
        b = _host(tmp_path)
        b.learns = False
        n = [0]

        def _req(h):
            b.requested.append(h.hex())
            n[0] += 1
            if n[0] == 1:
                assert b.rns_ingress_warmup() == {}  # reconnect mid-pass
            return "sent"
        b._rns_request_path = _req
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            b.rns_ingress_warmup(settle_s=0, retry_delays_s=())
        assert len(self._lines(caplog)) == 2       # ran again for the kick
        assert not b._rns_warmup_state()["active"]

    def test_retries_end_when_rns_disconnects(self, tmp_path, caplog, clock):
        b = _host(tmp_path)
        b.learns = False
        b._connected_rns = True

        def _drop(n):
            b._connected_rns = False
        b._stop_event, _ = self._ev(clock, on_wait=_drop)
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            out = b.rns_ingress_warmup(settle_s=0, retry_delays_s=(30.0, 90.0))
        assert len(b.requested) == 2 and out["retries"] == 0
        assert "RNS disconnected" in self._lines(caplog)[-1]

    def test_a_kick_during_shutdown_sends_nothing(self, tmp_path, clock):
        b = _host(tmp_path)
        b.learns = False
        st = b._rns_warmup_state()
        b._stop_event, _ = self._ev(clock, on_wait=lambda n: st["kick"].set(), stop=True)
        b.rns_ingress_warmup(settle_s=0, retry_delays_s=(30.0, 90.0))
        assert len(b.requested) == 2                 # first pass only, no (reconnect) round
        assert not st["active"]

    def test_a_kick_landing_before_a_round_is_absorbed_by_it(self, tmp_path, caplog, clock):
        # review 4 #4: a kick set as a wait times out must not cause a 2nd burst
        b = _host(tmp_path)
        b.learns = False
        st = b._rns_warmup_state()
        b._stop_event, waits = self._ev(
            clock, on_wait=lambda n: st["kick"].set() if n == 1 else None)
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            b.rns_ingress_warmup(settle_s=0, retry_delays_s=(1.0, 90.0))
        lines = self._lines(caplog)
        assert "(reconnect)" in lines[1]
        assert b.requested.count(LISTED) == 1 + 1 + 2   # first, reconnect, 2 retries; no extra

    def test_an_absorbed_kick_is_visible_with_the_running_age(self, tmp_path, caplog, clock):
        b = _host(tmp_path)
        st = b._rns_warmup_state()
        st["active"], st["since"] = True, clock[0] - 400
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            assert b.rns_ingress_warmup() == {}
        assert any("already running (400s)" in r.getMessage() for r in caplog.records)

    def test_an_exception_never_escapes_and_frees_the_slot(self, tmp_path):
        b = _host(tmp_path)

        def boom():
            raise RuntimeError("posture read failed")
        b.rns_ingress_posture = boom
        assert b.rns_ingress_warmup(settle_s=0) == {}
        assert not b._rns_warmup_state()["active"]

    def test_the_default_schedule_is_bounded(self):
        assert 1 <= len(mix.INGRESS_WARMUP_RETRY_S) <= 5
        assert sum(mix.INGRESS_WARMUP_RETRY_S) <= 900   # gives up inside ~one announce interval


class TestNudgeOnSourceUnknown:
    def _claim(self, b, src, reason=0x01):
        return b._rns_ingress_admits(src, signature_validated=False,
                                     unverified_reason=reason)

    def test_a_member_arriving_source_unknown_gets_one_path_request(self, tmp_path, monkeypatch):
        b = _host(tmp_path)
        t = [1000.0]
        monkeypatch.setattr(mix, "_monotonic", lambda: t[0])
        assert self._claim(b, LISTED) is False          # still refused: decision unchanged
        assert b.requested == [LISTED]
        t[0] += 30
        self._claim(b, LISTED)
        assert b.requested == [LISTED]                  # rate-limited
        t[0] += mix.INGRESS_PATH_NUDGE_S
        self._claim(b, LISTED)
        assert b.requested == [LISTED, LISTED]

    def test_a_peer_arriving_source_unknown_is_nudged_too(self, tmp_path, monkeypatch):
        b = _host(tmp_path)
        monkeypatch.setattr(mix, "_monotonic", lambda: 5.0)
        self._claim(b, PEER)
        assert b.requested == [PEER]

    def test_an_invalid_signature_is_never_nudged(self, tmp_path):
        # a path cannot fix a forged signature; asking would be work for a forger
        b = _host(tmp_path)
        self._claim(b, LISTED, reason=0x02)
        assert b.requested == []

    def test_a_stranger_is_never_nudged(self, tmp_path):
        b = _host(tmp_path)
        self._claim(b, STRANGER)
        assert b.requested == []


class TestTheRealRnsCalls:
    """The helpers above are stubbed; these run the REAL ones against a fake
    RNS module (review S3: a helper that let TransmitBlocked escape, or asked
    recall the RPC-making way, passed everything else)."""

    @pytest.fixture
    def fake_rns(self, monkeypatch):
        calls = types.SimpleNamespace(recall=[], request=[])
        rns = types.ModuleType("RNS")
        rns.Identity = types.SimpleNamespace(
            recall=lambda h, **kw: calls.recall.append((h, kw)) or None)
        rns.Transport = types.SimpleNamespace(
            request_path=lambda h: calls.request.append(h))
        monkeypatch.setitem(__import__("sys").modules, "RNS", rns)
        return calls

    def test_recall_asks_the_way_lxmf_does_no_rpc(self, fake_rns):
        BridgeRnsEventsMixin._rns_recall_identity(None, bytes.fromhex(LISTED))
        assert fake_rns.recall == [(bytes.fromhex(LISTED), {"_no_use": True})]

    def test_request_path_sends_when_the_guard_allows(self, fake_rns, monkeypatch):
        monkeypatch.setattr("utils.tx_guard.assert_rns_tx_allowed", lambda **k: None)
        r = BridgeRnsEventsMixin._rns_request_path(None, bytes.fromhex(LISTED))
        assert r == "sent" and fake_rns.request == [bytes.fromhex(LISTED)]

    def test_an_armed_guard_blocks_and_nothing_escapes(self, fake_rns, monkeypatch):
        from utils.tx_guard import TransmitBlocked

        def refuse(**k):
            raise TransmitBlocked("armed")
        monkeypatch.setattr("utils.tx_guard.assert_rns_tx_allowed", refuse)
        r = BridgeRnsEventsMixin._rns_request_path(None, bytes.fromhex(LISTED))
        assert r == "blocked" and fake_rns.request == []
