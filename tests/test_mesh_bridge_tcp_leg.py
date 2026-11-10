"""BR-T #8 / #10 / #11 / #12 — the default (TCP) leg says it is connected while
it cannot hear, cannot be heard, or cannot come back.

Found by the 11-09 and 11-10 reviews by running
(.claude/research/landscape_2026_11_09.md §B):

- #8  the TCP RX listener was a bare closure. pypubsub holds listeners WEAKLY,
      so it was garbage-collected after connect and RX died silently; it had no
      interface filter either, so a second meshtastic interface in the process
      leaked into the leg. The serial leg fixed both on 2026-06-03.
- #10 a dead TCP leg was still booked as sent. The library's ``_writeBytes`` is
      a silent no-op when ``socket is None`` (a failed ``_reconnect()`` leaves it
      so) and ``sendText`` still returns a packet. Only the serial leg listened
      for ``meshtastic.connection.lost``, so the TCP leg never left "connected".
- #11 ``_connect_tcp`` holds ``MESHTASTIC_CONNECTION_LOCK`` (a plain Lock) "until
      disconnect", but nothing released it — the first reconnect after any loss
      waited 10 s on a lock this process held, and failed, forever.
- #12 ``_connect_tcp`` ignored ``config.port``: a leg configured for :4404
      connected to the library default :4403 — the OTHER leg's radio.

The library is REAL here wherever its behaviour is the claim (pubsub delivery
and weak references, ``TCPInterface`` socket state, the ``connection.lost``
publication from ``_disconnected()``); fakes only replace the network.
"""
import gc
import logging
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Queues under tmp (never the live ~/.config/meshforge/mesh_bridge_queues),
    and the process-global connection lock + pubsub topics left as found:
    on the OLD code a test here leaves the lock held, which would poison every
    later test in the run that takes it."""
    import gateway.mesh_bridge as mb
    from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK
    from pubsub import pub
    monkeypatch.setattr(mb, "get_real_user_home", lambda: tmp_path)
    assert not MESHTASTIC_CONNECTION_LOCK.locked(), "lock held before the test"
    yield
    if MESHTASTIC_CONNECTION_LOCK.locked():
        MESHTASTIC_CONNECTION_LOCK.release()
    for topic in ("meshtastic.receive", "meshtastic.connection.lost"):
        try:
            pub.unsubAll(topic)
        except Exception:  # topic never created in this test
            pass


@pytest.fixture
def config():
    cfg = GatewayConfig()
    cfg.enabled = True
    cfg.bridge_mode = "mesh_bridge"
    cfg.mesh_bridge = MeshtasticBridgeConfig(
        enabled=True,
        primary=MeshtasticConfig(host="primary.invalid", port=4403, preset="LONG_FAST"),
        secondary=MeshtasticConfig(host="secondary.invalid", port=4404,
                                   preset="SHORT_TURBO"),
        direction="bidirectional",
        add_prefix=True,
        prefix_format="[Mesh:{source_preset}] ",
    )
    return cfg


@pytest.fixture
def bridge(config):
    from gateway.mesh_bridge import MeshtasticPresetBridge
    return MeshtasticPresetBridge(config=config)


def _connect_tcp(bridge, leg, callback, iface):
    """Run the REAL _connect_tcp with the network replaced by ``iface`` and the
    REAL pubsub library. Returns (interface, ok, the TCPInterface mock)."""
    from pubsub import pub as real_pub
    fake_tcp = MagicMock()
    fake_tcp.TCPInterface = MagicMock(return_value=iface)
    cfg = getattr(bridge.bridge_config, leg)
    with patch("gateway.mesh_bridge._HAS_MESHTASTIC", True), \
         patch("gateway.mesh_bridge._HAS_MESHTASTIC_TCP", True), \
         patch("gateway.mesh_bridge._HAS_PUBSUB", True), \
         patch("gateway.mesh_bridge._meshtastic_tcp", fake_tcp), \
         patch("gateway.mesh_bridge._pub", real_pub), \
         patch("utils.meshtastic_connection.wait_for_cooldown"), \
         patch("utils.service_check.check_service"):
        got, ok = bridge._connect_tcp(cfg, leg, callback)
    gc.collect()  # the weakref killer
    return got, ok, fake_tcp.TCPInterface


def _packet():
    return {"decoded": {"portnum": "TEXT_MESSAGE_APP", "payload": "hi"}}


def _msg(**kw):
    """Unique content per call: the RF TX registry is process-global, and a
    repeat of an earlier test's text is (correctly) suppressed as dual-path."""
    import uuid
    from gateway.mesh_bridge import BridgedMeshMessage
    base = dict(source_preset="SHORT_TURBO", source_id="!b03bb70c",
                destination_id="^all", content=f"hello from ST {uuid.uuid4().hex[:8]}",
                channel=0, is_broadcast=True)
    base.update(kw)
    return BridgedMeshMessage(**base)


