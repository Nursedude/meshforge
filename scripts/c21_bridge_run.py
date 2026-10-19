#!/usr/bin/env python3
"""C21 cross-preset bridge run — LONG_FAST <-> SHORT_TURBO through mesh_bridge.

Plan + approved acceptance sentence:
    .claude/plans/c21_cross_preset_run_2026_10_18.md

Four USB Heltecs on one radio-less host:
    leg_lf / leg_st   — the bridge's two serial legs (V3s)
    src_lf / src_st   — scripted source/sink on each side (V4s)

Every radio sits on a PRIVATE lab channel (random PSK, never committed) on
slots clear of the fleet's LF ch20 (906.875), ST ch8 (905.75) and the
regional RNS frequency (903.625), at ~1 dBm, hop_limit 1, MQTT off.

The logic below is pure and unit-tested (tests/test_c21_bridge_run.py); the
radio I/O around it is thin and is proven by the run itself.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.meshtastic_modem import channel_centre_mhz  # noqa: E402
from utils.paths import get_real_user_home  # noqa: E402

CHANNEL_NAME = "MFLab"
TX_POWER_DBM = 1          # 0 is NOT "off": the firmware reads it as region max
HOP_LIMIT = 1

# side -> (preset, US channel_num)
SIDES = {
    "lf": ("LONG_FAST", 80),
    "st": ("SHORT_TURBO", 45),
}
ROLES = ("leg_lf", "leg_st", "src_lf", "src_st")


def radio_plan(role: str, psk: Optional[bytes] = None) -> Dict[str, Any]:
    """The settings ``role``'s radio must carry, as flat readback keys.

    ``freq_mhz`` is derived (firmware slot formula) and is never read back;
    every other key must match the radio's own readback before traffic runs.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {ROLES}")
    preset, slot = SIDES[role.rsplit("_", 1)[1]]
    freq, _slot, _n = channel_centre_mhz(preset, slot, "US")
    plan: Dict[str, Any] = {
        "freq_mhz": freq,
        "lora.region": "US",
        "lora.use_preset": True,
        "lora.modem_preset": preset,
        "lora.channel_num": slot,
        "lora.tx_power": TX_POWER_DBM,
        "lora.hop_limit": HOP_LIMIT,
        "lora.tx_enabled": True,
        "lora.override_frequency": 0.0,   # non-zero ignores channel_num
        "lora.frequency_offset": 0.0,
        "mqtt.enabled": False,
        "position.gps_mode": "DISABLED",
        "channel0.name": CHANNEL_NAME,
        "channel0.uplink_enabled": False,
        "channel0.downlink_enabled": False,
    }
    if psk is not None:
        plan["channel0.psk"] = psk
    return plan


def readback_mismatches(plan: Dict[str, Any],
                        got: Dict[str, Any]) -> List[Tuple[str, Any, Any]]:
    """(key, want, got) for every planned key the radio does not confirm.

    A key absent from the readback is a mismatch (unobserved is not agreed).
    PSK values are redacted to their length so a mismatch never prints a key.
    """
    out: List[Tuple[str, Any, Any]] = []
    for key, want in plan.items():
        if key == "freq_mhz":
            continue
        have = got.get(key, _ABSENT)
        if have == want:
            continue
        if key.endswith(".psk"):
            out.append((key, _redact(want), _redact(have)))
        else:
            out.append((key, want, None if have is _ABSENT else have))
    return out


_ABSENT = object()


def _redact(v: Any) -> str:
    if v is _ABSENT:
        return "<absent>"
    return f"<{len(v)}-byte key>" if isinstance(v, (bytes, bytearray)) else "<key>"


# --- the run's bridge config ------------------------------------------------

BY_ID = "/dev/serial/by-id/"


