"""Journeys for ``scripts/tui_journey.py`` — see its docstring for the contract.

Each entry: name, section, tag, path (scripted answers), why, check(text,
oracle) -> [(ok|None, msg)], plant(text) -> text. ``None`` = UNKNOWN (the
oracle could not answer), never agreement.

Oracle rule: an oracle must be a command the TUI does not use for that
fact, and must not import the TUI's parser for it (authorial distance) —
``rnpath`` lines are counted here with our own regex, not
``utils.node_counts.parse_rnpath_table``.
"""
from __future__ import annotations

import re
import time

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _clean(text):
    return _ANSI.sub("", text)


def _unit(oracle, unit):
    """(ActiveState, UnitFileState, active_since_epoch|None) or None if unreadable."""
    out = oracle(["systemctl", "show", unit, "-p", "ActiveState", "-p",
                  "UnitFileState", "-p", "ActiveEnterTimestampMonotonic"])
    if not out:
        return None
    kv = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    mono = int(kv.get("ActiveEnterTimestampMonotonic") or 0)
    since = None
    if mono:
        since = time.time() - (time.monotonic() - mono / 1e6)
    return kv.get("ActiveState"), kv.get("UnitFileState"), since


def _agree_state(name, claimed_up, oracle):
    u = _unit(oracle, name)
    if u is None:
        return (None, f"{name}: systemctl show unreadable — UNKNOWN")
    actual_up = u[0] == "active"
    word = "up" if claimed_up else "not up"
    return (claimed_up == actual_up,
            f"{name}: screen says {word}, systemd ActiveState={u[0]}")


# --- NOC Home -----------------------------------------------------------
_NOC_ROW = re.compile(r"^\s*\[\s*(UP|--|!!|\?\?|DN|DOWN|WARN)\s*\]\s+(\S+)\s+(.*)$", re.M)


def _check_noc_home(text, oracle):
    rows = {m.group(2): (m.group(1), m.group(3).strip()) for m in _NOC_ROW.finditer(_clean(text))}
    res = []
    for unit in ("meshtasticd", "rnsd", "meshforge-gateway"):
        if unit not in rows:
            res.append((False, f"{unit}: row missing from NOC Home HEALTH"))
            continue
        badge, _ = rows[unit]
        res.append(_agree_state(unit, badge == "UP", oracle))
    return res


def _plant_noc_home(text):
    return re.sub(r"\[ UP \] rnsd(\s+)running", r"[ -- ] rnsd\1off (disabled)", text, count=1)


# --- Service Status -----------------------------------------------------
_SVC_ROW = re.compile(r"^\s*●\s+(\S+)\s+(.+?)\s*$", re.M)


def _check_service_status(text, oracle):
    rows = _SVC_ROW.findall(_clean(text))
    if not rows:
        return [(False, "no service rows rendered")]
    return [_agree_state(name, state.lower().startswith(("running", "active")), oracle)
            for name, state in rows]


def _plant_service_status(text):
    return re.sub(r"(mosquitto\s+)running", r"\1stopped", text, count=1)


# --- Stack Health -------------------------------------------------------
_PATH_ROW = re.compile(r"RNS path table\s+(\d+) network destinations?, (\d+) local")
_UP_ROW = re.compile(r"^\[ OK \]\s+(rnsd|Local mesh radio)\s+(?:(\S+) )?active, ([\d.]+) (hr|min|d)", re.M)
_RNPATH_LINE = re.compile(r"^<[0-9a-f]+> is \d+ hops?\s+away", re.M)


