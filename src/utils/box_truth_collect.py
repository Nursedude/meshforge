"""box_truth/v1 export — what a box reads about ITSELF for a granted center.

Runs as the forced command of a NOC read grant (``scripts/box_truth_export.py``)
and as the payload of the legacy spool path. It replaces the inline shell
string ``scripts/fleet_truth_spool.py`` used to send (``_REMOTE_CMD``): a
forced command cannot sensibly allow an arbitrary shell string, so the payload
had to become ONE program the box owns.

Every section is byte-compatible with what the spool carried before, so the
collector (``utils.fleet_truth_collector``) reads it unchanged:

  slo / status   — the map's own VIEW endpoints over loopback (S4: never a
                   route behind the operator session)
  raw_watchdog   — /var/lib/meshforge/watchdog.json
  deployment     — the box's own role declaration
  raw_mini       — ~/mini_dudeai_state.json
  radio_probe    — /proc LISTEN state for :4403 + USB presence. NEVER a
                   connect to :4403 (#17/#75 PhoneAPI contention; N4).
  services       — systemd ActiveState / UnitFileState
  schedules      — crontab + the last TWO cron verdicts per name, base64
  clock          — this box's own clock
  ingress        — the RNS->RF ingress ledger

Security-pass bindings (``.claude/audits/noc_read_grant_security_pass_2026_10_14.md``):
B1 never reads stdin, never dispatches on SSH_ORIGINAL_COMMAND (recorded as
data); B2 utf-8 everywhere + ASCII-only output; B4 the grant fingerprint
arrives on argv; B7b allowlisted sections + a tripwire that REPLACES a tripped
section; B8 per-fingerprint rate limit from the witness; S5 an app-independent
witness path; S7 an flock that serialises concurrent exports.
"""
from __future__ import annotations

import base64
import errno
import fcntl
import glob
import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from utils import box_truth as bt
from utils.paths import atomic_write_text

#: The map's VIEW routes the export reads (S4 — a test pins that neither is
#: in the map's OPERATOR_GETS).
HTTP_PATHS = {"slo": "/fleet/slo", "status": "/api/status"}
HTTP_TIMEOUT_S = 6.0
MAX_FILE_BYTES = 8 * 1024 * 1024
SUBPROCESS_TIMEOUT_S = 10
WATCHDOG_PATH = Path("/var/lib/meshforge/watchdog.json")
PROC_TCP = ("/proc/net/tcp", "/proc/net/tcp6")
DEV_GLOBS = ("/dev/ttyUSB*", "/dev/ttyACM*")
_MESHTASTIC_PORT_HEX = "1133"          # :4403, read from /proc only
_LISTEN = "0A"

WITNESS_CAP = 1000                     # rows kept (Q13)
WITNESS_TRIM_AT = 1200                 # rewrite when the file grows past this
LOCK_WAIT_S = 20.0
ORIG_CMD_MAX = 120


@dataclass
class Sources:
    """Everything the export touches, injectable for tests."""
    home: Path
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    http_port: int = 5000
    watchdog_path: Path = WATCHDOG_PATH
    proc_tcp: Tuple[str, ...] = PROC_TCP
    dev_globs: Tuple[str, ...] = DEV_GLOBS
    runner: Callable[..., Any] = subprocess.run
    http_get: Optional[Callable[[str], Optional[dict]]] = None

    def _xdg(self, var: str, default: str) -> Path:
        v = self.env.get(var)
        return Path(v) if v else self.home / default

    @property
    def deployment_path(self) -> Path:
        return self._xdg("XDG_CONFIG_HOME", ".config") / "meshforge" / "deployment.json"

    @property
    def mini_path(self) -> Path:
        return self.home / "mini_dudeai_state.json"

    @property
    def verdicts_path(self) -> Path:
        return self.home / "cron_verdicts.log"

    @property
    def ingress_path(self) -> Path:
        return (self._xdg("XDG_DATA_HOME", ".local/share") / "meshforge"
                / "rns_ingress_ledger.json")

    @property
    def witness_path(self) -> Path:
        return witness_path(self.home, self.env)


def witness_path(home: Path, env: Mapping[str, str]) -> Path:
    """``$XDG_STATE_HOME/box_truth/reads.jsonl`` — app-independent (S5), so
    a box running both apps keeps ONE record of who read it."""
    v = env.get("XDG_STATE_HOME")
    base = Path(v) if v else home / ".local" / "state"
    return base / "box_truth" / "reads.jsonl"


# ── section readers — each returns (value, error) and never raises ──────

def _read_text(path: Path) -> str:
    with open(path, "rb") as fh:
        data = fh.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{path.name} larger than {MAX_FILE_BYTES} bytes")
    return data.decode("utf-8", "replace")


