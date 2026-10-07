"""The gateway must SAY its ingress policy, and the only refusing knob must
be a valid, honoured configuration.

Ingress trust-boundary enumeration (review_provenance QUEUED 2026-09-18,
run 2026-10-06). Measured on both live gateways: ``routing_rules: []``,
``default_route: bidirectional``, classifier importable. Drilled with that
shape: every message from every source network is bridged in every
direction at confidence 0.6 — "Source identified" is any non-empty id. The
only startup line said "Routing classifier initialized with confidence
scoring", which reads as though a policy exists.

Three measured facts this file pins:

1. Under the classifier (the live decider) an ALLOW rule with a
   ``source_filter`` restricts nothing and ``default_route`` is ignored —
   the knob the 09-18 queue entry pointed at is a decoy on the live path
   (the fifth decoy of that arc, same shape as ``desired_channels``).
2. The only refusing knob is a rule with ``direction: "drop"`` — and
   ``validate_direction`` rejected that word, while the legacy decider read
   it as "unknown direction = allow". A refusing rule that validation
   refuses and one decider inverts is not a knob.
3. Nothing told the operator any of this in-app. The router now logs its
   EFFECTIVE policy per decider at startup.
"""
from __future__ import annotations

import datetime
import logging
import threading
import types
from unittest.mock import patch

from gateway import message_routing as mr
from gateway.config import GatewayConfig, RoutingRule, validate_direction

STRANGER = "deadbeef" * 4
PEER = "abc123" * 5


def _msg(src="rns", sid=STRANGER, dest=None):
    return types.SimpleNamespace(
        source_network=src, source_id=sid, source_address=sid,
        destination_id=dest, destination_address=dest, content="hello",
        is_broadcast=dest is None, timestamp=datetime.datetime.now(),
        metadata={})


def _router(rules=(), default_route="bidirectional"):
    cfg = GatewayConfig()
    cfg.enabled = True
    cfg.routing_rules = list(rules)
    cfg.default_route = default_route
    return mr.MessageRouter(cfg, {"bounced": 0}, threading.Lock())


class TestTheLiveShapeIsAllowAll:
    def test_default_config_bridges_every_direction(self):
        r = _router()
        assert r._classifier is not None, "live decider is the classifier"
        for src, dest in (("rns", None), ("rns", "!12345678"),
                          ("meshtastic", None), ("meshcore", None)):
            assert r.should_bridge(_msg(src, dest=dest)) is True

    def test_allow_rule_source_filter_does_not_restrict_under_classifier(self):
        """Measured 2026-10-06: the knob the enumeration assumed."""
        r = _router([RoutingRule(name="peers", direction="rns_to_mesh",
                                 source_filter="^abc")],
                    default_route="mesh_to_rns")
        assert r.should_bridge(_msg(sid=STRANGER)) is True
        assert r.should_bridge(_msg(sid=PEER)) is True


class TestDropIsARealKnob:
    def test_drop_is_a_valid_direction(self):
        assert validate_direction("drop", "routing_rules[0].direction") is None

    def test_drop_rule_refuses_under_classifier(self):
        r = _router([RoutingRule(name="block", direction="drop",
                                 source_filter="^dead")])
        assert r.should_bridge(_msg(sid=STRANGER)) is False
        assert r.should_bridge(_msg(sid=PEER)) is True

    def test_drop_rule_refuses_under_legacy_too(self):
        """Pre-fix the legacy decider read 'drop' as unknown = allow."""
        with patch.object(mr, "CLASSIFIER_AVAILABLE", False):
            r = _router([RoutingRule(name="block", direction="drop",
                                     source_filter="^dead")])
            assert r._classifier is None
            assert r.should_bridge(_msg(sid=STRANGER)) is False
            assert r.should_bridge(_msg(sid=PEER)) is True

    def test_legacy_allowlist_shape_still_works(self):
        """Control: the legacy decider's real allow-list shape — allow rules
        plus a non-bidirectional default — keeps refusing strangers."""
        with patch.object(mr, "CLASSIFIER_AVAILABLE", False):
            r = _router([RoutingRule(name="peers", direction="rns_to_mesh",
                                     source_filter="^abc")],
                        default_route="mesh_to_rns")
            assert r.should_bridge(_msg(sid=STRANGER)) is False
            assert r.should_bridge(_msg(sid=PEER)) is True


class TestTheRouterSaysItsPolicy:
    def _line(self, caplog, **kw):
        caplog.set_level(logging.INFO, logger="gateway.message_routing")
        caplog.clear()
        r = _router(**kw)
        lines = [rec.getMessage() for rec in caplog.records
                 if "ingress policy" in rec.getMessage()]
        assert len(lines) == 1, lines
        assert lines[0] == r.describe_ingress_policy()
        return lines[0]

    def test_live_shape_reads_open(self, caplog):
        line = self._line(caplog)
        assert "decider=classifier" in line and "rules=0" in line
        assert "OPEN" in line
        assert "any sender" in line
        assert "drop" in line          # names the knob that would refuse

    def test_allow_rules_do_not_change_the_verdict_under_classifier(self, caplog):
        line = self._line(caplog, rules=[RoutingRule(
            name="peers", direction="rns_to_mesh", source_filter="^abc")],
            default_route="mesh_to_rns")
        assert "OPEN" in line and "rules=1" in line
        assert "do not restrict" in line

    def test_drop_rules_are_counted(self, caplog):
        line = self._line(caplog, rules=[RoutingRule(
            name="block", direction="drop", source_filter="^dead")])
        assert "1 drop rule" in line
        assert "OPEN" not in line or "except" in line

    def test_legacy_allowlist_reads_allow_listed(self, caplog):
        with patch.object(mr, "CLASSIFIER_AVAILABLE", False):
            line = self._line(caplog, rules=[RoutingRule(
                name="peers", direction="rns_to_mesh", source_filter="^abc")],
                default_route="mesh_to_rns")
        assert "decider=legacy" in line
        assert "ALLOW-LISTED" in line


class TestDropWinsOverAllow:
    """Non-author review 2026-10-07 (CONFIRMED): a drop rule was silently
    defeated by any ALLOW rule that matched first — the classifier `break`s
    on the first match in priority order and the legacy decider returns True
    on the first allow in config order — while the disclosure line said the
    drop rule was in force. A refusing rule must win regardless of order."""

    def _rules(self):
        return [RoutingRule(name="allow_all", direction="bidirectional", priority=10),
                RoutingRule(name="block", direction="drop", source_filter="^dead",
                            priority=5)]

    def test_classifier_drop_wins_whatever_the_priority(self):
        r = _router(self._rules())
        assert r.should_bridge(_msg(sid=STRANGER)) is False
        assert r.should_bridge(_msg(sid=PEER)) is True

    def test_legacy_drop_wins_whatever_the_order(self):
        with patch.object(mr, "CLASSIFIER_AVAILABLE", False):
            r = _router(self._rules())
            assert r.should_bridge(_msg(sid=STRANGER)) is False
            assert r.should_bridge(_msg(sid=PEER)) is True
