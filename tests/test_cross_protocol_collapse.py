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


# ---------- freshness across DISPLAY-TEXT last_seen (2026-11-04) ----------
#
# Live Meshtastic collectors write last_seen as TEXT ("5m ago") and carry
# the comparable instant in last_heard (epoch). The numeric-only max above
# skipped text silently, so a stale primary ("199d ago") hid a fresh
# secondary: the manager box's map served a live node as "199d ago, offline"
# while meshtasticd's own node DB held it at 0.30 d (honest_failure_modes #1).

def _fresh(fid, name, *, last_seen, last_heard=None, is_online=None,
           source_origin="local_radio"):
    f = _f(fid, name, "meshtastic", 19.43142, -155.20891,
           source_origin=source_origin, last_seen=last_seen)
    if last_heard is not None:
        f["properties"]["last_heard"] = last_heard
    if is_online is not None:
        f["properties"]["is_online"] = is_online
    return f


class TestFreshnessAcrossTextLastSeen:
    NOW = 1_793_830_000.0

    def test_stale_text_primary_does_not_hide_fresh_secondary(self):
        # The 11-04 case: tracker copy first (stale text, no last_heard),
        # live meshtasticd copy second (fresh text + last_heard + online).
        feats = [
            _fresh("mesh_!9666541f", "meshforge base1",
                   last_seen="199d ago", is_online=False),
            _fresh("!9666541f", "meshforge base1", last_seen="5m ago",
                   last_heard=self.NOW - 300, is_online=True),
        ]
        out, n = collapse_cross_protocol(feats)
        assert n == 1
        p = out[0]["properties"]
        assert p["last_seen"] == "5m ago"
        assert p["last_heard"] == self.NOW - 300
        assert p["is_online"] is True

    def test_fresher_primary_keeps_its_own_freshness(self):
        # Control: the primary is the fresher copy — nothing may regress it.
        feats = [
            _fresh("a", "N", last_seen="2m ago",
                   last_heard=self.NOW - 120, is_online=True),
            _fresh("b", "N", last_seen="3d ago",
                   last_heard=self.NOW - 3 * 86400, is_online=False),
        ]
        out, _ = collapse_cross_protocol(feats)
        p = out[0]["properties"]
        assert p["last_seen"] == "2m ago"
        assert p["last_heard"] == self.NOW - 120
        assert p["is_online"] is True

    def test_no_comparable_instant_keeps_primary(self):
        # Control: neither copy has an instant — nothing is comparable, so
        # nothing is invented; the primary stands as before.
        feats = [
            _fresh("a", "N", last_seen="4d ago"),
            _fresh("b", "N", last_seen="1h ago"),
        ]
        out, _ = collapse_cross_protocol(feats)
        assert out[0]["properties"]["last_seen"] == "4d ago"

    def test_numeric_last_seen_vs_text_with_last_heard(self):
        # Mixed shapes: a cache/history row's epoch last_seen vs a live
        # copy's text + last_heard. The newer INSTANT wins whichever field
        # holds it. (A FEDERATED epoch is not an instant — TestFreshnessReviewR2.)
        feats = [
            _fresh("a", "N", last_seen=self.NOW - 10 * 86400),
            _fresh("b", "N", last_seen="1m ago",
                   last_heard=self.NOW - 60, is_online=True),
        ]
        out, _ = collapse_cross_protocol(feats)
        p = out[0]["properties"]
        assert p["last_seen"] == "1m ago"
        assert p["last_heard"] == self.NOW - 60
        assert p["is_online"] is True


class TestFreshnessReviewR1:
    """Non-author review r1 of c21d82fd (2026-11-04), findings F1/F2/F3/F6."""
    NOW = 1_793_830_000.0

    def test_F1_federated_copy_never_moves_is_online(self):
        # A federated copy's is_online=False is a deliberate NON-claim
        # ("federation only knows what the peer claimed"); a newer federated
        # instant must not turn a locally-online node "offline".
        local = _fresh("a", "N", last_seen="2m ago", is_online=True)
        fed = _fresh("b", "N", last_seen=self.NOW - 60, is_online=False)
        fed["properties"]["federated"] = True
        out, _ = collapse_cross_protocol([local, fed], now=self.NOW)
        assert out[0]["properties"]["is_online"] is True

    def test_F2_future_instant_is_not_fresh(self):
        # A last_heard past now + skew tolerance is forged/skewed, not fresh.
        good = _fresh("a", "N", last_seen="1m ago",
                      last_heard=self.NOW - 60, is_online=True)
        future = _fresh("b", "N", last_seen="0s ago",
                        last_heard=self.NOW + 86400, is_online=False)
        out, _ = collapse_cross_protocol([good, future], now=self.NOW)
        p = out[0]["properties"]
        assert p["last_heard"] == self.NOW - 60
        assert p["is_online"] is True

    def test_F3_winner_without_text_does_not_keep_stale_text(self):
        # Winner has last_heard + is_online but NO last_seen: the primary's
        # stale text must not survive beside the winner's online flag.
        stale = _fresh("a", "N", last_seen="199d ago", is_online=False)
        win = _fresh("b", "N", last_seen="x", last_heard=self.NOW - 30,
                     is_online=True)
        del win["properties"]["last_seen"]
        out, _ = collapse_cross_protocol([stale, win], now=self.NOW)
        p = out[0]["properties"]
        assert p["last_seen"] != "199d ago"
        assert p["is_online"] is True

    def test_F6_zero_or_negative_last_seen_is_not_an_instant(self):
        online = _fresh("a", "N", last_seen="2m ago", is_online=True)
        zero = _fresh("b", "N", last_seen=0, is_online=False)
        out, _ = collapse_cross_protocol([online, zero], now=self.NOW)
        p = out[0]["properties"]
        assert p["last_seen"] == "2m ago"
        assert p["is_online"] is True


