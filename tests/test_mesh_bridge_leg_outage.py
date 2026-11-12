"""BR-T #14 — a message that arrives while the OTHER leg is down for longer than
the queue's retry budget was dead-lettered and never delivered.

Found by the 11-10 r6 review by running and re-measured on the TCP leg by the
MA-port review (.claude/research/landscape_2026_11_09.md §B #14): serial unplug
→ refused at +0.26 s and +5.4 s → ``dead_letter unknown_error_exhausted``; the
leg came back at +10.3 s and the message never crossed. The bridge's senders
return a bare ``False`` for "link down", which the RetryPolicy classifies as
``unknown`` (2 tries), so a link outage spent the budget meant for send errors.

The fix: ``_forward_message``'s two link-down refusals raise the queue's
``SendDeferred`` — the row returns to pending, retry_count UNTOUCHED — and rows
age out WITNESSED at ``MESH_BRIDGE_MAX_AGE_S``. v1 (``bf5a3b90``) gated the
whole destination with a ``ready_fn`` instead; the 11-11 non-author review
found that gate ran AHEAD of the downlink-inject branch (broker, not socket —
F1) and of the sender's dual-path dedup (F2), so the deferral lives at the
refusal itself, after both.

Real ``PersistentMessageQueue`` (tmp sqlite) and the real bridge sender; only
the radio (and, for F1, the broker injector) is replaced.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import logging

import pytest

from gateway.config import GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig
from utils.db_helpers import connect_tuned


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Queues under tmp, never the live ~/.config/meshforge/mesh_bridge_queues."""
    import gateway.mesh_bridge as mb
    monkeypatch.setattr(mb, "get_real_user_home", lambda: tmp_path)


@pytest.fixture
def bridge():
    from gateway.mesh_bridge import MeshtasticPresetBridge
    cfg = GatewayConfig()
    cfg.enabled = True
    cfg.bridge_mode = "mesh_bridge"
    cfg.mesh_bridge = MeshtasticBridgeConfig(
        enabled=True,
        primary=MeshtasticConfig(host="192.0.2.1", port=4403, preset="LONG_FAST"),   # TEST-NET-1
        secondary=MeshtasticConfig(host="192.0.2.2", port=4404, preset="SHORT_TURBO"),
        direction="bidirectional", add_prefix=True,
        prefix_format="[Mesh:{source_preset}] ",
    )
    return MeshtasticPresetBridge(config=cfg)


class _Radio:
    """A leg's interface: counts writes, returns a packet (library success)."""

    def __init__(self):
        self.sent = []

    def sendText(self, text, **kw):
        self.sent.append(text)
        return {"id": len(self.sent)}


def _msg(text, preset):
    import uuid
    from gateway.mesh_bridge import BridgedMeshMessage
    return BridgedMeshMessage(
        source_preset=preset, source_id="!b03bb70c", destination_id="^all",
        content=f"{text} {uuid.uuid4().hex[:8]}", channel=0, is_broadcast=True)


def _enqueue_for_primary(bridge, text):
    msg = _msg(text, "SHORT_TURBO")
    q = bridge._secondary_to_primary
    msg_id = q.enqueue(msg.to_payload(), destination="primary")
    assert msg_id
    return q, msg_id, msg.content


def _enqueue_for_secondary(bridge, text):
    msg = _msg(text, "LONG_FAST")
    q = bridge._primary_to_secondary
    msg_id = q.enqueue(msg.to_payload(), destination="secondary")
    assert msg_id
    return q, msg_id, msg.content


def _sql(q, sql, args=()):
    conn = connect_tuned(q._db_path)
    try:
        rows = conn.execute(sql, args).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def _row(q, msg_id):
    status, retry_count = _sql(
        q, "SELECT status, retry_count FROM messages WHERE id = ?", (msg_id,))[0]
    return {"status": status, "retry_count": retry_count}


def _tick(q, n):
    """Each process_once is the NEXT attempt (backoff collapsed), so the outage
    outlasts any retry budget in a handful of ticks."""
    with patch("gateway.mesh_bridge.assert_iface_tx_allowed"):
        for _ in range(n):
            _sql(q, "UPDATE messages SET retry_after = NULL")
            q.process_once(batch_size=5)


class TestLegDownLongerThanTheRetryBudget:

    def test_a_disconnected_leg_holds_the_row_untouched(self, bridge):
        q, msg_id, _ = _enqueue_for_primary(bridge, "held")
        bridge._primary_interface, bridge._primary_connected = None, False
        _tick(q, 6)                                   # > every retry budget
        row = _row(q, msg_id)
        assert row["status"] == "pending"             # not dead-lettered
        assert row["retry_count"] == 0                # an outage is not an attempt

    def test_the_held_row_crosses_when_the_leg_returns(self, bridge):
        q, msg_id, content = _enqueue_for_primary(bridge, "late")
        bridge._primary_interface, bridge._primary_connected = None, False
        _tick(q, 6)
        radio = _Radio()
        bridge._primary_interface, bridge._primary_connected = radio, True
        _tick(q, 1)
        assert _row(q, msg_id)["status"] == "delivered"
        assert len(radio.sent) == 1 and content in radio.sent[0]
        assert bridge.stats["messages_secondary_to_primary"] == 1

    def test_connected_flag_with_a_dead_socket_holds_too(self, bridge):
        """#10's window (flag still True, library write would no-op)."""
        q, msg_id, _ = _enqueue_for_primary(bridge, "socketless")
        radio = _Radio()
        bridge._primary_interface, bridge._primary_connected = radio, True
        with patch("gateway.mesh_bridge._write_target_gone", return_value=True):
            _tick(q, 6)
        assert _row(q, msg_id) == {"status": "pending", "retry_count": 0}
        assert radio.sent == []

    def test_an_outage_is_logged_once_not_per_tick(self, bridge, caplog):
        """A 10-min hold re-asks every few seconds; the ERROR is the EPISODE."""
        import logging
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        q, _, _ = _enqueue_for_primary(bridge, "quiet")
        bridge._primary_interface, bridge._primary_connected = None, False
        _tick(q, 6)
        loud = [r for r in caplog.records if r.levelno >= logging.WARNING
                and "primary" in r.getMessage() and "link down" in r.getMessage()]
        assert len(loud) == 1
        assert bridge.stats["link_down_deferrals"] == 6

    def test_a_send_error_on_a_live_leg_still_spends_the_budget(self, bridge):
        """Control that can fail: deferral must not swallow REAL send failures
        (an interface refusing the write on a link that is up)."""
        q, msg_id, _ = _enqueue_for_primary(bridge, "refused")

        class _Refuses(_Radio):
            def sendText(self, text, **kw):
                return False

        bridge._primary_interface, bridge._primary_connected = _Refuses(), True
        _tick(q, 6)
        assert _row(q, msg_id)["status"] == "dead_letter"


