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
  role           — the box's OWN verdict on what its declared role expects
                   (session 3 step 2, 2026-10-14): the role name, the
                   resolved ``services`` map it rests on, and
                   ``expected.http_surface`` / ``expected.watchdog`` as
                   tri-states. Judged HERE through this app's provisioner +
                   catalog, so a center of either app reads a verdict and
                   never needs the target app's catalog. ``why`` says what
                   made it undecidable; None is never collapsed to False.

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
import http.client
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from utils import box_truth as bt
from utils import box_truth_app as app
from utils.paths import atomic_write_text

#: The map's VIEW routes the export reads (S4 — a test pins that neither is
#: in the map's OPERATOR_GETS).
HTTP_PATHS = {"slo": "/fleet/slo", "status": "/api/status"}
HTTP_TIMEOUT_S = 6.0
MAX_FILE_BYTES = 8 * 1024 * 1024
SUBPROCESS_TIMEOUT_S = 10
WATCHDOG_PATH = app.WATCHDOG_PATH
#: Role-catalog unit states that mean "this box is not supposed to run it".
NOT_EXPECTED = ("disabled", "absent")
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
    provisioner: Path = app.PROVISIONER
    catalog: Path = app.ROLE_CATALOG
    http_surface_unit: str = app.HTTP_SURFACE_UNIT
    watchdog_unit: str = app.WATCHDOG_UNIT

    def _xdg(self, var: str, default: str) -> Path:
        v = self.env.get(var)
        return Path(v) if v else self.home / default

    @property
    def deployment_path(self) -> Path:
        return self._xdg("XDG_CONFIG_HOME", ".config") / app.CONFIG_DIRNAME / "deployment.json"

    @property
    def mini_path(self) -> Path:
        return self.home / app.MINI_STATE_RELPATH

    @property
    def verdicts_path(self) -> Path:
        return self.home / "cron_verdicts.log"

    @property
    def ingress_path(self) -> Path:
        return (self._xdg("XDG_DATA_HOME", ".local/share") / app.CONFIG_DIRNAME
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
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError,
            socket.timeout) as e:
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
    rc, out = _run(src, ["systemctl", "list-unit-files", app.UNIT_GLOB,
                         "--no-legend"])
    if rc is None:
        return None, "systemctl list-unit-files failed"
    units = ["meshtasticd", "rnsd"]
    for line in out.decode("utf-8", "replace").splitlines():
        f = line.split()
        if f and f[0] not in units:
            units.append(f[0])
    show = ["systemctl", "show", "--property=ActiveState,UnitFileState"]
    rc, out = _run(src, show + units)
    if rc is None:
        return None, "systemctl show failed"
    if rc == 0:
        blocks = out.decode("utf-8", "replace").strip("\n").split("\n\n")
    else:
        # A bad unit name stops the batch after the units before it (measured
        # 2026-10-14, review B 5) — ask each unit on its own instead of
        # reading every later unit as unknown.
        blocks = []
        for unit in units:
            urc, uout = _run(src, show + [unit])
            blocks.append(uout.decode("utf-8", "replace").strip("\n") if urc == 0 else "")
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


def unit_expected(services: Any, unit: str) -> Optional[bool]:
    """Does a resolved role's ``services`` map expect ``unit`` to run?

    True / False when the role says something about the unit; None when it
    is silent, because silence is not a declaration. None is load-bearing
    and must never collapse to False: False is what lets the fleet verdict
    stop tainting on a gap, so guessing it would quiet a broken box."""
    if not isinstance(services, dict):
        return None
    state = services.get(unit)
    if not isinstance(state, str):
        return None
    return state.strip().lower() not in NOT_EXPECTED


