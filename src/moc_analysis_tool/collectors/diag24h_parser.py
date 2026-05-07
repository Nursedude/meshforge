"""diag24h log parser → presentation_capture.db.

Re-parses ``~/meshforge_diag_24h/<box>/meshtasticd.log`` (and optionally
the snapshots/ JSON sidecars) into a structured, queryable SQLite store.

Why this collector exists: on the canonical capture box the live
``traffic_capture.db`` may not be actively written (no daemon pumps
packets in unless one is started), but the diag_24h cron has been
faithfully tailing the meshtasticd journal for days. That journal is
the only continuous record of every packet the box received, and is
the canonical input for analysis runs.

Idempotent: re-running the parser over the same log file does not
duplicate packet rows (UNIQUE on packet_id × source × timestamp).

Run via the CLI:
    python -m moc_analysis_tool.collectors.diag24h_parser \\
        --log ~/meshforge_diag_24h/<host>/meshtasticd.log \\
        --since 2026-04-28 --until 2026-05-10

Or programmatically as part of an analysis run.
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple, Optional

from utils.db_helpers import connect_tuned
from utils.db_inventory import find_spec
from utils.paths import get_real_user_home

logger = logging.getLogger(__name__)


# ── Log line shape (meshtasticd journal as captured by diag_24h) ──────
#
# Example:
#   2026-04-28T08:27:02-10:00 <host> meshtasticd[1087]: INFO  | 18:27:00
#       784349 [Router] Received traceroute from=0xa2e1bc60, id=0x14ff1e28,
#       portnum=70, payloadlen=46
#
# Each logical packet is logged twice — once as the app kind (traceroute,
# position, nodeinfo, telemetry, text, ...) and once as "Received routing
# from=..." (the routing-app handler echo). The parser keeps the kind
# variant and skips the routing variant by deduping on (packet_id, source).

_LINE_RE = re.compile(
    r"^(?P<wallts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+\-]\d{2}:\d{2}|Z))"
    r"\s+(?P<host>\S+)\s+meshtasticd\[\d+\]:\s+\w+\s+\|\s+\S+\s+\d+\s+"
    r"\[Router\]\s+Received\s+(?P<kind>\S+)\s+from=0x(?P<from_hex>[0-9a-fA-F]+),\s*"
    r"id=0x(?P<pid_hex>[0-9a-fA-F]+),\s+portnum=(?P<portnum>-?\d+)"
    r"(?:,\s+payloadlen=(?P<paylen>\d+))?"
)

# Channel-utilization companion line:
#   ... [Router] (Received from FXV1): air_util_tx=0.640361,
#       channel_utilization=4.421667, battery_level=101, voltage=4.250000
_CHUTIL_RE = re.compile(
    r"^(?P<wallts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+\-]\d{2}:\d{2}|Z))"
    r".*?\(Received\s+from\s+(?P<sender>\S+?)\):\s*"
    r"air_util_tx=(?P<air>[\d.]+),\s*"
    r"channel_utilization=(?P<cu>[\d.]+)"
    r"(?:,\s*battery_level=(?P<batt>\d+))?"
    r"(?:,\s*voltage=(?P<volt>[\d.]+))?"
)

# PortNum integers we map to canonical app names. Mirrors
# ``monitoring/packet_dissectors.MESHTASTIC_PORTS`` — duplicated here so
# this collector can run without importing the dissector layer.
_PORTNUM_NAME = {
    0: "UNKNOWN_APP",
    1: "TEXT_MESSAGE_APP",
    2: "REMOTE_HARDWARE_APP",
    3: "POSITION_APP",
    4: "NODEINFO_APP",
    5: "ROUTING_APP",
    6: "ADMIN_APP",
    7: "TEXT_MESSAGE_COMPRESSED_APP",
    8: "WAYPOINT_APP",
    9: "AUDIO_APP",
    10: "DETECTION_SENSOR_APP",
    32: "REPLY_APP",
    33: "IP_TUNNEL_APP",
    34: "PAXCOUNTER_APP",
    64: "SERIAL_APP",
    65: "STORE_FORWARD_APP",
    66: "RANGE_TEST_APP",
    67: "TELEMETRY_APP",
    68: "ZPS_APP",
    69: "SIMULATOR_APP",
    70: "TRACEROUTE_APP",
    71: "NEIGHBORINFO_APP",
    72: "ATAK_PLUGIN",
    73: "MAP_REPORT_APP",
    74: "POWERSTRESS_APP",
    256: "PRIVATE_APP",
    257: "ATAK_FORWARDER",
}

_KIND_TO_PORTNUM_FALLBACK = {
    "traceroute": 70,
    "position": 3,
    "nodeinfo": 4,
    "DeviceTelemetry": 67,
    "HostMetrics": 67,
    "EnvironmentMetrics": 67,
    "PowerMetrics": 67,
    "AirQualityMetrics": 67,
    "text": 1,
    "TextMessage": 1,
    "routing": 5,
}


def _portnum_to_name(portnum: int, kind: Optional[str] = None) -> str:
    """Return a canonical app name for a portnum int.

    Anomalous portnums (e.g. ``portnum=800129062`` from a malformed log
    line) get bucketed as ``MALFORMED`` rather than failing the row.
    """
    name = _PORTNUM_NAME.get(portnum)
    if name is not None:
        return name
    if portnum < 0 or portnum > 511:
        return "MALFORMED"
    if kind and kind in _KIND_TO_PORTNUM_FALLBACK:
        return _PORTNUM_NAME.get(_KIND_TO_PORTNUM_FALLBACK[kind], f"PORT_{portnum}")
    return f"PORT_{portnum}"


class ParsedPacket(NamedTuple):
    timestamp: datetime
    host: str
    kind: str
    source: str           # "!aabbccdd" form
    packet_id: str        # "0x..." form
    portnum: int
    port_name: str
    payload_len: Optional[int]


class ParsedTelemetry(NamedTuple):
    timestamp: datetime
    sender: str           # short_name as logged (e.g. "FXV1", "KREP")
    air_util_tx: float
    channel_utilization: float
    battery_level: Optional[int]
    voltage: Optional[float]


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the presentation_capture schema if absent.

    Two tables: ``packets`` (one row per Received-from-X log line, deduped
    on packet_id+source) and ``telemetry_samples`` (per-sender channel util
    + battery + voltage from the ``(Received from FXV1)`` companion line).
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS packets (
            timestamp REAL NOT NULL,
            host TEXT,
            kind TEXT,
            source TEXT NOT NULL,
            packet_id TEXT NOT NULL,
            portnum INTEGER,
            port_name TEXT,
            payload_len INTEGER,
            PRIMARY KEY (packet_id, source)
        );

        CREATE INDEX IF NOT EXISTS idx_packets_source ON packets(source);
        CREATE INDEX IF NOT EXISTS idx_packets_port ON packets(port_name);
        CREATE INDEX IF NOT EXISTS idx_packets_ts ON packets(timestamp);

        CREATE TABLE IF NOT EXISTS telemetry_samples (
            timestamp REAL NOT NULL,
            sender TEXT NOT NULL,
            air_util_tx REAL,
            channel_utilization REAL,
            battery_level INTEGER,
            voltage REAL
        );

        CREATE INDEX IF NOT EXISTS idx_telem_sender ON telemetry_samples(sender);
        CREATE INDEX IF NOT EXISTS idx_telem_ts ON telemetry_samples(timestamp);

        CREATE TABLE IF NOT EXISTS parser_runs (
            run_started REAL NOT NULL,
            run_finished REAL,
            log_path TEXT NOT NULL,
            log_size_bytes INTEGER,
            packets_inserted INTEGER,
            telemetry_inserted INTEGER,
            packets_skipped INTEGER,
            window_start REAL,
            window_end REAL
        );
    """)


