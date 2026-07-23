"""
Tests for GatewayHeartbeat cross-gateway failover via MQTT.

Covers:
- Heartbeat configuration and initialization
- Gateway ID auto-generation
- State management (active, standby, promoting, demoting)
- Peer tracking (heartbeat received, missed, recovery)
- Promotion logic (secondary promotes when primary goes down)
- Demotion logic (secondary demotes when primary recovers)
- Status report generation
- Event history tracking
- LWT handling
"""

import json
import time
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from gateway.gateway_heartbeat import (
    GatewayHeartbeat,
    HeartbeatConfig,
    GatewayRole,
    GatewayState,
    PeerInfo,
    HeartbeatEvent,
)


@pytest.fixture
def primary_config():
    """Config for a primary gateway."""
    return HeartbeatConfig(
        enabled=True,
        mqtt_broker="localhost",
        mqtt_port=1883,
        heartbeat_interval=1.0,
        missed_heartbeats_threshold=3,
        gateway_id="gw-primary",
        role="primary",
    )


@pytest.fixture
def secondary_config():
    """Config for a secondary gateway."""
    return HeartbeatConfig(
        enabled=True,
        mqtt_broker="localhost",
        mqtt_port=1883,
        heartbeat_interval=1.0,
        missed_heartbeats_threshold=3,
        gateway_id="gw-secondary",
        role="secondary",
    )


class TestHeartbeatInit:
    """Tests for GatewayHeartbeat initialization."""

    def test_primary_starts_active(self, primary_config):
        """Primary gateway should start in ACTIVE state."""
        hb = GatewayHeartbeat(config=primary_config)
        assert hb.state == GatewayState.ACTIVE
        assert hb.role == GatewayRole.PRIMARY
        assert hb.is_active is True

    def test_secondary_starts_standby(self, secondary_config):
        """Secondary gateway should start in STANDBY state."""
        hb = GatewayHeartbeat(config=secondary_config)
        assert hb.state == GatewayState.STANDBY
        assert hb.role == GatewayRole.SECONDARY
        assert hb.is_active is False

    def test_disabled_starts_disabled(self):
        """Disabled config should result in DISABLED state."""
        config = HeartbeatConfig(enabled=False)
        hb = GatewayHeartbeat(config=config)
        assert hb.state == GatewayState.DISABLED

    @patch('gateway.gateway_heartbeat.platform')
    def test_auto_generates_gateway_id(self, mock_platform):
        """Should auto-generate gateway ID from hostname when not set."""
        mock_platform.node.return_value = "rpi4-mesh.local"
        config = HeartbeatConfig(enabled=True, gateway_id="")
        hb = GatewayHeartbeat(config=config)
        assert hb.gateway_id == "gw-rpi4-mesh"


class TestPeerTracking:
    """Tests for tracking peer heartbeats."""

    def test_handles_peer_heartbeat(self, secondary_config):
        """Should track peer info from heartbeat message."""
        hb = GatewayHeartbeat(config=secondary_config)

        payload = json.dumps({
            'id': 'gw-primary',
            'role': 'primary',
            'state': 'active',
            'uptime': 3600,
            'health_score': 85,
            'timestamp': time.time(),
        }).encode()

        hb._handle_peer_heartbeat('gw-primary', payload)

        assert 'gw-primary' in hb._peers
        peer = hb._peers['gw-primary']
        assert peer.role == 'primary'
        assert peer.alive is True
        assert peer.health_score == 85
        assert peer.missed_count == 0

    def test_ignores_invalid_payload(self, secondary_config):
        """Should handle invalid JSON gracefully."""
        hb = GatewayHeartbeat(config=secondary_config)
        hb._handle_peer_heartbeat('gw-bad', b'not-json')
        assert 'gw-bad' not in hb._peers

    def test_updates_existing_peer(self, secondary_config):
        """Should update existing peer info on subsequent heartbeats."""
        hb = GatewayHeartbeat(config=secondary_config)

        payload1 = json.dumps({'role': 'primary', 'state': 'active',
                               'health_score': 80}).encode()
        hb._handle_peer_heartbeat('gw-primary', payload1)

        payload2 = json.dumps({'role': 'primary', 'state': 'active',
                               'health_score': 95}).encode()
        hb._handle_peer_heartbeat('gw-primary', payload2)

        assert hb._peers['gw-primary'].health_score == 95