class TestReviewFindings_2026_11_11:

    def test_F1_downlink_broadcast_still_crosses_with_the_socket_gone(self, bridge):
        """The broker path does not use the socket: a primary broadcast whose
        TCP socket is None is delivered by downlink inject, as before v1."""
        q, msg_id, _ = _enqueue_for_primary(bridge, "via broker")
        radio = _Radio()
        bridge._primary_interface, bridge._primary_connected = radio, True
        inj = MagicMock()
        inj.inject.return_value = True
        inj.inject_nodeinfo.return_value = True
        bridge._primary_downlink = inj
        with patch("gateway.mesh_bridge._write_target_gone", return_value=True):
            _tick(q, 1)
        assert _row(q, msg_id)["status"] == "delivered"
        assert inj.inject.call_count == 1
        assert radio.sent == []                       # never the dead socket

    def test_F2_content_already_on_the_far_mesh_is_not_held(self, bridge):
        """The sender's dual-path check runs BEFORE the link-down deferral: a
        copy that already reached the far mesh resolves now, not after."""
        from gateway.mesh_bridge import get_secondary_rf_registry
        q, msg_id, content = _enqueue_for_secondary(bridge, "already there")
        bridge._secondary_interface, bridge._secondary_connected = None, False
        get_secondary_rf_registry().register(content)
        _tick(q, 1)
        assert _row(q, msg_id)["status"] == "delivered"   # suppressed = resolved
        assert bridge.stats.get("dual_path_suppressed_secondary") == 1

    def test_F5_an_undrained_queue_still_ages_out(self, bridge):
        """One-way bridge: the reverse queue is never drained, but rows already
        in it (from before #4, or a direction change) must still expire,
        witnessed — never count as pending forever."""
        from gateway.mesh_bridge import MESH_BRIDGE_MAX_AGE_S
        bridge.bridge_config.direction = "primary_to_secondary"
        q, msg_id, _ = _enqueue_for_primary(bridge, "stranded")
        old = (datetime.now() - timedelta(seconds=MESH_BRIDGE_MAX_AGE_S + 60))
        _sql(q, "UPDATE messages SET created_at = ? WHERE id = ?",
             (old.isoformat(), msg_id))
        bridge._expire_undrained()
        assert _row(q, msg_id)["status"] == "dead_letter"
        # the drained direction is left to process_once
        assert bridge._primary_to_secondary.get_queue_depth() == 0

    def test_F6_held_flag_is_rows_waiting_on_a_down_leg_now(self, bridge):
        bridge._primary_interface, bridge._primary_connected = None, False
        assert bridge.get_status()["queue"]["s2p_held_link_down"] is False  # empty
        _enqueue_for_primary(bridge, "visible")
        assert bridge.get_status()["queue"]["s2p_held_link_down"] is True
        bridge._primary_interface, bridge._primary_connected = _Radio(), True
        assert bridge.get_status()["queue"]["s2p_held_link_down"] is False  # live read

    def test_held_rows_age_out_witnessed_not_forever(self, bridge):
        from gateway.mesh_bridge import MESH_BRIDGE_MAX_AGE_S
        assert 0 < MESH_BRIDGE_MAX_AGE_S
        for q, dest in ((bridge._secondary_to_primary, "primary"),
                        (bridge._primary_to_secondary, "secondary")):
            assert q._send_max_age.get(dest) == MESH_BRIDGE_MAX_AGE_S


def _mqtt_leg():
    import threading
    from gateway.mesh_bridge import MQTTMeshInterface
    cfg = MeshtasticConfig(use_mqtt=True, mqtt_channel="meshforge", host="192.0.2.1")
    return MQTTMeshInterface(config=cfg, name="primary",
                             message_callback=lambda p: None,
                             stop_event=threading.Event())


class _Client:
    def __init__(self, connects, sends=True):
        self.is_connected = False
        self._connects, self._sends = connects, sends
        self.sent = []

    def connect(self):
        self.is_connected = self._connects
        return self._connects

    def send_text(self, text, **kw):
        self.sent.append(text)
        return self._sends


