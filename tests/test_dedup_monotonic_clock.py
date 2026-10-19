"""In-memory bridge dedup must age entries on a MONOTONIC clock.

Found 2026-10-18 by the non-author review of the "Nursedude Mesh Research"
bundle: the research prototype (mt_router) dedups on ``time.monotonic``,
while MeshForge's three in-memory dedup tables aged entries on the wall
clock (``datetime.now()`` / ``time.time()``). This fleet runs RTC-less Pis
(fake-hwclock restores a stale time, NTP then steps it — honest_failure_
modes #6). When the wall clock steps BACKWARD after an entry is recorded,
the entry looks younger than ``now`` for as long as the step, so a 60 s
dedup window silently becomes hours: a legitimate repeat ("OK", "copy") is
dropped as a duplicate and the table only grows.

Five tables are covered: mesh_bridge (preset bridge + MQTT leg),
mqtt_bridge_handler, gateway_heartbeat alerts, and EAS seen-alerts. The
backward-step tests move the wall clock back while the monotonic clock
passes the window; the forward-step tests move it a day ahead while the
monotonic clock stays inside it. Measured 2026-10-18 on the wall-clock
code: 8 failed, 1 passed — the one that passes is the invariant the fix
must keep (a repeat inside the window is still a duplicate).
"""

import threading
import time as _real_time
from datetime import datetime as _real_datetime, timedelta

import pytest


class _Clock:
    """A wall clock and a monotonic clock that move independently."""

    def __init__(self):
        self.wall = 1_800_000_000.0
        self.mono = 1_000.0

    def step(self, wall_s: float, mono_s: float):
        self.wall += wall_s
        self.mono += mono_s


class _FakeTimeModule:
    """Stands in for ``time`` inside ONE module (scoped patch — never the
    shared ``time`` module object, the cascade_fingerprints trap)."""

    def __init__(self, clock: _Clock):
        self._clock = clock

    def time(self):
        return self._clock.wall

    def monotonic(self):
        return self._clock.mono

    def __getattr__(self, name):
        return getattr(_real_time, name)


