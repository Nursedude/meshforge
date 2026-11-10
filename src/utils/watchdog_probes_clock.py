"""meshtasticd started on an unrated clock — read the NTP gate's witness (2026-11-09).

``templates/systemd/meshtasticd.service.d/20-wait-for-ntp-sync.conf`` holds
meshtasticd's start until ``timedatectl`` says synchronized, and FAILS OPEN
after 60 s with a witness line (``wait_for_ntp_sync: TIMEOUT ...``). Portduino
meshtasticd rates its clock ONCE at startup; started on an unsynced clock it may
stamp received packets ``rx_time 0`` so NodeDB's ``last_heard`` never moves and
every node reads offline for the life of the process (moc4 5.8 d, 10-02; moc2
13 d, 10-26 -> 11-08). Nothing read the witness: moc2's sat unread 13 days.

The witness alone does NOT prove the consequence (MEASURED 11-09: lehua's gate
timed out 10-26 23:07, clock synced 56 s later, and its radio read 17 -> 35
nodes online from the first telemetry line — BELIEVED: a big mesh hands it
valid time). So this probe FIRES only on witness AND consequence:

* the CURRENT invocation (systemd ``InvocationID``) carries the gate's TIMEOUT
  line, read from the invocation's HEAD only — ``ExecStartPre`` runs before the
  main start, so the gate's lines are its first entries (a full-invocation
  ``-g`` scan took 42 s on lehua's Pi Zero 2 W, 728,965 lines), cached per
  invocation;
* its last 2 DeviceTelemetry lines both read ``num_online_nodes=0`` (the
  newest at most 2 h old by the MONOTONIC clock) while non-Admin
  ``[Router] Received`` lines from >= 2 distinct senders arrived since the
  older one (the radio logs its OWN packets as ``Received ... from=<own id>``
  — measured on moc2 11-09 — so one sender can be ourselves).

The gate prints a line on EVERY run since 11-09 (``synchronized after Ns``,
including 0 s). A start whose head holds NO gate line (gate did not run, its
lines rotated out, or the start predates the always-speaking gate) is never
read clean on that absence — nor on journal telemetry, which is mesh-writable
(below): every consequence shape is indeterminate, named by its mark (frozen
shape said loudly; nodes online named unverified, with the one-time cure — a
restart lets the always-speaking gate record the start; nothing to judge).
Only the authenticated gate line reads clean (Fable 5.2 review 11-09 F2). systemd's own ExecStartPre record cannot answer "did the gate
run" — it read ``status=0/0 start_time=[n/a]`` on moc4 and lehua (11-09).

Cure: ``sudo systemctl restart meshtasticd`` once ``timedatectl`` shows
synchronized — a new invocation re-rates the clock.

OBSERVE-ONLY (operator 11-09, after review r5): this probe NEVER pages. The
journal is MESH-WRITABLE — the firmware logs a received text message
verbatim and keeps ``\n`` (RedirectablePrint), journald splits on newline,
so a stranger's text BEGINS a new journal entry field-identical to a real
``[DeviceTelemetry]`` line (seen live: a space-weather bot's text lands as 8
entries). The WITNESS is authenticated (journald's trusted ``_COMM`` is the
gate's own, stamped before meshtasticd's main start); the CONSEQUENCE is not,
so it is reported in the disposition reason, labelled unverified. Promotion
to a page waits for an unforgeable consequence source (the radio's own
nodedb lastHeard) and a soak.

Replaces ``inherited_app_drift`` in SIGNAL_CLASSES (operator 11-09: inert on
9/9 watchdog boxes, zero inherited checkouts on all 11 by direct listing).
"""

import json
import re
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from utils.watchdog_probe_core import (
    Signal,  # noqa: F401 — the return type; observe-only never builds one (r5)
    _resolve_main_pid_status,
    note_disposition,
    note_unit_presence_gate,
)

