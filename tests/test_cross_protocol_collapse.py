"""Tests for utils.cross_protocol_collapse.

Collapses cross-protocol duplicates (same operator on Meshtastic + RNS +
MeshCore) for /api/nodes/geojson responses. Row model in node_history.db
stays untouched — federation contract requires raw (network, node_id)
rows.
"""

import pytest

from utils.cross_protocol_collapse import collapse_cross_protocol


def _f(fid, name, network, lat=None, lon=None, *,
       source_origin=None, last_seen=None, protocol_meta=None):
    """Synthesize a GeoJSON feature."""
    geom: dict = {}
    if lat is not None and lon is not None:
        geom = {"type": "Point", "coordinates": [lon, lat, 0]}
    props = {"id": fid, "name": name, "network": network}
    if source_origin is not None:
        props["source_origin"] = source_origin
    if last_seen is not None:
        props["last_seen"] = last_seen
    if protocol_meta is not None:
        props["protocol_meta"] = protocol_meta
    return {"type": "Feature", "geometry": geom, "properties": props}


# ---------- Tier 1 (both positioned, within 50 m) ----------

class TestTier1:
    def test_same_name_close_position_collapses(self):
        feats = [
            _f("mt1", "WH6GXZ", "meshtastic", 19.5, -155.5,
               source_origin="local_radio"),
            _f("rns1", "WH6GXZ", "rns", 19.500001, -155.500001,
               source_origin="rns_path_table"),
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 1
        assert len(out) == 1
        props = out[0]["properties"]
        assert props["collapsed"] is True
        assert set(props["networks"]) == {"meshtastic", "rns"}
        # local_radio (priority 100) > rns_path_table (90) → mt1 wins
        assert props["id"] == "mt1"
        assert props["alt_ids"] == [["rns", "rns1"]]
        assert out[0]["geometry"]["coordinates"][:2] == [-155.5, 19.5]

    def test_same_name_far_apart_does_not_collapse(self):
        # 5 km separation — way past the 50 m tier-1 threshold.
        feats = [
            _f("mt1", "Repeater-North", "meshtastic", 19.5, -155.5,
               source_origin="local_radio"),
            _f("rns1", "Repeater-North", "rns", 19.55, -155.55,
               source_origin="rns_path_table"),
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 0
        assert len(out) == 2


# ---------- Tier 2 (one position, other position-less) ----------

class TestTier2:
    def test_position_less_rns_collapses_into_meshtastic_pin(self):
        feats = [
            _f("mt1", "WH6GXZ", "meshtastic", 19.5, -155.5,
               source_origin="local_radio"),
            _f("rns1", "WH6GXZ", "rns",
               source_origin="rns_path_table"),
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 1
        assert len(out) == 1
        props = out[0]["properties"]
        assert props["collapsed"] is True
        assert set(props["networks"]) == {"meshtastic", "rns"}
        # Geometry from the positioned feature
        assert out[0]["geometry"]["type"] == "Point"

    def test_both_position_less_same_name_still_collapses(self):
        # Two RNS announces from the same operator — both lack GPS.
        feats = [
            _f("rns1", "WH6GXZ-Mobile", "rns",
               source_origin="rns_path_table"),
            _f("rns2", "WH6GXZ-Mobile", "meshcore",
               source_origin="meshcore_public"),
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 1
        assert len(out) == 1


# ---------- Empty / no-name ----------

class TestNoCollapseOnEmptyName:
    def test_unnamed_features_never_collapse(self):
        feats = [
            _f("a", "", "meshtastic", 19.5, -155.5),
            _f("b", "", "rns", 19.5, -155.5),
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 0
        assert len(out) == 2

    def test_missing_name_property_never_collapses(self):
        feats = [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-155.5, 19.5]},
             "properties": {"id": "a", "network": "meshtastic"}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-155.5, 19.5]},
             "properties": {"id": "b", "network": "rns"}},
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 0
        assert len(out) == 2


# ---------- Name normalization ----------

class TestNameNormalization:
    def test_case_insensitive(self):
        feats = [
            _f("a", "WH6GXZ", "meshtastic", 19.5, -155.5,
               source_origin="local_radio"),
            _f("b", "wh6gxz", "rns", 19.5, -155.5,
               source_origin="rns_path_table"),
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 1

    def test_trims_whitespace(self):
        feats = [
            _f("a", "  WH6GXZ ", "meshtastic", 19.5, -155.5,
               source_origin="local_radio"),
            _f("b", "WH6GXZ", "rns", 19.5, -155.5,
               source_origin="rns_path_table"),
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 1


# ---------- N-way collapse ----------

class TestThreeWayCollapse:
    def test_meshtastic_plus_rns_plus_meshcore(self):
        feats = [
            _f("mt1", "WH6GXZ", "meshtastic", 19.5, -155.5,
               source_origin="local_radio"),
            _f("rns1", "WH6GXZ", "rns", 19.5, -155.5,
               source_origin="rns_path_table"),
            _f("mc1", "WH6GXZ", "meshcore", 19.500001, -155.500001,
               source_origin="meshcore_public"),
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 2  # two pairs merged into the primary
        assert len(out) == 1
        props = out[0]["properties"]
        assert set(props["networks"]) == {"meshtastic", "rns", "meshcore"}
        assert sorted(props["alt_ids"]) == sorted(
            [["rns", "rns1"], ["meshcore", "mc1"]]
        )


# ---------- Priority resolution ----------

class TestPrimarySelection:
    def test_local_radio_beats_meshcore_public(self):
        feats = [
            _f("mc", "Shared", "meshcore", 19.5, -155.5,
               source_origin="meshcore_public"),
            _f("mt", "Shared", "meshtastic", 19.5, -155.5,
               source_origin="local_radio"),
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 1
        # local_radio (priority 100) > meshcore_public (30) — mt is primary
        assert out[0]["properties"]["id"] == "mt"

    def test_tie_keeps_first_feature_as_primary(self):
        # Both at the same priority — first in input wins.
        feats = [
            _f("a", "Same", "meshtastic", 19.5, -155.5,
               source_origin="local_radio"),
            _f("b", "Same", "meshtastic", 19.5, -155.5,
               source_origin="local_radio"),
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 1
        assert out[0]["properties"]["id"] == "a"


# ---------- last_seen merging ----------

class TestLastSeenMax:
    def test_max_across_merged(self):
        feats = [
            _f("mt", "X", "meshtastic", 19.5, -155.5,
               source_origin="local_radio", last_seen=100),
            _f("rns", "X", "rns", 19.5, -155.5,
               source_origin="rns_path_table", last_seen=200),
        ]
        out, _ = collapse_cross_protocol(feats)
        assert out[0]["properties"]["last_seen"] == 200


# ---------- protocol_meta keyed by network ----------

class TestProtocolMetaPromotion:
    def test_protocol_meta_keyed_by_network(self):
        feats = [
            _f("mt", "X", "meshtastic", 19.5, -155.5,
               source_origin="local_radio",
               protocol_meta={"hops": 1}),
            _f("rns", "X", "rns", 19.5, -155.5,
               source_origin="rns_path_table",
               protocol_meta={"iface": "RNode"}),
        ]
        out, _ = collapse_cross_protocol(feats)
        pm = out[0]["properties"]["protocol_meta"]
        assert pm == {"meshtastic": {"hops": 1}, "rns": {"iface": "RNode"}}


# ---------- Pass-through ----------

class TestPassThrough:
    def test_empty_input_returns_empty(self):
        assert collapse_cross_protocol([]) == ([], 0)

    def test_unique_names_pass_through(self):
        feats = [
            _f("a", "Alpha", "meshtastic", 19.5, -155.5),
            _f("b", "Bravo", "meshtastic", 19.5, -155.5),
            _f("c", "Charlie", "rns", 19.5, -155.5),
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 0
        assert len(out) == 3
        # Order preserved
        assert [f["properties"]["id"] for f in out] == ["a", "b", "c"]


# ---------- Federation contract: don't strip federated flag ----------

class TestFederationFlagPreserved:
    def test_collapsing_federated_into_local_keeps_local_as_primary(self):
        # If a federated entry shares a name with a local one, the local
        # one wins on priority (federated entry carries upstream's
        # source_origin tag, but local always rooted in the local
        # ingestion path). Don't want a "collapsed" entry pretending it
        # was purely local if it absorbed federated data — but for the
        # purposes of this collapse layer, we just keep the primary's
        # geometry/id; the `collapsed` flag tells the UI to show
        # network badges.
        feats = [
            _f("mt", "X", "meshtastic", 19.5, -155.5,
               source_origin="local_radio"),
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [-155.5, 19.5, 0]},
             "properties": {
                 "id": "fed", "name": "X", "network": "meshtastic",
                 "source_origin": "local_radio",
                 "federated": True, "federated_from": "peer-a",
             }},
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 1
        # Primary: the locally-rooted one (first by tie-break — both
        # carry local_radio priority 100, first in input wins).
        assert out[0]["properties"]["id"] == "mt"