class TestReviewRound2_2026_11_11:
    """Fable round 2 (by running) of 16f97fa7."""

    def _mqtt_tick(self, bridge, q, client, n):
        with patch("gateway.meshtastic_protobuf_client.send_text_direct",
                   return_value=None), \
             patch("gateway._mesh_bridge_mqtt._get_protobuf_client",
                   return_value=client), \
             patch("gateway._mesh_bridge_mqtt._HAS_PROTOBUF_CLIENT", True), \
             patch("gateway._mesh_bridge_mqtt._HAS_PROTOBUF_CONFIG", True), \
             patch("gateway._mesh_bridge_mqtt._ProtobufTransportConfig"):
            _tick(q, n)

    def test_known_limit_mqtt_leg_outage_spends_the_budget_named(self, bridge, caplog):
        """KNOWN LIMIT, pinned on purpose (r3 11-11 reverted the r2 MQTT hold):
        the MQTT leg's TX is HTTP to meshtasticd and its failure shapes
        (refused, HTTP 500, stale session, missing dep) cannot be told apart
        from the send path — r2's inference held a reachable daemon's 500s for
        600 s and missed a second outage. Until a DIRECT reachability fact
        exists (queued), an unreachable daemon dead-letters the row like any
        send error — and the loss is NAMED, never silent. If this test starts
        failing because the row is held, that is the queued fix landing:
        replace this pin with its tests."""
        import logging
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        q, msg_id, content = _enqueue_for_primary(bridge, "mqtt limit")
        bridge._primary_interface, bridge._primary_connected = _mqtt_leg(), True
        self._mqtt_tick(bridge, q, _Client(connects=False), 6)
        assert _row(q, msg_id)["status"] == "dead_letter"
        assert any("mesh_bridge drop leg=secondary reason=dead_letter" in r.getMessage()
                   and content[:40] in r.getMessage() for r in caplog.records)

    def test_episode_gap_is_thirty_seconds(self):
        """r3 m13: the tests derive from the constant; pin its value."""
        import gateway.mesh_bridge as mb
        assert mb.LINK_DOWN_EPISODE_GAP_S == 30.0

    def test_F3_a_new_outage_logs_again_without_a_send_between(self, bridge, caplog):
        """Episode = deferrals closer than LINK_DOWN_EPISODE_GAP_S; an outage
        whose rows all aged out must not silence the next one."""
        import logging
        import gateway.mesh_bridge as mb
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        bridge._primary_interface, bridge._primary_connected = None, False
        clock = [1000.0]
        with patch.object(mb.time, "monotonic", lambda: clock[0]):
            q, msg_id, _ = _enqueue_for_primary(bridge, "one")
            _tick(q, 2)                                    # outage 1
            _sql(q, "DELETE FROM messages")                # its rows gone (expired)
            clock[0] += mb.LINK_DOWN_EPISODE_GAP_S + 1
            _enqueue_for_primary(bridge, "two")
            _tick(q, 2)                                    # outage 2
        loud = [r for r in caplog.records if r.levelno >= logging.WARNING
                and "DEFERRED" in r.getMessage()]
        assert len(loud) == 2

    def test_F3_a_send_ends_the_episode(self, bridge, caplog):
        """m04/m05: the success path re-arms too (no gap needed)."""
        import logging
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        q, _, _ = _enqueue_for_primary(bridge, "a")
        bridge._primary_interface, bridge._primary_connected = None, False
        _tick(q, 2)
        bridge._primary_interface, bridge._primary_connected = _Radio(), True
        _tick(q, 1)
        _enqueue_for_primary(bridge, "b")
        bridge._primary_interface, bridge._primary_connected = None, False
        _tick(q, 2)
        loud = [r for r in caplog.records if r.levelno >= logging.WARNING
                and "DEFERRED" in r.getMessage()]
        assert len(loud) == 2

    def test_m14_m17_held_flag_reads_both_link_facts(self, bridge):
        _enqueue_for_primary(bridge, "flag")
        bridge._primary_interface, bridge._primary_connected = _Radio(), False
        assert bridge.get_status()["queue"]["s2p_held_link_down"] is True   # flag
        bridge._primary_connected = True
        with patch("gateway.mesh_bridge._write_target_gone", return_value=True):
            assert bridge.get_status()["queue"]["s2p_held_link_down"] is True  # target
        assert bridge.get_status()["queue"]["s2p_held_link_down"] is False

    def test_m08_cleanup_loop_runs_the_undrained_expiry(self, bridge):
        bridge._running = True
        waits = iter([False, True])
        with patch.object(bridge._stop_event, "wait", lambda *_: next(waits)), \
             patch.object(bridge, "_expire_undrained") as exp:
            bridge._cleanup_loop()
        assert exp.call_count == 1

    @pytest.mark.parametrize("direction,drained", [
        ("primary_to_secondary", ("p2s",)),
        ("secondary_to_primary", ("s2p",)),
        ("bidirectional", ("p2s", "s2p")),
    ])
    def test_m18_the_drain_obeys_direction(self, bridge, direction, drained):
        bridge.bridge_config.direction = direction
        bridge._running = True
        # legs DOWN (R4-F1): direction alone decides — a leg gate anywhere in
        # the loop would skip a queue here and fail this too
        bridge._primary_interface, bridge._primary_connected = None, False
        bridge._secondary_interface, bridge._secondary_connected = None, False
        waits = iter([True])
        with patch.object(bridge._stop_event, "wait", lambda *_: next(waits)), \
             patch.object(bridge._primary_to_secondary, "process_once",
                          return_value=0) as p2s, \
             patch.object(bridge._secondary_to_primary, "process_once",
                          return_value=0) as s2p:
            bridge._bridge_loop()
        assert (p2s.call_count == 1) == ("p2s" in drained)
        assert (s2p.call_count == 1) == ("s2p" in drained)

    def test_m07_a_fault_after_enqueue_is_counted_not_named_a_loss(self, bridge, caplog):
        import logging
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        q = bridge._primary_to_secondary
        pkt = {"decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": b"queued ok"},
               "fromId": "!aaaa0001", "toId": "^all", "to": 0xFFFFFFFF, "channel": 0}
        with patch.object(bridge, "_notify_message", side_effect=RuntimeError("cb")):
            bridge._process_receive(pkt, "primary", "secondary", q)
        assert bridge.stats.get("receive_errors") == 1
        assert q.get_queue_depth() == 1
        assert not any("reason=receive_error" in r.getMessage() for r in caplog.records)

    def test_m04_a_downlink_delivery_ends_the_episode_too(self, bridge, caplog):
        import logging
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        inj = MagicMock()
        inj.inject.return_value = True
        inj.inject_nodeinfo.return_value = True
        q, _, _ = _enqueue_for_primary(bridge, "dl a")
        bridge._primary_interface, bridge._primary_connected = None, False
        _tick(q, 2)                                    # outage 1: ERROR
        bridge._primary_interface, bridge._primary_connected = _Radio(), True
        bridge._primary_downlink = inj
        _tick(q, 1)                                    # delivered by downlink
        assert inj.inject.call_count == 1
        _enqueue_for_primary(bridge, "dl b")
        bridge._primary_interface, bridge._primary_connected = None, False
        _tick(q, 2)                                    # outage 2: ERROR again
        loud = [r for r in caplog.records if r.levelno >= logging.WARNING
                and "DEFERRED" in r.getMessage()]
        assert len(loud) == 2


def test_hold_constants_are_pinned():
    """End-state review 11-11 F6: mutants 2.0→200 s and 600→6000 s survived
    every bridge test (the tests derive from the constants). Pin the values;
    MESH_BRIDGE_MAX_AGE_S is the operator's 11-11 '10 min' (ASSERTED, deferred
    measure row) — change it here and in the deferred ledger together."""
    from gateway.mesh_bridge import LINK_DOWN_RETRY_S, MESH_BRIDGE_MAX_AGE_S
    assert LINK_DOWN_RETRY_S == 2.0
    assert MESH_BRIDGE_MAX_AGE_S == 600


