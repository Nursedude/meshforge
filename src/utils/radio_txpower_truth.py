"""TX power truth — what the radio SAVED vs what meshtasticd APPLIED vs a change
still PENDING in an open settings transaction. Read-only; never opens a radio
session (a TCPInterface on meshtasticd can starve PhoneAPI, #17).

WHY (measured 2026-10-03, a fleet HAT box): a tx_power change made from the
meshtasticd web client logged `Delay save of changes to disk until the open
transaction is committed` and nothing else — no `Set radio` (never applied to
the radio), no `Save /prefs/config.proto` (never saved). `meshtastic --get`
read the new value back from RAM, so every check a person would run said
"done", while the HAT kept transmitting at the old power until a restart
silently discarded it. Nothing in the app could show that; this module is the
in-app witness.

What each leg is OF (firmware behaviour read at the pinned source,
RadioInterface.cpp / AdminModule.cpp / portduino main.cpp, 2026-10-03):
  service  — check_service(): a stopped/failed unit applies NOTHING, whatever
             its last activation logged (ActiveEnterTimestamp survives a stop).
  saved    — LocalConfig.lora.tx_power (field 6 → 10, int32) from the prefs
             file under the state root meshtasticd ITSELF logged
             (`Portduino is starting, VFS root at …`); the unit's
             --fsdir/-d/User=/HOME is the fallback when that line has rotated
             out, and a disagreement is Unobservable. 0 = firmware default
             (the region limit), not "0 dBm".
  applied  — `Set radio: … power=N` = the configured value AFTER the firmware's
             region clamp (power > limit and unlicensed → limit; 0 → limit).
             So saved > applied is CLAMPED (every restart re-clamps), never
             drift; saved < applied is real DRIFT. `Final Tx power: N dBm` is
             the CHIP drive after module limits — on a PA module (E22-900M30S,
             MeshAdv) the antenna power is higher. Labelled, never compared.
  pending  — the firmware's edit transaction (`Begin transaction for editing
             settings` … `Commit transaction for edited settings`). While it is
             open every save is `Delay save …`: in RAM only, NOT on the air,
             NOT on disk — for ANY setting, not just tx_power. A single-field
             CLI `--set` does NOT close it; `--commit-edit` does (then reboots
             meshtasticd), a restart discards it.
  lost     — an in-process `Rebooting` with the transaction open. A service
             restart/crash/power cycle that discards it starts a new activation
             and is NOT visible here (the pane says so).
Live RAM value: deliberately not read (#17). The pane says how to get it.
"""
from __future__ import annotations

import pwd
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from utils.observation import Failed, Observation, Seen, Unobservable, assert_never

UNIT = "meshtasticd"
_LOCALCONFIG_LORA = 6
_LORA_TX_POWER = 10
JOURNAL_TIMEOUT_S = 20

_RE_SET_RADIO = re.compile(r"Set radio: .*\bpower=(-?\d+)")
_RE_FINAL = re.compile(r"Final Tx power: (-?\d+) dBm")
_RE_VFS = re.compile(r"VFS root at (\S+)")
_BEGIN = "Begin transaction for editing settings"
_COMMIT = "Commit transaction for edited settings"
_DELAY = "Delay save of changes to disk until the open transaction is committed"
_SAVES = ("Save changes to disk", "Save /prefs/config.proto")
_REBOOT = "Rebooting"
JOURNAL_GREP = ("Set radio|Final Tx power|VFS root at|Begin transaction for editing|"
                "Commit transaction for edited|Delay save of changes|Save changes to disk|"
                "Save /prefs/config.proto|Rebooting")


# ── protobuf wire format (two fields; dependency-free on purpose) ─────────

class _Malformed(Exception):
    pass


def _varint(buf: bytes, i: int) -> Tuple[int, int]:
    shift = 0
    val = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise _Malformed()
        b = buf[i]
        i += 1
        val |= (b & 0x7F) << shift
        if not b & 0x80:
            return val, i
        shift += 7


