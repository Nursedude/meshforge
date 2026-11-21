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

DESIGN (operator 2026-11-20 18:4x, "switch to the federated representation";
r1-r3's "our record at a borrowed position" leaked into every geometry
consumer): THIS radio's GPS-less record stays exactly as it is (position-less,
ours — history keeps the hearing with no extra code). A peer's fix for that
node is drawn as an ordinary FEDERATED feature — which every consumer already
treats as not-ours — carrying this radio's hearing in separate ``heard_here*``
fields so the page can say it truthfully.
"""

import json
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


def _federate_positioned_copy(collector, peer_write_ts, lat=19.43, lon=-155.21,
                              origin="local_radio"):
    """A peer HAS a GPS fix for a node THIS radio hears without one."""
    _federate_stub_copy(collector, peer_write_ts)
    snap = collector._federation.get_snapshot.return_value
    snap.by_node[("meshtastic", NODE_ID)].update(
        {"lat": lat, "lon": lon, "source_origin": origin})


FED_KEY = f"federated:meshtastic:{NODE_ID}"


def _drain(collector, heard_ts):
    _, no_position, _ = collector._drain_interface_nodes(
        _fake_interface(heard_ts), "meshtasticd")
    return list(no_position)


class TestPeerFixDrawnAsFederated:
    def test_peer_fix_is_a_federated_feature_beside_our_record(self, collector):
        now = time.time()
        heard = now - 60
        position_less = _drain(collector, heard)
        features: dict = {}
        _federate_positioned_copy(collector, peer_write_ts=now - 5)
        collector._merge_federation(features, position_less)

        # ours is untouched: still position-less, still THIS radio's judgement
        _assert_local_record_survived(position_less, heard)
        assert list(features) == [FED_KEY], features
        f = features[FED_KEY]
        props = f["properties"]
        assert f["geometry"]["coordinates"][:2] == [-155.21, 19.43]
        # the federated contract every consumer already honours
        assert props["federated"] is True
        assert props["federated_from"] == "moc5.mf.internal"
        assert props["is_online"] is False and props["is_local"] is False
        assert props["source_origin"] == "local_radio"   # the PEER's origin
        # this radio's hearing, in its own fields
        assert props["heard_here"] is True
        assert props["heard_here_at"] == int(heard)
        assert 55 <= props["heard_here_age_s"] <= 70, props

    @pytest.mark.parametrize("origin", ["local_radio", "mqtt_local",
                                        "node_tracker", "meshcore_public"])
    def test_any_peer_fix_at_or_below_our_trust_is_drawn(self, collector, origin):
        """r2 F1 (operator 11-20 "adopt lower-trust positions"): before H4a an
        untagged local lost to ANY tagged peer fix and the marker showed;
        tagging must not take those markers away."""
        now = time.time()
        position_less = _drain(collector, now - 60)
        features: dict = {}
        _federate_positioned_copy(collector, now - 5, origin=origin)
        collector._merge_federation(features, position_less)
        assert list(features) == [FED_KEY]
        assert features[FED_KEY]["properties"]["source_origin"] == origin
        _assert_local_record_survived(position_less, now - 60)

    def test_heard_long_ago_says_so(self, collector):
        now = time.time()
        heard = now - 3 * 3600
        position_less = _drain(collector, heard)
        features: dict = {}
        _federate_positioned_copy(collector, now - 5)
        collector._merge_federation(features, position_less)
        props = features[FED_KEY]["properties"]
        assert props["heard_here"] is False
        assert props["heard_here_at"] == int(heard)

    def test_peer_stub_without_position_never_replaces(self, collector):
        """The pure stub case stays local (no position to gain)."""
        now = time.time()
        position_less = _drain(collector, now - 60)
        features: dict = {}
        _federate_stub_copy(collector, peer_write_ts=now - 5)
        collector._merge_federation(features, position_less)
        assert features == {}
        _assert_local_record_survived(position_less, now - 60)

    def test_our_own_fix_gets_no_federated_twin(self, collector):
        """Pre-H4a rule kept: a node WE have a fix for is ours alone."""
        now = time.time()
        features = {NODE_ID: {"type": "Feature",
                              "geometry": {"type": "Point", "coordinates": [-155.0, 19.0, 0]},
                              "properties": {"id": NODE_ID, "network": "meshtastic",
                                             "source_origin": "local_radio",
                                             "is_online": True}}}
        _federate_positioned_copy(collector, now - 5)
        collector._merge_federation(features, [])
        assert list(features) == [NODE_ID]

    def test_merge_block_counts_it(self, collector):
        now = time.time()
        position_less = _drain(collector, now - 60)
        _federate_positioned_copy(collector, now - 5)
        block = collector._merge_federation({}, position_less)
        assert block["with_position"] == 1
        assert block["by_network"] == {"meshtastic": 1}
        assert block["position_adopted"] == 1

    def test_witness_zero_off_none_on_snapshot_failure(self, collector):
        assert collector._merge_federation({}, [])["position_adopted"] == 0  # off
        fc = MagicMock(spec=FederationCollector)
        fc.get_snapshot.side_effect = RuntimeError("boom")
        collector._federation = fc
        assert collector._merge_federation({}, [])["position_adopted"] is None


def _peer_rows(peer, origin, lat, lon, last_seen):
    return ([{"network": "meshtastic", "id": NODE_ID, "name": f"from-{peer}",
              "lat": lat, "lon": lon, "last_seen": last_seen,
              "source_origin": origin, "federated_from": peer}],
            FederationPeerStatus(hostname=peer, ok=True, last_sync=time.time(),
                                 last_count=1, last_attempt=time.time()))


def _real_federation(collector, responses):
    """The REAL FederationCollector winner rule, only the fetch patched."""
    fc = FederationCollector(list(responses), poll_interval=10, timeout=1.0)
    with patch("utils.map_federation.fetch_peer_directory",
               side_effect=lambda peer, *a, **kw: responses[peer]):
        fc.poll_once()
    collector._federation = fc
    return fc


class TestBestFixAcrossPeers:
    """r2 F1: by_node keeps ONE row per node (trust, then newest WRITE time)
    — a GPS-less peer row hid another peer's fix. The best fix rides beside a
    winner that has none; F3 (r3 cost 0.24 -> 0.64 s/poll, +34 MB): ONLY then."""

    @pytest.mark.parametrize("fix_origin,fix_ts,stub_ts", [
        ("local_radio", 100, 200),      # equal trust, the stub written later
        ("mqtt_global", 200, 100),      # lower trust than the stub
        ("meshcore_public", 200, 100),
    ])
    def test_another_peers_fix_is_drawn(self, collector, fix_origin, fix_ts, stub_ts):
        now = time.time()
        _real_federation(collector, {
            "peerSTUB": _peer_rows("peerSTUB", "local_radio", None, None, now - 1000 + stub_ts),
            "peerFIX": _peer_rows("peerFIX", fix_origin, 19.5, -155.5, now - 1000 + fix_ts),
        })
        features, position_less = {}, _drain(collector, now - 60)
        collector._merge_federation(features, position_less)
        assert list(features) == [FED_KEY], features
        f = features[FED_KEY]
        assert f["geometry"]["coordinates"][:2] == [-155.5, 19.5]
        assert f["properties"]["federated_from"] == "peerFIX"
        assert f["properties"]["source_origin"] == fix_origin
        assert f["properties"]["heard_here"] is True
        _assert_local_record_survived(position_less, now - 60)

    def test_zero_zero_is_never_the_best_fix(self, collector):
        now = time.time()
        _real_federation(collector, {
            "peerSTUB": _peer_rows("peerSTUB", "local_radio", None, None, now),
            "peerZERO": _peer_rows("peerZERO", "local_radio", 0.0, 0.0, now - 10),
            "peerFIX": _peer_rows("peerFIX", "mqtt_global", 19.5, -155.5, now - 50),
        })
        features = {}
        collector._merge_federation(features, _drain(collector, now - 60))
        assert features[FED_KEY]["properties"]["federated_from"] == "peerFIX"

    def test_best_fix_only_where_the_winner_has_none(self, collector):
        now = time.time()
        fc = _real_federation(collector, {
            "peerA": _peer_rows("peerA", "local_radio", 19.1, -155.1, now),
            "peerB": _peer_rows("peerB", "local_radio", 19.2, -155.2, now - 10),
        })
        assert "best_fix" not in fc.get_snapshot().by_node[("meshtastic", NODE_ID)]

    def test_better_fix_orders_trust_then_newest(self):
        from utils.map_federation import better_fix
        hi_old = {"source_origin": "local_radio", "last_seen": 1}
        lo_new = {"source_origin": "mqtt_global", "last_seen": 9}
        eq_new = {"source_origin": "local_radio", "last_seen": 5}
        assert better_fix(hi_old, lo_new) is True
        assert better_fix(lo_new, hi_old) is False
        assert better_fix(eq_new, hi_old) is True
        assert better_fix(hi_old, None) is True
        assert better_fix(None, hi_old) is False


def _history_collector(tmp_path, heard):
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

    def radio():
        _, no_pos, _ = c._drain_interface_nodes(_fake_interface(heard), "meshtasticd")
        c._nodes_without_position.extend(no_pos)
        return []
    c._collect_meshtasticd = radio
    c._history.record_observations = MagicMock()
    return c


class TestHistoryKeepsOurHearingNotThePeersFix:
    """The ONE exception the design needs falls out of keeping our record:
    the position-less loop persists our hearing (GPS-less, local_radio); the
    federated feature is skipped like every federated feature."""

    def test_history_row_is_our_hearing_without_coordinates(self, tmp_path):
        now = time.time()
        heard = now - 60
        c = _history_collector(tmp_path, heard)
        _federate_positioned_copy(c, peer_write_ts=now - 5)

        geo = c._collect_locked()

        drawn = [f for f in geo["features"] if f["properties"].get("id") == NODE_ID]
        assert len(drawn) == 1 and drawn[0]["properties"]["federated"] is True
        assert [e["id"] for e in geo["properties"]["nodes_without_position"]] == [NODE_ID]
        persisted = [f for call in c._history.record_observations.call_args_list
                     for f in call.args[0]
                     if f.get("properties", {}).get("id") == NODE_ID]
        assert len(persisted) == 1, persisted
        assert not (persisted[0].get("geometry") or {}).get("coordinates"), persisted[0]
        assert persisted[0]["properties"]["source_origin"] == "local_radio"
        assert persisted[0]["properties"]["radio_last_heard"] == float(int(heard))


class TestStatusWitnessIsThePublishedCount:
    """F2: 0 when federation is off. F7: /api/status says what the PUBLISHED
    map says — a merge that has not been published yet does not move it."""

    def test_off_is_zero(self, collector):
        from utils._map_status_endpoints import build_federation_status
        collector._federation = None
        assert build_federation_status(collector)["position_adopted"] == 0

    def test_before_any_publish_is_unknown(self, collector):
        from utils._map_status_endpoints import build_federation_status
        _federate_positioned_copy(collector, time.time())
        assert build_federation_status(collector)["position_adopted"] is None

    def test_reads_the_published_map_and_holds_it(self, collector):
        from utils._map_status_endpoints import build_federation_status
        now = time.time()
        position_less = _drain(collector, now - 60)
        _federate_positioned_copy(collector, now - 5)
        features: dict = {}
        block = collector._merge_federation(features, position_less)
        collector._publish_snapshot({"type": "FeatureCollection",
                                     "features": list(features.values()),
                                     "properties": {"federation": block}})
        assert build_federation_status(collector)["position_adopted"] == 1
        # an unpublished merge with nothing to adopt does not move the witness
        _federate_stub_copy(collector, now)
        assert collector._merge_federation({}, _drain(collector, now - 60))[
            "position_adopted"] == 0
        assert build_federation_status(collector)["position_adopted"] == 1


from tests.test_peer_radio_heard import (  # noqa: E402 — the page-JS harness
    _HTML, _js_engine, _js_fn, _run_page_js)


class TestPageSaysHeardHere:
    """The page labels every federated marker "Not heard by this box's
    radio" — FALSE for these. It must say this radio's hearing and whose fix
    it is, with the fix's age unknown (a peer's last_seen is its WRITE time)."""

    @pytest.mark.parametrize("engine", [_js_engine() or "none"])
    def test_status_text_executed(self, tmp_path, engine):
        if engine == "none":
            pytest.skip("no node and no headless chromium on this box — JS not executed")
        cases = [
            {"federated": True, "network": "meshtastic", "heard_here": True, "heard_here_age_s": 90,
             "federated_from": "moc5", "source_origin": "local_radio"},
            {"federated": True, "network": "meshtastic", "heard_here": False,
             "heard_here_age_s": 3 * 3600,
             "federated_from": "moc5", "source_origin": "mqtt_global"},
            {"federated": True, "federated_from": "moc5"},   # plain federated
            {"federated": True, "heard_here": True, "heard_here_age_s": "x",
             "federated_from": "moc5", "source_origin": "local_radio"},
            # heard here: no peer clause (this radio is the better witness);
            # not heard here: the peer's verdict is said
            {"federated": True, "heard_here": True, "heard_here_age_s": 30,
             "federated_from": "moc5", "source_origin": "local_radio",
             "peer_heard": {"state": "heard", "by": "moc5", "age_s": 30}},
            {"federated": True, "heard_here": False, "heard_here_age_s": 7200,
             "federated_from": "moc5", "source_origin": "local_radio",
             "peer_heard": {"state": "heard", "by": "moc5", "age_s": 30}},
            # 11-08 F6: Meshtastic's window never judges another network
            {"federated": True, "network": "meshcore", "heard_here": False,
             "heard_here_age_s": 7200, "federated_from": "moc5",
             "source_origin": "local_radio"},
        ]
        funcs = ("function meshtasticWindowText(){return '2 h';}\n"
                 "function %s\nfunction %s\nfunction %s\n" % (
                     _js_fn("agoText"), _js_fn("peerHeardText"),
                     _js_fn("nodeStatusText")))
        out = _run_page_js(engine, funcs, "JSON.stringify(%s.map(nodeStatusText))"
                           % json.dumps(cases), tmp_path)
        assert out[0] == "Heard here 1 min ago · position via moc5 (local_radio) · age unknown"
        assert out[1] == ("Not heard here in 2 h (last 3 h ago) · position via moc5 "
                          "(mqtt_global) · age unknown")
        assert out[2] == "Not heard by this box's radio"
        assert out[3] == "Heard here · position via moc5 (local_radio) · age unknown"
        assert out[4] == "Heard here 30 s ago · position via moc5 (local_radio) · age unknown"
        assert out[5].endswith("· age unknown · heard by moc5 30 s ago"), out[5]
        assert out[6] == ("Not heard here recently (last 2 h ago) · position via moc5 "
                          "(local_radio) · age unknown")

    def test_popup_refresh_gate_sees_the_hearing(self):
        gate = _HTML[_HTML.index("const propsChanged = ("):][:600]
        for field in ("heard_here", "heard_here_age_s", "federated_from"):
            assert f"oldProps.{field} !== props.{field}" in gate, field