class TestPortReviewTwin_2026_11_11:
    """MA port review r1 (code-review medium on MA b2e0fb85, 11-11): findings
    that hold in MF too (the port copied MF) — landscape §B #19–#23."""

    def test_19_a_library_exit_on_send_is_a_counted_failure_not_a_stuck_row(self, bridge):
        """F4: SystemExit (library our_exit) was caught only at _bridge_loop,
        after process_once had marked the row in_progress — the row stuck,
        the batch was skipped, nothing named it."""
        q, msg_id, _ = _enqueue_for_secondary(bridge, "lib exit")

        class _Exiter(_Radio):
            def sendText(self, text, **kw):
                raise SystemExit("Warning: library our_exit")

        bridge._secondary_interface, bridge._secondary_connected = _Exiter(), True
        errors_before = bridge.stats["errors"]
        _tick(q, 1)                                   # must not raise
        assert _row(q, msg_id)["status"] == "pending"  # retried, not stuck
        assert _row(q, msg_id)["retry_count"] == 1
        assert bridge.stats["errors"] == errors_before + 1
        assert bridge.stats.get("library_exit_caught") == 1

    def test_20_a_hold_that_ages_out_is_named_expired_not_dead_letter(self, bridge, caplog):
        """F5: every terminal drop read `dead_letter` — the #14 hold's own
        loss (aged out while the leg was down) was indistinct from a send
        that failed N times."""
        q, msg_id, content = _enqueue_for_secondary(bridge, "aged")
        # 2 h: past MESH_BRIDGE_MAX_AGE_S, inside AGE_SANITY_CAP (an older
        # stamp reads as a clock jump and is re-stamped, never expired).
        from datetime import datetime, timedelta
        _sql(q, "UPDATE messages SET created_at = ? WHERE id = ?",
             ((datetime.now() - timedelta(hours=2)).isoformat(), msg_id))
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        q.expire_overdue()
        drops = [r.getMessage() for r in caplog.records
                 if r.getMessage().startswith("mesh_bridge drop")]
        assert drops == [f"mesh_bridge drop leg=primary reason=expired: {content}"]

    def test_20_a_shed_row_is_named_shed(self, bridge, caplog):
        q, msg_id, content = _enqueue_for_secondary(bridge, "shed me")
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        assert q._shed_overflow(1) == 1
        drops = [r.getMessage() for r in caplog.records
                 if r.getMessage().startswith("mesh_bridge drop")]
        assert drops == [f"mesh_bridge drop leg=primary reason=shed: {content}"]

    def test_20_control_retries_exhausted_keeps_dead_letter(self, bridge, caplog):
        """c21's `delivered_unrecorded` keys on dead_letter — that name stays."""
        q, msg_id, content = _enqueue_for_secondary(bridge, "fails")

        class _Refuser(_Radio):
            def sendText(self, text, **kw):
                return False

        bridge._secondary_interface, bridge._secondary_connected = _Refuser(), True
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        _tick(q, 10)
        assert _row(q, msg_id)["status"] == "dead_letter"
        drops = [r.getMessage() for r in caplog.records
                 if r.getMessage().startswith("mesh_bridge drop")]
        assert drops == [f"mesh_bridge drop leg=primary reason=dead_letter: {content}"]

    def test_21_the_connected_event_sees_the_leg_installed(self, bridge):
        """F6: _notify_status('<leg>_connected') ran inside _connect_* BEFORE
        _install_leg — the event's own payload read the leg as down."""
        _enqueue_for_secondary(bridge, "waiting")
        seen = []
        bridge.register_status_callback(
            lambda status, data: seen.append((status, data["queue"]["p2s_held_link_down"])))
        with patch.object(bridge, "_connect_interface", return_value=(_Radio(), True)):
            bridge._connect_secondary()
        assert ("secondary_connected", False) in seen

    def test_22r_a_down_leg_holds_a_downlink_broadcast_and_publishes_nothing(self, bridge):
        """§B #22 REVERTED (Fable r3, CONFIRMED with the real injector + a fake
        broker): with the defer after the downlink branch, a held row read
        `delivered` whenever the broker took the publish — but a leg flagged
        down usually means meshtasticd, the topic's only subscriber, is down.
        The row is HELD and nothing is published."""
        q, msg_id, _ = _enqueue_for_primary(bridge, "broker path")
        bridge._primary_interface, bridge._primary_connected = None, False
        inj = MagicMock()
        inj.inject.return_value = True
        inj.inject_nodeinfo.return_value = True
        bridge._primary_downlink = inj
        _tick(q, 1)
        assert _row(q, msg_id)["status"] == "pending"
        assert inj.inject.call_count == 0

    def test_22_control_a_dm_on_a_down_leg_is_still_held(self, bridge):
        from gateway.mesh_bridge import BridgedMeshMessage
        q = bridge._secondary_to_primary
        m = BridgedMeshMessage(source_preset="SHORT_TURBO", source_id="!b03bb70c",
                               destination_id="!1234abcd", content="dm held",
                               channel=0, is_broadcast=False)
        msg_id = q.enqueue(m.to_payload(), destination="primary")
        bridge._primary_interface, bridge._primary_connected = None, False
        bridge._primary_downlink = MagicMock()
        _tick(q, 1)
        assert _row(q, msg_id)["status"] == "pending"
        assert bridge._primary_downlink.inject.call_count == 0

_LOOP_CADENCE_WAITS = (0.2, 0.05)   # _bridge_loop's idle / busy waits


def _loop_pass(bridge, n=1):
    """n passes of the REAL ``_bridge_loop`` (not its helper), backoff
    collapsed between passes; returns the wait the loop asked for after each.

    The seam #23 hid behind: a test that ticks q.process_once cannot see a gate
    the loop puts in front of the sender (Fable r2) — and a test that calls
    ``_drain_once`` cannot see one inlined in ``_bridge_loop`` (R4-F1, mutant
    m23k survived every test). Each pass must end at a cadence wait: the
    except branch's wait(1) means the pass raised, which fails here."""
    queues = (bridge._primary_to_secondary, bridge._secondary_to_primary)

    def collapse_backoff():
        for q in queues:
            _sql(q, "UPDATE messages SET retry_after = NULL")

    waits = []

    def one_pass_then_stop(timeout=None):
        waits.append(timeout)
        if len(waits) >= n:
            return True
        collapse_backoff()
        return False

    collapse_backoff()
    bridge._running = True
    with patch("gateway.mesh_bridge.assert_iface_tx_allowed"), \
            patch.object(bridge._stop_event, "wait", one_pass_then_stop):
        bridge._bridge_loop()
    assert len(waits) == n, f"loop ran {len(waits)} of {n} passes"
    assert all(w in _LOOP_CADENCE_WAITS for w in waits), \
        f"a pass took the loop's except branch: waits={waits}"
    return waits


