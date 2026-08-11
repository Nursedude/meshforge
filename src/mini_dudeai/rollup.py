"""Fleet mini-dudeai posture rollup — one pane, every box.

Each box's mini daemon writes its own ``~/mini_dudeai_state.json`` (the brief is
the human render; the state file is the SSOT). This module ssh-fans to the fleet
— resolving the host list the SAME way ``scripts/fleet_sync.sh`` does, so no
operator hostnames live in the repo (MF014) — reads each box's state, and renders
a single posture pane.

The honesty contract mirrors ``warmstart``: each box's freshness is re-derived
NOW from its ``last_tick_ts``, so a box whose daemon died shows 🔴 STALE rather
than a confident lie. Applied across the fleet instead of one box.

Read-only and on-demand:

    python3 -m mini_dudeai.rollup

The local/manager box is excluded from ``fleet_hosts`` (it can't ssh itself), so
it is read directly from the local state file and folded into the same pane.
A host that answers ssh but runs no mini (e.g. a MeshAnchor-only box) is reported
``no mini``, not an error. A host that won't answer ssh is ``unreachable``.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import subprocess
import sys
import time

from ._util import read_json, resolve_home
from .claw_telemetry import CLAW_TICK_BASENAME, SECONDARY_TICK_GLOB

from .brief import (
    DEFAULT_STALE_S,
    ESCALATION_WINDOW_S,
    _age,
    _split_escalations_by_activity,
    recent_escalations,
)

#: ssh-cat the state file. BatchMode so a missing key fails fast instead of
#: hanging on a password prompt; ConnectTimeout bounds an unreachable host.
DEFAULT_SSH_TIMEOUT_S = 10.0
_STATE_BASENAME = "mini_dudeai_state.json"
_HISTORY_BASENAME = "mini_dudeai_history.jsonl"
#: separates state from history in the single deep-pull ssh round trip. MUST be
#: free of shell metacharacters — it is echoed by the REMOTE shell, so '<<<'/'>>>'
#: would be parsed as here-string/redirection and the command would emit nothing.
_DEEP_SENTINEL = "__MINI_DUDEAI_DEEP_SENTINEL__"
#: cap on fires shown in the merged deep feed (escalations are never capped).
_DEEP_FIRES_CAP = 20
#: dude-claw last-tick capture, cat'd in the same breadth round trip (most
#: boxes have no claw -> the second cat is empty and no card renders). Same
#: shell-safe charset constraint as _DEEP_SENTINEL. Aliased from the
#: tick-shape owner (imported at top) — a basename rename must move every
#: reader at once.
_CLAW_BASENAME = CLAW_TICK_BASENAME
#: every ADDITIONAL claw on the same brain box (dudeclaw-02, …). Same
#: writer-owned constant; the two-dot shape excludes the single-dot primary.
_CLAW_GLOB = SECONDARY_TICK_GLOB
_CLAW_SENTINEL = "__MINI_DUDEAI_CLAW_SENTINEL__"
#: 3x the */5-min claw_metrics capture cadence (matches _read_claw_state_block).
CLAW_STALE_S = 900.0


def resolve_fleet_hosts(env: dict | None = None) -> list[str]:
    """Fleet remote-host list — delegates to ``utils.fleet_hosts``, THE
    resolver (this WAS one of ~13 independent copies of the chain; converged
    2026-07-29). Kept as a thin wrapper so daemon/fleet_truth_collector
    callers and the env-injecting tests keep their seam. Its authoritative-
    override rule (a SET but unresolvable $MESHFORGE_FLEET_HOSTS yields []
    rather than falling through to the box's real config) originated here
    and is now the shared behavior. [] if no list exists."""
    from utils.fleet_hosts import resolve_fleet_hosts as _resolve
    return _resolve(env=env)


def parse_claw_posture(claw: dict | None, now_ts: float,
                       claw_stale_s: float = CLAW_STALE_S) -> dict | None:
    """Pure: distil a box's claw_last_tick.json into a compact card summary.

    Returns None when the box carries no claw tick (the common case). status ∈
    {fresh, stale, unreachable, unknown}: ``stale`` = the capture cron stopped,
    ``unreachable`` = the claw didn't answer at last capture (tick ok False).
    Both are degraded — neither renders as healthy numbers (honest_failure_modes).
    """
    if not isinstance(claw, dict) or not claw:
        return None
    ts = claw.get("captured_at")
    age_s = (now_ts - float(ts)) if isinstance(ts, (int, float)) else None
    if age_s is None:
        status = "unknown"
    elif age_s > claw_stale_s:
        status = "stale"
    elif not (claw.get("reachable")
              if isinstance(claw.get("reachable"), bool) else claw.get("ok")):
        # Read the EXPLICIT reachability fact when the capture provides it
        # (since 2026-07-19), falling back to ok for older ticks. Before that
        # split, ok folded in accessory halves, so a perfectly reachable claw
        # that simply has no BLE scanner rendered here as "unreachable" — a
        # display stating the opposite of the truth.
        status = "unreachable"
    else:
        status = "fresh"
    di = claw.get("device_info") or {}
    ble = claw.get("ble") or {}
    return {
        "status": status,
        "device": claw.get("device"),
        "age": _age(now_ts, ts) if ts else "?",
        "uptime_s": di.get("uptime_s"),
        "heap_free_bytes": di.get("heap_free_bytes"),
        "wifi_rssi_dbm": di.get("wifi_rssi_dbm"),
        "ble_adv_age_s": ble.get("adv_age_s"),
        "ble_advs": ble.get("advs"),
    }


def parse_state_posture(host: str, state: dict | None, now_ts: float,
                        stale_s: float = DEFAULT_STALE_S,
                        self_box: bool = False,
                        claw: dict | None = None,
                        claws: list | None = None) -> dict:
    """Pure: distil a box's state dict into a compact posture record.

    status ∈ {fresh, stale, no_state}. ``no_state`` = ssh worked but the box has
    no/empty mini state (never ticked here). Active rules carried for the pane.
    ``claw`` (optional) is the box's parsed claw_last_tick.json; its compact
    summary rides along so a claw card can render under the box. ``claws``
    (optional) is the FULL tick list for a box hosting more than one dude-claw
    — every claw gets its own card (07-24 audit: reading only the primary made
    a dead dudeclaw-02 invisible in this pane). ``claw`` stays as the primary
    for back-compat with callers that pass/read a single tick.
    """
    state = state if isinstance(state, dict) else {}
    last_tick = state.get("last_tick_ts")
    rules = state.get("rules") or {}
    active = [
        {"rule_id": rs.get("rule_id"), "subject": rs.get("subject"),
         "detail": str(rs.get("last_detail", ""))[:100]}
        for rs in rules.values()
        if isinstance(rs, dict) and rs.get("currently_active")
    ]
    if not last_tick:
        status = "no_state"
    elif (now_ts - float(last_tick)) > stale_s:
        status = "stale"
    else:
        status = "fresh"
    docs = [d for d in (claws if claws is not None else [claw]) if isinstance(d, dict)]
    cards = [c for c in (parse_claw_posture(d, now_ts) for d in docs) if c]
    return {
        "host": host,
        "self_box": self_box,
        "status": status,
        "last_tick_ts": last_tick,
        "age": _age(now_ts, last_tick) if last_tick else "?",
        "rule_count": state.get("rule_count", len(rules)),
        "src_errors": state.get("error_count", 0),
        "source_errors": state.get("source_errors") or [],
        # None (not 0) when absent — a pre-upgrade daemon's state simply
        # doesn't carry the count, and unknown must never read as zero.
        "pending_deltas": state.get("pending_deltas"),
        "active": active,
        "state_host": state.get("host"),
        "claw": cards[0] if cards else None,
        "claws": cards,
    }


def _remote_breadth_cmd() -> str:
    """The remote shell one-liner for the breadth round trip. Pure + module
    level so a test can run it through a real shell instead of asserting on a
    string (a command only ssh ever executes is a command nothing verifies).

    ``for f in <primary> <glob>`` with a ``-f`` guard: an unmatched glob stays
    literal and is skipped, so a claw-less box emits state + one sentinel
    exactly as before. Trailing ``true`` keeps the compound rc off the last
    ``[ -f ]`` test, which would otherwise report 1 on a claw-less box."""
    return (f"cat {_STATE_BASENAME} 2>/dev/null; "
            f"echo '{_CLAW_SENTINEL}'; "
            f"for f in {_CLAW_BASENAME} {_CLAW_GLOB}; do "
            f"[ -f \"$f\" ] && {{ cat \"$f\" 2>/dev/null; echo; "
            f"echo '{_CLAW_SENTINEL}'; }}; done; true")


def _default_ssh_runner(host: str, timeout_s: float) -> tuple[int, str, str]:
    """ssh <host> 'cat state; SENTINEL; cat claw' — one round trip. Returns
    (returncode, stdout, stderr). Never raises; a timeout/transport failure
    becomes returncode 255 with a stderr note.

    The remote `cat`s swallow their own errors (2>/dev/null) so the compound rc
    is NOT a reliable mini/no-mini signal — collect_remote keys no-mini off
    EMPTY state content instead (255 still uniquely flags ssh transport
    failure, which a cat never produces). The claw cat is the second half: most
    boxes have no claw file, so it is simply empty and renders no card.

    EVERY claw tick is cat'd, each terminated by the same sentinel (07-24
    audit): a brain box hosting dudeclaw-02 writes a suffixed sibling that the
    old single `cat` never read, so a dead second claw was invisible in this
    pane. The glob is the writer's own SECONDARY_TICK_GLOB — one constant, one
    shape. An unmatched glob stays literal and is skipped by the -f test."""
    cmd = ["ssh", "-o", "BatchMode=yes",
           "-o", f"ConnectTimeout={int(timeout_s)}", host,
           _remote_breadth_cmd()]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 5)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 255, "", "ssh timed out"
    except OSError as e:
        return 255, "", f"ssh exec failed: {e}"


def _split_claw_payload(stdout: str) -> tuple[str, list[dict]]:
    """Split the breadth ssh payload (state + SENTINEL + claw [+ SENTINEL +
    claw…]) into (state_text, claw_dicts). No sentinel (a legacy/injected
    single-doc runner) → the whole thing is state and NO claws — backward
    compatible, as is the single-claw payload the old remote emitted.

    A torn/unparseable tick is DROPPED from the list rather than aborting the
    split: one bad file must not hide the box's other claws. It leaves a gap in
    the pane, never a healthy-looking substitute (honest_failure_modes)."""
    parts = (stdout or "").split(_CLAW_SENTINEL)
    claws: list[dict] = []
    for chunk in parts[1:]:
        cp = chunk.strip()
        if not cp:
            continue
        try:
            loaded = json.loads(cp)
        except ValueError:
            continue
        if isinstance(loaded, dict):
            claws.append(loaded)
    return parts[0].strip(), claws


def collect_remote(host: str, now_ts: float, timeout_s: float = DEFAULT_SSH_TIMEOUT_S,
                   stale_s: float = DEFAULT_STALE_S, runner=None) -> dict:
    """ssh-cat a remote box's mini state (+ claw tick) and distil its posture.

    runner(host, timeout_s) -> (rc, stdout, stderr) is injectable for tests.
    ssh transport failure (rc 255) → 'unreachable'. ssh OK but empty/invalid
    state → 'no_mini'. A claw tick after the sentinel rides into the posture.
    """
    runner = runner or _default_ssh_runner
    rc, out, err = runner(host, timeout_s)
    # rc 255 is ssh's OWN transport failure (refused/timeout/auth); a remote
    # `cat` never produces it. The compound command's rc is otherwise the last
    # cat's exit, so it can't distinguish mini from no-mini — empty STATE
    # content does that below.
    if rc == 255:
        return {"host": host, "self_box": False, "status": "unreachable",
                "error": (err or "").strip()[:160] or "ssh failed"}
    state_text, claws = _split_claw_payload(out)
    # A no-mini box MAY still host a claw — build_rollup has always rendered a
    # claw card on this branch, but the collector never filled one in (a reader
    # with no writer, honest_failure_modes #4). Carry the ticks through.
    claw_cards = [c for c in (parse_claw_posture(d, now_ts) for d in claws) if c]
    if not state_text:
        return {"host": host, "self_box": False, "status": "no_mini",
                "error": (err or "").strip()[:160] or "no mini_dudeai_state.json",
                "claw": claw_cards[0] if claw_cards else None,
                "claws": claw_cards}
    try:
        state = json.loads(state_text)
    except ValueError:
        return {"host": host, "self_box": False, "status": "no_mini",
                "error": "state unparseable",
                "claw": claw_cards[0] if claw_cards else None,
                "claws": claw_cards}
    return parse_state_posture(host, state, now_ts, stale_s, claws=claws)


def collect_local(now_ts: float, state_path: str | None = None,
                  stale_s: float = DEFAULT_STALE_S,
                  claw_path: str | None = None) -> dict | None:
    """Read the manager box's own state file directly (it's excluded from
    fleet_hosts). Returns None if there is no local mini state at all.
    Also folds in the local claw tick (sibling claw_last_tick.json) if present."""
    home = resolve_home()
    if state_path is None:
        state_path = os.path.join(home, _STATE_BASENAME)
    claw_dir = os.path.dirname(state_path) or home
    if claw_path is None:
        claw_path = os.path.join(claw_dir, _CLAW_BASENAME)
    claw_docs = []
    primary_doc, _ = read_json(claw_path)
    if isinstance(primary_doc, dict):
        claw_docs.append(primary_doc)
    # Every ADDITIONAL claw on this box (dudeclaw-02, …) writes a suffixed
    # sibling; the primary-only read hid a dead second claw here (07-24 audit).
    for extra in sorted(glob.glob(os.path.join(claw_dir, _CLAW_GLOB))):
        if os.path.abspath(extra) == os.path.abspath(claw_path):
            continue
        doc, _ = read_json(extra)
        if isinstance(doc, dict):
            claw_docs.append(doc)
    state, err = read_json(state_path)
    if err == "not found":
        return None  # genuinely no mini here
    if err:
        # CORRUPT is not ABSENT: a truncated state file (the very failure
        # class the fleet pane exists to expose) used to render identically
        # to "box runs no mini" — silently. Surface it as a broken posture.
        posture = parse_state_posture("self", {}, now_ts, stale_s,
                                      self_box=True, claws=claw_docs)
        posture["error"] = f"state unreadable: {err}"
        return posture
    label = (state.get("host") if isinstance(state, dict) else None) or "self"
    return parse_state_posture(label, state, now_ts, stale_s,
                               self_box=True, claws=claw_docs)


_BANNER = {
    "fresh": "🟢",
    "stale": "🔴",
    "no_state": "⚪",
    "no_mini": "—",
    "unreachable": "❌",
}
#: problems first, healthy last; then alpha by host within a bucket.
_ORDER = {"unreachable": 0, "stale": 1, "no_state": 2, "no_mini": 3, "fresh": 4}

_CLAW_BANNER = {"fresh": "🟢", "stale": "🔴", "unreachable": "❌", "unknown": "⚪"}


def _fmt_uptime(s) -> str:
    if not isinstance(s, (int, float)):
        return "?"
    h = int(s // 3600)
    return f"{h}h" if h < 48 else f"{h // 24}d"


def _claw_line(c: dict) -> str:
    """Compact one-line claw card under its host. A stopped capture (stale) or
    a claw that didn't answer (unreachable) renders as such — never healthy-
    looking numbers from a dead capture (honest_failure_modes)."""
    banner = _CLAW_BANNER.get(c["status"], "?")
    dev = c.get("device") or "claw"
    if c["status"] == "stale":
        return (f"    · 🦞 {dev}: {banner} STALE — capture cron stopped "
                f"(last {c['age']} ago)")
    if c["status"] == "unreachable":
        return (f"    · 🦞 {dev}: {banner} UNREACHABLE — claw did not answer "
                f"at last capture ({c['age']} ago)")
    parts = []
    if c.get("uptime_s") is not None:
        parts.append(f"up {_fmt_uptime(c['uptime_s'])}")
    if c.get("heap_free_bytes") is not None:
        parts.append(f"heap {int(c['heap_free_bytes']) // 1024}k")
    if c.get("wifi_rssi_dbm") is not None:
        parts.append(f"wifi {c['wifi_rssi_dbm']}dBm")
    if c.get("ble_adv_age_s") is not None:
        parts.append(f"ble adv {c['ble_adv_age_s']}s")
    body = " · ".join(parts) if parts else "no fields"
    return f"    · 🦞 {dev}: {banner} {c['status']} · {body} · captured {c['age']} ago"


def _append_claw(lines: list[str], posture: dict | None) -> None:
    """Render a card for EVERY claw on the box (07-24 audit). Falls back to the
    single ``claw`` key so a posture built by an older caller still renders."""
    if not isinstance(posture, dict):
        return
    cards = posture.get("claws")
    if not isinstance(cards, list) or not cards:
        single = posture.get("claw")
        cards = [single] if single else []
    for c in cards:
        if c:
            lines.append(_claw_line(c))


def build_rollup(postures: list[dict], now_ts: float) -> str:
    """Pure: render the all-boxes posture pane. Problems sort to the top."""
    stamp = datetime.datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S")
    counts: dict[str, int] = {}
    for p in postures:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    summary = " · ".join(
        f"{_BANNER.get(s, '?')} {counts[s]} {s}"
        for s in ("fresh", "stale", "no_state", "no_mini", "unreachable")
        if counts.get(s)
    ) or "no boxes"

    lines = [
        f"# mini-dudeai fleet posture — {len(postures)} boxes",
        f"_rolled up {stamp} · per-box freshness re-derived now · {summary}_",
        "",
    ]
    ordered = sorted(postures, key=lambda p: (_ORDER.get(p["status"], 9), p["host"]))
    for p in ordered:
        banner = _BANNER.get(p["status"], "?")
        tag = " (self)" if p.get("self_box") else ""
        if p["status"] == "unreachable":
            # ssh transport failed → we never read the claw file either.
            lines.append(f"{banner} **{p['host']}**{tag} — {p['status']}"
                         + (f": {p['error']}" if p.get("error") else ""))
            continue
        if p["status"] == "no_mini":
            lines.append(f"{banner} **{p['host']}**{tag} — {p['status']}"
                         + (f": {p['error']}" if p.get("error") else ""))
            _append_claw(lines, p)  # a no-mini box may still run a claw
            continue
        if p["status"] == "no_state":
            lines.append(f"{banner} **{p['host']}**{tag} — never ticked (no state)")
            _append_claw(lines, p)
            continue
        head = (f"{banner} **{p['host']}**{tag} — {p['status']} · "
                f"last tick {p['age']} ago · {p['rule_count']} rules · "
                f"src_errors={p['src_errors']}")
        if p["src_errors"] and p.get("source_errors"):
            head += f" ({'; '.join(p['source_errors'])})"
        pd = p.get("pending_deltas")
        if isinstance(pd, int) and pd > 0:
            head += f" · 💭 {pd} delta(s) pending"
        if p["status"] == "stale":
            head += " · ⚠️ daemon may be down"
        lines.append(head)
        _append_claw(lines, p)
        for a in p.get("active", [])[:4]:
            lines.append(f"    · active: {a['rule_id']} · {a['subject']} · {a['detail']}")
    return "\n".join(lines) + "\n"


def collect_fleet(now_ts: float | None = None,
                  timeout_s: float = DEFAULT_SSH_TIMEOUT_S,
                  stale_s: float = DEFAULT_STALE_S,
                  runner=None, env: dict | None = None,
                  local_state_path: str | None = None) -> list[dict]:
    """Local box (direct read) + every remote in fleet_hosts (ssh). Ordered as
    [local, *remotes] before build_rollup re-sorts by status."""
    now_ts = time.time() if now_ts is None else now_ts
    postures: list[dict] = []
    local = collect_local(now_ts, local_state_path, stale_s)
    if local is not None:
        postures.append(local)
    for host in resolve_fleet_hosts(env):
        postures.append(collect_remote(host, now_ts, timeout_s, stale_s, runner))
    return postures


# === deep merge: escalations + fires across the fleet ============
# The breadth pane (above) is posture-only. --deep additionally pulls each box's
# history.jsonl (bounded at 1MB by rotation — weeks of forensic record, NOT a
# 24h file) and merges every box's escalations + edge_up fires into one
# box-tagged, time-sorted feed; build_box_deep filters fires to the window so
# the "(24h window)" header tells the truth.

def _parse_history_lines(text: str) -> list[dict]:
    """Parse JSONL history text into dicts; skip malformed lines, never raise."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _default_ssh_runner_deep(host: str, timeout_s: float) -> tuple[int, str, str]:
    """ssh <host> 'cat state; echo SENTINEL; cat history' — one round trip. The
    remote `cat`s swallow their own errors so rc reflects only ssh transport
    (255 = unreachable); empty content means the box runs no mini."""
    remote = (f"cat {_STATE_BASENAME} 2>/dev/null; "
              f"echo '{_DEEP_SENTINEL}'; "
              f"cat {_HISTORY_BASENAME} 2>/dev/null")
    cmd = ["ssh", "-o", "BatchMode=yes",
           "-o", f"ConnectTimeout={int(timeout_s)}", host, remote]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 5)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 255, "", "ssh timed out"
    except OSError as e:
        return 255, "", f"ssh exec failed: {e}"


