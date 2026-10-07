"""Measured USE per listed identity — house cleaning from proven data.

Operator 2026-10-07: "if a hash is stale then it needs to go — like pids —
there needs to be a connection, a measurable use"; 14 days; NO auto-remove
(a firm remove is cheap: a useful hash re-announces, and under observe its
next message lands in the ledger, named). This pins the MEASUREMENT half:

* every VALIDATED listed/peer sender stamps ``listed_seen[hash]``;
* ``watch_since`` persists from the first stamp — stale is judgeable only
  once the window has actually been WATCHED (absence of evidence ≠ evidence
  of absence: a gateway that was down did not see silence);
* the quantity is INBOUND use — the projection says so, it never says
  "unused" (an identity may be used only as a delivery destination).
"""
from __future__ import annotations

import threading
import types
from queue import Queue

import pytest

from gateway import rns_ingress_policy as pol
from gateway.bridge_rns_events_mixin import BridgeRnsEventsMixin
from gateway.config import GatewayConfig, RNSConfig

A = "1f68b5463f1b0e8bbac685c109bd1760"
B = "7cda0fabcf4e714c421813c22897a565"
PEER = "3dfbdb5d" + "0" * 24
DAY = 86400.0


def test_window_is_fourteen_days():
    assert pol.USE_STALE_S == 14 * DAY


class TestLedgerUse:
    def _led(self, tmp_path, clock):
        return pol.IngressLedger(tmp_path / "l.json", now_fn=lambda: clock["t"])

    def test_too_early_until_watched_long_enough(self, tmp_path):
        clock = {"t": 1000.0}
        led = self._led(tmp_path, clock)
        led.stamp(policy="observe", listed=2, identities=[A, B])
        led.note_listed(A)
        clock["t"] += 3 * DAY
        use = led.snapshot()["use"]
        assert use["judgeable"] is False
        assert use["observed_s"] == pytest.approx(3 * DAY)
        assert use["no_inbound"] is None          # not judged, not "none"

    def test_stale_after_window(self, tmp_path):
        clock = {"t": 1000.0}
        led = self._led(tmp_path, clock)
        led.stamp(policy="observe", listed=2, identities=[A, B])
        clock["t"] += 15 * DAY
        led.note_listed(A)                         # A used recently
        use = led.snapshot()["use"]
        assert use["judgeable"] is True
        assert use["no_inbound"] == [B]            # B: never seen in 15 d
        assert use["last_inbound"][A] == pytest.approx(clock["t"])
        assert use["quantity"] == "inbound"

    def test_old_use_ages_out(self, tmp_path):
        clock = {"t": 1000.0}
        led = self._led(tmp_path, clock)
        led.stamp(policy="observe", listed=1, identities=[A])
        led.note_listed(A)
        clock["t"] += 20 * DAY
        led.stamp(policy="observe", listed=1, identities=[A])  # restart
        assert led.snapshot()["use"]["no_inbound"] == [A]

    def test_watch_since_survives_restart_and_reload(self, tmp_path):
        clock = {"t": 1000.0}
        led = self._led(tmp_path, clock)
        led.stamp(policy="observe", listed=1, identities=[A])
        clock["t"] += 15 * DAY
        led2 = self._led(tmp_path, clock)          # new process
        led2.stamp(policy="observe", listed=1, identities=[A])
        assert led2.snapshot()["use"]["observed_s"] == pytest.approx(15 * DAY)

    def test_clock_backwards_is_not_judgeable(self, tmp_path):
        clock = {"t": 50 * DAY}
        led = self._led(tmp_path, clock)
        led.stamp(policy="observe", listed=1, identities=[A])
        clock["t"] = 10 * DAY                      # clock stepped back
        use = led.snapshot()["use"]
        assert use["judgeable"] is False

    def test_projection_of_old_doc_without_use_is_safe(self):
        proj = pol.project_ledger_doc({"senders": {}, "policy": "observe"},
                                      now=1000.0)
        assert proj["use"]["judgeable"] is False
        assert proj["use"]["no_inbound"] is None


class _Tracker:
    def add_node(self, node):
        pass


def _bridge(tmp_path):
    cfg = GatewayConfig()
    cfg.enabled = True
    cfg.rns = RNSConfig(bridge_source_identities=[A, B],
                        bridge_source_policy="observe",
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
                 "_lxmf_identity_registry", "rns_ingress_stamp"):
        setattr(b, name, getattr(BridgeRnsEventsMixin, name).__get__(b))
    return b


def _msg(src, validated=True):
    return types.SimpleNamespace(source_hash=bytes.fromhex(src), content=b"x",
                                 title="", stamp=None, fields={},
                                 signature_validated=validated,
                                 unverified_reason=None if validated else 2)


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    monkeypatch.setattr("gateway.rns_bridge.HAS_RNS_SNIFFER", False)
    monkeypatch.setattr("commands.messaging.store_incoming", lambda **kw: None)


class TestThroughTheGateway:
    def test_validated_listed_and_peer_stamp_use(self, tmp_path):
        b = _bridge(tmp_path)
        b.rns_ingress_stamp()
        b._on_lxmf_receive(_msg(A))
        b._on_lxmf_receive(_msg(PEER))
        use = b._rns_ingress_ledger().snapshot()["use"]
        assert set(use["last_inbound"]) == {A, PEER}

    def test_forged_claim_does_not_count_as_use(self, tmp_path):
        b = _bridge(tmp_path)
        b.rns_ingress_stamp()
        b._on_lxmf_receive(_msg(B, validated=False))
        assert B not in b._rns_ingress_ledger().snapshot()["use"]["last_inbound"]

    def test_stamp_records_the_listed_ids(self, tmp_path):
        b = _bridge(tmp_path)
        b.rns_ingress_stamp()
        doc = b._rns_ingress_ledger().snapshot()
        assert doc["use"]["listed_ids"] == [A, B]
