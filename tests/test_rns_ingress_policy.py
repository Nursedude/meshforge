"""RNS→RF ingress allowlist: open → observe → enforce, with a ledger.

Ingress trust-boundary enumeration, 2026-10-06 (review_provenance). The
router bridged every RNS sender onto RF; this is the declared membership
the enumeration found missing, built observe-first so the list can be
proven complete on live traffic before anything is refused.
"""
from __future__ import annotations

import json
import logging
import threading
import types
from queue import Queue
from unittest.mock import patch

import pytest

from gateway import rns_ingress_policy as pol
from gateway.bridge_rns_events_mixin import BridgeRnsEventsMixin
from gateway.config import GatewayConfig, RNSConfig
from gateway import message_routing as mr

LISTED = "1f68b5463f1b0e8bbac685c109bd1760"
PEER = "3dfbdb5d" + "0" * 24
STRANGER = "627fa566" + "f" * 24


# ── pure functions ─────────────────────────────────────────────────────

class TestNormalize:
    def test_str_list_and_case(self):
        assert pol.normalize_hashes(LISTED.upper()) == [LISTED]
        assert pol.normalize_hashes([LISTED, " " + PEER + " ", LISTED]) == [LISTED, PEER]

    def test_malformed_is_dropped_and_named(self):
        raw = [LISTED, "not-a-hash", 42, "abcd"]
        assert pol.normalize_hashes(raw) == [LISTED]
        assert pol.malformed_hashes(raw) == ["'not-a-hash'", "42", "'abcd'"]

    def test_effective_policy(self):
        assert pol.effective_policy([], "enforce") == "open"       # no list = open
        assert pol.effective_policy([LISTED], None) == "observe"
        assert pol.effective_policy([LISTED], "ENFORCE") == "enforce"
        assert pol.effective_policy([LISTED], "bogus") == "observe"

    def test_verdict(self):
        assert pol.verdict(LISTED.upper(), [LISTED], []) == "listed"
        assert pol.verdict(PEER, [LISTED], [PEER]) == "peer_gateway"
        assert pol.verdict(STRANGER, [LISTED], [PEER]) == "unlisted"


class TestLedger:
    def test_records_persists_and_snapshots(self, tmp_path):
        clock = {"t": 1000.0}
        led = pol.IngressLedger(tmp_path / "l.json", now_fn=lambda: clock["t"])
        led.record(STRANGER, policy="observe", refused=False)
        clock["t"] = 1500.0
        led.record(STRANGER, policy="enforce", refused=True)
        snap = led.snapshot(window_s=600)
        assert snap["unlisted_total"] == 1 and snap["refused_total"] == 1
        assert snap["unlisted_recent"][0]["hash"] == STRANGER
        assert snap["unlisted_recent"][0]["seen"] == 2
        # Survives a process restart.
        again = pol.IngressLedger(tmp_path / "l.json", now_fn=lambda: 2000.0)
        assert again.snapshot()["unlisted_total"] == 1
        assert json.loads((tmp_path / "l.json").read_text())["schema"] == pol.LEDGER_SCHEMA

    def test_window_hides_old_senders_but_keeps_totals(self, tmp_path):
        led = pol.IngressLedger(tmp_path / "l.json", now_fn=lambda: 1000.0)
        led.record(STRANGER, policy="observe", refused=False)
        late = pol.IngressLedger(tmp_path / "l.json", now_fn=lambda: 1000.0 + 90000)
        snap = late.snapshot(window_s=86400)
        assert snap["unlisted_recent"] == [] and snap["unlisted_total"] == 1

    def test_bounded(self, tmp_path):
        led = pol.IngressLedger(tmp_path / "l.json", max_senders=3,
                                now_fn=lambda: 1.0)
        for i in range(5):
            led._now = lambda i=i: float(i)
            led.record(f"{i:032x}", policy="observe", refused=False)
        assert led.snapshot()["unlisted_total"] == 3

    def test_unwritable_path_never_raises(self, tmp_path, caplog):
        caplog.set_level(logging.WARNING, logger="gateway.rns_ingress")
        led = pol.IngressLedger(tmp_path / "nodir" / "x" / "l.json")
        with patch("utils.paths.atomic_write_text", side_effect=OSError("ro")):
            led.record(STRANGER, policy="observe", refused=False)
            led.record(STRANGER, policy="observe", refused=False)
        assert sum("unwritable" in r.getMessage() for r in caplog.records) == 1