def write_bridge_config(path: Path, *, leg_lf: str, leg_st: str) -> None:
    """Write the run's own gateway.json (never the operator's).

    Legs are /dev/serial/by-id paths: ttyUSB/ttyACM numbers reshuffle on
    replug, and a swapped leg would bridge each preset back onto itself.
    """
    for name, port in (("leg_lf", leg_lf), ("leg_st", leg_st)):
        if not port.startswith(BY_ID):
            raise ValueError(f"{name} must be a {BY_ID} path, got {port!r}")
    import json
    data = {
        "bridge_mode": "mesh_bridge",
        "mesh_bridge": {
            "enabled": True,
            "primary": {"name": "lab_lf", "preset": SIDES["lf"][0],
                        "connection_type": "serial", "serial_device": leg_lf},
            "secondary": {"name": "lab_st", "preset": SIDES["st"][0],
                          "connection_type": "serial", "serial_device": leg_st},
            "direction": "bidirectional",
            "channels": [0],
            "dedup_window_sec": 60,
            "add_prefix": True,
        },
    }
    Path(path).write_text(json.dumps(data, indent=2) + "\n")


def load_bridge_config(path: Path):
    """Load ``path`` through the real GatewayConfig.load().

    load() only reads the operator's fixed path, so the path is swapped in
    for the duration of the call and restored after, even on error. Not
    thread-safe: call it before the bridge starts its threads.
    """
    from gateway.config import GatewayConfig
    original = GatewayConfig.__dict__["get_config_path"]
    GatewayConfig.get_config_path = classmethod(lambda cls: Path(path))
    try:
        return GatewayConfig.load()
    finally:
        GatewayConfig.get_config_path = original


QUEUE_FILES = ("p2s.db", "s2p.db")


def leftover_queue_depth(queue_dir: Path) -> Dict[str, int]:
    """Active (pending + in_progress) messages per bridge queue file.

    Non-empty means an earlier run's messages would be re-sent into this
    one. The run refuses on it; it never clears the queues itself.
    """
    from gateway.message_queue import PersistentMessageQueue
    out: Dict[str, int] = {}
    for name in QUEUE_FILES:
        db = Path(queue_dir) / name
        if not db.exists():
            continue
        depth = PersistentMessageQueue(db_path=str(db)).get_queue_depth()
        if depth:
            out[name] = depth
    return out


# --- message text -------------------------------------------------------------


DIRECTIONS = ("lf2st", "st2lf")
LOOP_TAG = "LAB"
_MSG_RE = re.compile(
    r"^(?:\[Mesh:(?P<tag>[A-Za-z0-9_]+)\]\s)?"
    r"c21 (?P<run>\S+) (?P<seq>\d+) (?P<dir>lf2st|st2lf)$")


def format_msg(run: str, seq: int, direction: str) -> str:
    """The planned message text: ``c21 <run> <seq> <dir>``."""
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}")
    return f"c21 {run} {seq} {direction}"


def loop_control_msg(run: str, seq: int, direction: str) -> str:
    """A message that already carries a bridge tag, as if another bridge had
    forwarded it. The bridge must drop it as already_bridged."""
    return f"[Mesh:{LOOP_TAG}] {format_msg(run, seq, direction)}"


def parse_msg(text: str) -> Optional[Tuple[str, int, str, Optional[str]]]:
    """(run, seq, direction, bridge tag or None), or None if not ours."""
    m = _MSG_RE.match(text or "")
    if not m:
        return None
    return m["run"], int(m["seq"]), m["dir"], m["tag"]


# --- reconcile: the approved acceptance sentence, as a verdict ---------------


RF_LOSS_LIMIT = 0.05      # per leg; above it the run is INCONCLUSIVE, never PASS
COINCIDE_S = 2.0          # ~one LONG_FAST airtime: TX this close may have collided
# direction -> (bridge leg that hears it, bridge leg it goes out on, far radio)
ROUTE = {
    "lf2st": ("primary", "secondary", "src_st"),
    "st2lf": ("secondary", "primary", "src_lf"),
}
EXPECTED_DROP = {"repeat": "duplicate", "loop": "already_bridged"}
FAILING = {"unaccounted", "duplicate", "echo", "control_failed", "no_send_record",
           "delivered_unrecorded"}


