"""TRACKER-FORGET slice 1 (2026-11-21): the map's tracker reader has an age gate.

MEASURED on the lab manager box 06:5x: the map process builds its own UnifiedNodeTracker
singleton, which loads ``node_cache.json`` at construction and never runs its
live loop (never evicts, never writes — write authority, 09-22). The file was
last saved 2026-08-03 (by a gateway that box no longer runs); the live map
logged ``Loaded 819 nodes from cache`` at 00:32 and served all of it: 236
positioned records, every one >180 d old, tagged ``source_origin=local_radio``
— "our radio hears this". Two other map-only boxes carry 209-day-old twins.

The file reader for the SAME artifact (``_collect_node_tracker``) already
refuses a file older than ``get_node_cache_max_age_seconds()``. Two readers
of one artifact share ONE rule (hfm #5): the tracker reader drops a node whose
last_seen is older than that window, counts the drops (hfm #9), and holds a
node whose age is unknown (hfm #2 — unknown age is not staleness).
"""
import json
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from tests._federation_net_guard import _no_real_peer_http  # noqa: F401 — autouse
from utils.map_data_collector import MapDataCollector


def _node(nid, network, age_s, pos=True):
    d = {"id": nid, "network": network, "name": nid.upper()}
    if age_s is not None:
        d["last_seen"] = (datetime.now() - timedelta(seconds=age_s)).isoformat()
    if pos:
        d["position"] = {"latitude": 19.4, "longitude": -155.2}
    return d


@pytest.fixture
def setup(tmp_path):
    home = tmp_path / "home"
    cfg = home / ".config" / "meshforge"
    cfg.mkdir(parents=True)
    with patch("utils.map_data_collector.get_real_user_home", return_value=home):
        c = MapDataCollector(cache_dir=tmp_path / "cache", enable_history=False, config_dir=cfg)
    return c, cfg


def _collect_with_tracker(collector, cfg, nodes):
    (cfg / "node_cache.json").write_text(json.dumps({"version": 1, "nodes": nodes}))
    from gateway.node_tracker import UnifiedNodeTracker
    # conftest's _isolate_node_cache_files patches get_cache_file suite-wide;
    # an inner patch wins (its docstring) — point it at THIS test's file.
    with patch.object(UnifiedNodeTracker, "get_cache_file", return_value=cfg / "node_cache.json"):
        tracker = UnifiedNodeTracker()          # constructed, NEVER started — the map's case
    assert len(tracker.get_all_nodes()) == len(nodes), "fixture did not load"
    collector._nodes_without_position = []
    with patch("utils.map_data_collector.get_node_tracker", return_value=tracker):
        feats = collector._collect_unified_tracker()
    return feats


class TestTrackerSnapshotAgeGate:
    def test_stale_positioned_records_are_not_served(self, setup):
        collector, cfg = setup
        window = collector.get_node_cache_max_age_seconds()
        feats = _collect_with_tracker(collector, cfg, [
            _node("!stale001", "meshtastic", window + 200 * 86400),
            _node("!fresh001", "meshtastic", 3600),
        ])
        ids = {f["properties"]["id"] for f in feats}
        assert ids == {"!fresh001"}, ids

    def test_stale_position_less_records_are_not_listed(self, setup):
        collector, cfg = setup
        window = collector.get_node_cache_max_age_seconds()
        _collect_with_tracker(collector, cfg, [
            _node("rns_stale", "rns", window + 86400, pos=False),
            _node("rns_fresh", "rns", 60, pos=False),
        ])
        ids = {e["id"] for e in collector._nodes_without_position}
        assert ids == {"rns_fresh"}, ids

    def test_unknown_age_is_held(self, setup):
        collector, cfg = setup
        feats = _collect_with_tracker(collector, cfg, [_node("!noage001", "meshtastic", None)])
        assert {f["properties"]["id"] for f in feats} == {"!noage001"}

    def test_drops_leave_a_witness(self, setup):
        collector, cfg = setup
        window = collector.get_node_cache_max_age_seconds()
        _collect_with_tracker(collector, cfg, [
            _node("!stale001", "meshtastic", window + 86400),
            _node("rns_stale", "rns", window + 86400, pos=False),
            _node("!fresh001", "meshtastic", 60),
        ])
        diag = collector._source_diagnostics["unified_tracker"]
        assert collector._tracker_stale_dropped == 2
        assert "2 older than" in (diag["notes"] or ""), diag
        assert diag["yielded"] == 1

    def test_all_stale_is_said_not_silent(self, setup):
        """The lab box's case: every record is past the window. The source must
        say so — never read as 'tracker empty'."""
        collector, cfg = setup
        window = collector.get_node_cache_max_age_seconds()
        feats = _collect_with_tracker(collector, cfg, [
            _node("!stale001", "meshtastic", window + 86400),
            # position-less RNS: this reader lists it (a position-less
            # MESHTASTIC record is the HTTP collector's, never served here)
            _node("rns_stale2", "rns", window + 2 * 86400, pos=False),
        ])
        assert feats == []
        diag = collector._source_diagnostics["unified_tracker"]
        assert "2 older than" in (diag["notes"] or ""), diag
        assert "empty" not in (diag["notes"] or ""), diag

    def test_window_is_the_file_readers_window(self, setup):
        """ONE rule: a node just inside the configured window is served, just
        outside is not — the same get_node_cache_max_age_seconds() the file
        reader gates on."""
        collector, cfg = setup
        collector._settings.set("node_cache_max_age_hours", 2)
        feats = _collect_with_tracker(collector, cfg, [
            _node("!inside01", "meshtastic", 2 * 3600 - 120),
            _node("!outside1", "meshtastic", 2 * 3600 + 120),
        ])
        assert {f["properties"]["id"] for f in feats} == {"!inside01"}
