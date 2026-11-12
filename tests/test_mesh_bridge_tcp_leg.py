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
    import utils.meshtastic_connection as mc
    for lk in [MESHTASTIC_CONNECTION_LOCK] + list(mc._port_locks.values()):
        if lk.locked():   # per-endpoint since BR-T #13: release them ALL
            lk.release()
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
        primary=MeshtasticConfig(host="192.0.2.1", port=4403, preset="LONG_FAST"),   # TEST-NET-1: never local, no DNS
        secondary=MeshtasticConfig(host="192.0.2.2", port=4404,
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


def _leg_lock(bridge, leg):
    """The lock THIS leg takes (per endpoint since BR-T #13)."""
    from utils.meshtastic_connection import connection_lock_for
    cfg = getattr(bridge.bridge_config, leg)
    return connection_lock_for(cfg.host, cfg.port)


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
        got_p, got_s = [], []
        ip, okp, _ = _connect_tcp(bridge, "primary", got_p.append, MagicMock())
        # no lock hand-back between the connects: two endpoints, two locks (#13)
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
        from gateway.message_queue_models import SendDeferred
        errors_before = bridge.stats["errors"]
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"), \
             patch.object(type(iface), "sendText") as send, \
             pytest.raises(SendDeferred):      # BR-T #14: held, not a send error
            bridge._send_to_primary(_msg().to_payload())
        assert send.call_count == 0            # never handed to a dead socket
        assert bridge.stats["messages_secondary_to_primary"] == 0
        assert bridge.stats["errors"] == errors_before
        assert bridge.stats["link_down_deferrals"] == 1
        assert _tx_lines(caplog) == []
        assert any(r.levelno == logging.ERROR and "DEFERRED" in r.getMessage()
                   for r in caplog.records)

    def test_a_refusal_leaves_the_leg_to_the_library(self, bridge):
        """11-10 review F1: tearing the leg down on a refusal, during the
        library's own 1 s ``_reconnect()`` window, closed an interface whose
        reader then opened a fresh socket nobody owned (a zombie on
        meshtasticd's ONE TCP slot). A refusal refuses; only connection.lost
        tears a leg down."""
        iface = _real_tcp_iface()
        bridge._primary_interface, bridge._primary_connected = iface, True
        from gateway.message_queue_models import SendDeferred
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"), \
             patch.object(type(iface), "sendText"), \
             patch.object(type(iface), "close") as close, \
             pytest.raises(SendDeferred):
            bridge._send_to_primary(_msg().to_payload())
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

    def test_the_real_stop_path_says_planned_close(self, bridge, caplog):
        """11-10 review r2 F-C: the old test sent connection.lost by hand while
        the handle was still assigned — a path stop() never takes. Drive stop()
        itself: the close must read planned, by the leg's kind, and not count."""
        from pubsub import pub
        caplog.set_level(logging.INFO, logger="gateway.mesh_bridge")
        iface, ok, _ = _connect_tcp(bridge, "primary", lambda p: None, MagicMock())
        bridge._install_leg("primary", iface, ok)
        # what the library does when stop() closes it: publish connection.lost
        iface.close.side_effect = lambda: pub.sendMessage(
            "meshtastic.connection.lost", interface=iface)
        bridge._running = True
        bridge.stop()
        assert bridge.stats["link_lost"] == 0
        assert any("primary tcp link closed" in r.getMessage() and "stopping" in
                   r.getMessage() for r in caplog.records)
        assert not any("LOST" in r.getMessage() for r in caplog.records)

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
        from gateway.message_queue_models import SendDeferred
        with patch("gateway.mesh_bridge.assert_iface_tx_allowed"), \
             patch.object(StreamInterface, "sendText", create=True) as send, \
             pytest.raises(SendDeferred):
            bridge._send_to_secondary(
                _msg(source_preset="LONG_FAST").to_payload())
        assert send.call_count == 0
        assert bridge.stats["messages_primary_to_secondary"] == 0
        assert bridge._secondary_interface is iface        # left to the library


# ------------------------------------------------ #11 lock on disconnect ---

class TestTcpLockReleasedOnDisconnect:

    @pytest.mark.parametrize("leg", ["primary", "secondary"])
    def test_disconnect_releases_the_lock_this_leg_took(self, bridge, leg):
        lk = _leg_lock(bridge, leg)
        iface, ok, _ = _connect_tcp(bridge, leg, lambda p: None, MagicMock())
        assert ok and lk.locked()
        bridge._install_leg(leg, iface, True)
        getattr(bridge, f"_disconnect_{leg}")()
        assert not lk.locked()
        assert iface not in bridge._tcp_lock_holds     # 11-10 review M1

    def test_a_stale_handle_never_frees_another_holders_lock(self, bridge):
        """M1: after a release, the OLD handle must no longer count as owner —
        or a late second teardown frees whoever holds the lock next."""
        lk = _leg_lock(bridge, "primary")
        iface, ok, _ = _connect_tcp(bridge, "primary", lambda p: None, MagicMock())
        bridge._install_leg("primary", iface, ok)
        bridge._disconnect_primary()
        assert lk.acquire(timeout=1)                            # someone else's
        try:
            bridge._release_tcp_lock_if_owner(iface)            # late, stale
            assert lk.locked()
        finally:
            lk.release()

    def test_release_of_an_already_unlocked_lock_is_witnessed_not_raised(
            self, bridge, caplog):
        """M4: if something released our hold out from under us, teardown must
        say so and carry on, not raise out of a disconnect."""
        caplog.set_level(logging.WARNING, logger="gateway.mesh_bridge")
        iface = MagicMock()
        from utils.meshtastic_connection import MESHTASTIC_CONNECTION_LOCK
        bridge._tcp_lock_holds[iface] = MESHTASTIC_CONNECTION_LOCK   # owner, lock NOT held
        bridge._release_tcp_lock_if_owner(iface)
        assert iface not in bridge._tcp_lock_holds
        assert any("already released" in r.getMessage() for r in caplog.records)

    def test_a_connect_that_fails_after_construction_leaves_nothing_held(
            self, bridge):
        """11-10 review F5: a step raising after the interface exists must
        release the lock, forget the owner AND close the interface. (§B #21
        moved the status notify out of _connect_tcp; the listener attach is the
        post-construction step that remains.)"""
        lk = _leg_lock(bridge, "primary")
        bridge._attach_stream_listeners = MagicMock(side_effect=RuntimeError("attach boom"))
        made = MagicMock()
        iface, ok, _ = _connect_tcp(bridge, "primary", lambda p: None, made)
        assert (iface, ok) == (None, False)
        assert not lk.locked()
        assert bridge._tcp_lock_holds == {}
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
    assert kwargs.get("hostname") == {"primary": "192.0.2.1", "secondary": "192.0.2.2"}[leg]


# ------------------------------------- r2: one writer for leg state ---

class TestLegStateHasOneWriter:
    """11-10 review r1 F2 and r2 F-A/F-B were ONE defect kind twice: a leg's
    handle/connected pair written non-atomically from several places. The seam:
    every write goes through ``_install_leg`` / ``_take_leg`` / ``_mark_leg_down``
    under ``_leg_lock``; this gate refuses a new writer anywhere else."""

    LEG_FIELDS = {"_primary_interface", "_secondary_interface",
                  "_primary_connected", "_secondary_connected"}
    WRITERS = {"__init__", "_install_leg", "_take_leg", "_mark_leg_down"}

    def test_only_the_seam_writes_leg_state(self):
        import inspect
        import gateway.mesh_bridge as mb
        offenders = self._offenders(inspect.getsource(mb))
        assert offenders == [], offenders

    @pytest.mark.parametrize("planted", [
        "        self._primary_connected = False\n",
        "        self._secondary_interface, self._secondary_connected = x, True\n",
        "        setattr(self, '_primary_interface', None)\n",
    ])
    def test_the_gate_catches_a_planted_writer(self, planted):
        """The REAL gate must be able to FAIL: plant each writer shape outside
        the seam and require a hit; the same line inside a writer is allowed."""
        outside = "class B:\n    def _primary_loop(self):\n" + planted
        inside = "class B:\n    def _install_leg(self):\n" + planted
        assert self._offenders(outside)
        assert self._offenders(inside) == []

    def _offenders(self, source):
        import ast
        tree = ast.parse(source)
        offenders = []
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                    targets = [node.target]
                flat = []
                for t in targets:
                    flat.extend(t.elts if isinstance(t, ast.Tuple) else [t])
                for t in flat:
                    if (isinstance(t, ast.Attribute) and t.attr in self.LEG_FIELDS
                            and isinstance(t.value, ast.Name) and t.value.id == "self"
                            and fn.name not in self.WRITERS):
                        offenders.append(f"{fn.name}:{node.lineno} {t.attr}")
                # setattr(self, f"_{leg}_...") is the seam's own idiom: allowed
                # only inside the writers
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "setattr" and fn.name not in self.WRITERS
                        and node.args and isinstance(node.args[0], ast.Name)
                        and node.args[0].id == "self"):
                    offenders.append(f"{fn.name}:{node.lineno} setattr(self, …)")
        return offenders

    def test_take_leg_holds_the_leg_lock(self, bridge):
        """M10: the claim is atomic only under the lock."""
        entered = []

        class Recording:
            def __enter__(self):
                entered.append(True)
            def __exit__(self, *a):
                return False

        bridge._primary_interface, bridge._primary_connected = MagicMock(), True
        bridge._leg_lock = Recording()
        bridge._take_leg("primary")
        assert entered == [True]

    def test_install_leg_holds_the_leg_lock(self, bridge):
        entered = []

        class Recording:
            def __enter__(self):
                entered.append(True)
            def __exit__(self, *a):
                return False

        bridge._leg_lock = Recording()
        bridge._install_leg("primary", MagicMock(), True)
        assert entered == [True]

    def test_mark_down_with_no_handle_still_marks_down(self, bridge):
        """M32: connected=True with no handle (a state F-B could make) must not
        survive a disconnect."""
        bridge._primary_interface, bridge._primary_connected = None, True
        bridge._disconnect_primary()
        assert bridge._primary_connected is False