class TestFreshnessReviewR2:
    """Non-author review r2 of c21d82fd^..5783fbac (2026-11-04), F-R2-1/2.

    A federated copy's last_seen is the PEER'S collection time
    (node_history._build_directory_row stamps local-origin rows with `now`),
    not when the node was heard — it is not a freshness instant at all.
    """
    NOW = 1_793_830_000.0
    D = 86400.0

    @staticmethod
    def _fed(f):
        f["properties"]["federated"] = True
        f["properties"]["federated_from"] = "peer"
        return f

    def test_R2_1_peer_poll_stamp_does_not_freshen_a_silent_node(self):
        # Live 14:46: 7 nodes reading "200d ago" locally were served with
        # one shared poll stamp from a peer — a 200d-silent node as heard.
        tracker = _fresh("mesh_!e213a228", "N", last_seen="200d ago",
                         is_online=False)
        fed = self._fed(_fresh("!e213a228", "N", last_seen=self.NOW - 60,
                               is_online=False))
        out, n = collapse_cross_protocol([tracker, fed], now=self.NOW)
        assert n == 1
        p = out[0]["properties"]
        assert p["last_seen"] == "200d ago"
        assert p["is_online"] is False
        assert p.get("last_heard") is None

    def test_R2_1_federated_primary_does_not_hide_a_fresh_local_copy(self):
        # F-R2-6: rank must not decide is_online. A federated primary has no
        # instant, so the local copy's heard time and flag supply freshness
        # — the same answer whichever side outranks the other.
        def pair(fed_origin, local_origin):
            local = _fresh("a", "N", last_seen="2m ago",
                           last_heard=self.NOW - 120, is_online=True,
                           source_origin=local_origin)
            fed = self._fed(_fresh("b", "N", last_seen=self.NOW - 60,
                                   is_online=False, source_origin=fed_origin))
            out, _ = collapse_cross_protocol([local, fed], now=self.NOW)
            return out[0]["properties"]

        fed_primary = pair("local_radio", "node_tracker")
        local_primary = pair("node_tracker", "local_radio")
        for p in (fed_primary, local_primary):
            assert p["last_seen"] == "2m ago"
            assert p["last_heard"] == self.NOW - 120
            assert p["is_online"] is True

    def test_R2_1_federated_copy_leaves_a_stale_local_reading_whole(self):
        local = _fresh("a", "N", last_seen="199d ago",
                       last_heard=self.NOW - 199 * self.D, is_online=False)
        fed = self._fed(_fresh("b", "N", last_seen=self.NOW - 300,
                               is_online=False))
        out, _ = collapse_cross_protocol([local, fed], now=self.NOW)
        p = out[0]["properties"]
        assert p["last_seen"] == "199d ago"
        assert p["last_heard"] == self.NOW - 199 * self.D

    def test_R2_2_winner_without_last_heard_replaces_the_stale_one(self):
        # A non-federated winner carrying only an epoch last_seen: its flag
        # moved, so the primary's 199d last_heard must not stay beside it
        # (the popup reads last_heard first).
        local = _fresh("a", "N", last_seen="199d ago",
                       last_heard=self.NOW - 199 * self.D, is_online=False)
        other = _fresh("b", "N", last_seen=self.NOW - 120, is_online=True,
                       source_origin="node_tracker")
        out, _ = collapse_cross_protocol([local, other], now=self.NOW)
        p = out[0]["properties"]
        assert p["is_online"] is True
        assert p["last_heard"] == self.NOW - 120

    def test_R2_2_winner_carries_its_plausible_instant_not_a_forged_one(self):
        # F-R2-9: the winner is chosen on a plausible instant (its last_seen);
        # a future last_heard beside it must not be copied into the result.
        stale = _fresh("a", "N", last_seen="3d ago",
                       last_heard=self.NOW - 3 * self.D, is_online=False)
        win = _fresh("b", "N", last_seen=self.NOW - 60,
                     last_heard=self.NOW + 2 * self.D, is_online=True)
        out, _ = collapse_cross_protocol([stale, win], now=self.NOW)
        p = out[0]["properties"]
        assert p["last_heard"] == self.NOW - 60
        assert p["is_online"] is True


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
