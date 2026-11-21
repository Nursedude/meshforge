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