def _fields(buf: bytes) -> List[Tuple[int, int, object]]:
    """[(field_no, wire_type, int value | bytes payload)] for one message.
    Field number 0 is illegal in protobuf — a NUL-filled (power-loss) block
    raises instead of decoding as data."""
    out: List[Tuple[int, int, object]] = []
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        num, wt = key >> 3, key & 7
        if num == 0:
            raise _Malformed()
        if wt == 0:
            v, i = _varint(buf, i)
            out.append((num, wt, v))
        elif wt == 1:
            if i + 8 > len(buf):
                raise _Malformed()
            out.append((num, wt, buf[i:i + 8]))
            i += 8
        elif wt == 2:
            n, i = _varint(buf, i)
            if i + n > len(buf):
                raise _Malformed()
            out.append((num, wt, buf[i:i + n]))
            i += n
        elif wt == 5:
            if i + 4 > len(buf):
                raise _Malformed()
            out.append((num, wt, buf[i:i + 4]))
            i += 4
        else:
            raise _Malformed()
    return out


def _int32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= 1 << 31 else v


def decode_lora_tx_power(blob: bytes) -> Observation[int]:
    """LocalConfig bytes → Seen(tx_power). Absent field is Seen(0) (proto3
    default = firmware default); empty, truncated, NUL-filled or malformed is
    Failed, never 0; no LoRa message at all is Unobservable. Repeated field-6
    chunks MERGE (protobuf semantics: concatenate, last tx_power wins)."""
    if not blob:
        return Failed("prefs file is EMPTY (zero-byte — power-loss truncation?) — not decoded as 0")
    try:
        lora = b""
        seen_lora = False
        for n, wt, v in _fields(blob):
            if n == _LOCALCONFIG_LORA:
                if wt != 2 or not isinstance(v, bytes):
                    return Failed("prefs file has a LoRa field that is not a message — malformed")
                lora += v
                seen_lora = True
        if not seen_lora:
            return Unobservable("prefs file has no LoRa config section")
        power = 0
        for n, wt, v in _fields(lora):
            if n == _LORA_TX_POWER and wt == 0 and isinstance(v, int):
                power = _int32(v)
        return Seen(power)
    except _Malformed:
        return Failed("prefs file is truncated or malformed — tx_power not decodable")


# ── journal ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class JournalFacts:
    applied_power: Optional[int]     # last `Set radio … power=N` (post region clamp)
    applied_ts: Optional[float]
    chip_power: Optional[int]        # `Final Tx power` AFTER that Set radio, else None
    pending_since: Optional[float]   # transaction open (Begin, else first Delay)
    lost_at: Optional[float]         # in-process reboot while the transaction was open
    vfs_root: Optional[str]          # state root meshtasticd logged at its last start


def parse_journal(lines: Iterable[str]) -> JournalFacts:
    """Lines as `<unix_ts> <host> <ident>: <message>` (journalctl -o
    short-unix), oldest first. Matching is by substring, so host/ident vary."""
    applied: Optional[int] = None
    applied_ts: Optional[float] = None
    chip: Optional[int] = None
    pending: Optional[float] = None
    lost: Optional[float] = None
    vfs: Optional[str] = None
    for raw in lines:
        parts = raw.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            ts = float(parts[0])
        except ValueError:
            continue
        msg = parts[1]
        m = _RE_VFS.search(msg)
        if m:
            vfs = m.group(1)
            continue
        m = _RE_SET_RADIO.search(msg)
        if m:
            applied, applied_ts, chip = int(m.group(1)), ts, None
            lost = None                      # a newer apply supersedes an old loss
            continue
        m = _RE_FINAL.search(msg)
        if m:
            chip = int(m.group(1))
            continue
        if _BEGIN in msg:
            pending, lost = ts, None
        elif _DELAY in msg:
            if pending is None:
                pending = ts
            lost = None
        elif _COMMIT in msg or any(s in msg for s in _SAVES):
            pending = None
            lost = None
        elif _REBOOT in msg and pending is not None:
            lost, pending = ts, None
    return JournalFacts(applied, applied_ts, chip, pending, lost, vfs)


