"""box_truth/v1 ``rx_heard`` — which text packets THIS box's radio decoded.

INBOX-2 step 2 (2026-11-08). Design + pre-registered acceptance:
``.claude/plans/inbox2_heard_by_design_2026_11_07.md`` (D1).

The inbox host sends a channel post and keeps the radio's ``packet_id``
(step 1). Every other fleet box that decodes it logs, in its own meshtasticd
journal, ``Received text msg from=0x<from>, id=0x<id>, msg=...``. This
section carries ``[from, id, rx_ts]`` for those lines — **never the message
text** — so a center can say "heard by moc, kiai" from evidence the HEARER
wrote, instead of "unconfirmed" forever.

A RING, not a delta: the box keeps the last ``RING_WINDOW_S`` of entries
(cap ``RING_CAP``) in its own state file and serves the whole ring on every
read, so an export whose answer never reaches a center loses nothing.

The journal is read incrementally by CURSOR (measured 2026-11-08: lehua 6 h
cold = 6.3 s, resume-from-cursor = 54 ms). ``journalctl`` refuses
``--since`` together with any cursor option, so this module keeps the cursor
itself (``--show-cursor`` → ``--after-cursor=``) and uses ``--since`` only on
a first read or a reset.

Coverage is what makes ``not_heard`` honest (hfm #2 — absence of evidence is
not evidence of absence). The section says:

* ``covered_since`` — the earliest instant this ring can vouch for: the
  first journal line ever read into it (never the ``--since`` bound — a box
  whose journal starts later must not claim the gap), moved FORWARD by every
  reset, trim and cap. None = nothing read yet → a center reads
  ``unobservable``, never ``not_heard``.
* ``covered_until`` — wall time of the last SUCCESSFUL journal read. A
  failed read keeps the old value, so a stuck reader goes stale instead of
  looking current.
* ``last_line_ts`` — the newest journal line of the unit at all (meshtasticd
  logs ~13 lines/min); a deaf, silent radio shows here as an old value.
* ``read`` — closed vocabulary ``READ_KINDS``.

Pure stdlib, no MeshForge import: byte-identical in MeshForge and MeshAnchor
(``parity_check``). Called only from ``box_truth_collect.collect_sections``,
which runs under the export's flock (S7) — so the ring and its cursor have
ONE writer at a time (hfm #8).
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SOURCE_UNIT = "meshtasticd"
RING_WINDOW_S = 24 * 3600
RING_CAP = 2000
#: How far back a FIRST read (or a reset) looks. Also the stale-cursor bound:
#: a cursor whose last good read is older than this is dropped, so one read
#: never walks more than ~6 h of journal (lehua: ~13k lines, ~1 s warm).
INITIAL_SINCE_S = 6 * 3600
#: One journal read. lehua's COLD 24 h read measured 6.3 s; a 6 h read is
#: bounded below that. The export's whole ssh round trip is 30 s.
JOURNAL_TIMEOUT_S = 8
#: A stamp further ahead of this box's clock than this is not believed
#: (hfm #6: RTC-less Pis, NTP steps).
FUTURE_SLOP_S = 300
STATE_VERSION = 1
STATE_BASENAME = "rx_heard.json"
#: The ring is THIS machine's evidence. journalctl ACCEPTS another machine's
#: cursor (it seeks by the cursor's realtime — measured 2026-11-08, lehua
#: with moc4's cursor: rc 0, 48 lines), so a cloned or rsynced state file
#: would otherwise become this box's hearings and coverage.
MACHINE_ID_PATH = Path("/etc/machine-id")
_MACHINE_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")

#: How the last read ended. Consumers switch on it (hfm #7).
#:   initial      — first read (no cursor yet), from --since
#:   incremental  — resumed after the saved cursor
#:   reset        — the cursor could not be used (rejected by journalctl,
#:                  older than INITIAL_SINCE_S, or from a clock that went
#:                  backward); re-read from --since, coverage restarted
#:   failed       — journalctl timed out / could not run / exited non-zero;
#:                  the ring is served as it was, coverage NOT advanced
READ_KINDS = ("initial", "incremental", "reset", "failed")

_ID_MAX = 0xFFFFFFFF
_CURSOR_RE = re.compile(r"\A[A-Za-z0-9=;_-]{1,512}\Z")
#: Anchored on the router's own prefix so text INSIDE ``msg=`` can never be
#: read as a header (the real header always comes first on the line).
_RX_RE = re.compile(r"\[Router\] Received text msg from=0x([0-9a-fA-F]{1,8}), "
                    r"id=0x([0-9a-fA-F]{1,8}),")
_CURSOR_LINE = "-- cursor: "


def state_path_for(witness: Path) -> Path:
    """The ring lives beside the read witness (S5's app-independent dir)."""
    return witness.with_name(STATE_BASENAME)


def machine_identity(path: Path = MACHINE_ID_PATH) -> Optional[str]:
    """This machine's id, or None when unreadable / not an id. None never
    matches a stored identity — an unknown machine vouches for nothing."""
    try:
        v = path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return None
    return v if _MACHINE_ID_RE.match(v) else None


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) and abs(v) < 1e11 else None