class TestDrainOffersEveryQueue:
    """§B #23 REVERTED (Fable r2 by running, NOT CLEAN): the drain must offer
    every direction-allowed queue to its sender whatever the leg state — a
    leg gate ran AHEAD of the sender's per-row decisions (dedup, DM refusal,
    downlink) and of process_once's clock-step shift. Same kind as #14 v1's
    ready_fn gate; this class is the seam guard so a third one fails here.
    Every case drives the REAL ``_bridge_loop`` (R4-F1): a gate inlined there,
    above ``_drain_once``, must fail too."""

    @pytest.mark.parametrize("p_up,s_up", [(False, False), (True, False),
                                           (False, True), (True, True)])
    def test_every_queue_is_offered_whatever_the_legs(self, bridge, p_up, s_up):
        bridge._primary_interface, bridge._primary_connected = (_Radio(), True) if p_up else (None, False)
        bridge._secondary_interface, bridge._secondary_connected = (_Radio(), True) if s_up else (None, False)
        p2s, s2p = bridge._primary_to_secondary, bridge._secondary_to_primary
        with patch.object(p2s, "process_once", return_value=0) as a, \
                patch.object(s2p, "process_once", return_value=0) as b:
            assert _loop_pass(bridge) == [0.2]          # idle cadence
        assert (a.call_count, b.call_count) == (1, 1)

    def test_drain_returns_what_the_senders_processed(self, bridge):
        p2s, s2p = bridge._primary_to_secondary, bridge._secondary_to_primary
        with patch.object(p2s, "process_once", return_value=2), \
                patch.object(s2p, "process_once", return_value=3):
            assert bridge._drain_once() == 5
            assert _loop_pass(bridge) == [0.05]        # work done -> busy cadence

    def test_F2_through_the_loop_far_mesh_copy_resolves_with_the_leg_down(self, bridge):
        from gateway.mesh_bridge import get_secondary_rf_registry
        q, msg_id, content = _enqueue_for_secondary(bridge, "loop already there")
        bridge._secondary_interface, bridge._secondary_connected = None, False
        get_secondary_rf_registry().register(content)
        _loop_pass(bridge)
        assert _row(q, msg_id)["status"] == "delivered"
        assert bridge.stats.get("dual_path_suppressed_secondary") == 1

    def test_22r_through_the_loop_a_down_leg_holds_a_downlink_broadcast(self, bridge):
        q, msg_id, _ = _enqueue_for_primary(bridge, "loop broker path")
        bridge._primary_interface, bridge._primary_connected = None, False
        inj = MagicMock()
        inj.inject.return_value = True
        inj.inject_nodeinfo.return_value = True
        bridge._primary_downlink = inj
        _loop_pass(bridge)
        assert _row(q, msg_id)["status"] == "pending"
        assert inj.inject.call_count == 0

    def test_through_the_loop_a_dm_with_no_destination_is_refused_not_held(self, bridge):
        from gateway.mesh_bridge import BridgedMeshMessage
        q = bridge._primary_to_secondary
        m = BridgedMeshMessage(source_preset="LONG_FAST", source_id="!b03bb70c",
                               destination_id="", content="no dest dm",
                               channel=0, is_broadcast=False)
        msg_id = q.enqueue(m.to_payload(), destination="secondary")
        bridge._secondary_interface, bridge._secondary_connected = None, False
        _loop_pass(bridge, 6)
        assert _row(q, msg_id)["status"] == "dead_letter"
        assert bridge.stats.get("dm_no_destination", 0) >= 1

    def test_F1_through_the_loop_a_wall_step_mid_outage_does_not_expire_the_hold(self, bridge):
        """A +1 h wall step while the leg is down: process_once's note_clock
        must shift the row before any expiry (a gate called expire_overdue
        with no note_clock — the row died as 'expired after 3600s')."""
        from datetime import timedelta
        q, msg_id, _ = _enqueue_for_secondary(bridge, "held across a step")
        bridge._secondary_interface, bridge._secondary_connected = None, False
        _loop_pass(bridge)                             # baseline clock tick
        assert q._clock_wall is not None
        q._clock_wall = q._clock_wall - timedelta(hours=1)   # wall stepped +1 h
        (created,) = _sql(q, "SELECT created_at FROM messages WHERE id = ?", (msg_id,))[0]
        from datetime import datetime
        _sql(q, "UPDATE messages SET created_at = ? WHERE id = ?",
             ((datetime.fromisoformat(created) - timedelta(hours=1)).isoformat(), msg_id))
        _loop_pass(bridge)
        assert _row(q, msg_id)["status"] == "pending"
        assert q._stats["clock_jumps"] == 1           # the shift ran, before any expiry

def test_21_a_failing_connected_notify_leaves_the_leg_installed(bridge):
    """§B #21 hardening: the notify now runs after the install; a status read
    that raises must not unseat the leg it is reporting."""
    radio = _Radio()
    with patch.object(bridge, "_notify_status", side_effect=RuntimeError("sqlite gone")):
        bridge._install_or_close("secondary", radio, True)
    assert bridge._secondary_interface is radio and bridge._secondary_connected


def test_21_no_connected_event_on_a_failed_connect(bridge):
    """Fable r2 M13: a connect that fails must not announce `<leg>_connected`."""
    seen = []
    bridge.register_status_callback(lambda status, data: seen.append(status))
    with patch.object(bridge, "_connect_interface", return_value=(None, False)):
        bridge._connect_secondary()
    assert "secondary_connected" not in seen


