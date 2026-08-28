"""virtual_fleet — an isolated N-node RNS fleet on one machine.

The cloud-dev living-lab tier (plan: .claude/plans/virtual_fleet_cloud_dev.md):
spin up a sandboxed fleet — per-node ``rnsd`` processes with unique
``instance_name``s, linked ONLY over 127.0.0.1 TCP — and drive it with the
SAME lab organs that drill the real fleet (lxmf_echo, lxmf_tracer). The
prototype topology is three nodes:

    transport   rnsd, TCPServerInterface :BP, enable_transport
    gw          rnsd, TCPClientInterface -> transport
    echo        rnsd, TCPClientInterface -> transport

ISOLATION BY CONSTRUCTION (the safety story, per the 2026-08-09 tx-guard
incident): no AutoInterface anywhere, no RF device, listeners on loopback
only, instance names namespaced ``vfleet-*`` so no client can attach to the
box's real shared instance by accident. ``status`` re-derives all of that
from the LIVE configs and sockets instead of trusting this docstring.

Client processes attach to a node the same way real fleet clients attach to
/etc/reticulum: by using the node's OWN configdir
(``MESHFORGE_LAB_RNS_CONFIGDIR`` hook in lxmf_echo/lxmf_tracer), so
shared-instance auth needs no extra plumbing.

Workdir defaults under ``~/.local/state`` — never /tmp (tmpfs = RAM on the
fleet; feedback_timed_out_command_keeps_running).

Usage:
    python3 -m lab.virtual_fleet up
    python3 -m lab.virtual_fleet status
    python3 -m lab.virtual_fleet smoke     # up if needed + echo/tracer round trip
    python3 -m lab.virtual_fleet down
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

RNSD_BIN = "/usr/local/bin/rnsd"
RNSTATUS_BIN = "/usr/local/bin/rnstatus"

NODES = ("transport", "gw", "echo")

READY_TIMEOUT_S = 30
SMOKE_TIMEOUT_S = 120


# ------------------------------------------------------------ config render

def render_node_config(name: str, base_port: int) -> str:
    """Pure function: the rnsd config for one virtual node.

    Kept pure and import-cheap so tests can pin the isolation invariants
    (loopback-only, no AutoInterface, unique names/ports) without running
    anything.
    """
    idx = NODES.index(name)
    shared_port = base_port + 11 + idx
    control_port = base_port + 21 + idx
    lines = [
        f"# virtual_fleet node '{name}' (auto-generated; safe to delete with the workdir)",
        "[reticulum]",
        f"  enable_transport = {'True' if name == 'transport' else 'False'}",
        "  share_instance = Yes",
        f"  shared_instance_port = {shared_port}",
        f"  instance_control_port = {control_port}",
        f"  instance_name = vfleet-{name}",
        "  panic_on_interface_error = No",
        "",
        "[logging]",
        "  loglevel = 4",
        "",
        "[interfaces]",
    ]
    if name == "transport":
        lines += [
            "  [[VFleet TCP Server]]",
            "    type = TCPServerInterface",
            "    interface_enabled = True",
            "    listen_ip = 127.0.0.1",
            f"    listen_port = {base_port}",
        ]
    else:
        lines += [
            "  [[VFleet Link To Transport]]",
            "    type = TCPClientInterface",
            "    interface_enabled = True",
            "    target_host = 127.0.0.1",
            f"    target_port = {base_port}",
        ]
    return "\n".join(lines) + "\n"


def config_violations(config_text: str) -> List[str]:
    """Isolation invariants a node config must satisfy. Empty list = clean."""
    out = []
    if "AutoInterface" in config_text:
        out.append("AutoInterface present — node could reach the real mesh")
    if "instance_name = vfleet-" not in config_text:
        out.append("instance_name not vfleet-namespaced — could collide "
                   "with the box's real shared instance")
    for line in config_text.splitlines():
        s = line.strip()
        if s.startswith("listen_ip") and "127.0.0.1" not in s:
            out.append(f"non-loopback listener: {s}")
        if s.startswith("target_host") and "127.0.0.1" not in s:
            out.append(f"non-loopback link target: {s}")
    return out


# ------------------------------------------------------------ process mgmt

def _node_dir(workdir: Path, name: str) -> Path:
    return workdir / name


def _pidfile(workdir: Path, name: str) -> Path:
    return _node_dir(workdir, name) / "rnsd.pid"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _read_pid(path: Path) -> Optional[int]:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _instance_socket_present(name: str) -> bool:
    """Abstract-namespace socket @rns/vfleet-<name> is being listened on."""
    try:
        proc = subprocess.run(
            ["ss", "-xl"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return f"@rns/vfleet-{name}" in proc.stdout


def up(workdir: Path, base_port: int) -> int:
    workdir.mkdir(parents=True, exist_ok=True)
    for name in NODES:
        nd = _node_dir(workdir, name)
        nd.mkdir(parents=True, exist_ok=True)
        (nd / "ids").mkdir(exist_ok=True)
        cfg = nd / "config"
        cfg.write_text(render_node_config(name, base_port))

        pid = _read_pid(_pidfile(workdir, name))
        if pid and _pid_alive(pid):
            logger.info("%s: rnsd already running (pid %d)", name, pid)
            continue

        log = open(nd / "rnsd.log", "ab")
        proc = subprocess.Popen(  # daemon under our management; reaped by down()
            [RNSD_BIN, "--config", str(nd), "-v"],
            stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _pidfile(workdir, name).write_text(str(proc.pid))
        logger.info("%s: rnsd spawned (pid %d)", name, proc.pid)

    deadline = time.monotonic() + READY_TIMEOUT_S
    pending = set(NODES)
    while pending and time.monotonic() < deadline:
        for name in list(pending):
            if _instance_socket_present(name):
                pending.discard(name)
        if pending:
            time.sleep(1)
    if pending:
        logger.error("nodes never claimed their instance socket: %s "
                     "(see rnsd.log in each node dir)", sorted(pending))
        return 1
    logger.info("all %d nodes up", len(NODES))
    return 0


def down(workdir: Path) -> int:
    rc = 0
    for name in NODES:
        for extra in ("echo.pid",):
            p = _read_pid(_node_dir(workdir, name) / extra)
            if p and _pid_alive(p):
                os.kill(p, signal.SIGTERM)
        pid = _read_pid(_pidfile(workdir, name))
        if not pid:
            continue
        if _pid_alive(pid):
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                if not _pid_alive(pid):
                    break
                time.sleep(0.25)
            if _pid_alive(pid):
                logger.warning("%s: rnsd %d ignored SIGTERM — SIGKILL", name, pid)
                os.kill(pid, signal.SIGKILL)
        _pidfile(workdir, name).unlink(missing_ok=True)
        logger.info("%s: down", name)
    return rc


def status(workdir: Path, base_port: int) -> int:
    """Re-derive fleet + ISOLATION state from live configs/sockets/pids."""
    worst = 0
    for name in NODES:
        nd = _node_dir(workdir, name)
        cfg = nd / "config"
        pid = _read_pid(_pidfile(workdir, name))
        alive = bool(pid and _pid_alive(pid))
        sock = _instance_socket_present(name)
        viols = config_violations(cfg.read_text()) if cfg.exists() else ["no config"]
        ok = alive and sock and not viols
        print(f"{name:10s} pid={pid or '-':<8} alive={alive} socket={sock} "
              f"isolation={'OK' if not viols else 'VIOLATION: ' + '; '.join(viols)}")
        if not ok:
            worst = 1
    return worst


# ------------------------------------------------------------ smoke

def _lab_env(node_dir: Path) -> Dict[str, str]:
    env = dict(os.environ)
    env["MESHFORGE_LAB_RNS_CONFIGDIR"] = str(node_dir)
    env["MESHFORGE_LAB_IDENTITY_DIR"] = str(node_dir / "ids")
    return env


def smoke(workdir: Path, base_port: int, repo_src: Path) -> int:
    """PING/ACK round trip across the virtual fabric — 'message arrives'.

    gw-node tracer -> transport -> echo-node responder -> ACK back.
    Exit 0 only when the tracer confirms the ACK.
    """
    if up(workdir, base_port) != 0:
        return 1
    if status(workdir, base_port) != 0:
        logger.error("smoke refused: fleet not clean (see status above)")
        return 1

    echo_nd = _node_dir(workdir, "echo")
    gw_nd = _node_dir(workdir, "gw")

    # 1. echo identity -> destination hash (no transport needed)
    proc = subprocess.run(
        [sys.executable, "-m", "lab.lxmf_echo", "--init"],
        capture_output=True, text=True, timeout=30,
        cwd=str(repo_src), env=_lab_env(echo_nd),
    )
    if proc.returncode != 0:
        logger.error("echo --init failed: %s", proc.stderr.strip())
        return 1
    # --init prints a ready-for-lab_peers "<name>=<hash>" line; keep the hash.
    echo_hash = proc.stdout.strip().splitlines()[-1].split("=")[-1].strip()
    logger.info("echo destination: %s", echo_hash)

    # 2. echo responder attached to the echo node
    echo_log = open(echo_nd / "echo.log", "ab")
    echo_proc = subprocess.Popen(  # managed daemon; killed in finally
        [sys.executable, "-m", "lab.lxmf_echo", "--announce-interval", "20"],
        stdout=echo_log, stderr=subprocess.STDOUT,
        cwd=str(repo_src), env=_lab_env(echo_nd),
        start_new_session=True,
    )
    (echo_nd / "echo.pid").write_text(str(echo_proc.pid))

    try:
        time.sleep(5)  # let the responder announce once
        peers = workdir / "lab_peers"
        peers.write_text(f"vecho={echo_hash}\n")

        env = _lab_env(gw_nd)
        env["XDG_STATE_HOME"] = str(workdir / "state")
        proc = subprocess.run(
            [sys.executable, "-m", "lab.lxmf_tracer",
             "--peers", str(peers), "--loglevel", "INFO"],
            capture_output=True, text=True, timeout=SMOKE_TIMEOUT_S,
            cwd=str(repo_src), env=env,
        )
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-6:])
        if proc.returncode == 0:
            print(f"SMOKE OK — PING/ACK round trip across the virtual fleet\n{tail}")
            return 0
        print(f"SMOKE FAIL rc={proc.returncode}\n{tail}")
        return 1
    finally:
        if _pid_alive(echo_proc.pid):
            os.kill(echo_proc.pid, signal.SIGTERM)


# ------------------------------------------------------------ cli

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("up", "down", "status", "smoke"))
    from utils.paths import get_real_user_home
    parser.add_argument(
        "--workdir", type=Path,
        default=Path(os.environ.get("XDG_STATE_HOME",
                                    str(get_real_user_home() / ".local" / "state")))
        / "meshforge" / "virtual_fleet",
    )
    parser.add_argument("--base-port", type=int, default=14200)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s vfleet %(levelname)s %(message)s")

    repo_src = Path(__file__).resolve().parent.parent
    if args.command == "up":
        return up(args.workdir, args.base_port)
    if args.command == "down":
        return down(args.workdir)
    if args.command == "status":
        return status(args.workdir, args.base_port)
    return smoke(args.workdir, args.base_port, repo_src)


if __name__ == "__main__":
    sys.exit(main())
