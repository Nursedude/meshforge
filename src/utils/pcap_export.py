"""PCAP export from packet_archive.db.

Reads MeshPacket raw bytes from the long-term archive and emits a
classic libpcap file. Wireshark / tshark can open it directly for
offline forensic analysis.

Phase F of the map/observability arc (2026-04-27).

Source DB: ``packet_archive.db`` (PacketArchive — opt-in, enabled via
``TrafficInspector.enable_archival()``). The metadata-only
``traffic_capture.db`` is NOT a valid source; ``MeshPacket.to_dict``
does not serialize ``raw_bytes``, so it has no wire bytes to export.

Frame format:
- Link type: LINKTYPE_USER0 (147), suitable for protocols without a
  registered DLT. Wireshark renders the body as raw hex; future work
  may add a Lua dissector keyed on this DLT.
- Timestamps: microsecond precision (PCAP magic 0xa1b2c3d4).
- Body: the ``raw_data`` BLOB column.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import struct
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Iterator, Optional, Tuple

from .db_helpers import connect_tuned
from .paths import get_real_user_home

logger = logging.getLogger(__name__)


# libpcap classic format constants. Values from
# https://www.tcpdump.org/linktypes.html and pcap(5).
PCAP_MAGIC_MICROS = 0xA1B2C3D4
PCAP_VERSION_MAJOR = 2
PCAP_VERSION_MINOR = 4
LINKTYPE_USER0 = 147
DEFAULT_SNAPLEN = 65535


class PcapExportError(RuntimeError):
    """pcap_export refused to produce output."""


def _archive_db_path() -> Path:
    return get_real_user_home() / ".config" / "meshforge" / "packet_archive.db"


def _write_global_header(
    fh: BinaryIO, *, snaplen: int = DEFAULT_SNAPLEN, linktype: int = LINKTYPE_USER0
) -> None:
    # Per pcap(5): magic, vmajor, vminor, thiszone, sigfigs, snaplen, network.
    # Native byte order matches the magic; readers detect endianness from the
    # magic and byte-swap accordingly.
    fh.write(
        struct.pack(
            "=IHHiIII",
            PCAP_MAGIC_MICROS,
            PCAP_VERSION_MAJOR,
            PCAP_VERSION_MINOR,
            0,
            0,
            snaplen,
            linktype,
        )
    )


def _write_record(
    fh: BinaryIO, *, ts: datetime, data: bytes, snaplen: int = DEFAULT_SNAPLEN
) -> None:
    # Per-packet header: ts_sec, ts_usec, incl_len (caplen), orig_len.
    epoch = ts.timestamp()
    ts_sec = int(epoch)
    ts_usec = int((epoch - ts_sec) * 1_000_000)
    orig_len = len(data)
    capped = data[:snaplen]
    fh.write(struct.pack("=IIII", ts_sec, ts_usec, len(capped), orig_len))
    fh.write(capped)


def _query_archive(
    conn: sqlite3.Connection,
    *,
    since: Optional[datetime],
    until: Optional[datetime],
    protocol: Optional[str],
    source: Optional[str],
) -> Iterator[Tuple[datetime, bytes]]:
    """Yield (timestamp, raw_bytes) pairs in chronological order.

    Skips rows where ``raw_data`` is NULL or empty — those are
    metadata-only archive entries (e.g., post-#33 path-trace records)
    and would produce zero-length pcap frames that crash some readers.
    """
    where = ["raw_data IS NOT NULL", "length(raw_data) > 0"]
    params: list = []
    if since is not None:
        where.append("timestamp >= ?")
        params.append(since.isoformat())
    if until is not None:
        where.append("timestamp <= ?")
        params.append(until.isoformat())
    if protocol is not None:
        # PacketArchive stores protocol from packet.protocol.value, which is
        # lower-case ("meshtastic", "rns"). Normalize either way.
        where.append("LOWER(protocol) = ?")
        params.append(protocol.lower())
    if source is not None:
        where.append("source = ?")
        params.append(source)

    sql = (
        "SELECT timestamp, raw_data FROM archived_packets "
        f"WHERE {' AND '.join(where)} ORDER BY timestamp ASC"
    )
    cursor = conn.execute(sql, params)
    for ts_iso, raw in cursor:
        try:
            ts = datetime.fromisoformat(ts_iso)
        except (TypeError, ValueError):
            logger.debug("Skipping archive row with malformed timestamp: %r", ts_iso)
            continue
        yield ts, bytes(raw)


def export_pcap(
    *,
    db_path: Optional[Path] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    protocol: Optional[str] = None,
    source: Optional[str] = None,
    output_path: Optional[Path] = None,
    snaplen: int = DEFAULT_SNAPLEN,
    linktype: int = LINKTYPE_USER0,
) -> Tuple[Path, int]:
    """Export packets from packet_archive.db to a libpcap file.

    Returns ``(output_path, packet_count)``.

    Raises:
        FileNotFoundError: archive DB does not exist (operator hasn't
            enabled archival yet).
        PcapExportError: archive exists but is empty for the requested
            window. The error message includes the operator-actionable
            enable command.
    """
    db_path = Path(db_path) if db_path else _archive_db_path()
    if not db_path.exists():
        raise FileNotFoundError(
            f"packet_archive.db not found at {db_path}. "
            "Enable archival first: see docs/PACKET_CAPTURE.md or run "
            "scripts/enable_packet_archive.py on this box."
        )

    if output_path is None:
        ts_tag = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = (
            get_real_user_home() / ".cache" / "meshforge" / f"capture-{ts_tag}.pcap"
        )
    output_path = Path(output_path)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise PcapExportError(
            f"Cannot write to {output_path.parent}: {e}. "
            "On fleet boxes this often means the dir is root-owned from a "
            "prior sudo install. Fix: "
            f"sudo chown -R $(id -un):$(id -gn) {output_path.parent.parent} "
            "(or pass --output to a writable path)."
        ) from None

    # Atomic write: build into tempfile in same dir, rename on success.
    # Avoids leaving partial pcaps on disk if SIGINT mid-export.
    try:
        tmp = tempfile.NamedTemporaryFile(
            prefix=output_path.stem + ".",
            suffix=".pcap.tmp",
            dir=str(output_path.parent),
            delete=False,
        )
    except PermissionError as e:
        raise PcapExportError(
            f"Cannot create tempfile in {output_path.parent}: {e}. "
            "Fix: sudo chown -R $(id -un):$(id -gn) "
            f"{output_path.parent.parent} (or pass --output to a writable path)."
        ) from None
    tmp_path = Path(tmp.name)
    count = 0
    try:
        with tmp:
            _write_global_header(tmp, snaplen=snaplen, linktype=linktype)
            conn = connect_tuned(db_path)
            try:
                for ts, raw in _query_archive(
                    conn,
                    since=since,
                    until=until,
                    protocol=protocol,
                    source=source,
                ):
                    _write_record(tmp, ts=ts, data=raw, snaplen=snaplen)
                    count += 1
            finally:
                conn.close()
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    if count == 0:
        tmp_path.unlink(missing_ok=True)
        raise PcapExportError(
            "No archived packets matched the requested window. "
            "If archival was just enabled, the archive may not yet contain "
            "this time range. Verify with: "
            f"sqlite3 {db_path} 'SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM archived_packets;'"
        )

    shutil.move(str(tmp_path), str(output_path))
    return output_path, count


# --- CLI -------------------------------------------------------------------


def _parse_when(s: str) -> datetime:
    """Accept ISO 8601 or `YYYY-MM-DD HH:MM[:SS]`."""
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        raise argparse.ArgumentTypeError(
            f"Could not parse timestamp {s!r}. "
            "Use ISO 8601 (2026-04-27T14:00:00) or 'YYYY-MM-DD HH:MM'."
        )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pcap_export",
        description=(
            "Export packets from packet_archive.db to a libpcap file. "
            "Open the result in Wireshark or tshark."
        ),
    )
    p.add_argument("--since", type=_parse_when, help="Start time (inclusive).")
    p.add_argument("--until", type=_parse_when, help="End time (inclusive).")
    p.add_argument(
        "--protocol",
        choices=("meshtastic", "rns"),
        help="Filter by protocol (case-insensitive).",
    )
    p.add_argument("--source", help="Filter by source node id.")
    p.add_argument(
        "--output",
        type=Path,
        help="Output pcap path (default: ~/.cache/meshforge/capture-<ts>.pcap).",
    )
    p.add_argument(
        "--db",
        type=Path,
        help="Override archive DB path (default: ~/.config/meshforge/packet_archive.db).",
    )
    return p


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        out, count = export_pcap(
            db_path=args.db,
            since=args.since,
            until=args.until,
            protocol=args.protocol,
            source=args.source,
            output_path=args.output,
        )
    except FileNotFoundError as e:
        print(f"pcap_export: {e}", file=sys.stderr)
        return 2
    except PcapExportError as e:
        print(f"pcap_export: {e}", file=sys.stderr)
        return 3
    print(f"Wrote {count} packet(s) to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