def _split_deep_payload(stdout: str) -> tuple[dict, list[dict]]:
    """Split the deep ssh payload (state + SENTINEL + history) into (state, history)."""
    state_part, _, hist_part = (stdout or "").partition(_DEEP_SENTINEL)
    state: dict = {}
    sp = state_part.strip()
    if sp:
        try:
            loaded = json.loads(sp)
            state = loaded if isinstance(loaded, dict) else {}
        except ValueError:
            state = {}
    return state, _parse_history_lines(hist_part)


def build_box_deep(host: str, state: dict, history: list[dict], now_ts: float,
                   stale_s: float = DEFAULT_STALE_S,
                   window_s: float = ESCALATION_WINDOW_S,
                   self_box: bool = False) -> dict:
    """Pure: posture + this box's escalations (with ts) + edge_up fires, box-tagged.
    Reuses brief.recent_escalations (with_ts) so the deep feed and the per-box
    brief never disagree on what's an escalation."""
    posture = parse_state_posture(host, state, now_ts, stale_s, self_box)
    stale = posture["status"] == "stale"
    # Mark each escalation with whether its (rule, subject) is STILL active on
    # that box, using the box's own rules state — the same SSOT the per-box
    # brief splits on. Without this the deep feed listed cleared conditions
    # under "look here first" forever, which is the 2026-06-03 parity_drift
    # defect the brief was fixed for and this sibling was not (found
    # 2026-08-11: a fix applied to one instance is not applied to the class).
    # Conservative in the same direction: unknown pairs stay ACTIVE, so a live
    # escalation is never hidden by state drift.
    _rules = state.get("rules") or {}
    posture["escalations"] = [
        {"ts": ts, "box": host, "stale": stale, "esc": esc,
         "resolved": bool(_split_escalations_by_activity([esc], _rules)[1])}
        for ts, esc in recent_escalations(history, now_ts, window_s, with_ts=True)
    ]
    # Filter fires to the window: history.jsonl holds WEEKS (1MB rotation cap),
    # and unfiltered fires made the deep feed's "(24h window)" header a lie —
    # week-old transitions presented as recent fleet activity.
    cutoff = now_ts - window_s
    posture["fires"] = [
        {"ts": h.get("ts"), "box": host, "stale": stale,
         "iso": str(h.get("iso", ""))[:19], "rule_id": h.get("rule_id"),
         "subject": h.get("subject"), "detail": str(h.get("detail", ""))[:90]}
        for h in history
        if h.get("transition") == "edge_up" and float(h.get("ts") or 0) >= cutoff
    ]
    return posture


