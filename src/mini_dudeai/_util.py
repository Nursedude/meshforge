"""Internal stdlib-only utilities. No third-party deps."""
import json
import os
import urllib.error
import urllib.request


def read_json(path):
    """Return (data, None) on success, (None, error_str) on failure. Never raises."""
    try:
        with open(path) as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, "not found"
    except (OSError, ValueError) as e:
        return None, f"{type(e).__name__}: {e}"


def atomic_write_json(path, data):
    """Tmp + os.replace — safe against partial writes / power loss."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


def fetch_json(url, timeout=8):
    """GET a URL, decode JSON (gzip-aware). Returns (data, None) or (None, error)."""
    try:
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "replace")), None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as e:
        return None, f"{type(e).__name__}: {e}"
