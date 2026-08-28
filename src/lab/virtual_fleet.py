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
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Binary resolution: env override -> PATH (CI installs the forks via pip,
# entry points land on PATH) -> the fleet's fork install location.
import shutil as _shutil

RNSD_BIN = (os.environ.get("MESHFORGE_RNSD_BIN")
            or _shutil.which("rnsd") or "/usr/local/bin/rnsd")
RNSTATUS_BIN = (os.environ.get("MESHFORGE_RNSTATUS_BIN")
                or _shutil.which("rnstatus") or "/usr/local/bin/rnstatus")

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
        # RNS creates <configdir>/storage lazily, but the gateway's sandbox
        # preflight probes it for existence+writability at startup.
        (nd / "storage").mkdir(exist_ok=True)
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
        for extra in ("echo.pid", "gateway.pid", "mesh_stub.pid"):
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


# ------------------------------------------------------------ gateway node

# A loopback port nothing listens on: the sandbox gateway's Meshtastic leg
# must fail fast and stay down — pointing it at the box's REAL meshtasticd
# would let the sandbox key a real radio (the 2026-08-09 class). The RNS
# leg is independent (proven by moc3 running 12 days radio-dead).
MESHTASTIC_BLACKHOLE_PORT_OFFSET = 199


def _gw_home(workdir: Path) -> Path:
    return _node_dir(workdir, "gw") / "home"


def _gateway_env(workdir: Path, base_port: int) -> Dict[str, str]:
    """Sandbox HOME so every get_real_user_home()-derived path (config,
    identity, lxmf storage, queue + counters DBs) lands under the workdir.
    SUDO_USER/LOGNAME are stripped — they outrank HOME in the resolver."""
    env = dict(os.environ)
    env["HOME"] = str(_gw_home(workdir))
    env.pop("SUDO_USER", None)
    env.pop("LOGNAME", None)
    env.pop("XDG_STATE_HOME", None)
    # Process-wide RNS resolution root (utils.paths.ReticulumPaths): RNS is
    # a per-process singleton and the gateway has MULTIPLE RNS clients
    # (bridge, node_tracker, boundary RPC) — without this, whichever inits
    # first attaches the whole process to the BOX's real instance (caught
    # live 2026-08-27: the sandbox gateway discovered the real fleet).
    env["MESHFORGE_RNS_CONFIGDIR"] = str(_node_dir(workdir, "gw"))
    # Sandbox websocket port — never the box's real UI port 5001.
    env["MESHFORGE_WS_PORT"] = str(base_port + 198)
    # HOME changed => python's user-site (where RNS/LXMF are pip --user
    # installed) would vanish from sys.path. Carry the REAL user's site dir.
    import site
    user_site = site.getusersitepackages()
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (env.get("PYTHONPATH", ""), user_site) if p)
    return env


def _write_gateway_config(workdir: Path, base_port: int) -> Path:
    home = _gw_home(workdir)
    cfg_dir = home / ".config" / "meshforge"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    # The gateway's startup sandbox preflight (#58/#60 class) requires all
    # three data buckets to EXIST, not just be creatable.
    (home / ".local" / "share" / "meshforge").mkdir(parents=True, exist_ok=True)
    (home / ".cache" / "meshforge").mkdir(parents=True, exist_ok=True)
    cfg = cfg_dir / "gateway.json"
    # Always (re)write: the sandbox config is generated, and a stale copy
    # from an older orchestrator version is exactly how config drift hides.
    import json
    cfg.write_text(json.dumps({
        "enabled": True,
        "rns_bridge_enabled": True,
        "rns": {
            "config_dir": str(_node_dir(workdir, "gw")),
            "gateway_name": "vfleet-gw",
            "announce_interval": 60,
        },
        "meshtastic": {
            "host": "127.0.0.1",
            "port": base_port + MESHTASTIC_BLACKHOLE_PORT_OFFSET,
        },
        # Empty channel NAME: the TX channel resolver otherwise queries
        # meshtasticd's channel list to map the name to an index — and that
        # query goes to a meshtastic CLI default, which on a radio box is
        # the REAL daemon (read-leak caught 2026-08-27 when the real
        # PhoneAPI was busy and the query's failure unmasked it). Numeric
        # channel 0 needs no resolution and no query.
        "mqtt_bridge": {"channel": ""},
    }, indent=2))
    return cfg


