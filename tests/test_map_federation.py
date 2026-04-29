"""Tests for the FederationCollector (Issue #49 follow-up).

Covers the Phase-1 contract:
  - peer URL building (bare hostname, host:port, full URL)
  - directory payload extraction (positioned + position-less, malformed)
  - self-hostname filtering
  - per-peer fetch error paths (timeout, HTTP non-200, JSON parse, oversize)
  - poll_once merge with conflict resolution (newer last_seen wins)
  - peer status tracking (consecutive_failures, ok, last_error)
  - thread lifecycle (start/stop/no peers/duplicate start)
"""

import json
import time
from unittest.mock import patch, MagicMock

import pytest

from utils.map_federation import (
    FederationCollector,
    FederationPeerStatus,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    _peer_url,
    _extract_features,
    fetch_peer_directory,
    filter_self_from_peers,
    get_local_hostnames,
)


# ── _peer_url ────────────────────────────────────────────────────────────


class TestPeerURL:
    def test_bare_hostname_uses_default_port(self):
        assert _peer_url("moc3") == "http://moc3:5000/api/nodes/directory"

    def test_host_port_form(self):
        assert _peer_url("moc3:8808") == "http://moc3:8808/api/nodes/directory"

    def test_full_url_passthrough(self):
        assert _peer_url("https://moc3.local:5000") == "https://moc3.local:5000/api/nodes/directory"

    def test_custom_port_arg(self):
        assert _peer_url("moc3", port=9000) == "http://moc3:9000/api/nodes/directory"

    def test_custom_path(self):
        assert _peer_url("moc3", path="/api/status").endswith("/api/status")


# ── _extract_features ─────────────────────────────────────────────────────


class TestExtractFeatures:
    def test_extracts_positioned_features(self):
        payload = {
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-155.3, 19.4, 100]},
                "properties": {
                    "id": "!abc",
                    "network": "meshtastic",
                    "name": "Hilo",
                    "last_seen": 1700000000.0,
                    "source_origin": "local_radio",
                },
            }],
            "nodes_without_position": [],
        }
        out = _extract_features(payload, "moc3")
        assert len(out) == 1
        assert out[0]["id"] == "!abc"
        assert out[0]["lat"] == 19.4
        assert out[0]["lon"] == -155.3
        assert out[0]["altitude"] == 100
        assert out[0]["federated_from"] == "moc3"
        assert out[0]["source_origin"] == "local_radio"

    def test_extracts_position_less(self):
        payload = {
            "features": [],
            "nodes_without_position": [
                {"id": "rns_xyz", "network": "rns", "name": "Some RNS",
                 "last_seen": 1700000000.0, "source_origin": "rns_path_table"},
            ],
        }
        out = _extract_features(payload, "moc3")
        assert len(out) == 1
        assert out[0]["network"] == "rns"
        assert out[0]["lat"] is None
        assert out[0]["lon"] is None
        assert out[0]["federated_from"] == "moc3"

    def test_rejects_missing_id(self):
        payload = {
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-155.3, 19.4]},
                "properties": {"network": "meshtastic"},  # no id
            }],
            "nodes_without_position": [],
        }
        assert _extract_features(payload, "p") == []

    def test_rejects_missing_network(self):
        payload = {
            "features": [],
            "nodes_without_position": [{"id": "x"}],  # no network
        }
        assert _extract_features(payload, "p") == []

    def test_handles_empty_payload(self):
        assert _extract_features({}, "p") == []

    def test_handles_geometry_without_coords(self):
        payload = {
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point"},  # no coordinates
                "properties": {"id": "x", "network": "meshtastic"},
            }],
        }
        out = _extract_features(payload, "p")
        assert len(out) == 1
        assert out[0]["lat"] is None and out[0]["lon"] is None


# ── filter_self_from_peers ────────────────────────────────────────────────


class TestSelfFiltering:
    def test_strips_exact_match(self):
        assert filter_self_from_peers(["host-a", "host-b"], local_names=["host-a"]) == ["host-b"]

    def test_strips_prefix_match_against_fleet_alias(self):
        # Fleet config might list "meshforge-<host>" while bare hostname is "<host>"
        result = filter_self_from_peers(
            ["meshforge-host-a", "meshforge-host-b"], local_names=["host-b"]
        )
        assert result == ["meshforge-host-a"]

    def test_case_insensitive(self):
        assert filter_self_from_peers(["MyHost"], local_names=["myhost"]) == []

    def test_no_local_names_keeps_all(self):
        assert filter_self_from_peers(["host-a", "host-b"], local_names=[]) == ["host-a", "host-b"]

    def test_get_local_hostnames_returns_list(self):
        names = get_local_hostnames()
        assert isinstance(names, list)
        # Should at least include something
        assert all(isinstance(n, str) for n in names)


