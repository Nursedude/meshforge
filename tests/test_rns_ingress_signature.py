"""The allowlist must trust a VALIDATED signature, never a claimed hash.

Found 2026-10-07 reading the pinned fork the gateway imports (LXMF
1.0.1+mf.5): ``LXMessage.unpack_from_bytes`` sets ``signature_validated``
False with ``unverified_reason`` SIGNATURE_INVALID (forged/garbled source
field) or SOURCE_UNKNOWN (never announced), and ``LXMRouter.lxmf_delivery``
STILL hands the message to our delivery callback. The ingress gate read only
``source_hash`` — so anyone who knows an allowlisted hash (announces publish
them) could claim it and be "listed". Under enforce that is a bypass.

Rule pinned here: listed / peer_gateway REQUIRE ``signature_validated is
True``. Absent attribute = unverified (fail closed: a caller that forgets the
flag must not open the gate).
"""
from __future__ import annotations

import logging
import threading
import types
from queue import Queue

import pytest

from gateway import rns_ingress_policy as pol
from gateway.bridge_rns_events_mixin import BridgeRnsEventsMixin
from gateway.config import GatewayConfig, RNSConfig

LISTED = "1f68b5463f1b0e8bbac685c109bd1760"
PEER = "3dfbdb5d" + "0" * 24
STRANGER = "627fa566" + "f" * 24

SOURCE_UNKNOWN = 0x01      # LXMF.LXMessage.SOURCE_UNKNOWN
SIGNATURE_INVALID = 0x02   # LXMF.LXMessage.SIGNATURE_INVALID


class _Tracker:
    def add_node(self, node):
        pass


def _bridge(tmp_path, policy):
    cfg = GatewayConfig()
    cfg.enabled = True
    cfg.rns = RNSConfig(bridge_source_identities=[LISTED],
                        bridge_source_policy=policy,
                        peer_gateway_destinations=[PEER])
    b = types.SimpleNamespace()
    b.config = cfg
    b.stats = {"errors": 0}
    b._stats_lock = threading.Lock()
    b._rns_to_mesh_queue = Queue()
    b._oracle_rns = None
    b.node_tracker = _Tracker()
    b._notify_message = lambda m: None
    b._router = types.SimpleNamespace(should_bridge=lambda m: True)
    b._peer_gateway_hash_set = lambda: {PEER}
    b._rns_ingress_ledger_path = tmp_path / "ledger.json"
    b._lxmf_identity_registry_dir = tmp_path
    for name in ("_on_lxmf_receive", "_rns_ingress_ledger",
                 "rns_ingress_posture", "_rns_ingress_admits",
                 "_lxmf_identity_registry"):
        setattr(b, name, getattr(BridgeRnsEventsMixin, name).__get__(b))
    return b


_ABSENT = object()


def _msg(src, validated=_ABSENT, reason=None):
    m = types.SimpleNamespace(source_hash=bytes.fromhex(src), content=b"hi",
                              title="", stamp=None, fields={})
    if validated is not _ABSENT:
        m.signature_validated = validated
        m.unverified_reason = reason
    return m


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    monkeypatch.setattr("gateway.rns_bridge.HAS_RNS_SNIFFER", False)
    monkeypatch.setattr("commands.messaging.store_incoming", lambda **kw: None)


class TestVerdict:
    def test_validated_listed_is_listed(self):
        assert pol.verdict(LISTED, [LISTED], [], signature_validated=True) == "listed"

    def test_unvalidated_claim_of_a_listed_hash_is_unverified(self):
        assert pol.verdict(LISTED, [LISTED], [], signature_validated=False) == "unverified"

    def test_unvalidated_claim_of_a_peer_is_unverified(self):
        assert pol.verdict(PEER, [], [PEER], signature_validated=False) == "unverified"

    def test_unvalidated_stranger_is_still_unlisted(self):
        assert pol.verdict(STRANGER, [LISTED], [], signature_validated=False) == "unlisted"

    def test_default_is_fail_closed(self):
        assert pol.verdict(LISTED, [LISTED], []) == "unverified"


class TestEnforce:
    def test_forged_listed_hash_is_refused(self, tmp_path, caplog):
        caplog.set_level(logging.INFO, logger="gateway.bridge_rns_events_mixin")
        b = _bridge(tmp_path, "enforce")
        b._on_lxmf_receive(_msg(LISTED, False, SIGNATURE_INVALID))
        assert b._rns_to_mesh_queue.qsize() == 0
        assert b.stats["rns_to_mesh_refused_unlisted"] == 1
        line = [r.getMessage() for r in caplog.records
                if "RNS ingress" in r.getMessage()]
        assert len(line) == 1
        assert "signature invalid" in line[0] and LISTED in line[0]

    def test_unknown_source_claiming_listed_is_refused(self, tmp_path, caplog):
        caplog.set_level(logging.INFO, logger="gateway.bridge_rns_events_mixin")
        b = _bridge(tmp_path, "enforce")
        b._on_lxmf_receive(_msg(LISTED, False, SOURCE_UNKNOWN))
        assert b._rns_to_mesh_queue.qsize() == 0
        assert any("source unknown" in r.getMessage() for r in caplog.records)

    def test_missing_flag_is_refused_not_trusted(self, tmp_path):
        b = _bridge(tmp_path, "enforce")
        b._on_lxmf_receive(_msg(LISTED))          # no signature_validated attr
        assert b._rns_to_mesh_queue.qsize() == 0

    def test_validated_listed_and_peer_pass(self, tmp_path):
        b = _bridge(tmp_path, "enforce")
        b._on_lxmf_receive(_msg(LISTED, True))
        b._on_lxmf_receive(_msg(PEER, True))
        assert b._rns_to_mesh_queue.qsize() == 2

    def test_ledger_marks_the_claim_unverified(self, tmp_path):
        b = _bridge(tmp_path, "observe")
        b._on_lxmf_receive(_msg(LISTED, False, SIGNATURE_INVALID))
        assert b._rns_to_mesh_queue.qsize() == 1      # observe still bridges
        ent = b._rns_ingress_ledger().snapshot()["unlisted_recent"][0]
        assert ent["hash"] == LISTED
        assert ent.get("unverified") == 1
