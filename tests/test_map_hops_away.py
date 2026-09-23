"""The positioned TCP path carries meshtasticd's hop count (2026-09-23).

Before this, `_parse_tcp_node` passed only `is_local` (hopsAway == 0) and
dropped the count itself, so no screen could show a node's distance in hops
— the map cache held `hops_away` on 0 of 569 local RF nodes. Driven through
the REAL parser with a real-shaped node dict, not by inspecting source.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.map_data_collector import MapDataCollector  # noqa: E402


def _node(**extra):
    d = {"num": 0x12345678, "user": {"id": "!12345678", "longName": "Hop Test",
                                     "shortName": "HT", "hwModel": "TBEAM"},
         "position": {"latitude": 21.3, "longitude": -157.8},
         "lastHeard": int(time.time()) - 60, "snr": 4.5}
    d.update(extra)
    return d


def _parse(data):
    c = MapDataCollector.__new__(MapDataCollector)
    return c._parse_tcp_node("!12345678", data, time.time())


def test_hop_count_reaches_the_feature():
    f = _parse(_node(hopsAway=2))
    assert f["properties"]["hops_away"] == 2
    assert f["properties"]["is_local"] is False


def test_direct_neighbor_is_hops_zero_and_local():
    f = _parse(_node(hopsAway=0))
    assert f["properties"]["hops_away"] == 0 and f["properties"]["is_local"] is True


def test_unreported_hop_count_stays_absent():
    f = _parse(_node())
    assert "hops_away" not in f["properties"]  # unknown is not "far"