class TestPromotion:
    """Tests for secondary promoting to active."""

    def test_secondary_promotes_on_primary_down(self, secondary_config):
        """Secondary should promote when primary goes down."""
        hb = GatewayHeartbeat(config=secondary_config)
        assert hb.state == GatewayState.STANDBY

        # Register primary as a known peer
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary',
            role='primary',
            alive=True,
            last_heartbeat=time.time(),
        )

        # Primary goes down
        hb._peers['gw-primary'].alive = False
        hb._handle_peer_down('gw-primary')

        assert hb.state == GatewayState.ACTIVE
        assert hb.is_active is True

    def test_primary_does_not_promote(self, primary_config):
        """Primary should not change state when secondary goes down."""
        hb = GatewayHeartbeat(config=primary_config)
        assert hb.state == GatewayState.ACTIVE

        hb._peers['gw-secondary'] = PeerInfo(
            gateway_id='gw-secondary',
            role='secondary',
            alive=False,
        )
        hb._handle_peer_down('gw-secondary')

        # Primary stays active (it already is)
        assert hb.state == GatewayState.ACTIVE

    def test_promotion_records_event(self, secondary_config):
        """Promotion should be recorded in event history."""
        hb = GatewayHeartbeat(config=secondary_config)
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary', role='primary', alive=False,
        )

        hb._handle_peer_down('gw-primary')

        assert len(hb._events) >= 1
        # Find the promoted event
        promoted = [e for e in hb._events if e.event_type == 'promoted']
        assert len(promoted) == 1


class TestDemotion:
    """Tests for secondary demoting back to standby."""

    def test_secondary_demotes_on_primary_recovery(self, secondary_config):
        """Promoted secondary should demote when primary recovers."""
        hb = GatewayHeartbeat(config=secondary_config)

        # Simulate promotion
        hb._state = GatewayState.ACTIVE
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary', role='primary', alive=False,
        )

        # Primary recovers (heartbeat arrives)
        payload = json.dumps({
            'role': 'primary', 'state': 'active', 'health_score': 90
        }).encode()
        hb._handle_peer_heartbeat('gw-primary', payload)

        assert hb.state == GatewayState.STANDBY
        assert hb.is_active is False

    def test_demotion_records_event(self, secondary_config):
        """Demotion should be recorded in event history."""
        hb = GatewayHeartbeat(config=secondary_config)
        hb._state = GatewayState.ACTIVE
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary', role='primary', alive=False,
        )

        payload = json.dumps({'role': 'primary', 'state': 'active'}).encode()
        hb._handle_peer_heartbeat('gw-primary', payload)

        demoted = [e for e in hb._events if e.event_type == 'demoted']
        assert len(demoted) == 1


class TestPeerChecker:
    """Tests for missed heartbeat detection."""

    def test_detects_missed_heartbeats(self, secondary_config):
        """Should detect when peer has missed too many heartbeats."""
        hb = GatewayHeartbeat(config=secondary_config)

        # Peer with old heartbeat
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary',
            role='primary',
            alive=True,
            last_heartbeat=time.time() - 100,  # 100s ago
            last_heartbeat_mono=time.monotonic() - 100,  # liveness anchor
        )

        hb._check_peers()

        peer = hb._peers['gw-primary']
        assert peer.alive is False

    def test_does_not_flag_recent_heartbeat(self, secondary_config):
        """Should not flag peer with recent heartbeat."""
        hb = GatewayHeartbeat(config=secondary_config)

        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary',
            role='primary',
            alive=True,
            last_heartbeat=time.time(),  # Just now
            last_heartbeat_mono=time.monotonic(),
        )

        hb._check_peers()

        assert hb._peers['gw-primary'].alive is True


class TestLWTHandling:
    """Tests for MQTT Last Will and Testament handling."""

    def test_lwt_offline_marks_peer_down(self, secondary_config):
        """Receiving LWT offline should mark peer as down."""
        hb = GatewayHeartbeat(config=secondary_config)
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary', role='primary', alive=True,
        )

        hb._handle_peer_status('gw-primary', b'offline')

        assert hb._peers['gw-primary'].alive is False


