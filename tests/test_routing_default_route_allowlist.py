"""default_route is ONE contract across both deciders (MeshAnchor #151).

KN4YDL's re-test (2026-10-19) relied on a ``source_filter`` allowlist on
``rns_to_mesh`` / ``rns_to_meshcore`` and a ``message_filter`` on
``mesh_to_rns``. Under the classifier (the live decider) neither restricted
anything: a non-matching ALLOW rule only lowers confidence, and
``default_route`` was never consulted — the decoy test_ingress_policy_
disclosure.py measured and DISCLOSED on 2026-10-06 without tightening.

The legacy decider always had the contract: a message no rule matches is
bridged only when ``default_route`` is bidirectional/all_to_all; anything
else makes the rules an allow-list. The classifier now honours the same
switch. Default configs (``bidirectional``, every live gateway surveyed
2026-10-19: rules=0) are unchanged — the knob only works for whoever sets it.
"""
from __future__ import annotations

import datetime
import logging
import threading
import types

import pytest

from gateway import message_routing as mr
from gateway.config import GatewayConfig, RoutingRule
from utils.classifier import RoutingCategory, create_routing_system

ALLOWED = "aaaa" + "0" * 28
STRANGER = "ffff" + "0" * 28

# The tester's rules, as posted on #151 (addresses redacted to two prefixes).
TESTER_RULES = [
    RoutingRule(name="meshcore_to_rns", direction="meshcore_to_rns", priority=20),
    RoutingRule(name="rns_to_meshcore", direction="rns_to_meshcore",
                source_filter="^(aaaa|bbbb)", priority=20),
    RoutingRule(name="mesh_to_rns", direction="mesh_to_rns", priority=20,
                message_filter=r"^(?!\s*\[(RNS|MC|Mesh):)"),
    RoutingRule(name="rns_to_mesh", direction="rns_to_mesh",
                source_filter="^(aaaa|bbbb)", priority=20),
]


def _msg(src, sid, content="hello", dest=None):
    return types.SimpleNamespace(
        source_network=src, source_id=sid, source_address=sid,
        destination_id=dest, destination_address=dest, content=content,
        is_broadcast=dest is None, timestamp=datetime.datetime.now(),
        metadata={})


@pytest.fixture(autouse=True)
def _no_real_fixes_file(tmp_path, monkeypatch):
    """The router builds the classifier against routing_fixes.json under the
    real home; the operator box carries one a 2026-02 test wrote. Pin it
    (non-author review 2026-10-19)."""
    monkeypatch.setattr(mr, "get_real_user_home", lambda: tmp_path)


def _router(rules, default_route):
    cfg = GatewayConfig()
    cfg.enabled = True
    cfg.routing_rules = list(rules)
    cfg.default_route = default_route
    return mr.MessageRouter(cfg, {"bounced": 0}, threading.Lock())


class TestAllowListUnderTheClassifier:
    """default_route closed → the rules ARE the policy."""

    def test_stranger_rns_sender_is_refused(self):
        r = _router(TESTER_RULES, "allowlist")
        assert r._classifier is not None, "this pins the live decider"
        assert r.should_bridge(_msg("rns", STRANGER)) is False

    def test_allowlisted_rns_sender_still_bridges(self):
        r = _router(TESTER_RULES, "allowlist")
        assert r.should_bridge(_msg("rns", ALLOWED)) is True

    def test_message_filter_refuses_tagged_mesh_text(self):
        r = _router(TESTER_RULES, "allowlist")
        assert r.should_bridge(
            _msg("meshtastic", "!dd03e678", "[MC:KN4YDL-mc2] Test 5")) is False
        assert r.should_bridge(_msg("meshtastic", "!dd03e678", "Test 5")) is True

    def test_a_direction_no_rule_names_is_refused(self):
        """Allow-list means allow-list: nothing names meshcore_to_mesh."""
        rules = [x for x in TESTER_RULES if x.direction != "meshcore_to_rns"]
        r = _router(rules, "allowlist")
        assert r.should_bridge(_msg("meshcore", "KN4YDL-mc2")) is False

    def test_drop_still_wins_over_an_allow_match(self):
        rules = TESTER_RULES + [RoutingRule(
            name="block", direction="drop", source_filter="^aaaa", priority=99)]
        r = _router(rules, "allowlist")
        assert r.should_bridge(_msg("rns", ALLOWED)) is False

    def test_refusal_leaves_a_witness(self, caplog):
        """A silent refusal is the defect class (honest_failure_modes #9)."""
        stats = {"bounced": 0}
        cfg = GatewayConfig()
        cfg.enabled = True
        cfg.routing_rules = list(TESTER_RULES)
        cfg.default_route = "allowlist"
        r = mr.MessageRouter(cfg, stats, threading.Lock())
        with caplog.at_level(logging.INFO, logger="gateway.message_routing"):
            r.should_bridge(_msg("rns", STRANGER))
            r.should_bridge(_msg("rns", STRANGER))
        assert stats.get("routing_refused_no_rule") == 2
        infos = [rec for rec in caplog.records if rec.levelno == logging.INFO
                 and "no enabled rule matched" in rec.getMessage()]
        assert len(infos) == 1, "first refusal at INFO, the rest at DEBUG"


