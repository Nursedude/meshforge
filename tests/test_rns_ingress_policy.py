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
        # membership needs a VALIDATED signature (test_rns_ingress_signature)
        assert pol.verdict(LISTED.upper(), [LISTED], [],
                           signature_validated=True) == "listed"
        assert pol.verdict(PEER, [LISTED], [PEER],
                           signature_validated=True) == "peer_gateway"
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
                 "rns_ingress_posture", "_rns_ingress_admits",
                 "_record_ingress_refusal"):
        setattr(b, name, getattr(BridgeRnsEventsMixin, name).__get__(b))
    return b


def _lxmf(source_hex):
    # a GENUINE message: LXMF validated its signature against the source key
    return types.SimpleNamespace(source_hash=bytes.fromhex(source_hex),
                                 content=b"hello", title="", stamp=None,
                                 fields={}, signature_validated=True,
                                 unverified_reason=None)


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch, tmp_path):
    monkeypatch.setattr("gateway.rns_bridge.HAS_RNS_SNIFFER", False)
    # the identity registry must never read the REAL ~/.config (ambient state)
    monkeypatch.setattr("gateway.lxmf_identity_registry.default_config_dir",
                        lambda: tmp_path / "no-registry")
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
        # members leave measured USE (house cleaning, 2026-10-07) but are
        # never recorded as unlisted senders
        snap = b._rns_ingress_ledger().snapshot()
        assert snap["unlisted_total"] == 0
        assert set(snap["use"]["last_inbound"]) == {LISTED, PEER}

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
        assert "(RNS→RF included)" in r.describe_ingress_policy()

    # 2026-10-08, moc's live startup line after the enforce flip read
    # "policy=enforce … OPEN on every source network: any sender … is
    # bridged onward (RNS→RF included)" — the sentence knew the routing
    # rules and not the allowlist, so it contradicted its own clause.
    @staticmethod
    def _line(policy, legacy=False, peers=(), rules=(),
              default_route="bidirectional"):
        from gateway.config import RoutingRule
        cfg = GatewayConfig()
        cfg.enabled = True
        cfg.routing_rules = [RoutingRule(**r) for r in rules]
        cfg.default_route = default_route
        cfg.rns = RNSConfig(bridge_source_identities=[LISTED, PEER],
                            bridge_source_policy=policy,
                            peer_gateway_destinations=list(peers))
        # Pin the decider both ways: CLASSIFIER_AVAILABLE is machine state
        # (a minimal CI profile would silently test legacy twice).
        with patch.object(mr, "CLASSIFIER_AVAILABLE", not legacy):
            return mr.MessageRouter(cfg, {"bounced": 0},
                                    threading.Lock()).describe_ingress_policy()

    # review 2026-10-08 (non-author, on 4d03bbf9): under enforce the bridge
    # also admits every peer gateway (VERDICT_PEER), and a peer relays its
    # whole RF segment as one identity — "only the N listed" hid that.
    @pytest.mark.parametrize("legacy", [False, True])
    def test_enforce_names_peer_gateways_and_their_relay(self, legacy):
        other = "ab" * 16
        line = self._line("enforce", legacy, peers=[PEER, other])
        assert "2 peer gateway(s)" in line
        assert "relays its whole mesh segment" in line

    def test_restricted_branch_says_and_not_or(self):
        drop = [dict(name="block", direction="drop", source_filter="^dead")]
        line = self._line("enforce", rules=drop)
        assert "also admits" not in line
        assert "RNS→RF additionally requires one of the 2 listed" in line
        allow = [dict(name="peers", direction="rns_to_mesh",
                      source_filter="^abc")]
        line = self._line("enforce", legacy=True, rules=allow,
                          default_route="mesh_to_rns")
        assert "ALLOW-LISTED" in line
        assert "RNS→RF additionally requires one of the 2 listed" in line

    def test_legacy_drop_clause_does_not_qualify_the_allowlist(self):
        drop = [dict(name="block", direction="drop", source_filter="^dead")]
        line = self._line("enforce", legacy=True, rules=drop)
        assert line.index("unless one of 1 drop rule(s) matches") < \
            line.index("RNS→RF")

    @pytest.mark.parametrize("legacy", [False, True])
    def test_enforce_never_claims_rns_to_rf_is_open(self, legacy):
        line = self._line("enforce", legacy)
        assert "RNS→RF included" not in line
        assert ("RNS→RF, which admits only the 2 listed identities, "
                "signature-verified (enforce)") in line

    @pytest.mark.parametrize("legacy", [False, True])
    def test_observe_says_the_list_does_not_refuse(self, legacy):
        line = self._line("observe", legacy)
        assert "the 2-identity list only logs strangers, policy=observe" in line
        assert "admits only" not in line


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