def _tx_lines(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == "gateway.mesh_bridge"
            and r.getMessage().startswith("mesh_bridge tx ")]


def _real_tcp_iface():
    """A REAL library TCPInterface with no socket — the state a failed
    ``_reconnect()`` or a ``close()`` leaves. connectNow=False: no network."""
    from meshtastic.tcp_interface import TCPInterface
    iface = TCPInterface(hostname="tcp.invalid", connectNow=False)
    assert iface.socket is None
    return iface


# ---------------------------------------------------------------- #8 RX ---

class TestTcpRxListener:

    def test_listener_survives_gc_and_delivers(self, bridge):
        from pubsub import pub
        received = []
        iface, ok, _ = _connect_tcp(bridge, "primary", received.append, MagicMock())
        assert ok is True
        pub.sendMessage("meshtastic.receive", packet=_packet(), interface=iface)
        assert len(received) == 1

    def test_listener_filters_foreign_interfaces(self, bridge):
        from pubsub import pub
        received = []
        _connect_tcp(bridge, "primary", received.append, MagicMock())
        pub.sendMessage("meshtastic.receive", packet=_packet(), interface=MagicMock())
        assert received == []

    def test_two_tcp_legs_each_hear_only_their_own_radio(self, bridge):
        from pubsub import pub
        from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK
        got_p, got_s = [], []
        ip, okp, _ = _connect_tcp(bridge, "primary", got_p.append, MagicMock())
        # the two legs share one process lock today; this test is about RX
        # isolation, so hand the lock back between the two connects
        if MESHTASTIC_CONNECTION_LOCK.locked():
            MESHTASTIC_CONNECTION_LOCK.release()
        isec, oks, _ = _connect_tcp(bridge, "secondary", got_s.append, MagicMock())
        assert okp and oks
        pub.sendMessage("meshtastic.receive", packet=_packet(), interface=ip)
        assert (len(got_p), len(got_s)) == (1, 0)
        pub.sendMessage("meshtastic.receive", packet=_packet(), interface=isec)
        assert (len(got_p), len(got_s)) == (1, 1)


# ------------------------------------------------------ #10 link loss ---