#: The probe body writes the class as a LITERAL in every disposition call —
#: the class-wiring guards (ownership table, disposition adoption) read
#: literals; a test pins this constant equal to them (hfm #5).
CLS = "meshtasticd_clock_unrated"
GATE_DROPIN = "20-wait-for-ntp-sync.conf"
GATE_MARK = "wait_for_ntp_sync:"
#: journald's TRUSTED ``_COMM`` for the gate (the kernel's 15-char comm of
#: ``wait_for_ntp_sync.sh``; measured r3 on 3 past invocations). A payload
#: can forge MESSAGE text, never ``_COMM`` (review r5).
GATE_COMM = "wait_for_ntp_sy"
#: The disposition-reason marker for the frozen SHAPE (witness + journal
#: telemetry 0/0 + foreign RX) — the would-be page, observe-only.
FROZEN_MARK = "UNVERIFIED FROZEN SHAPE"
#: The other two consequence shapes, each named in its reason so the three
#: ``indeterminate`` branches stay distinguishable — to the tests (Fable 5.2
#: review 11-09 F1) and to an operator reading the coverage block.
ONLINE_MARK = "UNVERIFIED ONLINE"
BLIND_MARK = "CONSEQUENCE UNJUDGEABLE"
WITNESS_MARK = "wait_for_ntp_sync: TIMEOUT"
#: How far past the main start the head read goes before it stops (the gate
#: finishes BEFORE the main start; this only bounds the read).
HEAD_READ_PAST_START_US = 30 * 1_000_000
HEAD_READ_MAX_ENTRIES = 400
HEAD_READ_DEADLINE_S = 15.0
#: The newest telemetry line must be at most this old (monotonic) to judge.
TELEMETRY_MAX_AGE_US = 2 * 3600 * 1_000_000
RX_LINES_READ = 1000
MIN_DISTINCT_SENDERS = 2
CONSEQUENCE_CACHE_S = 900.0

#: ONE definition per line kind, shared by the Python match, the ``-g``
#: pattern and the raw-tail filter (hfm #5). Anchored to the TAG the live line
#: carries: the gate's own witness text names ``num_online_nodes=0`` and
#: ``[Router] Received`` — unanchored, it read as a telemetry line (review r3
#: F1: one real zero line + the witness fired as "a run of two").
#
#: THE SEAM (review r4, the same defect KIND a second time): every mark is
#: anchored to the LINE START — meshtasticd's own prefix
#: ``<LEVEL> | HH:MM:SS <uptime> [<Thread>]`` (measured r4: 20,000 live lines,
#: ``[DeviceTelemetry]`` never appears anywhere else) — because a received
#: TEXT MESSAGE's ``msg=`` payload reaches the journal verbatim: any stranger
#: on a public mesh could otherwise send ``[DeviceTelemetry]
#: num_online_nodes=0`` and page a healthy box. ``classify_line`` is the ONE
#: reader; the ``-g`` patterns are built from the same prefix.
_LINE_PREFIX = r"^[A-Z]+ *\| \d\d:\d\d:\d\d \d+ "
TELEMETRY_GREP = _LINE_PREFIX + r"\[DeviceTelemetry\] .*num_online_nodes="
RX_GREP = _LINE_PREFIX + r"\[Router\] Received .+? from="
_ONLINE_RE = re.compile(_LINE_PREFIX + r"\[DeviceTelemetry\] .*?num_online_nodes=(\d+)")
#: The kind may hold a space (``Received text msg from=...`` — review r2 F5).
_RX_RE = re.compile(_LINE_PREFIX + r"\[Router\] Received (.+?) from=(\S+?)(?:,|\s|$)")


def classify_line(msg: str):
    """The ONE line reader: ``("telemetry", n)`` | ``("rx", kind, sender)`` |
    ``("gate", is_witness)`` | None. Every mark matches at the line START only
    (the gate's own lines start at column 0 with its name — measured r4)."""
    if not isinstance(msg, str):
        return None
    if msg.startswith(GATE_MARK):
        return ("gate", msg.startswith(WITNESS_MARK))
    x = _ONLINE_RE.match(msg)
    if x:
        return ("telemetry", int(x.group(1)))
    x = _RX_RE.match(msg)
    if x:
        return ("rx", x.group(1), x.group(2).lower())
    return None