class TestLedgerCorruptionCannotOpenTheBridge:
    """Non-author review 2026-10-07 (CONFIRMED): under enforce a malformed
    ledger entry made record() raise inside the except-guarded path, so that
    sender was admitted "as if OPEN" on every message, while the refused
    counter still incremented. The ledger is on-disk INPUT to the enforce
    path; state must never open the bridge, only a policy error may."""

    @pytest.mark.parametrize("entry", [{"seen": None}, 7, "x", {"refused": "n"}])
    def test_enforce_still_refuses_with_a_corrupt_ledger_entry(self, tmp_path, entry):
        (tmp_path / "ledger.json").write_text(json.dumps(
            {"schema": pol.LEDGER_SCHEMA, "senders": {STRANGER: entry}}))
        b = _fake_bridge(tmp_path, [LISTED], "enforce")
        b._on_lxmf_receive(_lxmf(STRANGER))
        b._on_lxmf_receive(_lxmf(STRANGER))
        assert b._rns_to_mesh_queue.qsize() == 0
        assert b.stats["rns_to_mesh_refused_unlisted"] == 2

    def test_loader_sanitizes_entries(self, tmp_path):
        (tmp_path / "l.json").write_text(json.dumps(
            {"schema": pol.LEDGER_SCHEMA,
             "senders": {STRANGER: {"seen": None, "refused": "n"}, PEER: 7}}))
        led = pol.IngressLedger(tmp_path / "l.json", now_fn=lambda: 1.0)
        ent = led.record(STRANGER, policy="enforce", refused=True)
        assert ent["seen"] == 1 and ent["refused"] == 1
        assert led.snapshot()["unlisted_total"] == 1      # the int entry was dropped


class TestConfigShapeIsNamed:
    """Non-author review 2026-10-07 (CONFIRMED): a dict/int/bool/None list
    normalised to [] → OPEN with nothing named, despite a declared policy."""

    @pytest.mark.parametrize("raw", [{"a": LISTED}, 123, True])
    def test_wrong_shape_is_named(self, raw):
        cfg = GatewayConfig()
        cfg.rns = RNSConfig(bridge_source_identities=raw, bridge_source_policy="enforce")
        _ok, errors = cfg.validate()
        assert any("bridge_source_identities" in str(e) for e in errors), errors

    def test_declared_policy_with_empty_list_is_named(self):
        cfg = GatewayConfig()
        cfg.rns = RNSConfig(bridge_source_identities=[], bridge_source_policy="enforce")
        _ok, errors = cfg.validate()
        assert any("bridge_source_policy" in str(e) and "OPEN" in str(e) for e in errors), errors


def test_lazy_ledger_init_is_single_under_threads(tmp_path):
    """Non-author review 2026-10-07 (PLAUSIBLE → pinned): two threads calling
    the lazy getter before start() must share ONE ledger object."""
    import threading as _t
    b = _fake_bridge(tmp_path, [LISTED], "observe")
    seen = []
    def go():
        seen.append(id(b._rns_ingress_ledger()))
    ts = [_t.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]; [t.join(5) for t in ts]
    assert len(set(seen)) == 1


def test_unparseable_last_seen_stays_in_the_window():
    """Non-author review 2026-10-07: untyped last_seen silently aged a
    sender out of the window → healthy. Unknown is not old."""
    doc = {"senders": {STRANGER: {"last_seen": None, "seen": 2, "refused": 2},
                       PEER: {"last_seen": "yesterday", "seen": 1, "refused": 1},
                       "ab" * 16: {"last_seen": "1799999995", "seen": 1}}}
    p = pol.project_ledger_doc(doc, now=1_800_000_000.0)
    assert {r["hash"] for r in p["unlisted_recent"]} == {STRANGER, PEER, "ab" * 16}
    assert p["refused_total"] == 3


