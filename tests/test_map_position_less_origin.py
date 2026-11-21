"""MAP-H H4a — this radio's own position-less entries must survive federation.

MEASURED 2026-11-20 15:28 HST (the lab map, read-only): the radio reports
400 nodes, 226 positioned; 174 position-less ones should come from THIS
radio, but /api/nodes/geojson carried only 25. 157 federated stubs stood in
their place — no ``is_online``, ``last_seen`` = the peer's collector WRITE
time (H14's wrong quantity). Radio ``num_online_nodes=37`` vs map ~26.

Cause (read): the radio's position-less records carry no ``source_origin``
→ ``_origin_priority(None) == 0`` → any peer ``local_radio`` copy (100)
wins the merge and the local record — the one holding THIS radio's
``lastHeard`` — is dropped.

The existing collision tests in test_map_collector_federation.py hand-build
their local stubs WITH ``source_origin``, so they could never see this.
These tests drive the REAL producers into the real merge.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from tests._federation_net_guard import _no_real_peer_http  # noqa: F401 — autouse: no real peer HTTP
from utils.map_data_collector import MapDataCollector
from utils.map_federation import (
    FederationCollector,
    FederationPeerStatus,
    FederationSnapshot,
)

NODE_NUM = 0xA2EA2F10
NODE_ID = "!a2ea2f10"


@pytest.fixture
def collector(tmp_path):
    fake_home = tmp_path / "home"
    cfg_dir = fake_home / ".config" / "meshforge"
    cfg_dir.mkdir(parents=True)
    with patch("utils.map_data_collector.get_real_user_home", return_value=fake_home):
        c = MapDataCollector(
            cache_dir=tmp_path / "cache",
            enable_history=False,
            config_dir=cfg_dir,
        )
    return c


def _federate_stub_copy(collector, peer_write_ts):
    """A peer federates the SAME node, position-less, as local_radio — the
    live shape (federated_from moc5, last_seen = peer's write time)."""
    fc = MagicMock(spec=FederationCollector)
    fc.peers = ["moc5.mf.internal"]
    fc.get_snapshot.return_value = FederationSnapshot(
        by_node={("meshtastic", NODE_ID): {
            "id": NODE_ID, "network": "meshtastic", "name": "FROM PEER",
            "lat": None, "lon": None,
            "last_seen": peer_write_ts, "source_origin": "local_radio",
            "federated_from": "moc5.mf.internal",
            "seen_by_peers": ["moc5.mf.internal"],
        }},
        peer_status={"moc5.mf.internal": FederationPeerStatus(
            hostname="moc5.mf.internal", ok=True,
            last_sync=peer_write_ts, last_attempt=peer_write_ts, last_count=1)},
        last_sync=peer_write_ts,
        last_attempt=peer_write_ts,
    )
    collector._federation = fc


def _fake_interface(heard_ts):
    iface = MagicMock()
    iface.myInfo = None
    iface.localNode = None
    iface.nodes = {
        NODE_ID: {
            "num": NODE_NUM,
            "user": {"id": NODE_ID, "longName": "LOCAL HEARD", "shortName": "LH"},
            "lastHeard": int(heard_ts),
            # no "position" — the position-less case
        },
    }
    return iface


def _assert_local_record_survived(position_less, heard_ts):
    mine = [e for e in position_less if e.get("id") == NODE_ID]
    assert len(mine) == 1, f"expected one entry for {NODE_ID}, got {mine}"
    entry = mine[0]
    assert not entry.get("federated"), (
        "this radio's own position-less record was REPLACED by a peer's "
        f"federated stub (H4a): {entry}"
    )
    assert entry.get("is_online") is True, entry
    assert entry.get("last_heard") == int(heard_ts), entry


class TestRadioPositionLessSurvivesFederation:
    def test_tcp_drain_record_beats_peer_stub(self, collector):
        now = time.time()
        heard = now - 60  # heard by THIS radio a minute ago
        features, no_position, total = collector._drain_interface_nodes(
            _fake_interface(heard), "meshtasticd")
        assert features == [] and total == 1 and len(no_position) == 1

        position_less = list(no_position)  # what collect() publishes
        _federate_stub_copy(collector, peer_write_ts=now - 5)
        collector._merge_federation({}, position_less)

        _assert_local_record_survived(position_less, heard)

    def test_tcp_drain_tags_position_less_local_radio(self, collector):
        """The seam: every position-less record from the radio drain says
        where it came from, as the positioned ones do (collect() tags those
        at the meshtasticd source)."""
        _, no_position, _ = collector._drain_interface_nodes(
            _fake_interface(time.time() - 60), "meshtasticd")
        assert [e.get("source_origin") for e in no_position] == ["local_radio"]


def _federate_positioned_copy(collector, peer_write_ts, lat=19.43, lon=-155.21):
    """A peer HAS a GPS fix for a node THIS radio hears without one."""
    _federate_stub_copy(collector, peer_write_ts)
    snap = collector._federation.get_snapshot.return_value
    snap.by_node[("meshtastic", NODE_ID)].update({"lat": lat, "lon": lon})


class TestPeerPositionAdoptedLocalHearingKept:
    """Option (b), operator 2026-11-20 ("your call"): a peer's position is
    real data and THIS radio's hearing time is real data — keep both.
    Before H4a the peer copy REPLACED the local record (position kept,
    hearing lost); a tag-only fix would keep hearing and drop the position."""

    def test_peer_position_drawn_with_this_radios_hearing(self, collector):
        now = time.time()
        heard = now - 60
        _, no_position, _ = collector._drain_interface_nodes(
            _fake_interface(heard), "meshtasticd")
        position_less = list(no_position)
        features: dict = {}
        _federate_positioned_copy(collector, peer_write_ts=now - 5)
        collector._merge_federation(features, position_less)

        assert [e for e in position_less if e.get("id") == NODE_ID] == [], (
            "node still listed as position-less although a peer has its fix")
        drawn = [f for f in features.values()
                 if f["properties"].get("id") == NODE_ID]
        assert len(drawn) == 1, features
        props = drawn[0]["properties"]
        assert drawn[0]["geometry"]["coordinates"][:2] == [-155.21, 19.43]
        # THIS radio's judgement, not the peer's bookkeeping
        assert props.get("federated") is not True, props
        assert props["is_online"] is True
        assert props["last_heard"] == int(heard)
        assert props["source_origin"] == "local_radio"
        # and the page can say whose fix it is
        assert props["position_from"] == "moc5.mf.internal"

    def test_peer_stub_without_position_never_replaces(self, collector):
        """The pure stub case stays local (no position to gain)."""
        now = time.time()
        _, no_position, _ = collector._drain_interface_nodes(
            _fake_interface(now - 60), "meshtasticd")
        position_less = list(no_position)
        features: dict = {}
        _federate_stub_copy(collector, peer_write_ts=now - 5)
        collector._merge_federation(features, position_less)
        assert features == {}
        _assert_local_record_survived(position_less, now - 60)


class TestEveryPositionLessWriterTagsOrigin:
    """The other two producers of position-less records (HTTP nodedb path,
    unified tracker for MeshCore/RNS) carried no origin either."""

    def test_http_path_tags_local_radio(self, collector):
        from utils.meshtastic_http import MeshtasticNode
        client = MagicMock()
        client.is_available = True
        client.get_nodes.return_value = [MeshtasticNode(
            node_id=NODE_ID, long_name="HTTP NOPOS",
            last_heard=int(time.time() - 60))]
        collector._nodes_without_position = []
        with patch("utils._map_collector_meshtastic.get_http_client",
                   return_value=client):
            collector._collect_via_http("127.0.0.1")
        origins = [e.get("source_origin") for e in collector._nodes_without_position
                   if e.get("id") == NODE_ID]
        assert origins == ["local_radio"]

    @pytest.mark.parametrize("network,origin", [
        ("meshcore", "local_radio"),
        ("rns", "rns_path_table"),
    ])
    def test_unified_tracker_tags_origin(self, collector, network, origin):
        node = MagicMock()
        node.id = f"{network}:deadbeef"
        node.name = "TRACKED"
        node.network = network
        node.is_online = True
        node.get_age_string.return_value = "1m ago"
        tracker = MagicMock()
        tracker.get_all_nodes.return_value = [node]
        tracker.to_geojson.return_value = {"features": []}
        tracker.get_nodes_with_position.return_value = []
        collector._nodes_without_position = []
        with patch("utils.map_data_collector.get_node_tracker",
                   return_value=tracker):
            collector._collect_unified_tracker()
        origins = [e.get("source_origin") for e in collector._nodes_without_position]
        assert origins == [origin]


class TestAdoptedPositionNotPersistedAsOurs:
    """(b)'s consequence: the adopted feature is LOCAL (not federated), so the
    history writer would store the PEER'S fix as this radio's position — and
    federation would then export it back out as local_radio (an echo loop).
    Our hearing is persisted; the borrowed position is not."""

    def test_history_gets_hearing_not_borrowed_position(self, tmp_path):
        fake_home = tmp_path / "home"
        cfg_dir = fake_home / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        with patch("utils.map_data_collector.get_real_user_home",
                   return_value=fake_home):
            c = MapDataCollector(cache_dir=tmp_path / "cache",
                                 enable_history=True, config_dir=cfg_dir)
        for name in ("_collect_unified_tracker", "_collect_direct_radio",
                     "_collect_mqtt", "_collect_node_tracker", "_collect_aredn",
                     "_collect_aredn_worldmap", "_collect_rns_direct",
                     "_collect_meshcore_public"):
            setattr(c, name, lambda: [])
        c._collect_public_fallbacks = lambda current_feature_count=0: []
        now = time.time()
        heard = now - 60

        def radio():
            _, no_pos, _ = c._drain_interface_nodes(
                _fake_interface(heard), "meshtasticd")
            c._nodes_without_position.extend(no_pos)
            return []
        c._collect_meshtasticd = radio
        c._history.record_observations = MagicMock()
        _federate_positioned_copy(c, peer_write_ts=now - 5)

        geo = c._collect_locked()

        drawn = [f for f in geo["features"]
                 if f["properties"].get("id") == NODE_ID]
        assert len(drawn) == 1 and drawn[0]["properties"].get("position_from")
        persisted = [f for call in c._history.record_observations.call_args_list
                     for f in call.args[0]
                     if f.get("properties", {}).get("id") == NODE_ID]
        assert len(persisted) == 1, persisted
        assert not (persisted[0].get("geometry") or {}).get("coordinates"), (
            f"peer's position persisted as this radio's: {persisted[0]}")
        assert persisted[0]["properties"]["radio_last_heard"] == float(int(heard))


# ── Round 2 (review of e8bb8135) ────────────────────────────────────────────

import re
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "web" / "node_map.html").read_text()


