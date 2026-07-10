"""Tests for traffic_capture.db hardening — WAL pragmas + time-based retention.

Phase 1 closure: TrafficCapture and PacketArchive both opened bare
sqlite3.connect; only TrafficCapture had a row-count cap, no time-based
floor. A quiet mesh would hoard month-old packets indefinitely."""

import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from monitoring.traffic_storage import TrafficCapture


@pytest.fixture
def capture(tmp_path: Path) -> TrafficCapture:
    db_path = tmp_path / "traffic_capture.db"
    return TrafficCapture(db_path=str(db_path), max_packets=100, retention_hours=24)


class TestTrafficCapturePragmas:
    def test_get_connection_returns_wal(self, capture):
        with capture._get_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"

    def test_get_connection_synchronous_normal(self, capture):
        with capture._get_connection() as conn:
            sync = conn.execute("PRAGMA synchronous").fetchone()[0]
            assert sync == 1

    def test_get_connection_journal_size_capped(self, capture):
        with capture._get_connection() as conn:
            limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
            assert limit == 67_108_864


class TestTrafficCaptureRetention:
    def _insert(self, capture, pid: str, ts_iso: str):
        with capture._get_connection() as conn:
            conn.execute(
                "INSERT INTO packets (id, timestamp, direction, protocol, source, destination, channel, hop_limit, hop_start, hops_taken, portnum, port_name, snr, rssi, size, data) "
                "VALUES (?, ?, 'rx', 'meshtastic', '!a', '!b', 0, 3, 3, 0, 1, 'TEXT', 0.0, -100, 100, '{}')",
                (pid, ts_iso),
            )

    def test_time_based_retention_deletes_old_packets(self, capture):
        # 48h ago: outside 24h retention
        old_ts = (datetime.now() - timedelta(hours=48)).isoformat()
        # 1h ago: well within retention
        fresh_ts = (datetime.now() - timedelta(hours=1)).isoformat()
        self._insert(capture, "old1", old_ts)
        self._insert(capture, "fresh1", fresh_ts)

        capture._cleanup_old_packets()

        with capture._get_connection() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM packets").fetchall()]
        assert "old1" not in ids, "aged packet survived time-based retention"
        assert "fresh1" in ids, "fresh packet should not be pruned"

    def test_count_cap_still_enforced_after_time_retention(self, tmp_path):
        # max_packets=2, retention=very long → count cap is the only filter
        cap = TrafficCapture(
            db_path=str(tmp_path / "tc.db"), max_packets=2, retention_hours=24 * 365
        )
        now = datetime.now()
        for i in range(5):
            ts = (now - timedelta(minutes=i)).isoformat()
            self._insert(cap, f"p{i}", ts)
        cap._cleanup_old_packets()
        with cap._get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
        assert count == 2

    def test_retention_disabled_when_zero(self, tmp_path):
        # retention_hours=0 disables time-based prune; row cap still works
        cap = TrafficCapture(
            db_path=str(tmp_path / "tc.db"), max_packets=100, retention_hours=0
        )
        old_ts = (datetime.now() - timedelta(days=30)).isoformat()
        self._insert(cap, "ancient", old_ts)
        cap._cleanup_old_packets()
        with cap._get_connection() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM packets").fetchall()]
        assert "ancient" in ids, "retention=0 should not delete by time"


# ---------------------------------------------------------------------------
# 2026-07-09 frontier review Pri-3 fixes
# ---------------------------------------------------------------------------

class TestFrontierReviewFixes20260709:
    """Pins for the dissector-seam findings of the 2026-07-09 frontier
    worklist Pri-3 pass: RNS tree-rebuild arity, None-data tolerance, and
    the witnessed dissect-failure fallback in capture_packet."""

    def test_rns_build_tree_two_arg_rebuild_shape(self):
        """The storage rebuild path calls _build_tree(packet, metadata) with
        two args on EVERY dissector — RNSDissector's missing third arg
        raised TypeError for every stored RNS row and killed the whole
        packet-list query."""
        from monitoring.packet_dissectors import RNSDissector
        from monitoring.traffic_models import MeshPacket, PacketProtocol
        packet = MeshPacket(protocol=PacketProtocol.RNS, raw_bytes=b"",
                            size=0)
        tree = RNSDissector()._build_tree(packet, {})
        assert tree is not None

    def test_rns_dissect_none_data_no_crash(self):
        """MeshPacket treats data-less capture as legal, but the payload-size
        fallback ran len(raw_data) eagerly — dissect(None, ...) crashed."""
        from monitoring.packet_dissectors import RNSDissector
        packet = RNSDissector().dissect(None, {"protocol": "rns"})
        assert packet is not None
        assert packet.size == 0

    def test_capture_packet_dissector_crash_degrades_with_witness(
            self, capture):
        """A malformed packet (junk channel string) must degrade to the
        basic UNKNOWN packet with a dissect_errors stats witness — never
        kill the capture path."""
        from monitoring.traffic_models import PacketProtocol
        packet = capture.capture_packet(
            b"x", {"from": "!aabbccdd", "to": "!11223344",
                   "channel": "not-a-number"})
        assert packet is not None
        assert packet.protocol == PacketProtocol.UNKNOWN
        assert capture.get_stats()["dissect_errors"] == 1
