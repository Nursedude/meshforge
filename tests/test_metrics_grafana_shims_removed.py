"""The :9090 metrics server serves Prometheus and nothing Grafana-shaped.

Operator 2026-11-14 ("remove the endpoints"): Grafana was stopped + disabled
on moc1 on 2026-09-23 ("no i don't use grafana - and we have a UI") and the
dashboards were removed with it. The exporter still carried Grafana-only
shims — `/api/json/{metrics,nodes,status}`, the `/api/v1/*` PromQL
imitations, a POST mirror of all of them, and `Access-Control-Allow-Origin: *`
with an OPTIONS preflight — whose only callers were those dashboards
(measured across meshforge, meshanchor, meshforge-maps). Unused surface with a
wildcard CORS header is exposure, not a feature.

The END claim: a removed path answers 404 and no response carries a wildcard
CORS header; what real consumers use (`/metrics` for the Prometheus scraper,
`/health`, `/`, `/api/json/meshcore` for MeshCore posture) still serves.
"""
import io
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.prometheus_exporter import MetricsHTTPHandler  # noqa: E402

REMOVED = [
    "/api/json/metrics",
    "/api/json/nodes",
    "/api/json/status",
    "/api/v1/query?query=up",
    "/api/v1/query_range?query=up&start=0&end=1&step=1",
    "/api/v1/labels",
    "/api/v1/label/__name__/values",
]


def _stub(path):
    h = MetricsHTTPHandler.__new__(MetricsHTTPHandler)
    h.path = path
    h.wfile = io.BytesIO()
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    h.send_error = MagicMock()
    return h


def _status(h):
    if h.send_error.called:
        return h.send_error.call_args[0][0]
    return h.send_response.call_args[0][0]


def _wildcard_cors(h):
    return any(c.args[:2] == ("Access-Control-Allow-Origin", "*")
               for c in h.send_header.call_args_list)


@pytest.mark.parametrize("path", REMOVED)
def test_removed_path_is_404_on_get(path):
    h = _stub(path)
    h.do_GET()
    assert _status(h) == 404
    assert not _wildcard_cors(h)


@pytest.mark.parametrize("path", REMOVED + ["/metrics"])
def test_post_is_not_served(path):
    """The POST mirror existed only for Grafana's Prometheus data source."""
    h = _stub(path)
    do_post = getattr(h, "do_POST", None)
    if do_post is None:
        return          # BaseHTTPRequestHandler answers 501 itself
    do_post()
    assert _status(h) in (404, 405, 501)
    assert not _wildcard_cors(h)


def test_no_wildcard_cors_preflight():
    h = _stub("/metrics")
    do_options = getattr(h, "do_OPTIONS", None)
    if do_options is None:
        return
    do_options()
    assert not _wildcard_cors(h)


def test_metrics_still_served():
    """Control: the Prometheus scrape endpoint is the server's reason to exist."""
    h = _stub("/metrics")
    h.exporter = MagicMock()
    h.exporter.export.return_value = "meshforge_up 1\n"
    MetricsHTTPHandler.exporter = h.exporter
    try:
        h.do_GET()
    finally:
        MetricsHTTPHandler.exporter = None
    assert _status(h) == 200


@pytest.mark.parametrize("path", ["/health", "/"])
def test_health_and_index_still_served(path):
    h = _stub(path)
    h.do_GET()
    assert _status(h) == 200


def test_index_names_no_grafana():
    h = _stub("/")
    h.do_GET()
    assert b"grafana" not in h.wfile.getvalue().lower()