class TestLossCountedOnlyByTheClaimant:

    def test_a_racer_that_loses_the_claim_counts_nothing(self, bridge, caplog):
        """M41: the identity pre-check passed, the claim did not — no count,
        no LOST line (the winner already said it)."""
        caplog.set_level(logging.INFO, logger="gateway.mesh_bridge")
        iface = MagicMock()
        bridge._primary_interface, bridge._primary_connected = iface, True
        with patch.object(bridge, "_take_leg", return_value=None):
            bridge._on_stream_link_lost("primary", iface, "tcp")
        assert bridge.stats["link_lost"] == 0
        assert not any("LOST" in r.getMessage() for r in caplog.records)

    def test_the_loss_site_claims_only_the_handle_it_was_told_about(self, bridge):
        """M7: the claim at the loss site is identity-checked."""
        iface = MagicMock()
        bridge._primary_interface, bridge._primary_connected = iface, True
        with patch.object(bridge, "_take_leg", wraps=bridge._take_leg) as take:
            bridge._on_stream_link_lost("primary", iface, "tcp")
        assert take.call_args.kwargs.get("expected") is iface

    def test_a_close_that_raises_still_frees_the_lock(self, bridge):
        """M40: a failing close() is swallowed and the lock still released."""
        lk = _leg_lock(bridge, "primary")
        iface, ok, _ = _connect_tcp(bridge, "primary", lambda p: None, MagicMock())
        bridge._install_leg("primary", iface, ok)
        iface.close.side_effect = OSError("close boom")
        bridge._disconnect_primary()
        assert not lk.locked()
        assert bridge._primary_interface is None

    def test_serial_connect_site_names_serial(self, bridge, caplog):
        """M34: the production leg (moc's USB secondary) must log
        'secondary serial link LOST' — pinned at the connect site."""
        from pubsub import pub as real_pub
        caplog.set_level(logging.INFO, logger="gateway.mesh_bridge")
        fake_iface = MagicMock()
        fake_serial = MagicMock()
        fake_serial.SerialInterface = MagicMock(return_value=fake_iface)
        cfg = MeshtasticConfig(preset="SHORT_TURBO", connection_type="serial",
                               serial_device="/dev/null.invalid")
        with patch("gateway.mesh_bridge._HAS_MESHTASTIC", True), \
             patch("gateway.mesh_bridge._HAS_MESHTASTIC_SERIAL", True), \
             patch("gateway.mesh_bridge._HAS_PUBSUB", True), \
             patch("gateway.mesh_bridge._meshtastic_serial", fake_serial), \
             patch("gateway.mesh_bridge._pub", real_pub):
            iface, ok = bridge._connect_serial(cfg, "secondary", lambda p: None)
        bridge._install_leg("secondary", iface, ok)
        real_pub.sendMessage("meshtastic.connection.lost", interface=iface)
        assert any("secondary serial link LOST" in r.getMessage()
                   for r in caplog.records)