#: A telemetry line may land WHILE the window is read; tolerate this much
#: negative age before calling the clock "ahead" (review r2 F3).
FUTURE_TOLERANCE_US = 60 * 1_000_000
#: journalctl's ``-n N -g PAT`` means "newest N MATCHING" only from systemd
#: 255; on <= 254 it greps inside the newest N RAW entries (review r2 F1,
#: source-read v252/v254, measured 257). moc4 runs 252 (11-09).
JOURNALCTL_NEWEST_MATCHING_MIN = 255
#: Raw-tail fallback size: measured 11-09 at 0.11-0.70 s and >= 5 telemetry
#: lines on every box but moc (one per ~9 h — indeterminate by age).
RAW_TAIL_LINES = 3000

#: InvocationID -> (head verdict, what an UNAUTHENTICATED gate line said:
#: "TIMEOUT" | "synchronized" | None) — see judge_head.
_HEAD_CACHE: Dict[str, Tuple[str, Optional[str]]] = {}
#: InvocationID -> (monotonic ts, verdict tuple) for the consequence leg.
_CONSEQUENCE_CACHE: Dict[str, Tuple[float, Tuple[str, dict]]] = {}


def _systemctl_show(unit: str, props: List[str], *,
                    systemctl_path: str = "systemctl") -> Optional[Dict[str, str]]:
    """``systemctl show -p A -p B`` as a dict, or None when it could not run."""
    cmd = [systemctl_path, "show", unit]
    for p in props:
        cmd += ["-p", p]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    out: Dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        k, sep, v = line.partition("=")
        if sep:
            out[k] = v
    return out if all(p in out for p in props) else None


def _parse_entry(line: str) -> Optional[Tuple[int, str]]:
    try:
        e = json.loads(line)
        mono = int(e.get("__MONOTONIC_TIMESTAMP"))
    except (ValueError, TypeError, AttributeError):
        return None
    msg = e.get("MESSAGE")
    if isinstance(msg, list):          # journald emits non-UTF-8 as a byte list
        try:
            msg = bytes(msg).decode("utf-8", "replace")
        except (TypeError, ValueError):
            msg = ""
    return mono, msg if isinstance(msg, str) else ""


def _parse_head_entry(line: str) -> Optional[Tuple[int, str, Optional[str]]]:
    """``(mono, MESSAGE, _COMM)`` — the head read needs journald's trusted
    ``_COMM`` to authenticate the gate's lines (review r5)."""
    parsed = _parse_entry(line)
    if parsed is None:
        return None
    try:
        comm = json.loads(line).get("_COMM")
    except (ValueError, AttributeError):
        comm = None
    return parsed[0], parsed[1], comm if isinstance(comm, str) else None