def reconcile(events: List[Dict[str, Any]], run: str) -> Dict[str, Any]:
    """Label every message of ``run`` and give PASS / FAIL / INCONCLUSIVE.

    Labels, per (run, seq, direction):
      delivered        bridge heard it, sent it on, far radio heard it once
      rf_ingress_loss  sent, the bridge never heard it (not the bridge's fault)
      rf_egress_loss   the bridge sent it on, the far radio never heard it
      dropped:<reason> the bridge dropped a normal message (FAIL: named, but wrong)
      control_ok       a planted control dropped under its expected reason
      control_failed   a planted control was not dropped as expected
      unaccounted      the bridge heard it and the record does not say what
                       happened to it next
      duplicate        sent on, or heard at the far end, more than once
      echo             seen on, or sent back out of, the side it came from
      no_send_record   the bridge or far radio saw it but no send was logged
      delivered_unrecorded  heard at the far end, but the queue reaped it as
                       dead_letter (a bookkeeping failure, not a loss)
    FAIL on any label in FAILING or any dropped:*. Otherwise INCONCLUSIVE if
    RF loss on any leg is above RF_LOSS_LIMIT, a control kind was never
    exercised, a radio's link was lost, or traffic stopped before its
    deadline (each named in ``notes``). Otherwise PASS.

    A ``send_error`` (the source radio refused the write) is not an
    injection: it is counted in ``not_injected`` and never labelled. Each RF
    loss lists in ``coincident`` the other transmissions within COINCIDE_S.
    """
    per: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
    notes: List[str] = []
    not_injected = 0
    tx_moments: List[Tuple[float, Tuple[str, int, str], str]] = []
    for e in events:
        ev = e.get("ev")
        if ev == "link_lost":
            notes.append(f"source radio {e.get('radio')} link lost: run cut short")
        elif ev == "bridge_link_lost":
            notes.append(f"bridge {e.get('leg')} leg link lost: its RF loss counts "
                         "include the outage, not RF")
        elif ev == "traffic_end" and e.get("completed") is False:
            notes.append("traffic stopped before its deadline: not a full run")
        elif ev == "send_error":
            not_injected += 1
            continue
        parsed = parse_msg(e.get("text", ""))
        if not parsed or parsed[0] != run:
            continue
        key = parsed[:3]
        rec = per.setdefault(key, {"sends": [], "rx_in": 0, "rx_wrong": 0,
                                   "drops": Counter(), "tx": 0, "tx_wrong": 0,
                                   "heard": 0, "send_wall": None, "tx_wall": None})
        ingress, egress, far = ROUTE[key[2]]
        wall = e.get("wall")
        if ev == "send":
            rec["sends"].append(e.get("kind", "normal"))
            if wall is not None:
                rec["send_wall"] = rec["send_wall"] or wall
                tx_moments.append((wall, key, e["text"]))
        elif ev == "bridge_rx":
            rec["rx_in" if e.get("leg") == ingress else "rx_wrong"] += 1
        elif ev == "bridge_drop" and e.get("leg") == ingress:
            rec["drops"][e.get("reason", "?")] += 1
        elif ev == "bridge_tx":
            rec["tx" if e.get("dest") == egress else "tx_wrong"] += 1
            if wall is not None:
                rec["tx_wall"] = rec["tx_wall"] or wall
                tx_moments.append((wall, key, e["text"]))
        elif ev == "heard" and e.get("radio") == far:
            rec["heard"] += 1

    labels = {key: _label(rec) for key, rec in per.items()}

    coincident: Dict[Tuple[str, int, str], List[str]] = {}
    for key, lab in labels.items():
        at = {"rf_ingress_loss": per[key]["send_wall"],
              "rf_egress_loss": per[key]["tx_wall"]}.get(lab)
        if at is not None:
            coincident[key] = [text for t, k, text in tx_moments
                               if k != key and abs(t - at) <= COINCIDE_S]

    rf_loss: Dict[str, Dict[str, Tuple[int, int]]] = {}
    for d in ROUTE:
        normal = [k for k, rec in per.items()
                  if k[2] == d and _kind(rec) == "normal"]
        ing = sum(labels[k] == "rf_ingress_loss" for k in normal)
        out = [k for k in normal if labels[k] in ("delivered", "rf_egress_loss")]
        egr = sum(labels[k] == "rf_egress_loss" for k in out)
        rf_loss[d] = {"ingress": (ing, len(normal)), "egress": (egr, len(out))}

    counts = Counter(labels.values())
    if any(lab in FAILING or lab.startswith("dropped:") for lab in labels.values()):
        verdict = "FAIL"
    else:
        for d, legs in rf_loss.items():
            for leg, (lost, n) in legs.items():
                if n and lost / n > RF_LOSS_LIMIT:
                    notes.append(f"RF {leg} loss {d} {lost}/{n} above "
                                 f"{RF_LOSS_LIMIT:.0%}: re-site the radios")
        for kind in EXPECTED_DROP:
            if not any(labels[k] == "control_ok" for k, rec in per.items()
                       if _kind(rec) == kind):
                notes.append(f"{kind} control never exercised")
        verdict = "INCONCLUSIVE" if notes else "PASS"
    return {"verdict": verdict, "labels": labels, "rf_loss": rf_loss,
            "counts": dict(counts), "notes": notes,
            "not_injected": not_injected, "coincident": coincident}