def _empty_state() -> dict:
    return {"v": STATE_VERSION, "machine": None, "cursor": None, "covered_since": None,
            "covered_until": None, "last_line_ts": None, "entries": []}


def _valid_entry(row: Any) -> Optional[List]:
    if not (isinstance(row, list) and len(row) == 3):
        return None
    frm, pid, ts = row
    if (isinstance(frm, bool) or not isinstance(frm, int) or not 0 < frm <= _ID_MAX
            or isinstance(pid, bool) or not isinstance(pid, int)
            or not 0 < pid <= _ID_MAX):
        return None
    ts = _num(ts)
    return None if ts is None else [frm, pid, ts]


def load_state(path: Path) -> Tuple[dict, Optional[str]]:
    """(state, why_reset). A missing file is a clean first read (why None);
    an unreadable or malformed one restarts the ring and SAYS so."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_state(), None
    except (OSError, ValueError) as e:
        return _empty_state(), f"state unreadable ({type(e).__name__}) — ring restarted"
    try:
        doc = json.loads(raw)
    except ValueError:
        return _empty_state(), "state corrupt (not JSON) — ring restarted"
    if not isinstance(doc, dict) or doc.get("v") != STATE_VERSION \
            or not isinstance(doc.get("entries"), list):
        return _empty_state(), "state shape unknown — ring restarted"
    st = _empty_state()
    mid = doc.get("machine")
    st["machine"] = mid if isinstance(mid, str) else None
    cur = doc.get("cursor")
    st["cursor"] = cur if isinstance(cur, str) and _CURSOR_RE.match(cur) else None
    for k in ("covered_since", "covered_until", "last_line_ts"):
        st[k] = _num(doc.get(k))
    st["entries"] = [e for e in (_valid_entry(r) for r in doc["entries"]) if e]
    return st, None


def save_state(path: Path, state: dict) -> None:
    """temp + fsync + rename, 0600. Raises OSError."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = json.dumps(state, ensure_ascii=True, separators=(",", ":"))
    fd, tmp = tempfile.mkstemp(prefix=".rx_heard.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def parse_journal(text: str) -> Tuple[List[List], Optional[str], Optional[float], Optional[float]]:
    """``journalctl -o short-unix --show-cursor`` output →
    (entries, cursor, first_line_ts, last_line_ts). Never message text."""
    entries: List[List] = []
    cursor = None
    first = last = None
    for line in text.splitlines():
        if line.startswith(_CURSOR_LINE):
            c = line[len(_CURSOR_LINE):].strip()
            cursor = c if _CURSOR_RE.match(c) else None
            continue
        head, _, _rest = line.partition(" ")
        try:
            ts = float(head)
        except ValueError:
            continue
        if _num(ts) is None:
            continue
        first = ts if first is None else first
        last = ts
        m = _RX_RE.search(line)
        if not m:
            continue
        frm, pid = int(m.group(1), 16), int(m.group(2), 16)
        if pid and frm:                    # from=0x0 is this box's OWN client
            entries.append([frm, pid, ts])
    return entries, cursor, first, last


def _argv(unit: str, *, cursor: Optional[str], since: float) -> List[str]:
    base = ["journalctl", "-u", unit, "-o", "short-unix", "--no-pager", "-q",
            "--show-cursor"]
    if cursor:
        return base + [f"--after-cursor={cursor}"]
    return base + [f"--since=@{int(since)}"]


def _journal(runner: Callable[..., Any], argv: List[str],
             timeout_s: float) -> Tuple[Optional[int], str, str]:
    try:
        proc = runner(argv, capture_output=True, timeout=timeout_s,
                      stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, "", f"journalctl timed out after {timeout_s:.0f}s"
    except OSError as e:
        return None, "", f"journalctl could not run: {type(e).__name__}"
    out = proc.stdout or b""
    err = proc.stderr or b""
    out = out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out)
    err = err.decode("utf-8", "replace") if isinstance(err, bytes) else str(err)
    return proc.returncode, out, err.strip()[:160]


def _merge(old: List[List], new: List[List]) -> List[List]:
    """Union on (from, id), keeping the EARLIEST stamp (a re-decode of the
    same packet is not a second hearing). Sorted oldest first."""
    seen: Dict[Tuple[int, int], List] = {}
    for e in list(old) + list(new):
        k = (e[0], e[1])
        if k not in seen or e[2] < seen[k][2]:
            seen[k] = e
    return sorted(seen.values(), key=lambda e: e[2])


def section_of(state: dict, *, read: str, lines: int, future_dropped: int,
               note: Optional[str]) -> dict:
    return {"source": f"{SOURCE_UNIT} journal",
            "entries": [[e[0], e[1], round(e[2], 3)] for e in state["entries"]],
            "covered_since": state["covered_since"],
            "covered_until": state["covered_until"],
            "last_line_ts": state["last_line_ts"],
            "read": read, "lines_read": lines, "future_dropped": future_dropped,
            "window_s": RING_WINDOW_S, "cap": RING_CAP, "note": note}


_UNSET = object()


def read_rx_heard(state_path: Path, runner: Callable[..., Any], *, now: float,
                  unit: str = SOURCE_UNIT, timeout_s: float = JOURNAL_TIMEOUT_S,
                  identity: Any = _UNSET) -> Tuple[dict, Optional[str]]:
    """Advance the ring from the journal and return (section, error).

    Never raises for a data problem. ``error`` is set only when something is
    WRONG (journalctl failed, the state could not be saved) — a box with no
    meshtasticd journal at all reads an empty ring with ``covered_since``
    None and no error (absent ≠ error)."""
    if identity is _UNSET:
        identity = machine_identity()
    state, note = load_state(state_path)
    had_data = bool(state["cursor"] or state["entries"]
                    or state["covered_since"] is not None)
    if had_data and (identity is None or state["machine"] != identity):
        # Not ours (a clone / rsync), or we cannot tell: its hearings and
        # its coverage are another machine's — drop them, never inherit.
        note = ("ring state is from another machine (or this machine's id is "
                "unreadable) — ring restarted")
        state = _empty_state()
    state["machine"] = identity
    since = now - INITIAL_SINCE_S
    cursor = state["cursor"]
    kind = "incremental" if cursor else ("reset" if note else "initial")
    cu = state["covered_until"]
    if cursor and (cu is None or cu < since or cu > now + FUTURE_SLOP_S):
        why = ("clock went backward" if cu is not None and cu > now + FUTURE_SLOP_S
               else "cursor older than the first-read window")
        note = f"{why} — coverage restarted"
        cursor, kind = None, "reset"

    rc, out, err = _journal(runner, _argv(unit, cursor=cursor, since=since), timeout_s)
    rejected = None
    if rc not in (None, 0) and cursor:
        # journalctl could not SEEK to the cursor (measured: only a malformed
        # cursor fails — a foreign machine's cursor is accepted, which is why
        # the state carries the machine id). Re-read from --since.
        rejected = err or f"exit {rc}"
        note = f"cursor rejected by journalctl ({rejected}) — coverage restarted"
        cursor, kind = None, "reset"
        rc, out, err = _journal(runner, _argv(unit, cursor=None, since=since), timeout_s)
    if rc != 0:
        why = err or f"journalctl exited {rc}"
        if rejected is not None:
            note = (f"cursor rejected by journalctl ({rejected}) and the --since "
                    f"re-read failed too — nothing restarted")
        return section_of(state, read="failed", lines=0, future_dropped=0,
                          note=note), f"rx_heard: {why}"

    entries, new_cursor, first, last = parse_journal(out)
    if kind == "incremental" and new_cursor is None:
        # A resume ALWAYS prints the cursor, even with no new entry (measured);
        # rc 0 with nothing at all means journalctl now sees NO entries for the
        # unit — renamed, or this user lost journal access. Coverage must not
        # advance over a journal this box can no longer read (hfm #1).
        return section_of(state, read="failed", lines=0, future_dropped=0,
                          note=note), (f"rx_heard: journalctl returned no entries for "
                                       f"{unit} (not even a cursor) — unit renamed, or "
                                       f"this user lost journal access (adm group?)")
    future = [e for e in entries if e[2] > now + FUTURE_SLOP_S]
    entries = [e for e in entries if e[2] <= now + FUTURE_SLOP_S]
    lines = sum(1 for ln in out.splitlines() if not ln.startswith(_CURSOR_LINE))

    if kind != "incremental":
        # Coverage (re)starts at the first line ACTUALLY read — never at the
        # --since bound, which the journal may not reach.
        state["covered_since"] = first
    elif state["covered_since"] is None and first is not None:
        state["covered_since"] = first
    state["entries"] = _merge(state["entries"], entries)
    if new_cursor:
        state["cursor"] = new_cursor
    elif kind != "incremental":
        state["cursor"] = None
    if state["covered_since"] is not None or new_cursor:
        state["covered_until"] = now
    if last is not None:
        state["last_line_ts"] = last

    floor = now - RING_WINDOW_S
    state["entries"] = [e for e in state["entries"] if e[2] >= floor]
    if state["covered_since"] is not None and state["covered_since"] < floor:
        state["covered_since"] = floor
    if len(state["entries"]) > RING_CAP:
        state["entries"] = state["entries"][-RING_CAP:]
        oldest = state["entries"][0][2]
        if state["covered_since"] is None or state["covered_since"] < oldest:
            state["covered_since"] = oldest

    error = None
    try:
        save_state(state_path, state)
    except OSError as e:
        error = (f"rx_heard: ring state unwritable at {state_path.parent}: "
                 f"{type(e).__name__} — next read repeats from the old cursor")
    return section_of(state, read=kind, lines=lines, future_dropped=len(future),
                      note=note), error