def _read_head(invocation: str, stop_after_mono: int, *,
               journalctl_path: str = "journalctl",
               deadline_s: float = HEAD_READ_DEADLINE_S) -> Optional[List[Tuple[int, str]]]:
    """The invocation's FIRST entries, oldest first, up to ``stop_after_mono``.
    Streams and stops early (never reads the whole invocation); a reader thread
    with a deadline bounds a journalctl that stalls BETWEEN lines too. None =
    unobservable."""
    cmd = [journalctl_path, f"_SYSTEMD_INVOCATION_ID={invocation}",
           "-o", "json", "-q", "--no-pager"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
    except (FileNotFoundError, OSError):
        return None
    entries: List[Tuple[int, str]] = []
    done = threading.Event()

    def reader():
        try:
            for line in proc.stdout:        # type: ignore[union-attr]
                parsed = _parse_head_entry(line)
                if parsed is None:
                    continue
                entries.append(parsed)
                if parsed[0] > stop_after_mono or len(entries) >= HEAD_READ_MAX_ENTRIES:
                    break
        except (OSError, ValueError):
            pass
        finally:
            done.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    finished = done.wait(deadline_s)
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    if not finished:
        return None                         # stalled: unobservable, never partial
    t.join(timeout=1)
    if not entries and proc.returncode not in (0, -9, None):
        # journalctl exited with an error before writing anything (review r2
        # F4: this read as "no journal lines yet" forever — wrong cause).
        return None
    return list(entries)


_JOURNALCTL_VERSION: Dict[str, Optional[int]] = {}


def _journalctl_version(journalctl_path: str = "journalctl") -> Optional[int]:
    """systemd major version from ``journalctl --version``, cached per path;
    None when unreadable (callers then take the version-proof raw tail)."""
    if journalctl_path in _JOURNALCTL_VERSION:
        return _JOURNALCTL_VERSION[journalctl_path]
    ver: Optional[int] = None
    try:
        proc = subprocess.run([journalctl_path, "--version"],
                              capture_output=True, text=True, timeout=10)
        m = re.match(r"\s*systemd\s+(\d+)", proc.stdout or "")
        if proc.returncode == 0 and m:
            ver = int(m.group(1))
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        ver = None
    _JOURNALCTL_VERSION[journalctl_path] = ver
    return ver


def _read_last(invocation: str, pattern: Optional[str], n: int, *,
               journalctl_path: str = "journalctl") -> Optional[List[Tuple[int, str]]]:
    """The newest ``n`` entries of this invocation (matching ``pattern`` when
    given — ONLY valid on systemd >= 255, see JOURNALCTL_NEWEST_MATCHING_MIN).
    Order is whatever journalctl emits (257: newest-first WITH ``-g``, which
    implies ``--reverse``; oldest-first without): consumers sort by stamp. Count-bounded, never a wall-clock ``--since``
    (hfm #6: this class exists because the wall clock was wrong). None =
    unobservable; ``[]`` = observed, nothing matched."""
    cmd = [journalctl_path, f"_SYSTEMD_INVOCATION_ID={invocation}"]
    if pattern is not None:
        cmd += ["-g", pattern]
    cmd += ["-n", str(n), "-o", "json", "--output-fields=MESSAGE", "-q", "--no-pager"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode > 1 or (proc.returncode == 1 and (proc.stderr or "").strip()):
        return None
    out = []
    for line in (proc.stdout or "").splitlines():
        parsed = _parse_entry(line)
        if parsed is not None:
            out.append(parsed)
    return out


def _default_window(invocation: str, journalctl_path: str):
    """Telemetry + RX lines for the consequence leg. On systemd >= 255 two
    cheap newest-N-MATCHING reads (any telemetry interval); otherwise — or
    when the version is unreadable — a raw tail filtered here, because on
    <= 254 ``-n -g`` greps inside the newest N raw lines and would read the
    probe blind forever (review r2 F1)."""
    ver = _journalctl_version(journalctl_path)
    if ver is not None and ver >= JOURNALCTL_NEWEST_MATCHING_MIN:
        tele = _read_last(invocation, TELEMETRY_GREP, 2,
                          journalctl_path=journalctl_path)
        rx = _read_last(invocation, RX_GREP, RX_LINES_READ,
                        journalctl_path=journalctl_path)
        if tele is None or rx is None:
            return None
        return tele + rx
    raw = _read_last(invocation, None, RAW_TAIL_LINES,
                     journalctl_path=journalctl_path)
    if raw is None:
        return None
    return [(m, msg) for m, msg in raw
            if (classify_line(msg) or ("",))[0] in ("telemetry", "rx")]


def judge_head(entries, main_start_mono: int) -> Optional[str]:
    """"witness" | "gate_clean" | "gate_unauthenticated" | "gate_silent", or
    None for an empty head
    (not yet in the journal — never cached). A gate line counts ONLY when
    journald's trusted ``_COMM`` is the gate's (review r5) — a payload logs
    under meshtasticd's comm and cannot forge it. No stamp rule: it adds no
    authentication and races systemd's fork (measured r6: the gate line lands
    ~1.4 ms before ExecMainStart on boot), which would DEMOTE a real witness.
    ``main_start_mono`` is kept for the caller's contract.

    ``gate_unauthenticated`` (Fable 5.2 review r2 11-09): a gate line with NO
    ``_COMM`` — journald reads it from /proc/<pid> lazily, and a gate that
    exits right after speaking loses it (measured 8/30, 19/30 kept on the 0 s
    path; 30/30 with a 0.3 s linger, so the gate now lingers 1 s). PRESENT but
    unprovable — never reported as "no gate line", which would send the
    operator to restart for a line a restart cannot authenticate. A line with
    a FOREIGN comm is a forgery and stays ignored (``gate_silent``)."""
    if not entries:
        return None
    gate, unauth = [], []
    for m, msg, comm in entries:
        c = classify_line(msg)
        if not c or c[0] != "gate":
            continue
        if comm == GATE_COMM:
            gate.append(c)
        elif comm is None:
            unauth.append(c)
    if any(c[1] for c in gate):
        return "witness"
    if gate:
        return "gate_clean"
    return "gate_unauthenticated" if unauth else "gate_silent"


def judge_consequence(entries: List[Tuple[int, str]],
                      now_mono_us: int) -> Tuple[str, dict]:
    """("fire" | "online" | "blind", facts) from telemetry + RX lines."""
    classified = [(m, c) for m, msg in entries for c in [classify_line(msg)] if c]
    tele = sorted((m, c[1]) for m, c in classified if c[0] == "telemetry")
    if len(tele) < 2:
        return "blind", {"telemetry_lines": len(tele)}
    (t_old, n1), (t_new, n2) = tele[-2], tele[-1]
    facts = {"online_last_two": [n1, n2]}
    age = now_mono_us - t_new
    if age < -FUTURE_TOLERANCE_US or age > TELEMETRY_MAX_AGE_US:
        facts["newest_telemetry_age_s"] = age // 1_000_000
        return "blind", facts
    if n1 != 0 or n2 != 0:
        return "online", facts
    senders = set()
    rx = 0
    for m, c in classified:
        # Only RX BETWEEN the two zero lines proves the nodedb ignored it: a
        # packet after the newest line has not been reflected in any
        # telemetry yet (review r3 F2).
        if c[0] == "rx" and c[1] != "Admin" and t_old <= m <= t_new:
            rx += 1
            senders.add(c[2])
    facts["rx_lines"] = rx
    facts["rx_senders"] = len(senders)
    facts["rx_read"] = sum(1 for _, c in classified if c[0] == "rx")
    if rx == 0 and facts["rx_read"] >= RX_LINES_READ:
        # Every RX line read is newer than the last telemetry line: the read
        # saturated, so "no RX in the window" is not an observation (r4 F3).
        facts["rx_read_saturated"] = True
    if len(senders) < MIN_DISTINCT_SENDERS:
        # Zero online and nothing heard from another node: a quiet or deaf
        # radio (its own packets log as Received too) — not this class.
        return "blind", facts
    return "fire", facts


#: The two cures a consequence reason can carry (one table, review r3).
CURE_RERATE = ("once `timedatectl` shows synchronized, sudo systemctl restart "
               "meshtasticd re-rates the clock")
CURE_NEXT_START = ("sudo systemctl restart meshtasticd when convenient — the next "
                   "start's gate line should be authenticated (BELIEVED: the gate "
                   "lingers so journald keeps its _COMM; unmeasured under systemd)")


#: The node-list check that must precede any restart on journal evidence
#: (the journal is mesh-writable; review r4 F2 pins it in every cell).
CHECK_NODE_LIST = ("check the radio's own node list (TUI / map) — if every node "
                   "reads offline: ")
HEADS = ("witness", "gate_unauthenticated", "gate_silent")
#: judge_consequence's verdicts — "fire" is the frozen SHAPE (observe-only).
VERDICTS = ("online", "fire", "blind")


def _blind_cause(facts: dict) -> str:
    """Name WHICH of the four blind causes (review r4 F3) — never a raw dict."""
    if "telemetry_lines" in facts:
        return (f"fewer than 2 DeviceTelemetry lines this start "
                f"({facts['telemetry_lines']} read)")
    if "newest_telemetry_age_s" in facts:
        age = facts["newest_telemetry_age_s"]
        if age < 0:
            return f"the newest telemetry line is stamped {-age} s AHEAD of now"
        return f"the newest telemetry line is {age} s old (over the 2 h bound)"
    return (f"0 nodes online but only {facts.get('rx_senders', 0)} other sender(s) "
            f"heard between the readings (a quiet segment or a deaf radio)")


def compose_reason(head: str, said: Optional[str], verdict: str, facts: dict) -> str:
    """The ONE reason writer for the consequence branches, checked against an
    independent spec over every reachable input x cache path (review r4).
    Every cell is ``indeterminate``; only the authenticated gate line reads
    clean, and that branch never reaches here.

    The CURE follows what the gate line SAYS, not whether journald kept its
    _COMM (r3 F1): a TIMEOUT — authenticated or not — gets the re-rate cure,
    always behind the node-list check when the evidence is the journal;
    otherwise "restart when convenient". An input outside the closed enums is
    a PROBE BUG and says so (hfm #7, r4 F5) — never the gate-silent text."""
    if head not in HEADS or verdict not in VERDICTS or (
            head == "gate_unauthenticated" and said not in ("TIMEOUT", "synchronized")):
        return (f"probe bug: unknown head/said/verdict {head!r}/{said!r}/{verdict!r} "
                "— this class cannot say what it saw")
    timed_out = head == "witness" or said == "TIMEOUT"
    if head == "witness":
        why = "the NTP gate timed out at this start"
    elif head == "gate_unauthenticated":
        why = ("this start's NTP-gate line is present but unauthenticated (it reads "
               f"{said}; journald recorded no _COMM — the gate exited before "
               "journald read it)")
    else:
        why = ("no NTP-gate line is readable for this start (gate did not run, its "
               "lines rotated out, or the start predates the always-speaking gate)")
    if verdict == "online":
        # Journal text is mesh-writable (r5): it never excuses a start (r1 F2).
        n1, n2 = facts["online_last_two"]
        cure = CURE_RERATE if timed_out else CURE_NEXT_START
        return (f"{ONLINE_MARK}: {why}, and journal-derived telemetry (unverified) "
                f"reads {n1} then {n2} node(s) online (last two: "
                f"{facts['online_last_two']}) — journal text cannot excuse the "
                f"start; to verify: {cure}")
    if verdict == "blind":
        # r4 F3: a TIMED-OUT start the journal cannot judge still names the
        # next step (moc logs telemetry ~9 h apart: blind by age most ticks).
        action = (f"; the gate timed out, so {CHECK_NODE_LIST}{CURE_RERATE}"
                  if timed_out else "; nothing points at a fault — no action")
        return (f"{BLIND_MARK}: {why}; the consequence cannot be judged: "
                f"{_blind_cause(facts)}{action}")
    # The frozen SHAPE. OBSERVE-ONLY (operator 11-09, review r5): SAID, never paged.
    # r4 F4: "LIKELY started before NTP sync" only when the gate timed out.
    if timed_out:
        cause = "LIKELY started before NTP sync"
    elif head == "gate_silent":
        cause = "possibly started before NTP sync (no gate line to say)"
    else:
        cause = "cause UNKNOWN (the gate says synchronized)"
    return (f"{FROZEN_MARK}: {why}; journal-derived telemetry (UNVERIFIED — "
            f"the journal is mesh-writable) reads 0 nodes online while "
            f"{facts['rx_senders']} other sender(s) are heard "
            f"({facts['rx_lines']} RX line(s)) — {cause}; "
            f"{CHECK_NODE_LIST}{CURE_RERATE}")


def probe_meshtasticd_clock_unrated(
    *,
    unit: str = "meshtasticd.service",
    systemctl_path: str = "systemctl",
    journalctl_path: str = "journalctl",
    show_fn: Optional[Callable[[], Optional[Dict[str, str]]]] = None,
    head_fn: Optional[Callable[[str, int], Optional[List[Tuple[int, str]]]]] = None,
    window_fn: Optional[Callable[[str], Optional[List[Tuple[int, str]]]]] = None,
    main_pid: Optional[int] = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Optional[Signal]:
    """Fire when the CURRENT meshtasticd started on an unsynced clock (the NTP
    gate's TIMEOUT witness) AND its nodedb shows the consequence (last two
    telemetry lines 0 online while >= 2 other senders are heard). See the
    module docstring."""
    if main_pid is not None:
        pid_status, pid = "ok", main_pid
    else:
        pid_status, pid = _resolve_main_pid_status(unit, systemctl_path=systemctl_path)
    if pid is None:
        note_unit_presence_gate(
            "meshtasticd_clock_unrated", pid_status,
            absent_reason=f"no {unit} unit on this box; no radio clock to rate",
            unresolved_reason="meshtasticd MainPID unresolved; service_inactive owns that",
        )
        return None

    props = (show_fn or (lambda: _systemctl_show(
        unit, ["InvocationID", "ExecMainStartTimestampMonotonic", "DropInPaths"],
        systemctl_path=systemctl_path)))()
    try:
        invocation = (props or {}).get("InvocationID", "").strip()
        main_start = int((props or {}).get("ExecMainStartTimestampMonotonic", ""))
    except ValueError:
        invocation, main_start = "", 0
    if not invocation or main_start <= 0:
        note_disposition("meshtasticd_clock_unrated", "indeterminate",
                         reason="meshtasticd InvocationID / start time unobservable")
        return None
    if GATE_DROPIN not in (props or {}).get("DropInPaths", ""):
        note_disposition(
            "meshtasticd_clock_unrated", "indeterminate",
            reason=(f"{GATE_DROPIN} not loaded for {unit} — a start before NTP "
                    "sync would leave no witness (install it: scripts/update.sh)"))
        return None

    cached_head = _HEAD_CACHE.get(invocation)
    head, said = cached_head if cached_head else (None, None)
    if head is None:
        entries = (head_fn or (lambda inv, stop: _read_head(
            inv, stop, journalctl_path=journalctl_path)))(
            invocation, main_start + HEAD_READ_PAST_START_US)
        if entries is None:
            note_disposition("meshtasticd_clock_unrated", "indeterminate",
                             reason="journalctl unavailable/stalled; invocation head unread")
            return None
        head = judge_head(entries, main_start)
        if head is None:
            # Not cached: a just-started unit's first lines may not be
            # flushed yet (review r1 F1) — re-read next tick.
            note_disposition("meshtasticd_clock_unrated", "indeterminate",
                             reason="this meshtasticd start has no journal lines yet")
            return None
        said = None
        if head == "gate_unauthenticated":
            said = ("TIMEOUT" if any(
                c[0] == "gate" and c[1]
                for _, msg, comm in entries if comm is None
                for c in [classify_line(msg)] if c) else "synchronized")
        _HEAD_CACHE.clear()                 # one live invocation per box
        _HEAD_CACHE[invocation] = (head, said)

    if head == "gate_clean":
        note_disposition("meshtasticd_clock_unrated", "clean")
        return None

    cached = _CONSEQUENCE_CACHE.get(invocation)
    now = monotonic()
    if cached is not None and 0 <= now - cached[0] < CONSEQUENCE_CACHE_S:
        verdict, facts = cached[1]
    else:
        window = (window_fn or (lambda inv: _default_window(
            inv, journalctl_path)))(invocation)
        now = monotonic()   # AFTER the read: a line landing mid-read is not "future" (r2 F3)
        if window is None:
            note_disposition("meshtasticd_clock_unrated", "indeterminate",
                             reason="journalctl unavailable; telemetry/RX lines unread")
            return None
        verdict, facts = judge_consequence(window, int(now * 1_000_000))
        _CONSEQUENCE_CACHE.clear()
        _CONSEQUENCE_CACHE[invocation] = (now, (verdict, facts))

    note_disposition("meshtasticd_clock_unrated", "indeterminate",
                     reason=compose_reason(head, said, verdict, facts))
    return None