def _kind(rec: Dict[str, Any]) -> str:
    for kind in EXPECTED_DROP:
        if kind in rec["sends"]:
            return kind
    return "normal"


def _label(rec: Dict[str, Any]) -> str:
    if not rec["sends"]:
        return "no_send_record"
    if rec["rx_wrong"] or rec["tx_wrong"]:
        return "echo"
    if rec["heard"] > 1 or rec["tx"] > 1:
        return "duplicate"
    kind = _kind(rec)
    rx, tx, heard, drops = rec["rx_in"], rec["tx"], rec["heard"], rec["drops"]
    if kind == "repeat":
        if rx < 2:
            return "rf_ingress_loss" if not drops else "control_failed"
        ok = rx == 2 and tx == 1 and drops == Counter(duplicate=1)
        return "control_ok" if ok else "control_failed"
    if kind == "loop":
        if rx == 0 and not (tx or heard or drops):
            return "rf_ingress_loss"
        ok = rx == 1 and not tx and not heard and drops == Counter(already_bridged=1)
        return "control_ok" if ok else "control_failed"
    if rx == 0:
        return "rf_ingress_loss" if not (tx or heard or drops) else "unaccounted"
    if drops == Counter(dead_letter=1) and tx == 1 and heard == 1:
        return "delivered_unrecorded"   # went out + heard; the queue lost the record
    if drops:
        return "dropped:" + ",".join(sorted(drops))
    if tx == 1:
        return "delivered" if heard == 1 else "rf_egress_loss"
    return "unaccounted"


# --- the bridge's own per-message lines -> events ----------------------------
# Format written by gateway._mesh_bridge_legs.bridge_event — change both together.

_STAMP_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) ")
_LOST_RE = re.compile(r"(?P<side>primary|secondary) serial link LOST")
_EVENT_RE = re.compile(
    r"mesh_bridge (?P<verb>rx|drop|tx) (?:leg|dest)=(?P<side>primary|secondary)"
    r"(?: reason=(?P<reason>\w+))?: (?P<text>.*)$")


def bridge_event_from_log(line: str) -> Optional[Dict[str, Any]]:
    """One bridge log line (journal prefix allowed) as a reconcile event."""
    line = (line or "").rstrip("\n")
    m = _EVENT_RE.search(line)
    if m:
        verb, side, text = m["verb"], m["side"], m["text"]
        if verb == "tx":
            e = {"ev": "bridge_tx", "dest": side, "text": text}
        elif verb == "drop":
            e = {"ev": "bridge_drop", "leg": side, "reason": m["reason"], "text": text}
        else:
            e = {"ev": "bridge_rx", "leg": side, "text": text}
    else:
        lost = _LOST_RE.search(line)
        if not lost:
            return None
        e = {"ev": "bridge_link_lost", "leg": lost["side"]}
    stamp = _STAMP_RE.match(line)
    if stamp:   # bridge-host's "%(asctime)s %(message)s" formatter, local time
        import datetime
        dt = datetime.datetime.strptime(stamp[1], "%Y-%m-%d %H:%M:%S")
        e["wall"] = dt.timestamp() + int(stamp[2]) / 1000
    return e