class TestStatusReport:
    """Tests for get_status() output."""

    def test_status_includes_all_sections(self, primary_config):
        """Status should include all expected sections."""
        hb = GatewayHeartbeat(config=primary_config)
        status = hb.get_status()

        assert 'enabled' in status
        assert 'gateway_id' in status
        assert 'role' in status
        assert 'state' in status
        assert 'mqtt_connected' in status
        assert 'peers' in status
        assert 'events' in status

    def test_status_shows_peer_info(self, secondary_config):
        """Status should include peer information."""
        hb = GatewayHeartbeat(config=secondary_config)
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary',
            role='primary',
            state='active',
            alive=True,
            health_score=90,
            uptime=3600,
            last_heartbeat=time.time(),
        )

        status = hb.get_status()
        assert 'gw-primary' in status['peers']
        assert status['peers']['gw-primary']['alive'] is True
        assert status['peers']['gw-primary']['health_score'] == 90


class TestMQTTMessageHandling:
    """Tests for MQTT message routing."""

    def test_ignores_own_messages(self, primary_config):
        """Should ignore heartbeats from self."""
        hb = GatewayHeartbeat(config=primary_config)

        msg = MagicMock()
        msg.topic = f"meshforge/gateway/{primary_config.gateway_id}/heartbeat"
        msg.payload = json.dumps({'role': 'primary'}).encode()

        hb._on_mqtt_message(None, None, msg)

        # Should not track self as peer
        assert primary_config.gateway_id not in hb._peers

    def test_routes_heartbeat_messages(self, secondary_config):
        """Should route heartbeat messages to _handle_peer_heartbeat."""
        hb = GatewayHeartbeat(config=secondary_config)

        msg = MagicMock()
        msg.topic = "meshforge/gateway/gw-primary/heartbeat"
        msg.payload = json.dumps({
            'role': 'primary', 'state': 'active', 'health_score': 85,
        }).encode()

        hb._on_mqtt_message(None, None, msg)

        assert 'gw-primary' in hb._peers

    def test_routes_status_messages(self, secondary_config):
        """Should route status (LWT) messages to _handle_peer_status."""
        hb = GatewayHeartbeat(config=secondary_config)
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary', role='primary', alive=True,
        )

        msg = MagicMock()
        msg.topic = "meshforge/gateway/gw-primary/status"
        msg.payload = b"offline"

        hb._on_mqtt_message(None, None, msg)

        assert hb._peers['gw-primary'].alive is False


class TestReconnectGracePeriod:
    """Tests for grace period after MQTT reconnect."""

    def test_grace_period_after_reconnect(self, secondary_config):
        """Peers should not be marked down during grace window after reconnect."""
        hb = GatewayHeartbeat(config=secondary_config)

        # Simulate MQTT reconnect just happened
        hb._last_mqtt_connect = time.monotonic()

        # Peer with old heartbeat (would normally be declared down)
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary',
            role='primary',
            alive=True,
            last_heartbeat=time.time() - 100,
            last_heartbeat_mono=time.monotonic() - 100,
        )

        hb._check_peers()

        # Should still be alive — grace period active
        assert hb._peers['gw-primary'].alive is True

    def test_no_grace_period_when_connected_long_ago(self, secondary_config):
        """After grace period expires, normal peer checking resumes."""
        hb = GatewayHeartbeat(config=secondary_config)

        # MQTT connected long ago
        hb._last_mqtt_connect = time.monotonic() - 100

        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary',
            role='primary',
            alive=True,
            last_heartbeat=time.time() - 100,
            last_heartbeat_mono=time.monotonic() - 100,
        )

        hb._check_peers()

        # Should be declared down — grace period expired
        assert hb._peers['gw-primary'].alive is False


class TestDuplicatePeerDownGuard:
    """Tests for preventing duplicate peer-down thread spawns."""

    def test_no_duplicate_peer_down_threads(self, secondary_config):
        """Should not spawn another thread if peer-down already pending."""
        hb = GatewayHeartbeat(config=secondary_config)
        hb._last_mqtt_connect = 0  # No grace period

        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary',
            role='primary',
            alive=True,
            last_heartbeat=time.time() - 100,
            last_heartbeat_mono=time.monotonic() - 100,
        )

        # Simulate a peer-down already pending
        hb._pending_peer_down.add('gw-primary')

        hb._check_peers()

        # Peer marked dead but no new thread spawned (stays in pending set)
        assert hb._peers['gw-primary'].alive is False
        assert 'gw-primary' in hb._pending_peer_down


