"""Tests for utils.pcap_export.

Phase F: round-trip mock packets through PacketArchive → pcap_export →
struct-parse the pcap → assert timestamp + bytes preserved.

Parses pcap with stdlib ``struct`` (no scapy) to keep the test
dependency surface minimal.
"""

from __future__ import annotations

import sqlite3
import struct
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

import pytest

from utils.pcap_export import (
    DEFAULT_SNAPLEN,
    LINKTYPE_USER0,
    PCAP_MAGIC_MICROS,
    PCAP_VERSION_MAJOR,
    PCAP_VERSION_MINOR,
    PcapExportError,
    export_pcap,
)


# --- pcap parser for assertions -------------------------------------------


def _parse_pcap(path: Path) -> Tuple[dict, List[Tuple[int, int, bytes]]]:
    """Return (global_header_dict, [(ts_sec, ts_usec, body), ...])."""
    with path.open("rb") as fh:
        gh = fh.read(24)
        magic, vmaj, vmin, tz, sigfigs, snaplen, network = struct.unpack(
            "=IHHiIII", gh
        )
        records: List[Tuple[int, int, bytes]] = []
        while True:
            hdr = fh.read(16)
            if not hdr:
                break
            assert len(hdr) == 16, "truncated record header"
            ts_sec, ts_usec, incl, orig = struct.unpack("=IIII", hdr)
            body = fh.read(incl)
            assert len(body) == incl, "truncated record body"
            records.append((ts_sec, ts_usec, body))
        return (
            {
                "magic": magic,
                "vmaj": vmaj,
                "vmin": vmin,
                "tz": tz,
                "sigfigs": sigfigs,
                "snaplen": snaplen,
                "network": network,
            },
            records,
        )


# --- archive fixture -------------------------------------------------------


def _make_archive(path: Path) -> sqlite3.Connection:
    """Create an archived_packets DB matching PacketArchive's schema."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE archived_packets (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            protocol TEXT NOT NULL,
            direction TEXT NOT NULL,
            source TEXT,
            destination TEXT,
            port_name TEXT,
            raw_data BLOB,
            size INTEGER,
            metadata TEXT
        )
        """
    )
    conn.commit()
    return conn


def _insert(
    conn: sqlite3.Connection,
    *,
    pid: str,
    ts: datetime,
    protocol: str = "meshtastic",
    direction: str = "inbound",
    source: str = "!abcdef00",
    destination: str = "broadcast",
    port_name: str = "TEXT_MESSAGE_APP",
    raw_data: bytes = b"\x00\x01\x02\x03",
    size: int = 4,
    metadata: str = "{}",
) -> None:
    conn.execute(
        "INSERT INTO archived_packets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pid,
            ts.isoformat(),
            protocol,
            direction,
            source,
            destination,
            port_name,
            raw_data,
            size,
            metadata,
        ),
    )
    conn.commit()


# --- tests -----------------------------------------------------------------