class TestRound3Pins_2026_11_11:
    """Fable r3 (by running) surviving mutants + the MF/MA pre-seed asymmetry."""

    def test_F5_library_exit_and_loop_fatal_counters_are_pre_seeded(self, bridge):
        assert bridge.stats["library_exit_caught"] == 0
        assert bridge.stats["loop_fatal_caught"] == 0

    def test_B11_no_connected_event_when_the_install_is_refused(self, bridge):
        """stop() began: the fresh handle is refused + closed — and must not be
        announced as connected."""
        seen = []
        bridge.register_status_callback(lambda status, data: seen.append(status))
        bridge._stop_event.set()
        radio = MagicMock()
        bridge._install_or_close("secondary", radio, True)
        assert "secondary_connected" not in seen
        assert bridge._secondary_interface is None

    def test_X2_the_terminal_reason_rides_a_copy_never_the_callers_payload(self, bridge):
        from gateway.message_queue_outage_mixin import TERMINAL_REASON_KEY
        q = bridge._primary_to_secondary
        payload = {"content": "x"}
        q._fire_terminal("secondary", payload, "dropped", reason="expired")
        assert TERMINAL_REASON_KEY not in payload


# ════════════════════════════════════════════════════════════════════════════
# THE LEG-STATE TABLES (R5 seam, 2026-11-12). Rounds 4 and 5 each found the
# guard blind ONE STEP OVER — a gate inlined in the loop (R4-F1), then a gate
# on the PRIMARY leg once a queue has rows (R5-F1), an early notify one frame
# above the connectors (R5-F2), the #22 shortcut on a marked-down leg that
# kept its handle (R5-F3). Same kind twice = a seam: one table, symmetric in
# the leg, every row through the REAL _bridge_loop / _connect_<leg>. A gate
# or shortcut keyed on ANY leg, state or row kind now has a row that fails.
# ════════════════════════════════════════════════════════════════════════════

_LEGS = ("primary", "secondary")
# none = no handle · marked = _mark_leg_down (flag down, handle KEPT) ·
# gone = flag up, write target gone (#10 window) · up = writable
_LEG_STATES = ("none", "marked", "gone", "up")
_ROWS = ("bcast", "bcast_downlink", "dm", "dm_nodest", "dedup", "aged")


def _expected(row, leg, state):
    """THE spec, from the sender's own order (_send_to_<leg> → _forward_message
    → process_once): returns (status, via) where via ∈ held / radio / inject /
    dedup / refused / expired."""
    if row == "aged":                 # process_once expires BEFORE any send
        return "dead_letter", "expired"
    if row == "dedup":                # the sender's dedup runs before the defer
        return "delivered", "dedup"
    if row == "dm_nodest":            # #18: refused before the defer
        return "dead_letter", "refused"
    if state in ("none", "marked"):   # #22 REVERTED: the defer comes first
        return "pending", "held"
    if row == "bcast_downlink" and leg == "primary":
        return "delivered", "inject"  # F1: broker path, not the socket
    if state == "gone":               # #10: the library would drop the write
        return "pending", "held"
    return "delivered", "radio"


def _set_leg(bridge, leg, state):
    radio = None if state == "none" else _Radio()
    setattr(bridge, f"_{leg}_interface", radio)
    setattr(bridge, f"_{leg}_connected", state in ("gone", "up"))
    return radio


def _enqueue_row(bridge, row, dest):
    """One row of kind ``row`` waiting for ``dest``; returns (queue, id, text)."""
    from datetime import datetime, timedelta
    from gateway.mesh_bridge import (BridgedMeshMessage, get_rf_tx_registry,
                                     get_secondary_rf_registry)
    import uuid
    src_preset = "SHORT_TURBO" if dest == "primary" else "LONG_FAST"
    q = bridge._secondary_to_primary if dest == "primary" else bridge._primary_to_secondary
    dm = row in ("dm", "dm_nodest")
    text = f"{row}->{dest} {uuid.uuid4().hex[:8]}"
    m = BridgedMeshMessage(
        source_preset=src_preset, source_id="!b03bb70c",
        destination_id={"dm": "!0badcafe", "dm_nodest": ""}.get(row, "^all"),
        content=text, channel=0, is_broadcast=not dm)
    msg_id = q.enqueue(m.to_payload(), destination=dest)
    assert msg_id
    if row == "dedup":
        (get_rf_tx_registry() if dest == "primary"
         else get_secondary_rf_registry()).register(text)
    if row == "aged":   # past MESH_BRIDGE_MAX_AGE_S, inside the clock-jump cap
        _sql(q, "UPDATE messages SET created_at = ? WHERE id = ?",
             ((datetime.now() - timedelta(seconds=720)).isoformat(), msg_id))
    return q, msg_id, text


@pytest.mark.parametrize("other_state", ["none", "up"])
@pytest.mark.parametrize("state", _LEG_STATES)
@pytest.mark.parametrize("dest", _LEGS)
@pytest.mark.parametrize("row", _ROWS)
def test_leg_state_table_through_the_real_loop(bridge, caplog, row, dest, state,
                                                other_state):
    other = "secondary" if dest == "primary" else "primary"
    radio = _set_leg(bridge, dest, state)
    other_radio = _set_leg(bridge, other, other_state)
    inj = MagicMock()
    inj.inject.return_value = True
    inj.inject_nodeinfo.return_value = True
    bridge._primary_downlink = inj if row == "bcast_downlink" else None
    q, msg_id, text = _enqueue_row(bridge, row, dest)
    caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")

    with patch.object(bridge, "_dual_path_dedup_on", return_value=True), \
            patch("gateway.mesh_bridge._write_target_gone",
                  side_effect=lambda i: state == "gone" and i is radio):
        _loop_pass(bridge, 6)

    status, via = _expected(row, dest, state)
    got = _row(q, msg_id)
    assert got["status"] == status, (row, dest, state, other_state, got)
    sent = radio.sent if radio is not None else []
    if via == "radio":                         # exactly one write, this text
        assert len(sent) == 1 and sent[0].endswith(text), sent
    else:
        assert sent == []
    assert inj.inject.call_count == (1 if via == "inject" else 0)
    assert (other_radio.sent if other_radio else []) == []
    if via == "held":
        assert got["retry_count"] == 0         # the budget is for send errors
    if via == "refused":
        assert bridge.stats.get("dm_no_destination", 0) >= 1
    if via == "expired":
        assert f"mesh_bridge drop leg={other} reason=expired: {text}" in [
            r.getMessage() for r in caplog.records]


