"""RNode Interference — the RNode's own interference flag, sampled a few times,
shown WITH what it is of and what it may cost. Read-only: `rnstatus -j`
against the local shared instance (require_shared_instance — it can never
open the serial port itself).

WHY (2026-10-03): a fleet RNode reported "interference -71 dBm" 65-96 % of the
time. Two independent SDRs placed beside it (different receivers, antennas and
positions; n=84 paired polls) measured NO matching energy in the air. The
number is real — the radio really asserts it — but it is not proof of RF.

What the number is OF (read at source 2026-10-03, re-checked by two reviewers):
  RNS RNodeInterface.py:967-971 — r_interference = ntf - 157; ntf == 0xFF →
      None. RNS's own filter is commented out (:950-965). Updated only when a
      CHTM stats frame arrives (~1 Hz and after each TX); while asserted,
      interference_last_ts is re-stamped on every frame. NOT cleared when the
      port drops — a down interface keeps exporting its last value. Exported
      by Reticulum.py:1428-1435. noise_floor/interference stay None until the
      first CHTM: AVR or old-firmware RNodes never send one.
  RNode_Firmware (upstream HEAD 56775c51; THIS radio's firmware version is not
      readable without the serial port rnsd holds — unverified):
      :1415   interference_detected = !carrier_detected
                                      && current_rssi > noise_floor + 11 dB
              (Heltec V4 boards use an absolute LNA limit below a threshold)
      Utilities.h:967  reported value = the INSTANTANEOUS RSSI at that moment
      :1358-1385  noise_floor = mean of 128 carrier-free RSSI samples below
                  floor + 11 dB (self-referential)
      :1421-1429  asserted BELOW -83 dBm for >= 2.5 s → the floor is thrown
                  away and re-learned (weak assertions clear themselves; an
                  assertion above -83 dBm never triggers this and persists)
      :1351   if avoid_interference is set, an asserted flag makes the channel
              "not free" and the RNode DEFERS its own TX. avoid_interference is
              read from EEPROM at boot (:283-295): ON only if that byte is
              0x00; an erased/autoinstalled EEPROM (0xFF) reads OFF, and
              `rnodeconf -x/-X` sets it. Not readable here — the cost is
              stated conditionally, never as fact.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from utils.observation import Failed, Observation, Seen, Unobservable, assert_never

RNSTATUS_TIMEOUT_S = 20
TOTAL_DEADLINE_S = 35.0
FW_REF = "upstream RNode firmware 56775c51"
FRESH_S = 10.0          # an asserted value whose last_ts is older than this is a frozen frame
MIN_FOR_MOSTLY = 3


class _BadShape(Exception):
    pass


@dataclass(frozen=True)
class RNodeReading:
    name: str
    kind: str                         # RNodeInterface | RNodeMulti… | RNodeSub…
    up: bool
    interference: Optional[int]       # asserted at the radio's last frame (None = 0xFF)
    last_dbm: Optional[int]
    last_ts: Optional[float]          # rnsd wall clock
    noise_floor: Optional[int]        # None = no CHTM frame received yet
    load_long: Optional[float]
    bitrate: Optional[float]
    taken_at: float                   # when this sample was taken (same box, same clock)


def _num(d: dict, k: str) -> Optional[float]:
    v = d.get(k)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _int(d: dict, k: str) -> Optional[int]:
    v = _num(d, k)
    return None if v is None else int(v)


def parse_snapshot(doc: Any, taken_at: float) -> List[RNodeReading]:
    """RNode-family entries from one `rnstatus -j` document. A document of
    the wrong SHAPE raises — it must never read as "no RNode here"."""
    if not isinstance(doc, dict) or not isinstance(doc.get("interfaces"), list):
        raise _BadShape()
    out: List[RNodeReading] = []
    for i in doc["interfaces"]:
        if not isinstance(i, dict):
            continue
        kind = str(i.get("type", ""))
        if not kind.startswith("RNode"):
            continue
        out.append(RNodeReading(
            name=str(i.get("name", "?")), kind=kind, up=bool(i.get("status")),
            interference=_int(i, "interference"), last_dbm=_int(i, "interference_last_dbm"),
            last_ts=_num(i, "interference_last_ts"), noise_floor=_int(i, "noise_floor"),
            load_long=_num(i, "channel_load_long"), bitrate=_num(i, "bitrate"), taken_at=taken_at))
    return out


@dataclass(frozen=True)
class Verdict:
    status: str      # NONE | EARLIER | ASSERTED | MOSTLY ASSERTED | DOWN | UNOBSERVABLE | ABSENT
    lines: List[str]


@dataclass
class Sampling:
    snaps: List[List[RNodeReading]] = field(default_factory=list)
    stopped_why: Optional[str] = None     # a failure part-way through, kept as a witness


def _ago(ts: float, now: float) -> str:
    d = now - ts
    if d < -5:
        return "a time in the FUTURE — the clock stepped; not usable"
    d = max(0.0, d)
    return f"{d:.0f} s ago" if d < 120 else (f"{d / 60:.0f} min ago" if d < 7200 else f"{d / 3600:.1f} h ago")


CAVEAT = [
    "what it is: the radio's own figure — in-band RSSI more than 11 dB over its own floor at a",
    "  moment with no LoRa carrier; the value is that instant's RSSI. Not proof of RF in the air:",
    "  conducted noise (USB cable, supply, the board) or the radio's own floor estimate can assert",
    "  it too. An external receiver beside the antenna is evidence (not proof — different",
    f"  bandwidth and sensitivity). ({FW_REF}; this radio's firmware version is unverified.)",
    "  The floor shown is the floor NOW; the floor at the moment of an assertion is not exported.",
    "  Assertions weaker than -83 dBm make the firmware re-learn its floor; stronger ones persist.",
    "what it may cost: IF interference avoidance is enabled in this radio's EEPROM (not readable",
    "  here; `rnodeconf -i` on the stopped port shows it; an autoinstalled RNode reads Disabled),",
    "  the RNode defers its own transmissions while the flag is asserted.",
]


def verdict(sampling: Observation[Sampling], now: float) -> Verdict:
    match sampling:
        case Seen(value=s):
            return _judge(s, now)
        case Unobservable(why=why) | Failed(why=why):
            return Verdict("UNOBSERVABLE", [f"rnstatus : {why}"])
        case _ as unreachable:
            assert_never(unreachable)


def _judge(s: Sampling, now: float) -> Verdict:
    snaps = s.snaps
    if not snaps:
        return Verdict("UNOBSERVABLE", [f"rnstatus : no sample was taken"
                                        + (f" — {s.stopped_why}" if s.stopped_why else "")])
    names = sorted({r.name for snap in snaps for r in snap})
    if not names:
        return Verdict("ABSENT", ["no RNode on this box's shared RNS instance — absent by design is not a fault"])
    rank = {"NONE": 0, "EARLIER": 1, "UNOBSERVABLE": 2, "ASSERTED": 3, "MOSTLY ASSERTED": 4, "DOWN": 5}
    states: List[str] = []
    lines: List[str] = [f"samples  : {len(snaps)} taken"
                        + (f" — sampling STOPPED early: {s.stopped_why}" if s.stopped_why else "")]
    for name in names:
        rs = [r for snap in snaps for r in snap if r.name == name]
        latest = rs[-1]
        lines.append(name)
        if not latest.kind.startswith("RNodeInterface"):
            lines.append(f"  type     : {latest.kind} — present, but this interface type does not export "
                         "interference")
            states.append("UNOBSERVABLE")
            continue
        live = [r for r in rs if r.up]
        down = len(rs) - len(live)
        if not live:
            lines.append("  status   : DOWN in every sample — its last figures are stale, not shown")
            states.append("DOWN")
            continue
        if all(r.noise_floor is None for r in live):
            lines.append("  stats    : the radio has sent no channel stats (old/AVR firmware, or just "
                         "opened) — interference is unobservable, NOT clean")
            states.append("UNOBSERVABLE")
            continue
        fresh = [r for r in live if r.interference is not None and r.last_ts is not None
                 and -FRESH_S <= r.taken_at - r.last_ts <= FRESH_S]
        stale = [r for r in live if r.interference is not None and r not in fresh]
        n = len(live)
        if latest.bitrate is not None:
            lines.append(f"  rate     : {latest.bitrate / 1000:.2f} kbps")
        nf = next((r.noise_floor for r in reversed(live) if r.noise_floor is not None), None)
        lines.append(f"  floor    : {nf} dBm now (the radio's own estimate)")
        if down:
            lines.append(f"  status   : DOWN in {down} of {len(rs)} samples — counted over the {n} up samples only")
        if fresh:
            vals = [r.interference for r in fresh if r.interference is not None]
            lo, hi = min(vals), max(vals)
            span = f"{hi} dBm" if lo == hi else f"{lo}..{hi} dBm"
            lines.append(f"  interference: asserted in {len(fresh)} of {n} live samples at {span} "
                         "(small n — a duty estimate, not a measurement)")
        if stale:
            lines.append(f"  frozen   : {len(stale)} sample(s) carried an asserted value from an OLD frame "
                         "(stats stopped arriving) — not counted")
        if not fresh:
            if latest.last_ts is None:
                lines.append(f"  interference: none in {n} live samples; none reported since rnsd started")
            else:
                lines.append(f"  interference: none now; last {latest.last_dbm} dBm {_ago(latest.last_ts, now)}")
        if latest.load_long is not None:
            lines.append(f"  ch. load : {latest.load_long:.2f}% (long window, the radio's own)")
        if fresh:
            state = "MOSTLY ASSERTED" if (2 * len(fresh) >= n and n >= MIN_FOR_MOSTLY) else "ASSERTED"
        elif stale and not fresh:
            state = "UNOBSERVABLE"
        else:
            state = "EARLIER" if latest.last_ts is not None else "NONE"
        states.append(state)
    worst = max(states, key=rank.__getitem__)
    if any(st in ("ASSERTED", "MOSTLY ASSERTED", "EARLIER") for st in states):
        lines += CAVEAT
    return Verdict(worst, lines)


def _rnstatus_bin() -> Optional[str]:
    from utils.rns_status_parser import _find_rnstatus_binary
    return _find_rnstatus_binary()


def read_snapshot() -> Observation[List[RNodeReading]]:
    exe = _rnstatus_bin()
    if exe is None:
        return Unobservable("rnstatus is not installed on this box")
    try:
        r = subprocess.run([exe, "-j"], capture_output=True, text=True, timeout=RNSTATUS_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return Unobservable(f"rnstatus timed out after {RNSTATUS_TIMEOUT_S} s — rnsd RPC may be wedged, "
                            "or the box is CPU-starved")
    except OSError as e:
        return Unobservable(f"rnstatus could not run: {e.__class__.__name__}")
    taken = time.time()
    if r.returncode != 0:
        tail = ((r.stderr or r.stdout or "").strip().splitlines() or [""])[-1]
        return Unobservable(f"rnstatus rc={r.returncode}: {tail[:140]}")
    try:
        return Seen(parse_snapshot(json.loads(r.stdout), taken))
    except ValueError as e:
        return Failed(f"rnstatus -j output is not JSON ({e.__class__.__name__})")
    except _BadShape:
        return Failed("rnstatus -j output has no `interfaces` list — unknown format, not 'no RNode'")


def sample(samples: int = 5, gap_s: float = 5.0,
           reader: Callable[[], Observation[List[RNodeReading]]] = read_snapshot,
           sleep: Callable[[float], None] = time.sleep,
           clock: Callable[[], float] = time.monotonic) -> Observation[Sampling]:
    """Up to `samples` reads, `gap_s` apart, under a TOTAL deadline. A failure
    before any read is the answer; a failure part-way is kept as a witness."""
    start = clock()
    out = Sampling()
    for k in range(samples):
        obs = reader()
        match obs:
            case Seen(value=rs):
                out.snaps.append(rs)
            case Unobservable(why=why) | Failed(why=why):
                if not out.snaps:
                    return obs if isinstance(obs, Failed) else Unobservable(why)
                out.stopped_why = f"sample {k + 1}/{samples}: {why}"
                break
            case _ as unreachable:
                assert_never(unreachable)
        if k < samples - 1:
            if clock() - start + gap_s > TOTAL_DEADLINE_S:
                out.stopped_why = f"time budget ({TOTAL_DEADLINE_S:.0f} s) reached after {k + 1} samples"
                break
            sleep(gap_s)
    return Seen(out)


def read_all(samples: int = 5, gap_s: float = 5.0) -> Verdict:
    return verdict(sample(samples, gap_s), time.time())