# --- the plan <-> meshtastic protobufs ---------------------------------------

def apply_plan(plan: Dict[str, Any], local_config, module_config, channel0) -> None:
    """Write ``plan`` into a radio's LocalConfig / LocalModuleConfig / channel 0."""
    from meshtastic.protobuf import channel_pb2, config_pb2
    lora_t = config_pb2.Config.LoRaConfig
    lora = local_config.lora
    lora.region = lora_t.RegionCode.Value(plan["lora.region"])
    lora.use_preset = plan["lora.use_preset"]
    lora.modem_preset = lora_t.ModemPreset.Value(plan["lora.modem_preset"])
    lora.channel_num = plan["lora.channel_num"]
    lora.tx_power = plan["lora.tx_power"]
    lora.hop_limit = plan["lora.hop_limit"]
    lora.tx_enabled = plan["lora.tx_enabled"]
    lora.override_frequency = plan["lora.override_frequency"]
    lora.frequency_offset = plan["lora.frequency_offset"]
    module_config.mqtt.enabled = plan["mqtt.enabled"]
    local_config.position.gps_mode = (
        config_pb2.Config.PositionConfig.GpsMode.Value(plan["position.gps_mode"]))
    channel0.index = 0
    channel0.role = channel_pb2.Channel.Role.PRIMARY
    channel0.settings.name = plan["channel0.name"]
    channel0.settings.uplink_enabled = plan["channel0.uplink_enabled"]
    channel0.settings.downlink_enabled = plan["channel0.downlink_enabled"]
    if "channel0.psk" in plan:
        channel0.settings.psk = plan["channel0.psk"]


def flatten_readback(local_config, module_config, channel0) -> Dict[str, Any]:
    """A radio's config, read back into radio_plan's flat keys."""
    from meshtastic.protobuf import config_pb2
    lora_t = config_pb2.Config.LoRaConfig
    lora = local_config.lora
    return {
        "lora.region": lora_t.RegionCode.Name(lora.region),
        "lora.use_preset": lora.use_preset,
        "lora.modem_preset": lora_t.ModemPreset.Name(lora.modem_preset),
        "lora.channel_num": lora.channel_num,
        "lora.tx_power": lora.tx_power,
        "lora.hop_limit": lora.hop_limit,
        "lora.tx_enabled": lora.tx_enabled,
        "lora.override_frequency": lora.override_frequency,
        "lora.frequency_offset": lora.frequency_offset,
        "mqtt.enabled": module_config.mqtt.enabled,
        "position.gps_mode": config_pb2.Config.PositionConfig.GpsMode.Name(
            local_config.position.gps_mode),
        "channel0.name": channel0.settings.name,
        "channel0.uplink_enabled": channel0.settings.uplink_enabled,
        "channel0.downlink_enabled": channel0.settings.downlink_enabled,
        "channel0.psk": bytes(channel0.settings.psk),
    }


# =============================================================================
# Radio I/O, launcher and CLI. Thin by design: the logic above is unit-tested;
# this part is proven by the run itself.
# =============================================================================

INTERVAL_S = 120          # one message each way per 2 min (~0.4 % LF airtime)
CONTROL_EVERY = 30        # one repeat + one loop control per hour, early in
REPEAT_AT, LOOP_AT = 3, 4 # each hour so a 15-min smoke run exercises both
REPEAT_GAP_S = 5          # well inside the bridge's 60 s dedup window
CONTROL_GAP_S = 20        # controls never overlap the normal send before them
assert INTERVAL_S / 2 > CONTROL_GAP_S + REPEAT_GAP_S, "cadence would collapse"
DRAIN_S = 180             # after the last send, keep listening this long
REBOOT_WAIT_S = 25        # a lora config write reboots the radio
CONTROL_SEQ_BASE = 100000
EXIT = {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}


def _psk_path() -> Path:
    return get_real_user_home() / ".config" / "meshforge" / "c21_psk"