# ── verdict ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Verdict:
    # OK | DRIFT | CLAMPED | DEFAULT | PENDING | LOST | FAILED | ABSENT | UNOBSERVABLE
    status: str
    lines: List[str]


def _fmt_ts(ts: Optional[float]) -> str:
    import time
    return "?" if ts is None else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def verdict(saved: Observation[int], journal: Observation[JournalFacts],
            service: Observation[str] = Seen("running")) -> Verdict:
    """`service`: Seen("running") | Seen("absent") | Unobservable/Failed(why)."""
    lines: List[str] = []
    match service:
        case Seen(value="absent"):
            return Verdict("ABSENT", ["meshtasticd is not installed on this box — nothing to "
                                      "compare (absent by design is not a fault)."])
        case Seen(value=_):
            pass
        case Unobservable(why=swhy) | Failed(why=swhy):
            return Verdict("UNOBSERVABLE", [f"service  : {swhy}",
                                            "a stopped or failed meshtasticd applies nothing — "
                                            "no saved/applied comparison is meaningful"])
        case _ as unreachable_s:
            assert_never(unreachable_s)

    saved_v: Optional[int] = None
    saved_failed = False
    match saved:
        case Seen(value=v):
            saved_v = v
            lines.append(f"saved    : {v} dBm" if v != 0 else "saved    : 0 = firmware default (region limit)")
        case Unobservable(why=why):
            lines.append(f"saved    : UNOBSERVABLE — {why}")
        case Failed(why=why):
            saved_failed = True
            lines.append(f"saved    : FAILED — {why} (the firmware may fall back to defaults on its next start)")
        case _ as unreachable:
            assert_never(unreachable)

    facts: Optional[JournalFacts] = None
    match journal:
        case Seen(value=f):
            facts = f
        case Unobservable(why=jwhy) | Failed(why=jwhy):
            lines.append(f"applied  : UNOBSERVABLE — {jwhy}")
        case _ as unreachable_j:
            assert_never(unreachable_j)

    if facts is not None:
        if facts.applied_power is None:
            lines.append("applied  : UNOBSERVABLE — no `Set radio` line since meshtasticd started "
                         "(journal rotated, journal access, or a Logging LogLevel above info)")
        else:
            lines.append(f"applied  : {facts.applied_power} dBm configured, after the region limit "
                         f"(at {_fmt_ts(facts.applied_ts)})")
            chip = ("not logged since that change" if facts.chip_power is None
                    else f"{facts.chip_power} dBm")
            lines.append(f"chip drive: {chip} — after module limits; on a PA module the antenna "
                         "power is higher. Labelled, never compared.")
        if facts.pending_since is not None:
            lines.append(f"PENDING  : a settings edit transaction open since {_fmt_ts(facts.pending_since)} "
                         "was never committed — its changes (ANY setting, not only tx_power) are in RAM "
                         "only: NOT on the air, NOT saved")
        if facts.lost_at is not None:
            lines.append(f"LOST     : an uncommitted change was discarded by the reboot at {_fmt_ts(facts.lost_at)}")
    lines.append("live RAM : not read here (a radio session can starve meshtasticd, #17) — "
                 "`meshtastic --host localhost --get lora.tx_power` shows it")
    lines.append("note     : a change discarded by a service RESTART/crash/power loss is not visible here")

    if facts is not None and facts.pending_since is not None:
        return Verdict("PENDING", lines)
    if saved_failed:
        return Verdict("FAILED", lines)
    if facts is not None and facts.lost_at is not None:
        return Verdict("LOST", lines)
    if saved_v is None or facts is None or facts.applied_power is None:
        return Verdict("UNOBSERVABLE", lines)
    if saved_v == 0:
        lines.append("saved 0 means the firmware picks the power (the region limit) — not compared")
        return Verdict("DEFAULT", lines)
    if saved_v > facts.applied_power:
        lines.append(f"CLAMPED  : {saved_v} dBm is saved, the firmware applied {facts.applied_power} dBm "
                     "(region / licence limit) — a restart re-clamps; the transmit power does not change")
        return Verdict("CLAMPED", lines)
    if saved_v < facts.applied_power:
        lines.append(f"DRIFT    : the radio is configured for {facts.applied_power} dBm but {saved_v} dBm "
                     "is saved — the next restart LOWERS the transmit power")
        return Verdict("DRIFT", lines)
    return Verdict("OK", lines)