def _fake_datetime(clock: _Clock):
    """HEAD's dedup read ``datetime.now()``; patching it too is what lets the
    forward-step tests FAIL on wall-clock code instead of passing by default."""
    class _FakeDatetime(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            return _real_datetime.fromtimestamp(clock.wall, tz)
    return _FakeDatetime


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def patched_mesh_bridge(clock, monkeypatch, tmp_path):
    import gateway.mesh_bridge as mb
    monkeypatch.setattr(mb, "get_real_user_home", lambda: tmp_path)
    monkeypatch.setattr(mb, "time", _FakeTimeModule(clock))
    monkeypatch.setattr(mb, "datetime", _fake_datetime(clock))
    return mb


def _run_one_cleanup_tick(bridge, monkeypatch):
    """Drive the REAL cleanup loop for one tick (not a helper it calls), so
    the test pins the sweep's clock on HEAD as well as after the fix."""
    bridge._running = True
    ticks = iter([False, True])
    monkeypatch.setattr(bridge._stop_event, "wait", lambda *_: next(ticks))
    bridge._cleanup_loop()


_ONE_DAY = timedelta(days=1).total_seconds()


class TestPresetBridgeDedup:
    def _bridge(self, mb):
        from gateway.config import GatewayConfig, MeshtasticBridgeConfig
        config = GatewayConfig()
        config.mesh_bridge = MeshtasticBridgeConfig(enabled=True)
        config.mesh_bridge.dedup_window_sec = 60
        return mb.MeshtasticPresetBridge(config=config)

    def _msg(self, mb, text="OK"):
        return mb.BridgedMeshMessage(
            source_preset="LONG_FAST", source_id="!a1b2c3d4",
            destination_id=None, content=text, channel=0, is_broadcast=True,
        )

    def test_repeat_inside_window_is_duplicate(self, patched_mesh_bridge, clock):
        mb = patched_mesh_bridge
        bridge = self._bridge(mb)
        assert bridge._is_duplicate(self._msg(mb)) is False
        clock.step(wall_s=10, mono_s=10)
        assert bridge._is_duplicate(self._msg(mb)) is True

    def test_backward_wall_step_does_not_stretch_window(
            self, patched_mesh_bridge, clock):
        mb = patched_mesh_bridge
        bridge = self._bridge(mb)
        assert bridge._is_duplicate(self._msg(mb)) is False
        clock.step(wall_s=-_ONE_DAY, mono_s=120)
        assert bridge._is_duplicate(self._msg(mb)) is False

    def test_expiry_sweep_uses_monotonic_age(
            self, patched_mesh_bridge, clock, monkeypatch):
        mb = patched_mesh_bridge
        bridge = self._bridge(mb)
        bridge._is_duplicate(self._msg(mb))
        clock.step(wall_s=-_ONE_DAY, mono_s=120)
        _run_one_cleanup_tick(bridge, monkeypatch)
        assert bridge._seen_messages == {}

    def test_forward_wall_step_does_not_shrink_window(
            self, patched_mesh_bridge, clock, monkeypatch):
        mb = patched_mesh_bridge
        bridge = self._bridge(mb)
        assert bridge._is_duplicate(self._msg(mb)) is False
        clock.step(wall_s=+_ONE_DAY, mono_s=5)
        _run_one_cleanup_tick(bridge, monkeypatch)
        assert bridge._is_duplicate(self._msg(mb)) is True


class TestMQTTMeshInterfaceDedup:
    def test_backward_wall_step_does_not_stretch_window(
            self, patched_mesh_bridge, clock):
        mb = patched_mesh_bridge
        from gateway.config import MeshtasticConfig
        iface = mb.MQTTMeshInterface(
            config=MeshtasticConfig(use_mqtt=True), name="t",
            message_callback=lambda p: None, stop_event=threading.Event(),
        )
        iface._dedup_window = 60
        assert iface._is_duplicate("id-1") is False
        clock.step(wall_s=10, mono_s=10)
        assert iface._is_duplicate("id-1") is True
        clock.step(wall_s=-_ONE_DAY, mono_s=120)
        assert iface._is_duplicate("id-1") is False


class TestMQTTBridgeHandlerDedup:
    def test_backward_wall_step_does_not_stretch_window(
            self, clock, monkeypatch):
        import gateway.mqtt_bridge_handler as mbh
        monkeypatch.setattr(mbh, "time", _FakeTimeModule(clock))
        handler = mbh.MQTTBridgeHandler.__new__(mbh.MQTTBridgeHandler)
        handler._mqtt_lock = threading.Lock()
        handler._recent_ids = {}
        handler._dedup_window = 60
        assert handler._is_duplicate("id-1") is False
        clock.step(wall_s=10, mono_s=10)
        assert handler._is_duplicate("id-1") is True
        clock.step(wall_s=-_ONE_DAY, mono_s=120)
        # An entry past the window is not a duplicate even before the
        # periodic sweep runs, and the sweep removes it.
        assert handler._is_duplicate("id-1") is False
        clock.step(wall_s=-_ONE_DAY, mono_s=120)
        handler._cleanup_dedup()
        assert handler._recent_ids == {}


class TestHeartbeatAlertDedup:
    def test_backward_wall_step_does_not_mute_alerts(self, clock, monkeypatch):
        from unittest.mock import MagicMock
        import gateway.gateway_heartbeat as ghb
        monkeypatch.setattr(ghb, "time", _FakeTimeModule(clock))
        hb = ghb.GatewayHeartbeat(config=ghb.HeartbeatConfig(
            enabled=True, mqtt_broker="localhost", mqtt_port=1883,
            gateway_id="gw-test", role="primary"))
        hb._mqtt_client = MagicMock()
        hb._mqtt_connected = True
        hb.publish_alert("critical", "radio_failover", "down", "crashed")
        clock.step(wall_s=-_ONE_DAY, mono_s=120)
        hb.publish_alert("critical", "radio_failover", "down", "again")
        assert hb._mqtt_client.publish.call_count == 2


class TestEASSeenAlerts:
    """A forward step re-broadcast already-sent EAS alerts to the mesh; a
    backward step muted new copies of an alert for the length of the step."""

    def _plugin(self, tmp_path, monkeypatch, clock):
        import plugins.eas_alerts as eas
        monkeypatch.setattr(eas, "time", _FakeTimeModule(clock))
        monkeypatch.setattr(eas, "datetime", _fake_datetime(clock))
        p = eas.EASAlertsPlugin()
        p._config = p._load_config()
        p._cache_path = lambda: tmp_path / "eas_last.json"
        return eas, p

    def _tick(self, p, eas, alerts):
        from unittest.mock import patch
        with patch.object(p, 'get_weather_alerts', return_value=alerts), \
             patch.object(p, 'get_volcano_alerts', return_value=[]), \
             patch.object(p, 'get_fema_alerts', return_value=[]):
            p._check_alerts()

    def _alert(self, eas):
        return eas.Alert(
            id="urn:test:hw", source=eas.AlertSource.NOAA,
            title="Hurricane Warning", description="body",
            severity=eas.AlertSeverity.EXTREME, event_type="Hurricane Warning",
            areas=["Hawaii County"])

    def test_forward_wall_step_does_not_resend(self, tmp_path, monkeypatch, clock):
        eas, p = self._plugin(tmp_path, monkeypatch, clock)
        self._tick(p, eas, [self._alert(eas)])
        first = dict(p._seen_alerts)
        clock.step(wall_s=+_ONE_DAY, mono_s=60)
        self._tick(p, eas, [self._alert(eas)])
        assert p._seen_alerts == first, "seen entry was re-stamped = re-sent"

    def test_backward_wall_step_expires_on_monotonic_age(
            self, tmp_path, monkeypatch, clock):
        eas, p = self._plugin(tmp_path, monkeypatch, clock)
        self._tick(p, eas, [self._alert(eas)])
        clock.step(wall_s=-_ONE_DAY, mono_s=7200)   # window 3600 s
        self._tick(p, eas, [])
        assert p._seen_alerts == {}
