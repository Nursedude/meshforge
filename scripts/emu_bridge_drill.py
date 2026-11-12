#!/usr/bin/env python3
"""Emulated tier-1 drill: BR-T #14 leg-down hold on two meshtasticd --sim nodes.

PRE-REGISTERED ACCEPTANCE (written 2026-11-11 before any run):
  PASS iff, on two meshtasticd --sim nodes (the fleet build, 2.7.26.61) bridged
  by the REAL MeshtasticPresetBridge over two TCP legs (primary :A, secondary :B):
   (1) BASE: a text injected as over-air RX on A reaches B — B's own log shows
       "[ServerAPI] Received text msg ... <text>" — within 15 s;
   (2) HELD: with B's process killed for >= 20 s, a text injected on A during
       the outage is HELD — its p2s queue row is NOT dead_letter at the end of
       the outage (pending/in_progress);
   (3) CROSSES: after B restarts (same fsdir/port), that held text reaches B's
       log within 60 s of the restart;
   (4) CONTROL: the same driver against the pre-fix tree (33743347) FAILS at
       (2) or (3).
  n = 2 runs on the fixed tree. Anything else = FAIL / INCONCLUSIVE, named.
  Emulation proves the code against the real library + firmware API; it says
  NOTHING about RF, airtime, USB resets or power.

Isolation: run under `env -u SUDO_USER -u LOGNAME HOME=<scratch>` so every
store the bridge resolves through get_real_user_home lands in scratch; the
driver REFUSES otherwise (check_isolation). The driver opens NO client of its
own: injection rides the bridge's own primary connection (meshtasticd serves
one PhoneAPI client — a second would force-close the bridge's, #17).

First run 2026-11-11 on meshforge-pro (meshtasticd 2.7.26.61 held, service
disabled): fixed tree bc5effbf PASS 2/2 (held 20 s `pending`, crossed 3.3 s
after restart); pre-fix 33743347 FAIL at (2) `dead_letter`.

Usage (on a box with meshtasticd installed; nothing transmits — sim radio):
  env -u SUDO_USER -u LOGNAME HOME=/path/to/scratch PYTHONPATH=<tree>/src \
      <venv>/bin/python scripts/emu_bridge_drill.py <run-id> <portA> <portB>
Exit: 0 PASS, 1 FAIL, 2 INCONCLUSIVE, 3 REFUSED.
"""
import json
import os
import pwd
import subprocess
import sys
import threading
import time
from pathlib import Path

MTD = "/usr/bin/meshtasticd"
STOP = threading.Event()

NODE_YAML = (
    "---\nLora:\n  Module: sim\nLogging:\n  LogLevel: info\n"
    "General:\n  MaxNodes: 50\n  MaxMessageQueue: 100\n")


class Refused(RuntimeError):
    """The environment would let a store land in a real home."""


def check_isolation(env) -> Path:
    """HOME must be a scratch dir: get_real_user_home reads SUDO_USER, then
    LOGNAME, before HOME — either set means the bridge's queues/counters
    would land in that user's REAL home."""
    for var in ("SUDO_USER", "LOGNAME"):
        if env.get(var):
            raise Refused(f"{var} is set — unset it (env -u {var}); stores "
                          f"would resolve to that user's real home")
    home = env.get("HOME")
    if not home:
        raise Refused("HOME is not set — point it at a scratch dir")
    real = pwd.getpwuid(os.getuid()).pw_dir
    if os.path.realpath(home) == os.path.realpath(real):
        raise Refused(f"HOME is this account's real home ({real}) — use a "
                      f"scratch dir")
    return Path(home)


def write_node_config(home: Path) -> Path:
    """A sim-only node config: no web server, no config.d overlays (two
    nodes on one box must not collide on any shared port)."""
    cfg = home / "node.yaml"
    cfg.write_text(NODE_YAML)
    return cfg


def wait(s):
    STOP.wait(s)


def start_node(name, port, fsdir, log, hwid, cfg):
    fsdir.mkdir(parents=True, exist_ok=True)
    fh = open(log, "ab")
    return subprocess.Popen(
        [MTD, "-s", "-c", str(cfg), "-d", str(fsdir),
         "-p", str(port), "-h", hwid],
        stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True)


def listening(port):
    out = subprocess.run(["ss", "-ltn"], capture_output=True, text=True,
                         timeout=10).stdout
    return f":{port} " in out


def until(pred, timeout_s, step=0.25):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        if pred():
            return time.monotonic() - t0
        wait(step)
    return None


def log_has(log, needle):
    try:
        return any("Received text msg" in ln and needle in ln
                   for ln in Path(log).read_text(errors="replace").splitlines())
    except FileNotFoundError:
        return False