# ── prefs path ────────────────────────────────────────────────────────────

_RE_FSDIR = re.compile(r"(?:--fsdir[= ]|(?<!\S)-d\s+)(\S+)")


def prefs_path_from(exec_start: str, user: str, home_env: str = "") -> Observation[Path]:
    """State root from the unit as systemd would launch it: --fsdir / -d,
    else $HOME (Environment=HOME=), else User='s passwd home (name or uid),
    else root. An unresolvable User= is Unobservable — never a guessed /root."""
    m = _RE_FSDIR.search(exec_start)
    if m:
        root = m.group(1).rstrip(";")
        if not root.startswith("/"):
            return Unobservable(f"--fsdir {root!r} is relative — resolved against the unit's working dir, not ours")
        return Seen(Path(root) / "prefs" / "config.proto")
    if home_env:
        return Seen(Path(home_env) / ".portduino" / "default" / "prefs" / "config.proto")
    if not user or user == "root":
        return Seen(Path("/root/.portduino/default/prefs/config.proto"))
    try:
        entry = pwd.getpwuid(int(user)) if user.isdigit() else pwd.getpwnam(user)
    except (KeyError, ValueError):
        return Unobservable(f"meshtasticd User={user!r} does not resolve to a home directory")
    return Seen(Path(entry.pw_dir) / ".portduino" / "default" / "prefs" / "config.proto")


# ── live readers (thin; every failure is a named Unobservable/Failed) ─────

