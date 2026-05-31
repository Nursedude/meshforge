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

import datetime
import json
import os
import subprocess
import sys
import time

from .brief import DEFAULT_STALE_S, _age

#: ssh-cat the state file. BatchMode so a missing key fails fast instead of
#: hanging on a password prompt; ConnectTimeout bounds an unreachable host.
DEFAULT_SSH_TIMEOUT_S = 10.0
_STATE_BASENAME = "mini_dudeai_state.json"


def resolve_fleet_hosts(env: dict | None = None) -> list[str]:
    """Fleet remote-host list, same resolution order as fleet_sync.sh:
    $MESHFORGE_FLEET_HOSTS → ~/.config/meshforge/fleet_hosts → /etc/meshforge/fleet_hosts.
    One host per line; '#' comments and blanks ignored. [] if no list exists."""
    env = os.environ if env is None else env
    candidates = []
    if env.get("MESHFORGE_FLEET_HOSTS"):
        candidates.append(env["MESHFORGE_FLEET_HOSTS"])
    home = env.get("HOME") or os.path.expanduser("~")
    candidates.append(os.path.join(home, ".config", "meshforge", "fleet_hosts"))
    candidates.append("/etc/meshforge/fleet_hosts")
    for path in candidates:
        try:
            with open(path) as f:
                hosts = []
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if line:
                        hosts.append(line)
                return hosts
        except OSError:
            continue
    return []


def parse_state_posture(host: str, state: dict | None, now_ts: float,
                        stale_s: float = DEFAULT_STALE_S,
                        self_box: bool = False) -> dict:
    """Pure: distil a box's state dict into a compact posture record.

    status ∈ {fresh, stale, no_state}. ``no_state`` = ssh worked but the box has
    no/empty mini state (never ticked here). Active rules carried for the pane.
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
    return {
        "host": host,
        "self_box": self_box,
        "status": status,
        "last_tick_ts": last_tick,
        "age": _age(now_ts, last_tick) if last_tick else "?",
        "rule_count": state.get("rule_count", len(rules)),
        "src_errors": state.get("error_count", 0),
        "active": active,
        "state_host": state.get("host"),
    }


def _default_ssh_runner(host: str, timeout_s: float) -> tuple[int, str, str]:
    """ssh <host> cat <state>. Returns (returncode, stdout, stderr). Never raises;
    a timeout/transport failure becomes returncode 255 with a stderr note."""
    cmd = [
        "ssh", "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={int(timeout_s)}",
        host, "cat", _STATE_BASENAME,
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 5)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 255, "", "ssh timed out"
    except OSError as e:
        return 255, "", f"ssh exec failed: {e}"


def collect_remote(host: str, now_ts: float, timeout_s: float = DEFAULT_SSH_TIMEOUT_S,
                   stale_s: float = DEFAULT_STALE_S, runner=None) -> dict:
    """ssh-cat a remote box's mini state and distil its posture.

    runner(host, timeout_s) -> (rc, stdout, stderr) is injectable for tests.
    ssh failure → status 'unreachable'. ssh OK but empty/invalid state → 'no_mini'.
    """
    runner = runner or _default_ssh_runner
    rc, out, err = runner(host, timeout_s)
    # ssh returns 255 ONLY for its own transport failure (refused/timeout/auth);
    # any other non-zero is the remote command's exit (e.g. `cat` rc=1 when the
    # box simply has no state file → it runs no mini, NOT unreachable).
    if rc == 255:
        return {"host": host, "self_box": False, "status": "unreachable",
                "error": (err or "").strip()[:160] or "ssh failed"}
    out = (out or "").strip()
    if rc != 0 or not out:
        return {"host": host, "self_box": False, "status": "no_mini",
                "error": (err or "").strip()[:160] or "no mini_dudeai_state.json"}
    try:
        state = json.loads(out)
    except ValueError:
        return {"host": host, "self_box": False, "status": "no_mini",
                "error": "state unparseable"}
    return parse_state_posture(host, state, now_ts, stale_s)


def collect_local(now_ts: float, state_path: str | None = None,
                  stale_s: float = DEFAULT_STALE_S) -> dict | None:
    """Read the manager box's own state file directly (it's excluded from
    fleet_hosts). Returns None if there is no local mini state at all."""
    if state_path is None:
        state_path = os.path.join(os.path.expanduser("~"), _STATE_BASENAME)
    try:
        with open(state_path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    label = (state.get("host") if isinstance(state, dict) else None) or "self"
    return parse_state_posture(label, state, now_ts, stale_s, self_box=True)


_BANNER = {
    "fresh": "🟢",
    "stale": "🔴",
    "no_state": "⚪",
    "no_mini": "—",
    "unreachable": "❌",
}
#: problems first, healthy last; then alpha by host within a bucket.
_ORDER = {"unreachable": 0, "stale": 1, "no_state": 2, "no_mini": 3, "fresh": 4}


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
        if p["status"] in ("unreachable", "no_mini"):
            lines.append(f"{banner} **{p['host']}**{tag} — {p['status']}"
                         + (f": {p['error']}" if p.get("error") else ""))
            continue
        if p["status"] == "no_state":
            lines.append(f"{banner} **{p['host']}**{tag} — never ticked (no state)")
            continue
        head = (f"{banner} **{p['host']}**{tag} — {p['status']} · "
                f"last tick {p['age']} ago · {p['rule_count']} rules · "
                f"src_errors={p['src_errors']}")
        if p["status"] == "stale":
            head += " · ⚠️ daemon may be down"
        lines.append(head)
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


def main(argv: list[str] | None = None) -> int:
    now_ts = time.time()
    postures = collect_fleet(now_ts)
    if not postures:
        print("# mini-dudeai fleet posture\n\n"
              "No local mini state and no fleet_hosts list found "
              "(set $MESHFORGE_FLEET_HOSTS or create ~/.config/meshforge/fleet_hosts).")
        return 0
    sys.stdout.write(build_rollup(postures, now_ts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