def inject(iface, text, sender=0x1234ABCD, pid=None):
    from meshtastic.protobuf import mesh_pb2, portnums_pb2
    c = mesh_pb2.Compressed()
    c.portnum = portnums_pb2.PortNum.TEXT_MESSAGE_APP
    c.data = text.encode()
    mp = mesh_pb2.MeshPacket()
    setattr(mp, "from", sender)
    mp.to = 0xFFFFFFFF
    mp.id = pid or (int(time.time() * 1000) & 0x7FFFFFFF)
    mp.decoded.portnum = portnums_pb2.PortNum.SIMULATOR_APP
    mp.decoded.payload = c.SerializeToString()
    tr = mesh_pb2.ToRadio()
    tr.packet.CopyFrom(mp)
    iface._sendToRadio(tr)


def row_statuses(q):
    from utils.db_helpers import connect_tuned
    conn = connect_tuned(q._db_path)
    try:
        return [(r[0], r[1]) for r in conn.execute(
            "SELECT status, payload FROM messages ORDER BY created_at")]
    finally:
        conn.close()


def main():
    run, port_a, port_b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    home = check_isolation(os.environ)
    cfg_path = write_node_config(home)
    work = home / f"run_{run}"
    work.mkdir(parents=True, exist_ok=True)
    log_a, log_b = work / "A.log", work / "B.log"
    res = {"run": run, "verdict": "INCONCLUSIVE", "steps": {}}
    import gateway.mesh_bridge as mb
    res["imported"] = mb.__file__
    from gateway.config import GatewayConfig, MeshtasticBridgeConfig, MeshtasticConfig
    a = start_node("A", port_a, work / "A", log_a, "02:00:00:00:01:0a", cfg_path)
    b = start_node("B", port_b, work / "B", log_b, "02:00:00:00:01:0b", cfg_path)
    bridge = None
    try:
        if until(lambda: listening(port_a) and listening(port_b), 30) is None:
            res["why"] = "sim nodes never listened"
            return res
        cfg = GatewayConfig()
        cfg.mesh_bridge = MeshtasticBridgeConfig(
            enabled=True,
            primary=MeshtasticConfig(preset="LONG_FAST", name="lf", host="localhost",
                                     port=port_a, connection_type="tcp"),
            secondary=MeshtasticConfig(preset="SHORT_TURBO", name="st", host="localhost",
                                       port=port_b, connection_type="tcp"),
        )
        bridge = mb.MeshtasticPresetBridge(config=cfg)
        bridge.start()
        up = lambda: bool(bridge._primary_connected and bridge._secondary_connected
                          and bridge._primary_interface and bridge._secondary_interface)
        t = until(up, 60)
        res["steps"]["legs_up_s"] = t
        if t is None:
            res["why"] = "legs never both connected"
            return res
        wait(2)
        base = f"emu {run} base"
        inject(bridge._primary_interface, base)
        t = until(lambda: log_has(log_b, base), 15)
        res["steps"]["1_base_s"] = t
        if t is None:
            res["verdict"], res["why"] = "FAIL", "(1) base text never reached B"
            return res
        # (2) leg down
        b.terminate()
        b.wait(timeout=15)
        t_kill = time.monotonic()
        res["steps"]["leg_noticed_s"] = until(lambda: not bridge._secondary_connected, 20)
        held = f"emu {run} held"
        inject(bridge._primary_interface, held)
        wait(max(0.0, 20 - (time.monotonic() - t_kill)))
        q = bridge._primary_to_secondary
        rows = [s for s, p in row_statuses(q) if held in p]
        res["steps"]["2_held_row_status"] = rows
        st = bridge.get_status().get("queue", {})
        res["steps"]["2_status_p2s_held_link_down"] = st.get("p2s_held_link_down", "absent")
        res["steps"]["outage_s"] = round(time.monotonic() - t_kill, 1)
        if not rows or any(s == "dead_letter" for s in rows):
            res["verdict"], res["why"] = "FAIL", f"(2) held row not held: {rows}"
            return res
        # (3) leg returns
        b = start_node("B", port_b, work / "B", log_b, "02:00:00:00:01:0b", cfg_path)
        t = until(lambda: log_has(log_b, held), 60)
        res["steps"]["3_crossed_after_restart_s"] = t
        res["steps"]["3_final_rows"] = [s for s, p in row_statuses(q) if held in p]
        if t is None:
            res["verdict"], res["why"] = "FAIL", "(3) held text never reached B after restart"
            return res
        res["verdict"] = "PASS"
        res["stats"] = {k: v for k, v in bridge.stats.items()
                        if isinstance(v, (int, float)) and v}
        return res
    finally:
        if bridge is not None:
            try:
                bridge.stop()
            except Exception as e:  # report, never mask the verdict
                res["stop_error"] = repr(e)
        for p in (a, b):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()


if __name__ == "__main__":
    try:
        out = main()
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        sys.exit(3)
    print(json.dumps(out, indent=1, default=str))
    sys.exit(0 if out["verdict"] == "PASS" else 1 if out["verdict"] == "FAIL" else 2)
