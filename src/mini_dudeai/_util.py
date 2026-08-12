"""Internal stdlib-only utilities. No third-party deps."""
import datetime
import gzip
import http.client
import json
import os
import tempfile
import urllib.error
import urllib.request
import zlib

# Cap for HTTP bodies (wire AND decompressed). mini's sources read small
# status APIs; anything near this is a wrong endpoint or a runaway response,
# and slurping it unbounded on a 512MB-class box is worse than erroring.
DEFAULT_FETCH_MAX_BYTES = 8_000_000

# ── app adapter (the twin-divergence point) ─────────────────────────────────
# The mini engine core is byte-locked across the MeshForge/MeshAnchor twins
# (parity_check tiers 1-2), so any APP-specific value a locked module needs
# lives HERE — this file is the deliberately-UNLOCKED namespacing/adapter
# seam, and the MA copy carries MA's values. Before 07-23, locked files
# (brief.py, warmstart.py) carried literal meshforge unit/repo/preset names:
# on the dual-stack box MA's warm-start attributed MeshForge's HEAD and
# honest-verdict to MeshAnchor-side claims, and MA's rendered remediation
# named units that don't exist there. Locked modules import these; never
# hardcode an app name in a byte-locked file again.
APP_MINI_UNIT = "meshforge-mini-dudeai"      # the mini daemon's --user unit
APP_FLEET_PRESET = "meshforge_fleet"         # the fleet preset name
APP_REPO_ENV = "MESHFORGE_REPO"              # env override for the repo root
APP_REPO_DEFAULT = "/opt/meshforge"          # repo root (calibration HEAD)
APP_VERDICT_SUBDIR = os.path.join(".cache", "meshforge")  # honest_verdict dir

# Artifact PATHS joined this seam 2026-08-11. The 07-23 pass carried
# unit/repo/preset NAMES through the adapter but not paths, so the byte-locked
# readers (warmstart; the rollups' basename constants) kept MeshForge-
# convention locations baked in — and MA's copies read paths its daemon never
# writes. Measured on the MA replica: bare warmstart reported "mini has not
# run here" beside a daemon ticking 30s away. Reader and writer both resolve
# through these (honest_failure_modes #4: reader/writer pairs wire together).
# All are RELATIVE to the mini home (resolve_home) so the rollup's remote
# `cat` (cwd = the remote $HOME) and local joins agree on one value.
APP_MINI_SUBDIR = ""                         # MF artifacts live in the mini home itself
APP_BRIEF_RELPATH = "mini_dudeai_brief.md"
APP_STATE_RELPATH = "mini_dudeai_state.json"
APP_HISTORY_RELPATH = "mini_dudeai_history.jsonl"


def app_artifact_paths(home=None):
    """(brief, state, history) absolute paths THIS app's fleet-preset daemon
    writes — the one place readers and the preset writer both resolve."""
    home = home or resolve_home()
    return (os.path.join(home, APP_BRIEF_RELPATH),
            os.path.join(home, APP_STATE_RELPATH),
            os.path.join(home, APP_HISTORY_RELPATH))


def resolve_home():
    """The ONE home-dir resolution for mini artifacts: $MINI_DUDEAI_HOME → ~.

    Seven call sites used to hand-roll this with THREE different precedence
    rules — audit.py honored MINI_DUDEAI_HOME while the daemon/dreams/rollup
    ignored it, so the honesty audit could certify a DIFFERENT artifact dir
    than the one the daemon actually wrote. One resolver, every consumer.
    """
    env = os.environ.get("MINI_DUDEAI_HOME")
    if env:
        return os.path.expanduser(env)
    return os.path.expanduser("~")