class TestGlobalHeader:
    def test_writes_canonical_libpcap_header(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        conn = _make_archive(db)
        _insert(conn, pid="p1", ts=datetime(2026, 4, 27, 14, 0, 0), raw_data=b"\xaa")
        conn.close()

        out, count = export_pcap(
            db_path=db, output_path=tmp_path / "out.pcap"
        )
        assert count == 1

        header, _ = _parse_pcap(out)
        assert header["magic"] == PCAP_MAGIC_MICROS
        assert header["vmaj"] == PCAP_VERSION_MAJOR
        assert header["vmin"] == PCAP_VERSION_MINOR
        assert header["snaplen"] == DEFAULT_SNAPLEN
        assert header["network"] == LINKTYPE_USER0


class TestRoundTrip:
    def test_raw_bytes_preserved(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        conn = _make_archive(db)
        payload = b"\xde\xad\xbe\xef\x00\x01\x02\x03"
        _insert(conn, pid="p1", ts=datetime(2026, 4, 27, 14, 0, 0), raw_data=payload)
        conn.close()

        out, _ = export_pcap(db_path=db, output_path=tmp_path / "out.pcap")
        _, records = _parse_pcap(out)
        assert len(records) == 1
        _, _, body = records[0]
        assert body == payload

    def test_microsecond_precision_preserved(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        conn = _make_archive(db)
        ts = datetime(2026, 4, 27, 14, 0, 0, 123456)
        _insert(conn, pid="p1", ts=ts, raw_data=b"\x01")
        conn.close()

        out, _ = export_pcap(db_path=db, output_path=tmp_path / "out.pcap")
        _, records = _parse_pcap(out)
        ts_sec, ts_usec, _ = records[0]
        assert ts_sec == int(ts.timestamp())
        assert ts_usec == 123456

    def test_records_are_chronological(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        conn = _make_archive(db)
        base = datetime(2026, 4, 27, 14, 0, 0)
        # Insert out of order to verify ORDER BY timestamp ASC.
        _insert(conn, pid="p3", ts=base + timedelta(seconds=2), raw_data=b"\x03")
        _insert(conn, pid="p1", ts=base, raw_data=b"\x01")
        _insert(conn, pid="p2", ts=base + timedelta(seconds=1), raw_data=b"\x02")
        conn.close()

        out, _ = export_pcap(db_path=db, output_path=tmp_path / "out.pcap")
        _, records = _parse_pcap(out)
        bodies = [r[2] for r in records]
        assert bodies == [b"\x01", b"\x02", b"\x03"]


class TestFiltering:
    def test_since_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        conn = _make_archive(db)
        base = datetime(2026, 4, 27, 14, 0, 0)
        _insert(conn, pid="early", ts=base - timedelta(hours=1), raw_data=b"\x01")
        _insert(conn, pid="late", ts=base + timedelta(hours=1), raw_data=b"\x02")
        conn.close()

        out, count = export_pcap(
            db_path=db, since=base, output_path=tmp_path / "out.pcap"
        )
        assert count == 1
        _, records = _parse_pcap(out)
        assert records[0][2] == b"\x02"

    def test_until_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        conn = _make_archive(db)
        base = datetime(2026, 4, 27, 14, 0, 0)
        _insert(conn, pid="early", ts=base - timedelta(hours=1), raw_data=b"\x01")
        _insert(conn, pid="late", ts=base + timedelta(hours=1), raw_data=b"\x02")
        conn.close()

        out, count = export_pcap(
            db_path=db, until=base, output_path=tmp_path / "out.pcap"
        )
        assert count == 1
        _, records = _parse_pcap(out)
        assert records[0][2] == b"\x01"

    def test_protocol_filter_case_insensitive(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        conn = _make_archive(db)
        base = datetime(2026, 4, 27, 14, 0, 0)
        _insert(conn, pid="m", ts=base, protocol="meshtastic", raw_data=b"\x01")
        _insert(
            conn, pid="r", ts=base + timedelta(seconds=1), protocol="rns", raw_data=b"\x02"
        )
        # PacketArchive.archive() upper-cases protocol on insert (line 928 of
        # traffic_storage.py uses packet.protocol.value, but enum values are
        # lowercase — cover the upper-case row too defensively).
        _insert(
            conn, pid="M", ts=base + timedelta(seconds=2), protocol="MESHTASTIC", raw_data=b"\x03"
        )
        conn.close()

        out, count = export_pcap(
            db_path=db, protocol="MESHTASTIC", output_path=tmp_path / "out.pcap"
        )
        assert count == 2
        _, records = _parse_pcap(out)
        assert {r[2] for r in records} == {b"\x01", b"\x03"}

    def test_source_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        conn = _make_archive(db)
        base = datetime(2026, 4, 27, 14, 0, 0)
        _insert(conn, pid="a", ts=base, source="!aaaa", raw_data=b"\x01")
        _insert(
            conn, pid="b", ts=base + timedelta(seconds=1), source="!bbbb", raw_data=b"\x02"
        )
        conn.close()

        out, count = export_pcap(
            db_path=db, source="!bbbb", output_path=tmp_path / "out.pcap"
        )
        assert count == 1
        _, records = _parse_pcap(out)
        assert records[0][2] == b"\x02"


class TestNullAndEmptyRawData:
    def test_null_raw_data_is_skipped(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        conn = _make_archive(db)
        base = datetime(2026, 4, 27, 14, 0, 0)
        # NULL raw_data — metadata-only archive entry. Must not appear in pcap.
        conn.execute(
            "INSERT INTO archived_packets VALUES (?, ?, 'rns', 'inbound', '!a', 'b', 'p', NULL, 0, '{}')",
            ("nullpkt", base.isoformat()),
        )
        _insert(
            conn,
            pid="ok",
            ts=base + timedelta(seconds=1),
            raw_data=b"\xaa\xbb",
        )
        conn.commit()
        conn.close()

        out, count = export_pcap(db_path=db, output_path=tmp_path / "out.pcap")
        assert count == 1
        _, records = _parse_pcap(out)
        assert records[0][2] == b"\xaa\xbb"

    def test_empty_raw_data_is_skipped(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        conn = _make_archive(db)
        base = datetime(2026, 4, 27, 14, 0, 0)
        _insert(conn, pid="empty", ts=base, raw_data=b"", size=0)
        _insert(
            conn,
            pid="ok",
            ts=base + timedelta(seconds=1),
            raw_data=b"\xaa\xbb",
        )
        conn.close()

        out, count = export_pcap(db_path=db, output_path=tmp_path / "out.pcap")
        assert count == 1


class TestErrorPaths:
    def test_missing_db_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.db"
        with pytest.raises(FileNotFoundError) as excinfo:
            export_pcap(db_path=missing, output_path=tmp_path / "out.pcap")
        # Operator-actionable message.
        msg = str(excinfo.value)
        assert "enable_packet_archive" in msg or "PACKET_CAPTURE" in msg

    def test_empty_window_raises_pcap_export_error(self, tmp_path: Path) -> None:
        db = tmp_path / "archive.db"
        conn = _make_archive(db)
        # Archive exists but has no rows.
        conn.close()

        out_path = tmp_path / "out.pcap"
        with pytest.raises(PcapExportError):
            export_pcap(db_path=db, output_path=out_path)
        # No partial pcap left on disk.
        assert not out_path.exists()

    def test_no_partial_file_on_empty_export(self, tmp_path: Path) -> None:
        """Tempfile is cleaned up; no .pcap.tmp left behind."""
        db = tmp_path / "archive.db"
        conn = _make_archive(db)
        conn.close()

        with pytest.raises(PcapExportError):
            export_pcap(db_path=db, output_path=tmp_path / "out.pcap")

        leftovers = list(tmp_path.glob("*.pcap.tmp"))
        assert leftovers == [], f"tempfile leak: {leftovers}"


class TestSnaplenCap:
    def test_oversized_packet_is_truncated_but_orig_len_recorded(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "archive.db"
        conn = _make_archive(db)
        big = b"\xff" * 200
        _insert(conn, pid="big", ts=datetime(2026, 4, 27, 14, 0), raw_data=big, size=200)
        conn.close()

        out, _ = export_pcap(
            db_path=db, snaplen=64, output_path=tmp_path / "out.pcap"
        )
        with out.open("rb") as fh:
            fh.read(24)  # global header
            ts_sec, ts_usec, incl, orig = struct.unpack("=IIII", fh.read(16))
            body = fh.read(incl)
        assert incl == 64
        assert orig == 200
        assert body == big[:64]
