#!/usr/bin/env python3
"""mf.5 / mf.4 soak watch — daily evidence collector + window-end verdict.

DECLARED SOAK EXIT CRITERIA (window 2026-06-10 .. 2026-06-23 inclusive,
declared 2026-06-09 after the mf.5 fleet-roll; final verdict renders on the
first run on/after 2026-06-24):

  PASS requires, across every rnsd host, for the whole window:
    C1  self-heal: every rnsd exit-75 (the mf.5 host-loss escalation) is
        followed by a systemd restart in the journal — an exit-75 with no
        restart means the box sat host-less (regression to the #69 class).
    C2  no unexplained exit-75: an exit-75 day must carry a namespace-
        collision witness (the rns_namespace_collision probe edge in that
        box's mini history). exit-75 with no collision edge = the mf.5
        listener-absent check fired falsely -> review, soak FAILs.
    C3  clean stops (mf.4 criterion): zero rnsd stop-timeout SIGKILLs
        (journal "status=9/KILL" / stop-sigterm timeout) in the window.
    C4  canary green: zero gateway_rt_canary FAIL verdicts on moc
        (CONCERN = echo-peer/return-path, noted but not soak-fatal).
    C5  no blind days: every host observable every day (an unreachable
        journal is surfaced as CONCERN the day it happens, never absorbed
        — unobservable is not healthy). >=2 blind days for one host FAILs.

  PASS consequence: the retire-decision gate OPENS for the mf.3 detach
  bound + the 15s stop-cap drop-in (operator decision, not automatic —
  the drop-in header still says DO NOT RETIRE until amended).
  FAIL consequence: file the defect, fix, restart the window.

Daily run (cron, the federator box): collects per-host journal evidence since the
last run, appends one JSON line to evidence.jsonl, and leaves a
cron_verdict `mf5_soak_watch` OK/CONCERN/FAIL — a criterion violation
pages the DAY it happens, not on day 14. On/after 2026-06-24 it also
renders the final `mf5_soak_verdict` PASS/FAIL exactly once.

Honest failure modes: ssh/journal failures are recorded per-host as
unobservable (CONCERN), never as zero events; the verdict line is the
witness; state (last-run cursor, final-rendered marker) lives in the
state dir, and a missing/corrupt state file degrades to a 25h lookback,
never to silence.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

WINDOW_START = "2026-06-10"
WINDOW_END = "2026-06-23"          # inclusive
FINAL_FROM = "2026-06-24"          # first day the final verdict may render

# Every rnsd host in the soak. Local box (the federator) is "local".
HOSTS = ["local", "moc", "moc1", "moc2", "moc3", "moc5", "meshanchor-server"]
CANARY_HOST = "moc"

SSH_TIMEOUT_S = 15
JOURNAL_FALLBACK_LOOKBACK_H = 25

# Journal signatures (systemd-emitted lines — rnsd's OWN log lines go to
# its logfile, not the journal, so they are deliberately not relied on).
SIG_EXIT75 = "status=75"
SIG_STARTED = "Started rnsd.service"
SIG_STOPKILL = "status=9/KILL"
SIG_STOPTIMEOUT = "stop-sigterm' timed out"
# Collision witness in mini history (per-box ~/mini_dudeai_history.jsonl).
SIG_COLLISION = "namespace_collision"


@dataclass
class HostDay:
    host: str
    observable: bool = True
    error: str = ""
    exit75: int = 0
    restarts: int = 0
    stop_kills: int = 0
    collision_edges: int = 0


@dataclass
class DayRecord:
    date: str
    since: str
    hosts: List[Dict] = field(default_factory=list)
    canary_ok: int = 0
    canary_concern: int = 0
    canary_fail: int = 0
    canary_observable: bool = True


# ---------------------------------------------------------------- shell


def _run(cmd: List[str], timeout: int = SSH_TIMEOUT_S) -> Optional[str]:
    """Run a command; None on any failure (callers record unobservable)."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if proc.returncode != 0 and not proc.stdout:
        return None
    return proc.stdout


