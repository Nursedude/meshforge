"""Texts this box's radio HEARD that /inbox cannot show (the downlink gap, 11-17).

/inbox reads the radio's MQTT uplink. meshtasticd publishes a received text
there unless the firmware refuses, and the refusals are SILENT — pinned in
v2.7.26.54e0d8d: ``MQTT::onSend`` returns for a packet that came via MQTT
(MQTT.cpp:768; the flag rides the RF header, RadioInterface.cpp:989 →
RadioLibInterface.cpp:602, so a bridge's MQTT-injected downlink is refused on
every radio that hears it) and for a channel without uplink (:798). Measured
11-17: 8 of 1365 texts in 7 days, all from one sender reaching LongFast only
as moc's RNS→Mesh downlink, were absent from /inbox with no witness.

This module is that witness. It reads the meshtasticd journal — the radio's
own record — and counts received texts whose packet id was never published.
The read is tri-state (honest_failure_modes #1/#2): ``ok`` with counts, or
``unobservable`` with why. It never says "0 heard" for a journal it could
not read. It shows counts, not the texts: showing them would make the journal
a second message source (a design decision, not this slice).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

UNIT = "meshtasticd"
LOOKBACK_H = 24
CACHE_S = 60          # the page polls every 10 s; one journal scan per minute
_TIMEOUT_S = 10

_RECV = re.compile(r"Received text msg from=0x([0-9a-fA-F]+), id=0x([0-9a-fA-F]+)")
_PUB = re.compile(r"JSON publish message to \S+, \d+ bytes: (\{.*)$")   # a truncated line still counts
_GREP = "Received text msg from=|JSON publish message to"
# journalctl's answer when the account may not read system logs: rc 0, no
# lines, this hint on stderr. Read as "nothing heard" it would be a lie.
_NO_SYSTEM_LOGS = "not seeing messages from other users and the system"

_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": None, "value": None}


def _run(argv):   # the one seam tests replace
    return subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT_S)


def _reset_cache() -> None:
    with _lock:
        _cache["at"] = None
        _cache["value"] = None


def _epoch(line: str) -> Optional[float]:
    head = line.split(" ", 1)[0]
    try:
        return float(head)
    except ValueError:
        return None


_ID_IN_PREFIX = re.compile(r'"id":(\d+)[,}]')


def _top_level_id(body: str) -> Optional[int]:
    """The packet id of one JSON publish, or None. TOP level only: a payload's
    own "id" is not a packet. meshtasticd truncates long log lines (measured
    11-17: traceroute publishes cut at char 444), so a body json cannot parse
    falls back to the part BEFORE the first nested object — the firmware
    writes top-level keys in order, and "id" sorts before "payload"."""
    try:
        obj = json.loads(body)
    except ValueError:
        head = body[1:].split("{", 1)[0]
        m = _ID_IN_PREFIX.search(head)
        return int(m.group(1)) if m else None
    pid = obj.get("id") if isinstance(obj, dict) else None
    return pid if isinstance(pid, int) and not isinstance(pid, bool) else None


def scan(lines: Iterable[str]) -> Dict[str, Any]:
    """Pure: count heard texts (by packet id) and those never published."""
    heard: Dict[int, Dict[str, Any]] = {}
    published = set()
    unparsed = 0
    for line in lines:
        m = _RECV.search(line)
        if m:
            pid = int(m.group(2), 16)
            if pid not in heard:
                heard[pid] = {"from": "!%08x" % int(m.group(1), 16), "ts": _epoch(line)}
            continue
        p = _PUB.search(line)
        if p:
            pid = _top_level_id(p.group(1))
            if pid is None:
                unparsed += 1            # never trusted as a pairing; counted (the page says so)
            else:
                published.add(pid)
    unseen = [v for k, v in heard.items() if k not in published]
    last = None
    if unseen:
        last = max(unseen, key=lambda v: (v["ts"] is not None, v["ts"] or 0.0))
        last = {"from": last["from"], "ts": last["ts"]}
    out: Dict[str, Any] = {"state": "ok", "heard": len(heard), "unseen": len(unseen),
                           "last": last}
    if unparsed:
        out["unparsed"] = unparsed
    return out


def _read_now() -> Dict[str, Any]:
    from utils.watchdog_probe_core import _journal_rc_unobservable
    argv = ["journalctl", "-u", UNIT, "--since", f"-{LOOKBACK_H}h", "-g", _GREP,
            "-o", "short-unix", "-q", "--no-pager"]
    try:
        proc = _run(argv)
    except subprocess.TimeoutExpired:
        return {"state": "unobservable", "error": f"radio journal read timed out ({_TIMEOUT_S}s)"}
    except OSError as e:
        return {"state": "unobservable", "error": f"radio journal unreadable: {e}"}
    if _journal_rc_unobservable(proc):
        return {"state": "unobservable",
                "error": f"radio journal query did not run (rc {proc.returncode})"}
    stdout = proc.stdout or ""
    if not stdout.strip() and _NO_SYSTEM_LOGS in (getattr(proc, "stderr", "") or ""):
        return {"state": "unobservable",
                "error": "this account cannot read system logs (the radio's journal)"}
    return scan(ln for ln in stdout.splitlines() if ln)


def read_unseen(now: Optional[float] = None) -> Dict[str, Any]:
    """Tri-state, cached for ``CACHE_S``; never raises."""
    t = time.monotonic() if now is None else now
    with _lock:
        at = _cache["at"]
        if at is not None and 0 <= t - at < CACHE_S:
            return _cache["value"]
        try:
            value = _read_now()
        except Exception as e:   # a witness never takes the inbox down; it says why
            logger.warning("inbox: unseen scan failed: %s", e)
            value = {"state": "unobservable", "error": f"radio journal scan failed: "
                                                       f"{type(e).__name__}: {e}"}
        value["lookback_h"] = LOOKBACK_H
        _cache["at"], _cache["value"] = t, value
        return value