# ── #4 (BIC-2 gate 3, review 2026-10-29): a refusal reaches the record ──
# LXMF proves delivery in the gateway's router BEFORE this gate runs, so the
# sender reads "delivered" for a message the bridge then refused. The sender
# cannot be told (an unverified source is one whose key we lack), so the
# per-message delivery record must carry it — not only a counter.

class TestRefusalReachesTheDeliveryRecord:
    @pytest.fixture
    def recorded(self, monkeypatch):
        calls = []
        monkeypatch.setattr("gateway.delivery_counters.record",
                            lambda *a, **kw: calls.append((a, kw)))
        return calls

    def _msg(self, source_hex, *, msg_hash=b"\x11" * 32, validated=True):
        m = _lxmf(source_hex)
        m.hash = msg_hash
        m.signature_validated = validated
        return m

    def test_enforce_refusal_is_one_dropped_ingress_refused_event(self, tmp_path, recorded):
        from gateway import delivery_counters as dc
        b = _fake_bridge(tmp_path, [LISTED], "enforce")
        b._on_lxmf_receive(self._msg(STRANGER))
        assert len(recorded) == 1
        (state, msg_id), kw = recorded[0]
        assert state == dc.DeliveryState.DROPPED
        assert kw["drop_reason"] == dc.DropReason.INGRESS_REFUSED
        assert msg_id == "11" * 32                      # the LXMF message hash
        assert kw["protocol"] == "meshtastic" and kw["source"] == "rns"
        assert kw["content_id"]                         # joins the M→R/R→M id

    def test_an_unverified_member_is_recorded_and_named(self, tmp_path, recorded):
        b = _fake_bridge(tmp_path, [LISTED], "enforce")
        b._on_lxmf_receive(self._msg(LISTED, validated=None))
        assert len(recorded) == 1
        assert "unverified" in recorded[0][1]["note"]

    def test_a_message_without_a_hash_still_gets_an_id(self, tmp_path, recorded):
        b = _fake_bridge(tmp_path, [LISTED], "enforce")
        m = self._msg(STRANGER)
        del m.hash
        b._on_lxmf_receive(m)
        # never the OUTBOUND prefix (review 10-29 #3)
        assert recorded[0][0][1].startswith("lxmf-in-")

    def test_an_unverified_stranger_is_named_unlisted_like_the_journal(self, tmp_path, recorded):
        # review 10-29 #2: "unverified" is the gate's verdict for a MEMBER
        # claim only; a stranger without a valid signature is just unlisted.
        b = _fake_bridge(tmp_path, [LISTED], "enforce")
        b._on_lxmf_receive(self._msg(STRANGER, validated=None))
        assert "not in bridge_source_identities" in recorded[0][1]["note"]

    def test_recording_is_bounded_and_the_excess_is_witnessed(self, tmp_path, recorded, monkeypatch):
        # review 10-29 #1: a public-net stranger must not be able to flood
        # the delivery record; the gate itself is unchanged.
        from gateway import bridge_rns_events_mixin as mx
        clock = [1000.0]
        monkeypatch.setattr(mx, "_monotonic", lambda: clock[0])
        b = _fake_bridge(tmp_path, [LISTED], "enforce")
        n = mx.INGRESS_REFUSAL_RECORD_MAX
        for i in range(n + 5):
            b._on_lxmf_receive(self._msg(STRANGER, msg_hash=bytes([i]) * 32))
        assert len(recorded) == n
        assert b.stats["rns_ingress_refusals_unrecorded"] == 5
        assert b.stats["rns_to_mesh_refused_unlisted"] == n + 5
        clock[0] += mx.INGRESS_REFUSAL_RECORD_WINDOW_S + 0.1
        b._on_lxmf_receive(self._msg(STRANGER, msg_hash=b"\xee" * 32))
        assert len(recorded) == n + 1