def _run_dir(run: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", run):
        raise SystemExit(f"run id must be 1-24 of [A-Za-z0-9_-], got {run!r}")
    return get_real_user_home() / ".local" / "share" / "meshforge" / "c21" / run


def _load_psk(create: bool = False) -> bytes:
    """The lab channel's key: random, 0600, outside the repo, never printed."""
    p = _psk_path()
    if not p.exists():
        if not create:
            raise SystemExit(f"no lab PSK at {p}; run `psk` first")
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_bytes(32))
    data = p.read_bytes()
    if len(data) != 32:
        raise SystemExit(f"{p} is {len(data)} bytes, expected 32")
    return data


def _refuse_if_held(port: str) -> None:
    from utils.meshtastic_connection import _device_held_by_other
    held = _device_held_by_other(str(Path(port).resolve()))
    if held:
        raise SystemExit(f"{port} is held by another process — not opening it")
    if held is None:
        # Unprivileged, root's fds are unreadable, so the scan reads unknown.
        # The library opens the port exclusive=True, so a held port still
        # fails at open (EBUSY): that open is the ownership measurement.
        print(f"{port}: ownership scan inconclusive (unreadable /proc fds); "
              "relying on the exclusive open")


def _open(port: str):
    # Direct SerialInterface, as mesh_bridge._connect_serial does: these are
    # USB radios with no meshtasticd, each port opened by one process only.
    from meshtastic import serial_interface
    return serial_interface.SerialInterface(devPath=port)


def _close(iface) -> None:
    try:
        iface.close()
    except Exception as e:  # a radio that rebooted under us closes noisily
        logging.getLogger("c21").debug("close: %s", e)


def cmd_psk(a) -> int:
    _load_psk(create=True)
    print(f"lab PSK ready at {_psk_path()} (0600, 32 bytes, not shown)")
    return 0


def cmd_configure(a) -> int:
    _refuse_if_held(a.port)
    plan = radio_plan(a.role, psk=_load_psk())
    iface = _open(a.port)
    try:
        node = iface.localNode
        apply_plan(plan, node.localConfig, node.moduleConfig,
                   node.getChannelByChannelIndex(0))
        node.beginSettingsTransaction()
        for section in ("lora", "position", "mqtt"):
            node.writeConfig(section)
        node.writeChannel(0)
        node.commitSettingsTransaction()
    finally:
        _close(iface)
    print(f"{a.role}: written; waiting {REBOOT_WAIT_S}s for the radio to reboot")
    threading.Event().wait(REBOOT_WAIT_S)
    iface = _open(a.port)
    try:
        node = iface.localNode
        got = flatten_readback(node.localConfig, node.moduleConfig,
                               node.getChannelByChannelIndex(0))
    finally:
        _close(iface)
    bad = readback_mismatches(plan, got)
    if bad:
        for key, want, have in bad:
            print(f"MISMATCH {a.role} {key}: want {want!r}, radio says {have!r}")
        print(f"REFUSED: {a.role} on {a.port} does not carry the plan")
        return 1
    print(f"OK {a.role} {a.port}: {plan['lora.modem_preset']} slot "
          f"{plan['lora.channel_num']} = {plan['freq_mhz']} MHz (physics until a "
          f"receiver confirms), {plan['lora.tx_power']} dBm, readback agrees")
    return 0


def _detach(unit: str, argv: List[str]) -> int:
    """Start ``argv`` as a transient user unit so it survives the ssh session."""
    cmd = ["systemd-run", "--user", f"--unit={unit}", "--collect",
           "-p", "Restart=no", "-E", "PYTHONUNBUFFERED=1",
           sys.executable, str(Path(__file__).resolve()), *argv]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    print((r.stdout + r.stderr).strip())
    if r.returncode == 0:
        print(f"started {unit}; follow: journalctl --user -u {unit} -f")
    return r.returncode


def cmd_bridge(a) -> int:
    queues = get_real_user_home() / ".config" / "meshforge" / "mesh_bridge_queues"
    left = leftover_queue_depth(queues)
    if left:
        print(f"REFUSED: earlier messages still queued in {queues}: {left}. "
              "They would be re-sent into this run. Inspect and clear them yourself.")
        return 1
    for port in (a.leg_lf, a.leg_st):
        _refuse_if_held(port)
    d = _run_dir(a.run)
    d.mkdir(parents=True, exist_ok=True)
    write_bridge_config(d / "gateway.json", leg_lf=a.leg_lf, leg_st=a.leg_st)
    return _detach(f"c21-bridge-{a.run}", ["bridge-host", "--run", a.run])