# ---------------------- r2 F-A: a missed connection.lost is never terminal ---

class TestLegLoopNoticesALibraryThatGaveUp:

    def _one_tick(self, bridge, leg):
        _tick_bounded(bridge, leg)        # one pass, then stop — and never a hang

    def test_a_loss_during_connect_is_caught_on_the_next_tick(self, bridge):
        """F-A: connection.lost fired BEFORE the handle was installed, so the
        identity check dropped it, and the leg read connected on a dead handle
        forever. The library's own isConnected says it gave up; the leg loop
        reads it every tick."""
        iface = _real_tcp_iface()          # isConnected NOT set: the dead state
        assert not iface.isConnected.is_set()
        bridge._install_leg("primary", iface, True)
        with patch.object(type(iface), "close"):
            self._one_tick(bridge, "primary")
        assert bridge._primary_connected is False
        assert bridge._primary_interface is None
        assert bridge.stats["link_lost"] == 1

    def test_a_live_library_link_is_left_alone(self, bridge):
        iface = _real_tcp_iface()
        iface.isConnected.set()
        iface.socket = MagicMock()
        bridge._install_leg("primary", iface, True)
        self._one_tick(bridge, "primary")
        assert bridge._primary_interface is iface
        assert bridge._primary_connected is True
        assert bridge.stats["link_lost"] == 0

    def test_mid_reconnect_is_left_to_the_library(self, bridge):
        """The library's own 1 s _reconnect: socket None, isConnected still SET
        (only _disconnected clears it) — not dead, do not tear down (r1 F1)."""
        iface = _real_tcp_iface()
        iface.isConnected.set()
        bridge._install_leg("primary", iface, True)
        self._one_tick(bridge, "primary")
        assert bridge._primary_interface is iface
        assert bridge.stats["link_lost"] == 0

    def test_a_non_library_leg_is_never_judged(self, bridge):
        """MQTT legs / fakes: not a meshtastic stream interface → untouched."""
        iface = MagicMock()
        bridge._install_leg("secondary", iface, True)
        self._one_tick(bridge, "secondary")
        assert bridge._secondary_interface is iface