# Accept-and-stay-silent TCP stub standing in for meshtasticd: passes the
# gateway's startup port preflight, then behaves like the known
# present-but-deaf PhoneAPI wedge state (#17/#75) — a real fleet condition
# the gateway is proven to run degraded under. Never speaks protobuf,
# never touches a radio.
_MESH_STUB_CODE = (
    "import socket,sys\n"
    "s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
    "s.bind(('127.0.0.1',int(sys.argv[1]))); s.listen(4)\n"
    "held=[]\n"
    "while True:\n"
    "    c,_=s.accept(); held.append(c)\n"
)


def _start_mesh_stub(workdir: Path, base_port: int) -> None:
    gw_nd = _node_dir(workdir, "gw")
    pidfile = gw_nd / "mesh_stub.pid"
    pid = _read_pid(pidfile)
    if pid and _pid_alive(pid):
        return
    port = base_port + MESHTASTIC_BLACKHOLE_PORT_OFFSET
    proc = subprocess.Popen(  # managed daemon; reaped by down()
        [sys.executable, "-c", _MESH_STUB_CODE, str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pidfile.write_text(str(proc.pid))
    logger.info("meshtasticd stub listening on 127.0.0.1:%d (pid %d) — "
                "accepts, never answers", port, proc.pid)


def start_gateway(workdir: Path, base_port: int, repo_src: Path) -> int:
    """Run the REAL gateway (bridge_cli) as a client of the vfleet-gw node."""
    gw_nd = _node_dir(workdir, "gw")
    pidfile = gw_nd / "gateway.pid"
    pid = _read_pid(pidfile)
    if pid and _pid_alive(pid):
        # Always respawn: a sandbox gateway is cattle. Reusing a survivor
        # means reusing ITS env/config — canary runs 4-6 on 2026-08-27 all
        # silently reused one broken pre-fix process this way.
        logger.info("killing previous sandbox gateway (pid %d)", pid)
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            if not _pid_alive(pid):
                break
            time.sleep(0.25)
        if _pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
    _write_gateway_config(workdir, base_port)
    _start_mesh_stub(workdir, base_port)
    # Truncate: the breach detector reads this file and must judge THIS
    # process's attachments, not a previous run's.
    log = open(gw_nd / "gateway.log", "wb")
    proc = subprocess.Popen(  # managed daemon; reaped by down()
        [sys.executable, str(repo_src / "gateway" / "bridge_cli.py")],
        stdout=log, stderr=subprocess.STDOUT,
        cwd=str(repo_src), env=_gateway_env(workdir, base_port),
        start_new_session=True,
    )
    pidfile.write_text(str(proc.pid))
    logger.info("gateway spawned (pid %d) HOME=%s", proc.pid, _gw_home(workdir))

    deadline = time.monotonic() + 60
    logpath = gw_nd / "gateway.log"
    while time.monotonic() < deadline:
        text = logpath.read_text(errors="replace")
        if "Connected to RNS (LXMF ready)" in text:
            logger.info("gateway LXMF ready")
            return 0
        if not _pid_alive(proc.pid):
            logger.error("gateway exited during startup — tail of log:\n%s",
                         "\n".join(text.splitlines()[-15:]))
            return 1
        time.sleep(2)
    logger.error("gateway never reached 'LXMF ready' in 60s — tail:\n%s",
                 "\n".join(logpath.read_text(errors="replace").splitlines()[-15:]))
    return 1


def _start_echo(workdir: Path, repo_src: Path):
    """Start the sandbox echo responder; returns (echo_hash, popen) or (None, None)."""
    echo_nd = _node_dir(workdir, "echo")
    proc = subprocess.run(
        [sys.executable, "-m", "lab.lxmf_echo", "--init"],
        capture_output=True, text=True, timeout=30,
        cwd=str(repo_src), env=_lab_env(echo_nd),
    )
    if proc.returncode != 0:
        logger.error("echo --init failed: %s", proc.stderr.strip())
        return None, None
    echo_hash = proc.stdout.strip().splitlines()[-1].split("=")[-1].strip()
    logger.info("echo destination: %s", echo_hash)

    echo_log = open(echo_nd / "echo.log", "ab")
    echo_proc = subprocess.Popen(  # managed daemon; caller kills
        [sys.executable, "-m", "lab.lxmf_echo", "--announce-interval", "20"],
        stdout=echo_log, stderr=subprocess.STDOUT,
        cwd=str(repo_src), env=_lab_env(echo_nd),
        start_new_session=True,
    )
    (echo_nd / "echo.pid").write_text(str(echo_proc.pid))
    return echo_hash, echo_proc


def _check_breach(workdir: Path) -> List[str]:
    """Runtime breach detector (caught a real one on 2026-08-27): every RNS
    listener preflight the gateway process logged must name a vfleet
    instance. A non-vfleet name means some component attached to the box's
    REAL mesh."""
    gw_log = (_node_dir(workdir, "gw") / "gateway.log").read_text(
        errors="replace")
    return [l for l in gw_log.splitlines()
            if "@rns/" in l and "@rns/vfleet-" not in l]


def _run_canary_once(workdir: Path, base_port: int, repo_src: Path,
                     *, leg1_timeout: int = 90,
                     leg2_timeout: int = 90) -> Tuple[int, str]:
    """One gateway_rt_canary fire in the sandbox env; (rc, verdict tail)."""
    peers = workdir / "lab_peers"
    env = _gateway_env(workdir, base_port)
    proc = subprocess.run(
        [sys.executable, "-m", "lab.gateway_rt_canary",
         "--peer", "vecho", "--peers-file", str(peers),
         "--leg1-timeout", str(leg1_timeout),
         "--leg2-timeout", str(leg2_timeout),
         "--skip-service-check"],  # sandbox gateway is a managed process, not a unit
        capture_output=True, text=True,
        timeout=leg1_timeout + leg2_timeout + 120,
        cwd=str(repo_src), env=env,
    )
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-3:])
    return proc.returncode, tail