@pytest.mark.parametrize("outcome", ["ok", "fail"])
@pytest.mark.parametrize("ctype", ["tcp", "mqtt", "serial"])
@pytest.mark.parametrize("leg", _LEGS)
def test_connect_table_exactly_one_event_after_the_install(bridge, leg, ctype, outcome):
    """#21 through the REAL _connect_<leg> → _connect_interface → install, the
    connector replaced: the WHOLE event list, per leg — one
    `<leg>_connected`, reading the leg up; none on a failed connect."""
    cfg = getattr(bridge.bridge_config, leg)
    cfg.connection_type = "" if ctype == "tcp" else ctype
    cfg.use_mqtt = False
    radio = _Radio()
    got = (radio, True) if outcome == "ok" else (None, False)
    seen = []
    bridge.register_status_callback(
        lambda status, data: seen.append((status, data[leg]["connected"])))
    with patch.object(bridge, "_connect_tcp", return_value=got), \
            patch.object(bridge, "_connect_mqtt", return_value=got), \
            patch.object(bridge, "_connect_serial", return_value=got):
        getattr(bridge, f"_connect_{leg}")()
    assert seen == ([(f"{leg}_connected", True)] if outcome == "ok" else [])
    assert (getattr(bridge, f"_{leg}_interface") is radio) == (outcome == "ok")


# ════════════════════════════════════════════════════════════════════════════
# R6 SCENARIOS (2026-11-12) — written by the round-6 reviewer (Fable 5.2,
# non-author, by running), adopted as written. The leg-state table above has
# one row per case, enqueued before the first pass; these vary what it does
# not: rows ARRIVING mid-outage, more held rows than a batch, the leg
# returning, reconnect over an old handle, the real _leg_loop, and the
# LINK_DOWN_RETRY_S use-site (with REAL backoff — _loop_pass collapses it).
# They kill R6-F1..F4 (g01 g02 g03 d03 d06 n02 n04 n06 f10 f16).
# ════════════════════════════════════════════════════════════════════════════

OTHER = {"primary": "secondary", "secondary": "primary"}


def _drops(caplog):
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("mesh_bridge drop")]


@pytest.mark.parametrize("state", ["none", "marked"])
@pytest.mark.parametrize("dest", _LEGS)
def test_S1_rows_arriving_MID_outage_still_get_every_per_row_decision(bridge, caplog, dest, state):
    caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
    radio = _set_leg(bridge, dest, state)
    _set_leg(bridge, OTHER[dest], "up")
    bridge._primary_downlink = None
    with patch.object(bridge, "_dual_path_dedup_on", return_value=True):
        q, held_id, _ = _enqueue_row(bridge, "bcast", dest)
        _loop_pass(bridge, 3)                       # the outage is established
        assert _row(q, held_id) == {"status": "pending", "retry_count": 0}
        # NOW rows arrive, mid-outage
        _, dedup_id, _ = _enqueue_row(bridge, "dedup", dest)
        _, nodest_id, _ = _enqueue_row(bridge, "dm_nodest", dest)
        _loop_pass(bridge, 6)
        assert _row(q, dedup_id)["status"] == "delivered"
        assert _row(q, nodest_id)["status"] == "dead_letter"
        assert _row(q, held_id) == {"status": "pending", "retry_count": 0}
        # the held row ages past the hold while the leg is still down
        _sql(q, "UPDATE messages SET created_at = ? WHERE id = ?",
             ((datetime.now() - timedelta(seconds=720)).isoformat(), held_id))
        q._last_expire_mono = None                  # = _EXPIRE_EVERY_S elapsed
        _loop_pass(bridge, 2)
        assert _row(q, held_id)["status"] == "dead_letter"
    assert any("reason=expired" in d for d in _drops(caplog))
    assert (radio.sent if radio else []) == []


def _real_backoff_passes(bridge, n):
    """n passes of the real loop WITHOUT collapsing retry_after (the tree's
    _loop_pass re-offers the same 5 head rows every pass, which is not what a
    2 s deferral does)."""
    waits = []
    def w(timeout=None):
        waits.append(timeout)
        return len(waits) >= n
    bridge._running = True
    with patch("gateway.mesh_bridge.assert_iface_tx_allowed"), \
            patch.object(bridge._stop_event, "wait", w):
        bridge._bridge_loop()
    assert all(x in (0.2, 0.05) for x in waits), waits
    return waits


@pytest.mark.parametrize("dest", _LEGS)
def test_S2_more_held_rows_than_a_batch_do_not_starve_a_resolvable_row(bridge, dest):
    _set_leg(bridge, dest, "none"); _set_leg(bridge, OTHER[dest], "up")
    bridge._primary_downlink = None
    with patch.object(bridge, "_dual_path_dedup_on", return_value=True):
        held = [_enqueue_row(bridge, "bcast", dest) for _ in range(8)]
        q, dedup_id, _ = _enqueue_row(bridge, "dedup", dest)
        waits = _real_backoff_passes(bridge, 3)
    assert waits == [0.05, 0.05, 0.2], waits      # 5 deferred, 3 deferred + 1 resolved, idle
    assert _row(q, dedup_id)["status"] == "delivered"
    assert all(_row(q, i)["status"] == "pending" and _row(q, i)["retry_count"] == 0
               for _, i, _ in held)
    assert bridge.stats["link_down_deferrals"] == 8


@pytest.mark.parametrize("dest", _LEGS)
def test_S3_the_leg_returns_every_held_row_crosses_once_in_order(bridge, dest):
    _set_leg(bridge, dest, "none"); _set_leg(bridge, OTHER[dest], "up")
    bridge._primary_downlink = None
    held = [_enqueue_row(bridge, "bcast", dest) for _ in range(7)]
    q = held[0][0]
    _loop_pass(bridge, 4)
    radio = _set_leg(bridge, dest, "up")
    _loop_pass(bridge, 4)
    assert [s.split("] ", 1)[-1] for s in radio.sent] == [t for _, _, t in held]
    assert all(_row(q, i)["status"] == "delivered" for _, i, _ in held)
    assert dest not in bridge._link_down_last