# ── the ingress path, through the real mixin ───────────────────────────

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
    b.notified = []
    b._notify_message = lambda m: b.notified.append(m)
    b._router = types.SimpleNamespace(should_bridge=lambda m: True)
    b._peer_gateway_hash_set = lambda: {PEER}
    b._rns_ingress_ledger_path = tmp_path / "ledger.json"
    # bind the real mixin methods onto the fake
    for name in ("_on_lxmf_receive", "_rns_ingress_ledger",
                 "rns_ingress_posture", "_rns_ingress_admits"):
        setattr(b, name, getattr(BridgeRnsEventsMixin, name).__get__(b))
    return b


def _lxmf(source_hex):
    return types.SimpleNamespace(source_hash=bytes.fromhex(source_hex),
                                 content=b"hello", title="", stamp=None,
                                 fields={})


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    monkeypatch.setattr("gateway.rns_bridge.HAS_RNS_SNIFFER", False)
    monkeypatch.setattr("commands.messaging.store_incoming",
                        lambda **kw: None)


class TestIngressThroughTheMixin:
    def test_open_bridges_a_stranger_and_touches_no_ledger(self, tmp_path):
        b = _fake_bridge(tmp_path, [], "enforce")
        b._on_lxmf_receive(_lxmf(STRANGER))
        assert b._rns_to_mesh_queue.qsize() == 1
        assert not (tmp_path / "ledger.json").exists()
        assert b.rns_ingress_posture()["policy"] == "open"

    def test_observe_bridges_but_witnesses_and_ledgers(self, tmp_path, caplog):
        caplog.set_level(logging.INFO, logger="gateway.bridge_rns_events_mixin")
        b = _fake_bridge(tmp_path, [LISTED], "observe")
        b._on_lxmf_receive(_lxmf(STRANGER))
        assert b._rns_to_mesh_queue.qsize() == 1          # still bridged
        assert b.stats["rns_ingress_unlisted"] == 1
        assert "rns_to_mesh_refused_unlisted" not in b.stats
        snap = b._rns_ingress_ledger().snapshot()
        assert snap["unlisted_recent"][0]["hash"] == STRANGER
        assert snap["refused_total"] == 0
        line = [r.getMessage() for r in caplog.records if "RNS ingress" in r.getMessage()]
        assert len(line) == 1 and STRANGER in line[0] and "unlisted" in line[0]

    def test_enforce_refuses_a_stranger(self, tmp_path, caplog):
        caplog.set_level(logging.INFO, logger="gateway.bridge_rns_events_mixin")
        b = _fake_bridge(tmp_path, [LISTED], "enforce")
        b._on_lxmf_receive(_lxmf(STRANGER))
        assert b._rns_to_mesh_queue.qsize() == 0
        assert b.stats["rns_to_mesh_refused_unlisted"] == 1
        assert len(b.notified) == 1                        # still visible in-app
        assert b._rns_ingress_ledger().snapshot()["refused_total"] == 1
        assert any("REFUSED" in r.getMessage() and STRANGER in r.getMessage()
                   for r in caplog.records)

    def test_enforce_admits_listed_and_peer_gateways(self, tmp_path):
        b = _fake_bridge(tmp_path, [LISTED], "enforce")
        b._on_lxmf_receive(_lxmf(LISTED))
        b._on_lxmf_receive(_lxmf(PEER))
        assert b._rns_to_mesh_queue.qsize() == 2
        assert "rns_ingress_unlisted" not in b.stats
        assert not (tmp_path / "ledger.json").exists()

    def test_policy_error_admits_as_open_and_says_so(self, tmp_path, caplog):
        """A broken policy must neither silently close nor silently open."""
        caplog.set_level(logging.WARNING, logger="gateway.bridge_rns_events_mixin")
        b = _fake_bridge(tmp_path, [LISTED], "enforce")
        b._peer_gateway_hash_set = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        b._on_lxmf_receive(_lxmf(STRANGER))
        assert b._rns_to_mesh_queue.qsize() == 1
        assert any("as if OPEN" in r.getMessage() for r in caplog.records)


# ── config + disclosure ────────────────────────────────────────────────

