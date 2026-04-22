"""Tests for AREDN sysinfo parser — especially the top-level lat/lon location
fields introduced in AREDN API v2.0 firmware."""

from unittest.mock import patch, MagicMock
import json

from utils.aredn import AREDNClient


def _client_with_response(payload):
    """AREDNClient whose HTTP GET returns the given dict."""
    client = AREDNClient("localnode.local.mesh")

    # Patch urllib's urlopen so _fetch returns our payload
    def fake_urlopen(req, timeout=None):
        response = MagicMock()
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        response.read.return_value = json.dumps(payload).encode()
        return response

    patcher = patch("utils.aredn.urllib.request.urlopen", side_effect=fake_urlopen)
    patcher.start()
    return client, patcher


class TestTopLevelLocation:
    """AREDN API v2.0 returns lat/lon/gridsquare at the TOP LEVEL of sysinfo,
    not nested under node_details or location. Without this handling, every
    v2.0 AREDN router looks like it has no GPS and gets dropped from the map."""

    def test_top_level_lat_lon_parsed(self):
        payload = {
            "api_version": "2.0",
            "node": "WH6GXZ-6-BI-ECOM",
            "lat": 19.43517,
            "lon": -155.21384,
            "gridsquare": None,
            "node_details": {
                "model": "MikroTik hAP ac lite",
                "description": "Ecomm Big Island",
            },
        }
        client, patcher = _client_with_response(payload)
        try:
            node = client.get_node_info()
        finally:
            patcher.stop()

        assert node is not None
        assert node.latitude == 19.43517
        assert node.longitude == -155.21384
        assert node.has_location() is True

    def test_null_gridsquare_doesnt_break(self):
        payload = {
            "api_version": "2.0", "node": "X",
            "lat": 10.0, "lon": 10.0,
            "gridsquare": None,
        }
        client, patcher = _client_with_response(payload)
        try:
            node = client.get_node_info()
        finally:
            patcher.stop()
        assert node.latitude == 10.0
        assert node.grid_square == ""

    def test_node_details_lat_lon_still_works(self):
        """Legacy firmware that nests lat/lon under node_details must still work."""
        payload = {
            "api_version": "1.14",
            "node": "OLD-AREDN",
            "node_details": {
                "model": "x",
                "lat": 22.5,
                "lon": -158.0,
                "grid_square": "BL11",
            },
        }
        client, patcher = _client_with_response(payload)
        try:
            node = client.get_node_info()
        finally:
            patcher.stop()
        assert node.latitude == 22.5
        assert node.longitude == -158.0
        assert node.grid_square == "BL11"

    def test_nested_location_still_works(self):
        """Some firmware variants nest under a 'location' dict."""
        payload = {
            "api_version": "1.x",
            "node": "NESTED",
            "location": {"lat": 45.0, "lon": -100.0, "gridsquare": "DN30"},
        }
        client, patcher = _client_with_response(payload)
        try:
            node = client.get_node_info()
        finally:
            patcher.stop()
        assert node.latitude == 45.0
        assert node.longitude == -100.0
        assert node.grid_square == "DN30"

    def test_no_location_means_has_location_false(self):
        payload = {"api_version": "2.0", "node": "NOWHERE"}
        client, patcher = _client_with_response(payload)
        try:
            node = client.get_node_info()
        finally:
            patcher.stop()
        assert node is not None
        assert node.has_location() is False

    def test_qualified_hostname_not_double_appended(self):
        """Regression: AREDNClient("localnode.local.mesh") used to produce
        http://localnode.local.mesh.local.mesh:8080 which doesn't resolve."""
        c = AREDNClient("localnode.local.mesh")
        assert c.base_url == "http://localnode.local.mesh:8080"

        c2 = AREDNClient("wh6gxz-6-bi-ecom.local.mesh")
        assert c2.base_url == "http://wh6gxz-6-bi-ecom.local.mesh:8080"

    def test_bare_short_name_gets_local_mesh_appended(self):
        c = AREDNClient("localnode")
        assert c.base_url == "http://localnode.local.mesh:8080"

    def test_ip_address_not_appended(self):
        c = AREDNClient("10.162.93.242")
        assert c.base_url == "http://10.162.93.242:8080"
        assert c.ip == "10.162.93.242"

    def test_nested_wins_over_top_level_when_both_present(self):
        """If some future firmware re-introduces nested location AND top-level,
        nested fields win (they're applied first; our new code only fills gaps)."""
        payload = {
            "api_version": "2.1",
            "node": "BOTH",
            "lat": 0.0,   # would be skipped as (0, 0) null-island anyway, but wins here
            "lon": 0.0,
            "node_details": {"lat": 10.0, "lon": 20.0},
        }
        client, patcher = _client_with_response(payload)
        try:
            node = client.get_node_info()
        finally:
            patcher.stop()
        # node_details wins because it's parsed first; top-level only fills None gaps.
        assert node.latitude == 10.0
        assert node.longitude == 20.0

    def test_reported_node_name_replaces_caller_hostname(self):
        """AREDN sysinfo `node` field (e.g. 'WH6GXZ-6-BI-ECOM') should become the
        canonical hostname on the parsed AREDNNode — not the string the caller
        used to connect ('localnode.local.mesh')."""
        payload = {
            "api_version": "2.0",
            "node": "WH6GXZ-6-BI-ECOM",
            "lat": 19.43517, "lon": -155.21384,
            "node_details": {"description": "Ecomm Big Island"},
        }
        client, patcher = _client_with_response(payload)
        try:
            node = client.get_node_info()
        finally:
            patcher.stop()
        assert node.hostname == "WH6GXZ-6-BI-ECOM"
        assert node.description == "Ecomm Big Island"

    def test_missing_node_name_falls_back_to_caller_hostname(self):
        payload = {"api_version": "2.0", "lat": 10.0, "lon": 20.0}
        client, patcher = _client_with_response(payload)
        try:
            node = client.get_node_info()
        finally:
            patcher.stop()
        assert node.hostname == "localnode.local.mesh"
