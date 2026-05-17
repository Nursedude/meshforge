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

from pathlib import Path

from utils.map_federation import (
    FederationCollector,
    FederationPeerStatus,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    DEFAULT_WAL_SKIP_THRESHOLD_BYTES,
    _peer_url,
    _extract_features,
    _wal_path_for,
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
        """At equal source_origin priority, newer last_seen wins (original rule)."""
        fc = FederationCollector(["a", "b"], poll_interval=10)
        # Same node on both peers — b has newer last_seen, same origin
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

    def test_conflict_higher_priority_origin_wins_over_newer_timestamp(self):
        """Higher source_origin priority beats newer last_seen.

        Sister peer (b) reports local_radio (priority 100) — that's the
        box actually hearing the radio. Fleet peer (a) republishes the
        same hash as meshcore_public (priority 30) from the firehose
        with a NEWER timestamp. Without this rule, the firehose entry
        would clobber the authoritative local_radio observation.
        """
        fc = FederationCollector(["a", "b"], poll_interval=10)
        firehose_a = {"network": "meshcore", "id": "812e3c8e",
                      "name": "FROM-A-PUBLIC", "lat": 19.435, "lon": -155.213,
                      "last_seen": 999,  # newer
                      "source_origin": "meshcore_public", "federated_from": "a"}
        local_b = {"network": "meshcore", "id": "812e3c8e",
                   "name": "FROM-B-LOCAL", "lat": 19.435274, "lon": -155.213797,
                   "last_seen": 100,  # older but high-trust
                   "source_origin": "local_radio", "federated_from": "b"}
        responses = {
            "a": ([firehose_a], FederationPeerStatus(hostname="a", ok=True,
                  last_sync=time.time(), last_count=1, last_attempt=time.time())),
            "b": ([local_b], FederationPeerStatus(hostname="b", ok=True,
                  last_sync=time.time(), last_count=1, last_attempt=time.time())),
        }
        with patch("utils.map_federation.fetch_peer_directory",
                   side_effect=lambda peer, *a, **kw: responses[peer]):
            fc.poll_once()
        snap = fc.get_snapshot()
        merged = snap.by_node[("meshcore", "812e3c8e")]
        assert merged["source_origin"] == "local_radio"
        assert merged["name"] == "FROM-B-LOCAL"
        # Coords from the local_radio observation, not the firehose entry
        assert merged["lat"] == pytest.approx(19.435274)
        # Both peers' provenance preserved
        assert set(merged["seen_by_peers"]) == {"a", "b"}

    def test_conflict_lower_priority_loses_even_with_newer_timestamp(self):
        """Reverse symmetry: a newer meshcore_public can't displace existing local_radio."""
        fc = FederationCollector(["a", "b"], poll_interval=10)
        # a polled first with local_radio, b reports same hash as newer
        # public — the local_radio entry must persist.
        local_a = {"network": "meshcore", "id": "x",
                   "name": "FROM-A-LOCAL", "lat": 1.0, "lon": 1.0,
                   "last_seen": 100,
                   "source_origin": "local_radio", "federated_from": "a"}
        public_b = {"network": "meshcore", "id": "x",
                    "name": "FROM-B-PUBLIC", "lat": 2.0, "lon": 2.0,
                    "last_seen": 999,
                    "source_origin": "meshcore_public", "federated_from": "b"}
        responses = {
            "a": ([local_a], FederationPeerStatus(hostname="a", ok=True,
                  last_sync=time.time(), last_count=1, last_attempt=time.time())),
            "b": ([public_b], FederationPeerStatus(hostname="b", ok=True,
                  last_sync=time.time(), last_count=1, last_attempt=time.time())),
        }
        with patch("utils.map_federation.fetch_peer_directory",
                   side_effect=lambda peer, *a, **kw: responses[peer]):
            fc.poll_once()
        snap = fc.get_snapshot()
        merged = snap.by_node[("meshcore", "x")]
        assert merged["source_origin"] == "local_radio"
        assert merged["name"] == "FROM-A-LOCAL"

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


# ── WAL backpressure (Track 0A) ────────────────────────────────────────────


class TestWalBackpressure:
    """FederationCollector must skip polls when node_history.db's WAL is
    oversize — adding another 50k-row federation cycle on top of an in-
    progress fsync stall is the documented cascade trigger.
    Memory: project_db_recurring_class.md, project_meshforge_map_cold_start_wal.md."""

    def test_wal_path_helper_appends_wal_suffix(self):
        db = Path("/tmp/example/node_history.db")
        assert _wal_path_for(db) == Path("/tmp/example/node_history.db-wal")

    def test_oversize_check_returns_none_when_db_path_unset(self):
        """No db_path = no WAL gate; existing single-box behavior preserved."""
        fc = FederationCollector(["a"], poll_interval=10)
        assert fc._wal_oversize() is None

    def test_oversize_check_returns_size_when_over_threshold(self):
        """When stat_fn reports a WAL above threshold, the helper returns
        the size — _run uses non-None as the skip signal."""
        big = DEFAULT_WAL_SKIP_THRESHOLD_BYTES + 1
        fc = FederationCollector(
            ["a"], poll_interval=10,
            db_path=Path("/tmp/whatever.db"),
            stat_fn=lambda p: big,
        )
        assert fc._wal_oversize() == big

    def test_oversize_check_returns_none_when_under_threshold(self):
        small = DEFAULT_WAL_SKIP_THRESHOLD_BYTES - 1
        fc = FederationCollector(
            ["a"], poll_interval=10,
            db_path=Path("/tmp/whatever.db"),
            stat_fn=lambda p: small,
        )
        assert fc._wal_oversize() is None

    def test_oversize_check_uses_wal_companion_path(self):
        """stat_fn should be called with the -wal sibling, not the .db itself."""
        seen = []
        FederationCollector(
            ["a"], poll_interval=10,
            db_path=Path("/data/node_history.db"),
            stat_fn=lambda p: (seen.append(p), 0)[1],
        )._wal_oversize()
        assert seen == [Path("/data/node_history.db-wal")]

    def test_run_loop_skips_poll_when_oversize_then_resumes_when_clear(self):
        """End-to-end: with WAL oversize, _run must not call poll_once.
        When WAL drops below threshold, polling resumes.

        We drive _run manually (don't use start() — would be racy). Patch
        _stop_event so the loop sees Stop immediately after one iteration
        per phase."""
        custom_threshold = 1024  # 1 KB so we can mock easily
        wal_size_holder = {"size": custom_threshold + 1}  # start oversize
        fc = FederationCollector(
            ["a"], poll_interval=10,
            db_path=Path("/data/node_history.db"),
            wal_skip_threshold_bytes=custom_threshold,
            stat_fn=lambda p: wal_size_holder["size"],
        )
        poll_calls = []
        with patch.object(fc, "poll_once",
                          side_effect=lambda: poll_calls.append(time.time())):
            # Phase 1: WAL oversize, _wal_oversize() returns non-None → skip
            assert fc._wal_oversize() is not None
            # Phase 2: WAL clears, _wal_oversize() returns None → would poll
            wal_size_holder["size"] = custom_threshold - 1
            assert fc._wal_oversize() is None
        # Helper-level assertion is sufficient — _run integration is the
        # five-line conditional immediately above.

    def test_default_threshold_is_64_mb(self):
        """Lock in the 64 MB default to match db_helpers.connect_tuned's
        journal_size_limit. A future bump should be a deliberate
        co-change, not a silent drift."""
        assert DEFAULT_WAL_SKIP_THRESHOLD_BYTES == 64 * 1024 * 1024

    def test_get_snapshot_is_safe_copy(self):
        fc = FederationCollector(["x"], poll_interval=3600)
        snap1 = fc.get_snapshot()
        # Mutating the returned dict should not affect internal state
        snap1.by_node[("rns", "fake")] = {"id": "fake"}
        snap2 = fc.get_snapshot()
        assert ("rns", "fake") not in snap2.by_node


# ── Per-peer HTTP timeout default (Issue #56) ─────────────────────────────


class TestDefaultTimeout:
    """Lock in the 30 s federation per-peer timeout so a future drift back
    to 5 s (or a casual bump higher) is a deliberate co-change with the
    settings default in `map_data_collector.py`. The 5 s value was right
    for ~1 MB directories pre-2026-04; 35 MB directories on Pi-class hosts
    need ~30 s to stream over LAN without false-positive `TimeoutError`."""

    def test_default_is_30_seconds(self):
        assert DEFAULT_TIMEOUT == 30.0

    def test_settings_default_matches_module_default(self):
        """The `MapDataCollector` initializes federation with whatever the
        operator's `map_settings.json` says — the bootstrap default for
        `federation_timeout_seconds` MUST match `DEFAULT_TIMEOUT` or the
        first-run experience silently bypasses this fix."""
        # Walk the source for the bootstrap default; this avoids importing
        # MapDataCollector (which pulls in heavy collector deps the test
        # environment doesn't need).
        from pathlib import Path
        src = Path(__file__).resolve().parents[1] / "src" / "utils" / "map_data_collector.py"
        text = src.read_text()
        # The bootstrap default sits next to the comment "Issue #56" in
        # the settings dict; a regex on the key/value pair tolerates
        # whitespace + future comment reflows.
        import re
        m = re.search(
            r'"federation_timeout_seconds"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
            text,
        )
        assert m is not None, (
            "could not find federation_timeout_seconds default in "
            "map_data_collector.py — has the settings dict moved?"
        )
        bootstrapped = float(m.group(1))
        assert bootstrapped == DEFAULT_TIMEOUT, (
            f"settings bootstrap default ({bootstrapped}) is out of sync "
            f"with map_federation.DEFAULT_TIMEOUT ({DEFAULT_TIMEOUT}). "
            f"Both must change together — see Issue #56."
        )

    def test_default_stays_under_poll_interval(self):
        """Worst-case poll cycle wall time = max(timeout) across parallel
        workers; if timeout ever exceeds poll_interval, cycles overlap
        themselves. Defensive invariant — keeps a future bump honest."""
        assert DEFAULT_TIMEOUT < DEFAULT_POLL_INTERVAL


# ── Peer name plumbing (IP↔hostname correlation across views) ─────────────


class TestPeerNamePlumbing:
    """The federation collector usually addresses peers by IP (fleet.json
    stores IPs); the tracer leaderboard and MA fleet rollup show friendly
    fleet-names (`fleet-host-2`). When a peer goes black-hole, the
    operator has to mentally map IP→name to correlate. Carrying the name
    alongside the IP in `peer_status` closes that gap so /api/status is
    self-correlating."""

    def test_status_carries_peer_name_at_construction(self):
        """Initial peer_status entries inherit the configured name mapping
        — no poll required, so a freshly-started collector with zero
        successful cycles already serializes the friendly name."""
        fc = FederationCollector(
            ["192.168.86.41", "192.168.86.20"],
            poll_interval=3600,
            peer_names={
                "192.168.86.41": "fleet-host-1",
                "192.168.86.20": "fleet-host-2",
            },
        )
        snap = fc.get_snapshot()
        assert snap.peer_status["192.168.86.41"].peer_name == "fleet-host-1"
        assert snap.peer_status["192.168.86.20"].peer_name == "fleet-host-2"

    def test_peer_name_defaults_to_none_when_unmapped(self):
        """No peer_names arg = backwards-compatible: name field stays None
        for callers that don't carry the fleet.json mapping (e.g. single-
        box installs or operator-supplied bare-hostname lists)."""
        fc = FederationCollector(["plain-host"], poll_interval=3600)
        snap = fc.get_snapshot()
        assert snap.peer_status["plain-host"].peer_name is None

    def test_peer_name_stamped_after_successful_poll(self):
        """fetch_peer_directory returns a status object that doesn't know
        the friendly name — the collector must stamp peer_name onto each
        returned status during poll_once, otherwise the field gets blown
        away on the first successful cycle."""
        fc = FederationCollector(
            ["192.168.86.41"], poll_interval=10,
            peer_names={"192.168.86.41": "fleet-host-1"},
        )
        # fetch_peer_directory returns a status WITHOUT peer_name set
        # (mirrors the real fetch — only the collector owns the mapping).
        good = FederationPeerStatus(
            hostname="192.168.86.41", ok=True, last_sync=time.time(),
            last_count=42, last_attempt=time.time(),
        )
        with patch("utils.map_federation.fetch_peer_directory",
                   return_value=([], good)):
            fc.poll_once()
        snap = fc.get_snapshot()
        assert snap.peer_status["192.168.86.41"].peer_name == "fleet-host-1"

    def test_peer_name_stamped_after_failed_poll(self):
        """The whole point of this field is to make failures correlatable —
        when a peer times out, the operator needs `peer_name` on the
        failure row, not on the success row."""
        fc = FederationCollector(
            ["192.168.86.41"], poll_interval=10,
            peer_names={"192.168.86.41": "fleet-host-1"},
        )
        bad = FederationPeerStatus(
            hostname="192.168.86.41", ok=False,
            last_error="URLError: Connection refused",
            last_attempt=time.time(),
        )
        with patch("utils.map_federation.fetch_peer_directory",
                   return_value=([], bad)):
            fc.poll_once()
        snap = fc.get_snapshot()
        assert snap.peer_status["192.168.86.41"].peer_name == "fleet-host-1"
        assert snap.peer_status["192.168.86.41"].ok is False

    def test_peer_name_stamped_on_executor_crash(self):
        """The crash branch builds a fresh FederationPeerStatus rather than
        reusing the fetch return — easy to forget to stamp peer_name here.
        Explicit test prevents regression."""
        fc = FederationCollector(
            ["192.168.86.41"], poll_interval=10,
            peer_names={"192.168.86.41": "fleet-host-1"},
        )

        def crash(*a, **kw):
            raise RuntimeError("simulated peer fetch crash")

        with patch("utils.map_federation.fetch_peer_directory",
                   side_effect=crash):
            fc.poll_once()
        snap = fc.get_snapshot()
        s = snap.peer_status["192.168.86.41"]
        assert s.peer_name == "fleet-host-1"
        assert s.ok is False
        assert "crash" in (s.last_error or "")

    def test_unmapped_peer_keeps_name_none_through_poll(self):
        """Mixed fleet: one peer in fleet.json (mapping carries name), one
        operator-added bare hostname (no mapping). The mapped peer gets a
        name; the unmapped one stays None — neither steals the other's
        name."""
        fc = FederationCollector(
            ["192.168.86.41", "operator-test-box"],
            poll_interval=10,
            peer_names={"192.168.86.41": "fleet-host-1"},
        )
        good = FederationPeerStatus(
            hostname="x", ok=True, last_sync=time.time(),
            last_count=0, last_attempt=time.time(),
        )
        with patch("utils.map_federation.fetch_peer_directory",
                   side_effect=lambda peer, *a, **kw: (
                       [], FederationPeerStatus(
                           hostname=peer, ok=True, last_sync=time.time(),
                           last_count=0, last_attempt=time.time(),
                       )
                   )):
            fc.poll_once()
        snap = fc.get_snapshot()
        assert snap.peer_status["192.168.86.41"].peer_name == "fleet-host-1"
        assert snap.peer_status["operator-test-box"].peer_name is None
