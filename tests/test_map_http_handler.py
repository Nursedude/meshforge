"""Tests for MapRequestHandler — focused on T1.2 gzip behavior.

The handler subclasses SimpleHTTPRequestHandler whose __init__ wants a real
socket. We bypass that by constructing instances via __new__ and stubbing
the minimum surface area each test exercises (`headers`, `wfile`, the
status/header sinks). This keeps the gzip path testable without standing
up a real HTTP server.
"""

from io import BytesIO
from unittest.mock import MagicMock

import gzip
import json

import pytest

from utils.map_http_handler import MapRequestHandler


def _make_handler(accept_encoding: str = "") -> MapRequestHandler:
    """Build a MapRequestHandler with just enough state to call _serve_json."""
    h = MapRequestHandler.__new__(MapRequestHandler)
    h.headers = {"Accept-Encoding": accept_encoding} if accept_encoding else {}
    h.wfile = BytesIO()
    h.send_response = MagicMock()
    h.end_headers = MagicMock()
    sent_headers: list = []
    h.send_header = lambda k, v: sent_headers.append((k, v))
    h._sent_headers = sent_headers
    return h


class TestClientAcceptsGzip:
    @pytest.mark.parametrize("header,expected", [
        ("", False),
        ("identity", False),
        ("gzip", True),
        ("gzip, deflate, br", True),
        ("deflate, gzip", True),
        ("gzip;q=0.5", True),
        ("gzip;q=0", False),
        ("identity;q=1, gzip;q=0", False),
    ])
    def test_header_parsing(self, header, expected):
        h = _make_handler(header)
        assert h._client_accepts_gzip() is expected


class TestServeJsonGzip:
    """Server gzips when client accepts AND payload exceeds threshold."""

    def test_small_payload_not_gzipped_even_when_accepted(self):
        # 100 features * ~50 bytes each is well under the 10 KB threshold.
        h = _make_handler("gzip")
        h._serve_json({"type": "FeatureCollection", "features": []})
        body = h.wfile.getvalue()
        # Must be raw JSON, not gzip magic bytes (0x1f 0x8b).
        assert not body.startswith(b"\x1f\x8b")
        encoding_headers = [v for k, v in h._sent_headers if k == "Content-Encoding"]
        assert encoding_headers == [], "small payload was gzipped"

    def test_large_payload_gzipped_when_client_accepts(self):
        # Build payload comfortably above the 10 KB threshold.
        payload = {"items": ["x" * 100 for _ in range(200)]}
        h = _make_handler("gzip")
        h._serve_json(payload)
        body = h.wfile.getvalue()
        assert body.startswith(b"\x1f\x8b"), "expected gzip magic bytes"
        # Round-trip: gunzip → json must equal original payload.
        assert json.loads(gzip.decompress(body)) == payload
        encoding_headers = [v for k, v in h._sent_headers if k == "Content-Encoding"]
        assert encoding_headers == ["gzip"]
        # Content-Length must equal the gzipped length, not the raw length.
        cl_headers = [int(v) for k, v in h._sent_headers if k == "Content-Length"]
        assert cl_headers == [len(body)]

    def test_large_payload_not_gzipped_without_accept_encoding(self):
        payload = {"items": ["x" * 100 for _ in range(200)]}
        h = _make_handler("")  # no Accept-Encoding header
        h._serve_json(payload)
        body = h.wfile.getvalue()
        assert not body.startswith(b"\x1f\x8b")
        # Plain JSON round-trips.
        assert json.loads(body) == payload
        encoding_headers = [v for k, v in h._sent_headers if k == "Content-Encoding"]
        assert encoding_headers == []

    def test_large_payload_not_gzipped_when_gzip_explicitly_disabled(self):
        payload = {"items": ["x" * 100 for _ in range(200)]}
        h = _make_handler("identity, gzip;q=0")
        h._serve_json(payload)
        body = h.wfile.getvalue()
        assert not body.startswith(b"\x1f\x8b")

    def test_vary_accept_encoding_always_sent(self):
        # Vary: Accept-Encoding must be advertised on EVERY response so
        # caches/CDNs key correctly, regardless of whether THIS response
        # was gzipped.
        h = _make_handler("")
        h._serve_json({"small": "payload"})
        vary_headers = [v for k, v in h._sent_headers if k == "Vary"]
        assert "Accept-Encoding" in vary_headers


