"""LXMF identity registry: every hash we print carries a name and a purpose.

Operator 2026-10-07: "name their purpose". The allowlist says WHO MAY bridge;
the registry says WHO IT IS. They are separate on purpose: a NAMED identity
that is not listed is a known stranger, an UNNAMED one is unknown, and the
surfaces must tell those apart. An unreadable registry is its own state —
never "everyone is unknown" (hfm #1: the degraded value must not overlap the
healthy domain).
"""
from __future__ import annotations

import json
import logging
import threading
import types
from queue import Queue

import pytest

from gateway import lxmf_identity_registry as reg
from gateway.bridge_rns_events_mixin import BridgeRnsEventsMixin
from gateway.config import GatewayConfig, RNSConfig
from gateway import message_routing as mr

ECHO = "1f68b5463f1b0e8bbac685c109bd1760"
BRIDGE = "627fa566" + "a" * 24
STRANGER = "58cecbd0" + "f" * 24
PEER = "3dfbdb5d" + "0" * 24


def _write(d, identities, **extra):
    doc = {"schema": reg.SCHEMA, "identities": identities}
    doc.update(extra)
    (d / reg.REGISTRY_FILENAME).write_text(json.dumps(doc))


# ── loading ─────────────────────────────────────────────────────────────

class TestLoad:
    def test_absent_is_absent_not_error(self, tmp_path):
        r = reg.load_registry(tmp_path)
        assert r.state == reg.STATE_ABSENT
        assert r.lookup(ECHO) is None
        assert r.label(ECHO) == f"unknown:{ECHO[:8]}"

    def test_declared_entry(self, tmp_path):
        _write(tmp_path, {BRIDGE: {"name": "MA broadcast bridge",
                                   "purpose": "MeshCore ch1 -> RNS"}})
        r = reg.load_registry(tmp_path)
        assert r.state == reg.STATE_OK
        e = r.lookup(BRIDGE.upper())
        assert e.name == "MA broadcast bridge" and e.source == "registry"
        assert r.label(BRIDGE) == "MA broadcast bridge"
        assert r.describe(BRIDGE) == (
            f"MA broadcast bridge — MeshCore ch1 -> RNS [{BRIDGE[:8]}]")

    def test_unreadable_is_its_own_state(self, tmp_path):
        (tmp_path / reg.REGISTRY_FILENAME).write_text("{not json")
        r = reg.load_registry(tmp_path)
        assert r.state == reg.STATE_UNREADABLE
        assert r.error
        # never the healthy "unknown" — the reader must see it is BLIND
        assert "registry unreadable" in r.label(ECHO)
        assert "registry unreadable" in r.describe(ECHO)

    def test_lab_peers_is_a_fallback(self, tmp_path):
        (tmp_path / "lab_peers").write_text(
            "# comment\nbox-a=" + ECHO + "\nbad line\n")
        r = reg.load_registry(tmp_path)
        e = r.lookup(ECHO)
        assert e.name == "lab-echo (box-a)" and e.source == "lab_peers"

    def test_registry_overrides_lab_peers(self, tmp_path):
        (tmp_path / "lab_peers").write_text("box-a=" + ECHO + "\n")
        _write(tmp_path, {ECHO: {"name": "moc1 echo", "purpose": "probe"}})
        assert reg.load_registry(tmp_path).lookup(ECHO).name == "moc1 echo"

    def test_malformed_entries_are_named_not_absorbed(self, tmp_path):
        _write(tmp_path, {"nothex": {"name": "x", "purpose": "y"},
                          ECHO: {"purpose": "no name"},
                          BRIDGE: "just a string"})
        r = reg.load_registry(tmp_path)
        assert r.state == reg.STATE_OK
        assert r.lookup(ECHO) is None and r.lookup(BRIDGE) is None
        assert len(r.problems) == 3
        assert any("nothex" in p for p in r.problems)

    def test_wrong_top_level_shape_is_unreadable(self, tmp_path):
        (tmp_path / reg.REGISTRY_FILENAME).write_text(json.dumps(
            {"identities": [ECHO]}))
        assert reg.load_registry(tmp_path).state == reg.STATE_UNREADABLE

    def test_unnamed_lists_what_needs_a_name(self, tmp_path):
        _write(tmp_path, {BRIDGE: {"name": "b", "purpose": "p"}})
        r = reg.load_registry(tmp_path)
        assert r.unnamed([BRIDGE, STRANGER]) == [STRANGER]

    def test_purpose_optional_but_name_required(self, tmp_path):
        _write(tmp_path, {BRIDGE: {"name": "b"}})
        r = reg.load_registry(tmp_path)
        assert r.describe(BRIDGE) == f"b — purpose not declared [{BRIDGE[:8]}]"


class TestReload:
    def test_edit_is_picked_up_without_restart(self, tmp_path):
        clock = {"t": 0.0}
        cache = reg.RegistryCache(tmp_path, now_fn=lambda: clock["t"],
                                  min_interval_s=5)
        assert cache.get().lookup(BRIDGE) is None
        _write(tmp_path, {BRIDGE: {"name": "b", "purpose": "p"}})
        clock["t"] = 10.0
        assert cache.get().lookup(BRIDGE).name == "b"