def collect_remote_deep(host: str, now_ts: float,
                        timeout_s: float = DEFAULT_SSH_TIMEOUT_S,
                        stale_s: float = DEFAULT_STALE_S,
                        window_s: float = ESCALATION_WINDOW_S, runner=None) -> dict:
    """ssh-pull a remote box's state+history and distil its deep record.
    runner(host, timeout_s) -> (rc, stdout, stderr) injectable for tests."""
    runner = runner or _default_ssh_runner_deep
    rc, out, err = runner(host, timeout_s)
    if rc == 255:
        return {"host": host, "self_box": False, "status": "unreachable",
                "error": (err or "").strip()[:160] or "ssh failed",
                "escalations": [], "fires": []}
    state, history = _split_deep_payload(out)
    if not state and not history:
        return {"host": host, "self_box": False, "status": "no_mini",
                "error": "no mini state/history", "escalations": [], "fires": []}
    return build_box_deep(host, state, history, now_ts, stale_s, window_s)


def collect_local_deep(now_ts: float, state_path: str | None = None,
                       history_path: str | None = None,
                       stale_s: float = DEFAULT_STALE_S,
                       window_s: float = ESCALATION_WINDOW_S) -> dict | None:
    """Read the manager box's own state + history directly. None if no state."""
    home = resolve_home()
    state_path = state_path or os.path.join(home, _STATE_BASENAME)
    history_path = history_path or os.path.join(home, _HISTORY_BASENAME)
    try:
        with open(state_path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        with open(history_path) as f:
            history = _parse_history_lines(f.read())
    except OSError:
        history = []
    label = (state.get("host") if isinstance(state, dict) else None) or "self"
    return build_box_deep(label, state, history, now_ts, stale_s, window_s, self_box=True)


def collect_fleet_deep(now_ts: float | None = None,
                       timeout_s: float = DEFAULT_SSH_TIMEOUT_S,
                       stale_s: float = DEFAULT_STALE_S,
                       window_s: float = ESCALATION_WINDOW_S,
                       runner=None, env: dict | None = None,
                       local_state_path: str | None = None,
                       local_history_path: str | None = None) -> list[dict]:
    """Local box (direct) + every remote in fleet_hosts (ssh), each with escalations+fires."""
    now_ts = time.time() if now_ts is None else now_ts
    results: list[dict] = []
    local = collect_local_deep(now_ts, local_state_path, local_history_path, stale_s, window_s)
    if local is not None:
        results.append(local)
    for host in resolve_fleet_hosts(env):
        results.append(collect_remote_deep(host, now_ts, timeout_s, stale_s, window_s, runner))
    return results


def build_deep_feed(results: list[dict], now_ts: float) -> str:
    """Pure: render the merged deep feed. Escalations first (uncapped, newest
    first, box-tagged), then recent fires (capped), then skipped boxes."""
    stamp = datetime.datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S")
    all_esc = [e for r in results for e in r.get("escalations", [])]
    all_fires = [f for r in results for f in r.get("fires", [])]
    all_esc.sort(key=lambda e: float(e.get("ts") or 0), reverse=True)
    all_fires.sort(key=lambda f: float(f.get("ts") or 0), reverse=True)
    contributing = [r for r in results if r["status"] in ("fresh", "stale")]
    skipped = [r for r in results if r["status"] not in ("fresh", "stale")]

    lines = [
        f"# mini-dudeai fleet deep feed — {len(contributing)} boxes reporting",
        f"_merged {stamp} · {len(all_esc)} escalations · {len(all_fires)} fires "
        f"(24h window, newest first)_",
        "",
        "## 🔎 Fleet escalations (look here first)",
    ]
    live_esc = [e for e in all_esc if not e.get("resolved")]
    done_esc = [e for e in all_esc if e.get("resolved")]
    if live_esc:
        for e in live_esc:
            esc = e["esc"]
            tag = " ⚠️stale" if e.get("stale") else ""
            note = f" — _{esc['note']}_" if esc.get("note") else ""
            lines.append(f"- `[{e['box']}]`{tag} {esc.get('rule')} · "
                         f"{esc.get('subject')} · {str(esc.get('detail', ''))[:120]}{note}")
    elif done_esc:
        lines.append("_None still active — see resolved below._")
    else:
        lines.append("_None in the window — no box is proposing an escalation._")

    # Cleared conditions get their own section and the past tense, for the same
    # reason the brief does it: the carried detail is the last ACTIVE text, so
    # rendered bare under "look here first" it reads as a live outage.
    if done_esc:
        lines.append("\n## ✅ Resolved in window (no longer active on that box)")
        lines.append("_Text below is each condition's last ACTIVE detail — "
                     "history, not current state._")
        for e in done_esc:
            esc = e["esc"]
            tag = " ⚠️stale" if e.get("stale") else ""
            lines.append(f"- `[{e['box']}]`{tag} {esc.get('rule')} · "
                         f"{esc.get('subject')} · was (last seen "
                         f"{_age(now_ts, e.get('ts'))} ago): "
                         f"{str(esc.get('detail', ''))[:120]}")

    lines.append("\n## Recent fleet fires")
    if all_fires:
        for f in all_fires[:_DEEP_FIRES_CAP]:
            tag = " ⚠️stale" if f.get("stale") else ""
            lines.append(f"- [{f['iso']}] `[{f['box']}]`{tag} {f['rule_id']} · "
                         f"{f['subject']} · {f['detail']}")
        if len(all_fires) > _DEEP_FIRES_CAP:
            lines.append(f"_… {len(all_fires) - _DEEP_FIRES_CAP} older fires not shown._")
    else:
        lines.append("_No edge_up fires in the window._")

    if skipped:
        lines.append("\n## Skipped (no deep data)")
        for r in skipped:
            lines.append(f"- **{r['host']}** — {r['status']}"
                         + (f": {r['error']}" if r.get("error") else ""))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="mini-dudeai-rollup",
        description="Fleet mini-dudeai posture (default, breadth) or merged "
                    "escalations+fires feed (--deep).",
    )
    p.add_argument("--deep", action="store_true",
                   help="Merge every box's escalations + fires into one feed "
                        "(pulls each box's history; on-demand, heavier than the "
                        "default posture pane).")
    args = p.parse_args(argv)
    now_ts = time.time()
    _no_data = ("No local mini state and no fleet_hosts list found "
                "(set $MESHFORGE_FLEET_HOSTS or create ~/.config/meshforge/fleet_hosts).")
    if args.deep:
        results = collect_fleet_deep(now_ts)
        if not results:
            print(f"# mini-dudeai fleet deep feed\n\n{_no_data}")
            return 0
        sys.stdout.write(build_deep_feed(results, now_ts))
        return 0
    postures = collect_fleet(now_ts)
    if not postures:
        print(f"# mini-dudeai fleet posture\n\n{_no_data}")
        return 0
    sys.stdout.write(build_rollup(postures, now_ts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
