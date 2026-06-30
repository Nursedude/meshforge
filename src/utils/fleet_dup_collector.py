"""Cross-box dedup rollup collector (dedup/identity arc STEP 4c-iii).

Gathers each active gateway's published ``content_id_view`` state file
(STEP 4c-i), JOINs them with :func:`utils.fleet_dup_view.compute_fleet_dup_view`,
and writes the fleet rollup to a state file the ``/fleet/dups`` endpoint
serves. Runs in the OPERATOR's context (cron — the established fleet cadence
with ``cron_verdict`` observability), NOT in the map daemon: ssh-collection
belongs where the operator's ssh keys + config live, and a gateway-only box
(moc3) serves no HTTP, so ssh-cat is the only reachable transport for it
(the moc3 hardware constraint — the operator's chosen design).

Transport honesty (honest_failure_modes #2 — absence != evidence of
absence): the JOIN sees ONLY boxes whose view file is present. A box that
is ssh-unreachable or has no file is recorded in the rollup's ``collector``
meta (with the ssh/cat exit code so "down gateway" (rc 255) reads
differently from "not a gateway" (rc 1)) and is NOT fed to the JOIN as a
phantom covered box. The JOIN's own ``indeterminate`` gate (<2 contributing
gateways) then keeps the verdict honest when coverage is thin.

MEASURE-ONLY. Lead-repo home; the MeshAnchor twin mirrors this shape.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.fleet_dup_view import compute_fleet_dup_view
from utils.paths import atomic_write_text, get_real_user_home

# Remote path of the per-box published view — matches the producer's
# content_id_view_state_path() (co-located with the DB in ~/.local/share so
# it lands inside the gateway sandbox's ReadWritePaths; see that function).
_REMOTE_VIEW_EXPR = '"$HOME/.local/share/meshforge/content_id_view.json"'

DEFAULT_SSH_TIMEOUT_S = 8

# Reader contract: (host) -> (rc, text). rc 0 = file present (text = JSON);
# rc 1 = reachable but no file (not a gateway); rc 255 / other = ssh
# failure. Injectable so the JOIN integration is testable without ssh.
Reader = Callable[[str], Tuple[int, str]]


def fleet_hosts_file() -> Optional[Path]:
    """Locate the fleet host list (same precedence as lab_traffic_rollup.sh)."""
    override = os.environ.get("FLEET_HOSTS")
    candidates = []
    if override:
        candidates.append(Path(override))
    home = get_real_user_home()
    candidates.append(home / ".config" / "meshforge" / "fleet_hosts")
    candidates.append(Path("/etc/meshforge/fleet_hosts"))
    for c in candidates:
        if c.is_file():
            return c
    return None


def parse_fleet_hosts(text: str) -> List[str]:
    """One host alias per line; ``#`` comments and blanks ignored."""
    hosts: List[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            hosts.append(line)
    return hosts


def rollup_state_path() -> Path:
    """``$XDG_STATE_HOME/meshforge/fleet_dup_view.json`` (operator-owned)."""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else get_real_user_home() / ".local" / "state"
    return base / "meshforge" / "fleet_dup_view.json"


def ssh_cat_reader(host: str, *, timeout_s: int = DEFAULT_SSH_TIMEOUT_S) -> Tuple[int, str]:
    """Real reader: ssh-cat the remote view file. Never raises — a timeout
    or transport error maps to a non-zero rc the collector records as a
    coverage note (not a phantom covered box)."""
    try:
        proc = subprocess.run(
            ["ssh", "-n", "-o", "BatchMode=yes",
             "-o", f"ConnectTimeout={int(timeout_s)}", host,
             f"cat {_REMOTE_VIEW_EXPR}"],
            capture_output=True, text=True, timeout=timeout_s + 7,
        )
        return proc.returncode, proc.stdout
    except subprocess.TimeoutExpired:
        return 255, ""
    except OSError as exc:  # ssh binary missing / spawn failure
        return 255, f"spawn error: {exc}"


def local_read_reader(path: Optional[Path] = None) -> Tuple[int, str]:
    """Read the local box's published view directly (no ssh to self).

    Resolves the SAME canonical path the producer writes
    (``delivery_counters.content_id_view_state_path``) so the read- and
    write-sides can't drift (honest_failure_modes #5)."""
    if path is None:
        from gateway.delivery_counters import content_id_view_state_path
        path = content_id_view_state_path()
    try:
        return 0, path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 1, ""
    except OSError as exc:
        return 255, f"read error: {exc}"


def collect_box_views(
    hosts: List[str],
    *,
    local_host: Optional[str] = None,
    reader: Optional[Reader] = None,
    local_reader: Optional[Callable[[], Tuple[int, str]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Collect per-box view items + a coverage-note list.

    Returns ``(box_views, collector_notes)``:
      - ``box_views`` — items for boxes whose file is PRESENT (rc 0): a
        ``{host, state}`` for valid JSON, or ``{host, error}`` for garbage
        (a real gateway with a broken file → surfaced, not hidden).
      - ``collector_notes`` — ``{host, rc, reason}`` for boxes skipped
        (rc 1 = no file / not a gateway; rc 255 = ssh-unreachable). These
        are NOT JOIN inputs — they can't masquerade as covered — but they
        ARE recorded so a down gateway is visible (rc 255 != rc 1).
    """
    reader = reader or ssh_cat_reader
    local_reader = local_reader or local_read_reader
    box_views: List[Dict[str, Any]] = []
    notes: List[Dict[str, Any]] = []

    for host in hosts:
        if local_host is not None and host == local_host:
            rc, text = local_reader()
        else:
            rc, text = reader(host)

        if rc == 0:
            try:
                state = json.loads(text)
            except (ValueError, TypeError):
                box_views.append(
                    {"host": host, "error": "malformed state file (bad JSON)"})
                continue
            box_views.append({"host": host, "state": state})
        elif rc == 1:
            notes.append({"host": host, "rc": rc, "reason": "no view file (not a gateway)"})
        else:
            notes.append({"host": host, "rc": rc, "reason": "ssh unreachable"})

    return box_views, notes


def build_fleet_dup_rollup(
    *,
    hosts: Optional[List[str]] = None,
    local_host: Optional[str] = None,
    reader: Optional[Reader] = None,
    local_reader: Optional[Callable[[], Tuple[int, str]]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Collect every gateway's view, JOIN them, and attach collector meta."""
    if local_host is None:
        local_host = socket.gethostname()
    if hosts is None:
        hf = fleet_hosts_file()
        hosts = parse_fleet_hosts(hf.read_text(encoding="utf-8")) if hf else []
        # Include the local box (the manager may itself be a gateway); the
        # fleet_hosts file excludes self by convention.
        if local_host not in hosts:
            hosts = [local_host] + hosts

    box_views, notes = collect_box_views(
        hosts, local_host=local_host, reader=reader, local_reader=local_reader)
    rollup = compute_fleet_dup_view(box_views, now=now)
    rollup["collector"] = {
        "host": local_host,
        "attempted": list(hosts),
        "covered": [b["host"] for b in box_views if "state" in b],
        "notes": notes,
    }
    return rollup


def write_rollup(rollup: Dict[str, Any], *, path: Optional[Path] = None) -> bool:
    """Atomic write of the rollup state file. Never raises."""
    try:
        target = Path(path) if path is not None else rollup_state_path()
        atomic_write_text(target, json.dumps(rollup, separators=(",", ":")))
        return True
    except Exception:
        return False


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for the operator's cron cadence.

    Exit 0 = rollup written. Exit 2 = could not write (the cron_verdict
    failure signal). The rollup CONTENT (indeterminate / dups found) is
    measure-only and never affects the exit code — a healthy collector
    that finds dups still exits 0; the probe (STEP 5) owns alerting."""
    import argparse

    parser = argparse.ArgumentParser(description="Fleet dedup rollup collector")
    parser.add_argument("--out", default=None, help="rollup output path")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    rollup = build_fleet_dup_rollup()
    ok = write_rollup(rollup, path=Path(args.out) if args.out else None)
    if not args.quiet:
        print(
            f"fleet_dup_view: status={rollup.get('status')} "
            f"dups={rollup.get('fleet_duplicate_pairs')} "
            f"covered={rollup.get('covered_hosts')} "
            f"written={ok}"
        )
    return 0 if ok else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