def _check_stack_health(text, oracle):
    t = _clean(text)
    res = []
    m = _PATH_ROW.search(t)
    if not m:
        res.append((False, "RNS path table row missing or not a count"))
    else:
        shown = int(m.group(1)) + int(m.group(2))
        out = oracle(["rnpath", "--config", "/etc/reticulum", "-t"])
        if not out:
            res.append((None, "rnpath oracle failed — UNKNOWN"))
        else:
            actual = len(_RNPATH_LINE.findall(out))
            tol = max(3, actual // 10)   # the table moves between capture and oracle
            res.append((abs(shown - actual) <= tol,
                        f"path table: screen {shown} (net+ipc), rnpath -t {actual} (±{tol})"))
    for label, unit_word, n, u in _UP_ROW.findall(t):
        unit = "rnsd" if label == "rnsd" else (unit_word or "meshtasticd")
        hours = float(n) * {"hr": 1, "min": 1 / 60, "d": 24}[u]
        st = _unit(oracle, unit)
        if st is None or st[2] is None:
            res.append((None, f"{unit}: uptime unreadable — UNKNOWN"))
            continue
        actual_h = (time.time() - st[2]) / 3600
        tol = max(0.25, actual_h * 0.02)
        res.append((st[0] == "active" and abs(hours - actual_h) <= tol,
                    f"{unit}: screen active {hours:.2f} h, systemd {st[0]} {actual_h:.2f} h (±{tol:.2f})"))
    if len(res) < 3:
        res.append((False, f"expected path table + 2 uptime rows, judged {len(res)}"))
    return res


def _plant_stack_health(text):
    # The classic lie: a failed rnpath rendered as an empty table.
    return _PATH_ROW.sub("RNS path table            0 network destinations, 0 local", text, count=1)


# --- Set Owner (SANDBOX: writes to a SimRadio, never the real radio) -----
_OWNER_LONG, _OWNER_SHORT = "SANDBOX-OWNER", "SBX1"
_INFO = ["meshtastic", "--host", "localhost", "--info"]
_OWNER_LINE = re.compile(r"^Owner:\s*(.*?)\s*\(([^)]*)\)\s*$", re.M)


def _check_owner(text, oracle):
    t = _clean(text)
    res = []
    success = "[msgbox] Success" in t
    res.append((success, "screen reports Success" if success
                else "no Success screen after the write"))
    info = oracle(_INFO)
    m = _OWNER_LINE.search(info or "")
    if not m:
        return res + [(None, "read-back of the sim's owner failed — UNKNOWN")]
    dev_long, dev_short = m.group(1), m.group(2)
    # 1. the device holds what the operator TYPED (independent of the screen)
    res.append((dev_long == _OWNER_LONG and dev_short == _OWNER_SHORT,
                f"device owner {dev_long!r} ({dev_short!r}), typed "
                f"{_OWNER_LONG!r} ({_OWNER_SHORT!r})"))
    # 2. the screen's claim matches the device
    claimed = re.search(r"Long name: (.+)", t)
    res.append((bool(claimed) and claimed.group(1).strip() == dev_long,
                f"screen claims long name {claimed.group(1).strip() if claimed else None!r}, "
                f"device has {dev_long!r}"))
    return res


def _plant_owner(text):
    # The 09-20 class: the screen says one name, the radio holds another.
    return text.replace(f"Long name: {_OWNER_LONG}", "Long name: Meshtastic 8d30", 1)


# --- One-Enter hazards (SANDBOX): does accepting a pre-fill change the radio?
# A fresh sim's defaults (slot 0, root msh, unnamed ch0) equal the suspect
# pre-fills and would HIDE the hazard, so each journey's setup gives the sim a
# fleet-shaped state first (moc2/moc3: SHORT_TURBO slot 8; root msh/US/HI).
_H = ["meshtastic", "--host", "localhost"]
_SLOT, _ROOT = _H + ["--get", "lora.channel_num"], _H + ["--get", "mqtt.root"]
_CH0_NAME = re.compile(r'Index 0: PRIMARY[^\n]*?"name":\s*"([^"]*)"')


def _get_value(out, key):
    m = re.search(rf"^{re.escape(key)}:\s*(.*?)\s*$", out or "", re.M)
    return m.group(1) if m else None


def _ch0_name(info):
    if not info or "Index 0: PRIMARY" not in info:
        return None
    m = _CH0_NAME.search(info)
    return m.group(1) if m else ""       # key omitted = empty name


def _one_enter_check(read, argv, expect_before, label, claim_re):
    """Shared shape: (1) setup took (else UNKNOWN), (2) one Enter must not
    change the device, (3) every value the screen claims it set must be what
    the device holds (the planted-lie hook)."""
    def check(text, oracle):
        before, after = read(oracle(["BEFORE"] + argv)), read(oracle(argv))
        if before != expect_before:
            return [(None, f"setup did not take: {label} before={before!r}, "
                           f"wanted {expect_before!r} — UNKNOWN")]
        if after is None:
            return [(None, f"{label} read-back after failed — UNKNOWN")]
        res = [(after == before,
                f"{label}: before {before!r}, after one Enter {after!r}")]
        for claimed in re.findall(claim_re, _clean(text)):
            res.append((claimed.strip() == after,
                        f"screen claims {label} {claimed.strip()!r}, device has {after!r}"))
        return res
    return check


def _deliberate_check(read, argv, expect_before, wanted, label, claim_re):
    """A typed, confirmed change MUST reach the device — the other half of a
    one-Enter fix (a fix that broke writing would pass the one-Enter journey)."""
    def check(text, oracle):
        before, after = read(oracle(["BEFORE"] + argv)), read(oracle(argv))
        if before != expect_before:
            return [(None, f"setup did not take: {label} before={before!r} — UNKNOWN")]
        if after is None:
            return [(None, f"{label} read-back after failed — UNKNOWN")]
        res = [(after == wanted, f"{label}: before {before!r}, typed {wanted!r}, after {after!r}")]
        claims = re.findall(claim_re, _clean(text))
        res.append((bool(claims), f"screen reports the write ({len(claims)} claim(s))"))
        for claimed in claims:
            res.append((claimed.strip() == after,
                        f"screen claims {label} {claimed.strip()!r}, device has {after!r}"))
        return res
    return check


def _plant_claim(line):
    return lambda text: text + f"\n[msgbox] Success\n{line}\n"


JOURNEYS = [
    {"name": "noc_home", "section": "main", "tag": "n", "path": [],
     "why": "landing screen; review claims unknown maps to UP (noc_home.py:89-93)",
     "check": _check_noc_home, "plant": _plant_noc_home},
    {"name": "service_status", "section": "dashboard", "tag": "status", "path": [],
     "why": "per-service state vs systemd",
     "check": _check_service_status, "plant": _plant_service_status},
    {"name": "stack_health", "section": "dashboard", "tag": "stack_health", "path": [],
     "why": "review claims a failed rnpath reads as an empty table (fleet_health.py:202-219)",
     "check": _check_stack_health, "plant": _plant_stack_health},
    {"name": "set_owner", "section": "meshtasticd", "tag": "owner", "sandbox": True,
     "path": [{"kind": "inputbox", "answer": _OWNER_LONG},
              {"kind": "inputbox", "answer": _OWNER_SHORT},
              {"kind": "msgbox"}],
     "readback": [_INFO],
     "why": "the 09-20 owner-rename class: does the typed name reach the radio, "
            "and does the screen say what the radio holds?",
     "check": _check_owner, "plant": _plant_owner},
    {"name": "preset_one_enter", "section": "meshtasticd", "tag": "presets", "sandbox": True,
     "setup": [_H + ["--set", "lora.region", "US", "--set", "lora.modem_preset",
                     "SHORT_TURBO", "--set", "lora.channel_num", "8"]],
     "path": [{"kind": "menu", "pick": "SHORT_TURBO"},
              {"kind": "inputbox", "answer": "__INIT__"},
              {"kind": "yesno", "answer": True},
              {"kind": "msgbox"}],
     "readback": [_SLOT],
     "why": "audit: slot pre-fills '0' not the current slot (meshtasticd_radio.py:283) — "
            "re-applying the SAME preset moves a ch8 box off its segment",
     "check": _one_enter_check(lambda o: _get_value(o, "lora.channel_num"), _SLOT, "8",
                               "channel_num", r"Frequency slot: (\d+)"),
     "plant": _plant_claim("Frequency slot: 31")},
    {"name": "primary_channel_one_enter", "section": "configuration", "tag": "channels",
     "sandbox": True,
     "setup": [_H + ["--ch-index", "0", "--ch-set", "name", "Fleet0"]],
     "path": [{"kind": "menu", "pick": "primary"},
              {"kind": "inputbox", "answer": "__INIT__"},
              {"kind": "msgbox"}],
     "readback": [_H + ["--info"]],
     "why": "audit: Primary Channel pre-fills 'MeshForge', writes on one Enter with no "
            "confirm (channel_config.py:498-517) — renames the mesh's primary channel",
     "check": _one_enter_check(_ch0_name, _H + ["--info"], "Fleet0", "channel 0 name",
                               r"channel name to ([^.\n]+?)(?:\.\.\.|$)"),
     "plant": _plant_claim("Setting channel name to Evil0...")},
    {"name": "mqtt_root_one_enter", "section": "meshtasticd", "tag": "mqtt", "sandbox": True,
     "setup": [_H + ["--set", "mqtt.root", "msh/US/HI"]],
     "path": [{"kind": "menu", "pick": "topic"},
              {"kind": "inputbox", "answer": "__INIT__"},
              {"kind": "msgbox"}],
     "readback": [_ROOT],
     "why": "audit: MQTT root pre-fills 'msh' not the current root, writes on one Enter "
            "(meshtasticd_mqtt.py:270) — produces mqtt_root_drift",
     "check": _one_enter_check(lambda o: _get_value(o, "mqtt.root"), _ROOT, "msh/US/HI",
                               "mqtt.root", r"MQTT root topic set to: (\S+)"),
     "plant": _plant_claim("MQTT root topic set to: msh/EVIL")},
    {"name": "preset_deliberate", "section": "meshtasticd", "tag": "presets", "sandbox": True,
     "setup": [_H + ["--set", "lora.region", "US", "--set", "lora.modem_preset",
                     "SHORT_TURBO", "--set", "lora.channel_num", "8"]],
     "path": [{"kind": "menu", "pick": "SHORT_TURBO"},
              {"kind": "inputbox", "answer": "12"},
              {"kind": "yesno", "answer": True},
              {"kind": "msgbox"}],
     "readback": [_SLOT],
     "why": "the preset fix must still WRITE a typed slot",
     "check": _deliberate_check(lambda o: _get_value(o, "lora.channel_num"), _SLOT, "8", "12",
                                "channel_num", r"Frequency slot: (\d+)"),
     "plant": _plant_claim("Frequency slot: 31")},
    {"name": "primary_channel_deliberate", "section": "configuration", "tag": "channels",
     "sandbox": True,
     "setup": [_H + ["--ch-index", "0", "--ch-set", "name", "Fleet0"]],
     "path": [{"kind": "menu", "pick": "primary"},
              {"kind": "inputbox", "answer": "Fleet1"},
              {"kind": "yesno", "answer": True},
              {"kind": "msgbox"}],
     "readback": [_H + ["--info"]],
     "why": "the primary-channel fix must still WRITE a typed, confirmed name",
     "check": _deliberate_check(_ch0_name, _H + ["--info"], "Fleet0", "Fleet1",
                                "channel 0 name", r"channel name to ([^.\n]+?)(?:\.\.\.|$)"),
     "plant": _plant_claim("Setting channel name to Evil0...")},
    {"name": "mqtt_root_deliberate", "section": "meshtasticd", "tag": "mqtt", "sandbox": True,
     "setup": [_H + ["--set", "mqtt.root", "msh/US/HI"]],
     "path": [{"kind": "menu", "pick": "topic"},
              {"kind": "inputbox", "answer": "msh/US/MAUI"},
              {"kind": "yesno", "answer": True},
              {"kind": "msgbox"}],
     "readback": [_ROOT],
     "why": "the MQTT-root fix must still WRITE a typed, confirmed root",
     "check": _deliberate_check(lambda o: _get_value(o, "mqtt.root"), _ROOT, "msh/US/HI",
                                "msh/US/MAUI", "mqtt.root", r"MQTT root topic set to: (\S+)"),
     "plant": _plant_claim("MQTT root topic set to: msh/EVIL")},
]