def operator_home():
    """The HUMAN operator's home — sudo-safe. NOT resolve_home():
    resolve_home() locates MINI artifacts (honors MINI_DUDEAI_HOME);
    this locates files the OPERATOR owns (the Claude memory corpus).
    Under `sudo python3` (the TUI's launch mode) expanduser('~') is
    /root and the operator's corpus silently vanishes from any reader
    that used it — SUDO_USER is the honest anchor there.
    """
    su = os.environ.get("SUDO_USER")
    if su:
        try:
            import pwd
            return pwd.getpwnam(su).pw_dir
        except (ImportError, KeyError):
            pass
    return os.path.expanduser("~")


def iso_or_none(ts):
    """Human-readable ISO stamp beside an epoch ts — None when the epoch
    can't render (RTC-less fleet: an absurd/negative clock must not crash
    a witness write; the epoch field stays the machine truth). ONE helper —
    this idiom was copy-pasted into three modules before it lived here.
    """
    try:
        return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _journal_prio(n):
    """sd-daemon priority prefix — journald maps '<N>' line prefixes on
    stdout/stderr to log priorities, so `journalctl -p err` filtering works
    fleet-wide with zero dependencies. Outside systemd (no JOURNAL_STREAM)
    the prefix is omitted and output is unchanged (tests, CLI)."""
    return f"<{n}>" if os.environ.get("JOURNAL_STREAM") else ""


def log_error(msg):
    """Failure that needs operator attention (journald priority err)."""
    print(f"{_journal_prio(3)}{msg}", flush=True)


def log_warning(msg):
    """Degraded-but-coping (journald priority warning)."""
    print(f"{_journal_prio(4)}{msg}", flush=True)


def log_info(msg):
    """Routine narration (journald priority info)."""
    print(f"{_journal_prio(6)}{msg}", flush=True)


# Sentinel for the file-absent leg — importable so consumers branching on
# "absent vs unreadable" (kilo's inert leg) don't hardcode the string.
READ_JSON_NOT_FOUND = "not found"


def read_json(path):
    """Return (data, None) on success, (None, error_str) on failure. Never raises.

    File-absent returns (None, READ_JSON_NOT_FOUND) — distinguishable from
    unreadable/corrupt, because absence and breakage are different facts.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, READ_JSON_NOT_FOUND
    except (OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}"


def _atomic_write(path, payload, mode="w"):
    """Unique tmp + fsync + os.replace.

    - Unique tmp name (mkstemp): two processes writing the same target never
      interleave inside one tmp file (the --once-vs-daemon collision class).
    - fsync before replace: a hard power cut cannot publish the rename ahead
      of the data (ext4 auto_da_alloc usually saves us, but it is a heuristic,
      not a contract — memory_apply._atomic_write set this standard).
    - tmp unlinked on failure: no .tmp litter accumulating next to the target.
    Raises OSError on failure — callers own their swallow-or-surface policy.
    """
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, mode) as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, data):
    """Atomic JSON write — unique tmp + fsync + os.replace."""
    _atomic_write(path, json.dumps(data, indent=2, default=str))


def atomic_write_text(path, text):
    """Atomic text write — same guarantees as atomic_write_json."""
    _atomic_write(path, text)


def atomic_write_bytes(path, payload):
    """Atomic bytes write — same guarantees as atomic_write_json."""
    _atomic_write(path, payload, mode="wb")


def fetch_json(url, timeout=8, max_bytes=DEFAULT_FETCH_MAX_BYTES):
    """GET a URL, decode JSON (gzip-aware). Returns (data, None) or (None, error).

    Never raises: the except tuple must cover every exception the body can
    actually produce — we request gzip, so gzip.decompress can raise EOFError
    or zlib.error on a truncated body, and r.read() can raise
    http.client.IncompleteRead (HTTPException, NOT an OSError). Those escaping
    this function was the contract violation that crashed sources mid-collect.
    """
    try:
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return None, f"response exceeded {max_bytes} bytes"
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
                if len(raw) > max_bytes:
                    return None, f"decompressed response exceeded {max_bytes} bytes"
            return json.loads(raw.decode("utf-8", "replace")), None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError,
            http.client.HTTPException, EOFError, zlib.error) as e:
        return None, f"{type(e).__name__}: {e}"
