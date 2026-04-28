#!/usr/bin/env python3
"""Periodic point-in-time snapshot for the 24h LongFast HI diag.

Captures state that the meshtasticd journal doesn't surface directly:
- MQTT-port sockets (who is talking to local mosquitto + remote brokers)
- Node role distribution (from local map server's /api/nodes/geojson)
- Channel utilization samples from the last 5 min of journal
- "Ch. util >25%. Skip send" suppression count for the same window

Designed for cron every 5 minutes. Idempotent and silent on success.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HOST = socket.gethostname().split(".")[0]
OWNER = (
    os.environ.get("SUDO_USER")
    or os.environ.get("USER")
    or os.environ.get("LOGNAME")
    or "wh6gxz"
)
OUTDIR = Path(f"/home/{OWNER}/meshforge_diag_24h/{HOST}/snapshots")
OUTDIR.mkdir(parents=True, exist_ok=True)

CHAN_UTIL_RE = re.compile(
    r"Received from (\S+?)\):\s*air_util_tx=([\d.]+),\s*channel_utilization=([\d.]+)"
)


def collect_mqtt_connections() -> dict:
    try:
        r = subprocess.run(
            ["ss", "-atn"], capture_output=True, text=True, timeout=5
        )
        return {
            "lines": [
                line.strip()
                for line in r.stdout.splitlines()
                if ":1883" in line or ":8883" in line
            ]
        }
    except Exception as e:
        return {"error": str(e)}


def collect_role_distribution() -> dict:
    try:
        import urllib.request

        with urllib.request.urlopen(
            "http://localhost:5000/api/nodes/geojson", timeout=8
        ) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}

    roles: dict[str, int] = {}
    networks: dict[str, int] = {}
    for feat in data.get("features", []):
        props = feat.get("properties", {}) or {}
        role = props.get("role") or "UNKNOWN"
        roles[role] = roles.get(role, 0) + 1
        net = props.get("network") or "unknown"
        networks[net] = networks.get(net, 0) + 1
    return {
        "node_count": len(data.get("features", [])),
        "roles": roles,
        "networks": networks,
    }


def collect_journal_window() -> dict:
    try:
        r = subprocess.run(
            ["journalctl", "-u", "meshtasticd", "--since", "5 min ago", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as e:
        return {"error": str(e)}
    samples = []
    skip_count = 0
    for line in r.stdout.splitlines():
        m = CHAN_UTIL_RE.search(line)
        if m:
            samples.append(
                {
                    "sender": m.group(1).rstrip(")"),
                    "air_util_tx": float(m.group(2)),
                    "channel_utilization": float(m.group(3)),
                }
            )
        if "Ch. util >25%" in line:
            skip_count += 1
    return {
        "channel_util_samples": samples,
        "channel_util_skip_count": skip_count,
    }


def main() -> None:
    snap = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "host": HOST,
        "mqtt_connections": collect_mqtt_connections(),
        "role_distribution": collect_role_distribution(),
        "journal_5min": collect_journal_window(),
    }
    fname = OUTDIR / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    fname.write_text(json.dumps(snap, indent=2))


if __name__ == "__main__":
    main()
