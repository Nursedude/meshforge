#!/usr/bin/env python3
"""host_probe_check — out-of-band liveness collector (Leg C, 2026-06-17).

Polls the dude-claw's ``host_probe`` tool over NATS for each configured target
and writes a verdict file (``~/host_probe_state.json``) that the watchdog's
``probe_host_frozen`` reads. The claw sits on the watched box's OWN subnet, so
it distinguishes a wedged-userspace freeze (kernel/IP stack answers but the app
port serves no banner) from an unreachable host — the swap-thrash class the
box's own self-petted HW watchdog can't catch.

This is the out-of-band collector half: the NATS call lives HERE, not in the
sandboxed watchdog — mirroring fleet_offline_check.sh's role for
``fleet_box_unreachable``. Runs ONLY where a config file is present, which
self-gates it to the claw's brain box (so the watchdog probe is INERT
everywhere else — no verdict file). Cron it with ``cron_verdict.sh`` so a dead
collector is caught by ``cron_verdict_stale``.

Config (operator-specific values live HERE, never in repo source — MF014):

    ~/.config/meshforge/host_probe_targets.json
    {
      "nats": "localhost:4222",
      "claw_device": "dudeclaw-01",
      "timeout_s": 8,
      "targets": [
        {"name": "bot-32", "host": "10.0.0.5", "app_port": 22, "closed_port": 9}
      ]
    }

``app_port`` MUST be a service that emits an unsolicited banner on connect
(sshd :22 sends ``SSH-2.0-...``); a no-banner service like HTTP would read as a
false HOST_FROZEN. Exit 0 when a verdict file was written (even if a target is
HOST_FROZEN — that is a successful collection); non-zero only if the collector
itself could not run (no/unreadable config, or it could not write the file). A
target the claw could not be reached for is written verdict UNKNOWN, exit 0 —
the witness is blind, which is honest, not a false OK or false FROZEN.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time

# Make src/ importable however we're invoked (cron may not set PYTHONPATH).
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from utils.paths import get_real_user_home  # noqa: E402

CONFIG_REL = os.path.join(".config", "meshforge", "host_probe_targets.json")
STATE_BASENAME = "host_probe_state.json"
DEFAULT_NATS = "localhost:4222"
DEFAULT_DEVICE = "dudeclaw-01"
DEFAULT_TIMEOUT_S = 8.0


def _parse_probe_result(result_line: str) -> dict:
    """Parse the claw's host_probe result string into fields. Missing fields
    stay None — we never invent a value the radio did not report."""
    fields: dict = {"ip_alive": None, "app_port": None, "app_state": None,
                    "banner": None, "kstack": None, "rtt_ms": None}
    m = re.search(r"ip_alive=(\d+)", result_line)
    if m:
        fields["ip_alive"] = int(m.group(1))
    m = re.search(r"app(\d+)=(\w+)", result_line)
    if m:
        fields["app_port"] = int(m.group(1))
        fields["app_state"] = m.group(2)
    m = re.search(r"banner=(\d+)B", result_line)
    if m:
        fields["banner"] = int(m.group(1))
    m = re.search(r"kstack=(\d+)", result_line)
    if m:
        fields["kstack"] = int(m.group(1))
    m = re.search(r"rtt_ms=(-?\d+)", result_line)
    if m:
        fields["rtt_ms"] = int(m.group(1))
    return fields


def _verdict(fields: dict, collector_ok: bool) -> str:
    """Map probe fields to a verdict. UNKNOWN when the witness itself was blind
    (collector couldn't reach the claw, or the line didn't parse) — lost
    visibility is NOT read as OK."""
    if not collector_ok or fields.get("ip_alive") is None:
        return "UNKNOWN"
    if fields["ip_alive"] == 0:
        return "UNREACHABLE"
    # app port completed the TCP handshake (kernel accept) but served no banner
    # = userspace not serving while the kernel/NIC is alive: the freeze class.
    if fields.get("app_state") == "open" and (fields.get("banner") or 0) == 0:
        return "HOST_FROZEN"
    return "OK"


def _probe_one(req, server: str, device: str, target: dict,
               timeout_s: float) -> dict:
    """Probe a single target via the claw. Never raises — a transport failure
    becomes a UNKNOWN verdict with the error recorded."""
    name = str(target.get("name") or target.get("host") or "?")
    host = str(target.get("host") or "")
    out = {"name": name, "host": host, "verdict": "UNKNOWN",
           "ip_alive": None, "app_state": None, "banner": None,
           "kstack": None, "rtt_ms": None, "raw": "", "error": None}
    if not host:
        out["error"] = "no host in config"
        return out
    args = {"tool": "host_probe", "host": host}
    if target.get("app_port") is not None:
        args["app_port"] = int(target["app_port"])
    if target.get("closed_port") is not None:
        args["closed_port"] = int(target["closed_port"])
    try:
        reply = req(server, f"{device}.tool_exec", json.dumps(args),
                    timeout_s=timeout_s)
        if isinstance(reply, (bytes, bytearray)):
            reply = reply.decode("utf-8", "replace")
        # nats_client.request() may hand back an already-parsed dict or a raw
        # JSON string — accept either, never assume.
        doc = json.loads(reply) if isinstance(reply, str) else reply
        if not isinstance(doc, dict):
            out["error"] = f"unexpected reply type: {type(reply).__name__}"
            return out
        result = str(doc.get("result") or "")
        out["raw"] = result
        if not doc.get("ok"):
            out["error"] = str(doc.get("error") or "claw returned ok=false")
            return out                       # UNKNOWN — claw errored
        fields = _parse_probe_result(result)
        out.update({k: fields.get(k) for k in
                    ("ip_alive", "app_state", "banner", "kstack", "rtt_ms")})
        out["verdict"] = _verdict(fields, collector_ok=True)
    except Exception as e:  # transport / parse failure → blind, not healthy
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def main() -> int:
    home = get_real_user_home()
    config_path = os.path.join(str(home), CONFIG_REL)
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        # No config here → not the claw's brain box. Nothing to do; the watchdog
        # probe self-guards INERT (no verdict file). Clean no-op.
        return 0
    except (OSError, ValueError) as e:
        sys.stderr.write(f"host_probe_check: unreadable config {config_path}: {e}\n")
        return 1

    server = str(cfg.get("nats") or DEFAULT_NATS)
    device = str(cfg.get("claw_device") or DEFAULT_DEVICE)
    token = cfg.get("nats_token") or None
    timeout_s = float(cfg.get("timeout_s") or DEFAULT_TIMEOUT_S)
    targets = cfg.get("targets") or []
    if not isinstance(targets, list):
        sys.stderr.write("host_probe_check: 'targets' must be a list\n")
        return 1

    from mini_dudeai.nats_client import request as _request

    def req(srv, subj, payload, *, timeout_s):
        return _request(srv, subj, payload, token=token, timeout_s=timeout_s)

    results = [_probe_one(req, server, device, t, timeout_s)
               for t in targets if isinstance(t, dict)]
    collector_ok = all(r["error"] is None for r in results) if results else True

    state = {"ts": time.time(), "collector_ok": collector_ok,
             "device": device, "targets": results}
    state_path = os.path.join(str(home), STATE_BASENAME)
    try:
        fd, tmp = tempfile.mkstemp(dir=str(home), prefix=".host_probe_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, state_path)
    except OSError as e:
        sys.stderr.write(f"host_probe_check: cannot write {state_path}: {e}\n")
        return 1

    # One-line summary for the cron log; exit 0 = collection ran (a HOST_FROZEN
    # target is still a successful collection — the watchdog renders the alert).
    summary = ", ".join(f"{r['name']}={r['verdict']}" for r in results) or "no targets"
    sys.stdout.write(f"host_probe_check: {summary}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