def _load_provisioner(path: Path):
    """importlib-load this app's provisioner from its script path.

    Registered in sys.modules BEFORE exec (py3.12+ @dataclass resolves
    annotations via sys.modules[cls.__module__]; unregistered it raises
    mid-import — the repo paid for this twice already). The provisioner
    ``sys.exit``s at import when its YAML dependency is missing: SystemExit
    is not an Exception, so it is turned into one here instead of killing
    the export mid-document."""
    spec = importlib.util.spec_from_file_location("box_truth_provisioner", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"no loader for {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except SystemExit as e:
        raise ImportError(f"{path.name} exited {e.code} at import") from None
    return mod


def _role(src: Sources) -> Tuple[dict, Optional[str]]:
    """The box's own role verdict. Returns (section, error): the section is
    ALWAYS a full object (expected tri-states present), ``why`` names what
    made it undecidable; ``error`` is set only when the tooling failed
    (provisioner/catalog unloadable), never for an absent or silent
    declaration."""
    out: Dict[str, Any] = {"app": app.APP, "name": None, "catalog": str(src.catalog),
                           "services": None,
                           "expected": {"http_surface": None, "watchdog": None},
                           "why": None}
    dep, dep_err = _json_file(src.deployment_path)
    if dep is None:
        out["why"] = (f"no role declared: deployment.json "
                      f"{dep_err or 'absent'} at {src.deployment_path}")
        return out, None
    role = dep.get("role")
    if not isinstance(role, str) or not role:
        out["why"] = "no role declared in deployment.json"
        return out, None
    out["name"] = role
    try:
        mod = _load_provisioner(src.provisioner)
        role_def = mod.resolve_role(mod.load_roles(src.catalog), role)
    except KeyError as e:
        missing = e.args[0] if e.args else None
        if missing == role:
            out["why"] = f"role {role!r} is not in the catalog {src.catalog}"
            return out, None
        # A KeyError for ANOTHER name is a broken catalog (an `inherits` to a
        # role that does not exist), not an undeclared role — a tooling error.
        out["why"] = "role undecidable: provisioner or catalog unloadable"
        return out, f"KeyError: catalog names {missing!r}, which it does not define"[:200]
    except Exception as e:  # noqa: BLE001 - tooling failure, reported as THIS section's error
        out["why"] = "role undecidable: provisioner or catalog unloadable"
        return out, f"{type(e).__name__}: {e}"[:200]
    services = role_def.get("services") if isinstance(role_def, dict) else None
    if not isinstance(services, dict):
        out["why"] = f"role {role!r} declares no services"
        return out, None
    # Judged from the RAW map (a non-string state is undecidable, as the
    # center always held); carried on the wire as strings.
    out["services"] = {str(k): str(v) for k, v in services.items()}
    out["expected"] = {"http_surface": unit_expected(services, src.http_surface_unit),
                       "watchdog": unit_expected(services, src.watchdog_unit)}
    # Every None verdict says why (the section's contract): silent, or a
    # state that is not a string.
    gaps = [f"{u} {'silent' if u not in services else 'state ' + repr(services[u]) + ' is not a string'}"
            for u in (src.http_surface_unit, src.watchdog_unit)
            if unit_expected(services, u) is None]
    if gaps:
        out["why"] = f"role {role!r} undecidable for: " + "; ".join(gaps)
    return out, None


def collect_sections(src: Sources) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """(sections, section_errors). Every allowlisted key is present; a value
    of None means unobservable (the error map says why when it is an error,
    and is silent when the source is simply absent on this box)."""
    sections: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    def put(name, reader, *args):
        """One section's failure is THAT section's error, never the document's
        (review B 1: one bad HTTP answer used to black out all ten)."""
        try:
            got = reader(*args)
        except Exception as e:  # noqa: BLE001 - any reader crash, recorded below
            sections[name] = None
            errors[name] = f"{type(e).__name__}: {e}"[:200]
            return
        val, err = got if isinstance(got, tuple) else (got, None)
        sections[name] = val
        if err:
            errors[name] = err

    put("slo", _http, src, HTTP_PATHS["slo"])
    put("status", _http, src, HTTP_PATHS["status"])
    put("raw_watchdog", _json_file, src.watchdog_path)
    put("deployment", _json_file, src.deployment_path)
    put("raw_mini", _json_file, src.mini_path)
    put("radio_probe", _radio_probe, src)
    put("services", _services, src)
    put("schedules", _schedules, src)
    put("clock", lambda: {"now": int(time.time())})
    put("ingress", _json_file, src.ingress_path)
    put("role", _role, src)
    assert tuple(sections) == bt.SECTIONS, "section order drifted from the allowlist"
    return sections, errors


# ── the read witness (Q13, B4, B8, S7) ───────────────────────────────────

def read_witness(path: Path) -> List[dict]:
    """Every parseable witness row, oldest first. [] when absent."""
    try:
        data = path.read_bytes()
    except OSError:
        return []
    rows = []
    for line in data.decode("utf-8", "replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def last_reads(path: Path) -> Dict[str, dict]:
    """{fingerprint: newest SERVED row} — the 'last read' a grant list shows."""
    out: Dict[str, dict] = {}
    for row in read_witness(path):
        if row.get("outcome") == "served" and isinstance(row.get("fp"), str):
            out[row["fp"]] = row
    return out


def lock_path(witness: Path) -> Path:
    """A SEPARATE lock file that nothing ever replaces. The lock used to live
    on reads.jsonl itself, which trim_witness replaces by rename — a waiter
    then held a lock on an unlinked inode and its row was lost (review A F5:
    200 reads -> ~55 rows)."""
    return witness.with_name("reads.lock")


def _acquire(witness: Path):
    """(fd, locked). Raises OSError when the state dir is unusable."""
    witness.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(lock_path(witness)), os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd, True
        except OSError:
            if time.monotonic() >= deadline:
                return fd, False
            time.sleep(0.1)  # bounded wait in a one-shot CLI, not a daemon loop


def _append(witness: Path, row: dict) -> None:
    fd = os.open(str(witness), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(row, ensure_ascii=True, separators=(",", ":"))
                      + "\n").encode("ascii"))
    finally:
        os.close(fd)


def trim_witness(witness: Path) -> None:
    """Keep the newest WITNESS_CAP rows once past WITNESS_TRIM_AT. Call only
    while holding the lock (the lock file is never the one replaced)."""
    rows = read_witness(witness)
    if len(rows) <= WITNESS_TRIM_AT:
        return
    keep = "".join(json.dumps(r, ensure_ascii=True, separators=(",", ":")) + "\n"
                   for r in rows[-WITNESS_CAP:])
    atomic_write_text(witness, keep)          # temp + fsync + rename, 0600


_FP_ARG = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")


def _app_version() -> str:
    return app.app_version()


def _refusal(kind: str, fp: Optional[str], reason: str) -> str:
    return bt.frame_document({"schema": bt.SCHEMA, "refused": kind, "fp": fp,
                              "reason": reason, "window_s": bt.RATE_WINDOW_S,
                              "max": bt.RATE_MAX})


def run_export(grant_fp: Optional[str], src: Sources, *,
               now: Optional[float] = None) -> str:
    """Produce the framed document — or a REFUSAL — and record the read.

    Fails CLOSED (review A F4): a read that cannot be recorded is not served,
    because the record IS the rate limit (B8) and the "last read from" a box
    owner audits. Refusals: ``rate`` (over the limit), ``busy`` (another read
    held the lock past LOCK_WAIT_S), ``witness`` (the state dir is unusable —
    the reason names the path). Never raises for a data problem."""
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

    def ms():
        return int((time.monotonic() - t0) * 1000)

    try:
        lock_fd, locked = _acquire(wpath)
    except OSError as e:
        return _refusal("witness", fp, f"read witness unwritable at {wpath.parent}: "
                                       f"{type(e).__name__} — fix ownership/mode")
    try:
        if not locked:
            return _refusal("busy", fp, f"another read held the lock > {LOCK_WAIT_S:.0f}s")
        try:
            recent = [r for r in read_witness(wpath)
                      if r.get("fp") == fp and isinstance(r.get("ts"), (int, float))
                      and ts - bt.RATE_WINDOW_S < r["ts"] <= ts]
            if len(recent) >= bt.RATE_MAX:
                text = _refusal("rate", fp, f"> {bt.RATE_MAX} reads in "
                                            f"{bt.RATE_WINDOW_S:.0f}s for this grant")
                _append(wpath, {**row, "outcome": "refused_rate", "bytes": len(text),
                                "ms": ms()})
                trim_witness(wpath)            # refusals are bounded too (F4c)
                return text

            sections, errors = collect_sections(src)
            tripped = bt.secret_tripwire(sections)
            for name, _pat in tripped:
                sections[name] = None
                errors[name] = "withheld: credential-shaped content (tripwire)"
            doc = {"schema": bt.SCHEMA, "app": app.APP, "app_version": _app_version(),
                   "host": socket.gethostname(), "generated_at": ts, "grant": fp,
                   "sections": sections, "section_errors": errors,
                   "tripwire": sorted({n for n, _ in tripped}), "witness": "ok"}
            text = bt.frame_document(doc)
            _append(wpath, {**row, "outcome": "served", "bytes": len(text), "ms": ms()})
            trim_witness(wpath)
            return text
        except OSError as e:
            return _refusal("witness", fp, f"read witness write failed at {wpath}: "
                                           f"{type(e).__name__} — fix ownership/mode")
    finally:
        os.close(lock_fd)