def _host_cmd(host: str, remote: str) -> List[str]:
    if host == "local":
        return ["bash", "-c", remote]
    return ["ssh", "-o", f"ConnectTimeout={SSH_TIMEOUT_S}",
            "-o", "BatchMode=yes", host, remote]


# ---------------------------------------------------------------- collect


def collect_host(host: str, since_iso: str) -> HostDay:
    hd = HostDay(host=host)

    journal = _run(_host_cmd(
        host,
        f"journalctl -u rnsd --since '{since_iso}' --no-pager -o cat 2>&1"))
    if journal is None:
        hd.observable = False
        hd.error = "journal unreadable / host unreachable"
        return hd
    for line in journal.splitlines():
        if SIG_EXIT75 in line:
            hd.exit75 += 1
        if SIG_STARTED in line:
            hd.restarts += 1
        if SIG_STOPKILL in line or SIG_STOPTIMEOUT in line:
            hd.stop_kills += 1

    # Collision witness — only consulted when exit75 fired (C2). A missing
    # history file reads as 0 edges, which can only make the verdict MORE
    # alarming (CONCERN review), never less.
    if hd.exit75 > 0:
        hist = _run(_host_cmd(
            host,
            f"grep -c '{SIG_COLLISION}' ~/mini_dudeai_history.jsonl"
            " 2>/dev/null || echo 0"))
        try:
            hd.collision_edges = int((hist or "0").strip().splitlines()[-1])
        except ValueError:
            hd.collision_edges = 0
    return hd


def collect_canary(since_iso: str) -> Dict:
    """Count gateway_rt_canary verdicts on moc since the cursor."""
    out = _run(_host_cmd(
        CANARY_HOST,
        "grep ' gateway_rt_canary ' ~/cron_verdicts.log 2>/dev/null"
        " || true"))
    if out is None:
        return {"observable": False, "ok": 0, "concern": 0, "fail": 0}
    ok = concern = fail = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0] < since_iso:
            continue
        status = parts[2]
        if status == "OK":
            ok += 1
        elif status == "CONCERN":
            concern += 1
        else:
            fail += 1
    return {"observable": True, "ok": ok, "concern": concern, "fail": fail}


# ---------------------------------------------------------------- verdicts


def classify_day(rec: DayRecord) -> tuple:
    """Map one day's evidence to (status, message). FAIL-fast on a
    criterion violation; CONCERN on blindness or review-needed."""
    problems: List[str] = []
    concerns: List[str] = []
    for h in rec.hosts:
        name = h["host"]
        if not h["observable"]:
            concerns.append(f"{name} unobservable (C5)")
            continue
        if h["stop_kills"]:
            problems.append(f"{name} {h['stop_kills']} stop-kill (C3)")
        if h["exit75"]:
            if h["restarts"] < 1:
                problems.append(f"{name} exit75 without restart (C1)")
            elif h["collision_edges"] < 1:
                concerns.append(
                    f"{name} exit75 without collision witness (C2 review)")
            else:
                concerns.append(
                    f"{name} {h['exit75']} self-healed inversion(s)")
    if not rec.canary_observable:
        concerns.append("canary verdicts unobservable (C5)")
    if rec.canary_fail:
        problems.append(f"canary {rec.canary_fail} FAIL (C4)")

    summary = (f"day evidence: {len(rec.hosts)} hosts,"
               f" canary {rec.canary_ok}OK/{rec.canary_concern}C/"
               f"{rec.canary_fail}F")
    if problems:
        return "FAIL", "; ".join(problems) + f" — {summary}"
    if concerns:
        return "CONCERN", "; ".join(concerns) + f" — {summary}"
    return "OK", summary