def canary(workdir: Path, base_port: int, repo_src: Path) -> int:
    """gateway_rt_canary green in the sandbox: enqueue -> LXMF CONFIRMED ->
    echo ACK bridged back to a mesh queue row. The canary runs in the SAME
    sandbox env, so its default DB paths resolve to the gateway's DBs."""
    if up(workdir, base_port) != 0:
        return 1
    if status(workdir, base_port) != 0:
        logger.error("canary refused: fleet not clean (see status above)")
        return 1

    echo_hash, echo_proc = _start_echo(workdir, repo_src)
    if echo_hash is None:
        return 1
    try:
        if start_gateway(workdir, base_port, repo_src) != 0:
            return 1
        breaches = _check_breach(workdir)
        if breaches:
            logger.error("ISOLATION BREACH — gateway touched a non-vfleet "
                         "RNS instance:\n%s", "\n".join(breaches[:4]))
            return 1

        (workdir / "lab_peers").write_text(f"vecho={echo_hash}\n")
        time.sleep(5)  # one announce cycle so the gateway has the echo's path

        rc, tail = _run_canary_once(workdir, base_port, repo_src)
        if rc == 0:
            print(f"CANARY OK — full gateway round trip in the sandbox\n{tail}")
            return 0
        print(f"CANARY rc={rc}\n{tail}")
        return rc
    finally:
        if _pid_alive(echo_proc.pid):
            os.kill(echo_proc.pid, signal.SIGTERM)


# ------------------------------------------------------------ chaos layer

def _truncate_sandbox_ratchets(workdir: Path) -> int:
    """Plant the exact power-loss corpse shape (0-byte ratchets) in the
    SANDBOX gateway's LXMF storage. Returns how many files were truncated.

    CONTAINMENT INVARIANT: refuses any path that does not resolve inside
    the workdir — a fault injector that can reach outside its sandbox is
    itself the incident (honest_failure_modes #8; tx-guard 2026-08-09).
    """
    ratchet_dir = (_gw_home(workdir) / ".config" / "meshforge"
                   / "lxmf_storage" / "lxmf" / "ratchets")
    count = 0
    work_root = workdir.resolve()
    if not ratchet_dir.is_dir():
        return 0
    for p in ratchet_dir.glob("*.ratchets"):
        resolved = p.resolve()
        if work_root not in resolved.parents:
            raise RuntimeError(
                f"chaos containment violation: {resolved} is outside the "
                f"sandbox workdir {work_root} — refusing to touch it")
        p.write_bytes(b"")
        count += 1
    return count