# ── fetch_peer_directory ──────────────────────────────────────────────────


class TestFetchPeerDirectory:
    def _mock_resp(self, payload: dict, status: int = 200):
        """Build a mock urlopen context manager returning JSON payload."""
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.status = status
        cm.read = MagicMock(return_value=json.dumps(payload).encode("utf-8"))
        return cm

    def test_successful_fetch_populates_status(self):
        payload = {
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-155.3, 19.4]},
                "properties": {"id": "!abc", "network": "meshtastic",
                               "last_seen": 1700000000.0},
            }],
            "nodes_without_position": [],
        }
        with patch("utils.map_federation.urllib.request.urlopen",
                   return_value=self._mock_resp(payload)):
            entries, status = fetch_peer_directory("moc3", timeout=1.0)
        assert status.ok is True
        assert status.last_count == 1
        assert status.last_error is None
        assert status.last_sync is not None
        assert status.last_latency_ms >= 0
        assert len(entries) == 1

    def test_http_500_marks_failed(self):
        with patch("utils.map_federation.urllib.request.urlopen",
                   return_value=self._mock_resp({}, status=500)):
            entries, status = fetch_peer_directory("moc3", timeout=1.0)
        assert status.ok is False
        assert "500" in (status.last_error or "")
        assert entries == []

    def test_timeout_marks_failed(self):
        import socket as _sk

        def boom(*a, **kw):
            raise _sk.timeout("timed out")
        with patch("utils.map_federation.urllib.request.urlopen", side_effect=boom):
            entries, status = fetch_peer_directory("moc3", timeout=0.5)
        assert status.ok is False
        assert status.last_error and "timeout" in status.last_error.lower()
        assert entries == []

    def test_invalid_json_marks_parse_error(self):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.status = 200
        cm.read = MagicMock(return_value=b"not json{")
        with patch("utils.map_federation.urllib.request.urlopen", return_value=cm):
            entries, status = fetch_peer_directory("moc3", timeout=1.0)
        assert status.ok is False
        assert "parse" in (status.last_error or "")
        assert entries == []

    def test_oversize_response_rejected(self):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.status = 200
        cm.read = MagicMock(return_value=b"x" * 200)  # 200 bytes
        with patch("utils.map_federation.urllib.request.urlopen", return_value=cm):
            entries, status = fetch_peer_directory("moc3", timeout=1.0, max_bytes=100)
        assert status.ok is False
        assert "100" in (status.last_error or "") or "bytes" in (status.last_error or "")
        assert entries == []


# ── FederationCollector.poll_once ─────────────────────────────────────────


