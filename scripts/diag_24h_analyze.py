#!/usr/bin/env python3
"""Analyze a 24h diag capture into structured + human-readable reports.

Usage:
    diag_24h_analyze.py <outdir>

Produces (alongside the captures):
    report.json   machine-readable summary
    report.md     operator-readable summary

Designed to be idempotent and to tolerate partial captures (early stop,
journal rotation, missing snapshots).
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median


RX_TEXT_RE = re.compile(
    r"Received text msg from=0x([0-9a-fA-F]+),\s*id=0x([0-9a-fA-F]+)"
)
RX_PKT_RE = re.compile(
    r"Received (\w+) from=0x([0-9a-fA-F]+),\s*id=0x([0-9a-fA-F]+).*?portnum=(-?\d+)"
)
CHAN_UTIL_RE = re.compile(
    r"Received from (\S+?)\):\s*air_util_tx=([\d.]+),\s*channel_utilization=([\d.]+)"
)


def parse_meshtasticd_log(path: Path) -> dict | None:
    if not path.exists():
        return None

    counters: Counter[str] = Counter()
    received_from: Counter[str] = Counter()
    portnums: Counter[str] = Counter()
    chan_util_values: list[float] = []
    chan_util_by_sender: dict[str, list[float]] = defaultdict(list)
    skip_send_events = 0
    history_evict_oldest = 0
    history_match = 0
    json_publish = 0
    json_subscribe = 0
    api_text_from_zero = 0  # local-API-sourced texts (firmware logs from=0x0)
    unique_packet_ids: set[str] = set()
    text_messages = 0

    for line in path.read_text(errors="replace").splitlines():
        m = RX_TEXT_RE.search(line)
        if m:
            text_messages += 1
            from_hex = m.group(1)
            received_from[f"0x{from_hex}"] += 1
            unique_packet_ids.add(f"0x{m.group(2)}")
            if from_hex.lower().lstrip("0") == "":
                api_text_from_zero += 1
        m = RX_PKT_RE.search(line)
        if m:
            kind, from_hex, pid_hex, portnum = m.groups()
            counters[f"recv_{kind.lower()}"] += 1
            received_from[f"0x{from_hex}"] += 1
            # Valid Meshtastic portnums are small non-negative ints (#1159).
            # Huge negative values are protobuf decode edge cases — drop at ingest.
            try:
                pn_int = int(portnum)
                if 0 <= pn_int <= 1023:
                    portnums[portnum] += 1
            except ValueError:
                pass
            unique_packet_ids.add(f"0x{pid_hex}")
        m = CHAN_UTIL_RE.search(line)
        if m:
            sender = m.group(1).rstrip(")")
            cu = float(m.group(3))
            chan_util_values.append(cu)
            chan_util_by_sender[sender].append(cu)
        if "Ch. util >25%" in line:
            skip_send_events += 1
        if "Reusing slot" in line and "OLDEST SLOT" in line:
            history_evict_oldest += 1
        if "MATCHED PACKET" in line:
            history_match += 1
        if "JSON publish" in line:
            json_publish += 1
        if "JSON subscribe" in line:
            json_subscribe += 1

    chan_util_summary = None
    if chan_util_values:
        chan_util_summary = {
            "samples": len(chan_util_values),
            "mean_pct": round(mean(chan_util_values), 2),
            "median_pct": round(median(chan_util_values), 2),
            "max_pct": round(max(chan_util_values), 2),
            "min_pct": round(min(chan_util_values), 2),
        }

    chan_util_per_sender = {}
    for sender, values in chan_util_by_sender.items():
        chan_util_per_sender[sender] = {
            "samples": len(values),
            "mean_pct": round(mean(values), 2),
            "max_pct": round(max(values), 2),
        }
    chan_util_per_sender_top = dict(
        sorted(
            chan_util_per_sender.items(),
            key=lambda kv: kv[1]["mean_pct"],
            reverse=True,
        )[:15]
    )

    return {
        "log_size_bytes": path.stat().st_size,
        "text_messages": text_messages,
        "api_text_from_zero": api_text_from_zero,
        "counters": dict(counters),
        "channel_utilization": chan_util_summary,
        "channel_util_top_senders": chan_util_per_sender_top,
        "channel_util_skip_send_events": skip_send_events,
        "packet_history": {
            "evict_oldest_slot": history_evict_oldest,
            "matched_packet": history_match,
        },
        "mqtt": {
            "json_publish": json_publish,
            "json_subscribe": json_subscribe,
        },
        "unique_packet_ids": len(unique_packet_ids),
        "received_from_top20": received_from.most_common(20),
        "portnums": dict(portnums),
    }


def parse_mosquitto_log(path: Path) -> dict | None:
    if not path.exists():
        return None
    connect = 0
    disconnect = 0
    publish = 0
    subscribe = 0
    for line in path.read_text(errors="replace").splitlines():
        ll = line.lower()
        if "new client connected" in ll or "new connection" in ll:
            connect += 1
        elif "client disconnected" in ll:
            disconnect += 1
        elif "received publish" in ll:
            publish += 1
        elif "received subscribe" in ll:
            subscribe += 1
    return {
        "log_size_bytes": path.stat().st_size,
        "connect_events": connect,
        "disconnect_events": disconnect,
        "publish_events": publish,
        "subscribe_events": subscribe,
    }


def parse_snapshots(snap_dir: Path, t0_iso: str | None = None) -> dict | None:
    if not snap_dir.exists():
        return None
    snaps = sorted(snap_dir.glob("*.json"))
    # Clamp to T0 window — drop leftover snapshots from prior runs (#1160).
    # Snapshot filename stems are YYYYMMDDTHHMMSSZ; T0 is ISO-8601.
    if t0_iso and t0_iso != "unknown":
        try:
            t0_dt = datetime.strptime(t0_iso, "%Y-%m-%dT%H:%M:%SZ")
            def _in_window(p: Path) -> bool:
                try:
                    return datetime.strptime(p.stem, "%Y%m%dT%H%M%SZ") >= t0_dt
                except ValueError:
                    return False
            snaps = [s for s in snaps if _in_window(s)]
        except ValueError:
            pass
    if not snaps:
        return None
    role_dists = []
    skip_count_series = []
    chan_util_series_mean = []
    for s in snaps:
        try:
            d = json.loads(s.read_text())
        except Exception:
            continue
        rd = (d.get("role_distribution") or {}).get("roles")
        if rd:
            role_dists.append(rd)
        jw = d.get("journal_5min") or {}
        if "channel_util_skip_count" in jw:
            skip_count_series.append((d.get("ts"), jw["channel_util_skip_count"]))
        samples = jw.get("channel_util_samples") or []
        if samples:
            cu_vals = [s["channel_utilization"] for s in samples]
            chan_util_series_mean.append((d.get("ts"), round(mean(cu_vals), 2)))
    return {
        "snapshot_count": len(snaps),
        "first_ts": snaps[0].stem if snaps else None,
        "last_ts": snaps[-1].stem if snaps else None,
        "role_distribution_latest": role_dists[-1] if role_dists else None,
        "skip_count_series": skip_count_series,
        "channel_util_series_mean": chan_util_series_mean,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: diag_24h_analyze.py <outdir>", file=sys.stderr)
        return 2
    outdir = Path(sys.argv[1])
    if not outdir.is_dir():
        print(f"Not a directory: {outdir}", file=sys.stderr)
        return 2

    t0 = (
        (outdir / "T0.txt").read_text().strip()
        if (outdir / "T0.txt").exists()
        else "unknown"
    )

    report = {
        "host": outdir.name,
        "t0": t0,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "meshtasticd": parse_meshtasticd_log(outdir / "meshtasticd.log"),
        "mosquitto": parse_mosquitto_log(outdir / "mosquitto.log"),
        "snapshots": parse_snapshots(outdir / "snapshots", t0),
    }

    (outdir / "report.json").write_text(json.dumps(report, indent=2))

    md: list[str] = []
    md.append(f"# 24h Diag Report — {report['host']}\n")
    md.append(f"- T0:        `{t0}`")
    md.append(f"- Analyzed:  `{report['analyzed_at']}`")
    snaps = report.get("snapshots") or {}
    if snaps:
        md.append(f"- Snapshots: {snaps.get('snapshot_count', 0)} "
                  f"({snaps.get('first_ts')} → {snaps.get('last_ts')})")
    md.append("")

    mt = report.get("meshtasticd") or {}
    if mt:
        cu = mt.get("channel_utilization") or {}
        md.append("## RF — meshtasticd journal\n")
        md.append(f"- Log size: {mt.get('log_size_bytes', 0):,} bytes")
        md.append(f"- Unique packet IDs observed: {mt.get('unique_packet_ids', 0):,}")
        md.append(f"- Text messages received: {mt.get('text_messages', 0):,}")
        md.append(
            f"  - of which API-sourced (from=0x0, local send): "
            f"{mt.get('api_text_from_zero', 0)}"
        )
        if cu:
            md.append(
                f"- Channel utilization (samples={cu.get('samples')}): "
                f"mean {cu.get('mean_pct')}%, median {cu.get('median_pct')}%, "
                f"max {cu.get('max_pct')}%, min {cu.get('min_pct')}%"
            )
        md.append(
            f"- 'Ch. util >25%. Skip send' suppression events: "
            f"{mt.get('channel_util_skip_send_events', 0)}"
        )
        ph = mt.get("packet_history") or {}
        md.append(
            f"- Packet-history slot evictions (potential drops): "
            f"{ph.get('evict_oldest_slot', 0):,}"
        )
        md.append(
            f"- Packet-history dedup matches (rebroadcast suppressions): "
            f"{ph.get('matched_packet', 0):,}"
        )
        mqtt = mt.get("mqtt") or {}
        md.append(
            f"- meshtasticd JSON publishes: {mqtt.get('json_publish', 0):,} | "
            f"subscribes: {mqtt.get('json_subscribe', 0):,}"
        )
        md.append("")

        md.append("### Top RF senders (by packet count)\n")
        for sender, count in (mt.get("received_from_top20") or [])[:20]:
            md.append(f"- `{sender}`: {count:,}")
        md.append("")

        md.append("### Channel utilization — top reporting senders (mean %)\n")
        cu_per = mt.get("channel_util_top_senders") or {}
        for sender, stats in cu_per.items():
            md.append(
                f"- `{sender}`: mean {stats['mean_pct']}%, "
                f"max {stats['max_pct']}%, samples {stats['samples']}"
            )
        md.append("")

        md.append("### Counters\n")
        for k, v in sorted((mt.get("counters") or {}).items()):
            md.append(f"- `{k}`: {v:,}")
        md.append("")

        md.append("### Portnums seen\n")
        # Sort by count desc; drop count=1 single-shot noise (#1159).
        for portnum, count in sorted(
            (mt.get("portnums") or {}).items(),
            key=lambda kv: kv[1],
            reverse=True,
        ):
            if count < 2:
                continue
            md.append(f"- `{portnum}`: {count:,}")
        md.append("")

    mq = report.get("mosquitto")
    if mq:
        md.append("## MQTT — local broker (mosquitto.log)\n")
        md.append(f"- Log size: {mq.get('log_size_bytes', 0):,} bytes")
        md.append(f"- Connect events: {mq.get('connect_events', 0):,}")
        md.append(f"- Disconnect events: {mq.get('disconnect_events', 0):,}")
        md.append(f"- Publish events: {mq.get('publish_events', 0):,}")
        md.append(f"- Subscribe events: {mq.get('subscribe_events', 0):,}")
        md.append("")

    if snaps:
        md.append("## Node roles — latest snapshot\n")
        for role, count in (snaps.get("role_distribution_latest") or {}).items():
            md.append(f"- `{role}`: {count}")
        md.append("")

    (outdir / "report.md").write_text("\n".join(md))
    print(f"Wrote {outdir / 'report.json'}")
    print(f"Wrote {outdir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
