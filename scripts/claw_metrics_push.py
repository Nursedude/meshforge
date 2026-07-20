#!/usr/bin/env python3
"""Paint fleet metrics onto the dude-claw's OLED (display_print over NATS).

Runs on the claw-brain box from the operator crontab, wired to the
cron-verdict regime:

    */5 * * * * cd /opt/meshforge && PYTHONPATH=src python3 scripts/claw_metrics_push.py >/dev/null 2>&1; /opt/meshforge/scripts/cron_verdict.sh claw_metrics $?

Rows (23-char budget, SSD1306 metric rows 0-1):
    row 0:  mesh:<directory total> fed:<reachable>/<peers>
    row 1:  wd:<signal count> <OK|SIG> <HH:MM>

Brain-tier glyph (firmware 0.4.0+dudeclaw.15 `display_tier` tool): F/L/R =
highest cognition tier a live probe PROVED this run (see _probe_tier). The
firmware decays the glyph to SOLO on its own clock when these pushes stop —
so this script never needs to push a "down" state; silence IS the signal.

Honesty: any unreadable source or failed push exits NONZERO so the verdict
line says FAIL and the cron_verdict_stale probe pages — never paint a row we
couldn't actually compute (the firmware adds its own "(old)" marker when the
pusher stops updating). Operator values (device name, NATS server, tier SLO
URL) come from the claw env file, not this script (MF014).
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mini_dudeai._util import atomic_write_json, fetch_json  # noqa: E402
from mini_dudeai.chat_compiler import probe_ollama  # noqa: E402
from mini_dudeai.claw_telemetry import (  # noqa: E402
    CLAW_TICK_BASENAME,
    build_tick,
    compute_brain_tier,
    secondary_tick_basename,
)
from mini_dudeai.nats_client import NatsConnection, NatsError  # noqa: E402
from mini_dudeai.presets.standalone import CLAW_STATE_BASENAME  # noqa: E402
from utils.paths import get_real_user_home  # noqa: E402

WATCHDOG_PATH = "/var/lib/meshforge/watchdog.json"
STATUS_URL = "http://localhost:5000/api/status"
# Last-tick telemetry capture (display only) read by /api/status.claw + the
# /fleet rollup + kilo's claw adapter. Basename owned by claw_telemetry
# (the tick-shape owner); operator-home path (MF001) so writer and readers
# resolve the same file — kilo test-pins its path formula to _tick_path().


# get_real_user_home, NOT expanduser: the tick writer below resolves the
# operator home, and this path anchors _tick_basename_for's primary-vs-
# secondary realpath comparison — two different "home"s in one identity
# decision would fork the primary tick under sudo (MF001 class).
DEFAULT_ENV_PATH = str(get_real_user_home() / ".config" / "meshforge"
                       / "mini_dudeai_claw.env")


def _tick_path(basename: str = CLAW_TICK_BASENAME) -> str:
    return str(get_real_user_home() / basename)


def _tick_basename_for(env_path: "str | None", device: str) -> str:
    """Which tick file this pusher instance owns (multi-claw, W5.1).

    The instance running on the DEFAULT env file is the primary claw and
    keeps the legacy CLAW_TICK_BASENAME (the single-claw display surfaces
    read exactly that file). Any --env instance writes the per-device
    secondary basename — compared by realpath so `--env <default>` cannot
    accidentally fork the primary's tick into a second file.
    """
    if env_path is None or \
            os.path.realpath(env_path) == os.path.realpath(DEFAULT_ENV_PATH):
        return CLAW_TICK_BASENAME
    basename = secondary_tick_basename(device)
    if basename != f"claw_last_tick.{device}.json":
        # The fold ('dudeclaw.02' → 'dudeclaw-02') means two DISTINCT
        # device names could silently share one tick file, alternating
        # overwrites — refuse loud at the writer, where the operator can
        # fix the env, instead of flapping a healthy claw toward DARK.
        raise SystemExit(
            f"claw_metrics: device name {device!r} contains characters "
            f"that fold in the tick filename ({basename}) — rename the "
            f"device to [A-Za-z0-9_-]+ in its env file")
    return basename


def _load_claw_env(path: "str | None" = None) -> dict:
    """KEY=VAL lines from the claw env file (same file the daemon loads).
    ``path`` selects an alternate instance env (multi-claw, one file per
    device); a missing file is a hard exit either way — never a silent
    empty env that would page about the wrong device."""
    path = path or DEFAULT_ENV_PATH
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
    # peer health field is `ok` (map_federation's FederationPeerStatus) — a
    # wrong key here defaults healthy peers to False and paints "fed:0/N",
    # the exact degraded-state-as-valid-value class this row exists to expose
    reachable = sum(1 for p in peers
                    if p.get("ok") and not p.get("in_backoff"))
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


def _probe_tier(env: dict) -> "tuple[str | None, str]":
    """Probe the cognition ladder and return ``(tier, note)``.

    ``MINI_DUDEAI_TIER_SLO_URL`` (the /fleet/slo of the box that runs the
    frontier cadence cron) is the feature switch: unset means we never LOOKED
    at the frontier, and pushing L/R without looking would claim more than we
    know — so unset disables the feed entirely (tier None, no push; the glass
    decays to SOLO). Configured-but-unreachable IS evidence: the frontier
    brain can't be confirmed from this segment, so the ladder falls through
    to the local probes.
    """
    slo_url = env.get("MINI_DUDEAI_TIER_SLO_URL")
    if not slo_url:
        return None, "tier feed disabled (MINI_DUDEAI_TIER_SLO_URL unset)"

    verdict_jobs = []
    slo, err = fetch_json(slo_url, timeout=10)
    if not err and isinstance(slo, dict):
        # Type-guard every nested level: `or {}` only covers None, and a
        # peer whose /fleet/slo ever ships a list here would AttributeError
        # this whole push into a FALSE claw-liveness page.
        sched = slo.get("schedules")
        verdicts = sched.get("verdicts") if isinstance(sched, dict) else None
        if isinstance(verdicts, dict) and verdicts.get("available"):
            jobs = verdicts.get("jobs")
            verdict_jobs = jobs if isinstance(jobs, list) else []

    ollama_ok = False
    ollama_url = env.get("MINI_DUDEAI_OLLAMA_URL")
    if ollama_url:
        # THE shared reachability probe (chat_compiler.probe_ollama) — same
        # check the TUI runs; 6 s here (cross-segment pinhole, not localhost).
        ollama_ok, _detail = probe_ollama(ollama_url, timeout_s=6)

    # R-tier evidence: the claw-mini daemon's state file. The WRITER anchors
    # on MINI_DUDEAI_HOME via _util.resolve_home, and the daemon's user unit
    # sees ONLY the claw env file (EnvironmentFile=) — so this reader reads
    # the SAME source and nothing else. Deliberately NOT os.environ: a
    # MINI_DUDEAI_HOME exported in the crontab/profile is an env the daemon
    # never sees, and honoring it here would point the reader at a state
    # file the writer never writes (tier R unprovable, glyph decays to SOLO
    # while the rule brain is healthy).
    claw_home = env.get("MINI_DUDEAI_HOME")
    state_dir = (os.path.expanduser(claw_home) if claw_home
                 else str(get_real_user_home()))
    rules_age_s = None
    try:
        rules_age_s = time.time() - os.path.getmtime(
            os.path.join(state_dir, CLAW_STATE_BASENAME))
    except OSError:
        pass  # no state file -> rules tier unprovable (compute handles None)

    return compute_brain_tier(verdict_jobs, ollama_ok, rules_age_s)


def _push_tier(nc: NatsConnection, device: str, tier: str):
    """Push the brain-tier glyph. Returns an error string on a real refusal,
    None on ok. A firmware that predates the tool (pre-0.4.0+dudeclaw.15)
    warns instead of paging — MF pulls may land before a claw reflash, and
    that ordering must not page the operator about a tool the glass never
    had (the glyph simply stays absent there)."""
    reply = nc.request(
        f"{device}.tool_exec",
        json.dumps({"tool": "display_tier", "tier": tier}),
    )
    if isinstance(reply, dict) and reply.get("ok"):
        return None
    err_txt = str((reply or {}).get("error", reply) if isinstance(reply, dict)
                  else reply)
    # Case-insensitive: the current fork emits "Error: unknown tool '...'"
    # (lowercase, pinned by tests), but this branch's whole job is surviving
    # OTHER firmware vintages — a recased/reworded refusal must not page.
    if "unknown tool" in err_txt.lower():
        print(f"claw_metrics: WARN firmware lacks display_tier "
              f"(pre-dudeclaw.15?): {err_txt[:120]}", file=sys.stderr)
        return None
    return f"display_tier refused: {err_txt[:120]}"


def _request_tool(nc: NatsConnection, device: str, tool: str):
    """One claw tool_exec; a transport failure becomes an honest error reply
    (build_tick records it) rather than aborting the capture."""
    try:
        return nc.request(f"{device}.tool_exec", json.dumps({"tool": tool}))
    except NatsError as e:
        return {"ok": False, "error": str(e)[:160]}


def _capture_tick(nc: NatsConnection, device: str, host: str,
                  now: float) -> dict:
    """Pull device_info + ble_stats + battery for the /api/status.claw block.

    Battery joined the capture 2026-07-19: a battery-powered claw drained to
    2.41 V and died dark for 17 h with the pack voltage recorded NOWHERE the
    fleet could see it — the only trace was an ad-hoc CSV from a transient
    unit. Voltage is a first-class fleet metric now, per device, so
    probe_claw_battery_low can warn BEFORE the node dies instead of the death
    surfacing as a failing cron.

    Best-effort: a half that fails is None + an error in the tick, never a
    fabricated value (build_tick enforces the honest-failure contract). A claw
    with no battery gauge simply reports None — an accessory it lacks does not
    make the device unhealthy."""
    di = _request_tool(nc, device, "device_info")
    bs = _request_tool(nc, device, "ble_stats")
    bat = _request_tool(nc, device, "battery_read")
    # lora_stats joined 2026-07-19 (row 9): the claw's OVER-THE-AIR witness —
    # a separate radio on separate silicon reporting what it actually heard on
    # the channel. No box self-report can corroborate the RF leg; this can.
    lora = _request_tool(nc, device, "lora_stats")
    return build_tick(now, host, device, di, bs, battery_reply=bat,
                      lora_reply=lora)


def _push_rows(nc: NatsConnection, rows: list[str], device: str):
    """Paint OLED rows. Returns an error string on refusal, else None."""
    for i, text in enumerate(rows):
        reply = nc.request(
            f"{device}.tool_exec",
            json.dumps({"tool": "display_print", "row": i, "text": text}),
        )
        if not (isinstance(reply, dict) and reply.get("ok")):
            return f"display_print row {i} refused: {reply!r:.120}"
    return None


def main(argv: "list[str] | None" = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="claw_metrics_push",
        description="Paint fleet metrics onto a dude-claw OLED + persist "
                    "its telemetry tick.")
    ap.add_argument("--env", default=None, metavar="PATH",
                    help="alternate claw env file (multi-claw: one env + "
                         "cron line per device; a non-default env writes "
                         "claw_last_tick.<device>.json so instances never "
                         "clobber each other's tick)")
    args = ap.parse_args(argv)
    env = _load_claw_env(args.env)
    server = env.get("MINI_DUDEAI_NATS_SERVER")
    device = env.get("MINI_DUDEAI_CLAW_DEVICE")
    if not server or not device:
        raise SystemExit("claw_metrics: claw env missing NATS server / device")
    rows = build_rows()
    tier, tier_note = _probe_tier(env)
    host = socket.gethostname()
    now = time.time()
    token = env.get("MINI_DUDEAI_NATS_TOKEN") or None

    # Default to an unreachable tick so a total NATS failure persists an honest
    # "claw didn't answer" record (the reader shows claw_unreachable) rather
    # than letting the last good tick silently age into stale.
    tick = build_tick(now, host, device, None, None)
    paint_err: str | None = None
    try:
        with NatsConnection(server, token=token, timeout_s=8) as nc:
            tick = _capture_tick(nc, device, host, now)
            paint_err = _push_rows(nc, rows, device)
            if tier and not paint_err:
                paint_err = _push_tier(nc, device, tier)
    except NatsError as e:
        paint_err = f"NATS connect/push failed: {e}"
    # The tier is a claim about the BRAIN, not the claw — record it even on
    # an unreachable tick so /api/status.claw can surface it later.
    tick["brain_tier"] = tier

    # Persist the capture for /api/status display (best-effort; a write failure
    # must NOT mask a paint failure, which is the claw-liveness page).
    try:
        atomic_write_json(_tick_path(_tick_basename_for(args.env, device)),
                          tick)
    except OSError as e:
        print(f"claw_metrics: WARN tick write failed: {e}", file=sys.stderr)

    if paint_err:
        raise SystemExit(f"claw_metrics: {paint_err}")
    print(f"claw_metrics: pushed {rows!r} tier={tier or '-'} ({tier_note}) "
          f"to {device}; tick ok={tick['ok']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
