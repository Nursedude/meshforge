"""RNS packets reach the traffic capture with their real sender + direction.

2026-09-26 on a gateway box: 5,027 of 5,027 captured RNS packets read source
"local", direction inbound — integrate_with_traffic_inspector() built the
metadata without source_hash / direction, and the RNS dissector filled the gap
with "local", a claim that THIS node sent it.
"""
from unittest.mock import patch

from monitoring import rns_sniffer as rs
from monitoring.packet_dissectors import RNSDissector
from monitoring.traffic_models import PacketDirection

SRC = bytes.fromhex("4b21083c21f84e0a" * 2)
DST = bytes.fromhex("2b86f1b1f4f475aa" * 2)


def test_unknown_source_is_empty_not_local():
    packet = RNSDissector().dissect(None, {"protocol": "rns"})
    assert packet.source == ""


def _captured_metadata(**info):
    seen = []

    class _Insp:
        def capture(self, data, metadata):
            seen.append(metadata)

    class _Sniffer:
        def register_callback(self, cb):
            self.cb = cb

    sniffer = _Sniffer()
    with patch.object(rs, "get_traffic_inspector", lambda: _Insp()), \
         patch.object(rs, "get_rns_sniffer", lambda: sniffer):
        rs.integrate_with_traffic_inspector()
        sniffer.cb(rs.RNSPacketInfo(**info))
    return seen[0]


def test_sender_and_direction_reach_the_capture():
    md = _captured_metadata(packet_type=rs.RNSPacketType.ANNOUNCE, destination_hash=DST,
                            source_hash=SRC, direction="outbound")
    assert md["source_hash"] == SRC.hex()
    assert md["direction"] == "outbound"
    packet = RNSDissector().dissect(None, md)
    assert packet.source == SRC.hex()
    assert packet.direction == PacketDirection.OUTBOUND


def test_packet_without_a_sender_stays_unknown():
    md = _captured_metadata(packet_type=rs.RNSPacketType.DATA, destination_hash=DST,
                            direction="inbound")
    assert RNSDissector().dissect(None, md).source == ""