class TestFailoverStateInPayload:
    """Tests for including failover_state in heartbeat payload."""

    def test_heartbeat_includes_failover_state(self, primary_config):
        """Heartbeat payload should include failover_state when manager available."""
        mock_fm = MagicMock()
        mock_fm.state.value = "secondary_active"

        hb = GatewayHeartbeat(config=primary_config, failover_manager=mock_fm)
        hb._mqtt_client = MagicMock()
        hb._mqtt_connected = True

        hb._publish_heartbeat()

        # Check the published payload
        call_args = hb._mqtt_client.publish.call_args
        payload = json.loads(call_args[0][1])
        assert payload['failover_state'] == 'secondary_active'

    def test_heartbeat_without_failover_manager(self, primary_config):
        """Heartbeat should work fine without failover_manager."""
        hb = GatewayHeartbeat(config=primary_config)
        hb._mqtt_client = MagicMock()
        hb._mqtt_connected = True

        hb._publish_heartbeat()

        call_args = hb._mqtt_client.publish.call_args
        payload = json.loads(call_args[0][1])
        assert 'failover_state' not in payload


class TestClockStepDoesNotSplitBrain:
    """Regression pins for the 2026-07-23 gateway review, Pri-1.

    Peer-liveness aging is measured on time.monotonic(), so a wall-clock/NTP
    step (routine on the RTC-less Pi fleet) cannot forge a peer-down and
    promote a SECONDARY to ACTIVE while the PRIMARY is still up (split-brain),
    nor forge liveness on a genuinely dead peer (missed failover).
    """

    def test_wall_clock_forward_step_does_not_forge_peer_down(self, secondary_config):
        hb = GatewayHeartbeat(config=secondary_config)
        hb._last_mqtt_connect = time.monotonic() - 1000  # grace long expired
        # NTP forward step: wall-clock stamp looks ancient, but the peer was
        # actually heard 1s ago on the monotonic clock.
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary', role='primary', alive=True,
            last_heartbeat=time.time() - 100000,
            last_heartbeat_mono=time.monotonic() - 1,
        )
        hb._check_peers()
        assert hb._peers['gw-primary'].alive is True

    def test_wall_clock_forward_step_does_not_promote_to_active(self, secondary_config):
        # The split-brain outcome specifically.
        hb = GatewayHeartbeat(config=secondary_config)
        assert hb.state == GatewayState.STANDBY
        hb._last_mqtt_connect = time.monotonic() - 1000
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary', role='primary', alive=True,
            last_heartbeat=time.time() - 100000,        # NTP forward step
            last_heartbeat_mono=time.monotonic() - 1,   # actually just heard
        )
        hb._check_peers()
        assert hb._peers['gw-primary'].alive is True
        assert hb.state == GatewayState.STANDBY  # no split-brain promotion

    def test_monotonic_age_still_declares_real_down(self, secondary_config):
        # A genuinely-stale peer (old monotonic) is still declared down even if
        # its wall-clock stamp looks fresh (NTP backward step) — failover works.
        hb = GatewayHeartbeat(config=secondary_config)
        hb._last_mqtt_connect = time.monotonic() - 1000
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary', role='primary', alive=True,
            last_heartbeat=time.time(),                  # looks fresh (backward step)
            last_heartbeat_mono=time.monotonic() - 100,  # really stale
        )
        hb._check_peers()
        assert hb._peers['gw-primary'].alive is False

    def test_negative_relative_mono_is_aged_not_skipped(self, secondary_config):
        # Regression (CI 3.11 flake, 2026-07-23): on a fresh-boot host
        # time.monotonic() can be < the age offset, so a real stamp computed as
        # (monotonic() - offset) is NEGATIVE. The liveness guard must skip ONLY
        # the unset 0.0 sentinel, not a negative stamp (which is a legitimately
        # old peer). A `<= 0` guard skipped it → the peer never aged → no
        # failover. `elapsed` (now_mono - (-50)) is huge, so it must age down.
        hb = GatewayHeartbeat(config=secondary_config)
        hb._last_mqtt_connect = 0  # no grace
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary', role='primary', alive=True,
            last_heartbeat=time.time(), last_heartbeat_mono=-50.0,
        )
        hb._check_peers()
        assert hb._peers['gw-primary'].alive is False

    def test_unset_mono_sentinel_is_skipped(self, secondary_config):
        # The 0.0 default (alive peer with no monotonic stamp — an inconsistent
        # state that shouldn't occur) must NOT forge a down from now_mono - 0.
        hb = GatewayHeartbeat(config=secondary_config)
        hb._last_mqtt_connect = 0
        hb._peers['gw-primary'] = PeerInfo(
            gateway_id='gw-primary', role='primary', alive=True,
            last_heartbeat=time.time(), last_heartbeat_mono=0.0,
        )
        hb._check_peers()
        assert hb._peers['gw-primary'].alive is True