# ── F8: Server-side View preset filter ─────────────────────────────────
from utils.map_http_handler import (
    VIEW_PRESETS,
    _apply_view_preset,
    _apply_view_preset_to_position_less,
    _feature_numeric_timestamp,
)


def _feat(origin="local_radio", federated=False, last_heard=None,
          last_seen=None, network="meshtastic", node_id="!abc"):
    """Build a minimal GeoJSON Feature for preset-filter tests.

    Mirrors the shape `_make_feature` produces in the live geojson path
    plus the federated branch in `_merge_federation`. Tests pass either
    `last_heard` (numeric, live shape) or `last_seen` (numeric epoch,
    directory shape) — preset filter accepts either.
    """
    props = {
        "id": node_id,
        "network": network,
        "source_origin": origin,
        "federated": federated,
    }
    if last_heard is not None:
        props["last_heard"] = last_heard
    if last_seen is not None:
        props["last_seen"] = last_seen
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-157.8, 21.3]},
        "properties": props,
    }


class TestFeatureNumericTimestamp:
    """Picks the right field across live + directory + federated shapes."""

    def test_prefers_last_heard_over_last_seen(self):
        # Live geojson features: last_seen is a string ("5m ago"),
        # last_heard is the numeric epoch. Filter must use last_heard.
        ts = _feature_numeric_timestamp({
            "last_heard": 1_700_000_000.0,
            "last_seen": "5m ago",
        })
        assert ts == 1_700_000_000.0

    def test_falls_back_to_last_seen_when_numeric(self):
        # Directory snapshot features: only last_seen, numeric epoch.
        ts = _feature_numeric_timestamp({"last_seen": 1_700_000_000.0})
        assert ts == 1_700_000_000.0

    def test_returns_none_for_string_last_seen_with_no_last_heard(self):
        # Federated features with peer-pushed string: no usable timestamp.
        assert _feature_numeric_timestamp({"last_seen": "now"}) is None

    def test_returns_none_for_zero_or_missing(self):
        assert _feature_numeric_timestamp({"last_heard": 0}) is None
        assert _feature_numeric_timestamp({}) is None


