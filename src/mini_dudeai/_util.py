"""Internal stdlib-only utilities. No third-party deps."""
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


def read_json(path):
    """Return (data, None) on success, (None, error_str) on failure. Never raises."""
    try:
        with open(path) as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, "not found"
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
