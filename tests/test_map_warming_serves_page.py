"""MAP-H H3 #3: while the map server warms, the PAGES load; only data answers 503.

Live, 2026-11-06 14:22 HST (the manager box, real Chrome): a browser that opened
``/`` during the ~27 s cold start got the raw 503 JSON body -- no page, so the
H1 "Map blind since ... warming up (cold start)" banner could never show. The
banner only reached a page that was ALREADY open.

Contract pinned here through a real HTTP server and the real dispatch:
  * while warming, ``/`` ``/index.html`` ``/fleet`` ``/inbox`` and the page's
    static assets answer 200 (each page already renders a failed fetch as
    blind/failed and retries);
  * every ``/api/*`` and every JSON route still answers 503 ``warming``;
  * serving a page while warming never touches the collector.
"""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from utils.map_http_handler import MapRequestHandler

WEB = Path(__file__).resolve().parents[1] / "web"


class _CollectorMustNotBeTouched:
    def __getattr__(self, name):  # any use of the collector while warming is the bug
        raise AssertionError(f"collector.{name} touched while warming")


@pytest.fixture
def warming_server(monkeypatch):
    monkeypatch.setattr(MapRequestHandler, "is_warming", True)
    monkeypatch.setattr(MapRequestHandler, "warming_started_at", 1.0)
    monkeypatch.setattr(MapRequestHandler, "web_dir", str(WEB))
    monkeypatch.setattr(MapRequestHandler, "collector", _CollectorMustNotBeTouched())
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MapRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _get(srv, path):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    try:
        c.request("GET", path, headers={"Host": "127.0.0.1"})
        r = c.getresponse()
        return r.status, r.getheader("Content-Type") or "", r.read()
    finally:
        c.close()


@pytest.mark.parametrize("path, marker", [
    ("/", b"map-blind-banner"),
    ("/index.html", b"map-blind-banner"),
    ("/fleet", b"<html"),
    ("/inbox", b"<html"),
])
def test_pages_load_while_warming(warming_server, path, marker):
    status, ctype, body = _get(warming_server, path)
    assert status == 200, (path, status, body[:200])
    assert ctype.startswith("text/html"), (path, ctype)
    assert marker in body.lower() if marker == b"<html" else marker in body


def test_the_page_static_script_loads_while_warming(warming_server):
    status, _ctype, body = _get(warming_server, "/js/lxmface.js")
    assert status == 200, (status, body[:200])
    assert body == (WEB / "js" / "lxmface.js").read_bytes()


@pytest.mark.parametrize("path", [
    "/api/nodes/geojson", "/api/status", "/api/settings",
    "/api/region-presets", "/api/inbox", "/api/fleet/truth",
    "/api/does-not-exist", "/api/does-not-exist.js",
    "/fleet/slo", "/fleet/dups",
])
def test_data_still_answers_warming_503(warming_server, path):
    status, ctype, body = _get(warming_server, path)
    assert status == 503, (path, status, body[:200])
    assert "json" in ctype, (path, ctype)
    doc = json.loads(body)
    assert doc["state"] == "warming" and doc["error"] == "service_warming"


@pytest.mark.parametrize("path", ["/%2e%2e/README.md", "/../README.md",
                                  "/js/../../README.md", "/js", "/nope.js"])
def test_warming_passthrough_never_escapes_web_dir_or_invents_files(warming_server, path):
    status, _ctype, body = _get(warming_server, path)
    assert status == 503, (path, status, body[:200])


def test_the_page_says_warming_when_its_data_fetch_gets_the_503():
    # The page that now loads must turn the geojson 503 body into the H1 banner
    # text (pinned statically; the live browser drill is the end-to-end check).
    html = (WEB / "node_map.html").read_text()
    assert "body.state === 'warming'" in html
    assert "map server warming up (cold start)" in html


def test_without_a_web_dir_static_stays_503_but_the_page_still_loads(warming_server, monkeypatch):
    # r1#8: static serves from web_dir; with none there is no root for the
    # passthrough to check, so it must not vouch for a file the static branch
    # would then look for elsewhere. The page route has its own fallback.
    monkeypatch.setattr(MapRequestHandler, "web_dir", None)
    assert _get(warming_server, "/js/lxmface.js")[0] == 503
    status, ctype, body = _get(warming_server, "/")
    assert status == 200 and b"map-blind-banner" in body
