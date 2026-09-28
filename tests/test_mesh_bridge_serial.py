"""Tests for mesh_bridge.py serial connection_type support (option B)."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _bridge_queues_in_tmp(tmp_path, monkeypatch):
    """MeshtasticPresetBridge.__init__ creates its persistent queues under
    get_real_user_home() — the operator's LIVE
    ~/.config/meshforge/mesh_bridge_queues/{p2s,s2p}.db, the store a gateway
    on this box drains to RF. A real-home audit of the full suite
    (2026-09-23) measured these tests mkdir-ing that dir and opening both
    DBs on every run (they held 0 messages — nothing was enqueued — but on
    a fresh box the run would CREATE them)."""
    import gateway.mesh_bridge as mb
    monkeypatch.setattr(mb, "get_real_user_home", lambda: tmp_path)


@pytest.fixture
def bridge_with_serial_secondary():
    """GatewayConfig with TCP primary + serial secondary (Heltec USB)."""
    from gateway.config import (
        GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig,
    )

    config = GatewayConfig()
    config.mesh_bridge = MeshtasticBridgeConfig(
        enabled=True,
        primary=MeshtasticConfig(
            host="localhost",
            port=4403,
            preset="LONG_FAST",
            name="longfast",
        ),
        secondary=MeshtasticConfig(
            preset="SHORT_TURBO",
            name="shortturbo-heltec",
            connection_type="serial",
            serial_device="/dev/ttyUSB0",
        ),
    )
    return config


class TestConnectionTypeDispatch:
    """_connect_interface must route by connection_type field."""

    def test_serial_dispatches_to_connect_serial(self, bridge_with_serial_secondary):
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
        with patch.object(bridge, "_connect_serial", return_value=(MagicMock(), True)) as serial, \
             patch.object(bridge, "_connect_tcp") as tcp, \
             patch.object(bridge, "_connect_mqtt") as mqtt:
            result = bridge._connect_interface(
                bridge_with_serial_secondary.mesh_bridge.secondary,
                "shortturbo-heltec",
                lambda pkt: None,
            )
        assert serial.called
        assert not tcp.called
        assert not mqtt.called
        assert result[1] is True

    def test_tcp_default_when_no_connection_type(self, bridge_with_serial_secondary):
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
        with patch.object(bridge, "_connect_tcp", return_value=(MagicMock(), True)) as tcp, \
             patch.object(bridge, "_connect_serial") as serial, \
             patch.object(bridge, "_connect_mqtt") as mqtt:
            bridge._connect_interface(
                bridge_with_serial_secondary.mesh_bridge.primary,
                "longfast",
                lambda pkt: None,
            )
        assert tcp.called
        assert not serial.called
        assert not mqtt.called

    def test_connection_type_mqtt_dispatches_to_mqtt(self, bridge_with_serial_secondary):
        from gateway.mesh_bridge import MeshtasticPresetBridge

        cfg = bridge_with_serial_secondary.mesh_bridge.primary
        cfg.connection_type = "mqtt"
        bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
        with patch.object(bridge, "_connect_mqtt", return_value=(MagicMock(), True)) as mqtt, \
             patch.object(bridge, "_connect_serial") as serial, \
             patch.object(bridge, "_connect_tcp") as tcp:
            bridge._connect_interface(cfg, "longfast", lambda pkt: None)
        assert mqtt.called
        assert not serial.called
        assert not tcp.called

    def test_legacy_use_mqtt_still_routes_to_mqtt(self, bridge_with_serial_secondary):
        """Back-compat: existing configs with use_mqtt=True still work."""
        from gateway.mesh_bridge import MeshtasticPresetBridge

        cfg = bridge_with_serial_secondary.mesh_bridge.primary
        cfg.connection_type = ""
        cfg.use_mqtt = True
        bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
        with patch.object(bridge, "_connect_mqtt", return_value=(MagicMock(), True)) as mqtt, \
             patch.object(bridge, "_connect_tcp") as tcp:
            bridge._connect_interface(cfg, "longfast", lambda pkt: None)
        assert mqtt.called
        assert not tcp.called


class TestConnectSerial:
    """_connect_serial must construct SerialInterface with correct devPath."""

    def test_explicit_device_path_passed_to_serial_interface(
        self, bridge_with_serial_secondary
    ):
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
        fake_iface = MagicMock()
        fake_serial_mod = MagicMock()
        fake_serial_mod.SerialInterface = MagicMock(return_value=fake_iface)

        with patch("gateway.mesh_bridge._HAS_MESHTASTIC", True), \
             patch("gateway.mesh_bridge._HAS_MESHTASTIC_SERIAL", True), \
             patch("gateway.mesh_bridge._HAS_PUBSUB", True), \
             patch("gateway.mesh_bridge._meshtastic_serial", fake_serial_mod), \
             patch("gateway.mesh_bridge._pub") as fake_pub:
            iface, ok = bridge._connect_serial(
                bridge_with_serial_secondary.mesh_bridge.secondary,
                "shortturbo-heltec",
                lambda pkt: None,
            )

        assert ok is True
        assert iface is fake_iface
        fake_serial_mod.SerialInterface.assert_called_once_with(devPath="/dev/ttyUSB0")
        assert fake_pub.subscribe.called

    def test_empty_device_path_triggers_auto_detect(self, bridge_with_serial_secondary):
        from gateway.mesh_bridge import MeshtasticPresetBridge

        cfg = bridge_with_serial_secondary.mesh_bridge.secondary
        cfg.serial_device = ""
        bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
        fake_iface = MagicMock()
        fake_serial_mod = MagicMock()
        fake_serial_mod.SerialInterface = MagicMock(return_value=fake_iface)

        with patch("gateway.mesh_bridge._HAS_MESHTASTIC", True), \
             patch("gateway.mesh_bridge._HAS_MESHTASTIC_SERIAL", True), \
             patch("gateway.mesh_bridge._HAS_PUBSUB", True), \
             patch("gateway.mesh_bridge._meshtastic_serial", fake_serial_mod), \
             patch("gateway.mesh_bridge._pub"):
            bridge._connect_serial(cfg, "shortturbo-heltec", lambda pkt: None)

        fake_serial_mod.SerialInterface.assert_called_once_with()

    def test_missing_library_returns_failure(self, bridge_with_serial_secondary):
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
        with patch("gateway.mesh_bridge._HAS_MESHTASTIC_SERIAL", False):
            iface, ok = bridge._connect_serial(
                bridge_with_serial_secondary.mesh_bridge.secondary,
                "shortturbo-heltec",
                lambda pkt: None,
            )
        assert iface is None
        assert ok is False

    def test_serial_interface_construction_error_is_caught(
        self, bridge_with_serial_secondary
    ):
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
        fake_serial_mod = MagicMock()
        fake_serial_mod.SerialInterface = MagicMock(
            side_effect=OSError("device busy")
        )

        with patch("gateway.mesh_bridge._HAS_MESHTASTIC", True), \
             patch("gateway.mesh_bridge._HAS_MESHTASTIC_SERIAL", True), \
             patch("gateway.mesh_bridge._HAS_PUBSUB", True), \
             patch("gateway.mesh_bridge._meshtastic_serial", fake_serial_mod), \
             patch("gateway.mesh_bridge._pub"):
            iface, ok = bridge._connect_serial(
                bridge_with_serial_secondary.mesh_bridge.secondary,
                "shortturbo-heltec",
                lambda pkt: None,
            )
        assert iface is None
        assert ok is False


class TestBridgeCliPreflight:
    """bridge_cli auto-correct must skip TCP check when secondary is serial."""

    def _cfg(self, connection_type: str, serial_device: str = ""):
        from gateway.config import (
            GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig,
        )
        cfg = GatewayConfig()
        cfg.bridge_mode = "mesh_bridge"
        cfg.mesh_bridge = MeshtasticBridgeConfig(
            enabled=True,
            primary=MeshtasticConfig(host="localhost", port=4403, preset="LONG_FAST"),
            secondary=MeshtasticConfig(
                preset="SHORT_TURBO",
                connection_type=connection_type,
                serial_device=serial_device,
            ),
        )
        return cfg

    def test_serial_secondary_present_passes_preflight(self, tmp_path):
        fake_dev = tmp_path / "ttyUSB0"
        fake_dev.write_text("")
        cfg = self._cfg("serial", str(fake_dev))

        from gateway.bridge_cli import preflight_checks

        with patch("gateway.bridge_cli.check_service") as chk, \
             patch("gateway.bridge_cli.check_port") as port:
            chk.return_value = MagicMock(available=True, message="ok", fix_hint="")
            port.return_value = True  # primary :4403 reachable
            preflight_checks(cfg)
        # Only primary (:4403) checked via check_port — serial secondary must not.
        ports_checked = [call.args[0] for call in port.call_args_list]
        assert 4404 not in ports_checked, "TCP port check must not run for serial secondary"

    def test_serial_secondary_missing_fails_preflight(self):
        cfg = self._cfg("serial", "/dev/does-not-exist-xyz")

        from gateway.bridge_cli import preflight_checks

        with patch("gateway.bridge_cli.check_service") as chk, \
             patch("gateway.bridge_cli.check_port") as port:
            chk.return_value = MagicMock(available=True, message="ok", fix_hint="")
            port.return_value = True  # primary :4403 reachable
            ok = preflight_checks(cfg)
        assert ok is False
        ports_checked = [call.args[0] for call in port.call_args_list]
        assert 4404 not in ports_checked


class TestMeshtasticConfigSerialFields:
    """New config fields default sensibly and round-trip through asdict()."""

    def test_defaults_preserve_tcp_behavior(self):
        from dataclasses import asdict

        from gateway.config import MeshtasticConfig

        c = MeshtasticConfig()
        assert c.connection_type == ""
        assert c.serial_device == ""
        d = asdict(c)
        assert "connection_type" in d
        assert "serial_device" in d

    def test_serial_config_round_trips(self):
        from dataclasses import asdict

        from gateway.config import MeshtasticConfig

        c = MeshtasticConfig(
            connection_type="serial",
            serial_device="/dev/ttyUSB0",
            preset="SHORT_TURBO",
            name="shortturbo-heltec",
        )
        d = asdict(c)
        c2 = MeshtasticConfig(**d)
        assert c2.connection_type == "serial"
        assert c2.serial_device == "/dev/ttyUSB0"


class TestSerialRxListenerLifetime:
    """pypubsub holds listeners WEAKLY: a nested on_receive closure with no
    retained reference is garbage-collected right after _connect_serial
    returns, and serial RX silently dies. Pin gc-survival with the REAL
    pubsub library (live failure shape: moc 2026-06-03 — ST->LF text never
    reached _process_receive, no error anywhere)."""

    def _connect(self, bridge, config, received):
        import gc
        from pubsub import pub as real_pub

        fake_iface = MagicMock()
        fake_serial_mod = MagicMock()
        fake_serial_mod.SerialInterface = MagicMock(return_value=fake_iface)

        with patch("gateway.mesh_bridge._HAS_MESHTASTIC", True), \
             patch("gateway.mesh_bridge._HAS_MESHTASTIC_SERIAL", True), \
             patch("gateway.mesh_bridge._HAS_PUBSUB", True), \
             patch("gateway.mesh_bridge._meshtastic_serial", fake_serial_mod), \
             patch("gateway.mesh_bridge._pub", real_pub):
            iface, ok = bridge._connect_serial(
                config, "shortturbo-heltec", received.append,
            )
        assert ok is True
        gc.collect()  # the weakref killer
        return iface, real_pub

    def test_listener_survives_gc_and_delivers(self, bridge_with_serial_secondary):
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
        received = []
        iface, real_pub = self._connect(
            bridge, bridge_with_serial_secondary.mesh_bridge.secondary, received)
        try:
            real_pub.sendMessage(
                "meshtastic.receive",
                packet={'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': 'hi'}},
                interface=iface,
            )
            assert len(received) == 1
        finally:
            real_pub.unsubAll("meshtastic.receive")

    def test_listener_filters_foreign_interfaces(self, bridge_with_serial_secondary):
        """Global pubsub topic: packets from another meshtastic interface in
        the same process must not leak into this bridge leg."""
        from gateway.mesh_bridge import MeshtasticPresetBridge

        bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
        received = []
        _, real_pub = self._connect(
            bridge, bridge_with_serial_secondary.mesh_bridge.secondary, received)
        try:
            real_pub.sendMessage(
                "meshtastic.receive",
                packet={'decoded': {'portnum': 'TEXT_MESSAGE_APP', 'payload': 'hi'}},
                interface=MagicMock(),  # not our interface
            )
            assert received == []
        finally:
            real_pub.unsubAll("meshtastic.receive")


class TestSerialLinkLost:
    """moc 2026-09-27: the secondary's USB radio was unplugged at 08:36; the
    leg stayed "connected" on a dead handle, never reconnected, and the first
    sign was "Failed to forward to secondary" 5.7 h later. The leg must now
    hear meshtastic.connection.lost (REAL pubsub) and drop to disconnected."""

    def _connected(self, bridge, config):
        from pubsub import pub as real_pub
        fake_iface = MagicMock()
        fake_serial_mod = MagicMock()
        fake_serial_mod.SerialInterface = MagicMock(return_value=fake_iface)
        with patch("gateway.mesh_bridge._HAS_MESHTASTIC", True), \
             patch("gateway.mesh_bridge._HAS_MESHTASTIC_SERIAL", True), \
             patch("gateway.mesh_bridge._HAS_PUBSUB", True), \
             patch("gateway.mesh_bridge._meshtastic_serial", fake_serial_mod), \
             patch("gateway.mesh_bridge._pub", real_pub):
            iface, ok = bridge._connect_serial(config, "secondary", lambda p: None)
        assert ok
        bridge._secondary_interface, bridge._secondary_connected = iface, True
        import gc
        gc.collect()  # the lost-listener must survive like the rx one
        return iface, real_pub

    def test_link_lost_marks_leg_disconnected_with_witness(
            self, bridge_with_serial_secondary):
        from gateway.mesh_bridge import MeshtasticPresetBridge
        bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
        iface, real_pub = self._connected(
            bridge, bridge_with_serial_secondary.mesh_bridge.secondary)
        statuses = []
        bridge._notify_status = statuses.append
        try:
            real_pub.sendMessage("meshtastic.connection.lost", interface=iface)
        finally:
            real_pub.unsubAll("meshtastic.connection.lost")
            real_pub.unsubAll("meshtastic.receive")
        assert bridge._secondary_connected is False
        assert bridge._secondary_interface is None
        assert bridge.stats['link_lost'] == 1
        assert "secondary_disconnected" in statuses
        iface.close.assert_called_once()

    def test_foreign_or_replaced_interface_changes_nothing(
            self, bridge_with_serial_secondary):
        from gateway.mesh_bridge import MeshtasticPresetBridge
        bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
        iface, real_pub = self._connected(
            bridge, bridge_with_serial_secondary.mesh_bridge.secondary)
        try:
            real_pub.sendMessage("meshtastic.connection.lost", interface=MagicMock())
            assert bridge._secondary_connected is True
            # a late event for the OLD handle after a reconnect replaced it
            bridge._secondary_interface = MagicMock()
            real_pub.sendMessage("meshtastic.connection.lost", interface=iface)
            assert bridge._secondary_connected is True
            assert bridge.stats['link_lost'] == 0
        finally:
            real_pub.unsubAll("meshtastic.connection.lost")
            real_pub.unsubAll("meshtastic.receive")

    def test_failed_reconnect_backs_off_ten_seconds(
            self, bridge_with_serial_secondary):
        from gateway.mesh_bridge import MeshtasticPresetBridge
        bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
        bridge._running = True
        waits = []
        bridge._stop_event = MagicMock()
        bridge._stop_event.wait = lambda t: (waits.append(t), True)[1]
        bridge._connect_secondary = lambda: None  # radio absent: stays down
        bridge._secondary_loop()
        assert waits == [10]


def test_status_reports_serial_leg_as_serial(bridge_with_serial_secondary):
    """get_status() labelled the moc USB leg 'tcp' — a legibility lie."""
    from gateway.mesh_bridge import MeshtasticPresetBridge
    st = MeshtasticPresetBridge(config=bridge_with_serial_secondary).get_status()
    assert st['secondary']['mode'] == 'serial'
    assert st['primary']['mode'] == 'tcp'


def test_planned_stop_close_is_not_a_lost_radio(bridge_with_serial_secondary):
    """moc 2026-09-28: every gateway restart logged "serial link LOST — radio
    gone" and counted a lost link, because stop() closing the port fires
    connection.lost too. A close under stop() is planned, not a lost radio."""
    from gateway.mesh_bridge import MeshtasticPresetBridge
    bridge = MeshtasticPresetBridge(config=bridge_with_serial_secondary)
    iface, real_pub = TestSerialLinkLost()._connected(
        bridge, bridge_with_serial_secondary.mesh_bridge.secondary)
    statuses = []
    bridge._notify_status = statuses.append
    bridge._stop_event.set()
    try:
        real_pub.sendMessage("meshtastic.connection.lost", interface=iface)
    finally:
        real_pub.unsubAll("meshtastic.connection.lost")
        real_pub.unsubAll("meshtastic.receive")
    assert bridge.stats['link_lost'] == 0
    assert "secondary_disconnected" not in statuses


def test_start_banner_names_the_real_endpoint(bridge_with_serial_secondary):
    """moc 2026-09-28: the start banner printed the serial RAK leg as
    'localhost:4404 (TCP)'. It must say where the leg really connects."""
    from gateway.config import MeshtasticConfig
    from gateway.mesh_bridge import MeshtasticPresetBridge as B
    mb = bridge_with_serial_secondary.mesh_bridge
    assert B._leg_endpoint(mb.secondary) == "/dev/ttyUSB0 (serial)"
    assert B._leg_endpoint(mb.primary) == "localhost:4403 (TCP)"
    mq = MeshtasticConfig(connection_type="mqtt", mqtt_broker="b", mqtt_port=1)
    assert B._leg_endpoint(mq) == "b:1 (MQTT)"
    assert B._leg_endpoint(MeshtasticConfig(connection_type="serial")) == \
        "auto-detect (serial)"
