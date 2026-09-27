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
]