def _js_body(name):
    m = re.search(r"function %s\(([^)]*)\)\s*\{" % name, HTML)
    assert m, name
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(HTML[i], 0)
        i += 1
    return HTML[m.end():i]


def _merge_one(collector, peer_origin, local_entry=None, features=None):
    now = time.time()
    if local_entry is None:
        _, no_position, _ = collector._drain_interface_nodes(
            _fake_interface(now - 60), "meshtasticd")
        position_less = list(no_position)
    else:
        position_less = [local_entry]
    features = {} if features is None else features
    _federate_positioned_copy(collector, peer_write_ts=now - 5)
    snap = collector._federation.get_snapshot.return_value
    snap.by_node[("meshtastic", NODE_ID)]["source_origin"] = peer_origin
    block = collector._merge_federation(features, position_less)
    return features, position_less, block


def _adopted(features):
    return [f for f in features.values()
            if f["properties"].get("id") == NODE_ID and f["properties"].get("position_from")]


class TestR2LowerTrustPeerPositionAdopted:
    """R2 F1 (operator 2026-11-20: "adopt lower-trust positions"): before H4a
    any tagged peer fix drew an untagged local record; tagging must not lose
    those markers. A position-less record adopts ANY peer's valid fix."""

    @pytest.mark.parametrize("origin", ["local_radio", "mqtt_local",
                                        "node_tracker", "meshcore_public"])
    def test_adopts_peer_fix_at_or_below_local_trust(self, collector, origin):
        features, position_less, block = _merge_one(collector, origin)
        assert len(_adopted(features)) == 1, (origin, features)
        assert [e for e in position_less if e.get("id") == NODE_ID] == []
        assert block["position_adopted"] == 1


