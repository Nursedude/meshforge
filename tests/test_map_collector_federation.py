"""Tests for MapDataCollector + FederationCollector integration.

Covers Phase 2 of the federation work (Issue #49 follow-up):
  - federation_peers default = None triggers fleet.json bootstrap
  - empty list = federation disabled, no thread, federation block reflects it
  - merge into local features (positioned & position-less)
  - local-wins on (network, id) collisions
  - federation block shape in geojson properties
  - bootstrap skips self via filter_self_from_peers
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from utils.map_data_collector import MapDataCollector
from utils.map_federation import FederationCollector, FederationPeerStatus, FederationSnapshot


@pytest.fixture
def collector(tmp_path):
    """Collector with no fleet.json available (no bootstrap)."""
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


class TestFederationDefaults:
    def test_no_fleet_json_means_empty_peers(self, collector):
        assert collector._settings.get("federation_peers") == []
        assert collector._federation is None

    def test_settings_persist_after_bootstrap(self, tmp_path):
        # First collector: no fleet.json -> persists []
        fake_home = tmp_path / "home"
        cfg_dir = fake_home / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        with patch("utils.map_data_collector.get_real_user_home", return_value=fake_home):
            c1 = MapDataCollector(cache_dir=tmp_path / "c1", enable_history=False,
                                  config_dir=cfg_dir)
        assert c1._settings.get("federation_peers") == []

        # Second collector reads persisted [], doesn't re-bootstrap
        with patch("utils.map_data_collector.get_real_user_home", return_value=fake_home):
            c2 = MapDataCollector(cache_dir=tmp_path / "c1", enable_history=False,
                                  config_dir=cfg_dir)
        assert c2._settings.get("federation_peers") == []
        assert c2._federation is None


class TestBootstrapFromFleetJson:
    def test_bootstrap_reads_peer_ips(self, tmp_path):
        fake_home = tmp_path / "home"
        cfg_dir = fake_home / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        fleet = {
            "this_host": "host-a",
            "peers": {
                "host-a": {"ip": "10.0.0.1"},
                "host-b": {"ip": "10.0.0.2"},
                "host-c": {"ip": "10.0.0.3"},
            },
        }
        (cfg_dir / "fleet.json").write_text(json.dumps(fleet))

        with patch("utils.map_data_collector.get_real_user_home", return_value=fake_home):
            # Force self-name match so host-a is filtered out
            with patch("utils.map_federation.get_local_hostnames",
                       return_value=["host-a"]):
                c = MapDataCollector(cache_dir=tmp_path / "cache", enable_history=False,
                                     config_dir=cfg_dir)

        peers = c._settings.get("federation_peers")
        # host-a (self) excluded; host-b and host-c IPs included
        assert "10.0.0.1" not in peers
        assert "10.0.0.2" in peers
        assert "10.0.0.3" in peers
        # Federation collector instantiated since peers non-empty
        assert c._federation is not None

    def test_bootstrap_falls_back_to_hostname_when_no_ip(self, tmp_path):
        fake_home = tmp_path / "home"
        cfg_dir = fake_home / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        fleet = {"peers": {"alpha": {}, "beta": {}}}  # no ips
        (cfg_dir / "fleet.json").write_text(json.dumps(fleet))

        with patch("utils.map_data_collector.get_real_user_home", return_value=fake_home):
            with patch("utils.map_federation.get_local_hostnames",
                       return_value=["alpha"]):
                c = MapDataCollector(cache_dir=tmp_path / "cache", enable_history=False,
                                     config_dir=cfg_dir)

        peers = c._settings.get("federation_peers")
        assert peers == ["beta"]

    def test_bootstrap_handles_malformed_fleet_json(self, tmp_path):
        fake_home = tmp_path / "home"
        cfg_dir = fake_home / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "fleet.json").write_text("not valid json{")

        with patch("utils.map_data_collector.get_real_user_home", return_value=fake_home):
            c = MapDataCollector(cache_dir=tmp_path / "cache", enable_history=False,
                                 config_dir=cfg_dir)

        # Bootstrap failed quietly -> []
        assert c._settings.get("federation_peers") == []


class TestMergeFederationBlock:
    """The _merge_federation method folds peer entries into local features
    + position_less. Local always wins on (network, node_id) collisions."""

    def _stub_federation(self, collector, by_node, peers=("peer-a",)):
        """Inject a fake FederationCollector that returns the given snapshot."""
        fc = MagicMock(spec=FederationCollector)
        fc.peers = list(peers)
        snap = FederationSnapshot(
            by_node=by_node,
            peer_status={p: FederationPeerStatus(hostname=p, ok=True,
                                                  last_sync=100, last_attempt=100,
                                                  last_count=len(by_node))
                         for p in peers},
            last_sync=100,
            last_attempt=100,
        )
        fc.get_snapshot.return_value = snap
        collector._federation = fc

    def test_disabled_federation_returns_empty_block(self, collector):
        block = collector._merge_federation({}, [])
        assert block["enabled"] is False
        assert block["total"] == 0
        assert block["peers"] == []

    def test_merge_adds_positioned_federated_node(self, collector):
        self._stub_federation(collector, {
            ("rns", "abc"): {
                "id": "abc", "network": "rns", "name": "Federated RNS",
                "lat": 19.4, "lon": -155.3, "altitude": None,
                "last_seen": 100, "source_origin": "rns_path_table",
                "federated_from": "peer-a", "seen_by_peers": ["peer-a"],
            },
        })
        features: dict = {}
        position_less: list = []
        block = collector._merge_federation(features, position_less)

        assert block["enabled"] is True
        assert block["total"] == 1
        assert block["with_position"] == 1
        assert block["without_position"] == 0
        assert block["by_network"]["rns"] == 1

        # The new feature is keyed under the federated namespace
        fed_keys = [k for k in features.keys() if k.startswith("federated:")]
        assert len(fed_keys) == 1
        feat = features[fed_keys[0]]
        assert feat["properties"]["federated"] is True
        assert feat["properties"]["federated_from"] == "peer-a"
        assert feat["geometry"]["coordinates"][:2] == [-155.3, 19.4]
        assert position_less == []

    def test_merge_adds_position_less_federated_node(self, collector):
        self._stub_federation(collector, {
            ("rns", "noGPS"): {
                "id": "noGPS", "network": "rns", "name": "No GPS RNS",
                "lat": None, "lon": None, "altitude": None,
                "last_seen": 100, "source_origin": "rns_path_table",
                "federated_from": "peer-a", "seen_by_peers": ["peer-a"],
            },
        })
        features: dict = {}
        position_less: list = []
        block = collector._merge_federation(features, position_less)

        assert block["with_position"] == 0
        assert block["without_position"] == 1
        assert features == {}
        assert len(position_less) == 1
        assert position_less[0]["federated"] is True
        assert position_less[0]["network"] == "rns"

    def test_local_wins_on_collision(self, collector):
        """Local wins when source_origin priority is equal or higher.

        Both entries here carry `local_radio` (priority 100) — at parity, the
        original 'local always wins' guarantee against peer noise still holds.
        Federated peer entries can only override at *strictly higher* priority
        (see test_federated_higher_priority_overrides_local below).
        """
        self._stub_federation(collector, {
            ("meshtastic", "!abc"): {
                "id": "!abc", "network": "meshtastic", "name": "FROM PEER",
                "lat": 1.0, "lon": 1.0, "altitude": None,
                "last_seen": 200, "source_origin": "local_radio",
                "federated_from": "peer-a", "seen_by_peers": ["peer-a"],
            },
        })
        features = {
            "!abc": {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-1.0, -1.0]},
                "properties": {
                    "id": "!abc", "network": "meshtastic", "name": "LOCAL",
                    "source_origin": "local_radio",
                },
            },
        }
        position_less: list = []
        block = collector._merge_federation(features, position_less)

        assert block["total"] == 0
        assert "!abc" in features
        assert features["!abc"]["properties"]["name"] == "LOCAL"
        assert "federated:meshtastic:!abc" not in features

    def test_local_wins_on_position_less_collision(self, collector):
        """Equal-priority position_less stub blocks the federated copy."""
        self._stub_federation(collector, {
            ("rns", "shared"): {
                "id": "shared", "network": "rns", "name": "FROM PEER",
                "lat": None, "lon": None,
                "last_seen": 200, "source_origin": "rns_path_table",
                "federated_from": "peer-a", "seen_by_peers": ["peer-a"],
            },
        })
        position_less = [{
            "id": "shared", "network": "rns", "name": "LOCAL",
            "source_origin": "rns_path_table",
        }]
        block = collector._merge_federation({}, position_less)

        assert block["total"] == 0
        assert len(position_less) == 1
        assert position_less[0]["name"] == "LOCAL"
        assert "federated" not in position_less[0]

    def test_federated_higher_priority_overrides_local(self, collector):
        """A federated peer's local_radio (100) replaces a local meshcore_public (30).

        This is the cross-box bridge: when a sister fleet box is the only one
        with the radio hardware, its locally-heard observation must beat the
        public-firehose entry of the same hash. Without priority-aware merge,
        the public position (often stale or imprecise) would stick.
        """
        self._stub_federation(collector, {
            ("meshcore", "812e3c8e"): {
                "id": "812e3c8e", "network": "meshcore", "name": "MA-LOCAL-HEARD",
                "lat": 19.435274, "lon": -155.213797, "altitude": None,
                "last_seen": 200, "source_origin": "local_radio",
                "federated_from": "meshanchor-server", "seen_by_peers": ["meshanchor-server"],
            },
        })
        features = {
            "812e3c8e": {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-155.213, 19.435]},
                "properties": {
                    "id": "812e3c8e", "network": "meshcore", "name": "PUBLIC-STALE",
                    "source_origin": "meshcore_public",
                },
            },
        }
        position_less: list = []
        block = collector._merge_federation(features, position_less)

        # Local entry replaced by federated; counted as federated_with_position
        assert "812e3c8e" not in features  # local stub dropped
        assert "federated:meshcore:812e3c8e" in features
        fed_feat = features["federated:meshcore:812e3c8e"]
        assert fed_feat["properties"]["source_origin"] == "local_radio"
        assert fed_feat["properties"]["federated"] is True
        assert block["with_position"] == 1
        assert block["by_network"]["meshcore"] == 1

    def test_federated_lower_priority_yields_to_local(self, collector):
        """The reverse: local local_radio (100) keeps a federated meshcore_public (30) out."""
        self._stub_federation(collector, {
            ("meshcore", "abc"): {
                "id": "abc", "network": "meshcore", "name": "PEER-PUBLIC",
                "lat": 0.0, "lon": 0.0, "altitude": None,
                "last_seen": 200, "source_origin": "meshcore_public",
                "federated_from": "peer-x", "seen_by_peers": ["peer-x"],
            },
        })
        features = {
            "abc": {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1.0, 1.0]},
                "properties": {
                    "id": "abc", "network": "meshcore", "name": "LOCAL-HEARD",
                    "source_origin": "local_radio",
                },
            },
        }
        block = collector._merge_federation(features, [])

        assert block["total"] == 0
        assert features["abc"]["properties"]["name"] == "LOCAL-HEARD"
        assert "federated:meshcore:abc" not in features

    def test_federated_higher_priority_overrides_position_less(self, collector):
        """Federated local_radio with position upgrades a local position_less stub."""
        self._stub_federation(collector, {
            ("meshcore", "xyz"): {
                "id": "xyz", "network": "meshcore", "name": "MA-PEER",
                "lat": 19.4, "lon": -155.2, "altitude": None,
                "last_seen": 200, "source_origin": "local_radio",
                "federated_from": "meshanchor-server", "seen_by_peers": ["meshanchor-server"],
            },
        })
        # Stub from a low-priority local source (no GPS)
        position_less = [{
            "id": "xyz", "network": "meshcore", "name": "LOCAL-NOPOS",
            "source_origin": "meshcore_public",
        }]
        block = collector._merge_federation({}, position_less)

        # The local stub is dropped; the federated feature is added with position
        assert len(position_less) == 0
        assert block["with_position"] == 1
        # And it carries the federated provenance
        assert block["by_network"]["meshcore"] == 1

    def test_peer_status_in_block(self, collector):
        self._stub_federation(collector, {}, peers=("peer-a", "peer-b"))
        block = collector._merge_federation({}, [])

        assert len(block["peer_status"]) == 2
        statuses = {s["hostname"]: s for s in block["peer_status"]}
        assert statuses["peer-a"]["ok"] is True
        assert statuses["peer-b"]["ok"] is True

    def test_snapshot_failure_returns_empty_block(self, collector):
        fc = MagicMock(spec=FederationCollector)
        fc.peers = ["peer-a"]
        fc.get_snapshot.side_effect = RuntimeError("boom")
        collector._federation = fc

        block = collector._merge_federation({}, [])
        assert block["enabled"] is False
        assert block["total"] == 0


class TestGeoJSONIncludesFederationBlock:
    """Federation block must always appear in geojson properties — even
    when disabled — so the frontend can unconditionally read it."""

    def test_disabled_federation_present_as_block(self, tmp_path):
        from utils.map_data_collector import MapDataCollector
        fake_home = tmp_path / "home"
        cfg_dir = fake_home / ".config" / "meshforge"
        cfg_dir.mkdir(parents=True)
        with patch("utils.map_data_collector.get_real_user_home", return_value=fake_home):
            c = MapDataCollector(cache_dir=tmp_path / "cache", enable_history=False,
                                 config_dir=cfg_dir)

        # Mock all sources to return nothing
        for src in ("_collect_unified_tracker", "_collect_meshtasticd",
                    "_collect_direct_radio", "_collect_mqtt",
                    "_collect_node_tracker", "_collect_aredn",
                    "_collect_aredn_worldmap", "_collect_rns_direct",
                    "_collect_meshcore_public", "_collect_public_fallbacks"):
            patch.object(c, src, return_value=[]).start()

        result = c.collect(max_age_seconds=0)
        assert "federation" in result["properties"]
        block = result["properties"]["federation"]
        assert block["enabled"] is False
        assert block["total"] == 0
        assert block["peers"] == []