class TestTcpLinkLost:

    def test_connection_lost_marks_the_leg_disconnected(self, bridge, caplog):
        from pubsub import pub
        caplog.set_level(logging.INFO, logger="gateway.mesh_bridge")
        iface, ok, _ = _connect_tcp(bridge, "primary", lambda p: None, MagicMock())
        bridge._primary_interface, bridge._primary_connected = iface, ok
        statuses = []
        bridge._notify_status = statuses.append
        pub.sendMessage("meshtastic.connection.lost", interface=iface)
        assert bridge._primary_connected is False
        assert bridge._primary_interface is None
        assert bridge.stats["link_lost"] == 1
        assert "primary_disconnected" in statuses
        assert any("primary tcp link LOST" in r.getMessage() for r in caplog.records)

    def test_foreign_lost_event_changes_nothing(self, bridge):
        from pubsub import pub
        iface, ok, _ = _connect_tcp(bridge, "primary", lambda p: None, MagicMock())
        bridge._primary_interface, bridge._primary_connected = iface, ok
        pub.sendMessage("meshtastic.connection.lost", interface=MagicMock())
        assert bridge._primary_connected is True
        assert bridge.stats["link_lost"] == 0

    def test_real_library_disconnect_reaches_the_leg(self, bridge):
        """End to end with the REAL library: when its reader thread exits (a
        failed reconnect raises OSError inside it), ``_disconnected()`` publishes
        connection.lost from the library's own deferred publishing thread."""
        iface = _real_tcp_iface()
        got, ok, _ = _connect_tcp(bridge, "primary", lambda p: None, iface)
        bridge._primary_interface, bridge._primary_connected = got, ok
        iface._disconnected()          # what the reader's finally: runs
        deadline = time.monotonic() + 5
        while bridge._primary_connected and time.monotonic() < deadline:
            time.sleep(0.02)
        assert bridge._primary_connected is False
        assert bridge.stats["link_lost"] == 1

    def test_a_write_the_library_would_drop_is_refused(self, bridge, caplog):
        """The window before connection.lost lands: ``socket is None`` and the
        library's write is a silent no-op. Measured at send time, not assumed."""
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        iface = _real_tcp_iface()
        bridge._primary_interface, bridge._primary_connected = iface, True
        errors_before = bridge.stats["errors"]
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"), \
             patch.object(type(iface), "sendText") as send:
            assert bridge._send_to_primary(_msg().to_payload()) is False
        assert send.call_count == 0            # never handed to a dead socket
        assert bridge.stats["messages_secondary_to_primary"] == 0
        assert bridge.stats["errors"] == errors_before + 1
        assert _tx_lines(caplog) == []
        assert any(r.levelno == logging.ERROR and "REFUSED" in r.getMessage()
                   for r in caplog.records)

    def test_a_refusal_leaves_the_leg_to_the_library(self, bridge):
        """11-10 review F1: tearing the leg down on a refusal, during the
        library's own 1 s ``_reconnect()`` window, closed an interface whose
        reader then opened a fresh socket nobody owned (a zombie on
        meshtasticd's ONE TCP slot). A refusal refuses; only connection.lost
        tears a leg down."""
        iface = _real_tcp_iface()
        bridge._primary_interface, bridge._primary_connected = iface, True
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"), \
             patch.object(type(iface), "sendText"), \
             patch.object(type(iface), "close") as close:
            assert bridge._send_to_primary(_msg().to_payload()) is False
        assert close.call_count == 0
        assert bridge._primary_interface is iface
        assert bridge._primary_connected is True
        assert bridge.stats["link_lost"] == 0

    def test_one_loss_seen_by_two_threads_is_one_loss(self, bridge):
        """11-10 review F2: a second teardown arriving while the first is still
        inside close() must find the leg already taken — one count, one close."""
        from pubsub import pub
        iface, ok, _ = _connect_tcp(bridge, "primary", lambda p: None, MagicMock())
        bridge._primary_interface, bridge._primary_connected = iface, ok
        second = []

        def close_slowly():
            if second:          # once only: on broken code the second teardown
                return          # closes again, which must not recurse (hang)
            second.append(None)
            t = threading.Thread(target=lambda: pub.sendMessage(
                "meshtastic.connection.lost", interface=iface))
            t.start()
            t.join(5)
            second.append(t.is_alive())

        iface.close.side_effect = close_slowly
        pub.sendMessage("meshtastic.connection.lost", interface=iface)
        assert second == [None, False]
        assert bridge.stats["link_lost"] == 1
        assert iface.close.call_count == 1

    def test_a_late_loss_never_takes_the_handle_that_replaced_it(self, bridge):
        """The claim is identity-checked INSIDE the lock: a reconnect that swaps
        in a new handle between the lost handler's first look and its claim
        must leave the new handle alone."""
        old, new = MagicMock(), MagicMock()
        bridge._primary_interface, bridge._primary_connected = new, True
        assert bridge._take_leg("primary", expected=old) is None
        assert bridge._primary_interface is new
        assert bridge._primary_connected is True
        assert bridge._take_leg("primary", expected=new) is new
        assert bridge._primary_interface is None
        assert bridge._primary_connected is False

    def test_planned_stop_on_a_tcp_leg_says_tcp(self, bridge, caplog):
        from pubsub import pub
        caplog.set_level(logging.INFO, logger="gateway.mesh_bridge")
        iface, ok, _ = _connect_tcp(bridge, "primary", lambda p: None, MagicMock())
        bridge._primary_interface, bridge._primary_connected = iface, ok
        bridge._stop_event.set()
        pub.sendMessage("meshtastic.connection.lost", interface=iface)
        assert bridge.stats["link_lost"] == 0
        assert any("primary tcp link closed" in r.getMessage() for r in caplog.records)

    def test_downlink_inject_does_not_need_the_socket(self, bridge):
        """11-10 review F4: the true-origin downlink goes through the broker,
        not the TCP socket — a dead socket must not refuse it."""
        iface = _real_tcp_iface()
        bridge._primary_interface, bridge._primary_connected = iface, True
        bridge._primary_downlink = MagicMock()
        bridge._primary_downlink.inject.return_value = True
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"), \
             patch.object(bridge, "_ensure_nodeinfo", create=True), \
             patch.object(type(iface), "sendText") as send:
            assert bridge._send_to_primary(_msg().to_payload()) is True
        assert bridge._primary_downlink.inject.call_count == 1
        assert send.call_count == 0

    def test_a_live_socket_is_not_refused(self, bridge, caplog):
        """Control: a real TCPInterface WITH a socket goes through."""
        caplog.set_level(logging.DEBUG, logger="gateway.mesh_bridge")
        iface = _real_tcp_iface()
        iface.socket = MagicMock()
        bridge._primary_interface, bridge._primary_connected = iface, True
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"), \
             patch.object(type(iface), "sendText", return_value=object()) as send:
            assert bridge._send_to_primary(_msg().to_payload()) is True
        assert send.call_count == 1
        assert len(_tx_lines(caplog)) == 1

    def test_closed_serial_stream_is_refused_too(self, bridge):
        """Same library no-op on the serial leg (``if self.stream:``), in the
        window before its connection.lost lands."""
        from meshtastic.stream_interface import StreamInterface
        iface = StreamInterface.__new__(StreamInterface)
        iface.stream = None
        bridge._secondary_interface, bridge._secondary_connected = iface, True
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"), \
             patch.object(StreamInterface, "sendText", create=True) as send:
            assert bridge._send_to_secondary(
                _msg(source_preset="LONG_FAST").to_payload()) is False
        assert send.call_count == 0
        assert bridge.stats["messages_primary_to_secondary"] == 0
        assert bridge._secondary_interface is iface        # left to the library