class TestRound3Pins:
    """Survivors of the r3 mutant pass, pinned."""

    def test_an_event_named_isconnected_on_a_non_library_leg_is_not_judged(
            self, bridge):
        """Only the meshtastic library's stream interfaces carry the
        isConnected contract; anything else with the same attribute name is
        not read as 'gave up'."""
        from gateway.mesh_bridge import _library_gave_up

        class LookAlike:
            isConnected = threading.Event()        # never set

        assert _library_gave_up(LookAlike()) is False

    def test_no_handle_is_never_installed_as_connected(self, bridge):
        bridge._install_leg("primary", None, True)
        assert bridge._primary_connected is False

    def test_a_lost_event_during_stop_reads_planned_by_kind(self, bridge, caplog):
        """stop() sets the event, then tears down: a connection.lost landing in
        between still reads planned, with the leg's kind."""
        caplog.set_level(logging.INFO, logger="gateway.mesh_bridge")
        iface = MagicMock()
        bridge._install_leg("primary", iface, True)
        bridge._stop_event.set()
        bridge._on_stream_link_lost("primary", iface, "tcp")
        assert bridge.stats["link_lost"] == 0
        assert any("primary tcp link closed" in r.getMessage() for r in caplog.records)


# ------------------------------------------------- r3 review pins ---