class TestR2BorrowedPositionIsSaid:
    """R2 F2+F3: the fix's age is UNMEASURABLE (a peer's last_seen is its
    collector write time — H14), so the page must SAY the position is
    borrowed and of unknown age, never draw it as this radio's own."""

    def test_popup_names_the_lender_and_unknown_age(self):
        body = _js_body("createPopupContent")
        assert "props.position_from" in body
        assert "age unknown" in body

    def test_marker_is_styled_borrowed(self):
        assert "node-marker-borrowed" in _js_body("createMarkerIcon")
        assert ".node-marker-borrowed" in HTML  # the CSS exists

    def test_status_endpoint_carries_the_adopted_count(self, collector):
        from utils._map_status_endpoints import build_federation_status
        _merge_one(collector, "local_radio")
        assert build_federation_status(collector)["position_adopted"] == 1

    def test_status_count_is_unknown_before_any_merge(self, collector):
        from utils._map_status_endpoints import build_federation_status
        _federate_stub_copy(collector, peer_write_ts=time.time())
        # no merge has run: unobservable, never a healthy-looking 0
        assert build_federation_status(collector)["position_adopted"] is None


class TestR2NoBorrowedFixInAnyStore:
    """R2 F4: ONE rule for every writer of this box's own state — history AND
    the Source-7 disk cache. A borrowed fix in the cache replays as ours."""

    def test_cache_gets_no_borrowed_coordinates(self, tmp_path):
        fake_home = tmp_path / "home"
        cfg_dir = fake_home / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        with patch("utils.map_data_collector.get_real_user_home",
                   return_value=fake_home):
            c = MapDataCollector(cache_dir=tmp_path / "cache",
                                 enable_history=False, config_dir=cfg_dir)
        for name in ("_collect_unified_tracker", "_collect_direct_radio",
                     "_collect_mqtt", "_collect_node_tracker", "_collect_aredn",
                     "_collect_aredn_worldmap", "_collect_rns_direct",
                     "_collect_meshcore_public"):
            setattr(c, name, lambda: [])
        c._collect_public_fallbacks = lambda current_feature_count=0: []

        def radio():
            _, no_pos, _ = c._drain_interface_nodes(
                _fake_interface(time.time() - 60), "meshtasticd")
            c._nodes_without_position.extend(no_pos)
            return []
        c._collect_meshtasticd = radio
        c._save_cache = MagicMock()
        _federate_positioned_copy(c, peer_write_ts=time.time() - 5)

        geo = c._collect_locked()

        assert _adopted({i: f for i, f in enumerate(geo["features"])}), "not drawn"
        saved = c._save_cache.call_args.args[0]
        mine = [f for f in saved["features"] if f["properties"].get("id") == NODE_ID]
        # The cache replays POSITIONED nodes (Source 7) — a borrowed fix is not
        # ours to replay, so it is not cached at all (history keeps the hearing).
        assert mine == [], mine
        # R2 F5: the inventory does not count a borrowed fix as ours
        assert geo["properties"]["nodes_with_borrowed_position"] == 1
        assert geo["properties"]["nodes_with_position"] == len(geo["features"]) - 1