class TestApplyViewPreset:
    """Server-side View preset filter — pure function, no I/O."""

    NOW = 2_000_000_000.0  # fixed `now` so tests are deterministic

    def test_pass_through_for_none_preset(self):
        feats = [_feat()]
        assert _apply_view_preset(feats, None) is feats

    def test_pass_through_for_unknown_preset(self):
        feats = [_feat()]
        assert _apply_view_preset(feats, "no-such-preset") is feats

    def test_pass_through_for_custom(self):
        feats = [_feat(origin="meshcore_public")]
        assert _apply_view_preset(feats, "custom", now=self.NOW) == feats

    def test_pass_through_for_fleet_union(self):
        feats = [_feat(origin="meshcore_public", federated=True)]
        assert _apply_view_preset(feats, "fleet_union", now=self.NOW) == feats

    def test_pass_through_for_all_gps(self):
        feats = [_feat(origin="meshcore_public", federated=True)]
        assert _apply_view_preset(feats, "all_gps", now=self.NOW) == feats

    def test_live_rf_keeps_local_radio_within_5min(self):
        keep = _feat(origin="local_radio", last_heard=self.NOW - 60)
        out = _apply_view_preset([keep], "live_rf", now=self.NOW)
        assert out == [keep]

    def test_live_rf_drops_other_origins(self):
        # MQTT_local + meshcore_public are both not local_radio.
        feats = [
            _feat(origin="mqtt_local", last_heard=self.NOW - 10),
            _feat(origin="meshcore_public", last_heard=self.NOW - 10),
        ]
        assert _apply_view_preset(feats, "live_rf", now=self.NOW) == []

    def test_live_rf_drops_federated(self):
        # Even local_radio source must be dropped if federated=True.
        feats = [_feat(origin="local_radio", federated=True,
                       last_heard=self.NOW - 60)]
        assert _apply_view_preset(feats, "live_rf", now=self.NOW) == []

    def test_live_rf_drops_stale(self):
        feats = [_feat(origin="local_radio", last_heard=self.NOW - 600)]
        assert _apply_view_preset(feats, "live_rf", now=self.NOW) == []

    def test_live_rf_drops_features_without_numeric_timestamp(self):
        # Conservative: if we can't verify freshness, drop. Prevents
        # federated-string-last_seen from sneaking through the age gate.
        feats = [_feat(origin="local_radio")]  # no last_heard, no last_seen
        assert _apply_view_preset(feats, "live_rf", now=self.NOW) == []

    def test_live_rf_mqtt_accepts_both_origins(self):
        feats = [
            _feat(origin="local_radio", last_heard=self.NOW - 60),
            _feat(origin="mqtt_local", last_heard=self.NOW - 60),
        ]
        assert _apply_view_preset(feats, "live_rf_mqtt", now=self.NOW) == feats

    def test_live_rf_mqtt_drops_external(self):
        feats = [_feat(origin="meshcore_public", last_heard=self.NOW - 60)]
        assert _apply_view_preset(feats, "live_rf_mqtt", now=self.NOW) == []

    def test_live_rf_mqtt_15min_window(self):
        # Within 15min: keep. Beyond: drop.
        keep = _feat(origin="local_radio", last_heard=self.NOW - 800)
        drop = _feat(origin="local_radio", last_heard=self.NOW - 1000)
        out = _apply_view_preset([keep, drop], "live_rf_mqtt", now=self.NOW)
        assert out == [keep]

    def test_external_only_keeps_meshcore_aredn_public(self):
        feats = [
            _feat(origin="meshcore_public"),
            _feat(origin="aredn_worldmap"),
            _feat(origin="public_fallback"),
            _feat(origin="mqtt_global"),
        ]
        assert _apply_view_preset(feats, "external_only", now=self.NOW) == feats

    def test_external_only_drops_local_origins(self):
        feats = [
            _feat(origin="local_radio"),
            _feat(origin="rns_path_table"),
            _feat(origin="mqtt_local"),
        ]
        assert _apply_view_preset(feats, "external_only", now=self.NOW) == []

    def test_local_only_drops_federated(self):
        feats = [
            _feat(origin="local_radio", federated=False),
            _feat(origin="meshcore_public", federated=True),
        ]
        out = _apply_view_preset(feats, "local_only", now=self.NOW)
        assert len(out) == 1
        assert out[0]["properties"]["federated"] is False

    def test_local_only_keeps_external_when_not_federated(self):
        # external_only and local_only are different axes — local_only
        # is "this box's own data, no peers", which includes public
        # aggregator data this box pulled itself.
        feats = [_feat(origin="meshcore_public", federated=False)]
        assert _apply_view_preset(feats, "local_only", now=self.NOW) == feats

    def test_uses_last_seen_as_fallback_for_directory_shape(self):
        # Directory snapshot features have last_seen (numeric epoch) but
        # no last_heard. Filter must still work.
        feats = [_feat(origin="local_radio", last_seen=self.NOW - 60)]
        # Strip last_heard if it would have been there
        del feats[0]["properties"]
        feats[0]["properties"] = {
            "source_origin": "local_radio",
            "federated": False,
            "last_seen": self.NOW - 60,
            "id": "!x",
            "network": "meshtastic",
        }
        out = _apply_view_preset(feats, "live_rf", now=self.NOW)
        assert out == feats

    def test_known_presets_cover_dropdown(self):
        # Sanity: every option in web/node_map.html's dropdown maps to
        # a server-side spec. If the dropdown grows, this fails until
        # VIEW_PRESETS is extended in lockstep.
        dropdown_options = {
            "custom", "live_rf", "live_rf_mqtt",
            "all_gps", "external_only", "fleet_union", "local_only",
        }
        assert dropdown_options.issubset(set(VIEW_PRESETS.keys()))


class TestApplyViewPresetToPositionLess:
    """Same predicates, dict shape (no Feature wrapper)."""

    NOW = 2_000_000_000.0

    def test_pass_through_for_unknown_preset(self):
        entries = [{"id": "x", "source_origin": "meshcore_public"}]
        assert _apply_view_preset_to_position_less(entries, "no-such") is entries

    def test_external_only_filters_dict_entries(self):
        entries = [
            {"id": "a", "source_origin": "meshcore_public"},
            {"id": "b", "source_origin": "local_radio"},
        ]
        out = _apply_view_preset_to_position_less(
            entries, "external_only", now=self.NOW
        )
        assert len(out) == 1
        assert out[0]["id"] == "a"

    def test_local_only_drops_federated_dict_entries(self):
        entries = [
            {"id": "a", "source_origin": "rns_path_table", "federated": False},
            {"id": "b", "source_origin": "rns_path_table", "federated": True},
        ]
        out = _apply_view_preset_to_position_less(
            entries, "local_only", now=self.NOW
        )
        assert len(out) == 1
        assert out[0]["id"] == "a"
