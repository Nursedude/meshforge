"""Coverage Map "Live from meshtasticd only" / "From MQTT broker" filters.

2026-09-26 live-truth pass: both filtered on ``properties.source``, a value
no feature ever carries for those choices (5 fleet boxes measured: local radio
nodes read ``source`` null or ``unified_tracker``), so both answered
"No nodes found" beside 196 radio nodes. The provenance tag is
``source_origin``; federated features copy the PEER's origin and must not
count as this box's radio. Fixture shapes are the live ones, not invented.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from launcher_tui.handlers import _ai_tools_coverage as cov

SRC = Path(__file__).resolve().parent.parent / "src"


def _f(fid, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-155.0, 19.7]},
            "properties": {"id": fid, **props}}


LIVE_SHAPES = [
    _f("!a", network="meshtastic", source_origin="local_radio"),                 # source absent
    _f("!b", network="meshtastic", source="unified_tracker", source_origin="local_radio"),
    _f("!c", network="meshtastic", source="federation", source_origin="local_radio"),  # a PEER's radio
    _f("!d", network="meshtastic", source_origin="mqtt_local"),
    _f("mc:1", network="meshcore", source="federation", source_origin="meshcore_public"),
    _f("rns:1", network="rns", source_origin="rns_path_table"),
]


class _Collector:
    def collect(self):
        return {"type": "FeatureCollection", "features": list(LIVE_SHAPES)}


class _Host(cov.CoverageMapAndHeatmapMixin):
    pass


def _ids(source):
    with patch.object(cov, "_collect_geojson",
                      return_value=(_Collector().collect(), "test")):
        gj = _Host()._get_nodes_geojson_by_source(source)
    return sorted(f["properties"]["id"] for f in gj["features"])


@pytest.mark.parametrize("source,expected", [
    ("meshtasticd", ["!a", "!b"]),   # never the federated !c
    ("mqtt", ["!d"]),
    ("rns", ["rns:1"]),
    ("bogus", []),                   # unknown choice matches nothing, loudly empty
])
def test_filter_uses_this_boxes_origin(source, expected):
    assert _ids(source) == expected


def test_every_origin_is_one_the_collector_emits():
    """A renamed origin must fail here, not silently empty the menu."""
    text = (SRC / "utils" / "map_data_collector.py").read_text()
    for origin in cov._ORIGIN_FOR_SOURCE.values():
        assert f'"{origin}"' in text, origin


# --- _collect_geojson: the running service first, never a second radio client ---

class _Resp:
    def __init__(self, body):
        self._b = body

    def __enter__(self):
        import io
        return io.BytesIO(self._b)

    def __exit__(self, *a):
        return False


class _ExplodingCollector:
    def __init__(self):
        raise AssertionError("in-process collector built while the service answered")


def test_service_answer_is_used_and_no_collector_is_built():
    body = b'{"type":"FeatureCollection","features":[{"properties":{"id":"!a"}}]}'
    with patch("urllib.request.urlopen", return_value=_Resp(body)) as uo, \
         patch.object(cov, "_load_map_data_collector", return_value=_ExplodingCollector):
        gj, via = cov._collect_geojson()
    assert uo.call_args[0][0] == "http://127.0.0.1:5000/api/nodes/geojson"
    assert len(gj["features"]) == 1 and "map service" in via


@pytest.mark.parametrize("failure", [
    OSError("connection refused"),
    ValueError("not json"),
])
def test_service_down_falls_back_in_process_and_says_so(failure):
    with patch("urllib.request.urlopen", side_effect=failure), \
         patch.object(cov, "_load_map_data_collector", return_value=_Collector):
        gj, via = cov._collect_geojson()
    assert len(gj["features"]) == len(LIVE_SHAPES)
    assert "in-process" in via


def test_service_body_without_features_is_not_trusted():
    with patch("urllib.request.urlopen", return_value=_Resp(b'{"error":"warming"}')), \
         patch.object(cov, "_load_map_data_collector", return_value=_Collector):
        gj, via = cov._collect_geojson()
    assert "in-process" in via


def test_neither_available_is_none_with_a_reason():
    with patch("urllib.request.urlopen", side_effect=OSError("refused")), \
         patch.object(cov, "_load_map_data_collector", return_value=None):
        gj, via = cov._collect_geojson()
    assert gj is None and "not installed" in via


def test_snapshot_lists_every_source_and_the_federated_share():
    from launcher_tui.handlers.ai_tools import _snapshot_source_lines
    gj = {"properties": {"sources": {"meshtasticd": 196, "unified_tracker": 236,
                                     "mqtt": 0, "meshtasticd_via": "tcp",
                                     "meshcore_public": 48}},
          "features": [{"properties": {"source": "federation"}}] * 3
          + [{"properties": {}}]}
    text = _snapshot_source_lines(gj)
    assert "unified_tracker: 236" in text and "meshcore_public: 48" in text
    assert "federated from peers: 3" in text
    assert "mqtt" not in text and "tcp" not in text   # zero counts / transport notes