def _systemctl_show(prop: str) -> Observation[str]:
    if shutil.which("systemctl") is None:
        return Unobservable("systemctl not available")
    try:
        r = subprocess.run(["systemctl", "show", UNIT, "-p", prop, "--value"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Unobservable(f"systemctl show {prop}: {e.__class__.__name__}")
    if r.returncode != 0:
        return Unobservable(f"systemctl show {prop} rc={r.returncode}")
    return Seen(r.stdout.strip())


def read_service() -> Observation[str]:
    """Seen("running") | Seen("absent") | Unobservable(why). Via check_service,
    the project's single source of truth for unit state (MF008)."""
    try:
        from utils.service_check import ServiceState, check_service
    except ImportError as e:
        return Failed(f"service_check unimportable: {e}")
    st = check_service(UNIT)
    if st.state == ServiceState.NOT_INSTALLED:
        return Seen("absent")
    if st.state in (ServiceState.AVAILABLE, ServiceState.DEGRADED):
        return Seen("running")
    return Unobservable(f"meshtasticd is {st.state.value} — nothing is being applied")


def _home_env(env: str) -> str:
    m = re.search(r"(?:^|\s)HOME=(\S+)", env)
    return m.group(1) if m else ""


def _unit_prefs_path() -> Observation[Path]:
    match _systemctl_show("ExecStart"):
        case Seen(value=exec_start):
            return _unit_prefs_from(exec_start)
        case Unobservable(why=why) | Failed(why=why):
            return Unobservable(f"cannot read the unit's ExecStart — {why}")
        case _ as unreachable:
            assert_never(unreachable)


def _unit_prefs_from(exec_start: str) -> Observation[Path]:
    match _systemctl_show("Environment"):
        case Seen(value=env):
            return _unit_prefs_with_env(exec_start, _home_env(env))
        case Unobservable(why=ewhy) | Failed(why=ewhy):
            return Unobservable(f"cannot read the unit's Environment — {ewhy}")
        case _ as unreachable:
            assert_never(unreachable)


def _unit_prefs_with_env(exec_start: str, home: str) -> Observation[Path]:
    if _RE_FSDIR.search(exec_start) or home:
        return prefs_path_from(exec_start, "", home)
    match _systemctl_show("User"):
        case Seen(value=user):
            return prefs_path_from(exec_start, user, "")
        case Unobservable(why=uwhy) | Failed(why=uwhy):
            return Unobservable(f"cannot read the unit's User= — {uwhy}")
        case _ as unreachable:
            assert_never(unreachable)


def resolve_prefs(vfs_root: Optional[str]) -> Observation[Path]:
    """The state root meshtasticd LOGGED wins; the unit parse is the fallback
    and a cross-check — if both exist and disagree, we do not pick one."""
    unit = _unit_prefs_path()
    if vfs_root is None:
        return unit
    logged = Path(vfs_root) / "prefs" / "config.proto"
    match unit:
        case Seen(value=p) if p != logged:
            return Unobservable(f"meshtasticd logged state root {vfs_root} but the unit resolves to "
                                f"{p.parent.parent} — not guessing which file is current")
        case Seen() | Unobservable() | Failed():
            return Seen(logged)
        case _ as unreachable:
            assert_never(unreachable)


def _read_prefs(path: Path) -> Observation[int]:
    try:
        blob = path.read_bytes()
    except PermissionError:
        return Unobservable(f"{path} is not readable as this user — run the TUI with sudo to see it")
    except FileNotFoundError:
        return Unobservable(f"{path} does not exist")
    except OSError as e:
        return Failed(f"{path}: {e.__class__.__name__}: {e}")
    return decode_lora_tx_power(blob)


def read_saved(vfs_root: Optional[str]) -> Observation[int]:
    match resolve_prefs(vfs_root):
        case Seen(value=path):
            return _read_prefs(path)
        case Unobservable(why=why) | Failed(why=why):
            return Unobservable(why)
        case _ as unreachable:
            assert_never(unreachable)


def _journal_since(since: str) -> Observation[JournalFacts]:
    try:
        r = subprocess.run(["journalctl", "-u", UNIT, "--since", since, "--no-pager",
                            "-o", "short-unix", "--grep", JOURNAL_GREP],
                           capture_output=True, text=True, timeout=JOURNAL_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Unobservable(f"journalctl: {e.__class__.__name__}")
    err = (r.stderr or "").strip()
    # rc=1 with no stderr = "no matching lines"; rc=1 WITH stderr is a real
    # error (bad --since, journalctl built without --grep support).
    if r.returncode not in (0, 1) or (r.returncode == 1 and err):
        tail = err.splitlines()[-1:] or [""]
        return Unobservable(f"journalctl rc={r.returncode}: {tail[0][:140]}")
    return Seen(parse_journal(r.stdout.splitlines()))


def read_journal() -> Observation[JournalFacts]:
    match _systemctl_show("ActiveEnterTimestamp"):
        case Seen(value=since) if since:
            return _journal_since(since)
        case Seen():
            return Unobservable(f"{UNIT} has no activation time — nothing applied to observe")
        case Unobservable(why=why) | Failed(why=why):
            return Unobservable(why)
        case _ as unreachable:
            assert_never(unreachable)


def read_all() -> Verdict:
    """The pane's one entry point: service gate → journal (which also names
    the state root) → saved → verdict."""
    service = read_service()
    journal = read_journal()
    vfs: Optional[str] = None
    match journal:
        case Seen(value=f):
            vfs = f.vfs_root
        case Unobservable() | Failed():
            pass
        case _ as unreachable:
            assert_never(unreachable)
    return verdict(read_saved(vfs), journal, service)