@pytest.mark.parametrize("dest", _LEGS)
def test_S4_library_exit_on_one_row_does_not_stop_the_batch_either_leg(bridge, dest):
    class _ExitOnFirst(_Radio):
        def sendText(self, text, **kw):
            if not self.sent:
                self.sent.append("EXIT")
                raise SystemExit("our_exit")
            return super().sendText(text, **kw)
    radio = _ExitOnFirst()
    setattr(bridge, f"_{dest}_interface", radio); setattr(bridge, f"_{dest}_connected", True)
    _set_leg(bridge, OTHER[dest], "up")
    bridge._primary_downlink = None
    q, a, _ = _enqueue_row(bridge, "bcast", dest)
    _, b, tb = _enqueue_row(bridge, "bcast", dest)
    with patch.object(bridge, "_dual_path_dedup_on", return_value=False):
        _loop_pass(bridge, 1)
    assert _row(q, a) == {"status": "pending", "retry_count": 1}
    assert _row(q, b)["status"] == "delivered"
    assert bridge.stats["library_exit_caught"] == 1 and bridge.stats["loop_fatal_caught"] == 0


@pytest.mark.parametrize("state", ["up", "gone"])
def test_S5_a_DM_beside_a_downlink_injector_is_never_injected(bridge, state):
    class _R(_Radio):
        def sendText(self, text, **kw):
            self.kw = kw
            return super().sendText(text, **kw)
    radio = _R()
    bridge._primary_interface, bridge._primary_connected = radio, True
    _set_leg(bridge, "secondary", "up")
    inj = MagicMock(); inj.inject.return_value = True
    bridge._primary_downlink = inj
    q, dm_id, _ = _enqueue_row(bridge, "dm", "primary")
    with patch("gateway.mesh_bridge._write_target_gone",
               side_effect=lambda i: state == "gone" and i is radio):
        _loop_pass(bridge, 3)
    assert inj.inject.call_count == 0
    if state == "up":
        assert _row(q, dm_id)["status"] == "delivered"
        assert radio.kw.get("destinationId") == "!0badcafe"
    else:
        assert _row(q, dm_id) == {"status": "pending", "retry_count": 0}


@pytest.mark.parametrize("leg", _LEGS)
def test_S6_reconnect_over_an_old_handle_exact_event_list(bridge, leg):
    old, new = MagicMock(), _Radio()
    setattr(bridge, f"_{leg}_interface", old); setattr(bridge, f"_{leg}_connected", False)
    seen = []
    bridge.register_status_callback(lambda s, d: seen.append((s, d[leg]["connected"])))
    with patch.object(bridge, "_connect_interface", return_value=(new, True)):
        getattr(bridge, f"_connect_{leg}")()
    assert seen == [(f"{leg}_connected", True)]
    assert old.close.call_count == 1
    assert getattr(bridge, f"_{leg}_interface") is new


@pytest.mark.parametrize("leg", _LEGS)
def test_S7_refused_install_and_failing_notify_per_leg(bridge, leg):
    seen = []
    bridge.register_status_callback(lambda s, d: seen.append(s))
    r = _Radio()
    with patch.object(bridge, "get_status", side_effect=RuntimeError("sqlite gone")):
        bridge._install_or_close(leg, r, True)
    assert getattr(bridge, f"_{leg}_interface") is r and getattr(bridge, f"_{leg}_connected")
    bridge._stop_event.set()
    m = MagicMock()
    bridge._install_or_close(leg, m, True)
    assert seen == [] and m.close.call_count == 1
    assert getattr(bridge, f"_{leg}_interface") is r     # the refused one never replaced it


def test_S8_both_queues_loaded_both_legs_down_then_up(bridge):
    _set_leg(bridge, "primary", "none"); _set_leg(bridge, "secondary", "marked")
    bridge._primary_downlink = None
    qp, ip, tp = _enqueue_row(bridge, "bcast", "primary")
    qs, i_s, ts = _enqueue_row(bridge, "bcast", "secondary")
    _loop_pass(bridge, 4)
    assert _row(qp, ip)["status"] == _row(qs, i_s)["status"] == "pending"
    assert bridge.get_status()["queue"] == {
        "p2s_pending": 1, "s2p_pending": 1,
        "p2s_held_link_down": True, "s2p_held_link_down": True}
    rp, rs = _set_leg(bridge, "primary", "up"), _set_leg(bridge, "secondary", "up")
    _loop_pass(bridge, 2)
    assert len(rp.sent) == 1 and rp.sent[0].endswith(tp)
    assert len(rs.sent) == 1 and rs.sent[0].endswith(ts)


@pytest.mark.parametrize("dest", _LEGS)
def test_S9_a_deferred_row_is_due_again_in_LINK_DOWN_RETRY_S(bridge, dest):
    """The use-site of the constant: every tree helper collapses retry_after,
    so SendDeferred(300.0) / (0.0) at the raise pass every test."""
    from gateway.mesh_bridge import LINK_DOWN_RETRY_S
    _set_leg(bridge, dest, "none"); _set_leg(bridge, OTHER[dest], "up")
    bridge._primary_downlink = None
    q, mid, _ = _enqueue_row(bridge, "bcast", dest)
    t0 = datetime.now()
    _real_backoff_passes(bridge, 1)
    (ra,) = _sql(q, "SELECT retry_after FROM messages WHERE id = ?", (mid,))[0]
    due_in = (datetime.fromisoformat(ra) - t0).total_seconds()
    assert LINK_DOWN_RETRY_S - 0.01 <= due_in <= LINK_DOWN_RETRY_S + 1.0, due_in


@pytest.mark.parametrize("outcome", ["ok", "fail"])
@pytest.mark.parametrize("leg", _LEGS)
def test_S10_the_real_leg_loop_emits_the_exact_event_list(bridge, leg, outcome):
    """One frame ABOVE _connect_<leg>: the connect table calls the connector
    wrapper directly, so an announce in _leg_loop passes it."""
    radio = _Radio()
    got = (radio, True) if outcome == "ok" else (None, False)
    seen = []
    bridge.register_status_callback(lambda s, d: seen.append((s, d[leg]["connected"])))
    bridge._running = True
    with patch.object(bridge, "_connect_interface", return_value=got), \
            patch.object(bridge._stop_event, "wait", lambda *_: True):
        bridge._leg_loop(leg)
    assert seen == ([(f"{leg}_connected", True)] if outcome == "ok" else [])
