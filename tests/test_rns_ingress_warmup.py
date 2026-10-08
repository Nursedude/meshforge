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
    b = types.SimpleNamespace(config=cfg, stats={}, _stats_lock=threading.Lock())
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
    for name in ("rns_ingress_warmup", "_rns_ingress_warmup_once", "rns_ingress_posture",
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
                       "failed": 0, "after": 3}
        line = [r.getMessage() for r in caplog.records if "warm-up" in r.getMessage()]
        assert line and "1/3 recallable in-process before, 3/3 after" in line[0]

    def test_the_line_measures_the_end_not_the_send(self, tmp_path, caplog):
        # rnsd ignores a request whose cached announce is gone: "sent" must
        # not read as cured (review W1)
        b = _host(tmp_path)
        b.learns = False
        with caplog.at_level(logging.INFO, logger=mix.logger.name):
            out = b.rns_ingress_warmup(settle_s=0)
        assert out["after"] == 1
        line = [r.getMessage() for r in caplog.records if "warm-up" in r.getMessage()][0]
        assert "1/3 after" in line and "still unknown:" in line and LISTED[:8] in line

    def test_a_tx_guard_block_is_blocked_not_failed(self, tmp_path):
        b = _host(tmp_path)
        b._rns_request_path = lambda h: "blocked"
        out = b.rns_ingress_warmup(settle_s=0)
        assert out["blocked"] == 2 and out["failed"] == 0

    def test_single_flight(self, tmp_path):
        b = _host(tmp_path)
        assert mix._WARMUP_LOCK.acquire(blocking=False)
        try:
            assert b.rns_ingress_warmup(settle_s=0) == {}
        finally:
            mix._WARMUP_LOCK.release()
        assert b.requested == []

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
        assert b.rns_ingress_warmup(settle_s=0)["failed"] == 2

    def test_the_connect_path_calls_it(self):
        from gateway.rns_bridge import RNSMeshtasticBridge
        assert "rns_ingress_warmup" in inspect.getsource(RNSMeshtasticBridge._rns_loop)


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
