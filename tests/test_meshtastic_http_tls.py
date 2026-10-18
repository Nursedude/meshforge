"""MeshAnchor #151 item 6 (KN4YDL, 2026-10-10): the gateway's HTTP toradio
TX always used TLS, so a WiFi Meshtastic node serving plain HTTP on :80 was
called as https://host:80/... — failed at once, fell back to the CLI, and no
config field could change it. ``meshtastic.http_tls``: explicit bool wins;
unset (None) means plain HTTP on port 80 and TLS on every other port, so no
setup that worked before changes (TLS on :80 cannot have worked)."""
import threading
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, MeshtasticConfig


@pytest.mark.parametrize("http_tls,port,want", [
    (None, 80, False), (None, 9443, True), (None, 443, True),
    (True, 80, True), (False, 9443, False),
])
def test_tls_for_port(http_tls, port, want):
    assert MeshtasticConfig(http_tls=http_tls).tls_for_port(port) is want


def test_default_is_auto():
    assert MeshtasticConfig().http_tls is None


def test_round_trips_through_gateway_json(tmp_path):
    cfg = GatewayConfig()
    cfg.meshtastic.http_tls = False
    with patch.object(GatewayConfig, "get_config_path",
                      return_value=tmp_path / "gateway.json"):
        assert cfg.save()
        assert GatewayConfig.load().meshtastic.http_tls is False


def _handler(port):
    from gateway.mqtt_bridge_handler import MQTTBridgeHandler
    config = GatewayConfig()
    config.meshtastic.host = "radio.local"
    config.meshtastic.http_port = port
    return MQTTBridgeHandler(
        config=config, node_tracker=MagicMock(), health=MagicMock(),
        stop_event=threading.Event(), stats={},
        stats_lock=threading.Lock(), message_queue=MagicMock())


@pytest.mark.parametrize("port,want", [(80, False), (9443, True)])
def test_gateway_tx_passes_tls_for_its_port(port, want):
    h = _handler(port)
    with patch("gateway.meshtastic_protobuf_client.send_text_direct_with_id",
               return_value=1234) as send:
        h._send_via_http_protobuf("hi", None, 0)
    assert send.call_args.kwargs["tls"] is want
    assert send.call_args.kwargs["port"] == port


@pytest.mark.parametrize("port,want", [(80, False), (9443, True)])
def test_preset_bridge_leg_tx_passes_tls(port, want):
    """Review F10: the mesh_bridge leg's stateless TX took the same port
    without a tls argument, so a :80 leg still went https."""
    from gateway.mesh_bridge import MQTTMeshInterface
    leg = MQTTMeshInterface(MeshtasticConfig(host="leg.local", http_port=port),
                            "leg", MagicMock(), threading.Event())
    with patch("gateway.meshtastic_protobuf_client.send_text_direct",
               return_value=True) as send:
        leg.sendText("hi")
    assert send.call_args.kwargs["tls"] is want
