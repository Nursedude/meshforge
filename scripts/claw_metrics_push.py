#!/usr/bin/env python3
"""Paint fleet metrics onto the dude-claw's OLED (display_print over NATS).

Runs on the claw-brain box from the operator crontab, wired to the
cron-verdict regime:

    */5 * * * * cd /opt/meshforge && PYTHONPATH=src python3 scripts/claw_metrics_push.py >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh claw_metrics $?

Rows (23-char budget, SSD1306 metric rows 0-1):
    row 0:  mesh:<directory total> fed:<reachable>/<peers>
    row 1:  wd:<signal count> <OK|SIG> <HH:MM>

Honesty: any unreadable source or failed push exits NONZERO so the verdict
line says FAIL and the cron_verdict_stale probe pages — never paint a row we
couldn't actually compute (the firmware adds its own "(old)" marker when the
pusher stops updating). Operator values (device name, NATS server) come from
the claw env file, not this script (MF014).
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai._util import fetch_json  # noqa: E402
from mini_dudeai.nats_client import NatsConnection, NatsError  # noqa: E402

WATCHDOG_PATH = "/var/lib/meshforge/watchdog.json"
STATUS_URL = "http://localhost:5000/api/status"


def _load_claw_env() -> dict:
    """KEY=VAL lines from the claw env file (same file the daemon loads)."""
    path = os.path.join(os.path.expanduser("~"),
                        ".config", "meshforge", "mini_dudeai_claw.env")
    if not os.path.exists(path):
        raise SystemExit(f"claw_metrics: {path} missing — is this the claw-brain box?")
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def build_rows() -> list[str]:
    status, err = fetch_json(STATUS_URL, timeout=10)
    if err:
        raise SystemExit(f"claw_metrics: {STATUS_URL} unreachable: {err}")
    total = ((status.get("directory") or {}).get("total"))
    peers = ((status.get("federation") or {}).get("peer_status")) or []
    if total is None:
        raise SystemExit("claw_metrics: /api/status has no directory.total")
    reachable = sum(1 for p in peers
                    if p.get("reachable", False) and not p.get("in_backoff"))
    row0 = f"mesh:{total} fed:{reachable}/{len(peers)}"

    try:
        with open(WATCHDOG_PATH) as f:
            wd = json.load(f)
        n_sig = len(wd.get("signals") or [])
    except (OSError, ValueError) as e:
        raise SystemExit(f"claw_metrics: {WATCHDOG_PATH} unreadable: {e}")
    hhmm = time.strftime("%H:%M")
    row1 = f"wd:{n_sig} {'OK' if n_sig == 0 else 'SIG'} {hhmm}"
    return [row0[:23], row1[:23]]


def push_rows(rows: list[str], server: str, device: str,
              token: str | None) -> None:
    with NatsConnection(server, token=token, timeout_s=8) as nc:
        for i, text in enumerate(rows):
            reply = nc.request(
                f"{device}.tool_exec",
                json.dumps({"tool": "display_print", "row": i, "text": text}),
            )
            if not (isinstance(reply, dict) and reply.get("ok")):
                raise SystemExit(
                    f"claw_metrics: display_print row {i} refused: {reply!r:.120}")


def main() -> int:
    env = _load_claw_env()
    server = env.get("MINI_DUDEAI_NATS_SERVER")
    device = env.get("MINI_DUDEAI_CLAW_DEVICE")
    if not server or not device:
        raise SystemExit("claw_metrics: claw env missing NATS server / device")
    rows = build_rows()
    try:
        push_rows(rows, server, device, env.get("MINI_DUDEAI_NATS_TOKEN") or None)
    except NatsError as e:
        raise SystemExit(f"claw_metrics: NATS push failed: {e}")
    print(f"claw_metrics: pushed {rows!r} to {device}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
