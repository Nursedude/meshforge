"""Tests for PublicDataFallbackMixin — public data source fallbacks."""

import json
import math
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from utils._map_collector_public import PublicDataFallbackMixin


class FakeSettings:
    """Minimal settings stub for testing."""

    def __init__(self, overrides=None):
        self._data = {
            "enable_meshmap_fallback": False,
            "enable_rmap_fallback": False,
            "enable_aredn_worldmap_fallback": False,
            "public_fallback_threshold": 3,
        }
        if overrides:
            self._data.update(overrides)

    def get(self, key, default=None):
        return self._data.get(key, default)


class StubCollector(PublicDataFallbackMixin):
    """Minimal host class providing methods the mixin expects."""

    def __init__(self, settings=None):
        self._settings = settings or FakeSettings()

    @staticmethod
    def _is_valid_coordinate(lat, lon):
        if lat is None or lon is None:
            return False
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(lat) or not math.isfinite(lon):
            return False
        if lat < -90 or lat > 90 or lon < -180 or lon > 180:
            return False
        if lat == 0.0 and lon == 0.0:
            return False
        return True

    def _make_feature(self, node_id, name, lat, lon, network="meshtastic",
                      is_online=False, hardware="", role="", battery=None,
                      last_heard=None, channel_utilization=None,
                      air_util_tx=None, **kwargs):
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "id": str(node_id),
                "name": name or str(node_id),
                "network": network,
                "is_online": is_online,
                "hardware": hardware,
                "role": role,
                "last_heard": last_heard or 0,
            },
        }

    def _is_node_online(self, last_heard, source="meshtastic"):
        if not last_heard or last_heard <= 0:
            return False
        import time
        return (time.time() - last_heard) < 900


# -- meshmap.net tests ----------------------------------------------------

MESHMAP_SAMPLE = {
    "1234567890": {
        "latitude": 21.3069,
        "longitude": -157.8583,
        "longName": "Aloha Node",
        "shortName": "ALHA",
        "hwModel": "TBEAM",
        "role": "ROUTER",
        "batteryLevel": 85,
    },
    "9876543210": {
        "latitude": 0.0,
        "longitude": 0.0,
        "longName": "Null Island",
    },
}


