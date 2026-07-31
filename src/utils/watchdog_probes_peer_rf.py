"""Peer RF witness — "is our transmitter on the air?" for boxes no claw can hear.

WHY THIS EXISTS (2026-07-30)
----------------------------
The dude-claws are the fleet's only INDEPENDENT physical-layer witness: a
separate radio on separate silicon, reporting what it actually heard, which no
box can fabricate about itself. ``probe_claw_watched_node_silent`` turns that
into the mute-transmitter detector.

But the claws listen on LONG_FAST/ch20, and this fleet is deliberately
two-preset — moc2 and moc3 run SHORT_TURBO/ch8 (the throughput leg for
``ST<>meshforge<>RNS``). Different bandwidth, spreading factor and centre
frequency: the claws cannot demodulate those two boxes and never could. So the
gateway pair carrying the RNS leg had NO RF witness at all, which is precisely
the blind spot the ears were built to close, left open on the boxes that matter
most for RNS. (Measured the same day: the claw watch list had been pointed at
both of them and was reporting `silent` — see ``claw_rf_watch``'s segment gate.)

THE WITNESS THAT ALREADY EXISTS. moc2 and moc3 hear EACH OTHER — measured at
-17 dBm. A box reporting what it heard from a DIFFERENT box is independent
evidence in exactly the way a box reporting on itself is not. That costs no new
hardware and no new listener: it is a reading we were already able to take and
simply never took.

SOURCE OF TRUTH: the local meshtasticd JOURNAL, deliberately.
    * ``nodes.proto`` on disk is a periodically-flushed SNAPSHOT — measured
      2026-07-30 reporting a peer last heard 54.7 DAYS ago while that peer was
      being received continuously. A stale file that looks like live state is
      the worst possible input to a silence detector.
    * ``node_cache.json`` on a gateway-only box was 95 days stale for the same
      reason (nothing there refreshes it).
    * The journal is written by the consumer-of-record as packets actually
      arrive, and journal-only reading is the established pattern here
      (``mqtt_root_drift``, #77) precisely because it never touches the radio
      and so cannot steal a PhoneAPI packet (#17).

The scan is INCREMENTAL — a short window each tick, folding into a persisted
last-heard — rather than re-reading the full silence window every time. On a
905 MB Pi already paying ~4.3 s of CPU per watchdog tick, a repeated multi-hour
journal scan is not a rounding error.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from utils.watchdog_probe_core import (
    Signal,
    _load_parity_streak,
    _save_parity_streak,
    note_disposition,
)
from utils.watchdog_probes_liveness import _operator_home

DEFAULT_PEER_RF_STATE_PATH = "/var/lib/meshforge/peer_rf_witness.json"

#: Per-tick journal window. Must comfortably exceed the watchdog cadence so a
#: late tick cannot skip a reception; anything heard inside it refreshes the
#: persisted last-heard.
JOURNAL_WINDOW_S = 30 * 60

#: Config naming the segment this box is on and the peers it should be hearing.
PEER_CONFIG_BASENAME = "rf_segment_peers.json"

_NODE_RE = re.compile(r"^![0-9a-fA-F]{8}$")


def _config_path(home: Optional[str] = None) -> Optional[str]:
    """Where this box declares its segment peers.

    Falls back to the OPERATOR's home, not the caller's: the watchdog runs as
    root, so the effective-user home resolves to /root (the MF001 trap), where
    no operator config has ever been written. Returning None on an unresolvable
    home is correct — it degrades to INERT, which is honest — but the DEFAULT
    must resolve, or the probe is inert on every box that ever configures it.

    That is not hypothetical: shipped 2026-07-30 without this fallback, and the
    probe reported "no RF segment peers declared" on the two boxes whose configs
    had just been placed and validated. Reader and writer both shipped and never
    met (honest_failure_modes #4). Every unit test passed an explicit path, so
    the only resolution production uses was the only one untested — caught by
    running the real probe on the real box, not by the suite.
    """
    base = home or _operator_home()
    if not base:
        return None
    return os.path.join(base, ".config", "meshforge", PEER_CONFIG_BASENAME)


def load_peer_config(path: Optional[str]) -> Tuple[Optional[Dict[str, str]], Optional[str], Optional[str]]:
    """``(peers, segment, error)`` from the declaration file.

    Absent file → ``(None, None, None)``: this box declares no peers, the probe
    is INERT, and that is a legitimate state (most boxes have a claw, or are not
    on a witnessed segment). Absence of a declaration is not a finding.

    A file that EXISTS but cannot be read or has no peers is an ERROR, not an
    empty peer set. An empty ruleset that reads as "no conditions" is the
    canonical fail-dark shape (honest_failure_modes #1/#3): it would render this
    probe permanently silent on exactly the box someone had configured it for.
    """
    if not path or not os.path.exists(path):
        return None, None, None
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        return None, None, "peer config unreadable (%s)" % e
    if not isinstance(doc, dict):
        return None, None, "peer config is not an object"
    peers = doc.get("peers")
    if not isinstance(peers, dict) or not peers:
        return None, None, ("peer config declares no 'peers' — an empty peer set "
                            "would read as 'nothing to watch', which is the "
                            "failure this file exists to prevent")
    clean: Dict[str, str] = {}
    for node, label in peers.items():
        if not _NODE_RE.match(str(node)):
            return None, None, "peer id %r is not a !xxxxxxxx node id" % (node,)
        clean[str(node)] = str(label)
    return clean, doc.get("segment"), None


def meshtasticd_uptime_s(now: float) -> Optional[float]:
    """Seconds since meshtasticd became active, or None if unknowable.

    This is the LISTENING WINDOW, the same role the claw's uptime plays: the
    journal only holds what we received while we were running, so a daemon that
    restarted twenty minutes ago has not been listening long enough for silence
    to mean anything. Unknown uptime is unobservable, never "long enough".
    """
    try:
        out = subprocess.run(
            ["systemctl", "show", "-p", "ActiveEnterTimestampMonotonic",
             "--value", "meshtasticd"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    raw = (out.stdout or "").strip()
    if not raw.isdigit() or raw == "0":
        return None
    # Monotonic microseconds since boot — deliberately NOT the wall-clock
    # timestamp. These Pis are RTC-less and NTP steps the clock after boot, so a
    # wall-clock delta can read negative or absurd (honest_failure_modes #6).
    try:
        with open("/proc/uptime") as f:
            boot_age = float(f.read().split()[0])
    except (OSError, ValueError):
        return None
    up = boot_age - (int(raw) / 1_000_000.0)
    return up if up >= 0 else None


def scan_journal_for_peers(peer_ids: List[str], window_s: int = JOURNAL_WINDOW_S,
                           _runner=None) -> Tuple[Optional[set], Optional[str]]:
    """``(set_of_ids_seen, error)`` from the meshtasticd journal.

    Matches the packet ORIGINATOR field (``from=0x<num>``). A read failure
    returns an ERROR and an empty-but-distinct result — never an empty set,
    which the caller would otherwise be free to read as "heard nothing" and
    convert into silence manufactured out of a broken observation channel.
    """
    runner = _runner or subprocess.run
    try:
        out = runner(
            ["journalctl", "-u", "meshtasticd", "--no-pager",
             "--since", "-%ds" % int(window_s), "-o", "cat"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return None, "journal read failed (%s)" % e
    if getattr(out, "returncode", 0) not in (0, None):
        return None, "journalctl exited %s" % out.returncode
    text = (out.stdout or "").lower()
    seen = set()
    for node in peer_ids:
        # meshtasticd logs the originator %x-UNPADDED — live journal shows
        # `from=0x2ecc800` (7 hex) and diag24h_parser zfill(8)s what it reads
        # — while node ids are 8-hex zero-padded, so a padded needle can NEVER
        # match a peer whose id starts with a zero nibble: its last-heard
        # never refreshes and the probe pages a permanent false silence
        # (review 2026-07-31, finding 4). Strip the padding, and bound the
        # match so a 7-hex needle cannot prefix-match a longer originator.
        hexpart = node[1:].lower().lstrip("0") or "0"
        if re.search(r"from=0x%s(?![0-9a-f])" % hexpart, text):
            seen.add(node)
    return seen, None


#: In-process copy, memory-first — the ``_streak_mem_fallback`` pattern
#: (2026-07-26 drill): a broken state dir usually keeps the OLD file readable
#: while every write fails, so disk must not outrank what this process already
#: observed. Without this, a failed write made EVERY tick a first_run that
#: re-ran the full multi-hour journal seed scan every 30 s — the exact cost
#: the module header says the 905 MB box cannot afford — with no witness
#: (review 2026-07-31, finding 6).
_state_mem_fallback: Dict[str, Dict[str, Any]] = {}


def _load_state(path: str) -> Dict[str, Any]:
    if path in _state_mem_fallback:
        return _state_mem_fallback[path]
    try:
        with open(path) as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: str, doc: Dict[str, Any]) -> bool:
    """Persist the fold; the in-process copy is kept UNCONDITIONALLY first.

    Returns False on a disk failure so the caller can leave a witness in its
    disposition (honest_failure_modes #9 — the old ``pass`` here claimed the
    degradation was safe, but the real consequence was the repeated seed scan
    above, silently). Memory keeps the probe correct within this runner
    process; the file exists to survive a restart."""
    _state_mem_fallback[path] = doc
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def build_watched(peers: Dict[str, str], seen: Optional[set], now: float,
                  state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Fold this tick's sighting into the persisted last-heard, in claw shape.

    Emits exactly ``parse_lora_stats()["watched"]``'s contract so the verdict is
    produced by ``classify_watch`` — the SAME gate the claws go through. Two
    copies of a silence threshold WILL drift, and the copy that drifts silently
    is the one nobody is looking at (honest_failure_modes #5).
    """
    heard_at = dict(state.get("last_heard_ts") or {})
    if seen:
        for node in seen:
            heard_at[node] = now
    state["last_heard_ts"] = heard_at

    watched: Dict[str, Dict[str, Any]] = {}
    for node in peers:
        ts = heard_at.get(node)
        age = None
        backward = False
        if isinstance(ts, (int, float)):
            age = now - float(ts)
            # Clock went backward (NTP step on an RTC-less box): the stored
            # stamp is not comparable to `now`, so this is unobservable rather
            # than a suspiciously fresh reading. It must travel as
            # ``parse_error`` — the shape classify_watch reads as BLINDNESS —
            # not as ``never``: never + an elapsed window is a QUALIFIED
            # SILENT, so the old mapping let an NTP backstep manufacture a
            # false silence page (review 2026-07-31, finding 5).
            if age < 0:
                age = None
                backward = True
        watched[node] = {"age_s": age, "pkts": None, "rssi_dbm": None,
                         "never": age is None and not backward,
                         "parse_error": backward}
    return watched


def probe_segment_peer_silent(
    *,
    home: Optional[str] = None,
    now: Optional[float] = None,
    state_path: Optional[str] = None,
    config_path: Optional[str] = None,
    debounce_ticks: int = 2,
    _uptime_fn=None,
    _scan_fn=None,
) -> Optional[Signal]:
    """A declared same-segment PEER has not been heard for its full window.

    The peer-witness twin of ``probe_claw_watched_node_silent``, for boxes whose
    segment no claw can demodulate. Verdicts come from ``classify_watch``, so
    ``silent`` means the same thing here as there: we listened comfortably longer
    than that radio's expected transmit interval and heard nothing.

    Self-guards None, with the reason travelling alongside:

    * no peer config → INERT (this box witnesses nobody; most boxes)
    * config present but unreadable/empty → indeterminate, LOUD (a declared
      witness that quietly witnesses nothing is worse than none)
    * journal unreadable → indeterminate (a broken observation channel is not
      evidence of silence)
    * meshtasticd uptime unknown or below the window → indeterminate; the
      listening window cannot be established, exactly as for a rebooted claw
    """
    try:
        now = time.time() if now is None else now
        sp = state_path or DEFAULT_PEER_RF_STATE_PATH
        cp = config_path if config_path is not None else _config_path(home)

        peers, segment, cfg_err = load_peer_config(cp)
        if cfg_err:
            note_disposition("segment_peer_silent", "indeterminate", reason=cfg_err)
            _save_parity_streak(sp + ".streak", 0)
            return None
        if not peers:
            note_disposition("segment_peer_silent", "inert",
                             reason="no RF segment peers declared on this box")
            return None

        try:
            from mini_dudeai.claw_rf_watch import (
                SILENT, classify_watch, required_window_s, summarise)
        except ImportError:
            note_disposition("segment_peer_silent", "indeterminate",
                             reason="claw_rf_watch gate unavailable — refusing to "
                                    "judge silence with a second copy of the rule")
            return None

        state = _load_state(sp)
        first_run = "observing_since" not in state

        # FIRST RUN seeds from the full silence window, not the incremental one.
        # The incremental scan only knows what it saw since it started, and a
        # fleet gateway beacons roughly every 40 min — so a cold 30 min window
        # says "never heard" about a perfectly healthy radio. Seed once, then go
        # incremental. (Observed live on moc3 2026-07-30: first tick produced a
        # silent CANDIDATE for a peer it had received 9 times in the prior 6 h.)
        scan = _scan_fn or scan_journal_for_peers
        window = int(required_window_s()) if first_run else JOURNAL_WINDOW_S
        seen, scan_err = scan(list(peers), window)
        if scan_err:
            note_disposition("segment_peer_silent", "indeterminate",
                             reason="cannot observe: %s" % scan_err)
            _save_parity_streak(sp + ".streak", 0)
            return None

        # THE LISTENING WINDOW is the SHORTER of two things, and getting this
        # wrong is how the claw field first went wrong (3 of 4 radios read
        # `never` at 10 s of uptime):
        #   * how long meshtasticd has been receiving at all, and
        #   * how long THIS PROBE has been observing. The journal is scanned in
        #     bounded slices, so anything the daemon heard before our first scan
        #     is invisible to us no matter how long it has been up.
        # Taking only the daemon's uptime would claim a 9 h window on a probe
        # that has been watching for 30 s.
        if first_run:
            state["observing_since"] = now
            state["seed_window_s"] = window
        # The seed WIDTH is remembered, not recomputed from this tick's window:
        # we scanned `seed_window_s` backwards at `observing_since` and have been
        # watching ever since, so coverage only grows. Adding the CURRENT
        # (30 min) window instead made tick 2 claim LESS coverage than tick 1 and
        # bounced a qualified verdict back to unobservable.
        observed_for = ((now - float(state.get("observing_since", now)))
                        + float(state.get("seed_window_s", window)))
        daemon_up = (_uptime_fn or meshtasticd_uptime_s)(now)
        # Unknown daemon uptime stays None -> classify_watch calls it
        # unobservable. Substituting our own observation age here would let a
        # box with no running receiver claim a qualified silence.
        uptime = None if daemon_up is None else min(daemon_up, observed_for)

        watched = build_watched(peers, seen, now, state)
        # A failed write is WITNESSED on every emission below, not swallowed:
        # memory keeps this process honest, but a runner restart re-seeds the
        # full window, and the operator should learn that from a disposition,
        # not from the CPU graph (honest_failure_modes #9).
        state_warn = ("" if _save_state(sp, state) else
                      " [peer_rf state unwritable at %s — held in memory only;"
                      " a runner restart re-seeds the full window]" % sp)

        verdicts = classify_watch(watched, uptime)
        summary = summarise(verdicts) or {}
        silent = summary.get("silent") or []
        heard = summary.get("heard") or []
        blind = summary.get("unobservable") or []

        if not silent:
            if not heard:
                note_disposition(
                    "segment_peer_silent", "indeterminate",
                    reason=("%d peer(s) still inside the listening window (%s) — "
                            "no qualified verdict yet%s"
                            % (len(blind), ", ".join(blind), state_warn)))
                _save_parity_streak(sp + ".streak", 0)
                return None
            reason = "%d/%d segment peer(s) heard on the air (%s)" % (
                len(heard), len(peers), ", ".join(heard))
            if blind:
                reason += ("; %d not yet observable long enough (%s) — NOT "
                           "counted as healthy" % (len(blind), ", ".join(blind)))
            note_disposition("segment_peer_silent", "clean",
                             reason=reason + state_warn)
            _save_parity_streak(sp + ".streak", 0)
            return None

        streak = _load_parity_streak(sp + ".streak") + 1
        _save_parity_streak(sp + ".streak", streak)
        if streak < debounce_ticks:
            note_disposition(
                "segment_peer_silent", "indeterminate",
                reason="segment-peer-silent candidate (%s), debounce %d/%d%s"
                       % (", ".join(silent), streak, debounce_ticks, state_warn))
            return None

        listed = []
        for node in silent:
            rec = verdicts[node]
            held = rec.get("silent_for_at_least_s")
            label = peers.get(node, node)
            if held is None:
                listed.append("%s (%s)" % (node, label))
            else:
                listed.append("%s (%s, not heard in >=%.0fs of listening)"
                              % (node, label, held))
        blind_clause = ""
        if blind:
            blind_clause = ("; %d peer(s) still un-observable and NOT counted "
                            "either way" % len(blind))

        return Signal(
            cls="segment_peer_silent",
            subject=silent[0] if len(silent) == 1 else "%d segment peers" % len(silent),
            severity="degraded",
            detail=(
                "segment peer(s) not heard on %s: %s%s. This box is an "
                "INDEPENDENT receiver for that radio — it is the only RF witness "
                "this segment has, because the dude-claws listen on a different "
                "modem preset and structurally cannot hear it. Check that node's "
                "own TX leg (PA, antenna, coax, region/preset), not the channel: "
                "our receiver is demonstrably working, since this same journal is "
                "how the reading was taken. If the peer is simply powered down, "
                "that is the finding.%s"
                % (segment or "this segment", "; ".join(listed), blind_clause,
                   state_warn)),
            issue_ref=None,
        )
    except Exception as e:  # never raise into the watchdog tick
        note_disposition("segment_peer_silent", "indeterminate",
                         reason="probe raised %s: %s" % (type(e).__name__, e))
        return None