class TestConfigAndDisclosure:
    def test_malformed_identity_is_named_not_swallowed(self):
        cfg = GatewayConfig()
        cfg.rns = RNSConfig(bridge_source_identities=[LISTED, "oops"],
                            bridge_source_policy="enforce")
        _ok, errors = cfg.validate()
        msgs = [str(e) for e in errors if "bridge_source" in str(e)]
        assert any("'oops'" in m for m in msgs)
        assert cfg.rns.get_bridge_source_identities() == [LISTED]

    def test_unknown_policy_is_named(self):
        cfg = GatewayConfig()
        cfg.rns = RNSConfig(bridge_source_identities=[LISTED],
                            bridge_source_policy="deny")
        _ok, errors = cfg.validate()
        assert any("bridge_source_policy" in str(e) for e in errors)

    def test_router_line_carries_the_allowlist(self, caplog):
        caplog.set_level(logging.INFO, logger="gateway.message_routing")
        cfg = GatewayConfig()
        cfg.enabled = True
        cfg.rns = RNSConfig(bridge_source_identities=[LISTED, PEER],
                            bridge_source_policy="enforce")
        r = mr.MessageRouter(cfg, {"bounced": 0}, threading.Lock())
        assert "rns_allowlist=2 policy=enforce" in r.describe_ingress_policy()

    def test_router_line_silent_when_open(self):
        cfg = GatewayConfig()
        cfg.enabled = True
        r = mr.MessageRouter(cfg, {"bounced": 0}, threading.Lock())
        assert "rns_allowlist" not in r.describe_ingress_policy()


# ── the projection every surface shares ─────────────────────────────────

class TestProjection:
    def test_raw_doc_projects_like_the_ledger(self, tmp_path):
        led = pol.IngressLedger(tmp_path / "l.json", now_fn=lambda: 1000.0)
        led.stamp(policy="observe", listed=13)
        led.record(STRANGER, policy="observe", refused=False)
        raw = json.loads((tmp_path / "l.json").read_text())
        assert pol.project_ledger_doc(raw, now=1000.0) == led.snapshot()

    def test_malformed_doc_projects_empty_never_raises(self):
        for bad in (None, [], "x", {"senders": "nope"}, {"senders": {"a": 1}}):
            p = pol.project_ledger_doc(bad, now=0.0)
            assert p["unlisted_recent"] == [] and p["unlisted_total"] == 0
            assert p["policy"] is None

    def test_read_helper_is_none_without_a_ledger(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert pol.read_ledger_projection() is None
        (tmp_path / "meshforge").mkdir()
        (tmp_path / "meshforge" / pol.LEDGER_FILENAME).write_text("not json")
        assert pol.read_ledger_projection() is None
        pol.IngressLedger(pol.default_ledger_path(), now_fn=lambda: 5.0).stamp(
            policy="enforce", listed=2)
        got = pol.read_ledger_projection(now=6.0)
        assert got["policy"] == "enforce" and got["listed"] == 2


class TestStampAtStart:
    def test_declared_gateway_stamps_open_one_does_not(self, tmp_path):
        b = _fake_bridge(tmp_path, [LISTED], "observe")
        b.rns_ingress_stamp = BridgeRnsEventsMixin.rns_ingress_stamp.__get__(b)
        b.rns_ingress_stamp()
        doc = json.loads((tmp_path / "ledger.json").read_text())
        assert doc["policy"] == "observe" and doc["listed"] == 1
        o = _fake_bridge(tmp_path / "open", [], "observe")
        o.rns_ingress_stamp = BridgeRnsEventsMixin.rns_ingress_stamp.__get__(o)
        o.rns_ingress_stamp()
        assert not (tmp_path / "open" / "ledger.json").exists()


def test_default_ledger_path_is_inside_the_gateway_units_writable_dirs(monkeypatch):
    """Measured 2026-10-07 on a live gateway: the unit's ReadWritePaths are
    ~/.config/meshforge, ~/.cache/meshforge, ~/.local/share/meshforge, and
    ProtectHome=read-only makes ~/.local/state EROFS — the first stamp failed
    there and only the unwritable-ledger witness said so. Pin the contract."""
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    p = pol.default_ledger_path()
    assert p.parts[-3:] == (".local", "share", "meshforge")[:3][-3:] or ".local/share/meshforge" in str(p)
    assert p.name == pol.LEDGER_FILENAME
    assert ".local/state" not in str(p)