def _stop_on_signal() -> threading.Event:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    return stop


def cmd_bridge_host(a) -> int:
    from gateway.mesh_bridge import MeshtasticPresetBridge
    d = _run_dir(a.run)
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
    for h in logging.getLogger().handlers:   # per-message DEBUG -> bridge.log only
        h.setLevel(logging.INFO)
    blog = logging.getLogger("gateway.mesh_bridge")
    blog.setLevel(logging.DEBUG)
    fh = logging.FileHandler(d / "bridge.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    blog.addHandler(fh)
    bridge = MeshtasticPresetBridge(config=load_bridge_config(d / "gateway.json"))
    if not bridge.start():
        print("bridge failed to start")
        return 1
    stop = _stop_on_signal()
    with open(d / "bridge_stats.jsonl", "a") as out:
        while not stop.wait(60):
            out.write(json.dumps({"wall": time.time(), "mono": time.monotonic(),
                                  "status": bridge.get_status()}, default=str) + "\n")
            out.flush()
    bridge.stop()
    return 0


def cmd_traffic(a) -> int:
    for port in (a.src_lf, a.src_st):
        _refuse_if_held(port)
    _run_dir(a.run).mkdir(parents=True, exist_ok=True)
    argv = ["traffic-host", "--run", a.run, "--src-lf", a.src_lf,
            "--src-st", a.src_st, "--hours", str(a.hours)]
    return _detach(f"c21-traffic-{a.run}", argv)


def cmd_traffic_host(a) -> int:
    from pubsub import pub
    d = _run_dir(a.run)
    lock = threading.Lock()
    out = open(d / "events.jsonl", "a")

    def record(ev: Dict[str, Any]) -> None:
        ev.update(wall=time.time(), mono=time.monotonic())
        with lock:
            out.write(json.dumps(ev) + "\n")
            out.flush()

    radios = {"src_lf": _open(a.src_lf), "src_st": _open(a.src_st)}
    names = {id(i): n for n, i in radios.items()}
    lost = threading.Event()

    def on_text(packet, interface):
        name = names.get(id(interface))
        if name:
            record({"ev": "heard", "radio": name,
                    "text": packet.get("decoded", {}).get("text", ""),
                    "snr": packet.get("rxSnr"), "rssi": packet.get("rxRssi")})

    def on_lost(interface=None):
        if id(interface) in names:
            record({"ev": "link_lost", "radio": names[id(interface)]})
            lost.set()

    keep = (on_text, on_lost)   # pubsub holds listeners weakly
    pub.subscribe(on_text, "meshtastic.receive.text")
    pub.subscribe(on_lost, "meshtastic.connection.lost")
    stop = _stop_on_signal()

    def send(radio: str, text: str, kind: str) -> None:
        try:
            radios[radio].sendText(text, channelIndex=0)
            record({"ev": "send", "radio": radio, "kind": kind, "text": text})
        except Exception as e:
            record({"ev": "send_error", "radio": radio, "kind": kind,
                    "text": text, "error": str(e)})

    deadline = time.monotonic() + a.hours * 3600
    seq = 0
    while time.monotonic() < deadline and not (stop.is_set() or lost.is_set()):
        seq += 1
        send("src_lf", format_msg(a.run, seq, "lf2st"), "normal")
        if stop.wait(INTERVAL_S / 2) or lost.is_set():
            break
        send("src_st", format_msg(a.run, seq, "st2lf"), "normal")
        control = seq % CONTROL_EVERY in (REPEAT_AT, LOOP_AT)
        if control and stop.wait(CONTROL_GAP_S):   # never on top of a normal send
            break
        if seq % CONTROL_EVERY == REPEAT_AT:
            rep = format_msg(a.run, CONTROL_SEQ_BASE + seq, "lf2st")
            send("src_lf", rep, "repeat")
            if stop.wait(REPEAT_GAP_S):
                break
            send("src_lf", rep, "repeat")
        if seq % CONTROL_EVERY == LOOP_AT:
            send("src_st", loop_control_msg(a.run, CONTROL_SEQ_BASE + seq, "st2lf"), "loop")
        if stop.wait(INTERVAL_S / 2 - (CONTROL_GAP_S if control else 0)):
            break
    completed = time.monotonic() >= deadline and not lost.is_set()
    if not lost.is_set():
        record({"ev": "drain_start"})
        if stop.wait(DRAIN_S):   # cut short: last messages may still be in flight
            completed = False
    record({"ev": "traffic_end", "seq": seq, "link_lost": lost.is_set(),
            "completed": completed, "hours": a.hours})
    for iface in radios.values():
        _close(iface)
    del keep
    out.close()
    return 1 if lost.is_set() else 0


def load_run_events(d: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with open(d / "events.jsonl") as f:
        events += [json.loads(line) for line in f if line.strip()]
    with open(d / "bridge.log") as f:
        events += [e for e in map(bridge_event_from_log, f) if e]
    return events


def cmd_reconcile(a) -> int:
    d = _run_dir(a.run)
    events = load_run_events(d)
    r = reconcile(events, run=a.run)
    meta = Counter(e["ev"] for e in events
                   if e["ev"] in ("send_error", "link_lost", "traffic_end"))
    print(f"C21 run {a.run}: {r['verdict']}")
    print("labels: " + ", ".join(f"{k}={v}" for k, v in sorted(r["counts"].items())))
    for direction, legs in r["rf_loss"].items():
        print(f"rf loss {direction}: " + ", ".join(
            f"{leg} {lost}/{n}" + (f" ({lost / n:.1%})" if n else "")
            for leg, (lost, n) in legs.items()))
    for note in r["notes"]:
        print(f"⚠ {note}")
    if r["not_injected"]:
        print(f"⚠ {r['not_injected']} send(s) refused by the source radio (not injected, not counted)")
    finished = bool(meta.get("traffic_end"))
    if not finished:
        print("PROVISIONAL: no traffic_end record (traffic host still running, or died);"
              " in-flight messages read as unaccounted, so this is not a verdict")
    for key, lab in sorted(r["labels"].items(), key=lambda kv: kv[0][1]):
        if lab not in ("delivered", "control_ok"):
            near = r["coincident"].get(key)
            print(f"  {key[1]:>6} {key[2]} {lab}" + (f"  (overlapped: {near})" if near else ""))
    report = dict(r, labels={f"{k[1]} {k[2]}": v for k, v in r["labels"].items()},
                  coincident={f"{k[1]} {k[2]}": v for k, v in r["coincident"].items()})
    (d / "reconcile.json").write_text(json.dumps(report, indent=2) + "\n")
    return EXIT[r["verdict"]] if finished else 3


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("psk", help="create the lab channel key (once)")
    p = sub.add_parser("configure", help="write + read back one radio")
    p.add_argument("--role", choices=ROLES, required=True)
    p.add_argument("--port", required=True, help="/dev/serial/by-id/... path")
    p = sub.add_parser("bridge", help="start the run's bridge (transient unit)")
    p.add_argument("--run", required=True)
    p.add_argument("--leg-lf", required=True)
    p.add_argument("--leg-st", required=True)
    p = sub.add_parser("bridge-host")
    p.add_argument("--run", required=True)
    for name in ("traffic", "traffic-host"):
        p = (sub.add_parser(name, help="start scripted traffic (transient unit)")
             if name == "traffic" else sub.add_parser(name))
        p.add_argument("--run", required=True)
        p.add_argument("--src-lf", required=True)
        p.add_argument("--src-st", required=True)
        p.add_argument("--hours", type=float, default=24.0)
    p = sub.add_parser("reconcile", help="verdict for a run")
    p.add_argument("--run", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "configure" and not a.port.startswith(BY_ID):
        raise SystemExit(f"--port must be a {BY_ID} path")
    return {"psk": cmd_psk, "configure": cmd_configure, "bridge": cmd_bridge,
            "bridge-host": cmd_bridge_host, "traffic": cmd_traffic,
            "traffic-host": cmd_traffic_host, "reconcile": cmd_reconcile}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
