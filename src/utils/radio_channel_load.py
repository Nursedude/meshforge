"""Channel Load — how often the local radio found its channel over the 25 %
knee, its utilization samples, noise floor and decode failures. Read-only;
journal only; never opens a radio session (#17).

WHY (2026-10-03): the busiest fleet box logged 155 `Ch. util >25%. Skip send`
lines in 3 h and no utilization samples, and an operator could not see either
— the TUI had no pane for the channel at all. The first draft of this pane
then read "skips but no samples" as "over the knee the whole window"; two
contextless reviewers showed at the pinned firmware source that the samples'
absence means something else entirely (below), and the pane was rebuilt on
what each line actually proves.

What each leg is OF (pinned firmware v2.7.26 source, verified 2026-10-03):
  knee lines — `Ch. util >25%. Skip send` (airtime.cpp:123-131, polite gate)
               and `>40%` (impolite callers only: router/sensor telemetry,
               tracker position, NodeInfo replies). Position re-checks every
               5 s while over the knee whether or not a send was due, so the
               lines mark TIME over the knee, not lost messages. Only polite
               periodic broadcasts (position/telemetry/nodeinfo) are refused —
               text and relayed traffic are NOT gated. Reported as distinct
               minutes with ≥1 knee line: minutes TOUCHED, not a duration.
  samples    — `channel_utilization=` from `Send:` (every 60 s) and `Sending
               local stats` (every 15 min), logged ONLY while meshtasticd's
               to-phone queue is empty, i.e. an API client is draining it
               (DeviceTelemetry.cpp:26-43). No samples = no client attached,
               NOT a channel reading. A 60 s rolling window of the airtime of
               frames this radio locked onto (airtime.h:28, airtime.cpp:13-33):
               interference it cannot decode is invisible to it. Deduped by
               uptime (the two lines are twins).
  noise      — `noise_floor=` averaged over ≤20 idle samples taken one per
               stats call (a multi-hour average); -120 = NOISE_FLOOR_DEFAULT =
               no sample yet, never a reading.
  decode fails — delta of `num_packets_rx_bad` / `num_packets_rx`: CRC errors,
               corrupt and short frames. Collisions are one cause; weak
               signals and interference are others. Counters restart with the
               radio — on process start AND on the USB-radio in-process LoRa
               recovery, which does NOT reset uptime — so a decrease in either
               counter restarts the delta, and the span it covers is stated.
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set, Tuple

from utils.observation import Failed, Observation, Seen, Unobservable, assert_never

UNIT = "meshtasticd"
KNEE_PCT = 25.0
HARD_PCT = 40.0
NOISE_FLOOR_DEFAULT = -120
MOSTLY = 0.5
JOURNAL_TIMEOUT_S = 20
JOURNAL_GREP = "Sending local stats|num_packets_tx|DeviceTelemetry\\] Send: |Ch\\. util >"

_RE_TS = re.compile(r"^(\d+(?:\.\d+)?)\s")
_RE_STATS = re.compile(r"Sending local stats: uptime=(\d+), channel_utilization=([\d.]+), "
                       r"air_util_tx=([\d.]+), num_online_nodes=(\d+).*?noise_floor=(-?\d+)")
_RE_SEND = re.compile(r"\] Send: air_util_tx=([\d.]+), channel_utilization=([\d.]+), .*uptime=(\d+)")
_RE_COUNTERS = re.compile(r"num_packets_tx=\d+, num_packets_rx=(\d+), num_packets_rx_bad=(\d+)")
_RE_SKIP = re.compile(r"\[(\w+)\] Ch\. util >(\d+)%\. Skip send")


@dataclass
class LoadFacts:
    util_samples: List[float] = field(default_factory=list)   # deduped by uptime
    air_tx_latest: Optional[float] = None
    knee_minutes: int = 0                  # distinct minutes with a >25% line
    hard_minutes: int = 0                  # distinct minutes with a >40% line
    knee_by_module: dict = field(default_factory=dict)
    noise_floor: Optional[int] = None      # None also when the firmware default -120
    noise_default_seen: bool = False
    online_nodes: Optional[int] = None
    rx_delta: Optional[int] = None
    rx_bad_delta: Optional[int] = None
    delta_since: Optional[float] = None    # ts of the counter baseline
    first_ts: Optional[float] = None


def parse_journal(lines: Iterable[str]) -> LoadFacts:
    f = LoadFacts()
    seen_uptimes: Set[int] = set()
    knee_min: Set[int] = set()
    hard_min: Set[int] = set()
    base: Optional[Tuple[int, int, float]] = None     # rx, rx_bad, ts
    latest: Optional[Tuple[int, int]] = None
    for raw in lines:
        tm = _RE_TS.match(raw)
        if not tm:
            continue
        ts = float(tm.group(1))
        if f.first_ts is None:
            f.first_ts = ts
        m = _RE_STATS.search(raw)
        if m:
            up = int(m.group(1))
            if up not in seen_uptimes:
                seen_uptimes.add(up)
                f.util_samples.append(float(m.group(2)))
            f.air_tx_latest = float(m.group(3))
            f.online_nodes = int(m.group(4))
            nf = int(m.group(5))
            if nf == NOISE_FLOOR_DEFAULT:
                f.noise_default_seen = True
                f.noise_floor = None
            else:
                f.noise_floor = nf
            continue
        m = _RE_SEND.search(raw)
        if m:
            up = int(m.group(3))
            if up not in seen_uptimes:
                seen_uptimes.add(up)
                f.util_samples.append(float(m.group(2)))
            f.air_tx_latest = float(m.group(1))
            continue
        m = _RE_COUNTERS.search(raw)
        if m:
            rx, bad = int(m.group(1)), int(m.group(2))
            if latest is not None and (rx < latest[0] or bad < latest[1]):
                base = None                          # counters restarted (process or LoRa recovery)
            if base is None:
                base = (rx, bad, ts)
            latest = (rx, bad)
            continue
        m = _RE_SKIP.search(raw)
        if m:
            minute = int(ts // 60)
            mod = m.group(1)
            f.knee_by_module[mod] = f.knee_by_module.get(mod, 0) + 1
            if int(m.group(2)) >= int(HARD_PCT):
                hard_min.add(minute)
            knee_min.add(minute)
    f.knee_minutes, f.hard_minutes = len(knee_min), len(hard_min)
    if base is not None and latest is not None and latest[0] > base[0]:
        f.rx_delta = latest[0] - base[0]
        f.rx_bad_delta = latest[1] - base[1]
        f.delta_since = base[2]
    return f


@dataclass(frozen=True)
class Verdict:
    # OK | CROSSES KNEE | MOSTLY OVER KNEE | OVER 40% | UNOBSERVABLE | ABSENT
    status: str
    lines: List[str]


def _hm(ts: Optional[float]) -> str:
    return "?" if ts is None else time.strftime("%H:%M", time.localtime(ts))


def verdict(journal: Observation[LoadFacts], window_h: float,
            service: Observation[str] = Seen("running")) -> Verdict:
    match service:
        case Seen(value="absent"):
            return Verdict("ABSENT", ["meshtasticd is not installed on this box — no channel to measure "
                                      "(absent by design is not a fault)."])
        case Seen(value=_):
            pass
        case Unobservable(why=swhy) | Failed(why=swhy):
            return Verdict("UNOBSERVABLE", [f"service  : {swhy.split(' — ')[0]} — a stopped radio measures "
                                            "nothing; older journal lines are not the channel now"])
        case _ as unreachable_s:
            assert_never(unreachable_s)
    match journal:
        case Seen(value=facts):
            return _judge(facts, window_h)
        case Unobservable(why=why) | Failed(why=why):
            return Verdict("UNOBSERVABLE", [f"journal  : {why}"])
        case _ as unreachable:
            assert_never(unreachable)


def _judge(f: LoadFacts, window_h: float) -> Verdict:
    window_min = max(1, int(window_h * 60))
    span = "" if f.first_ts is None else f" (earliest matching line {_hm(f.first_ts)})"
    lines: List[str] = [f"window   : last {window_h:g} h{span}"]
    if f.knee_minutes:
        mods = ", ".join(f"{k} {v}" for k, v in sorted(f.knee_by_module.items()))
        lines.append(f"over knee: in ≥{f.knee_minutes} of {window_min} minutes the firmware found the channel "
                     f"over 25% at least once and refused a polite periodic broadcast ({mods}) — minutes touched, "
                     "NOT a duration (one check in a minute counts the minute). Text and relayed traffic are NOT throttled.")
        if f.hard_minutes:
            lines.append(f"over 40% : in ≥{f.hard_minutes} minutes (seen only by router/sensor/tracker roles "
                         "and NodeInfo replies)")
    else:
        lines.append("over knee: no knee line in the window")
    s = f.util_samples
    if s:
        over = sum(1 for x in s if x >= KNEE_PCT)
        lines.append(f"samples  : {len(s)} (60 s rolling utilization): latest {s[-1]:.1f}%, max {max(s):.1f}%, "
                     f"{over} at/over 25%")
        if f.air_tx_latest is not None:
            lines.append(f"own TX   : {f.air_tx_latest:.2f}% of airtime — the rest of the utilization is the "
                         "mesh's traffic, not this box's")
    else:
        lines.append("samples  : none — logged only while an API client drains meshtasticd's queue; "
                     "NOT a channel reading")
    if f.noise_floor is not None:
        lines.append(f"noise    : {f.noise_floor} dBm (radio-reported multi-hour average, uncalibrated)")
    elif f.noise_default_seen:
        lines.append("noise    : no sample yet (-120 is the firmware default, not a reading)")
    if f.online_nodes is not None:
        lines.append(f"nodes    : {f.online_nodes} online (radio's own count)")
    if f.rx_delta and f.rx_bad_delta is not None and f.rx_bad_delta >= 0:
        pct = 100.0 * f.rx_bad_delta / f.rx_delta
        lines.append(f"decode fails: {f.rx_bad_delta}/{f.rx_delta} received frames since {_hm(f.delta_since)} "
                     f"({pct:.1f}%) — CRC/corrupt/short; collisions are one cause, weak signals and "
                     "interference others")
    lines.append("model    : if this mesh behaved like pure ALOHA, 25% busy ≈ G 0.29 ≈ 56% of packets "
                 "surviving — a model, not a measurement (LoRa listens first; utilization ≠ offered load)")

    over_frac = f.knee_minutes / window_min
    sample_over_frac = (sum(1 for x in s if x >= KNEE_PCT) / len(s)) if s else 0.0
    if f.hard_minutes or any(x >= HARD_PCT for x in s):
        return Verdict("OVER 40%", lines)
    if over_frac >= MOSTLY or (s and sample_over_frac >= MOSTLY):
        return Verdict("MOSTLY OVER KNEE", lines)
    if f.knee_minutes or any(x >= KNEE_PCT for x in s):
        return Verdict("CROSSES KNEE", lines)
    if s:
        return Verdict("OK", lines)
    lines.append("cannot tell: no sample (no API client attached) and no knee line (which only appears "
                 "OVER 25%) — a quiet channel and an unwatched one look the same here")
    return Verdict("UNOBSERVABLE", lines)


def read_journal(window_h: float) -> Observation[LoadFacts]:
    try:
        r = subprocess.run(["journalctl", "-u", UNIT, "--since", f"-{int(window_h * 3600)}s",
                            "--no-pager", "-o", "short-unix", "--grep", JOURNAL_GREP],
                           capture_output=True, text=True, timeout=JOURNAL_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Unobservable(f"journalctl: {e.__class__.__name__}")
    err = (r.stderr or "").strip()
    if r.returncode not in (0, 1) or (r.returncode == 1 and err):
        tail = err.splitlines()[-1:] or [""]
        return Unobservable(f"journalctl rc={r.returncode}: {tail[0][:140]}")
    return Seen(parse_journal(r.stdout.splitlines()))


def read_all(window_h: float = 3) -> Verdict:
    from utils.radio_txpower_truth import read_service
    return verdict(read_journal(window_h), window_h, read_service())
