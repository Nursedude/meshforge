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

The journal is a MIXED channel (review r1 MED-1): meshtasticd logs a text's
first line on its "Received text msg" line, and journald stores every further
line of the text as its own raw entry — so a sender can type a line that
looks like a log line. A line counts only if it carries the full meshtasticd
prefix AND an uptime counter consistent with its own process (journal epoch
minus uptime is constant per process: measured 11-17 over 7 d, -0.51 s ..
+39 s for every receive/publish line; journald is only ever LATE). A line
that is a continuation of a PUBLISHED text is never a log line. A
prefix-carrying line that fails the uptime check is counted ``unverified``
and the page says the counts may be off (an NTP step shows here, not
silently). Residual, stated: a sender whose text is NOT published here (a
bridged one) and who knows this process's start time to the minute could
still forge one line.

Tri-state (honest_failure_modes #1/#2): ``ok`` with counts, or
``unobservable`` with why. Every working radio journal carries publish lines
(positions, telemetry), so NO verified publish line in the window is
unobservable — never "0 heard" (r1 MED-2/3: no permission, a vacuumed
journal, logging above INFO, JSON publishing off all look like nothing).
It shows counts, not the texts: showing them would make the journal a second
message source (a design decision, not this slice).
"""
from __future__ import annotations

import copy
import json
import logging
import re
import subprocess
import threading
import time
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

UNIT = "meshtasticd"
LOOKBACK_H = 24
CACHE_S = 60          # the page polls every 10 s; one journal scan per minute
_TIMEOUT_S = 10
# accepted (epoch - uptime) deviation from the process median, seconds.
# Measured 11-17 (7 d, 14 632 receive/publish lines): -0.51 .. +39.
_EARLY_S = 1.5
_LATE_S = 120.0

# `-o short-unix`: "<epoch> <host> <ident>[<pid>]: <message>"
_ENTRY = re.compile(r"^(\d+(?:\.\d+)?) \S+ [^\s\[]+\[(\d+)\]: (.*)$")
# a meshtasticd log line: "INFO  | 17:32:02 104520 [Router] ...". Only the
# Router thread's receives are texts HEARD over RF; a phone client's own send
# logs "Received text msg from=0x0" with no thread tag (17 in 24 h, 11-17) and
# is deliberately not counted (a no-uplink channel is the coverage line's job).
_PREFIX = re.compile(r"^(?:INFO|WARN|ERROR|DEBUG|TRACE|CRIT)\s*\| \d\d:\d\d:\d\d (\d+) \[Router\] (.*)$")
_RECV = re.compile(r"^Received text msg from=0x([0-9a-fA-F]+), id=0x([0-9a-fA-F]+), msg=")
_PUB = re.compile(r"^JSON publish message to \S+, \d+ bytes: (\{.*)$")   # a truncated line still counts
_GREP = "Received text msg from=|JSON publish message to"
_ID_IN_PREFIX = re.compile(r'"id":(\d+)[,}]')

_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": None, "value": None}


def _run(argv):   # the one seam tests replace
    return subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT_S)


def _reset_cache() -> None:
    with _lock:
        _cache["at"] = None
        _cache["value"] = None


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


def _continuations(body: str) -> List[str]:
    """Lines 2+ of a published text: what journald stored as raw entries."""
    try:
        text = json.loads(body).get("payload", {}).get("text")
    except (ValueError, AttributeError):
        return []
    return text.split("\n")[1:] if isinstance(text, str) else []


def _median(xs: List[float]) -> float:
    s = sorted(xs)
    return s[len(s) // 2]


def scan(lines: Iterable[str]) -> Dict[str, Any]:
    """Pure: count heard texts (by packet id) and those never published."""
    entries: List[Tuple[Optional[float], str, str]] = []   # (epoch, pid, message)
    for line in lines:
        e = _ENTRY.match(line)
        if e:
            entries.append((float(e.group(1)), e.group(2), e.group(3)))
        else:
            entries.append((None, "", line))
    # pass 1: candidate log lines, each process's uptime offset, and the
    # continuation lines every published text names
    cands = []
    offsets: Dict[str, List[float]] = defaultdict(list)
    named: Counter = Counter()   # a MULTISET: a copy never discards the genuine line too
    for ts, proc, msg in entries:
        p = _PREFIX.match(msg)
        if not p:
            continue                      # a raw continuation entry: never a log line
        rest = p.group(2)
        if ts is not None:
            offsets[proc].append(ts - int(p.group(1)))
        cands.append((ts, proc, int(p.group(1)), msg, rest))
        pub = _PUB.match(rest)
        if pub:
            named.update(_continuations(pub.group(1)))
    medians = {proc: _median(v) for proc, v in offsets.items()}
    heard: Dict[int, Dict[str, Any]] = {}
    published = set()
    unverified = 0
    verified_publishes = 0
    for ts, proc, n, msg, rest in cands:
        if named[msg] > 0:
            named[msg] -= 1
            continue                      # a published text's own line 2+, not a log line
        if ts is None or not (-_EARLY_S <= (ts - n) - medians[proc] <= _LATE_S):
            unverified += 1
            continue
        m = _RECV.match(rest)
        if m:
            pid = int(m.group(2), 16)
            if pid not in heard:          # first sighting wins
                heard[pid] = {"from": "!%08x" % int(m.group(1), 16), "ts": ts}
            continue
        pub = _PUB.match(rest)
        if pub:
            pid = _top_level_id(pub.group(1))
            if pid is None:
                unverified += 1           # never trusted as a pairing; counted (the page says so)
            else:
                published.add(pid)
                verified_publishes += 1
    if not verified_publishes:
        return {"state": "unobservable",
                "error": f"no MQTT publish line in the radio journal for {LOOKBACK_H} h "
                         "(unreadable here, logging above INFO, or JSON publishing off)"}
    unseen = [v for k, v in heard.items() if k not in published]
    last = None
    if unseen:
        newest = max(unseen, key=lambda v: (v["ts"] is not None, v["ts"] or 0.0))
        last = {"from": newest["from"], "ts": newest["ts"]}
    out: Dict[str, Any] = {"state": "ok", "heard": len(heard), "unseen": len(unseen),
                           "last": last}
    if unverified:
        out["unverified"] = unverified
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
    return scan(ln for ln in (proc.stdout or "").splitlines() if ln)


def read_unseen(now: Optional[float] = None) -> Dict[str, Any]:
    """Tri-state, cached for ``CACHE_S``; never raises; returns a copy."""
    t = time.monotonic() if now is None else now
    with _lock:
        at = _cache["at"]
        if at is None or not (0 <= t - at < CACHE_S):   # a clock that went back rescans
            try:
                value = _read_now()
            except Exception as e:   # a witness never takes the inbox down; it says why
                logger.warning("inbox: unseen scan failed: %s", e)
                value = {"state": "unobservable", "error": f"radio journal scan failed: "
                                                           f"{type(e).__name__}: {e}"}
            value["lookback_h"] = LOOKBACK_H
            _cache["at"], _cache["value"] = t, value
        return copy.deepcopy(_cache["value"])


def _hhmm(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return time.strftime("%H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return None


def describe(u: Dict[str, Any]) -> Tuple[str, str]:
    """The page's line and tone ("ok" / "warn") for one ``read_unseen`` result."""
    h = f"{u.get('lookback_h', LOOKBACK_H)} h"
    if u.get("state") != "ok":
        return (f"Radio journal: unobservable ({u.get('error') or 'no reason given'}) — this "
                "page cannot count texts the radio heard that are not on this page.", "warn")
    heard, unseen = u.get("heard", 0), u.get("unseen", 0)
    if unseen:
        line = (f"{unseen} of {heard} texts this radio heard in the last {h} are not on this "
                "page — they came in over MQTT (a bridge's downlink, which the firmware never "
                "re-publishes) or on a channel with no uplink.")
        last = u.get("last")
        if last:
            at = _hhmm(last.get("ts"))
            line += f" Last: {last.get('from')}" + (f" at {at}." if at else ".")
        tone = "warn"
    elif heard:
        line = (f"All {heard} texts this radio heard in the last {h} were published to MQTT, "
                "which this page reads.")
        tone = "ok"
    else:
        line, tone = f"The radio's journal shows no texts heard in the last {h}.", "ok"
    if u.get("unverified"):
        line += (f" ({u['unverified']} journal line(s) could not be verified, so these counts "
                 "may be off.)")
    return line, tone