def chaos(workdir: Path, base_port: int, repo_src: Path) -> int:
    """Chaos drills: replay paid-for failure classes against the LIVE
    sandbox pipeline. Each drill asserts BOTH halves — the honest failure
    behavior under fault AND the recovery after it. A canary that stays
    green through a partition would itself be the defect.

      baseline     canary OK on a healthy fleet
      corrupt      SIGKILL gateway (unclean stop), 0-byte its ratchets
                   (the Lala class) -> restart must log the quarantine
                   guard firing AND canary returns OK
      partition    SIGSTOP the transport rnsd (wedged/unreachable relay)
                   -> canary MUST fail; SIGCONT -> canary OK again
    """
    results = []

    def record(name: str, ok: bool, evidence: str):
        results.append((name, ok, evidence))
        print(f"DRILL {name:10s} {'OK  ' if ok else 'FAIL'} — {evidence}")

    if up(workdir, base_port) != 0:
        return 1
    if status(workdir, base_port) != 0:
        logger.error("chaos refused: fleet not clean (see status above)")
        return 1
    echo_hash, echo_proc = _start_echo(workdir, repo_src)
    if echo_hash is None:
        return 1

    try:
        if start_gateway(workdir, base_port, repo_src) != 0:
            return 1
        if _check_breach(workdir):
            logger.error("chaos refused: isolation breach at baseline")
            return 1
        (workdir / "lab_peers").write_text(f"vecho={echo_hash}\n")
        time.sleep(5)

        # --- drill 0: baseline -------------------------------------------
        rc, tail = _run_canary_once(workdir, base_port, repo_src)
        record("baseline", rc == 0, tail.splitlines()[-1] if tail else f"rc={rc}")
        if rc != 0:
            return 1  # no point drilling faults on a broken baseline

        # --- drill 1: power-loss ratchet corpse --------------------------
        gw_pid = _read_pid(_node_dir(workdir, "gw") / "gateway.pid")
        if gw_pid and _pid_alive(gw_pid):
            os.kill(gw_pid, signal.SIGKILL)   # unclean stop, like the mains
            time.sleep(1)
        n = _truncate_sandbox_ratchets(workdir)
        if n == 0:
            record("corrupt", False,
                   "no ratchet files existed to corrupt — vacuous drill "
                   "(an audit of zero things is not a pass)")
        else:
            ok_start = start_gateway(workdir, base_port, repo_src) == 0
            gw_log = (_node_dir(workdir, "gw") / "gateway.log").read_text(
                errors="replace")
            quarantined = "Quarantined corrupt ratchet" in gw_log
            rc, tail = _run_canary_once(workdir, base_port, repo_src)
            record("corrupt",
                   ok_start and quarantined and rc == 0,
                   f"truncated={n} guard_fired={quarantined} "
                   f"restart_ok={ok_start} canary_rc={rc}")

        # --- drill 2: transport partition --------------------------------
        tr_pid = _read_pid(_pidfile(workdir, "transport"))
        if not (tr_pid and _pid_alive(tr_pid)):
            record("partition", False, "transport rnsd not running")
        else:
            os.kill(tr_pid, signal.SIGSTOP)   # wedged relay: up but silent
            try:
                rc_fail, _ = _run_canary_once(
                    workdir, base_port, repo_src,
                    leg1_timeout=20, leg2_timeout=5)
            finally:
                os.kill(tr_pid, signal.SIGCONT)
            time.sleep(5)                      # let links settle
            rc_ok, tail = _run_canary_once(workdir, base_port, repo_src)
            record("partition",
                   rc_fail != 0 and rc_ok == 0,
                   f"during_partition_rc={rc_fail} (must be nonzero — a "
                   f"green canary through a partition is the lie), "
                   f"after_heal_rc={rc_ok}")

        failed = [n for n, ok, _ in results if not ok]
        if failed:
            print(f"CHAOS FAIL — drills not green: {failed}")
            return 1
        print(f"CHAOS OK — {len(results)} drills green "
              "(fault behavior AND recovery both asserted)")
        return 0
    finally:
        if _pid_alive(echo_proc.pid):
            os.kill(echo_proc.pid, signal.SIGTERM)


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
    parser.add_argument("command",
                        choices=("up", "down", "status", "smoke", "canary",
                                 "chaos"))
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
    if args.command == "canary":
        return canary(args.workdir, args.base_port, repo_src)
    if args.command == "chaos":
        return chaos(args.workdir, args.base_port, repo_src)
    return smoke(args.workdir, args.base_port, repo_src)


if __name__ == "__main__":
    sys.exit(main())
