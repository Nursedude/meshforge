"""An epoch Last-Modified is a SENTINEL, not an age (2026-09-06).

Field verdict that prompted this::

    cloud_map_freshness FAIL(1) | STALE: data.geojson is 1788682838s old
    (max 3600s) -- push chain or domain broken

1788682838s is 56.7 years -- it is ``now - 0``. The server had reported
``Last-Modified: Thu, 01 Jan 1970 00:00:00 GMT`` (an rsync that landed the
file with a zero mtime, or a server with no mtime to report). That string
PARSES, so the script's ``-z`` unparseable guard waved it through and the
sentinel became a measurement: a staleness claim that blames the push chain
when the defect is the timestamp. Absence rendered as an alarm value --
honest_failure_modes #1, and the same shape as the 2026-09-02
``nomadnet_silence_watch`` 29,806,174-minute age.

These RUN the script against a stub server rather than only grepping it: a
source assertion alone would re-commit the original error of trusting a
representation over the thing (calibrated_claims #7). Both polarities are
pinned together -- the guard must reject the sentinel AND must not have
swallowed genuine staleness on its way past (a frozen-green guard is the
worse half of the class).
"""

import os
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_SCRIPT = os.path.join(_ROOT, "scripts", "cloud_map_freshness.sh")

pytestmark = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("curl")),
    reason="needs bash + curl to exercise the real script",
)


def _http_date(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


class _Stub:
    """Serves one fixed Last-Modified value on 127.0.0.1, HEAD included."""

    def __init__(self, last_modified):
        lm = last_modified

        class Handler(BaseHTTPRequestHandler):
            def _headers(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/geo+json")
                self.send_header("Content-Length", "123")
                if lm is not None:
                    self.send_header("Last-Modified", lm)
                self.end_headers()

            def do_HEAD(self):
                self._headers()

            def do_GET(self):
                self._headers()
                self.wfile.write(b"x" * 123)

            def log_message(self, *args):
                pass

        self._srv = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/data.geojson" % self._srv.server_address[1]

    def __enter__(self):
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        self._t.join(timeout=5)


def _run(last_modified):
    env = dict(os.environ)
    with _Stub(last_modified) as stub:
        env["CLOUD_MAP_URL"] = stub.url
        p = subprocess.run(
            ["bash", _SCRIPT], env=env, capture_output=True, text=True, timeout=60
        )
    return p.returncode, p.stdout.strip()


def test_epoch_sentinel_is_rejected_not_measured():
    rc, out = _run("Thu, 01 Jan 1970 00:00:00 GMT")
    assert rc == 2, (
        "an epoch mtime means freshness is UNKNOWN (exit 2), never a "
        "staleness verdict (exit 1) -- got rc=%d: %s" % (rc, out)
    )
    assert "sentinel" in out
    # The lie this test exists to prevent: a ~56-year age in the message.
    assert "1788" not in out and "STALE" not in out


def test_pre_1970_last_modified_is_also_a_sentinel():
    rc, out = _run("Wed, 01 Jan 1969 00:00:00 GMT")
    assert rc == 2, "a negative epoch is no more an age than zero is: %s" % out
    assert "sentinel" in out


def test_genuine_staleness_still_fails_as_stale():
    old = datetime.now(timezone.utc) - timedelta(days=2)
    rc, out = _run(_http_date(old))
    assert rc == 1, "the guard must not have swallowed real staleness: %s" % out
    assert "STALE" in out


def test_fresh_is_still_fresh():
    rc, out = _run(_http_date(datetime.now(timezone.utc)))
    assert rc == 0, out
    assert out.startswith("OK:")


def test_missing_header_is_unobservable_not_fresh():
    rc, out = _run(None)
    assert rc == 2 and "no Last-Modified" in out