def _parse_iso_ts(s: str) -> datetime:
    # Python 3.11+ handles "Z" suffix; older versions need a swap.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def iter_log_packets(
    path: Path,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> Iterator[ParsedPacket]:
    """Yield ParsedPacket for every Received-X line in the log within window.

    Lines whose ``kind`` is ``routing`` are dropped — they're the
    routing-handler echo of a packet already yielded under its real kind.
    """
    with path.open("rt", encoding="utf-8", errors="replace") as fp:
        for raw in fp:
            m = _LINE_RE.match(raw)
            if not m:
                continue
            kind = m.group("kind")
            if kind == "routing":
                # Routing-handler echo of the same logical packet — skip.
                continue
            ts = _parse_iso_ts(m.group("wallts"))
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue
            try:
                portnum = int(m.group("portnum"))
            except ValueError:
                continue
            from_hex = m.group("from_hex").lower()
            pid_hex = m.group("pid_hex").lower()
            paylen_str = m.group("paylen")
            yield ParsedPacket(
                timestamp=ts,
                host=m.group("host"),
                kind=kind,
                source=f"!{from_hex}",
                packet_id=f"0x{pid_hex}",
                portnum=portnum,
                port_name=_portnum_to_name(portnum, kind=kind),
                payload_len=int(paylen_str) if paylen_str else None,
            )