# ------------------------------------------------ #11 lock on disconnect ---

class TestTcpLockReleasedOnDisconnect:

    @pytest.mark.parametrize("leg", ["primary", "secondary"])
    def test_disconnect_releases_the_lock_this_leg_took(self, bridge, leg):
        from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK
        iface, ok, _ = _connect_tcp(bridge, leg, lambda p: None, MagicMock())
        assert ok and MESHTASTIC_CONNECTION_LOCK.locked()
        setattr(bridge, f"_{leg}_interface", iface)
        setattr(bridge, f"_{leg}_connected", True)
        getattr(bridge, f"_disconnect_{leg}")()
        assert not MESHTASTIC_CONNECTION_LOCK.locked()
        assert bridge._tcp_lock_owner is None          # 11-10 review M1

    def test_a_stale_handle_never_frees_another_holders_lock(self, bridge):
        """M1: after a release, the OLD handle must no longer count as owner —
        or a late second teardown frees whoever holds the lock next."""
        from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK
        iface, ok, _ = _connect_tcp(bridge, "primary", lambda p: None, MagicMock())
        bridge._primary_interface, bridge._primary_connected = iface, ok
        bridge._disconnect_primary()
        assert MESHTASTIC_CONNECTION_LOCK.acquire(timeout=1)    # someone else's
        try:
            bridge._release_tcp_lock_if_owner(iface)            # late, stale
            assert MESHTASTIC_CONNECTION_LOCK.locked()
        finally:
            MESHTASTIC_CONNECTION_LOCK.release()

    def test_release_of_an_already_unlocked_lock_is_witnessed_not_raised(
            self, bridge, caplog):
        """M4: if something released our hold out from under us, teardown must
        say so and carry on, not raise out of a disconnect."""
        caplog.set_level(logging.WARNING, logger="gateway.mesh_bridge")
        iface = MagicMock()
        bridge._tcp_lock_owner = iface                 # owner, lock NOT held
        bridge._release_tcp_lock_if_owner(iface)
        assert bridge._tcp_lock_owner is None
        assert any("already released" in r.getMessage() for r in caplog.records)

    def test_a_connect_that_fails_after_construction_leaves_nothing_held(
            self, bridge):
        """11-10 review F5: _notify_status raising after the interface exists
        must release the lock, forget the owner AND close the interface."""
        from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK
        bridge._notify_status = MagicMock(side_effect=RuntimeError("status boom"))
        made = MagicMock()
        iface, ok, _ = _connect_tcp(bridge, "primary", lambda p: None, made)
        assert (iface, ok) == (None, False)
        assert not MESHTASTIC_CONNECTION_LOCK.locked()
        assert bridge._tcp_lock_owner is None
        made.close.assert_called_once()

    def test_lost_then_reconnect_succeeds(self, bridge):
        from pubsub import pub
        iface, ok, _ = _connect_tcp(bridge, "primary", lambda p: None, MagicMock())
        bridge._primary_interface, bridge._primary_connected = iface, ok
        pub.sendMessage("meshtastic.connection.lost", interface=iface)
        t0 = time.monotonic()
        again, ok2, _ = _connect_tcp(bridge, "primary", lambda p: None, MagicMock())
        assert ok2 is True and again is not None
        assert time.monotonic() - t0 < 5      # not the 10 s lock wait

    @pytest.mark.parametrize("leg", ["primary", "secondary"])   # M2: both legs
    def test_loop_error_branch_then_reconnect_succeeds(self, bridge, leg):
        """The OTHER way a leg goes down: the leg loop's except sets
        ``_<leg>_connected = False`` without a disconnect, leaving the old
        handle holding the lock. The next connect must not wait on it."""
        from pubsub import pub as real_pub
        iface, ok, _ = _connect_tcp(bridge, leg, lambda p: None, MagicMock())
        setattr(bridge, f"_{leg}_interface", iface)
        setattr(bridge, f"_{leg}_connected", False)  # what the loop's except does
        fake_tcp = MagicMock()
        fake_tcp.TCPInterface = MagicMock(return_value=MagicMock())
        t0 = time.monotonic()
        with patch("gateway.mesh_bridge._HAS_MESHTASTIC", True), \
             patch("gateway.mesh_bridge._HAS_MESHTASTIC_TCP", True), \
             patch("gateway.mesh_bridge._HAS_PUBSUB", True), \
             patch("gateway.mesh_bridge._meshtastic_tcp", fake_tcp), \
             patch("gateway.mesh_bridge._pub", real_pub), \
             patch("utils.meshtastic_connection.wait_for_cooldown"), \
             patch("utils.service_check.check_service"):
            getattr(bridge, f"_connect_{leg}")()
        assert getattr(bridge, f"_{leg}_connected") is True
        assert time.monotonic() - t0 < 5
        iface.close.assert_called_once()             # the stale handle was closed

    def test_disconnect_never_releases_a_lock_it_did_not_take(self, bridge):
        """An MQTT/serial leg, or a TCP connect that failed, holds nothing:
        releasing would free ANOTHER component's hold (or raise)."""
        from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK
        assert MESHTASTIC_CONNECTION_LOCK.acquire(timeout=1)   # someone else's
        try:
            bridge._primary_interface = MagicMock()
            bridge._primary_connected = True
            bridge._disconnect_primary()
            assert MESHTASTIC_CONNECTION_LOCK.locked()
        finally:
            MESHTASTIC_CONNECTION_LOCK.release()


# ------------------------------------------------------ #12 the port ---

@pytest.mark.parametrize("leg,port", [("primary", 4403), ("secondary", 4404)])
def test_tcp_leg_connects_to_its_configured_port(bridge, leg, port):
    _, ok, ctor = _connect_tcp(bridge, leg, lambda p: None, MagicMock())
    assert ok
    kwargs = ctor.call_args.kwargs
    assert kwargs.get("portNumber") == port
    assert kwargs.get("hostname") == f"{leg}.invalid"
