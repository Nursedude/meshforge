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


# --- announce redelivery (2026-09-26: every announce stored twice) ---

import inspect  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def sniffer():
    # _on_rns_announce imports RNS inside the call; CI installs no RNS, so
    # without a stub the handler returned before storing and these tests
    # passed only on boxes that have RNS (CI red on 5c61ba30, 2026-09-26).
    import types
    fake = types.ModuleType("RNS")
    fake.Transport = types.SimpleNamespace(has_path=lambda h: False, hops_to=lambda h: 0)
    with patch.dict("sys.modules", {"RNS": fake}):
        yield from _sniffer()


def _sniffer():
    s = rs.RNSSniffer()
    s._running = True
    stored = []
    s._store_packet = stored.append
    s._update_path_table = lambda *a, **k: None
    s.stored = stored
    yield s


def test_same_packet_twice_is_stored_once(sniffer):
    h = b"\x11" * 32
    sniffer._on_rns_announce(DST, None, b"", h)
    sniffer._on_rns_announce(DST, None, b"", h)
    assert len(sniffer.stored) == 1
    assert sniffer.get_stats()["announce_redeliveries_dropped"] == 1


def test_a_rebroadcast_is_a_different_packet_and_is_kept(sniffer):
    sniffer._on_rns_announce(DST, None, b"", b"\x11" * 32)
    sniffer._on_rns_announce(DST, None, b"", b"\x22" * 32)
    assert len(sniffer.stored) == 2
    assert sniffer.get_stats()["announce_redeliveries_dropped"] == 0


def test_no_hash_is_never_dropped(sniffer):
    sniffer._on_rns_announce(DST, None, b"")
    sniffer._on_rns_announce(DST, None, b"")
    assert len(sniffer.stored) == 2


def test_window_expiry_lets_a_hash_count_again(sniffer):
    h = b"\x33" * 32
    with patch.object(rs.time, "monotonic", side_effect=[0.0, 1000.0]):
        sniffer._on_rns_announce(DST, None, b"", h)
        sniffer._on_rns_announce(DST, None, b"", h)
    assert len(sniffer.stored) == 2


def test_handler_takes_the_packet_hash():
    """RNS picks the call form by arity; 4 params = it passes the hash."""
    src = inspect.getsource(rs.RNSSniffer._install_rns_hooks)
    assert "announce_packet_hash):" in src