class TestR2InventoryAndKeys:
    def test_adoption_never_overwrites_another_networks_feature(self, collector):
        """R2 F6: features is keyed by bare id; (network, id) is the identity."""
        other = {"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [1.0, 2.0, 0]},
                 "properties": {"id": NODE_ID, "network": "rns",
                                "source_origin": "rns_path_table"}}
        features = {NODE_ID: other}
        # local_index takes the (rns, id) feature; our (meshtastic, id) entry is
        # position-less — both must survive.
        features, position_less, _ = _merge_one(collector, "local_radio",
                                                features=features)
        assert features[NODE_ID] is other
        assert len(_adopted(features)) == 1

    def test_adopted_marker_gets_this_radios_online_verdict(self, collector):
        """R2 F7: an HTTP-path entry carries no is_online — judge it at adoption
        from THIS radio's last_heard, never leave it undefined."""
        entry = {"id": NODE_ID, "name": "HTTP", "network": "meshtastic",
                 "hw_model": "HELTEC_V3", "last_heard": int(time.time() - 60),
                 "source_origin": "local_radio", "via_mqtt": False}
        features, _, _ = _merge_one(collector, "local_radio", local_entry=entry)
        props = _adopted(features)[0]["properties"]
        assert props["is_online"] is True
        assert props["hardware"] == "HELTEC_V3"