def _tick_bounded(bridge, leg, waits_return=True, calls=None):
    """Run ONE leg-loop pass in a thread with a deadline: a mutant that drops a
    wait would otherwise spin forever and hang the run (local pytest has no
    timeout; CI kills at 30 s with no summary)."""
    calls = [] if calls is None else calls
    bridge._running = True
    bridge._stop_event = MagicMock()
    bridge._stop_event.wait = lambda t: (calls.append(f"wait{t}"), waits_return)[1]
    bridge._stop_event.is_set = lambda: False
    t = threading.Thread(target=getattr(bridge, f"_{leg}_loop"), daemon=True)
    t.start()
    t.join(5)
    spun = t.is_alive()
    if spun:                      # stop the spinner, or it starves every later
        bridge._running = False   # test of the GIL and the RUN hangs instead
        t.join(5)
    assert not spun, "leg loop spun without waiting"
    return calls


class TestRound3ReviewPins:

    def test_stop_racing_a_connect_never_installs_a_handle(self, bridge):
        """r3 finding 1: a connect that finishes after stop() began must not
        install a leg nobody will tear down (socket open, lock held)."""
        lk = _leg_lock(bridge, "primary")
        made = MagicMock()

        def connect_then_stop(cfg, name, cb):
            got = _connect_tcp(bridge, "primary", cb, made)[:2]
            bridge._stop_event.set()            # stop() lands mid-connect
            return got

        with patch.object(bridge, "_connect_interface", side_effect=connect_then_stop):
            bridge._connect_primary()
        assert bridge._primary_interface is None
        assert bridge._primary_connected is False
        made.close.assert_called_once()
        assert not lk.locked()

    def test_stop_with_an_mqtt_leg_is_clean(self, bridge):
        """r3 M29: production runs an MQTT primary; stop() must not trip on a
        leg that has no stream kind."""
        mqtt = MagicMock()
        bridge._install_leg("primary", mqtt, True)
        bridge._running = True
        bridge.stop()
        mqtt.close.assert_called_once()
        assert bridge._primary_interface is None

    def test_stop_tears_down_BOTH_legs(self, bridge):
        """r3 M53."""
        p, s = MagicMock(), MagicMock()
        bridge._install_leg("primary", p, True)
        bridge._install_leg("secondary", s, True)
        bridge._running = True
        bridge.stop()
        p.close.assert_called_once()
        s.close.assert_called_once()

    def test_the_backstop_line_is_the_line_c21_parses(self, bridge, caplog):
        """r3 M04: the backstop's LOST line carries the leg's real kind, so the
        c21 reconciler names the loss."""
        import importlib.util
        import pathlib
        path = pathlib.Path(__file__).parent.parent / "scripts" / "c21_bridge_run.py"
        spec = importlib.util.spec_from_file_location("c21_bridge_run_r3", path)
        c21 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(c21)
        caplog.set_level(logging.INFO, logger="gateway.mesh_bridge")
        iface = _real_tcp_iface()                 # isConnected unset: gave up
        bridge._stream_kind["primary"] = "tcp"
        bridge._install_leg("primary", iface, True)
        with patch.object(type(iface), "close"):
            _tick_bounded(bridge, "primary")
        lost = [r.getMessage() for r in caplog.records if "link LOST" in r.getMessage()]
        assert len(lost) == 1
        e = c21.bridge_event_from_log("2026-11-10 09:00:00,000 " + lost[0])
        assert e and e["ev"] == "bridge_link_lost" and e["leg"] == "primary"

    def test_the_backstop_waits_before_reconnecting(self, bridge):
        """r3 M03: after the backstop tears a leg down, the loop waits its 1 s
        tick before the reconnect — never straight back round."""
        calls = []
        iface = _real_tcp_iface()
        bridge._install_leg("primary", iface, True)
        bridge._connect_primary = lambda: calls.append("connect")
        with patch.object(type(iface), "close"):
            _tick_bounded(bridge, "primary", calls=calls)
        assert calls == ["wait1"]

    def test_a_loop_error_marks_the_leg_down_and_backs_off(self, bridge):
        """r3 M30 + M52: the except branch marks the leg down and waits 5 s."""
        bridge._install_leg("primary", MagicMock(), True)
        with patch("gateway.mesh_bridge._library_gave_up",
                   side_effect=RuntimeError("tick boom")):
            calls = _tick_bounded(bridge, "primary")
        assert bridge._primary_connected is False
        assert calls == ["wait5"]

    def test_an_idle_healthy_leg_waits_its_tick(self, bridge):
        """r3 M55: the idle 1 s wait — dropped, the loop spins (bounded here)."""
        iface = _real_tcp_iface()
        iface.isConnected.set()
        iface.socket = MagicMock()
        bridge._install_leg("primary", iface, True)
        assert _tick_bounded(bridge, "primary") == ["wait1"]

    @pytest.mark.parametrize("leg", ["primary", "secondary"])
    def test_disconnect_marks_down_with_no_handle(self, bridge, leg):
        """r3 M32 (secondary twin of the primary pin)."""
        setattr(bridge, f"_{leg}_connected", True)   # test sets up the F-B state
        getattr(bridge, f"_disconnect_{leg}")()
        assert getattr(bridge, f"_{leg}_connected") is False