class TestMeshmapFetch:
    def _make_collector(self, **overrides):
        settings = FakeSettings({"enable_meshmap_fallback": True, **overrides})
        return StubCollector(settings=settings)

    @patch("utils._map_collector_public.urlopen")
    def test_meshmap_returns_valid_nodes(self, mock_urlopen):
        resp = MagicMock()
        resp.read.side_effect = [json.dumps(MESHMAP_SAMPLE).encode(), b""]
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        c = self._make_collector()
        features = c._fetch_meshmap_nodes()

        assert len(features) == 1  # Null Island filtered out
        f = features[0]
        assert f["properties"]["id"] == "!499602d2"
        assert f["properties"]["name"] == "Aloha Node"
        assert f["properties"]["network"] == "meshtastic"
        assert f["properties"]["source"] == "meshmap_net"
        assert f["geometry"]["coordinates"] == [-157.8583, 21.3069]

    @patch("utils._map_collector_public.urlopen")
    def test_meshmap_integer_coords_converted(self, mock_urlopen):
        data = {
            "1111": {
                "latitude": 377749000,
                "longitude": -1224194000,
                "longName": "IntCoord",
            }
        }
        resp = MagicMock()
        resp.read.side_effect = [json.dumps(data).encode(), b""]
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        c = self._make_collector()
        features = c._fetch_meshmap_nodes()
        assert len(features) == 1
        coords = features[0]["geometry"]["coordinates"]
        assert abs(coords[1] - 37.7749) < 0.001
        assert abs(coords[0] - (-122.4194)) < 0.001

    @patch("utils._map_collector_public.urlopen")
    def test_meshmap_network_error_returns_empty(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("Connection refused")

        c = self._make_collector()
        features = c._fetch_meshmap_nodes()
        assert features == []

    @patch("utils._map_collector_public.urlopen")
    def test_meshmap_invalid_json_returns_empty(self, mock_urlopen):
        resp = MagicMock()
        resp.read.side_effect = [b"not json", b""]
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        c = self._make_collector()
        features = c._fetch_meshmap_nodes()
        assert features == []


# -- RMAP.world tests ------------------------------------------------------

RMAP_SAMPLE = {
    "nodes": [
        {
            "hash": "abcdef1234567890",
            "display_name": "RNS-Hawaii",
            "lat": 21.4389,
            "lon": -158.0001,
            "node_type": "rnode",
            "last_seen_ts": 0,
        },
        {
            "hash": "",
            "display_name": "No Hash",
            "lat": 21.0,
            "lon": -158.0,
        },
        {
            "hash": "deadbeef12345678",
            "lat": 0.0,
            "lon": 0.0,
        },
    ]
}


class TestRmapFetch:
    def _make_collector(self, **overrides):
        settings = FakeSettings({"enable_rmap_fallback": True, **overrides})
        return StubCollector(settings=settings)

    @patch("utils._map_collector_public.urlopen")
    def test_rmap_returns_valid_nodes(self, mock_urlopen):
        resp = MagicMock()
        resp.read.side_effect = [json.dumps(RMAP_SAMPLE).encode(), b""]
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        c = self._make_collector()
        features = c._fetch_rmap_nodes()

        # Only 1 valid: second has empty hash, third is null island
        assert len(features) == 1
        f = features[0]
        assert f["properties"]["id"] == "rns_abcdef1234567890"
        assert f["properties"]["name"] == "RNS-Hawaii"
        assert f["properties"]["network"] == "rns"
        assert f["properties"]["source"] == "rmap_world"
        assert f["properties"]["hardware"] == "RNode (LoRa)"

    @patch("utils._map_collector_public.urlopen")
    def test_rmap_ssl_context_default_verifies(self, mock_urlopen):
        """Default posture: TLS verification enabled. Flipped from the
        legacy CERT_NONE behavior (MITM on rmap.world could inject node
        positions into the map UI — see security review 2026-04-16)."""
        resp = MagicMock()
        resp.read.side_effect = [json.dumps({"nodes": []}).encode(), b""]
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        c = self._make_collector()
        c._fetch_rmap_nodes()

        call_kwargs = mock_urlopen.call_args
        assert call_kwargs is not None
        ctx = call_kwargs.kwargs.get("context") or call_kwargs[1].get("context")
        assert ctx is not None
        import ssl
        assert ctx.check_hostname is True
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    @patch("utils._map_collector_public.urlopen")
    def test_rmap_ssl_context_insecure_opt_in(self, mock_urlopen):
        """Opt-in path: setting rmap_insecure_tls=True disables verification."""
        resp = MagicMock()
        resp.read.side_effect = [json.dumps({"nodes": []}).encode(), b""]
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        c = self._make_collector(rmap_insecure_tls=True)
        c._fetch_rmap_nodes()

        call_kwargs = mock_urlopen.call_args
        ctx = call_kwargs.kwargs.get("context") or call_kwargs[1].get("context")
        assert ctx is not None
        import ssl
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE

    @patch("utils._map_collector_public.urlopen")
    def test_rmap_error_returns_empty(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("timeout")

        c = self._make_collector()
        assert c._fetch_rmap_nodes() == []


# -- AREDN worldmap tests -------------------------------------------------

AREDN_CSV = """\
node,lat,lon,model,firmware_version,grid_square,channel,last_seen
KH6TEST-hAP,21.3069,-157.8583,MikroTik hAP ac lite,4.2.1,BL01,5,2026-04-01T00:00:00
KH6NULL,0.0,0.0,Ubiquiti,4.0.0,AA00,1,2026-04-01T00:00:00
,21.0,-158.0,Model,4.0.0,BL01,1,2026-04-01T00:00:00
"""


class TestArednWorldmapFetch:
    def _make_collector(self, **overrides):
        settings = FakeSettings({"enable_aredn_worldmap_fallback": True, **overrides})
        return StubCollector(settings=settings)

    @patch("utils._map_collector_public.urlopen")
    def test_aredn_csv_returns_valid_nodes(self, mock_urlopen):
        resp = MagicMock()
        resp.read.side_effect = [AREDN_CSV.encode(), b""]
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        c = self._make_collector()
        features = c._fetch_aredn_worldmap_nodes()

        # Only 1 valid: second is null island, third has empty name
        assert len(features) == 1
        f = features[0]
        assert f["properties"]["id"] == "aredn_KH6TEST-hAP"
        assert f["properties"]["name"] == "KH6TEST-hAP"
        assert f["properties"]["network"] == "aredn"
        assert f["properties"]["source"] == "aredn_worldmap"
        assert f["properties"]["hardware"] == "MikroTik hAP ac lite"

    @patch("utils._map_collector_public.urlopen")
    def test_aredn_error_returns_empty(self, mock_urlopen):
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("network unreachable")

        c = self._make_collector()
        assert c._fetch_aredn_worldmap_nodes() == []


# -- Orchestrator tests ----------------------------------------------------

class TestPublicFallbackOrchestrator:
    @patch("utils._map_collector_public.urlopen")
    def test_all_disabled_makes_no_requests(self, mock_urlopen):
        c = StubCollector(settings=FakeSettings())
        features = c._collect_public_fallbacks(current_feature_count=0)
        assert features == []
        mock_urlopen.assert_not_called()

    @patch("utils._map_collector_public.urlopen")
    def test_threshold_skips_when_enough_local_nodes(self, mock_urlopen):
        settings = FakeSettings({
            "enable_meshmap_fallback": True,
            "public_fallback_threshold": 3,
        })
        c = StubCollector(settings=settings)
        features = c._collect_public_fallbacks(current_feature_count=5)
        assert features == []
        mock_urlopen.assert_not_called()

    @patch("utils._map_collector_public.urlopen")
    def test_threshold_allows_when_sparse_local_data(self, mock_urlopen):
        resp = MagicMock()
        resp.read.side_effect = [json.dumps(MESHMAP_SAMPLE).encode(), b""]
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        settings = FakeSettings({
            "enable_meshmap_fallback": True,
            "public_fallback_threshold": 3,
        })
        c = StubCollector(settings=settings)
        features = c._collect_public_fallbacks(current_feature_count=1)
        assert len(features) == 1  # Null island filtered

    @patch("utils._map_collector_public.urlopen")
    def test_multiple_sources_merged(self, mock_urlopen):
        meshmap_resp = MagicMock()
        meshmap_resp.read.side_effect = [json.dumps({
            "1111": {"latitude": 21.3, "longitude": -157.8, "longName": "Mesh1"}
        }).encode(), b""]
        meshmap_resp.__enter__ = MagicMock(return_value=meshmap_resp)
        meshmap_resp.__exit__ = MagicMock(return_value=False)

        rmap_resp = MagicMock()
        rmap_resp.read.side_effect = [json.dumps({
            "nodes": [{"hash": "abc123", "lat": 21.4, "lon": -158.0,
                        "display_name": "RNS1", "node_type": "rnode"}]
        }).encode(), b""]
        rmap_resp.__enter__ = MagicMock(return_value=rmap_resp)
        rmap_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [meshmap_resp, rmap_resp]

        settings = FakeSettings({
            "enable_meshmap_fallback": True,
            "enable_rmap_fallback": True,
            "public_fallback_threshold": 3,
        })
        c = StubCollector(settings=settings)
        features = c._collect_public_fallbacks(current_feature_count=0)
        assert len(features) == 2
        networks = {f["properties"]["network"] for f in features}
        assert networks == {"meshtastic", "rns"}


# -- Coordinate edge cases ------------------------------------------------

class TestCoordinateEdgeCases:
    def _make_collector(self):
        return StubCollector(settings=FakeSettings({"enable_meshmap_fallback": True}))

    def test_nan_coordinates_rejected(self):
        c = self._make_collector()
        result = c._parse_meshmap_node("1111", {
            "latitude": float("nan"), "longitude": -157.8, "longName": "Bad"
        })
        assert result is None

    def test_none_coordinates_rejected(self):
        c = self._make_collector()
        result = c._parse_meshmap_node("1111", {
            "latitude": None, "longitude": None, "longName": "Bad"
        })
        assert result is None

    def test_out_of_range_rejected(self):
        c = self._make_collector()
        # Use values that stay out-of-range even after int-to-float conversion
        # (abs > 900 triggers /1e7, so use values where /1e7 is still > 90)
        result = c._parse_meshmap_node("1111", {
            "latitude": 91.0, "longitude": -157.8, "longName": "Bad"
        })
        assert result is None

    def test_invalid_numeric_id_rejected(self):
        c = self._make_collector()
        result = c._parse_meshmap_node("not_a_number", {
            "latitude": 21.3, "longitude": -157.8, "longName": "Bad"
        })
        assert result is None

    def test_rmap_null_island_rejected(self):
        c = self._make_collector()
        result = c._parse_rmap_node({
            "hash": "abc123", "lat": 0.0, "lon": 0.0, "display_name": "Zero"
        })
        assert result is None

    def test_rmap_empty_hash_rejected(self):
        c = self._make_collector()
        result = c._parse_rmap_node({
            "hash": "", "lat": 21.0, "lon": -158.0, "display_name": "NoHash"
        })
        assert result is None

    def test_aredn_empty_name_rejected(self):
        c = self._make_collector()
        result = c._parse_worldmap_row({
            "node": "", "lat": "21.0", "lon": "-158.0", "model": "Test"
        })
        assert result is None

    def test_aredn_bad_lat_rejected(self):
        c = self._make_collector()
        result = c._parse_worldmap_row({
            "node": "TestNode", "lat": "abc", "lon": "-158.0"
        })
        assert result is None