# ── Round 3 (Fable r2, by running) ──────────────────────────────────────────
# Seam: borrowed-ness and its counts were decided in four places (merge,
# collector, 3 endpoint overwrites, page refresh gate) — same defect KIND in
# r1 F5 and r2 F2. One module now owns it: utils.map_borrowed_position.

from types import SimpleNamespace


def _peer_rows(peer, origin, lat, lon, last_seen):
    from utils.map_federation import FederationPeerStatus as PS
    return ([{"network": "meshtastic", "id": NODE_ID, "name": f"from-{peer}",
              "lat": lat, "lon": lon, "last_seen": last_seen,
              "source_origin": origin, "federated_from": peer}],
            PS(hostname=peer, ok=True, last_sync=time.time(), last_count=1,
               last_attempt=time.time()))


def _real_federation(collector, responses):
    """The REAL FederationCollector winner rule, only the fetch patched."""
    fc = FederationCollector(list(responses), poll_interval=10, timeout=1.0)
    with patch("utils.map_federation.fetch_peer_directory",
               side_effect=lambda peer, *a, **kw: responses[peer]):
        fc.poll_once()
    collector._federation = fc
    return fc


def _local_position_less(collector):
    _, no_position, _ = collector._drain_interface_nodes(
        _fake_interface(time.time() - 60), "meshtasticd")
    return list(no_position)