class TestOpenDefaultIsUnchanged:
    """Every live gateway's shape. A change here re-routes the fleet."""

    @pytest.mark.parametrize("default_route", ["bidirectional", "all_to_all"])
    def test_non_matching_allow_rule_still_bridges(self, default_route):
        r = _router(TESTER_RULES, default_route)
        assert r.should_bridge(_msg("rns", STRANGER)) is True

    def test_no_rules_bridges_everything(self):
        r = _router([], "bidirectional")
        for src in ("rns", "meshtastic", "meshcore"):
            assert r.should_bridge(_msg(src, STRANGER)) is True


class TestBothDecidersAgree:
    """One contract, two deciders: the reason this is a fix and not a mode."""

    @pytest.mark.parametrize("default_route", ["bidirectional", "allowlist"])
    @pytest.mark.parametrize("src,sid,content", [
        ("rns", STRANGER, "hi"), ("rns", ALLOWED, "hi"),
        ("meshtastic", "!dd03e678", "[RNS:f3b2] Test 1"),
        ("meshtastic", "!dd03e678", "Test 1"),
        ("meshcore", "KN4YDL-mc2", "Test 5"),
    ])
    def test_classifier_matches_legacy(self, default_route, src, sid, content):
        r = _router(TESTER_RULES, default_route)
        m = _msg(src, sid, content)
        assert r._classify_message(m) == r._should_bridge_legacy(m)


class TestClassifierUnit:

    def test_default_open_is_the_default(self):
        c = create_routing_system(rules=[{"name": "x", "direction": "rns_to_mesh",
                                          "source_filter": "^aaaa"}])
        res = c.classify("u1", {"source_network": "rns", "source_id": STRANGER,
                                "destination_id": None, "content": "hi",
                                "is_broadcast": True, "metadata": {}})
        assert res.category == RoutingCategory.BRIDGE_MESH.value

    def test_closed_refuses_with_a_reason(self):
        c = create_routing_system(rules=[{"name": "x", "direction": "rns_to_mesh",
                                          "source_filter": "^aaaa"}],
                                  default_open=False)
        res = c.classify("u2", {"source_network": "rns", "source_id": STRANGER,
                                "destination_id": None, "content": "hi",
                                "is_broadcast": True, "metadata": {}})
        assert res.category == RoutingCategory.DROP.value
        assert res.metadata.get("refused_no_rule") is True


class TestTheStartupLineTellsTheTruth:

    def test_closed_default_route_reads_allow_listed_on_the_classifier(self):
        line = _router(TESTER_RULES, "allowlist").describe_ingress_policy()
        assert "decider=classifier" in line
        assert "ALLOW-LISTED" in line, line
        assert "do not restrict" not in line, line

    def test_open_default_route_still_says_allow_rules_do_not_restrict(self):
        line = _router(TESTER_RULES, "bidirectional").describe_ingress_policy()
        assert "do not restrict" in line, line


class TestDirectionValuedDefaultRoute:
    """``template_mqtt_monitor`` sets ``default_route = "mesh_to_rns"  #
    Receive only`` with NO rules: a direction, not a closed switch. Read as a
    pure allow-list it would bridge nothing; ignored (the old classifier) it
    bridged everything both ways. It now opens exactly the source it names,
    and rules add to it — the same source-scoped reading rules get."""

    def test_named_source_is_open(self):
        r = _router([], "mesh_to_rns")
        assert r.should_bridge(_msg("meshtastic", "!dd03e678", "Test 1")) is True

    def test_other_sources_need_a_rule(self):
        r = _router([], "mesh_to_rns")
        assert r.should_bridge(_msg("rns", STRANGER)) is False
        assert r.should_bridge(_msg("meshcore", "KN4YDL-mc2")) is False

    def test_a_rule_adds_to_it(self):
        r = _router([RoutingRule(name="peers", direction="rns_to_mesh",
                                 source_filter="^aaaa")], "mesh_to_rns")
        assert r.should_bridge(_msg("rns", ALLOWED)) is True
        assert r.should_bridge(_msg("rns", STRANGER)) is False

    def test_the_monitor_template_does_what_its_comment_says(self):
        cfg = GatewayConfig.template_mqtt_monitor()
        r = mr.MessageRouter(cfg, {"bounced": 0}, threading.Lock())
        assert r.should_bridge(_msg("meshtastic", "!dd03e678", "Test 1")) is True
        assert r.should_bridge(_msg("rns", STRANGER)) is False

    @pytest.mark.parametrize("src,sid", [
        ("meshtastic", "!dd03e678"), ("rns", STRANGER), ("rns", ALLOWED),
        ("meshcore", "KN4YDL-mc2")])
    def test_both_deciders_agree(self, src, sid):
        r = _router([RoutingRule(name="peers", direction="rns_to_mesh",
                                 source_filter="^aaaa")], "mesh_to_rns")
        m = _msg(src, sid)
        assert r._classify_message(m) == r._should_bridge_legacy(m)

    def test_the_startup_line_names_the_open_source(self):
        line = _router([], "mesh_to_rns").describe_ingress_policy()
        assert "ALLOW-LISTED" in line and "meshtastic" in line, line