# ── surfaces ────────────────────────────────────────────────────────────

class _Tracker:
    def add_node(self, node):
        pass


def _fake_bridge(tmp_path, identities, policy):
    cfg = GatewayConfig()
    cfg.enabled = True
    cfg.rns = RNSConfig(bridge_source_identities=identities,
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


def _lxmf(source_hex):
    return types.SimpleNamespace(source_hash=bytes.fromhex(source_hex),
                                 content=b"hello", title="", stamp=None,
                                 fields={}, signature_validated=True,
                                 unverified_reason=None)


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    monkeypatch.setattr("gateway.rns_bridge.HAS_RNS_SNIFFER", False)
    monkeypatch.setattr("commands.messaging.store_incoming",
                        lambda **kw: None)


def _ingress_lines(caplog):
    return [r.getMessage() for r in caplog.records
            if "RNS ingress" in r.getMessage()]


class TestIngressNamesTheSender:
    def test_unknown_stranger_reads_unknown(self, tmp_path, caplog):
        caplog.set_level(logging.INFO, logger="gateway.bridge_rns_events_mixin")
        _write(tmp_path, {ECHO: {"name": "moc1 echo", "purpose": "probe"}})
        b = _fake_bridge(tmp_path, [ECHO], "observe")
        b._on_lxmf_receive(_lxmf(STRANGER))
        (line,) = _ingress_lines(caplog)
        assert STRANGER in line and "UNKNOWN identity" in line
        ent = b._rns_ingress_ledger().snapshot()["unlisted_recent"][0]
        assert ent["label"] == f"unknown:{STRANGER[:8]}"

    def test_named_but_unlisted_is_a_known_stranger(self, tmp_path, caplog):
        caplog.set_level(logging.INFO, logger="gateway.bridge_rns_events_mixin")
        _write(tmp_path, {BRIDGE: {"name": "MA broadcast bridge",
                                   "purpose": "MeshCore ch1 -> RNS"}})
        b = _fake_bridge(tmp_path, [ECHO], "observe")
        b._on_lxmf_receive(_lxmf(BRIDGE))
        (line,) = _ingress_lines(caplog)
        assert "MA broadcast bridge — MeshCore ch1 -> RNS" in line
        ent = b._rns_ingress_ledger().snapshot()["unlisted_recent"][0]
        assert ent["label"] == "MA broadcast bridge"

    def test_label_follows_a_later_naming(self, tmp_path):
        b = _fake_bridge(tmp_path, [ECHO], "observe")
        b._on_lxmf_receive(_lxmf(BRIDGE))
        _write(tmp_path, {BRIDGE: {"name": "named later", "purpose": "p"}})
        b._lxmf_identity_registry_obj = None   # force a re-read
        b._on_lxmf_receive(_lxmf(BRIDGE))
        ent = b._rns_ingress_ledger().snapshot()["unlisted_recent"][0]
        assert ent["label"] == "named later"

    def test_registry_failure_never_changes_the_decision(self, tmp_path,
                                                         monkeypatch):
        b = _fake_bridge(tmp_path, [ECHO], "enforce")

        def boom(self):
            raise RuntimeError("registry exploded")
        monkeypatch.setattr(BridgeRnsEventsMixin, "_lxmf_identity_registry",
                            boom)
        b._lxmf_identity_registry = boom.__get__(b)
        b._on_lxmf_receive(_lxmf(STRANGER))
        assert b._rns_to_mesh_queue.qsize() == 0      # still refused
        assert b.stats["rns_to_mesh_refused_unlisted"] == 1


class TestDisclosureCountsTheNamed:
    def test_router_line_names_the_unnamed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(reg, "default_config_dir", lambda: tmp_path)
        _write(tmp_path, {ECHO: {"name": "moc1 echo", "purpose": "probe"}})
        cfg = GatewayConfig()
        cfg.enabled = True
        cfg.rns = RNSConfig(bridge_source_identities=[ECHO, STRANGER],
                            bridge_source_policy="observe")
        line = mr.MessageRouter(cfg, {"bounced": 0},
                                threading.Lock()).describe_ingress_policy()
        assert "rns_allowlist=2 policy=observe" in line   # grep-compat
        assert "named=1/2" in line
        assert f"unnamed={STRANGER[:8]}" in line
        assert "registry=ok" in line

    def test_router_line_says_when_the_registry_is_blind(self, tmp_path,
                                                         monkeypatch):
        monkeypatch.setattr(reg, "default_config_dir", lambda: tmp_path)
        (tmp_path / reg.REGISTRY_FILENAME).write_text("{")
        cfg = GatewayConfig()
        cfg.enabled = True
        cfg.rns = RNSConfig(bridge_source_identities=[ECHO],
                            bridge_source_policy="observe")
        line = mr.MessageRouter(cfg, {"bounced": 0},
                                threading.Lock()).describe_ingress_policy()
        assert "registry=unreadable" in line