class TestR3BestFixSurvivesThePeerWinnerRule:
    """r2 F1: by_node keeps ONE row per node, chosen by trust then newest
    WRITE time — a GPS-less peer row hid another peer's fix. The snapshot now
    carries the best fix any peer offers, beside the winner."""

    @pytest.mark.parametrize("fix_origin,fix_ts,stub_ts", [
        ("local_radio", 100, 200),      # equal trust, the stub written later
        ("mqtt_global", 200, 100),      # lower trust than the stub
        ("meshcore_public", 200, 100),
    ])
    def test_another_peers_fix_is_adopted(self, collector, fix_origin, fix_ts, stub_ts):
        now = time.time()
        _real_federation(collector, {
            "peerSTUB": _peer_rows("peerSTUB", "local_radio", None, None, now - 1000 + stub_ts),
            "peerFIX": _peer_rows("peerFIX", fix_origin, 19.5, -155.5, now - 1000 + fix_ts),
        })
        features, position_less = {}, _local_position_less(collector)
        block = collector._merge_federation(features, position_less)
        adopted = _adopted(features)
        assert len(adopted) == 1, (block, position_less)
        props = adopted[0]["properties"]
        assert props["position_from"] == "peerFIX"
        assert props["position_origin"] == fix_origin
        assert adopted[0]["geometry"]["coordinates"][:2] == [-155.5, 19.5]

    def test_best_fix_prefers_trust_then_newest(self):
        from utils.map_federation import better_fix
        hi_old = {"lat": 1.0, "lon": 1.0, "source_origin": "local_radio", "last_seen": 1}
        lo_new = {"lat": 2.0, "lon": 2.0, "source_origin": "mqtt_global", "last_seen": 9}
        eq_new = {"lat": 3.0, "lon": 3.0, "source_origin": "local_radio", "last_seen": 5}
        assert better_fix(hi_old, lo_new) is True
        assert better_fix(lo_new, hi_old) is False
        assert better_fix(eq_new, hi_old) is True
        assert better_fix(hi_old, None) is True

    def test_invalid_fix_is_never_best(self, collector):
        """Same coordinate rule as the merge (0,0 = unset GPS)."""
        now = time.time()
        _real_federation(collector, {
            "peerZERO": _peer_rows("peerZERO", "local_radio", 0.0, 0.0, now),
            "peerFIX": _peer_rows("peerFIX", "mqtt_global", 19.5, -155.5, now - 50),
        })
        features, position_less = {}, _local_position_less(collector)
        collector._merge_federation(features, position_less)
        assert _adopted(features)[0]["properties"]["position_from"] == "peerFIX"


def _geo_with(features):
    return {"type": "FeatureCollection", "features": features,
            "properties": {"nodes_with_position": 999}}


def _own(i):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-155.0, 19.0]},
            "properties": {"id": f"!own{i}", "network": "meshtastic",
                           "source_origin": "local_radio"}}


def _borrowed():
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-155.2, 19.2]},
            "properties": {"id": NODE_ID, "network": "meshtastic",
                           "source_origin": "local_radio", "position_from": "peerFIX"}}


class TestR3OneCountEverywhere:
    """r2 F2: the served geojson overwrote nodes_with_position with len(...)
    three times. Every count of positioned nodes goes through ONE function."""

    @pytest.mark.parametrize("preset,bbox", [(None, None), ("local_only", None),
                                             (None, "18,-161,23,-154")])
    def test_served_geojson_excludes_borrowed(self, preset, bbox):
        import json
        from utils._map_node_endpoints import build_geojson_response
        snap = SimpleNamespace(geojson=_geo_with([_own(1), _borrowed()]))
        raw, _ = build_geojson_response(MagicMock(), None, preset, bbox, 10 ** 9,
                                        snapshot=snap)
        props = json.loads(raw)["properties"]
        assert props["nodes_with_position"] == 1, props
        assert props["nodes_with_borrowed_position"] == 1, props

    def test_prometheus_inventory_excludes_borrowed(self, tmp_path):
        from utils import map_metrics
        fake_home = tmp_path / "home"
        cfg_dir = fake_home / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        with patch("utils.map_data_collector.get_real_user_home", return_value=fake_home):
            c = MapDataCollector(cache_dir=tmp_path / "cache", enable_history=False,
                                 config_dir=cfg_dir)
        for name in ("_collect_unified_tracker", "_collect_direct_radio",
                     "_collect_mqtt", "_collect_node_tracker", "_collect_aredn",
                     "_collect_aredn_worldmap", "_collect_rns_direct",
                     "_collect_meshcore_public"):
            setattr(c, name, lambda: [])
        c._collect_public_fallbacks = lambda current_feature_count=0: []

        def radio():
            c._nodes_without_position.extend(_local_position_less(c))
            return []
        c._collect_meshtasticd = radio
        _federate_positioned_copy(c, peer_write_ts=time.time() - 5)
        with patch.object(map_metrics, "set_node_inventory") as inv:
            c._collect_locked()
        assert inv.call_args.kwargs["with_position"] == 0


