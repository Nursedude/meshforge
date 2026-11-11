"""BR-T #14 — a message that arrives while the OTHER leg is down for longer than
the queue's retry budget was dead-lettered and never delivered.

Found by the 11-10 r6 review by running and re-measured on the TCP leg by the
MA-port review (.claude/research/landscape_2026_11_09.md §B #14): serial unplug
→ refused at +0.26 s and +5.4 s → ``dead_letter unknown_error_exhausted``; the
leg came back at +10.3 s and the message never crossed. The bridge's senders
return a bare ``False`` for "link down", which the RetryPolicy classifies as
``unknown`` (2 tries), so a link outage spent the budget meant for send errors.

The fix is the queue's existing outage contract (RNS precedent, F4 review R2):
a ``ready_fn`` per leg. While the leg cannot take a write, its rows are left
untouched — no attempt, no retry_count bump — and they age out WITNESSED at
``MESH_BRIDGE_MAX_AGE_S`` instead of waiting forever.

Real ``PersistentMessageQueue`` (tmp sqlite) and the real bridge sender; only
the radio is replaced.
"""
import sqlite3
from unittest.mock import patch

import pytest

from gateway.config import GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig


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


def _enqueue_for_primary(bridge, text):
    import uuid
    from gateway.mesh_bridge import BridgedMeshMessage
    msg = BridgedMeshMessage(
        source_preset="SHORT_TURBO", source_id="!b03bb70c", destination_id="^all",
        content=f"{text} {uuid.uuid4().hex[:8]}", channel=0, is_broadcast=True)
    q = bridge._secondary_to_primary
    msg_id = q.enqueue(msg.to_payload(), destination="primary")
    assert msg_id
    return q, msg_id, msg.content


def _row(q, msg_id):
    conn = sqlite3.connect(q._db_path)
    try:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute(
            "SELECT status, retry_count FROM messages WHERE id = ?",
            (msg_id,)).fetchone())
    finally:
        conn.close()


def _make_every_row_due(q):
    """Collapse the retry backoff so each process_once is the NEXT attempt —
    the outage then outlasts any retry budget in a handful of ticks."""
    conn = sqlite3.connect(q._db_path)
    try:
        conn.execute("UPDATE messages SET retry_after = NULL")
        conn.commit()
    finally:
        conn.close()


def _tick(q, n):
    with patch("gateway.mesh_bridge.assert_iface_tx_allowed"):
        for _ in range(n):
            _make_every_row_due(q)
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
        """#10's window (flag still True, library write would no-op) is a link
        fact, read by the same ``_write_target_gone`` the send path refuses on."""
        q, msg_id, _ = _enqueue_for_primary(bridge, "socketless")
        bridge._primary_interface, bridge._primary_connected = _Radio(), True
        with patch("gateway.mesh_bridge._write_target_gone", return_value=True):
            _tick(q, 6)
        row = _row(q, msg_id)
        assert row["status"] == "pending"
        assert row["retry_count"] == 0

    def test_a_send_error_on_a_live_leg_still_spends_the_budget(self, bridge):
        """Control that can fail: the gate must not swallow REAL send failures
        (an interface refusing the write on a link that is up)."""
        q, msg_id, _ = _enqueue_for_primary(bridge, "refused")

        class _Refuses(_Radio):
            def sendText(self, text, **kw):
                return False

        bridge._primary_interface, bridge._primary_connected = _Refuses(), True
        _tick(q, 6)
        assert _row(q, msg_id)["status"] == "dead_letter"

    def test_the_status_says_the_leg_is_holding(self, bridge):
        q, _, _ = _enqueue_for_primary(bridge, "visible")
        bridge._primary_interface, bridge._primary_connected = None, False
        _tick(q, 1)
        status = bridge.get_status()["queue"]
        assert status["s2p_held_link_down"] is True
        assert status["p2s_held_link_down"] is False

    def test_held_rows_age_out_witnessed_not_forever(self, bridge):
        from gateway.mesh_bridge import MESH_BRIDGE_MAX_AGE_S
        assert 0 < MESH_BRIDGE_MAX_AGE_S
        for q, dest in ((bridge._secondary_to_primary, "primary"),
                        (bridge._primary_to_secondary, "secondary")):
            assert q._send_max_age.get(dest) == MESH_BRIDGE_MAX_AGE_S