class TestPollOnce:
    def test_merges_two_peers(self):
        fc = FederationCollector(["a", "b"], poll_interval=10, timeout=1.0)
        responses = {
            "a": ([{"network": "rns", "id": "1", "name": "A1",
                    "lat": None, "lon": None, "last_seen": 100,
                    "source_origin": "rns_path_table", "federated_from": "a"}],
                  FederationPeerStatus(hostname="a", ok=True, last_sync=time.time(),
                                       last_count=1, last_attempt=time.time())),
            "b": ([{"network": "rns", "id": "2", "name": "B1",
                    "lat": None, "lon": None, "last_seen": 200,
                    "source_origin": "rns_path_table", "federated_from": "b"}],
                  FederationPeerStatus(hostname="b", ok=True, last_sync=time.time(),
                                       last_count=1, last_attempt=time.time())),
        }
        with patch("utils.map_federation.fetch_peer_directory",
                   side_effect=lambda peer, *a, **kw: responses[peer]):
            fc.poll_once()
        snap = fc.get_snapshot()
        assert len(snap.by_node) == 2
        assert ("rns", "1") in snap.by_node
        assert ("rns", "2") in snap.by_node
        assert snap.peer_status["a"].ok
        assert snap.peer_status["b"].ok

    def test_conflict_newer_last_seen_wins(self):
        fc = FederationCollector(["a", "b"], poll_interval=10)
        # Same node on both peers — b has newer last_seen
        same_node_a = {"network": "rns", "id": "shared", "name": "from-a",
                       "lat": 1.0, "lon": 1.0, "last_seen": 100,
                       "source_origin": "rns_path_table", "federated_from": "a"}
        same_node_b = {"network": "rns", "id": "shared", "name": "from-b",
                       "lat": 2.0, "lon": 2.0, "last_seen": 200,
                       "source_origin": "rns_path_table", "federated_from": "b"}
        responses = {
            "a": ([same_node_a], FederationPeerStatus(hostname="a", ok=True,
                  last_sync=time.time(), last_count=1, last_attempt=time.time())),
            "b": ([same_node_b], FederationPeerStatus(hostname="b", ok=True,
                  last_sync=time.time(), last_count=1, last_attempt=time.time())),
        }
        with patch("utils.map_federation.fetch_peer_directory",
                   side_effect=lambda peer, *a, **kw: responses[peer]):
            fc.poll_once()
        snap = fc.get_snapshot()
        merged = snap.by_node[("rns", "shared")]
        assert merged["name"] == "from-b"
        assert merged["last_seen"] == 200
        assert set(merged["seen_by_peers"]) == {"a", "b"}

    def test_failed_peer_increments_consecutive_failures(self):
        fc = FederationCollector(["bad"], poll_interval=10)
        # First poll: peer fails
        with patch("utils.map_federation.fetch_peer_directory",
                   return_value=([], FederationPeerStatus(
                       hostname="bad", ok=False, last_error="boom",
                       last_attempt=time.time()))):
            fc.poll_once()
            fc.poll_once()
        snap = fc.get_snapshot()
        assert snap.peer_status["bad"].consecutive_failures == 2
        assert snap.peer_status["bad"].ok is False

    def test_success_resets_consecutive_failures(self):
        fc = FederationCollector(["p"], poll_interval=10)
        # First: fail
        with patch("utils.map_federation.fetch_peer_directory",
                   return_value=([], FederationPeerStatus(
                       hostname="p", ok=False, last_error="boom",
                       last_attempt=time.time()))):
            fc.poll_once()
        assert fc.get_snapshot().peer_status["p"].consecutive_failures == 1
        # Then: succeed
        good = FederationPeerStatus(hostname="p", ok=True, last_sync=time.time(),
                                    last_count=0, last_attempt=time.time())
        with patch("utils.map_federation.fetch_peer_directory",
                   return_value=([], good)):
            fc.poll_once()
        assert fc.get_snapshot().peer_status["p"].consecutive_failures == 0

    def test_executor_crash_does_not_kill_other_peers(self):
        fc = FederationCollector(["good", "crashy"], poll_interval=10)
        good_status = FederationPeerStatus(hostname="good", ok=True,
                                           last_sync=time.time(), last_count=1,
                                           last_attempt=time.time())
        good_entry = {"network": "rns", "id": "g1", "name": "Gn",
                      "lat": None, "lon": None, "last_seen": 100,
                      "source_origin": "", "federated_from": "good"}

        def fake_fetch(peer, *a, **kw):
            if peer == "crashy":
                raise RuntimeError("nope")
            return ([good_entry], good_status)

        with patch("utils.map_federation.fetch_peer_directory", side_effect=fake_fetch):
            fc.poll_once()
        snap = fc.get_snapshot()
        assert ("rns", "g1") in snap.by_node
        assert snap.peer_status["crashy"].ok is False
        assert snap.peer_status["crashy"].consecutive_failures == 1
        assert snap.peer_status["good"].ok is True


# ── Lifecycle ──────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_no_peers_does_not_start_thread(self):
        fc = FederationCollector([], poll_interval=10)
        fc.start()
        assert fc._thread is None

    def test_start_and_stop(self):
        fc = FederationCollector(["x"], poll_interval=3600)
        with patch("utils.map_federation.fetch_peer_directory",
                   return_value=([], FederationPeerStatus(hostname="x", ok=True))):
            fc.start()
            assert fc._thread is not None
            fc.stop(timeout=2.0)
            assert fc._thread is None

    def test_double_start_is_noop(self):
        fc = FederationCollector(["x"], poll_interval=3600)
        with patch("utils.map_federation.fetch_peer_directory",
                   return_value=([], FederationPeerStatus(hostname="x", ok=True))):
            fc.start()
            t1 = fc._thread
            fc.start()  # second start should be a no-op
            assert fc._thread is t1
            fc.stop(timeout=2.0)

    def test_get_snapshot_is_safe_copy(self):
        fc = FederationCollector(["x"], poll_interval=3600)
        snap1 = fc.get_snapshot()
        # Mutating the returned dict should not affect internal state
        snap1.by_node[("rns", "fake")] = {"id": "fake"}
        snap2 = fc.get_snapshot()
        assert ("rns", "fake") not in snap2.by_node