def test_refusals_cannot_evict_the_terminals_the_stall_probe_judges(tmp_path, monkeypatch):
    # review 10-29 #1 (the reviewer's flood, pinned): refusals are terminal
    # DROPPED rows; in the shared terminal ring they pushed out the rns
    # confirmations `delivery_confirmation_stall` (#74) needs.
    from gateway import delivery_counters as dc
    from utils.watchdog_probes_gateway import confirmation_window
    c = dc.DeliveryCounters(db_path=tmp_path / "dc.db", run_preflight=False)
    for i in range(30):
        c.record(dc.DeliveryState.CONFIRMED, f"x{i}", protocol="rns",
                 source="meshtastic")
    for i in range(250):
        c.record(dc.DeliveryState.DROPPED, f"r{i}", protocol="meshtastic",
                 source="rns", drop_reason=dc.DropReason.INGRESS_REFUSED)
    snap = c.snapshot()
    assert confirmation_window(snap)["terminal"] == 30
    assert not any(r.get("drop_reason") == "ingress_refused"
                   for r in snap["recent_terminal"])
    assert snap["legs"]["rns>meshtastic"]["15m"]["refused"] == 250


def test_traffic_pulse_excludes_the_same_reason_from_its_terminal_ring():
    # hfm #5: traffic_pulse mirrors the snapshot (it cannot import gateway).
    from gateway import delivery_counters as dc
    from monitoring import traffic_pulse as tp
    assert tp._TERMINAL_RING_EXCLUDED_REASON == dc.DropReason.INGRESS_REFUSED.value

    def test_observe_and_admitted_record_no_refusal(self, tmp_path, recorded):
        _fake_bridge(tmp_path, [LISTED], "observe")._on_lxmf_receive(
            self._msg(STRANGER))
        _fake_bridge(tmp_path, [LISTED], "enforce")._on_lxmf_receive(
            self._msg(LISTED))
        assert recorded == []

    def test_a_recorder_error_changes_nothing_about_the_refusal(self, tmp_path, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("disk")
        monkeypatch.setattr("gateway.delivery_counters.record", boom)
        b = _fake_bridge(tmp_path, [LISTED], "enforce")
        b._on_lxmf_receive(self._msg(STRANGER))
        assert b._rns_to_mesh_queue.qsize() == 0
        assert b.stats["rns_to_mesh_refused_unlisted"] == 1
        assert len(b.notified) == 1


def test_a_refusal_flood_never_prunes_a_delivery_row(tmp_path):
    # re-review 10-29 #1: the terminal-ring FILTER was not enough — the
    # physical prune-on-insert deleted the oldest rows whatever they were.
    from gateway import delivery_counters as dc
    c = dc.DeliveryCounters(db_path=tmp_path / "dc.db", run_preflight=False,
                            ring_cap=40)
    for i in range(30):
        c.record(dc.DeliveryState.CONFIRMED, f"x{i}", protocol="rns",
                 source="meshtastic")
    for i in range(dc.REFUSAL_RING_CAP * 4):
        c.record(dc.DeliveryState.DROPPED, f"r{i}", protocol="meshtastic",
                 source="rns", drop_reason=dc.DropReason.INGRESS_REFUSED)
    snap = c.snapshot()
    assert len(snap["recent_terminal"]) == 30
    import sqlite3
    with sqlite3.connect(str(tmp_path / "dc.db")) as raw:
        n_ref = raw.execute("SELECT COUNT(*) FROM events WHERE drop_reason = "
                            "'ingress_refused'").fetchone()[0]
        n_other = raw.execute("SELECT COUNT(*) FROM events WHERE drop_reason "
                              "IS NOT 'ingress_refused'").fetchone()[0]
    assert n_ref == dc.REFUSAL_RING_CAP          # bounded in its own pool
    assert n_other == 30                         # nothing of ours evicted
    # the delivery pool still prunes itself at its own cap
    for i in range(20):
        c.record(dc.DeliveryState.CONFIRMED, f"y{i}", protocol="rns",
                 source="meshtastic")
    with sqlite3.connect(str(tmp_path / "dc.db")) as raw:
        assert raw.execute("SELECT COUNT(*) FROM events WHERE drop_reason "
                           "IS NOT 'ingress_refused'").fetchone()[0] == 40
