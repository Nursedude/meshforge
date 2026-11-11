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