class TestReviewFindings:
    """Non-author review, 2026-10-19."""

    def test_a_policy_that_bridges_nothing_warns(self, caplog):
        """F1: closed + zero allow rules = a gateway that bridges nothing.
        A typo'd default_route was fully OPEN under the old classifier and
        is fully CLOSED now — that must not be a quiet INFO line."""
        with caplog.at_level(logging.INFO, logger="gateway.message_routing"):
            _router([], "bidirectonal")
        lines = [r for r in caplog.records if "Routing ingress policy" in r.getMessage()]
        assert lines and lines[-1].levelno == logging.WARNING, [
            (r.levelname, r.getMessage()) for r in lines]
        assert "bridges NOTHING" in lines[-1].getMessage()

    def test_an_allow_listed_policy_with_rules_stays_info(self, caplog):
        with caplog.at_level(logging.INFO, logger="gateway.message_routing"):
            _router(TESTER_RULES, "allowlist")
        lines = [r for r in caplog.records if "Routing ingress policy" in r.getMessage()]
        assert lines[-1].levelno == logging.INFO

    @pytest.mark.parametrize("value", ["bidirectonal", "", "primary_to_secondary"])
    def test_validate_warns_on_an_unrecognised_default_route(self, value):
        cfg = GatewayConfig()
        cfg.default_route = value
        _ok, errors = cfg.validate()
        hits = [e for e in errors if e.field == "default_route"]
        assert hits and hits[0].severity == "warning", [str(e) for e in errors]

    @pytest.mark.parametrize("value", ["bidirectional", "all_to_all", "allowlist",
                                       "mesh_to_rns", "rns_to_meshcore"])
    def test_validate_accepts_the_documented_values(self, value):
        cfg = GatewayConfig()
        cfg.default_route = value
        _ok, errors = cfg.validate()
        assert not [e for e in errors if e.field == "default_route"]

    def test_classifier_reads_default_route_live(self):
        """F2: a snapshot at construction let the classifier bridge while
        legacy refused and the startup line said ALLOW-LISTED."""
        r = _router(TESTER_RULES, "bidirectional")
        r.config.default_route = "allowlist"
        m = _msg("rns", STRANGER)
        assert r._classify_message(m) is False
        assert r._classify_message(m) == r._should_bridge_legacy(m)

    def test_destination_networks_use_the_same_switch(self):
        """F3: two constants for one switch (hfm #5)."""
        r = _router([], "mesh_to_rns")
        m = _msg("meshtastic", "!dd03e678", "Test 1")
        assert r.should_bridge(m) is True
        assert r.get_destination_networks(m), "bridged to nowhere"

    def test_a_bounced_refusal_still_leaves_the_named_witness(self):
        """F4: the bounce return ran before the refusal witness."""
        stats = {"bounced": 0}
        cfg = GatewayConfig()
        cfg.enabled = True
        cfg.routing_rules = list(TESTER_RULES)
        cfg.default_route = "allowlist"
        r = mr.MessageRouter(cfg, stats, threading.Lock())
        m = types.SimpleNamespace(
            source_network="rns", source_id="", source_address="",
            destination_id=None, destination_address=None, content="",
            is_broadcast=False, timestamp=datetime.datetime.now(), metadata={})
        assert r.should_bridge(m) is False
        assert stats.get("routing_refused_no_rule") == 1


def test_vocabulary_agrees_with_the_router_direction_map():
    """Two maps for one fact WILL drift (hfm #5): every source-directed
    entry in _DIRECTION_MAP names the same source in DEFAULT_ROUTE_SOURCE."""
    from gateway.routing_vocab import DEFAULT_ROUTE_SOURCE
    pairs = {k: v[0] for k, v in mr.MessageRouter._DIRECTION_MAP.items()
             if isinstance(v, tuple)}
    assert pairs == DEFAULT_ROUTE_SOURCE