class TestR3WitnessLifecycle:
    """r2 F6 + mutants m02/m03/m08: the witness is THIS merge's count."""

    def test_resets_each_merge(self, collector):
        _merge_one(collector, "local_radio")
        assert collector._position_adopted_last == 1
        _federate_stub_copy(collector, peer_write_ts=time.time())
        collector._merge_federation({}, _local_position_less(collector))
        assert collector._position_adopted_last == 0

    def test_snapshot_failure_is_unknown(self, collector):
        _merge_one(collector, "local_radio")
        collector._federation.get_snapshot.side_effect = RuntimeError("boom")
        collector._merge_federation({}, [])
        assert collector._position_adopted_last is None

    def test_exception_mid_merge_is_unknown_not_stale(self, collector):
        _merge_one(collector, "local_radio")
        _federate_positioned_copy(collector, peer_write_ts=time.time())
        with patch.object(type(collector), "_is_valid_coordinate",
                          side_effect=RuntimeError("mid-merge")):
            with pytest.raises(RuntimeError):
                collector._merge_federation({}, _local_position_less(collector))
        assert collector._position_adopted_last is None

    def test_no_federation_is_zero(self, collector):
        collector._federation = None
        collector._merge_federation({}, [])
        assert collector._position_adopted_last == 0

    def test_altitude_carried(self, collector):
        _federate_positioned_copy(collector, peer_write_ts=time.time())
        snap = collector._federation.get_snapshot.return_value
        snap.by_node[("meshtastic", NODE_ID)]["altitude"] = 412
        features = {}
        collector._merge_federation(features, _local_position_less(collector))
        assert _adopted(features)[0]["geometry"]["coordinates"] == [-155.21, 19.43, 412]


class TestR3PageRefreshesAndSaysTheSource:
    """r2 F3/F7 + mutant m05: the popup refresh gate must include the lender,
    the popup says the fix's SOURCE, and the lender is escaped."""

    def test_update_gate_includes_position_from(self):
        upd = HTML[HTML.index("const existing = state.markers.get(id);"):]
        gate = upd[:upd.index("if (propsChanged)")]
        assert "position_from" in gate
        assert "position_origin" in gate

    def test_popup_says_fix_source_and_escapes(self):
        body = _js_body("createPopupContent")
        assert "escHtml(String(props.position_from))" in body
        assert "props.position_origin" in body


class TestR3MutantSurvivors:
    """Pins for the three r3 mutants that survived the first pass."""

    @pytest.mark.parametrize("preset,bbox", [("local_only", None),
                                             (None, "18,-161,23,-154")])
    def test_count_holds_when_collapse_fails(self, preset, bbox):
        """The collapse step overwrites the count; when it raises, the preset /
        bbox count is what ships — it must exclude borrowed too."""
        import json
        from utils._map_node_endpoints import build_geojson_response
        snap = SimpleNamespace(geojson=_geo_with([_own(1), _borrowed()]))
        with patch("utils.cross_protocol_collapse.collapse_cross_protocol",
                   side_effect=RuntimeError("collapse down")):
            raw, _ = build_geojson_response(MagicMock(), None, preset, bbox,
                                            10 ** 9, snapshot=snap)
        props = json.loads(raw)["properties"]
        assert props["nodes_with_position"] == 1, props
        assert props["nodes_with_borrowed_position"] == 1, props

    def test_empty_lender_name_is_still_borrowed(self):
        from utils.map_borrowed_position import is_borrowed, own_cache_geojson
        f = _borrowed()
        f["properties"]["position_from"] = ""
        assert is_borrowed(f)
        assert own_cache_geojson({"features": [f]})["features"] == []