def render_final(evidence: List[Dict]) -> tuple:
    """Aggregate the whole window into the final PASS/FAIL verdict."""
    in_window = [r for r in evidence
                 if WINDOW_START <= r["date"] <= WINDOW_END]
    days = {r["date"] for r in in_window}
    expected = _window_days()
    missing = sorted(set(expected) - days)

    total = {"exit75": 0, "no_restart": 0, "no_witness": 0,
             "stop_kills": 0, "canary_fail": 0}
    blind: Dict[str, int] = {}
    for r in in_window:
        for h in r["hosts"]:
            if not h["observable"]:
                blind[h["host"]] = blind.get(h["host"], 0) + 1
                continue
            total["exit75"] += h["exit75"]
            total["stop_kills"] += h["stop_kills"]
            if h["exit75"] and h["restarts"] < 1:
                total["no_restart"] += 1
            if h["exit75"] and h["collision_edges"] < 1:
                total["no_witness"] += 1
        total["canary_fail"] += r.get("canary_fail", 0)

    fails: List[str] = []
    if missing:
        fails.append(f"{len(missing)} day(s) with no evidence: "
                     + ",".join(missing[:4]))
    if total["no_restart"]:
        fails.append(f"C1: {total['no_restart']} exit75 without restart")
    if total["no_witness"]:
        fails.append(f"C2: {total['no_witness']} exit75 without witness")
    if total["stop_kills"]:
        fails.append(f"C3: {total['stop_kills']} stop-kill(s)")
    if total["canary_fail"]:
        fails.append(f"C4: {total['canary_fail']} canary FAIL(s)")
    over_blind = {h: n for h, n in blind.items() if n >= 2}
    if over_blind:
        fails.append(f"C5: blind days {over_blind}")

    stats = (f"{len(in_window)} day-records, {total['exit75']} self-healed"
             f" exit75 total, 0 manual interventions required")
    if fails:
        return "FAIL", "; ".join(fails)
    return "OK", (f"PASS — all criteria held over {WINDOW_START}.."
                  f"{WINDOW_END}: {stats}. Retire-decision gate OPEN"
                  f" (mf.3 bound + 15s stop-cap — operator decision).")


def _window_days() -> List[str]:
    start = datetime.strptime(WINDOW_START, "%Y-%m-%d")
    end = datetime.strptime(WINDOW_END, "%Y-%m-%d")
    out = []
    d = start
    while d <= end:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------- state


def state_dir() -> Path:
    base = os.environ.get("MF5_SOAK_STATE_DIR")
    if base:
        return Path(base)
    # Cron runs as the operator user, never sudo — expanduser is the
    # correct resolver here (MF001's get_real_user_home lives in src/,
    # which scripts/ does not import).
    xdg = os.environ.get(
        "XDG_STATE_HOME",
        os.path.join(os.path.expanduser("~"), ".local", "state"))
    return Path(xdg) / "meshforge" / "mf5_soak"


def load_cursor(sdir: Path, now: datetime) -> str:
    cursor_file = sdir / "cursor"
    try:
        raw = cursor_file.read_text().strip()
        datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return raw
    except Exception:
        fallback = now - timedelta(hours=JOURNAL_FALLBACK_LOOKBACK_H)
        return fallback.strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    now = datetime.now(timezone.utc)
    sdir = state_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    since = load_cursor(sdir, now)

    rec = DayRecord(date=now.strftime("%Y-%m-%d"), since=since)
    for host in HOSTS:
        rec.hosts.append(asdict(collect_host(host, since)))
    canary = collect_canary(since.replace(" ", "T"))
    rec.canary_observable = canary["observable"]
    rec.canary_ok = canary["ok"]
    rec.canary_concern = canary["concern"]
    rec.canary_fail = canary["fail"]

    with open(sdir / "evidence.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(rec)) + "\n")
    (sdir / "cursor").write_text(now.strftime("%Y-%m-%d %H:%M:%S"))

    status, message = classify_day(rec)
    print(f"DAILY {status} {message}")

    # Window-end final verdict — renders exactly once.
    final_marker = sdir / "final_rendered"
    if rec.date >= FINAL_FROM and not final_marker.exists():
        evidence = []
        for line in (sdir / "evidence.jsonl").read_text().splitlines():
            try:
                evidence.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        fstatus, fmessage = render_final(evidence)
        print(f"FINAL {fstatus} {fmessage}")
        final_marker.write_text(rec.date)

    return 0 if status == "OK" else (2 if status == "CONCERN" else 1)


if __name__ == "__main__":
    sys.exit(main())