def test_B19_connect_tcp_itself_never_announces_connected(bridge):
    """Fable r3 B19: §B #21 moved `<leg>_connected` to after the install. A
    notify put back inside _connect_tcp (the original defect, beside the fix)
    survived every test — pin that the connector announces nothing."""
    bridge._notify_status = MagicMock()
    iface, ok, _ = _connect_tcp(bridge, "primary", lambda p: None, MagicMock())
    assert ok is True and iface is not None
    assert bridge._notify_status.call_count == 0
    lk = _leg_lock(bridge, "primary")
    bridge._close_taken("primary", iface)
    assert not lk.locked()


def test_B19_connect_serial_itself_never_announces_connected(bridge):
    """R4-F3 (Fable r4): B19 pinned only the tcp connector; a notify put back
    inside _connect_serial survived. The serial connector announces nothing —
    `<leg>_connected` belongs to _install_or_close (§B #21)."""
    bridge._notify_status = MagicMock()
    fake_serial = MagicMock()
    with patch("gateway.mesh_bridge._HAS_MESHTASTIC", True), \
         patch("gateway.mesh_bridge._HAS_MESHTASTIC_SERIAL", True), \
         patch("gateway.mesh_bridge._HAS_PUBSUB", True), \
         patch("gateway.mesh_bridge._meshtastic_serial", fake_serial), \
         patch("gateway.mesh_bridge._pub", MagicMock()):
        iface, ok = bridge._connect_serial(
            bridge.bridge_config.secondary, "secondary", lambda p: None)
    assert ok is True and iface is fake_serial.SerialInterface.return_value
    assert bridge._notify_status.call_count == 0


def test_B19_connect_mqtt_itself_never_announces_connected(bridge):
    """R4-F3: the same pin for the MQTT connector."""
    bridge._notify_status = MagicMock()
    fake_mqtt = MagicMock()
    fake_mqtt.return_value.connect.return_value = True
    with patch("gateway.mesh_bridge.MQTTMeshInterface", fake_mqtt):
        iface, ok = bridge._connect_mqtt(
            bridge.bridge_config.secondary, "secondary", lambda p: None)
    assert ok is True and iface is fake_mqtt.return_value
    assert bridge._notify_status.call_count == 0
