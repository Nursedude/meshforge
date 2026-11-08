"""MAP-H H15 — one Meshtastic radio, one id, one marker.

The gateway's unified tracker names Meshtastic nodes ``mesh_!xxxxxxxx``
(gateway/node_models.py); meshtasticd and every peer use ``!xxxxxxxx``. The
map keys features by id, so a tracker copy restored from a months-old
node_cache.json never collided with the radio's fresh copy: measured
2026-11-08 on the manager box, 163 tracker stubs all at "203d ago", 32 of them
beside the radio's own entry for the same node (the moc3 popup the operator
opened was the stub). Canonicalise once, at the tracker source — the radio's
copy (collected later) then replaces it, and peers export one spelling.
The topology view's ``topoCanonId`` (09-23) was the same split, patched
downstream only.
"""
import time
from unittest.mock import MagicMock, patch

from utils import map_data_collector as mdc
from utils.map_data_collector import MapDataCollector


def make(tmp_path):
    home = tmp_path / "home"
    cfg = home / ".config" / "meshforge"
    cfg.mkdir(parents=True)
    with patch("utils.map_data_collector.get_real_user_home", return_value=home):
        return MapDataCollector(cache_dir=tmp_path / "cache", enable_history=False,
                                config_dir=cfg)


def tracker_with(features):
    t = MagicMock()
    t.get_all_nodes.return_value = []
    t.get_nodes_with_position.return_value = []
    t.to_geojson.return_value = {"features": features}
    return t


def feat(nid, network="meshtastic", last_heard=None):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-155.2, 19.4]},
            "properties": {"id": nid, "network": network, "name": "x", "last_heard": last_heard,
                           "is_online": False, "last_seen": "203d ago"}}


def collect(c, features):
    with patch.object(mdc, "get_node_tracker", return_value=tracker_with(features)):
        return c._collect_unified_tracker()


def norm(c, props):
    c._normalize_meshtastic_props(props)
    return props["id"]


def test_tracker_meshtastic_id_is_canonical(tmp_path):
    assert norm(make(tmp_path), feat("mesh_!ebfa1b11")["properties"]) == "!ebfa1b11"


def test_already_canonical_id_is_unchanged(tmp_path):
    assert norm(make(tmp_path), feat("!ebfa1b11")["properties"]) == "!ebfa1b11"


def test_other_networks_keep_their_ids(tmp_path):
    c = make(tmp_path)
    assert norm(c, feat("mesh_!q", network="rns")["properties"]) == "mesh_!q"
    assert norm(c, feat("rns_ab12", network="rns")["properties"]) == "rns_ab12"


def test_only_the_mesh_bang_prefix_is_stripped(tmp_path):
    # `mesh_` without the `!` is not the tracker's Meshtastic scheme
    assert norm(make(tmp_path), feat("mesh_bridge_x")["properties"]) == "mesh_bridge_x"


def test_node_cache_source_is_normalised_too(tmp_path):
    # review 11-08 F2: Source 3 (node_cache.json) emits `mesh_!` ids as well
    c = make(tmp_path)
    cached = feat("mesh_!dddd0001")
    real_timed = MapDataCollector._timed_collect

    def only(self, name, fn, *a, **kw):
        return [cached] if name == "node_tracker" else []

    with patch.object(MapDataCollector, "_timed_collect", only), \
         patch.object(MapDataCollector, "_merge_federation", lambda self, f, pl, *a, **k: {}):
        geo = c.collect(max_age_seconds=0)
    ids = [f["properties"]["id"] for f in geo["features"]]
    assert "!dddd0001" in ids and "mesh_!dddd0001" not in ids


def test_radio_copy_replaces_the_tracker_stub_in_the_collect(tmp_path):
    # end to end through collect(): tracker stub (stale) + meshtasticd (fresh) -> ONE feature
    c = make(tmp_path)
    radio = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-155.2, 19.4]},
             "properties": {"id": "!ebfa1b11", "network": "meshtastic", "name": "meshforge moc3",
                            "last_heard": time.time() - 60, "is_online": True,
                            "last_seen": "1m ago"}}
    real_timed = MapDataCollector._timed_collect

    def only_two_sources(self, name, fn, *a, **kw):
        # every other source returns nothing — no network, radio, RNS or MQTT touched
        if name == "unified_tracker":
            return real_timed(self, name, fn, *a, **kw)
        if name == "meshtasticd":
            return [radio]
        return []

    with patch.object(mdc, "get_node_tracker",
                      return_value=tracker_with([feat("mesh_!ebfa1b11")])), \
         patch.object(MapDataCollector, "_timed_collect", only_two_sources), \
         patch.object(MapDataCollector, "_merge_federation", lambda self, f, pl, *a, **k: {}):
        geo = c.collect(max_age_seconds=0)
    ids = [f["properties"]["id"] for f in geo["features"]
           if f["properties"].get("network") == "meshtastic"]
    assert ids.count("!ebfa1b11") == 1 and "mesh_!ebfa1b11" not in ids
    moc3 = next(f["properties"] for f in geo["features"] if f["properties"]["id"] == "!ebfa1b11")
    assert moc3["is_online"] is True and moc3["last_seen"] == "1m ago"


def test_peer_directory_old_spelling_is_canonical_at_ingest():
    # review round 2 F2: a peer's node_history.db keeps `mesh_!` rows for 30 days
    from utils.map_federation import _extract_features
    payload = {"features": [{"type": "Feature",
                             "geometry": {"type": "Point", "coordinates": [-155.2, 19.4]},
                             "properties": {"id": "mesh_!ebfa1b11", "network": "meshtastic",
                                            "name": "x", "last_seen": 1}}],
               "nodes_without_position": [{"id": "mesh_!ffff0001", "network": "meshtastic"},
                                          {"id": "mesh_!q", "network": "rns"}]}
    ids = [e["id"] for e in _extract_features(payload, "peer-a")]
    assert ids == ["!ebfa1b11", "!ffff0001", "mesh_!q"]


def test_peer_stub_does_not_become_a_second_marker(tmp_path):
    from unittest.mock import MagicMock
    from utils.map_federation import FederationCollector, FederationPeerStatus, FederationSnapshot, \
        _extract_features
    c = make(tmp_path)
    payload = {"features": [{"type": "Feature",
                             "geometry": {"type": "Point", "coordinates": [-155.2, 19.4]},
                             "properties": {"id": "mesh_!ebfa1b11", "network": "meshtastic",
                                            "name": "x", "source_origin": "local_radio",
                                            "last_seen": 1}}]}
    entry = _extract_features(payload, "peer-a")[0]
    fc = MagicMock(spec=FederationCollector)
    fc.peers = ["peer-a"]
    fc.get_snapshot.return_value = FederationSnapshot(
        by_node={(entry["network"], entry["id"]): {**entry, "federated_from": "peer-a",
                                                   "seen_by_peers": ["peer-a"]}},
        peer_status={"peer-a": FederationPeerStatus(hostname="peer-a", ok=True)},
        last_sync=1, last_attempt=1)
    c._federation = fc
    features = {"!ebfa1b11": {"type": "Feature",
                              "geometry": {"type": "Point", "coordinates": [-155.2, 19.4]},
                              "properties": {"id": "!ebfa1b11", "network": "meshtastic",
                                             "source_origin": "local_radio"}}}
    c._merge_federation(features, [])
    assert list(features) == ["!ebfa1b11"]