def iter_log_telemetry(
    path: Path,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> Iterator[ParsedTelemetry]:
    """Yield ParsedTelemetry for every ``(Received from X):`` line."""
    with path.open("rt", encoding="utf-8", errors="replace") as fp:
        for raw in fp:
            m = _CHUTIL_RE.match(raw)
            if not m:
                continue
            ts = _parse_iso_ts(m.group("wallts"))
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue
            batt = m.group("batt")
            volt = m.group("volt")
            yield ParsedTelemetry(
                timestamp=ts,
                sender=m.group("sender"),
                air_util_tx=float(m.group("air")),
                channel_utilization=float(m.group("cu")),
                battery_level=int(batt) if batt else None,
                voltage=float(volt) if volt else None,
            )


def parse_log_into_db(
    log_path: Path,
    db_path: Optional[Path] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    batch_size: int = 1000,
) -> dict:
    """Parse a meshtasticd.log into ``presentation_capture.db``.

    Args:
        log_path: Path to the meshtasticd.log captured by diag_24h.
        db_path: Where to write the SQLite store. Defaults to the
            presentation_capture DBSpec path.
        since/until: Optional UTC time-window filter applied at parse time.
        batch_size: How many rows to insert per executemany (memory bound).

    Returns:
        A summary dict with counts (inserted, skipped, telemetry_inserted)
        and the actual time window the log covered.
    """
    if not log_path.exists():
        raise FileNotFoundError(f"meshtasticd log not found: {log_path}")

    if db_path is None:
        spec = find_spec("presentation_capture")
        if spec is None:
            raise RuntimeError("presentation_capture DBSpec missing")
        db_path = spec.path_factory()

    db_path.parent.mkdir(parents=True, exist_ok=True)

    log_size = log_path.stat().st_size
    run_started = datetime.now(timezone.utc).timestamp()

    inserted = skipped = telemetry_inserted = 0
    min_ts: Optional[float] = None
    max_ts: Optional[float] = None

    with connect_tuned(db_path) as conn:
        _ensure_schema(conn)

        batch: list = []
        for pkt in iter_log_packets(log_path, since=since, until=until):
            ts_epoch = pkt.timestamp.timestamp()
            min_ts = ts_epoch if min_ts is None else min(min_ts, ts_epoch)
            max_ts = ts_epoch if max_ts is None else max(max_ts, ts_epoch)
            batch.append((
                ts_epoch, pkt.host, pkt.kind, pkt.source,
                pkt.packet_id, pkt.portnum, pkt.port_name, pkt.payload_len,
            ))
            if len(batch) >= batch_size:
                ins, skip = _insert_packet_batch(conn, batch)
                inserted += ins
                skipped += skip
                batch.clear()
        if batch:
            ins, skip = _insert_packet_batch(conn, batch)
            inserted += ins
            skipped += skip
            batch.clear()

        # Telemetry samples
        telem_batch: list = []
        for sample in iter_log_telemetry(log_path, since=since, until=until):
            telem_batch.append((
                sample.timestamp.timestamp(), sample.sender,
                sample.air_util_tx, sample.channel_utilization,
                sample.battery_level, sample.voltage,
            ))
            if len(telem_batch) >= batch_size:
                conn.executemany(
                    "INSERT INTO telemetry_samples "
                    "(timestamp, sender, air_util_tx, channel_utilization, "
                    "battery_level, voltage) VALUES (?, ?, ?, ?, ?, ?)",
                    telem_batch,
                )
                telemetry_inserted += len(telem_batch)
                telem_batch.clear()
        if telem_batch:
            conn.executemany(
                "INSERT INTO telemetry_samples "
                "(timestamp, sender, air_util_tx, channel_utilization, "
                "battery_level, voltage) VALUES (?, ?, ?, ?, ?, ?)",
                telem_batch,
            )
            telemetry_inserted += len(telem_batch)
            telem_batch.clear()

        run_finished = datetime.now(timezone.utc).timestamp()
        conn.execute(
            "INSERT INTO parser_runs (run_started, run_finished, log_path, "
            "log_size_bytes, packets_inserted, telemetry_inserted, "
            "packets_skipped, window_start, window_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_started, run_finished, str(log_path), log_size,
             inserted, telemetry_inserted, skipped, min_ts, max_ts),
        )
        conn.commit()

    return {
        "log_path": str(log_path),
        "log_size_bytes": log_size,
        "packets_inserted": inserted,
        "packets_skipped_duplicate": skipped,
        "telemetry_inserted": telemetry_inserted,
        "window_start": min_ts,
        "window_end": max_ts,
    }


def _insert_packet_batch(conn: sqlite3.Connection, batch: list) -> tuple:
    """Insert a batch of packet rows; report (inserted, skipped_duplicate)."""
    before = conn.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO packets "
        "(timestamp, host, kind, source, packet_id, portnum, port_name, "
        "payload_len) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        batch,
    )
    after = conn.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
    inserted = after - before
    return inserted, len(batch) - inserted


def _default_log_path() -> Path:
    """Default to ~/meshforge_diag_24h/<hostname>/meshtasticd.log."""
    import socket
    return (
        get_real_user_home()
        / "meshforge_diag_24h"
        / socket.gethostname()
        / "meshtasticd.log"
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log", type=Path, default=None,
        help="meshtasticd.log path; default ~/meshforge_diag_24h/<host>/meshtasticd.log",
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help="presentation_capture.db path; default from DBSpec",
    )
    parser.add_argument("--since", type=str, default=None,
                        help="UTC ISO datetime, inclusive lower bound")
    parser.add_argument("--until", type=str, default=None,
                        help="UTC ISO datetime, inclusive upper bound")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    log_path = args.log or _default_log_path()
    since = _parse_iso_ts(args.since) if args.since else None
    until = _parse_iso_ts(args.until) if args.until else None

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    summary = parse_log_into_db(
        log_path=log_path, db_path=args.db, since=since, until=until,
    )
    for k, v in summary.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
