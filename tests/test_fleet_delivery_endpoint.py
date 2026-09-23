"""/fleet/delivery — the /fleet Delivery pane's data source (2026-09-23).

Serves utils.delivery_view for THIS box. Pinned: it is routed; it carries the
view's own headline and per-leg status through unflattened; and a reader that
blows up is served as a 500 with UNKNOWN, never as an empty healthy view.
"""
from io import BytesIO
from unittest.mock import MagicMock, patch

from utils import delivery_view as dv
from utils.map_http_handler import MapRequestHandler


def _handler():
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.path = "/fleet/delivery"
    h.headers = {}
    h.wfile = BytesIO()
    h.send_response = MagicMock()
    h.end_headers = MagicMock()
    h.send_header = MagicMock()
    h._served = {}

    def _capture(payload, status=200):
        h._served = {"payload": payload, "status": status}

    h._serve_json = _capture
    return h


def test_routed_and_labelled():
    h = _handler()
    h.is_warming = False
    h._serve_fleet_delivery = MagicMock()
    h._dispatch_get("/fleet/delivery")
    h._serve_fleet_delivery.assert_called_once()
    assert MapRequestHandler._endpoint_label("/fleet/delivery") == "/fleet/delivery"


def test_carries_the_views_own_verdict(tmp_path):
    view = dv.gather(home=str(tmp_path), now=1_800_000_000.0,
                     gateway_resolver=lambda u: ("ok", 42),
                     enrolled_fn=lambda u, home: False,
                     unit_enabled_fn=lambda u: True)
    h = _handler()
    with patch("utils.delivery_view.gather", return_value=view):
        h._serve_fleet_delivery()
    body = h._served["payload"]
    assert h._served["status"] == 200
    # gateway running, no record -> UNKNOWN; must survive the endpoint.
    assert body["headline"].startswith("UNKNOWN")
    assert body["legs"][0]["status"] == dv.UNKNOWN
    assert "This screen never changes anything." in body["text"]


def test_reader_failure_is_a_500_unknown():
    h = _handler()
    with patch("utils.delivery_view.gather", side_effect=OSError("boom")):
        h._serve_fleet_delivery()
    assert h._served["status"] == 500
    assert h._served["payload"]["headline"].startswith("UNKNOWN")