def _json_file(path: Path) -> Tuple[Optional[dict], Optional[str]]:
    try:
        doc = json.loads(_read_text(path))
    except FileNotFoundError:
        return None, None                     # absent — not an error
    except (OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}"[:200]
    if not isinstance(doc, dict):
        return None, "not a JSON object"
    return doc, None


def _default_http_get(url: str) -> Optional[dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310 - loopback only
        data = resp.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("response larger than cap")
    doc = json.loads(data.decode("utf-8", "replace"))
    return doc if isinstance(doc, dict) else None


def _http(src: Sources, path: str) -> Tuple[Optional[dict], Optional[str]]:
    getter = src.http_get or _default_http_get
    try:
        return getter(f"http://localhost:{src.http_port}{path}"), None
    except (urllib.error.URLError, OSError, ValueError, socket.timeout) as e:
        # A map-less box has no listener — that is the normal case for the
        # boxes this whole leg exists for, and reads as absent, not error.
        reason = getattr(e, "reason", e)
        if isinstance(reason, ConnectionRefusedError) or (
                isinstance(reason, OSError) and reason.errno == errno.ECONNREFUSED):
            return None, None
        return None, f"{type(e).__name__}: {e}"[:200]


def _radio_probe(src: Sources) -> dict:
    listening = False
    for p in src.proc_tcp:
        try:
            text = _read_text(Path(p))
        except OSError:
            continue
        for line in text.splitlines()[1:]:
            f = line.split()
            if len(f) > 3 and f[3] == _LISTEN and f[1].endswith(":" + _MESHTASTIC_PORT_HEX):
                listening = True
    usb = any(glob.glob(g) for g in src.dev_globs)
    return {"tcp_listening": listening, "usb_present": usb}


def _run(src: Sources, argv: List[str]) -> Tuple[Optional[int], bytes]:
    try:
        proc = src.runner(argv, capture_output=True, timeout=SUBPROCESS_TIMEOUT_S,
                          stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError):
        return None, b""
    return proc.returncode, proc.stdout or b""


def _services(src: Sources) -> Tuple[Optional[dict], Optional[str]]:
    rc, out = _run(src, ["systemctl", "list-unit-files", "meshforge-*.service",
                         "--no-legend"])
    if rc is None:
        return None, "systemctl list-unit-files failed"
    units = ["meshtasticd", "rnsd"]
    for line in out.decode("utf-8", "replace").splitlines():
        f = line.split()
        if f and f[0] not in units:
            units.append(f[0])
    rc, out = _run(src, ["systemctl", "show", "--property=ActiveState,UnitFileState",
                         *units])
    if rc is None:
        return None, "systemctl show failed"
    blocks = out.decode("utf-8", "replace").strip("\n").split("\n\n")
    result: Dict[str, dict] = {}
    for i, unit in enumerate(units):
        props: Dict[str, str] = {}
        if i < len(blocks):
            for kv in blocks[i].splitlines():
                k, _, v = kv.partition("=")
                props[k] = v
        result[unit] = {"active": props.get("ActiveState") or "unknown",
                        "enabled": props.get("UnitFileState") or "unknown"}
    return result, None


def last_two_verdicts(text: str) -> str:
    """The last TWO lines per verdict name (field 2), oldest first. Two is
    the minimum the fast-cron confirmation gate reads (2026-09-13: one line
    left every hourly FAIL 'unconfirmed' forever)."""
    order: List[str] = []
    newest: Dict[str, str] = {}
    prev: Dict[str, str] = {}
    for line in text.splitlines():
        f = line.split()
        key = f[1] if len(f) > 1 else ""
        if key in newest:
            prev[key] = newest[key]
        else:
            order.append(key)
        newest[key] = line
    out: List[str] = []
    for k in order:
        if k in prev:
            out.append(prev[k])
        out.append(newest[k])
    return "".join(line + "\n" for line in out)


def _schedules(src: Sources) -> dict:
    rc, crontab = _run(src, ["crontab", "-l"])
    ct = base64.b64encode(crontab).decode("ascii") if rc == 0 and crontab else ""
    try:
        verdicts = last_two_verdicts(_read_text(src.verdicts_path))
    except OSError:
        verdicts = ""
    cv = base64.b64encode(verdicts.encode("utf-8")).decode("ascii") if verdicts else ""
    return {"crontab_b64": ct, "verdicts_b64": cv}


def collect_sections(src: Sources) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """(sections, section_errors). Every allowlisted key is present; a value
    of None means unobservable (the error map says why when it is an error,
    and is silent when the source is simply absent on this box)."""
    sections: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    def put(name, pair):
        val, err = pair
        sections[name] = val
        if err:
            errors[name] = err

    put("slo", _http(src, HTTP_PATHS["slo"]))
    put("status", _http(src, HTTP_PATHS["status"]))
    put("raw_watchdog", _json_file(src.watchdog_path))
    put("deployment", _json_file(src.deployment_path))
    put("raw_mini", _json_file(src.mini_path))
    sections["radio_probe"] = _radio_probe(src)
    put("services", _services(src))
    sections["schedules"] = _schedules(src)
    sections["clock"] = {"now": int(time.time())}
    put("ingress", _json_file(src.ingress_path))
    assert tuple(sections) == bt.SECTIONS, "section order drifted from the allowlist"
    return sections, errors


# ── the read witness (Q13, B4, B8, S7) ───────────────────────────────────

def _read_rows(fh) -> List[dict]:
    fh.seek(0)
    rows = []
    for line in fh.read().decode("utf-8", "replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def read_witness(path: Path) -> List[dict]:
    """Every parseable witness row, oldest first. [] when absent."""
    try:
        with open(path, "rb") as fh:
            return _read_rows(fh)
    except OSError:
        return []


def last_reads(path: Path) -> Dict[str, dict]:
    """{fingerprint: newest SERVED row} — the 'last read' a grant list shows."""
    out: Dict[str, dict] = {}
    for row in read_witness(path):
        if row.get("outcome") == "served" and isinstance(row.get("fp"), str):
            out[row["fp"]] = row
    return out


def _open_locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
    fh = os.fdopen(fd, "a+b")
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh, True
        except OSError:
            if time.monotonic() >= deadline:
                return fh, False
            time.sleep(0.1)  # bounded wait in a one-shot CLI, not a daemon loop


def _append(fh, row: dict) -> None:
    fh.write((json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii"))
    fh.flush()


def _trim(fh, path: Path) -> None:
    rows = _read_rows(fh)
    if len(rows) <= WITNESS_TRIM_AT:
        return
    keep = "".join(json.dumps(r, ensure_ascii=True, separators=(",", ":")) + "\n"
                   for r in rows[-WITNESS_CAP:])
    # temp + fsync + rename, 0600 (mkstemp); the lock holder is the only writer
    atomic_write_text(path, keep)


_FP_ARG = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")


def _app_version() -> str:
    try:
        from __version__ import __version__
        return str(__version__)
    except Exception:
        return "unknown"


def run_export(grant_fp: Optional[str], src: Sources, *,
               now: Optional[float] = None) -> str:
    """Produce the framed document (or a refusal) and record the read.
    Returns the text to write to stdout. Never raises for a data problem."""
    t0 = time.monotonic()
    ts = time.time() if now is None else now
    fp = grant_fp if grant_fp and _FP_ARG.match(grant_fp) else None
    ssh_conn = src.env.get("SSH_CONNECTION", "")
    row = {"ts": ts, "fp": fp,
           "src": ssh_conn.split()[0] if ssh_conn else "local",
           "orig_cmd": src.env.get("SSH_ORIGINAL_COMMAND", "")[:ORIG_CMD_MAX]}
    if grant_fp and fp is None:
        row["fp_malformed"] = grant_fp[:80]

    wpath = src.witness_path
    fh = None
    witness = "ok"
    try:
        fh, locked = _open_locked(wpath)
        if not locked:
            witness = f"lock not acquired in {LOCK_WAIT_S:.0f}s"
    except OSError as e:
        witness = f"unwritable: {type(e).__name__}"

    try:
        if fh is not None:
            recent = [r for r in _read_rows(fh)
                      if r.get("fp") == fp and isinstance(r.get("ts"), (int, float))
                      and ts - bt.RATE_WINDOW_S < r["ts"] <= ts]
            if len(recent) >= bt.RATE_MAX:
                text = bt.frame_document({"schema": bt.SCHEMA, "refused": "rate",
                                          "fp": fp, "window_s": bt.RATE_WINDOW_S,
                                          "max": bt.RATE_MAX})
                _append(fh, {**row, "outcome": "refused_rate", "bytes": len(text),
                             "ms": int((time.monotonic() - t0) * 1000)})
                return text

        sections, errors = collect_sections(src)
        tripped = bt.secret_tripwire(sections)
        for name, _pat in tripped:
            sections[name] = None
            errors[name] = "withheld: credential-shaped content (tripwire)"
        doc = {"schema": bt.SCHEMA, "app": "meshforge", "app_version": _app_version(),
               "host": socket.gethostname(), "generated_at": ts, "grant": fp,
               "sections": sections, "section_errors": errors,
               "tripwire": sorted({n for n, _ in tripped}), "witness": witness}
        text = bt.frame_document(doc)
        if fh is not None:
            try:
                _append(fh, {**row, "outcome": "served", "bytes": len(text),
                             "ms": int((time.monotonic() - t0) * 1000)})
                _trim(fh, wpath)
            except OSError as e:
                # The read went unrecorded: say so in what we serve (hfm #9).
                doc["witness"] = f"append failed: {type(e).__name__}"
                text = bt.frame_document(doc)
        return text
    finally:
        if fh is not None:
            fh.close()
